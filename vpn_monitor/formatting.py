from datetime import datetime, timedelta, timezone
from html import escape

from .models import ServerStatus, StateSnapshot, Transition

MSK = timezone(timedelta(hours=3))

STATUS_VIEW = {
    ServerStatus.ONLINE: ("🟢", "В сети"),
    ServerStatus.ONLINE_UNCHECKED: ("🟢", "В сети · доступ из РФ не подтверждён"),
    ServerStatus.BLOCKED_RU: ("🟡", "Недоступен из РФ"),
    ServerStatus.OFFLINE: ("🔴", "Упал (оффлайн)"),
}


def human_duration(delta: timedelta) -> str:
    seconds = max(0, int(delta.total_seconds()))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days} д")
    if hours:
        parts.append(f"{hours} ч")
    if minutes:
        parts.append(f"{minutes} мин")
    if not parts:
        parts.append(f"{seconds} сек")
    return " ".join(parts[:2])


def dashboard(snapshot: StateSnapshot, *, confirmation_delay: float) -> str:
    lines = ["<b>Статус нод:</b>", ""]
    if not snapshot.locations:
        lines.append("Нет данных о локациях.")
    for location in snapshot.locations:
        icon, label = STATUS_VIEW[location.status]
        lines.append(f"{escape(location.server.name)}: {icon} {label}")
    now = datetime.now(timezone.utc)
    now_msk = now.astimezone(MSK).strftime("%H:%M:%S %d.%m.%Y")
    lines.extend(
        [
            "",
            f"<i>Последнее обновление: {now_msk} (МСК)</i>",
            "<i>Проверка из РФ при сбое повторяется "
            f"через {confirmation_delay:g} сек.</i>",
        ]
    )
    return "\n".join(lines)


def summary(snapshot: StateSnapshot) -> str:
    now = datetime.now(timezone.utc)
    counts = {status: 0 for status in ServerStatus}
    for location in snapshot.locations:
        counts[location.status] += 1
    online = counts[ServerStatus.ONLINE] + counts[ServerStatus.ONLINE_UNCHECKED]

    lines = [
        "<b>Сводка мониторинга</b>",
        f"Бот работает: <b>{human_duration(now - snapshot.started_at)}</b>",
        (
            f"Локации: <b>{len(snapshot.locations)}</b> · "
            f"🟢 {online} · "
            f"🟡 {counts[ServerStatus.BLOCKED_RU]} · "
            f"🔴 {counts[ServerStatus.OFFLINE]}"
        ),
        "",
        "<b>Текущее состояние:</b>",
    ]
    if not snapshot.locations:
        lines.append("Мониторинг ещё не получил список локаций.")
    for location in snapshot.locations:
        icon, label = STATUS_VIEW[location.status]
        duration = human_duration(now - location.state_since)
        suffix = "в сети" if location.status == ServerStatus.ONLINE else label.lower()
        lines.append(f"{icon} {escape(location.server.name)} — {suffix} {duration}")

    lines.extend(["", "<b>Последние сбои с запуска:</b>"])
    if not snapshot.incidents:
        lines.append("Сбоев не зафиксировано.")
    else:
        for incident in reversed(snapshot.incidents[-10:]):
            _, label = STATUS_VIEW[incident.status]
            started = incident.started_at.astimezone(MSK).strftime("%d.%m %H:%M")
            if incident.ended_at is None:
                ending = f"идёт {human_duration(now - incident.started_at)}"
            else:
                ending = (
                    f"длился {human_duration(incident.ended_at - incident.started_at)}"
                )
            lines.append(
                f"• {escape(incident.server_name)} — {label.lower()}, "
                f"{started} МСК, {ending}"
            )
    return "\n".join(lines)


def alert(transition: Transition) -> str:
    name = escape(transition.server.name)
    if transition.current == ServerStatus.OFFLINE:
        return f"🔴 Локация {name} полностью упала!"
    if transition.current == ServerStatus.BLOCKED_RU:
        return f"🟡 Локация {name} недоступна из РФ!"
    if transition.current == ServerStatus.ONLINE_UNCHECKED:
        return f"✅ Локация {name} снова в сети. Доступ из РФ пока не подтверждён."
    return f"✅ Локация {name} снова в сети и доступна из РФ!"
