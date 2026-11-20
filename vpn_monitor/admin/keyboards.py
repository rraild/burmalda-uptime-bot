from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from vpn_monitor.panel_models import ClientPage, HwidDevice, NodeInfo

from .sessions import AdminSessions
from .views import country_flag, format_bytes


def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🌍 Локации", callback_data="adm:nodes"),
                InlineKeyboardButton(text="👥 Пользователи", callback_data="adm:u:1"),
            ],
            [
                InlineKeyboardButton(
                    text="🔎 Найти пользователя", callback_data="adm:find"
                )
            ],
            [InlineKeyboardButton(text="➕ Создать подписку", callback_data="adm:new")],
            [
                InlineKeyboardButton(
                    text="🔄 Обновить статистику", callback_data="adm:menu"
                )
            ],
        ]
    )


def nodes_keyboard(nodes: tuple[NodeInfo, ...]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for node in nodes:
        if not node.enabled:
            icon = "⚪"
        elif node.status == "online":
            icon = "🟢"
        else:
            icon = "🔴"
        builder.button(
            text=f"{country_flag(node.name)} {node.name} {icon}",
            callback_data=f"adm:n:{node.id}",
        )
    builder.adjust(2)
    back_button = InlineKeyboardButton(
        text="↩️ В админ-панель", callback_data="adm:menu"
    )
    builder.row(back_button)
    return builder.as_markup()


def node_keyboard(node_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📈 График CPU / RAM",
                    callback_data=f"adm:g:{node_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Обновить", callback_data=f"adm:n:{node_id}"
                ),
                InlineKeyboardButton(text="↩️ Локации", callback_data="adm:nodes"),
            ],
        ]
    )


def users_keyboard(
    page: ClientPage,
    sessions: AdminSessions,
    *,
    mode: str,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for client in page.items:
        token = sessions.client_token(client.email)
        icon = "🟢" if client.enabled else "🔴"
        used = format_bytes(client.traffic.up + client.traffic.down)
        builder.row(
            InlineKeyboardButton(
                text=f"{icon} {_short(client.email)} · {used}",
                callback_data=f"adm:c:{token}:{mode}:{page.page}",
            )
        )

    page_count = max(1, (page.filtered + page.page_size - 1) // page.page_size)
    pagination: list[InlineKeyboardButton] = []
    prefix = "s" if mode == "s" else "u"
    if page.page > 1:
        pagination.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=f"adm:{prefix}:{page.page - 1}",
            )
        )
    pagination.append(
        InlineKeyboardButton(text=f"{page.page}/{page_count}", callback_data="adm:noop")
    )
    if page.page < page_count:
        pagination.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=f"adm:{prefix}:{page.page + 1}",
            )
        )
    builder.row(*pagination)
    builder.row(
        InlineKeyboardButton(text="🔎 Поиск", callback_data="adm:find"),
        InlineKeyboardButton(text="↩️ В админ-панель", callback_data="adm:menu"),
    )
    return builder.as_markup()


def client_keyboard(
    *,
    token: str,
    mode: str,
    page: int,
    has_devices: bool,
    subscription_url: str | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if subscription_url:
        rows.append(
            [
                InlineKeyboardButton(
                    text="📋 Скопировать подписку",
                    copy_text=CopyTextButton(text=subscription_url),
                )
            ]
        )
    if has_devices:
        rows.append(
            [
                InlineKeyboardButton(
                    text="📱 HWID-устройства",
                    callback_data=f"adm:h:{token}:{mode}:{page}",
                )
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="📦 Изменить лимит трафика",
                    callback_data=f"adm:t:{token}:{mode}:{page}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📅 Изменить срок подписки",
                    callback_data=f"adm:e:{token}:{mode}:{page}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔁 Перегенерировать ссылку",
                    callback_data=f"adm:rotate:{token}:{mode}:{page}",
                )
            ],
        ]
    )
    prefix = "s" if mode == "s" else "u"
    rows.append(
        [
            InlineKeyboardButton(
                text="🔄 Обновить",
                callback_data=f"adm:c:{token}:{mode}:{page}",
            ),
            InlineKeyboardButton(
                text="↩️ К списку", callback_data=f"adm:{prefix}:{page}"
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def expiry_keyboard(*, token: str, mode: str, page: int) -> InlineKeyboardMarkup:
    context = f"{token}:{mode}:{page}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📅 Установить дату",
                    callback_data=f"adm:ed:{context}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="➕/➖ Добавить или убрать дни",
                    callback_data=f"adm:ea:{context}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="♾ Сделать бессрочной",
                    callback_data=f"adm:ei:{context}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="↩️ К пользователю",
                    callback_data=f"adm:c:{context}",
                )
            ],
        ]
    )


def confirm_unlimited_keyboard(
    *, token: str, mode: str, page: int
) -> InlineKeyboardMarkup:
    context = f"{token}:{mode}:{page}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, сделать бессрочной",
                    callback_data=f"adm:eiy:{context}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=f"adm:e:{context}",
                )
            ],
        ]
    )


def cancel_input_keyboard(*, token: str, mode: str, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=f"adm:c:{token}:{mode}:{page}",
                )
            ]
        ]
    )


def devices_keyboard(
    devices: tuple[HwidDevice, ...],
    *,
    token: str,
    mode: str,
    page: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for device in devices:
        builder.row(
            InlineKeyboardButton(
                text=f"📱 {_short(device.device_model)}",
                callback_data=f"adm:d:{token}:{device.id}:{mode}:{page}",
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="↩️ К пользователю",
            callback_data=f"adm:c:{token}:{mode}:{page}",
        )
    )
    return builder.as_markup()


def device_keyboard(
    *,
    token: str,
    mode: str,
    page: int,
    device_id: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Удалить устройство",
                    callback_data=f"adm:hwdel:{token}:{device_id}:{mode}:{page}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="↩️ Ко всем устройствам",
                    callback_data=f"adm:h:{token}:{mode}:{page}",
                )
            ],
        ]
    )


def confirm_keyboard(text: str, *, confirm: str, cancel: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=confirm)],
            [InlineKeyboardButton(text="Отмена", callback_data=cancel)],
        ]
    )


def subscription_keyboard(
    url: str, *, token: str, mode: str, page: int
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 Скопировать ссылку", copy_text=CopyTextButton(text=url)
                )
            ],
            [
                InlineKeyboardButton(
                    text="Карточка клиента",
                    callback_data=f"adm:c:{token}:{mode}:{page}",
                )
            ],
        ]
    )


def cancel_search_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data="adm:menu")]
        ]
    )


def _short(value: str, limit: int = 42) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"
