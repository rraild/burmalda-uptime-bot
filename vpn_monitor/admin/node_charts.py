from datetime import datetime, timedelta

from matplotlib.dates import DateFormatter
from matplotlib.ticker import MaxNLocator

from vpn_monitor.formatting import MSK
from vpn_monitor.models import ServerStatus
from vpn_monitor.node_history import UPTIME_DAYS

from .charts import chart_png, make_chart

UPTIME_COLORS = {
    ServerStatus.ONLINE: "#4ade80",
    ServerStatus.BLOCKED_RU: "#facc15",
    ServerStatus.OFFLINE: "#fb7185",
    None: "#475569",
}


def render_online_chart(
    name: str, samples: list[tuple[int, int]], now: datetime
) -> bytes:
    figure, axis = make_chart()
    axis.set_title(f"{name} · пользователи онлайн", color="white", pad=14)
    axis.set_ylabel("Пользователи", color="#cbd5e1")
    axis.set_xlabel("Время (МСК)", color="#cbd5e1")
    axis.set_xlim(now - timedelta(days=1), now)
    axis.set_ylim(0, max((count for _, count in samples), default=0) + 1)
    axis.yaxis.set_major_locator(MaxNLocator(integer=True))
    axis.xaxis.set_major_formatter(DateFormatter("%d.%m %H:%M", tz=MSK))
    axis.grid(axis="y", color="#334155", alpha=0.55)
    if samples:
        axis.scatter(
            [datetime.fromtimestamp(timestamp, MSK) for timestamp, _ in samples],
            [count for _, count in samples],
            s=10,
            color="#38bdf8",
        )
    else:
        axis.text(
            0.5,
            0.5,
            "История ещё не накопилась",
            transform=axis.transAxes,
            ha="center",
            va="center",
            color="white",
        )
    figure.autofmt_xdate()
    return chart_png(figure)


def render_uptime_chart(
    locations: list[tuple[str, list[ServerStatus | None]]], now: datetime
) -> bytes:
    figure, axis = make_chart()
    figure.set_size_inches(10, max(2.6, 1.6 + len(locations) * 0.48))
    days = range(UPTIME_DAYS)
    for row, (_, statuses) in enumerate(locations):
        axis.bar(
            days,
            [0.62] * UPTIME_DAYS,
            bottom=row - 0.31,
            width=0.68,
            color=[UPTIME_COLORS[status] for status in statuses],
        )
    today = now.astimezone(MSK).date()
    ticks = [0, 7, 14, 21, UPTIME_DAYS - 1]
    labels = [
        f"{today - timedelta(days=UPTIME_DAYS - 1 - tick):%d.%m}" for tick in ticks
    ]
    axis.set_xticks(ticks, labels)
    axis.set_yticks(range(len(locations)), [name for name, _ in locations])
    axis.tick_params(length=0, pad=9)
    axis.set_xlim(-0.6, UPTIME_DAYS - 0.4)
    axis.set_ylim(len(locations) - 0.5, -0.5)
    axis.set_title("Аптайм локаций · 30 дней", color="white", pad=14)
    for spine in axis.spines.values():
        spine.set_visible(False)
    return chart_png(figure)
