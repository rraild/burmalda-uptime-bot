from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ServerStatus(StrEnum):
    ONLINE = "ONLINE"
    ONLINE_UNCHECKED = "ONLINE_UNCHECKED"
    BLOCKED_RU = "BLOCKED_RU"
    OFFLINE = "OFFLINE"


@dataclass(frozen=True, slots=True)
class Server:
    name: str
    host: str
    port: int
    node_id: int = 0
    panel_status: str = "unknown"
    scheme: str = "https"
    online_count: int | None = None

    @property
    def key(self) -> str:
        if self.node_id:
            return f"node:{self.node_id}"
        return f"{self.host.lower()}:{self.port}"

    @property
    def probe_url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.scheme}://{host}:{self.port}/"


@dataclass(frozen=True, slots=True)
class LocationSnapshot:
    server: Server
    status: ServerStatus
    state_since: datetime


@dataclass(slots=True)
class Incident:
    server_key: str
    server_name: str
    status: ServerStatus
    started_at: datetime
    ended_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class StateSnapshot:
    started_at: datetime
    locations: tuple[LocationSnapshot, ...]
    incidents: tuple[Incident, ...]


@dataclass(frozen=True, slots=True)
class Transition:
    server: Server
    previous: ServerStatus
    current: ServerStatus
