import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from vpn_monitor.admin.finances import render_revenue_chart
from vpn_monitor.payments import PaymentLedger, parse_payment


class PaymentTests(unittest.TestCase):
    def test_amount_is_parsed_as_exact_kopeks(self) -> None:
        self.assertEqual(parse_payment("499,90"), 49_990)
        self.assertEqual(parse_payment("500"), 50_000)
        for invalid in ("0", "-5", "1.234", "1e3", ""):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parse_payment(invalid)

    def test_ledger_survives_restart_and_uses_moscow_month(self) -> None:
        paid_at = datetime(2026, 11, 30, 21, 0, tzinfo=timezone.utc)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "payments.sqlite3"
            ledger = PaymentLedger(path)
            ledger.record("alice", 100, paid_at, admin_id=1, message_id=3)
            ledger = PaymentLedger(path)
            ledger.record("alice", 100, paid_at, admin_id=1, message_id=3)
            ledger.record("alice", 90, paid_at, admin_id=2, message_id=3)
            ledger.record("bob", 20, paid_at, admin_id=2, message_id=4)
            december = date(2026, 12, 1)
            report = ledger.report(month=december)
            self.assertEqual((report.total, report.count, report.clients), (210, 3, 2))
            self.assertEqual(ledger.report(email="alice").total, 190)
            totals = ledger.monthly_totals(december)
            self.assertEqual(len(totals), 12)
            self.assertEqual(totals[-2:], [(date(2026, 11, 1), 0), (december, 210)])
            self.assertTrue(render_revenue_chart(totals).startswith(b"\x89PNG"))
