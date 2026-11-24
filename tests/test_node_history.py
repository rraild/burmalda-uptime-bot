import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from vpn_monitor.admin.node_charts import render_online_chart, render_uptime_chart
from vpn_monitor.clients import NodesClient
from vpn_monitor.formatting import MSK
from vpn_monitor.models import ServerStatus
from vpn_monitor.node_history import UPTIME_DAYS, NodeHistory


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
            days = history.uptime(now)[1]
            self.assertEqual(len(days), UPTIME_DAYS)
            self.assertEqual(
                days[-3:], [None, ServerStatus.ONLINE, ServerStatus.OFFLINE]
            )
            self.assertEqual(
                history.uptime(now - timedelta(minutes=10))[1][-1],
                ServerStatus.BLOCKED_RU,
            )
            samples = history.connected(1, now)
            self.assertEqual([count for _, count in samples], [0, 0, 0])
        self.assertTrue(
            render_online_chart("Node", samples, now).startswith(b"\x89PNG")
        )
        self.assertTrue(
            render_uptime_chart([("Node", days)], now).startswith(b"\x89PNG")
        )
