import unittest

from vpn_monitor.clients import CheckHostClient, NodesClient
from vpn_monitor.models import Server, ServerStatus
from vpn_monitor.probes import ServerChecker
from vpn_monitor.state import MonitorState


class FakeCheckHost:
    async def is_available_from_ru(self, url: str) -> bool:
        return True


class SequenceChecker(ServerChecker):
    def __init__(self, results: list[ServerStatus]) -> None:
        super().__init__(
            FakeCheckHost(),
            confirmation_delay_seconds=0,
            max_concurrent_checks=1,
        )
        self.results = iter(results)

    async def _check_once(self, server: Server) -> ServerStatus:
        return next(self.results)


class ConfirmationTests(unittest.IsolatedAsyncioTestCase):
    async def test_transient_failure_is_suppressed(self) -> None:
        checker = SequenceChecker([ServerStatus.BLOCKED_RU, ServerStatus.ONLINE])
        result = await checker.check_confirmed(Server("A", "a.example", 443))
        self.assertEqual(result, ServerStatus.ONLINE)

    async def test_repeated_failure_is_confirmed(self) -> None:
        checker = SequenceChecker([ServerStatus.BLOCKED_RU, ServerStatus.BLOCKED_RU])
        result = await checker.check_confirmed(Server("A", "a.example", 443))
        self.assertEqual(result, ServerStatus.BLOCKED_RU)

    async def test_panel_offline_status_is_used_without_external_check(self) -> None:
        checker = ServerChecker(
            None, confirmation_delay_seconds=0, max_concurrent_checks=1
        )
        server = Server("A", "a.example", 443, panel_status="offline")
        self.assertEqual(await checker.check_confirmed(server), ServerStatus.OFFLINE)


class CheckHostParsingTests(unittest.TestCase):
    def test_http_error_still_proves_reachability(self) -> None:
        self.assertTrue(
            CheckHostClient._probe_outcome([[0, 0.17, "Not Found", "404", "192.0.2.1"]])
        )

    def test_nested_error(self) -> None:
        self.assertFalse(
            CheckHostClient._probe_outcome([[0, 0.1, "Timed out", None, None]])
        )

    def test_missing_result(self) -> None:
        self.assertIsNone(CheckHostClient._probe_outcome(None))

    def test_node_uses_scheme_and_panel_status(self) -> None:
        node = NodesClient._parse_server(
            {
                "id": 7,
                "address": "node.example",
                "scheme": "http",
                "port": 8080,
                "status": "online",
                "enable": True,
            }
        )
        self.assertEqual(node.key, "node:7")
        self.assertEqual(node.panel_status, "online")
        self.assertEqual(node.probe_url, "http://node.example:8080/")


class StateTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_ru_verdict_does_not_clear_a_block(self) -> None:
        state = MonitorState()
        server = Server("One", "one.example", 443)
        await state.apply([server], {server.key: ServerStatus.BLOCKED_RU})
        transitions = await state.apply(
            [server], {server.key: ServerStatus.ONLINE_UNCHECKED}
        )
        self.assertEqual(transitions, [])
        self.assertEqual(
            (await state.snapshot()).locations[0].status, ServerStatus.BLOCKED_RU
        )

    async def test_matches_locations_by_host_and_port_not_order(self) -> None:
        state = MonitorState()
        one = Server("One", "one.example", 443)
        two = Server("Two", "two.example", 443)
        await state.apply(
            [one, two],
            {one.key: ServerStatus.ONLINE, two.key: ServerStatus.OFFLINE},
        )
        transitions = await state.apply(
            [two, one],
            {one.key: ServerStatus.ONLINE, two.key: ServerStatus.OFFLINE},
        )
        self.assertEqual(transitions, [])


if __name__ == "__main__":
    unittest.main()
