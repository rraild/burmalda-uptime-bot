import asyncio
import math
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import aiohttp

from .panel_models import (
    ClientDetails,
    ClientPage,
    HistorySample,
    HwidDevice,
    NodeInfo,
    PanelOverview,
    TrafficStats,
)


class PanelApiError(RuntimeError):
    pass


class ClientNotFound(PanelApiError):
    pass


class PanelApiClient:
    # 3x-ui 3.8.5's runtime whitelist differs from its OpenAPI document.
    # Prefer the six-hour window (60 points x 360 seconds), then fall back to
    # every value accepted by either the implementation or the published spec.
    HISTORY_BUCKETS = (360, 180, 60, 30, 2, 720, 1440, 2880, 10080, 300, 120)

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        base_url: str,
        api_key: str,
        verify_ssl: bool,
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        self._verify_ssl = verify_ssl
        self._history_bucket: int | None = None
        self._subscription_base_url: str | None = None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        json_body: Any = None,
    ) -> Any:
        url = f"{self._base_url}/{path.lstrip('/')}"
        try:
            async with self._session.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=self._headers,
                ssl=self._verify_ssl,
                timeout=(
                    aiohttp.ClientTimeout(total=120)
                    if json_body is not None
                    else self._session.timeout
                ),
            ) as response:
                if response.status != 200:
                    raise PanelApiError(
                        f"3x-ui API returned HTTP {response.status} for {path}"
                    )
                payload = await response.json()
        except PanelApiError:
            raise
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise PanelApiError(f"3x-ui API request failed for {path}: {exc}") from exc

        if not isinstance(payload, dict):
            raise PanelApiError(f"3x-ui API returned invalid JSON for {path}")
        if payload.get("success") is False:
            message = str(payload.get("msg") or "unknown API error")
            if (
                path.startswith("clients/get/")
                and "record not found" in message.lower()
            ):
                raise ClientNotFound("Client does not exist in 3x-ui")
            raise PanelApiError(f"3x-ui API error for {path}: {message}")
        return payload.get("obj")

    async def get_clients_page(
        self,
        *,
        page: int,
        page_size: int,
        search: str = "",
    ) -> ClientPage:
        params: dict[str, str | int] = {
            "page": max(1, page),
            "pageSize": page_size,
            "sort": "email",
            "order": "ascend",
        }
        if search:
            params["search"] = search
        payload = await self._request("GET", "clients/list/paged", params=params)
        try:
            return ClientPage.from_payload(payload)
        except ValueError as exc:
            raise PanelApiError(str(exc)) from exc

    async def get_overview(self) -> PanelOverview:
        api_page_size = 200
        first = await self.get_clients_page(page=1, page_size=api_page_size)
        items = list(first.items)
        page_count = max(1, math.ceil(first.total / api_page_size))

        # Fetch in small batches so large installations do not overload the panel.
        for first_page in range(2, page_count + 1, 5):
            pages = await asyncio.gather(
                *(
                    self.get_clients_page(page=page, page_size=api_page_size)
                    for page in range(
                        first_page,
                        min(first_page + 5, page_count + 1),
                    )
                )
            )
            for page in pages:
                items.extend(page.items)

        return PanelOverview(
            total_clients=first.total,
            active_clients=first.active_count,
            online_clients=first.online_count,
            disabled_clients=first.disabled_count,
            depleted_clients=first.depleted_count,
            upload_bytes=sum(item.traffic.up for item in items),
            download_bytes=sum(item.traffic.down for item in items),
        )

    async def get_client_payload(self, email: str) -> dict[str, Any]:
        payload = await self._request("GET", f"clients/get/{quote(email, safe='')}")
        if not isinstance(payload, dict):
            raise PanelApiError("3x-ui API returned invalid client details")
        return payload

    async def get_client(self, email: str) -> ClientDetails:
        payload = await self.get_client_payload(email)
        try:
            return ClientDetails.from_payload(payload)
        except ValueError as exc:
            raise PanelApiError(str(exc)) from exc

    async def update_client_fields(self, email: str, **changes: Any) -> None:
        payload = await self.get_client_payload(email)
        client = payload.get("client")
        if not isinstance(client, dict):
            raise PanelApiError("3x-ui API returned invalid client record")

        # GET returns a persisted ClientRecord where `id` is the numeric DB id
        # and `uuid` is the protocol credential. POST /clients/update embeds
        # model.Client, where that same protocol credential must be sent as
        # string field `id` (the official frontend performs this mapping too).
        updated = dict(client)
        if isinstance(updated.get("allowedIPs"), str):
            updated["allowedIPs"] = _split_allowed_ips(updated["allowedIPs"])
        credential = client.get("uuid")
        if isinstance(credential, str) and credential:
            updated["id"] = credential
        elif not isinstance(updated.get("id"), str):
            updated.pop("id", None)

        tunnel_allowed_ips = payload.get("tunnelAllowedIPs")
        if isinstance(tunnel_allowed_ips, dict):
            updated["allowedIPsByInbound"] = {
                str(inbound_id): _split_allowed_ips(allowed_ips)
                for inbound_id, allowed_ips in tunnel_allowed_ips.items()
            }
        updated.update(changes)
        await self._request(
            "POST",
            f"clients/update/{quote(email, safe='')}",
            json_body=updated,
        )

    async def adjust_client_expiry(self, email: str, days: int) -> None:
        await self._request(
            "POST",
            "clients/bulkAdjust",
            json_body={
                "emails": [email],
                "addDays": days,
                "addBytes": 0,
                "flow": "",
                "adTag": "",
            },
        )

    async def get_subscription_url(self, sub_id: str) -> str:
        if not sub_id:
            raise PanelApiError("client has no subscription ID")
        base_url = getattr(self, "_subscription_base_url", None)
        if base_url is None:
            settings = await self._request("POST", "setting/defaultSettings")
            if not isinstance(settings, dict):
                raise PanelApiError("3x-ui API returned invalid panel settings")
            if settings.get("subEnable") is False:
                raise PanelApiError("3x-ui subscription service is disabled")
            base_url = str(settings.get("subURI") or "").strip()
            if not base_url:
                settings = await self._request("POST", "setting/all")
                if not isinstance(settings, dict):
                    raise PanelApiError("3x-ui API returned invalid panel settings")
                base_url = str(settings.get("subURI") or "").strip()
                if not base_url:
                    base_url = self._build_subscription_base_url(settings)
            self._subscription_base_url = base_url
        if not base_url:
            raise PanelApiError("subscription URL is not configured in 3x-ui")
        return f"{base_url.rstrip('/')}/{quote(sub_id, safe='')}"

    def _build_subscription_base_url(self, settings: dict[str, Any]) -> str:
        panel_url = urlsplit(self._base_url)
        host = str(
            settings.get("subDomain")
            or settings.get("webDomain")
            or panel_url.hostname
            or ""
        ).strip()
        path = str(settings.get("subPath") or "/sub/").strip()
        if not host:
            return ""

        has_tls = bool(settings.get("subCertFile") and settings.get("subKeyFile"))
        scheme = "https" if has_tls else "http"
        if "://" in host:
            parsed_host = urlsplit(host)
            scheme = parsed_host.scheme or scheme
            host = parsed_host.hostname or ""
            configured_port = parsed_host.port
        else:
            configured_port = None
        try:
            port = configured_port or int(settings.get("subPort") or 2096)
        except (TypeError, ValueError):
            port = 2096
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        is_standard_port = (scheme == "http" and port == 80) or (
            scheme == "https" and port == 443
        )
        netloc = host if is_standard_port else f"{host}:{port}"
        normalized_path = f"/{path.strip('/')}" if path.strip("/") else ""
        return urlunsplit((scheme, netloc, normalized_path, "", ""))

    async def get_client_traffic(self, email: str) -> TrafficStats:
        payload = await self._request("GET", f"clients/traffic/{quote(email, safe='')}")
        return TrafficStats.from_payload(payload)

    async def get_inbound_ids(self) -> tuple[int, ...]:
        payload = await self._request("GET", "inbounds/options")
        if not isinstance(payload, list):
            raise PanelApiError("3x-ui returned invalid inbound options")
        ids = {
            item["id"]
            for item in payload
            if isinstance(item, dict)
            and isinstance(item.get("id"), int)
            and item["id"] > 0
        }
        if not ids:
            raise PanelApiError("There are no inbounds in 3x-ui")
        return tuple(sorted(ids))

    async def create_client(
        self, client: dict[str, Any], inbound_ids: tuple[int, ...]
    ) -> Any:
        return await self._request(
            "POST",
            "clients/add",
            json_body={"client": client, "inboundIds": list(inbound_ids)},
        )

    async def set_client_traffic(self, email: str, traffic: TrafficStats) -> None:
        await self._request(
            "POST",
            f"clients/updateTraffic/{quote(email, safe='')}",
            json_body={"upload": traffic.up, "download": traffic.down},
        )

    async def set_external_links(self, email: str, links: list[dict[str, Any]]) -> None:
        await self._request(
            "POST",
            f"clients/{quote(email, safe='')}/externalLinks",
            json_body={"externalLinks": links},
        )

    async def delete_client(self, email: str) -> None:
        await self._request(
            "POST",
            f"clients/del/{quote(email, safe='')}",
            params={"keepTraffic": 1},
            json_body={},
        )

    async def delete_hwid(self, email: str, device_id: int) -> None:
        await self._request(
            "DELETE", f"clients/hwids/{quote(email, safe='')}/{device_id}"
        )

    async def get_hwids(self, email: str) -> tuple[HwidDevice, ...]:
        payload = await self._request("POST", f"clients/hwids/{quote(email, safe='')}")
        if not isinstance(payload, list):
            return ()
        return tuple(
            device
            for raw in payload
            if (device := HwidDevice.from_payload(raw)) is not None
        )

    async def get_nodes(self) -> tuple[NodeInfo, ...]:
        payload = await self._request("GET", "nodes/list")
        if not isinstance(payload, list):
            raise PanelApiError("3x-ui API returned an invalid node list")
        return tuple(
            node for raw in payload if (node := NodeInfo.from_payload(raw)) is not None
        )

    async def get_node(self, node_id: int) -> NodeInfo:
        payload = await self._request("GET", f"nodes/get/{node_id}")
        node = NodeInfo.from_payload(payload)
        if node is None:
            raise PanelApiError("3x-ui API returned an invalid node")
        return node

    async def get_node_history(
        self,
        node_id: int,
        metric: str,
        *,
        bucket: int = 300,
    ) -> tuple[HistorySample, ...]:
        if metric not in {"cpu", "mem"}:
            raise ValueError("node history metric must be cpu or mem")
        payload = await self._request(
            "GET", f"nodes/history/{node_id}/{metric}/{bucket}"
        )
        if not isinstance(payload, list):
            return ()
        return tuple(
            sample
            for raw in payload
            if (sample := HistorySample.from_payload(raw)) is not None
        )

    async def get_node_load_history(
        self,
        node_id: int,
    ) -> tuple[tuple[HistorySample, ...], tuple[HistorySample, ...], int]:
        buckets = list(self.HISTORY_BUCKETS)
        if self._history_bucket in buckets:
            buckets.remove(self._history_bucket)
            buckets.insert(0, self._history_bucket)

        last_error: PanelApiError | None = None
        for bucket in buckets:
            results = await asyncio.gather(
                self.get_node_history(node_id, "cpu", bucket=bucket),
                self.get_node_history(node_id, "mem", bucket=bucket),
                return_exceptions=True,
            )
            errors = [item for item in results if isinstance(item, BaseException)]
            if not errors:
                self._history_bucket = bucket
                cpu, memory = results
                return cpu, memory, bucket

            panel_errors = [
                error for error in errors if isinstance(error, PanelApiError)
            ]
            if len(panel_errors) != len(errors):
                raise errors[0]
            if not all(_is_invalid_bucket(error) for error in panel_errors):
                raise panel_errors[0]
            last_error = panel_errors[0]

        raise last_error or PanelApiError("3x-ui rejected every history bucket")


def _is_invalid_bucket(error: PanelApiError) -> bool:
    message = str(error).lower()
    return "invalid bucket" in message or "unsupported bucket" in message


def _split_allowed_ips(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value or "").split(",") if item.strip()]
