import asyncio
import logging

from .clients import CheckHostClient
from .models import Server, ServerStatus

logger = logging.getLogger(__name__)


class ServerChecker:
    def __init__(
        self,
        check_host: CheckHostClient,
        *,
        confirmation_delay_seconds: float,
        max_concurrent_checks: int,
    ) -> None:
        self._check_host = check_host
        self._confirmation_delay = confirmation_delay_seconds
        self._semaphore = asyncio.Semaphore(max_concurrent_checks)

    async def check_many(self, servers: list[Server]) -> dict[str, ServerStatus]:
        statuses = await asyncio.gather(*(self.check_confirmed(s) for s in servers))
        return {
            server.key: status
            for server, status in zip(servers, statuses)
            if status is not None
        }

    async def check_confirmed(self, server: Server) -> ServerStatus | None:
        first = await self._limited_check_once(server)
        if first != ServerStatus.BLOCKED_RU:
            return first

        logger.warning(
            "%s: preliminary state %s; checking again in %.1f seconds",
            server.name,
            first,
            self._confirmation_delay,
        )
        await asyncio.sleep(self._confirmation_delay)
        second = await self._limited_check_once(server)
        return second

    async def _limited_check_once(self, server: Server) -> ServerStatus | None:
        async with self._semaphore:
            return await self._check_once(server)

    async def _check_once(self, server: Server) -> ServerStatus | None:
        if server.panel_status in {"offline", "error"}:
            return ServerStatus.OFFLINE
        if server.panel_status != "online":
            return None
        reachable = await self._check_host.is_available_from_ru(server.probe_url)
        if reachable is False:
            return ServerStatus.BLOCKED_RU
        if reachable is True:
            return ServerStatus.ONLINE
        return ServerStatus.ONLINE_UNCHECKED
