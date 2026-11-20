import unittest
from datetime import datetime

from vpn_monitor.admin.charts import render_node_chart
from vpn_monitor.admin.inputs import (
    parse_day_delta,
    parse_expiry_date,
    parse_traffic_limit,
)
from vpn_monitor.admin.keyboards import client_keyboard, users_keyboard
from vpn_monitor.admin.sessions import AdminSessions
from vpn_monitor.admin.views import (
    country_flag,
    device_text,
    normalize_search_query,
)
from vpn_monitor.formatting import MSK
from vpn_monitor.panel_api import PanelApiClient, PanelApiError
from vpn_monitor.panel_models import (
    ClientPage,
    HistorySample,
    HwidDevice,
    NodeInfo,
)


def client_page_payload() -> dict:
    return {
        "filtered": 1,
        "items": [
            {
                "email": "alice@example.com",
                "enable": True,
                "expiryTime": 0,
                "inboundIds": [3, 5],
                "limitHwid": 2,
                "limitIp": 1,
                "subId": "abcd1234",
                "totalGB": 10_737_418_240,
                "traffic": {"up": 1024, "down": 4096},
            }
        ],
        "page": 1,
        "pageSize": 8,
        "summary": {
            "active": 1,
            "deactiveCount": 0,
            "depletedCount": 0,
            "onlineCount": 1,
        },
        "total": 1,
    }


def node() -> NodeInfo:
    return NodeInfo(
        id=7,
        name="DE-02",
        remark="Germany",
        status="online",
        enabled=True,
        cpu_pct=12.5,
        mem_pct=45.2,
        net_up=1024,
        net_down=2048,
        client_count=25,
        online_count=5,
        active_count=20,
        disabled_count=2,
        depleted_count=1,
        latency_ms=42,
        uptime_seconds=86400,
        panel_version="v3.8.5",
        xray_state="running",
        xray_version="v26.9.9",
        last_heartbeat=1_700_000_000,
        last_error="",
        xray_error="",
    )


class ModelTests(unittest.TestCase):
    def test_client_page_matches_3x_ui_385_shape(self) -> None:
        page = ClientPage.from_payload(client_page_payload())
        self.assertEqual(page.total, 1)
        self.assertEqual(page.online_count, 1)
        self.assertEqual(page.items[0].traffic.down, 4096)
        self.assertEqual(page.items[0].limit_hwid, 2)

    def test_node_matches_3x_ui_385_shape(self) -> None:
        payload = {
            "id": 7,
            "name": "DE-02",
            "status": "online",
            "enable": True,
            "cpuPct": 12.5,
            "memPct": 45.2,
            "netUp": 1024,
            "netDown": 2048,
            "clientCount": 25,
            "onlineCount": 5,
            "activeCount": 20,
            "disabledCount": 2,
            "depletedCount": 1,
            "latencyMs": 42,
            "uptimeSecs": 86400,
        }
        self.assertEqual(NodeInfo.from_payload(payload).name, "DE-02")


class ViewTests(unittest.TestCase):
    def test_country_flags_are_scalable_and_support_fl_alias(self) -> None:
        self.assertEqual(country_flag("DE-02"), "🇩🇪")
        self.assertEqual(country_flag("FL-01"), "🇫🇮")
        self.assertEqual(country_flag("US-10"), "🇺🇸")
        self.assertEqual(country_flag("custom"), "🌐")

    def test_subscription_url_search_uses_last_path_segment(self) -> None:
        result = normalize_search_query("https://vpn.example/sub/abcd1234")
        self.assertEqual(result, "abcd1234")

    def test_device_view_contains_hwid_details(self) -> None:
        device = HwidDevice(
            id=1,
            first_seen=1_795_997_944_000,
            last_seen=1_795_997_944_000,
            user_agent="INCY/2.6.1/ios CFNetwork/3860 Darwin/25.6.0",
            device_os="iOS",
            os_version="26.6.1",
            device_model="iPhone 16 Pro",
            fingerprint="5c522a1bae55",
        )
        text = device_text(device)
        self.assertIn("iPhone 16 Pro", text)
        self.assertIn("iOS 26.6.1", text)
        self.assertIn("5c522a1bae55", text)

    def test_long_email_does_not_overflow_callback_data(self) -> None:
        payload = client_page_payload()
        payload["items"][0]["email"] = "a" * 180 + "@example.com"
        page = ClientPage.from_payload(payload)
        keyboard = users_keyboard(page, AdminSessions(), mode="u")
        callback_data = keyboard.inline_keyboard[0][0].callback_data
        self.assertLessEqual(len(callback_data.encode("utf-8")), 64)

    def test_client_keyboard_has_native_copy_button(self) -> None:
        url = "https://sub.example.com/sub/abcd1234"
        keyboard = client_keyboard(
            token="token",
            mode="u",
            page=1,
            has_devices=False,
            subscription_url=url,
        )
        button = keyboard.inline_keyboard[0][0]
        self.assertEqual(button.copy_text.text, url)


class InputTests(unittest.TestCase):
    def test_traffic_limit_supports_decimal_and_unlimited(self) -> None:
        self.assertEqual(parse_traffic_limit("1,5"), int(1.5 * 1024**3))
        self.assertEqual(parse_traffic_limit("-1"), 0)

    def test_expiry_date_is_end_of_moscow_day(self) -> None:
        timestamp = parse_expiry_date("16.09.26")
        value = datetime.fromtimestamp(timestamp / 1000, tz=MSK)
        self.assertEqual(value.strftime("%d.%m.%Y %H:%M:%S"), "16.09.2026 23:59:59")

    def test_day_delta_supports_add_and_subtract(self) -> None:
        self.assertEqual(parse_day_delta("+30"), 30)
        self.assertEqual(parse_day_delta("-7"), -7)


class ChartTests(unittest.TestCase):
    def test_chart_is_a_png(self) -> None:
        cpu = (HistorySample(timestamp=1_700_000_000, value=12.5),)
        memory = (HistorySample(timestamp=1_700_000_000, value=45.2),)
        image = render_node_chart(node(), cpu, memory)
        self.assertTrue(image.startswith(b"\x89PNG\r\n\x1a\n"))


class OverviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_overview_aggregates_user_traffic(self) -> None:
        class FakePanelApi(PanelApiClient):
            async def get_clients_page(
                self, *, page: int, page_size: int, search: str = ""
            ) -> ClientPage:
                return ClientPage.from_payload(client_page_payload())

        api = object.__new__(FakePanelApi)
        overview = await api.get_overview()
        self.assertEqual(overview.total_clients, 1)
        self.assertEqual(overview.upload_bytes, 1024)
        self.assertEqual(overview.download_bytes, 4096)


class PanelApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_falls_back_and_caches_supported_bucket(self) -> None:
        class FakePanelApi(PanelApiClient):
            async def get_node_history(
                self,
                node_id: int,
                metric: str,
                *,
                bucket: int = 300,
            ) -> tuple[HistorySample, ...]:
                self.calls.append((metric, bucket))
                if bucket == 360:
                    raise PanelApiError("invalid bucket (unsupported bucket)")
                return (HistorySample(timestamp=1_700_000_000, value=12.5),)

        api = object.__new__(FakePanelApi)
        api._history_bucket = None
        api.calls = []
        cpu, memory, bucket = await api.get_node_load_history(1)
        self.assertEqual(api.calls[:2], [("cpu", 360), ("mem", 360)])
        self.assertEqual(bucket, 180)
        self.assertEqual(len(cpu), 1)
        self.assertEqual(len(memory), 1)
        self.assertEqual(api._history_bucket, 180)

    async def test_client_update_preserves_unmodified_fields(self) -> None:
        class FakePanelApi(PanelApiClient):
            async def _request(
                self,
                method: str,
                path: str,
                *,
                params: dict[str, str | int] | None = None,
                json_body: object = None,
            ) -> object:
                if method == "GET":
                    return {
                        "client": {
                            "id": 42,
                            "email": "alice@example.com",
                            "uuid": "keep-me",
                            "flow": "xtls-rprx-vision",
                            "totalGB": 10,
                        },
                        "tunnelAllowedIPs": {"7": "10.0.0.2/32, fd00::2/128"},
                    }
                self.updated = json_body
                return None

        api = object.__new__(FakePanelApi)
        api.updated = None
        await api.update_client_fields("alice@example.com", totalGB=20)
        self.assertEqual(api.updated["totalGB"], 20)
        self.assertEqual(api.updated["uuid"], "keep-me")
        self.assertEqual(api.updated["id"], "keep-me")
        self.assertEqual(api.updated["flow"], "xtls-rprx-vision")
        self.assertEqual(
            api.updated["allowedIPsByInbound"]["7"],
            ["10.0.0.2/32", "fd00::2/128"],
        )

    async def test_subscription_url_uses_panel_sub_uri(self) -> None:
        class FakePanelApi(PanelApiClient):
            async def _request(
                self,
                method: str,
                path: str,
                *,
                params: dict[str, str | int] | None = None,
                json_body: object = None,
            ) -> object:
                return {"subURI": "https://sub.example.com/custom/"}

        api = object.__new__(FakePanelApi)
        url = await api.get_subscription_url("abcd1234")
        self.assertEqual(url, "https://sub.example.com/custom/abcd1234")


if __name__ == "__main__":
    unittest.main()
