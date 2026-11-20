import asyncio
import logging
from time import monotonic
from urllib.parse import quote, urlsplit
from typing import Any

import aiohttp

from .models import Server

logger = logging.getLogger(__name__)


class NodesApiError(RuntimeError):
    pass


class NodesClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        url: str,
        api_key: str,
        verify_ssl: bool,
    ) -> None:
        self._session = session
        self._url = url
        self._api_key = api_key
        self._verify_ssl = verify_ssl

    async def fetch(self) -> list[Server]:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        try:
            async with self._session.get(
                self._url, headers=headers, ssl=self._verify_ssl
            ) as response:
                if response.status != 200:
                    raise NodesApiError(f"nodes API returned HTTP {response.status}")
                data = await response.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise NodesApiError(f"nodes API request failed: {exc}") from exc

        if isinstance(data, dict) and data.get("success") is False:
            raise NodesApiError("3x-ui rejected the nodes list request")
        raw_servers = data.get("obj", data) if isinstance(data, dict) else data
        if not isinstance(raw_servers, list):
            raise NodesApiError("nodes API response does not contain a list")

        servers: list[Server] = []
        seen: set[str] = set()
        for item in raw_servers:
            server = self._parse_server(item)
            if server is not None and server.key not in seen:
                servers.append(server)
                seen.add(server.key)
        return servers

    @staticmethod
    def _parse_server(item: Any) -> Server | None:
        if not isinstance(item, dict):
            return None
        if item.get("enable") is False:
            return None
        name = str(item.get("name") or item.get("remark") or "Unknown")
        raw_url = item.get("url") or item.get("address") or item.get("host") or ""
        if not isinstance(raw_url, str) or not raw_url.strip():
            return None
        raw_url = raw_url.strip()
        if "://" not in raw_url:
            scheme = item.get("scheme") or "https"
            raw_url = f"{scheme}://{raw_url}"
        try:
            parsed = urlsplit(raw_url)
            host = parsed.hostname
            default_port = 80 if parsed.scheme == "http" else 443
            port = parsed.port or int(item.get("port") or default_port)
            node_id = int(item.get("id") or 0)
        except (TypeError, ValueError):
            return None
        if parsed.scheme not in {"http", "https"} or not host or not 1 <= port <= 65535:
            return None
        return Server(
            name=name,
            host=host,
            port=port,
            node_id=node_id,
            panel_status=str(item.get("status") or "unknown").strip().lower(),
            scheme=parsed.scheme,
        )


class CheckHostClient:
    BASE_URL = "https://check-host.net"

    def __init__(
        self, session: aiohttp.ClientSession, *, result_delay_seconds: float
    ) -> None:
        self._session = session
        self._result_delay_seconds = result_delay_seconds
        self._nodes: list[str] = []
        self._nodes_until = 0.0
        self._nodes_lock = asyncio.Lock()

    async def _get(self, path: str, params: Any = None) -> Any:
        async with self._session.get(
            f"{self.BASE_URL}/{path}",
            params=params,
            headers={"Accept": "application/json"},
        ) as response:
            response.raise_for_status()
            return await response.json()

    async def _probe_nodes(self) -> list[str]:
        async with self._nodes_lock:
            if monotonic() < self._nodes_until:
                return self._nodes
            data = await self._get("nodes/hosts")
            nodes = data.get("nodes", {}) if isinstance(data, dict) else {}
            by_country: dict[str, list[str]] = {}
            if isinstance(nodes, dict):
                for name, info in nodes.items():
                    location = info.get("location") if isinstance(info, dict) else None
                    if isinstance(location, list) and location:
                        by_country.setdefault(str(location[0]).lower(), []).append(name)
            ru = by_country.pop("ru", [])
            controls = [names[0] for names in by_country.values()]
            self._nodes = ru[:3] + controls[:2] if ru and controls else []
            self._nodes_until = monotonic() + (600 if self._nodes else 60)
            return self._nodes

    async def is_available_from_ru(self, url: str) -> bool | None:
        """Return no verdict if probes are incomplete or the control also fails."""
        try:
            names = await self._probe_nodes()
            if not names:
                return None
            params = [("host", url), *(("node", name) for name in names)]
            data = await self._get("check-http", params)
            if not isinstance(data, dict):
                return None
            request_id = data.get("request_id")
            nodes = data.get("nodes", {})
            if not request_id or not isinstance(nodes, dict):
                return None
            countries = {
                node: info[0]
                for node, info in nodes.items()
                if isinstance(info, list) and info and isinstance(info[0], str)
            }
            await asyncio.sleep(self._result_delay_seconds)
            results = await self._get(f"check-result/{quote(str(request_id), safe='')}")
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            logger.warning("check-host request failed: %s", exc)
            return None

        if not isinstance(results, dict):
            return None
        ru = [
            self._probe_outcome(results.get(n))
            for n, c in countries.items()
            if c == "ru"
        ]
        if True in ru:
            return True
        if (
            len(ru) >= 2
            and all(outcome is False for outcome in ru)
            and any(
                self._probe_outcome(results.get(n)) is True
                for n, c in countries.items()
                if c != "ru"
            )
        ):
            return False
        return None

    @staticmethod
    def _probe_outcome(value: Any) -> bool | None:
        if not isinstance(value, list):
            return None
        failed = False
        for result in value:
            if not isinstance(result, list) or len(result) < 4:
                continue
            if str(result[3]).isdigit() and 100 <= int(result[3]) <= 599:
                return True
            if result[0] == 1:
                return True
            failed |= result[0] == 0 and bool(result[2]) and result[3] is None
        return False if failed else None
