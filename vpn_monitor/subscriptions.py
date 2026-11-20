import asyncio
import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from secrets import token_hex
from time import time
from typing import Any

from .panel_api import ClientNotFound, PanelApiClient, PanelApiError
from .panel_models import ClientDetails, TrafficStats

logger = logging.getLogger(__name__)
GRACE_SECONDS = 24 * 60 * 60

# 3x-ui generates fresh protocol credentials when these are omitted.
ROTATION_FIELDS = (
    "enable",
    "expiryTime",
    "totalGB",
    "limitIp",
    "limitHwid",
    "flow",
    "security",
    "reverse",
    "tgId",
    "comment",
    "group",
    "reset",
    "resetDay",
    "resetMax",
    "trafficReset",
    "trafficResetDay",
    "keepAlive",
    "adTag",
)
EXTERNAL_LINK_FIELDS = (
    "kind",
    "value",
    "remark",
    "enable",
    "expiryTime",
    "namePrefix",
)


@dataclass(frozen=True, slots=True)
class Rotation:
    old_email: str
    old_sub_id: str
    new_email: str
    new_sub_id: str
    delete_after: int | None = None
    traffic_copied: bool = False


@dataclass(frozen=True, slots=True)
class Subscription:
    client: ClientDetails
    url: str
    delete_after: int | None = None
    traffic_copied: bool = True
    node_pending: bool = False


def new_identity() -> tuple[str, str]:
    return f"sub-{token_hex(8)}", token_hex(8)


class SubscriptionService:
    def __init__(self, api: PanelApiClient, database: Path) -> None:
        self._api = api
        self._database = database
        self._lock = asyncio.Lock()
        database.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(database)) as db, db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS rotations (
                    old_email TEXT PRIMARY KEY, old_sub_id TEXT NOT NULL,
                    new_email TEXT UNIQUE NOT NULL, new_sub_id TEXT NOT NULL,
                    delete_after INTEGER, traffic_copied INTEGER NOT NULL DEFAULT 0,
                    completed INTEGER NOT NULL DEFAULT 0
                )"""
            )

    def _rotations(self, where: str, *args: Any) -> list[Rotation]:
        with closing(sqlite3.connect(self._database)) as db:
            rows = db.execute(
                "SELECT old_email, old_sub_id, new_email, new_sub_id, delete_after,"
                " traffic_copied"
                f" FROM rotations WHERE {where}",
                args,
            ).fetchall()
        return [Rotation(*row) for row in rows]

    def _write(self, sql: str, *args: Any) -> None:
        with closing(sqlite3.connect(self._database)) as db, db:
            db.execute(sql, args)

    async def create(
        self, email: str, sub_id: str, expiry_time: int, comment: str
    ) -> Subscription:
        api = self._api
        url = await api.get_subscription_url(sub_id)
        inbound_ids = await api.get_inbound_ids()
        async with self._lock:
            client, pending = await self._create_checked(
                {
                    "email": email,
                    "subId": sub_id,
                    "enable": True,
                    "totalGB": 0,
                    "limitHwid": 10,
                    "expiryTime": expiry_time,
                    "flow": "xtls-rprx-vision",
                    "comment": comment,
                },
                inbound_ids,
            )
        return Subscription(client=client, url=url, node_pending=pending)

    async def _create_checked(
        self, fields: dict[str, Any], inbound_ids: tuple[int, ...]
    ) -> tuple[ClientDetails, bool]:
        api = self._api
        # The same email and Sub ID can resume a partial multi-inbound add.
        result = await api.create_client(fields, inbound_ids)
        client = await api.get_client(fields["email"])
        if (
            client.sub_id != fields["subId"]
            or client.expiry_time != fields["expiryTime"]
            or client.enabled != fields.get("enable", True)
            or client.limit_hwid != fields.get("limitHwid", 0)
            or client.total_bytes != fields.get("totalGB", 0)
            or not set(inbound_ids).issubset(client.inbound_ids)
        ):
            raise PanelApiError("3x-ui did not confirm the full subscription settings")
        pending = isinstance(result, dict) and result.get("nodePending") is True
        return client, pending

    async def rotate(self, email: str) -> Subscription:
        api = self._api
        async with self._lock:
            existing = self._rotations(
                "old_email = ? OR (new_email = ? AND completed = 0)", email, email
            )
            if existing:
                rotation = existing[0]
                if rotation.delete_after is not None:
                    client = await api.get_client(rotation.new_email)
                    if client.sub_id != rotation.new_sub_id:
                        raise PanelApiError("The replacement subscription changed")
                    url = await api.get_subscription_url(client.sub_id)
                    return Subscription(
                        client,
                        url,
                        rotation.delete_after,
                        bool(rotation.traffic_copied),
                    )
            else:
                old = await api.get_client(email)
                if not old.sub_id or not old.inbound_ids:
                    raise PanelApiError("The source client needs a Sub ID and inbounds")
                rotation = Rotation(email, old.sub_id, *new_identity())
                self._write(
                    "INSERT INTO rotations"
                    " (old_email, old_sub_id, new_email, new_sub_id)"
                    " VALUES (?, ?, ?, ?)",
                    rotation.old_email,
                    rotation.old_sub_id,
                    rotation.new_email,
                    rotation.new_sub_id,
                )

            payload = await api.get_client_payload(rotation.old_email)
            old = ClientDetails.from_payload(payload)
            if old.sub_id != rotation.old_sub_id or not old.inbound_ids:
                raise PanelApiError("The source subscription changed during rotation")
            url = await api.get_subscription_url(rotation.new_sub_id)
            source = payload["client"]
            fields = {key: source[key] for key in ROTATION_FIELDS if key in source}
            fields.update(
                email=rotation.new_email,
                subId=rotation.new_sub_id,
                expiryTime=old.expiry_time,
            )
            client, pending = await self._create_checked(fields, old.inbound_ids)
            links = [
                {key: link[key] for key in EXTERNAL_LINK_FIELDS if key in link}
                for link in payload.get("externalLinks") or []
            ]
            if links:
                await api.set_external_links(client.email, links)

            traffic_copied = await self._copy_traffic(old.email, client.email)
            if old.total_bytes > 0 and not traffic_copied:
                raise PanelApiError("Traffic must be transferred for a metered client")
            delete_after = int(time()) + GRACE_SECONDS
            self._write(
                "UPDATE rotations SET delete_after = ?, traffic_copied = ?"
                " WHERE old_email = ?",
                delete_after,
                traffic_copied,
                rotation.old_email,
            )
            return Subscription(client, url, delete_after, traffic_copied, pending)

    async def _copy_traffic(self, old_email: str, new_email: str) -> bool:
        try:
            initial = await self._api.get_client_traffic(old_email)
            if not initial.up and not initial.down:
                return True
            current = await self._api.get_client_traffic(new_email)
            if current.up < initial.up or current.down < initial.down:
                await self._api.set_client_traffic(
                    new_email,
                    TrafficStats(
                        up=max(current.up, initial.up),
                        down=max(current.down, initial.down),
                    ),
                )
                current = await self._api.get_client_traffic(new_email)
            return current.up >= initial.up and current.down >= initial.down
        except PanelApiError as exc:
            logger.warning("Could not transfer subscription traffic: %s", exc)
            return False

    async def retire_due(self) -> None:
        async with self._lock:
            for rotation in self._rotations(
                "completed = 0 AND delete_after <= ?", int(time())
            ):
                try:
                    await self._retire(rotation)
                except PanelApiError as exc:
                    logger.warning("Could not retire old subscription: %s", exc)

    async def _retire(self, rotation: Rotation) -> None:
        api = self._api
        try:
            old = await api.get_client(rotation.old_email)
        except ClientNotFound:
            old = None
        if old and old.sub_id == rotation.old_sub_id:
            replacement = await api.get_client(rotation.new_email)
            if replacement.sub_id != rotation.new_sub_id or not replacement.inbound_ids:
                raise PanelApiError("Replacement subscription is no longer intact")
            await api.delete_client(old.email)
        self._write(
            "UPDATE rotations SET completed = 1 WHERE old_email = ?", rotation.old_email
        )

    async def run_cleanup(self) -> None:
        while True:
            await self.retire_due()
            await asyncio.sleep(30)
