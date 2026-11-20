import re
from datetime import datetime, timezone
from html import escape
from urllib.parse import parse_qs, unquote, urlsplit

from vpn_monitor.formatting import MSK, human_duration
from vpn_monitor.panel_models import (
    ClientDetails,
    ClientPage,
    HistorySample,
    HwidDevice,
    NodeInfo,
    PanelOverview,
    TrafficStats,
)

COUNTRY_ALIASES = {
    "FL": "FI",  # Common panel shorthand for Finland.
    "UK": "GB",
}


def format_bytes(value: int) -> str:
    amount = float(max(0, value))
    units = ("Б", "КиБ", "МиБ", "ГиБ", "ТиБ", "ПиБ")
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            precision = 0 if unit == "Б" else 2
            return f"{amount:.{precision}f} {unit}"
        amount /= 1024
    return f"{amount:.2f} ПиБ"


def country_flag(name: str) -> str:
    match = re.match(r"^([A-Za-z]{2})(?:[-_\s]|$)", name.strip())
    if not match:
        return "🌐"
    code = COUNTRY_ALIASES.get(match.group(1).upper(), match.group(1).upper())
    return "".join(chr(127397 + ord(letter)) for letter in code)


def normalize_search_query(value: str) -> str:
    query = value.strip()
    if "://" not in query:
        return query
    parsed = urlsplit(query)
    params = parse_qs(parsed.query)
    for key in ("subId", "sub", "id", "token"):
        if params.get(key):
            return params[key][0].strip()
    segments = [unquote(segment) for segment in parsed.path.split("/") if segment]
    return segments[-1].strip() if segments else query


def _timestamp(value: int, *, include_seconds: bool = False) -> str:
    if value <= 0:
        return "—"
    seconds = value / 1000 if value > 10_000_000_000 else value
    template = "%d.%m.%Y, %H:%M:%S" if include_seconds else "%d.%m.%Y %H:%M"
    return (
        datetime.fromtimestamp(seconds, tz=timezone.utc)
        .astimezone(MSK)
        .strftime(template)
    )


def _node_icon(node: NodeInfo) -> str:
    if not node.enabled:
        return "⚪"
    if node.status != "online" or node.xray_state not in {"running", "unknown", ""}:
        return "🔴"
    return "🟢"


def overview_text(overview: PanelOverview) -> str:
    total_traffic = overview.upload_bytes + overview.download_bytes
    return "\n".join(
        [
            "<b>Админ-панель 3x-ui</b>",
            "",
            f"👥 Всего пользователей: <b>{overview.total_clients}</b>",
            f"🟢 Активных: <b>{overview.active_clients}</b>",
            f"⚡ Сейчас онлайн: <b>{overview.online_clients}</b>",
            f"⛔ Отключённых: <b>{overview.disabled_clients}</b>",
            f"🪫 Исчерпали лимит: <b>{overview.depleted_clients}</b>",
            "",
            f"⬆️ Вкачано: <b>{format_bytes(overview.upload_bytes)}</b>",
            f"⬇️ Выкачано: <b>{format_bytes(overview.download_bytes)}</b>",
            f"📦 Всего трафика: <b>{format_bytes(total_traffic)}</b>",
        ]
    )


def locations_text(nodes: tuple[NodeInfo, ...]) -> str:
    lines = ["<b>Статистика локаций</b>", ""]
    if not nodes:
        lines.append("В панели нет подключённых узлов.")
        return "\n".join(lines)
    for node in nodes:
        lines.extend(
            [
                f"{country_flag(node.name)} <b>{escape(node.name)}</b> "
                f"{_node_icon(node)}",
                f"CPU {node.cpu_pct:.1f}% · RAM {node.mem_pct:.1f}% · "
                f"онлайн {node.online_count}/{node.client_count}",
                "",
            ]
        )
    return "\n".join(lines).rstrip()


def node_text(node: NodeInfo) -> str:
    status = "В сети" if node.status == "online" else node.status
    xray = "Работает" if node.xray_state == "running" else node.xray_state
    lines = [
        f"{country_flag(node.name)} <b>{escape(node.name)}</b>",
        f"Статус узла: {_node_icon(node)} <b>{escape(status)}</b>",
        f"Xray: <b>{escape(xray or 'неизвестно')}</b>",
        "",
        f"CPU: <b>{node.cpu_pct:.1f}%</b>",
        f"RAM: <b>{node.mem_pct:.1f}%</b>",
        f"Задержка панели: <b>{node.latency_ms} мс</b>",
        f"Uptime: <b>{human_duration_from_seconds(node.uptime_seconds)}</b>",
        "",
        f"Пользователей: <b>{node.client_count}</b>",
        f"Сейчас онлайн: <b>{node.online_count}</b>",
        f"Активных: <b>{node.active_count}</b>",
        f"Отключённых: <b>{node.disabled_count}</b>",
        f"Исчерпали лимит: <b>{node.depleted_count}</b>",
        "",
        f"Сеть ↑ {format_bytes(node.net_up)} · ↓ {format_bytes(node.net_down)}",
        f"Панель: {escape(node.panel_version)} · Xray: {escape(node.xray_version)}",
        f"Последний heartbeat: {_timestamp(node.last_heartbeat)} МСК",
    ]
    error = node.xray_error or node.last_error
    if error:
        lines.extend(["", f"⚠️ <code>{escape(error)}</code>"])
    return "\n".join(lines)


def human_duration_from_seconds(seconds: int) -> str:
    from datetime import timedelta

    return human_duration(timedelta(seconds=max(0, seconds)))


def users_text(page: ClientPage, *, search: str = "") -> str:
    title = "Результаты поиска" if search else "Пользователи"
    lines = [f"<b>{title}</b>"]
    if search:
        lines.append(f"Запрос: <code>{escape(search)}</code>")
    lines.append(f"Найдено: <b>{page.filtered}</b>")
    lines.append("")
    if not page.items:
        lines.append("Ничего не найдено.")
    else:
        for item in page.items:
            icon = "🟢" if item.enabled else "🔴"
            used = item.traffic.up + item.traffic.down
            lines.append(f"{icon} <b>{escape(item.email)}</b> — {format_bytes(used)}")
    page_count = max(1, (page.filtered + page.page_size - 1) // page.page_size)
    lines.extend(["", f"Страница {page.page} из {page_count}"])
    return "\n".join(lines)


def client_text(
    client: ClientDetails,
    traffic: TrafficStats,
    devices: tuple[HwidDevice, ...],
) -> str:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    expired = 0 < client.expiry_time <= now_ms
    used = traffic.up + traffic.down
    depleted = client.total_bytes > 0 and used >= client.total_bytes
    if not client.enabled:
        status = "🔴 Отключён"
    elif expired:
        status = "🔴 Срок истёк"
    elif depleted:
        status = "🟠 Лимит исчерпан"
    else:
        status = "🟢 Активен"

    quota = format_bytes(client.total_bytes) if client.total_bytes else "Безлимит"
    remaining = (
        format_bytes(max(0, client.total_bytes - used))
        if client.total_bytes
        else "Безлимит"
    )
    lines = [
        f"<b>Пользователь {escape(client.email)}</b>",
        status,
        "",
        f"⬆️ Вкачано: <b>{format_bytes(traffic.up)}</b>",
        f"⬇️ Выкачано: <b>{format_bytes(traffic.down)}</b>",
        f"📦 Использовано: <b>{format_bytes(used)}</b>",
        f"Лимит: <b>{quota}</b> · осталось: <b>{remaining}</b>",
        f"Срок действия: <b>{_timestamp(client.expiry_time)}</b>",
        f"Последний онлайн: <b>{_timestamp(traffic.last_online)}</b>",
        "",
        f"Sub ID: <code>{escape(client.sub_id or '—')}</code>",
        f"UUID: <code>{escape(client.uuid or '—')}</code>",
        f"Локаций/inbound: <b>{len(client.inbound_ids)}</b>",
        f"Лимит IP: <b>{client.limit_ip or '∞'}</b>",
        f"Лимит HWID: <b>{client.limit_hwid or '∞'}</b>",
        f"Зарегистрировано устройств: <b>{len(devices)}</b>",
    ]
    if client.group:
        lines.append(f"Группа: <b>{escape(client.group)}</b>")
    if client.comment:
        lines.append(f"Комментарий: {escape(client.comment)}")
    return "\n".join(lines)


def devices_text(email: str, devices: tuple[HwidDevice, ...]) -> str:
    lines = [
        f"<b>Устройства {escape(email)}</b>",
        f"Зарегистрировано: <b>{len(devices)}</b>",
        "",
    ]
    if not devices:
        lines.append("HWID-устройств нет.")
    else:
        lines.append("Выберите устройство:")
    return "\n".join(lines)


def device_text(device: HwidDevice) -> str:
    os_line = " ".join(part for part in (device.device_os, device.os_version) if part)
    last_seen = _timestamp(device.last_seen, include_seconds=True)
    return "\n".join(
        [
            f"<b>{escape(device.device_model)}</b>",
            escape(os_line or "Неизвестная ОС"),
            f"Первое появление: {_timestamp(device.first_seen, include_seconds=True)}",
            f"Последнее появление: {last_seen}",
            escape(device.user_agent or "User-Agent не передан"),
            f"<code>Отпечаток HWID: {escape(device.fingerprint or '—')}</code>",
        ]
    )


def history_caption(
    node: NodeInfo,
    cpu: tuple[HistorySample, ...],
    memory: tuple[HistorySample, ...],
    bucket: int,
) -> str:
    points = max(len(cpu), len(memory))
    return (
        f"📈 <b>{escape(node.name)}</b> — CPU и RAM за период, "
        f"точек: {points}, шаг: {bucket} сек."
    )
