from datetime import datetime, timedelta

from vpn_monitor.formatting import MSK
from vpn_monitor.models import ServerStatus
from vpn_monitor.node_history import UptimePeriod

from .charts import chart_png, make_chart

UPTIME_COLORS = {
    ServerStatus.ONLINE: "#4ade80",
    ServerStatus.BLOCKED_RU: "#facc15",
    ServerStatus.OFFLINE: "#fb7185",
    None: "#475569",
}


def render_online_chart(
    name: str, samples: list[tuple[int, float]], now: datetime, period: UptimePeriod
) -> bytes:
    figure, axis = make_chart()
    axis.set_title(f"{name} · онлайн за {period.label}", color="white", pad=14)
    axis.set_ylabel("Среднее число пользователей", color="#cbd5e1")
    axis.set_xlabel("Время", color="#cbd5e1")
    end = now.astimezone(MSK)
    start = end - timedelta(seconds=period.duration)
    axis.set_xlim(start, end)
    axis.set_ylim(0, max((count for _, count in samples), default=0) + 1)
    ticks = [start + (end - start) * index / 4 for index in range(5)]
    axis.set_xticks(ticks, [tick.strftime(period.tick_format) for tick in ticks])
    axis.grid(axis="y", color="#334155", alpha=0.55)
    if samples:
        timestamps, counts = online_line_points(samples, period.bucket_seconds)
        axis.plot(
            timestamps,
            counts,
            linewidth=1.6,
            marker=".",
            markersize=3,
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


def online_line_points(
    samples: list[tuple[int, float]], step: int
) -> tuple[list[datetime], list[float]]:
    timestamps: list[datetime] = []
    counts: list[float] = []
    previous = None
    for timestamp, count in samples:
        moment = datetime.fromtimestamp(timestamp, MSK)
        if previous is not None and timestamp - previous > step:
            timestamps.append(moment)
            counts.append(float("nan"))
        timestamps.append(moment)
        counts.append(count)
        previous = timestamp
    return timestamps, counts


def render_uptime_chart(
    locations: list[tuple[str, list[ServerStatus | None]]],
    now: datetime,
    period: UptimePeriod,
) -> bytes:
    figure, axis = make_chart()
    figure.set_size_inches(10, max(2.6, 1.6 + len(locations) * 0.48))
    buckets = range(period.buckets)
    for row, (_, statuses) in enumerate(locations):
        axis.bar(
            buckets,
            [0.62] * period.buckets,
            bottom=row - 0.31,
            width=0.68,
            color=[UPTIME_COLORS[status] for status in statuses],
        )
    start = now.astimezone(MSK) - timedelta(seconds=period.duration)
    ticks = [index * period.buckets / 4 - 0.5 for index in range(5)]
    labels = [
        (start + timedelta(seconds=index * period.duration / 4)).strftime(
            period.tick_format
        )
        for index in range(5)
    ]
    axis.set_xticks(ticks, labels)
    axis.set_yticks(range(len(locations)), [name for name, _ in locations])
    axis.tick_params(length=0, pad=9)
    axis.set_xlim(-0.6, period.buckets - 0.4)
    axis.set_ylim(len(locations) - 0.5, -0.5)
    axis.set_title(f"Аптайм · {period.label}", color="white", pad=14)
    for spine in axis.spines.values():
        spine.set_visible(False)
    return chart_png(figure)
