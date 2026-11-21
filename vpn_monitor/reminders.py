import asyncio
import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from time import time

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from .formatting import MSK, human_duration
from .panel_api import PanelApiClient, PanelApiError
from .panel_models import ClientListItem

logger = logging.getLogger(__name__)
GROUP = "СВОи, не трогать"
DAY_MS = 86_400_000


class ExpiryReminders:
    def __init__(
        self, bot: Bot, api: PanelApiClient, admin_ids: frozenset[int], database: Path
    ) -> None:
        self._bot = bot
        self._api = api
        self._admin_ids = admin_ids
        self._database = database
        with closing(sqlite3.connect(database)) as db, db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS sent (
                    admin_id INTEGER, email TEXT, expiry_ms INTEGER,
                    PRIMARY KEY (admin_id, email, expiry_ms)
                )"""
            )

    async def run(self) -> None:
        while True:
            try:
                await self.check_once()
            except (PanelApiError, sqlite3.Error) as exc:
                logger.warning("Expiry reminder check failed: %s", exc)
            await asyncio.sleep(60)

    async def check_once(self) -> None:
        if not self._admin_ids:
            return
        start_ms = int(time() * 1000)
        end_ms = start_ms + DAY_MS
        clients = await self._api.get_expiring_clients(start_ms, end_ms)
        now_ms = int(time() * 1000)
        with closing(sqlite3.connect(self._database)) as db, db:
            db.execute("DELETE FROM sent WHERE expiry_ms < ?", (now_ms - DAY_MS,))
            sent = set(
                db.execute(
                    "SELECT admin_id, email, expiry_ms FROM sent WHERE expiry_ms >= ?",
                    (now_ms,),
                )
            )
        for client in clients:
            if client.group != GROUP or not now_ms < client.expiry_time <= end_ms:
                continue
            for admin_id in self._admin_ids:
                key = (admin_id, client.email, client.expiry_time)
                if key in sent:
                    continue
                try:
                    await self._bot.send_message(
                        admin_id, reminder_text(client, now_ms), parse_mode="HTML"
                    )
                except TelegramAPIError as exc:
                    logger.warning("Expiry reminder delivery failed: %s", exc)
                    continue
                with closing(sqlite3.connect(self._database)) as db, db:
                    db.execute("INSERT OR IGNORE INTO sent VALUES (?, ?, ?)", key)
                sent.add(key)
                logger.info(
                    "Expiry reminder delivered (expiry_ms=%s)", client.expiry_time
                )


def reminder_text(client: ClientListItem, now_ms: int) -> str:
    expiry = datetime.fromtimestamp(client.expiry_time / 1000, MSK)
    remaining = human_duration(timedelta(milliseconds=client.expiry_time - now_ms))
    lines = [
        "⏳ <b>Подписка скоро закончится</b>",
        f"Группа: {escape(GROUP)}",
        f"Email: <code>{escape(client.email)}</code>",
        f"Срок: <b>{expiry:%d.%m.%Y %H:%M} МСК</b>",
        f"Осталось: {remaining}",
    ]
    if client.comment:
        lines.append(f"Комментарий: {escape(client.comment[:500])}")
    return "\n".join(lines)
