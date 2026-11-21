import asyncio
import logging
from contextlib import suppress
from pathlib import Path

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import SimpleEventIsolation

from vpn_monitor.admin.handlers import build_admin_router
from vpn_monitor.clients import CheckHostClient, NodesClient
from vpn_monitor.config import Settings
from vpn_monitor.handlers import build_router
from vpn_monitor.monitor import MonitorService
from vpn_monitor.panel_api import PanelApiClient
from vpn_monitor.probes import ServerChecker
from vpn_monitor.reminders import ExpiryReminders
from vpn_monitor.state import MonitorState
from vpn_monitor.subscriptions import SubscriptionService


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


async def main() -> None:
    ready = Path("/tmp/vpn-monitor-ready")
    if ready.parent.is_dir():
        ready.unlink(missing_ok=True)
    configure_logging()
    settings = Settings.from_env()
    if not settings.admin_ids:
        logging.getLogger(__name__).warning(
            "ADMIN_IDS is empty; the admin panel is disabled"
        )
    bot = Bot(token=settings.bot_token)
    dispatcher = Dispatcher(events_isolation=SimpleEventIsolation())
    state = MonitorState()

    timeout = aiohttp.ClientTimeout(total=settings.http_timeout_seconds)
    async with aiohttp.ClientSession(timeout=timeout) as http:
        panel_api = PanelApiClient(
            http,
            base_url=settings.panel_api_url,
            api_key=settings.api_key,
            verify_ssl=settings.nodes_api_verify_ssl,
        )
        subscriptions = SubscriptionService(
            panel_api, Path(settings.state_dir) / "subscriptions.sqlite3"
        )
        dispatcher.include_router(
            build_admin_router(
                panel_api,
                subscriptions,
                admin_ids=settings.admin_ids,
                page_size=settings.admin_page_size,
            )
        )
        dispatcher.include_router(build_router(state))
        nodes = NodesClient(
            http,
            url=settings.nodes_api_url,
            api_key=settings.api_key,
            verify_ssl=settings.nodes_api_verify_ssl,
        )
        check_host = CheckHostClient(
            http,
            result_delay_seconds=settings.check_host_result_delay_seconds,
        )
        checker = ServerChecker(
            check_host,
            confirmation_delay_seconds=settings.confirmation_delay_seconds,
            max_concurrent_checks=settings.max_concurrent_checks,
        )
        monitor = MonitorService(bot, settings, nodes, checker, state)
        monitor_task = asyncio.create_task(monitor.run(), name="vpn-monitor")
        cleanup_task = asyncio.create_task(
            subscriptions.run_cleanup(), name="subscription-cleanup"
        )
        reminder_task = None

        try:
            await bot.delete_webhook(drop_pending_updates=False)
            if settings.admin_ids:
                reminders = ExpiryReminders(
                    bot,
                    panel_api,
                    settings.admin_ids,
                    Path(settings.state_dir) / "expiry-reminders.sqlite3",
                )
                reminder_task = asyncio.create_task(
                    reminders.run(), name="expiry-reminders"
                )
            if ready.parent.is_dir():
                ready.touch()
            await dispatcher.start_polling(
                bot,
                allowed_updates=dispatcher.resolve_used_update_types(),
                close_bot_session=False,
            )
        finally:
            if ready.parent.is_dir():
                ready.unlink(missing_ok=True)
            monitor.stop()
            cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup_task
            if reminder_task is not None:
                reminder_task.cancel()
                with suppress(asyncio.CancelledError):
                    await reminder_task
            await monitor_task
            await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
