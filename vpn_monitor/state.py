import asyncio
from dataclasses import replace
from datetime import datetime, timezone

from .models import (
    Incident,
    LocationSnapshot,
    Server,
    ServerStatus,
    StateSnapshot,
    Transition,
)


class MonitorState:
    def __init__(self, *, max_incidents: int = 50) -> None:
        self._started_at = datetime.now(timezone.utc)
        self._locations: dict[str, LocationSnapshot] = {}
        self._incidents: list[Incident] = []
        self._max_incidents = max_incidents
        self._lock = asyncio.Lock()

    async def apply(
        self, servers: list[Server], statuses: dict[str, ServerStatus]
    ) -> list[Transition]:
        now = datetime.now(timezone.utc)
        transitions: list[Transition] = []
        active_keys = {server.key for server in servers}
        async with self._lock:
            for stale_key in self._locations.keys() - active_keys:
                self._close_open_incident(stale_key, now)
                del self._locations[stale_key]

            for server in servers:
                status = statuses.get(server.key)
                previous = self._locations.get(server.key)
                if status is None:
                    continue
                if status == ServerStatus.ONLINE_UNCHECKED and previous is not None:
                    if previous.status in {
                        ServerStatus.ONLINE,
                        ServerStatus.BLOCKED_RU,
                    }:
                        status = previous.status
                if previous is None:
                    self._locations[server.key] = LocationSnapshot(server, status, now)
                    if status in {ServerStatus.OFFLINE, ServerStatus.BLOCKED_RU}:
                        self._open_incident(server, status, now)
                    continue

                if previous.status == status:
                    self._locations[server.key] = LocationSnapshot(
                        server, status, previous.state_since
                    )
                    continue

                self._close_open_incident(server.key, now)
                if status in {ServerStatus.OFFLINE, ServerStatus.BLOCKED_RU}:
                    self._open_incident(server, status, now)
                self._locations[server.key] = LocationSnapshot(server, status, now)
                if (
                    previous.status != ServerStatus.ONLINE_UNCHECKED
                    or status != ServerStatus.ONLINE
                ):
                    transitions.append(Transition(server, previous.status, status))

            self._incidents = self._incidents[-self._max_incidents :]
        return transitions

    def _open_incident(
        self, server: Server, status: ServerStatus, started_at: datetime
    ) -> None:
        self._incidents.append(
            Incident(server.key, server.name, status, started_at=started_at)
        )

    def _close_open_incident(self, server_key: str, ended_at: datetime) -> None:
        for incident in reversed(self._incidents):
            if incident.server_key == server_key and incident.ended_at is None:
                incident.ended_at = ended_at
                return

    async def snapshot(self) -> StateSnapshot:
        async with self._lock:
            locations = tuple(
                sorted(
                    self._locations.values(), key=lambda item: item.server.name.lower()
                )
            )
            incidents = tuple(replace(item) for item in self._incidents)
            return StateSnapshot(self._started_at, locations, incidents)
