from datetime import date, datetime
from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from matplotlib.ticker import StrMethodFormatter

from vpn_monitor.formatting import MSK
from vpn_monitor.payments import PaymentReport, shift_month

from .charts import chart_png, make_chart


def rubles(amount: int) -> str:
    whole, fraction = divmod(amount, 100)
    return f"{whole:,}".replace(",", " ") + f",{fraction:02d} ₽"


def report_text(title: str, report: PaymentReport, *, monthly: bool) -> str:
    lines = [
        f"<b>{title}</b>",
        f"Выручка: <b>{rubles(report.total)}</b>",
        f"Платежей: {report.count}",
    ]
    if monthly:
        lines.append(f"Оплативших клиентов: {report.clients}")
    if report.count:
        average = (report.total + report.count // 2) // report.count
        lines.append(f"Средний платёж: {rubles(average)}")
        lines.extend(("", "<b>Последние оплаты</b>"))
        for payment in report.recent:
            timestamp = datetime.fromtimestamp(payment.paid_at, MSK)
            label = f" · {escape(payment.email)}" if monthly else ""
            lines.append(
                f"{timestamp:%d.%m.%Y %H:%M} · {rubles(payment.amount)}{label}"
            )
    else:
        lines.extend(
            (
                "",
                (
                    "За этот период нет записанных оплат."
                    if monthly
                    else "По этому email пока нет оплат."
                ),
            )
        )
    return "\n".join(lines)


def finance_keyboard(month: date, current: date) -> InlineKeyboardMarkup:
    navigation = []
    if month > date(2000, 1, 1):
        previous = shift_month(month, -1)
        navigation.append(
            InlineKeyboardButton(text="◀", callback_data=f"adm:fin:{previous:%Y%m}")
        )
    if month < current:
        following = shift_month(month, 1)
        navigation.append(
            InlineKeyboardButton(text="▶", callback_data=f"adm:fin:{following:%Y%m}")
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            *([navigation] if navigation else []),
            [
                InlineKeyboardButton(
                    text="📊 График за 12 месяцев",
                    callback_data=f"adm:fchart:{month:%Y%m}",
                )
            ],
            [InlineKeyboardButton(text="↩️ В админ-панель", callback_data="adm:menu")],
        ]
    )


def render_revenue_chart(totals: list[tuple[date, int]]) -> bytes:
    figure, axis = make_chart()
    labels = [f"{month:%m.%y}" for month, _ in totals]
    values = [amount / 100 for _, amount in totals]
    axis.bar(labels, values, color="#38bdf8", zorder=3)
    axis.set_title("Оплаты VPN по месяцам", color="white", pad=14)
    axis.set_ylabel("Выручка, ₽", color="#cbd5e1")
    axis.set_ylim(bottom=0)
    axis.yaxis.set_major_formatter(StrMethodFormatter("{x:,.2f}"))
    axis.grid(axis="y", color="#334155", alpha=0.55, linewidth=0.7)
    if not any(values):
        axis.text(
            0.5,
            0.5,
            "За этот период нет оплат",
            color="white",
            ha="center",
            va="center",
            transform=axis.transAxes,
        )
    return chart_png(figure)
