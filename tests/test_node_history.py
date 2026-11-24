import unittest
from datetime import datetime, timedelta
from math import isnan
from pathlib import Path
from tempfile import TemporaryDirectory

from vpn_monitor.admin.handlers import _chart_selection
from vpn_monitor.admin.keyboards import chart_period_keyboard
from vpn_monitor.admin.node_charts import (
    online_line_points,
    render_online_chart,
    render_uptime_chart,
)
from vpn_monitor.clients import NodesClient
from vpn_monitor.formatting import MSK
from vpn_monitor.models import ServerStatus
from vpn_monitor.node_history import ONLINE_PERIODS, UPTIME_PERIODS, NodeHistory


class HistoryTests(unittest.TestCase):
    def test_node_history_survives_restart_and_keeps_unknown_separate(self) -> None:
        now = datetime(2026, 11, 24, 0, 15, tzinfo=MSK)
        payload = {
            "id": 1,
            "address": "example.com",
            "name": "Node",
            "status": "online",
            "onlineCount": 0,
        }
        online = NodesClient._parse_server(payload)
        offline = NodesClient._parse_server(dict(payload, status="offline"))
        with TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            history = NodeHistory(path)
            for minutes, node, status in (
                (20, online, ServerStatus.ONLINE),
                (12, online, ServerStatus.BLOCKED_RU),
                (8, offline, ServerStatus.OFFLINE),
                (3, online, ServerStatus.ONLINE_UNCHECKED),
            ):
                timestamp = int((now - timedelta(minutes=minutes)).timestamp())
                history.record(timestamp, [node], {node.key: status})
            history = NodeHistory(path)
            for key, period in UPTIME_PERIODS.items():
                with self.subTest(period=key):
                    self.assertEqual(
                        len(history.uptime(now, period)[1]), period.buckets
                    )
            hour = UPTIME_PERIODS["1h"]
            statuses = history.uptime(now, hour)[1]
            self.assertEqual(statuses[20], ServerStatus.ONLINE)
            self.assertEqual(statuses[24], ServerStatus.BLOCKED_RU)
            self.assertEqual(statuses[26], ServerStatus.OFFLINE)
            self.assertIsNone(statuses[-2])
            self.assertEqual(
                history.uptime(now, UPTIME_PERIODS["30d"])[1][-1], ServerStatus.OFFLINE
            )
            period = ONLINE_PERIODS["1h"]
            samples, peak = history.connected(1, now, period)
            self.assertEqual([count for _, count in samples], [0, 0, 0])
            self.assertEqual(peak, 0)
        self.assertTrue(
            render_online_chart("Node", samples, now, period).startswith(b"\x89PNG")
        )
        self.assertTrue(
            render_uptime_chart([("Node", statuses)], now, hour).startswith(b"\x89PNG")
        )

    def test_period_buttons_preserve_selected_node(self) -> None:
        self.assertEqual(_chart_selection("adm:uptime"), ("30d", "all"))
        self.assertEqual(_chart_selection("adm:uptime:7"), ("30d", "7"))
        self.assertEqual(_chart_selection("adm:online:7"), ("24h", "7"))
        for chart, target in (("uptime", "all"), ("uptime", "7"), ("online", "7")):
            buttons = chart_period_keyboard(chart, target, "24h").inline_keyboard[0]
            self.assertEqual(
                [_chart_selection(button.callback_data) for button in buttons],
                [(key, target) for key in UPTIME_PERIODS],
            )
            self.assertTrue(buttons[1].text.startswith("✓"))

    def test_online_line_breaks_only_at_long_gap(self) -> None:
        _, counts = online_line_points([(1000, 2), (1300, 5), (1900, 0)], 300)
        self.assertEqual(counts[:2], [2, 5])
        self.assertTrue(isnan(counts[2]))
        self.assertEqual(counts[3:], [0])

    def test_online_buckets_average_observations_and_keep_raw_peak(self) -> None:
        now = datetime(2026, 11, 24, 12, tzinfo=MSK)
        end = int(now.timestamp())
        with TemporaryDirectory() as directory:
            history = NodeHistory(Path(directory) / "history.sqlite3")
            for age, count in ((4000, 100), (600, 0), (150, 2), (0, 5)):
                server = NodesClient._parse_server(
                    dict(
                        id=1, address="example.com", status="online", onlineCount=count
                    )
                )
                history.record(end - age, [server], {})
            points, peak = history.connected(1, now, ONLINE_PERIODS["1h"])
            self.assertEqual(points, [(end - 450, 0.0), (end - 150, 3.5)])
            self.assertEqual(peak, 5)
            for period in ONLINE_PERIODS.values():
                points, _ = history.connected(1, now, period)
                self.assertLessEqual(len(points), period.buckets)
