import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from vpn_monitor.panel_models import ClientListItem
from vpn_monitor.reminders import DAY_MS, GROUP, ExpiryReminders


class ExpiryReminderTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_exact_group_and_window_and_once_after_restart(self):
        now = 1_800_000_000
        now_ms = now * 1000
        payloads = [
            ("target", GROUP, now_ms + DAY_MS),
            ("other-group", "СВОи", now_ms + 1000),
            ("expired", GROUP, now_ms),
            ("unlimited", GROUP, 0),
            ("too-early", GROUP, now_ms + DAY_MS + 1),
        ]
        api, bot = AsyncMock(), AsyncMock()
        api.get_expiring_clients.return_value = [
            ClientListItem.from_payload(
                {"email": email, "group": group, "expiryTime": expiry}
            )
            for email, group, expiry in payloads
        ]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "test.sqlite3"
            with patch("vpn_monitor.reminders.time", return_value=now):
                for _ in range(2):
                    service = ExpiryReminders(bot, api, frozenset({1, 2}), path)
                    await service.check_once()
        self.assertEqual(bot.send_message.await_count, 2)
        for call in bot.send_message.await_args_list:
            self.assertIn("<code>target</code>", call.args[1])
            self.assertNotIn("МСК", call.args[1])
        api.get_expiring_clients.assert_awaited_with(now_ms, now_ms + DAY_MS)
