import sqlite3
from contextlib import closing
from datetime import datetime, time, timedelta
from pathlib import Path

from .formatting import MSK
from .models import Server, ServerStatus

UPTIME_DAYS = 30
SECONDS_PER_DAY = 86400
STATE_RANK = {
    ServerStatus.ONLINE: 1,
    ServerStatus.BLOCKED_RU: 2,
    ServerStatus.OFFLINE: 3,
}
STATE_BY_RANK = {rank: state for state, rank in STATE_RANK.items()}


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

    def connected(self, node_id: int, now: datetime) -> list[tuple[int, int]]:
        end = int(now.timestamp())
        with closing(sqlite3.connect(self._path)) as db:
            return db.execute(
                """SELECT sampled_at, online_count FROM samples
                   WHERE node_id = ? AND sampled_at BETWEEN ? AND ?
                   AND online_count IS NOT NULL ORDER BY sampled_at""",
                (node_id, end - SECONDS_PER_DAY, end),
            ).fetchall()

    def uptime(self, now: datetime) -> dict[int, list[ServerStatus | None]]:
        first_day = now.astimezone(MSK).date() - timedelta(days=UPTIME_DAYS - 1)
        start = int(datetime.combine(first_day, time.min, MSK).timestamp())
        with closing(sqlite3.connect(self._path)) as db:
            rows = db.execute(
                """SELECT node_id, (sampled_at - ?) / 86400 AS day, MAX(status_rank)
                   FROM samples WHERE sampled_at BETWEEN ? AND ?
                   GROUP BY node_id, day""",
                (start, start, int(now.timestamp())),
            ).fetchall()
        result: dict[int, list[ServerStatus | None]] = {}
        for node_id, day, rank in rows:
            if node_id not in result:
                result[node_id] = [None] * UPTIME_DAYS
            result[node_id][day] = STATE_BY_RANK.get(rank)
        return result
