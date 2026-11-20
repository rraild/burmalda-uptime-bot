from datetime import datetime, timezone
from io import BytesIO

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.dates import DateFormatter
from matplotlib.figure import Figure

from vpn_monitor.formatting import MSK
from vpn_monitor.panel_models import HistorySample, NodeInfo


def render_node_chart(
    node: NodeInfo,
    cpu: tuple[HistorySample, ...],
    memory: tuple[HistorySample, ...],
) -> bytes:
    figure = Figure(figsize=(10, 5.6), dpi=130, facecolor="#101827")
    FigureCanvasAgg(figure)
    axis = figure.subplots()
    axis.set_facecolor("#101827")

    if cpu:
        axis.plot(
            [_date(sample.timestamp) for sample in cpu],
            [sample.value for sample in cpu],
            color="#38bdf8",
            linewidth=2.2,
            label="CPU",
        )
    if memory:
        axis.plot(
            [_date(sample.timestamp) for sample in memory],
            [sample.value for sample in memory],
            color="#a78bfa",
            linewidth=2.2,
            label="RAM",
        )

    axis.set_title(f"{node.name}: нагрузка CPU / RAM", color="white", pad=14)
    axis.set_ylabel("Использование, %", color="#cbd5e1")
    axis.set_ylim(0, 100)
    axis.grid(color="#334155", alpha=0.55, linewidth=0.7)
    axis.tick_params(colors="#cbd5e1")
    axis.xaxis.set_major_formatter(DateFormatter("%H:%M", tz=MSK))
    for spine in axis.spines.values():
        spine.set_color("#475569")
    if cpu or memory:
        legend = axis.legend(facecolor="#1e293b", edgecolor="#475569")
        for text in legend.get_texts():
            text.set_color("white")
    else:
        axis.text(
            0.5,
            0.5,
            "История пока пуста",
            transform=axis.transAxes,
            ha="center",
            va="center",
            color="white",
            fontsize=16,
        )
    figure.autofmt_xdate()
    figure.tight_layout()
    output = BytesIO()
    figure.savefig(output, format="png", facecolor=figure.get_facecolor())
    return output.getvalue()


def _date(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)
