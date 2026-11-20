from dataclasses import dataclass
from typing import Any


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True, slots=True)
class TrafficStats:
    up: int = 0
    down: int = 0
    total: int = 0
    last_online: int = 0
    last_sub_fetch: int = 0

    @classmethod
    def from_payload(cls, payload: Any) -> "TrafficStats":
        if not isinstance(payload, dict):
            return cls()
        return cls(
            up=as_int(payload.get("up")),
            down=as_int(payload.get("down")),
            total=as_int(payload.get("total")),
            last_online=as_int(payload.get("lastOnline")),
            last_sub_fetch=as_int(payload.get("lastSubFetch")),
        )


@dataclass(frozen=True, slots=True)
class ClientListItem:
    email: str
    sub_id: str
    enabled: bool
    expiry_time: int
    total_bytes: int
    limit_hwid: int
    limit_ip: int
    comment: str
    group: str
    inbound_ids: tuple[int, ...]
    traffic: TrafficStats

    @classmethod
    def from_payload(cls, payload: Any) -> "ClientListItem | None":
        if not isinstance(payload, dict):
            return None
        email = str(payload.get("email") or "").strip()
        if not email:
            return None
        inbound_ids = payload.get("inboundIds")
        if not isinstance(inbound_ids, list):
            inbound_ids = []
        return cls(
            email=email,
            sub_id=str(payload.get("subId") or ""),
            enabled=bool(payload.get("enable", False)),
            expiry_time=as_int(payload.get("expiryTime")),
            total_bytes=as_int(payload.get("totalGB")),
            limit_hwid=as_int(payload.get("limitHwid")),
            limit_ip=as_int(payload.get("limitIp")),
            comment=str(payload.get("comment") or ""),
            group=str(payload.get("group") or ""),
            inbound_ids=tuple(as_int(value) for value in inbound_ids),
            traffic=TrafficStats.from_payload(payload.get("traffic")),
        )


@dataclass(frozen=True, slots=True)
class ClientPage:
    items: tuple[ClientListItem, ...]
    page: int
    page_size: int
    total: int
    filtered: int
    online_count: int
    active_count: int
    disabled_count: int
    depleted_count: int

    @classmethod
    def from_payload(cls, payload: Any) -> "ClientPage":
        if not isinstance(payload, dict):
            raise ValueError("clients page is not an object")
        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            raw_items = []
        items = tuple(
            item
            for raw in raw_items
            if (item := ClientListItem.from_payload(raw)) is not None
        )
        summary = payload.get("summary")
        if not isinstance(summary, dict):
            summary = {}
        return cls(
            items=items,
            page=max(1, as_int(payload.get("page"), 1)),
            page_size=max(1, as_int(payload.get("pageSize"), 25)),
            total=max(0, as_int(payload.get("total"))),
            filtered=max(0, as_int(payload.get("filtered"))),
            online_count=max(0, as_int(summary.get("onlineCount"))),
            active_count=max(0, as_int(summary.get("active"))),
            disabled_count=max(0, as_int(summary.get("deactiveCount"))),
            depleted_count=max(0, as_int(summary.get("depletedCount"))),
        )


@dataclass(frozen=True, slots=True)
class ClientDetails:
    email: str
    uuid: str
    sub_id: str
    enabled: bool
    expiry_time: int
    total_bytes: int
    limit_hwid: int
    limit_ip: int
    comment: str
    group: str
    inbound_ids: tuple[int, ...]
    created_at: int

    @classmethod
    def from_payload(cls, payload: Any) -> "ClientDetails":
        if not isinstance(payload, dict):
            raise ValueError("client details are not an object")
        client = payload.get("client", payload)
        if not isinstance(client, dict):
            raise ValueError("client record is not an object")
        email = str(client.get("email") or "").strip()
        if not email:
            raise ValueError("client record has no email")
        inbound_ids = payload.get("inboundIds")
        if not isinstance(inbound_ids, list):
            inbound_ids = []
        return cls(
            email=email,
            uuid=str(client.get("uuid") or client.get("id") or ""),
            sub_id=str(client.get("subId") or ""),
            enabled=bool(client.get("enable", False)),
            expiry_time=as_int(client.get("expiryTime")),
            total_bytes=as_int(client.get("totalGB")),
            limit_hwid=as_int(client.get("limitHwid")),
            limit_ip=as_int(client.get("limitIp")),
            comment=str(client.get("comment") or ""),
            group=str(client.get("group") or ""),
            inbound_ids=tuple(as_int(value) for value in inbound_ids),
            created_at=as_int(client.get("createdAt")),
        )


@dataclass(frozen=True, slots=True)
class HwidDevice:
    id: int
    first_seen: int
    last_seen: int
    user_agent: str
    device_os: str
    os_version: str
    device_model: str
    fingerprint: str

    @classmethod
    def from_payload(cls, payload: Any) -> "HwidDevice | None":
        if not isinstance(payload, dict):
            return None
        device_id = as_int(payload.get("id"))
        if device_id <= 0:
            return None
        return cls(
            id=device_id,
            first_seen=as_int(payload.get("firstSeen")),
            last_seen=as_int(payload.get("lastSeen")),
            user_agent=str(payload.get("userAgent") or ""),
            device_os=str(payload.get("deviceOs") or ""),
            os_version=str(payload.get("osVersion") or ""),
            device_model=str(payload.get("deviceModel") or "Unknown device"),
            fingerprint=str(payload.get("fingerprint") or ""),
        )


@dataclass(frozen=True, slots=True)
class NodeInfo:
    id: int
    name: str
    remark: str
    status: str
    enabled: bool
    cpu_pct: float
    mem_pct: float
    net_up: int
    net_down: int
    client_count: int
    online_count: int
    active_count: int
    disabled_count: int
    depleted_count: int
    latency_ms: int
    uptime_seconds: int
    panel_version: str
    xray_state: str
    xray_version: str
    last_heartbeat: int
    last_error: str
    xray_error: str

    @classmethod
    def from_payload(cls, payload: Any) -> "NodeInfo | None":
        if not isinstance(payload, dict):
            return None
        node_id = as_int(payload.get("id"))
        if node_id <= 0:
            return None
        return cls(
            id=node_id,
            name=str(payload.get("name") or f"Node {node_id}"),
            remark=str(payload.get("remark") or ""),
            status=str(payload.get("status") or "unknown").lower(),
            enabled=bool(payload.get("enable", False)),
            cpu_pct=as_float(payload.get("cpuPct")),
            mem_pct=as_float(payload.get("memPct")),
            net_up=as_int(payload.get("netUp")),
            net_down=as_int(payload.get("netDown")),
            client_count=as_int(payload.get("clientCount")),
            online_count=as_int(payload.get("onlineCount")),
            active_count=as_int(payload.get("activeCount")),
            disabled_count=as_int(payload.get("disabledCount")),
            depleted_count=as_int(payload.get("depletedCount")),
            latency_ms=as_int(payload.get("latencyMs")),
            uptime_seconds=as_int(payload.get("uptimeSecs")),
            panel_version=str(payload.get("panelVersion") or "—"),
            xray_state=str(payload.get("xrayState") or "unknown").lower(),
            xray_version=str(payload.get("xrayVersion") or "—"),
            last_heartbeat=as_int(payload.get("lastHeartbeat")),
            last_error=str(payload.get("lastError") or ""),
            xray_error=str(payload.get("xrayError") or ""),
        )


@dataclass(frozen=True, slots=True)
class HistorySample:
    timestamp: float
    value: float

    @classmethod
    def from_payload(cls, payload: Any) -> "HistorySample | None":
        if not isinstance(payload, dict):
            return None
        timestamp = as_float(payload.get("t"))
        if timestamp <= 0:
            return None
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return cls(timestamp=timestamp, value=as_float(payload.get("v")))


@dataclass(frozen=True, slots=True)
class PanelOverview:
    total_clients: int
    active_clients: int
    online_clients: int
    disabled_clients: int
    depleted_clients: int
    upload_bytes: int
    download_bytes: int
