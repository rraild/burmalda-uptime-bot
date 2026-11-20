import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from vpn_monitor.panel_api import PanelApiClient, PanelApiError
from vpn_monitor.panel_models import ClientDetails, TrafficStats
from vpn_monitor.subscriptions import GRACE_SECONDS, SubscriptionService


class SubscriptionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.database = Path(temp.name) / "subscriptions.sqlite"
        self.api = AsyncMock(spec=PanelApiClient)
        self.service = SubscriptionService(self.api, self.database)
        self.payload = {
            "client": {
                "email": "original",
                "subId": "old-id",
                "enable": True,
                "expiryTime": 1_900_000_000_000,
                "limitHwid": 10,
                "totalGB": 0,
                "uuid": "old-uuid",
            },
            "inboundIds": [1, 3],
        }
        self.records = {"original": self.payload}
        self.traffic = {"original": TrafficStats(up=100, down=200)}
        self.api.get_client.side_effect = lambda email: ClientDetails.from_payload(
            self.records[email]
        )
        self.api.get_client_payload.return_value = self.payload
        self.api.get_subscription_url.side_effect = (
            lambda sub_id: f"https://sub.example/{sub_id}"
        )
        self.api.get_inbound_ids.return_value = (1, 3)
        self.api.get_client_traffic.side_effect = lambda email: self.traffic.get(
            email, TrafficStats()
        )
        self.api.set_client_traffic.side_effect = self.traffic.__setitem__
        self.api.create_client.side_effect = self._save

    def _save(self, fields, inbound_ids):
        self.records[fields["email"]] = {
            "client": fields,
            "inboundIds": list(inbound_ids),
        }

    async def test_creation_defaults(self) -> None:
        result = await self.service.create("new", "new-id", 1_900_000_000_000, "note")
        self.assertEqual(result.client.total_bytes, 0)
        self.assertEqual(result.client.limit_hwid, 10)
        self.assertEqual(result.client.inbound_ids, (1, 3))
        self.assertEqual(result.client.comment, "note")
        self.assertEqual(
            self.api.create_client.await_args.args[0]["flow"], "xtls-rprx-vision"
        )

    async def test_rotation_preserves_expiry_and_retires_old_after_restart(
        self,
    ) -> None:
        with patch("vpn_monitor.subscriptions.time", return_value=1_800_000_000):
            result = await self.service.rotate("original")
            self.assertEqual(
                result.client.expiry_time, self.payload["client"]["expiryTime"]
            )
            self.assertTrue(result.traffic_copied)
            self.assertNotIn("uuid", self.api.create_client.await_args.args[0])
            await self.service.retire_due()
            self.api.delete_client.assert_not_awaited()
            duplicate = await self.service.rotate("original")
            self.assertEqual(duplicate.url, result.url)
            self.api.create_client.assert_awaited_once()

        service = SubscriptionService(self.api, self.database)
        with patch(
            "vpn_monitor.subscriptions.time", return_value=1_800_000_000 + GRACE_SECONDS
        ):
            await service.retire_due()
            await service.retire_due()
        self.api.delete_client.assert_awaited_once_with("original")

    async def test_failed_rotation_never_schedules_old_client_deletion(self) -> None:
        self.api.create_client.side_effect = PanelApiError("partial failure")
        with self.assertRaises(PanelApiError):
            await self.service.rotate("original")
        await SubscriptionService(self.api, self.database).retire_due()
        self.api.delete_client.assert_not_awaited()
