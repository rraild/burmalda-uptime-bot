import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .formatting import MSK


@dataclass(frozen=True, slots=True)
class Payment:
    email: str
    amount: int
    paid_at: int


@dataclass(frozen=True, slots=True)
class PaymentReport:
    total: int
    count: int
    clients: int
    recent: tuple[Payment, ...]


def month_start(moment: datetime) -> date:
    local = moment.astimezone(MSK)
    return date(local.year, local.month, 1)


def shift_month(month: date, offset: int) -> date:
    year, index = divmod(month.year * 12 + month.month - 1 + offset, 12)
    return date(year, index + 1, 1)


def parse_payment(value: str) -> int:
    value = value.strip().replace(",", ".")
    if not re.fullmatch(r"[0-9]{1,9}(?:\.[0-9]{1,2})?", value):
        raise ValueError("Введите сумму в рублях, например 500 или 499,90.")
    rubles, _, fraction = value.partition(".")
    amount = int(rubles) * 100 + int(fraction.ljust(2, "0"))
    if amount == 0:
        raise ValueError("Сумма оплаты должна быть больше нуля.")
    return amount


class PaymentLedger:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        with closing(self._connect()) as db, db:
            db.executescript(
                """CREATE TABLE IF NOT EXISTS payments (
                    admin_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    email TEXT NOT NULL,
                    amount INTEGER NOT NULL CHECK (amount > 0),
                    paid_at INTEGER NOT NULL,
                    month TEXT NOT NULL,
                    PRIMARY KEY (admin_id, message_id)
                );
                CREATE INDEX IF NOT EXISTS payments_month ON payments(month);
                CREATE INDEX IF NOT EXISTS payments_email ON payments(email);"""
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    def record(
        self,
        email: str,
        amount: int,
        paid_at: datetime,
        *,
        admin_id: int,
        message_id: int,
    ) -> None:
        with closing(self._connect()) as db, db:
            db.execute(
                """INSERT INTO payments VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT (admin_id, message_id) DO NOTHING""",
                (
                    admin_id,
                    message_id,
                    email,
                    amount,
                    int(paid_at.timestamp()),
                    month_start(paid_at).isoformat(),
                ),
            )

    def report(
        self, *, month: date | None = None, email: str | None = None
    ) -> PaymentReport:
        where, args = "", ()
        if month is not None:
            where, args = "WHERE month = ?", (month.isoformat(),)
        elif email is not None:
            where, args = "WHERE email = ?", (email,)
        with closing(self._connect()) as db:
            db.execute("BEGIN")
            total, count, clients = db.execute(
                f"SELECT COALESCE(SUM(amount), 0), COUNT(*), COUNT(DISTINCT email)"
                f" FROM payments {where}",
                args,
            ).fetchone()
            recent = db.execute(
                f"SELECT email, amount, paid_at FROM payments {where}"
                " ORDER BY paid_at DESC, message_id DESC, admin_id DESC LIMIT 5",
                args,
            ).fetchall()
        return PaymentReport(total, count, clients, tuple(Payment(*p) for p in recent))

    def monthly_totals(self, end: date) -> list[tuple[date, int]]:
        months = [shift_month(end, offset) for offset in range(-11, 1)]
        with closing(self._connect()) as db:
            totals = dict(
                db.execute(
                    """SELECT month, SUM(amount) FROM payments
                   WHERE month BETWEEN ? AND ? GROUP BY month""",
                    (months[0].isoformat(), end.isoformat()),
                )
            )
        return [(month, totals.get(month.isoformat(), 0)) for month in months]
