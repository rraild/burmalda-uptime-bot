import asyncio
import logging
import sqlite3
from time import time

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from .clients import NodesApiError, NodesClient
from .config import Settings
from .formatting import alert, dashboard
from .node_history import NodeHistory
from .probes import ServerChecker
from .state import MonitorState

logger = logging.getLogger(__name__)


class MonitorService:
    def __init__(
        self,
        bot: Bot,
        settings: Settings,
        nodes: NodesClient,
        checker: ServerChecker,
        state: MonitorState,
        history: NodeHistory,
    ) -> None:
        self._bot = bot
        self._settings = settings
        self._nodes = nodes
        self._checker = checker
        self._state = state
        self._history = history
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._run_cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Unexpected monitoring cycle failure")

            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._settings.check_interval_seconds
                )
            except TimeoutError:
                pass

    async def _run_cycle(self) -> None:
        try:
            servers = await self._nodes.fetch()
        except NodesApiError as exc:
            logger.error("Cannot update server list: %s", exc)
            return
        if not servers:
            logger.warning("Nodes API returned an empty server list")
            return

        sampled_at = int(time())
        statuses = await self._checker.check_many(servers)
        try:
            await asyncio.to_thread(self._history.record, sampled_at, servers, statuses)
        except sqlite3.Error:
            logger.exception("Cannot save node history")
        transitions = await self._state.apply(servers, statuses)
        snapshot = await self._state.snapshot()

        try:
            await self._bot.edit_message_text(
                chat_id=self._settings.chat_id,
                message_id=self._settings.message_id,
                text=dashboard(
                    snapshot,
                    confirmation_delay=self._settings.confirmation_delay_seconds,
                ),
                parse_mode="HTML",
            )
        except TelegramAPIError as exc:
            logger.error("Cannot edit status message: %s", exc)

        for transition in transitions:
            try:
                await self._bot.send_message(
                    chat_id=self._settings.chat_id,
                    text=alert(transition),
                    parse_mode="HTML",
                )
            except TelegramAPIError as exc:
                logger.error(
                    "Cannot send alert for %s: %s", transition.server.name, exc
                )
