import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .models import Server, ServerStatus

UPTIME_DAYS = 30
SECONDS_PER_DAY = 86400
STATE_RANK = {
    ServerStatus.ONLINE_UNCHECKED: -1,
    ServerStatus.ONLINE: 1,
    ServerStatus.BLOCKED_RU: 2,
    ServerStatus.OFFLINE: 3,
}
STATE_BY_PRIORITY = {
    1: ServerStatus.ONLINE_UNCHECKED,
    **{rank * 2: state for state, rank in STATE_RANK.items() if rank > 0},
}


@dataclass(frozen=True, slots=True)
class UptimePeriod:
    label: str
    bucket_seconds: int
    buckets: int
    bucket_label: str
    tick_format: str

    @property
    def duration(self) -> int:
        return self.bucket_seconds * self.buckets


UPTIME_PERIODS = {
    "1h": UptimePeriod("1 час", 120, 30, "2 минуты", "%H:%M"),
    "24h": UptimePeriod("24 часа", 3600, 24, "1 час", "%H:%M"),
    "7d": UptimePeriod("7 дней", SECONDS_PER_DAY, 7, "24 часа", "%d.%m"),
    "30d": UptimePeriod("30 дней", SECONDS_PER_DAY, UPTIME_DAYS, "24 часа", "%d.%m"),
}

ONLINE_PERIODS = {
    "1h": UptimePeriod("1 час", 300, 12, "5 минут", "%H:%M"),
    "24h": UptimePeriod("24 часа", 1800, 48, "30 минут", "%H:%M"),
    "7d": UptimePeriod("7 дней", 10800, 56, "3 часа", "%d.%m"),
    "30d": UptimePeriod("30 дней", 43200, 60, "12 часов", "%d.%m"),
}


class NodeHistory:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        with closing(sqlite3.connect(path)) as db, db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS samples (
                    sampled_at INTEGER NOT NULL,
                    node_id INTEGER NOT NULL,
                    status_rank INTEGER NOT NULL,
                    online_count INTEGER,
                    PRIMARY KEY (sampled_at, node_id)
                )"""
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS samples_node "
                "ON samples(node_id, sampled_at)"
            )

    def record(
        self, sampled_at: int, servers: list[Server], statuses: dict[str, ServerStatus]
    ) -> None:
        with closing(sqlite3.connect(self._path)) as db, db:
            db.executemany(
                "INSERT OR REPLACE INTO samples VALUES (?, ?, ?, ?)",
                (
                    (
                        sampled_at,
                        server.node_id,
                        STATE_RANK.get(statuses.get(server.key), 0),
                        server.online_count,
                    )
                    for server in servers
                    if server.node_id > 0
                ),
            )
            db.execute(
                "DELETE FROM samples WHERE sampled_at < ?",
                (sampled_at - UPTIME_DAYS * SECONDS_PER_DAY,),
            )

    def connected(
        self, node_id: int, now: datetime, period: UptimePeriod
    ) -> tuple[list[tuple[int, float]], int | None]:
        end = int(now.timestamp())
        start = end - period.duration
        step = period.bucket_seconds
        with closing(sqlite3.connect(self._path)) as db:
            rows = db.execute(
                """SELECT MIN((sampled_at - ?) / ?, ?) AS bucket,
                          AVG(online_count), MAX(online_count)
                   FROM samples
                   WHERE node_id = ? AND sampled_at BETWEEN ? AND ?
                   AND online_count IS NOT NULL GROUP BY bucket ORDER BY bucket""",
                (start, step, period.buckets - 1, node_id, start, end),
            ).fetchall()
        points = [(start + bucket * step + step // 2, mean) for bucket, mean, _ in rows]
        return points, max((peak for _, _, peak in rows), default=None)

    def uptime(
        self, now: datetime, period: UptimePeriod
    ) -> dict[int, list[ServerStatus | None]]:
        end = int(now.timestamp())
        start = end - period.duration
        with closing(sqlite3.connect(self._path)) as db:
            # Older rows stored unchecked probes as 0, but only online nodes
            # recorded an online count. Keep the stored ranks compatible.
            rows = db.execute(
                """SELECT node_id,
                          MIN((sampled_at - ?) / ?, ?) AS bucket,
                          MAX(CASE
                              WHEN status_rank = -1 THEN 1
                              WHEN status_rank = 0 AND online_count IS NOT NULL THEN 1
                              ELSE status_rank * 2
                          END)
                   FROM samples WHERE sampled_at BETWEEN ? AND ?
                   GROUP BY node_id, bucket""",
                (start, period.bucket_seconds, period.buckets - 1, start, end),
            ).fetchall()
        result: dict[int, list[ServerStatus | None]] = {}
        for node_id, bucket, priority in rows:
            if node_id not in result:
                result[node_id] = [None] * period.buckets
            result[node_id][bucket] = STATE_BY_PRIORITY.get(priority)
        return result
