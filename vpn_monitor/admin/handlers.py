import asyncio
import logging
import sqlite3
from collections.abc import Callable
from datetime import date, datetime
from html import escape

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)

from vpn_monitor.panel_api import PanelApiClient, PanelApiError
from vpn_monitor.panel_models import ClientDetails, HwidDevice, TrafficStats
from vpn_monitor.formatting import MSK
from vpn_monitor.node_history import ONLINE_PERIODS, UPTIME_PERIODS, NodeHistory
from vpn_monitor.payments import PaymentLedger, month_start, parse_payment
from vpn_monitor.subscriptions import Subscription, SubscriptionService, new_identity

from .charts import render_node_chart
from .finances import finance_keyboard, render_revenue_chart, report_text, rubles
from .node_charts import render_online_chart, render_uptime_chart
from .inputs import parse_day_delta, parse_expiry_date, parse_traffic_limit
from .keyboards import (
    cancel_input_keyboard,
    cancel_search_keyboard,
    chart_period_keyboard,
    client_keyboard,
    confirm_keyboard,
    confirm_unlimited_keyboard,
    device_keyboard,
    devices_keyboard,
    expiry_keyboard,
    main_keyboard,
    node_keyboard,
    nodes_keyboard,
    subscription_keyboard,
    users_keyboard,
)
from .sessions import AdminSessions
from .views import (
    client_text,
    device_text,
    devices_text,
    format_bytes,
    history_caption,
    locations_text,
    node_text,
    normalize_search_query,
    overview_text,
    users_text,
)

logger = logging.getLogger(__name__)


class AdminSearch(StatesGroup):
    waiting_for_query = State()
    waiting_for_traffic_limit = State()
    waiting_for_expiry_date = State()
    waiting_for_expiry_days = State()
    waiting_for_new_expiry = State()
    waiting_for_new_comment = State()
    waiting_for_payment = State()


def build_admin_router(
    api: PanelApiClient,
    subscriptions: SubscriptionService,
    ledger: PaymentLedger,
    node_history: NodeHistory,
    *,
    admin_ids: frozenset[int],
    page_size: int,
) -> Router:
    router = Router(name="admin-panel")
    sessions = AdminSessions()

    def is_admin(user_id: int | None) -> bool:
        return user_id is not None and user_id in admin_ids

    @router.message(AdminSearch.waiting_for_payment, ~F.text.startswith("/"))
    async def payment_input(message: Message, state: FSMContext) -> None:
        if not _is_private_admin_message(message, is_admin):
            return
        try:
            amount = parse_payment(message.text or "")
            token, mode, page, email = await _state_client_context(state, sessions)
        except ValueError as exc:
            await message.answer(str(exc))
            return
        try:
            ledger.record(
                email,
                amount,
                message.date,
                admin_id=message.chat.id,
                message_id=message.message_id,
            )
        except sqlite3.Error:
            logger.exception("Payment record failed")
            await message.answer("Не удалось сохранить оплату. Повторите ввод суммы.")
            return
        await state.clear()
        await message.answer(
            f"✅ Оплата записана.\n{escape(email)} · <b>{rubles(amount)}</b>",
            parse_mode="HTML",
            reply_markup=confirm_keyboard(
                "🧾 Оплаты клиента",
                confirm=f"adm:ph:{token}:{mode}:{page}",
                cancel=f"adm:c:{token}:{mode}:{page}",
                cancel_text="↩️ К клиенту",
            ),
        )

    @router.message(Command("admin"))
    async def admin_command(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id if message.from_user else None):
            await message.answer("⛔ Доступ к админ-панели запрещён.")
            return
        if message.chat.type != ChatType.PRIVATE:
            await message.answer("Админ-панель доступна только в личном чате.")
            return
        await state.clear()
        try:
            overview = await api.get_overview()
        except PanelApiError as exc:
            await _message_api_error(message, exc)
            return
        await message.answer(
            overview_text(overview),
            parse_mode="HTML",
            reply_markup=main_keyboard(),
        )

    @router.message(AdminSearch.waiting_for_new_expiry)
    async def new_expiry(message: Message, state: FSMContext) -> None:
        if not _is_private_admin_message(message, is_admin):
            return
        raw = (message.text or "").strip()
        try:
            expiry = 0 if raw == "-1" else parse_expiry_date(raw)
            if expiry and expiry <= int(datetime.now(MSK).timestamp() * 1000):
                raise ValueError("Выберите дату, срок которой ещё не истёк.")
        except ValueError as exc:
            await message.answer(str(exc))
            return
        email, sub_id = new_identity()
        await state.set_data({"expiry": expiry, "email": email, "sub_id": sub_id})
        await state.set_state(AdminSearch.waiting_for_new_comment)
        await message.answer(
            "Введите комментарий к подписке. "
            "Отправьте «-», чтобы оставить поле пустым.",
            reply_markup=cancel_search_keyboard(),
        )

    @router.message(AdminSearch.waiting_for_new_comment)
    async def new_comment(message: Message, state: FSMContext) -> None:
        if not _is_private_admin_message(message, is_admin):
            return
        comment = (message.text or "").strip()
        if not comment or len(comment) > 500:
            await message.answer("Введите комментарий длиной до 500 символов или «-».")
            return
        data = await state.get_data()
        try:
            result = await subscriptions.create(
                data["email"],
                data["sub_id"],
                data["expiry"],
                "" if comment == "-" else comment,
            )
        except PanelApiError as exc:
            logger.warning("Subscription creation was not confirmed: %s", exc)
            await message.answer(
                f"3x-ui не подтвердил создание клиента {data['email']}. "
                "При повторном вводе комментария бот использует тот же email."
            )
            return
        await state.clear()
        text, keyboard = _subscription_result(result, sessions, "u", 1)
        await message.answer(text, parse_mode="HTML", reply_markup=keyboard)

    @router.message(AdminSearch.waiting_for_query)
    async def search_query(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id if message.from_user else None):
            await state.clear()
            await message.answer("⛔ Доступ запрещён.")
            return
        query = normalize_search_query(message.text or "")
        if not query:
            await message.answer("Запрос пуст. Пришлите email, ID или URL.")
            return
        sessions.for_user(message.from_user.id).search_query = query
        await state.clear()
        try:
            page = await api.get_clients_page(
                page=1,
                page_size=page_size,
                search=query,
            )
        except PanelApiError as exc:
            await _message_api_error(message, exc)
            return
        await message.answer(
            users_text(page, search=query),
            parse_mode="HTML",
            reply_markup=users_keyboard(page, sessions, mode="s"),
        )

    @router.message(AdminSearch.waiting_for_traffic_limit)
    async def traffic_limit_input(message: Message, state: FSMContext) -> None:
        if not _is_private_admin_message(message, is_admin):
            await state.clear()
            await message.answer("⛔ Доступ запрещён.")
            return
        try:
            total_bytes = parse_traffic_limit(message.text or "")
        except ValueError as exc:
            await message.answer(str(exc))
            return

        token, mode, page, email = await _state_client_context(state, sessions)
        try:
            await api.update_client_fields(email, totalGB=total_bytes)
        except PanelApiError as exc:
            await _message_api_error(message, exc)
            return
        await state.clear()
        try:
            client, traffic, devices, subscription_url = await _client_card_data(
                api, email
            )
        except PanelApiError as exc:
            await _message_refresh_error(message, exc)
            return
        limit = format_bytes(total_bytes) if total_bytes else "Безлимит"
        await message.answer(
            f"✅ Лимит трафика изменён: <b>{limit}</b>\n\n"
            f"{client_text(client, traffic, devices)}",
            parse_mode="HTML",
            reply_markup=client_keyboard(
                token=token,
                mode=mode,
                page=page,
                has_devices=bool(devices),
                subscription_url=subscription_url,
            ),
        )

    @router.message(AdminSearch.waiting_for_expiry_date)
    async def expiry_date_input(message: Message, state: FSMContext) -> None:
        if not _is_private_admin_message(message, is_admin):
            await state.clear()
            await message.answer("⛔ Доступ запрещён.")
            return
        try:
            expiry_time = parse_expiry_date(message.text or "")
        except ValueError as exc:
            await message.answer(str(exc))
            return

        token, mode, page, email = await _state_client_context(state, sessions)
        try:
            await api.update_client_fields(email, expiryTime=expiry_time)
        except PanelApiError as exc:
            await _message_api_error(message, exc)
            return
        await state.clear()
        try:
            client, traffic, devices, subscription_url = await _client_card_data(
                api, email
            )
        except PanelApiError as exc:
            await _message_refresh_error(message, exc)
            return
        await message.answer(
            "✅ Дата окончания подписки изменена.\n\n"
            f"{client_text(client, traffic, devices)}",
            parse_mode="HTML",
            reply_markup=client_keyboard(
                token=token,
                mode=mode,
                page=page,
                has_devices=bool(devices),
                subscription_url=subscription_url,
            ),
        )

    @router.message(AdminSearch.waiting_for_expiry_days)
    async def expiry_days_input(message: Message, state: FSMContext) -> None:
        if not _is_private_admin_message(message, is_admin):
            await state.clear()
            await message.answer("⛔ Доступ запрещён.")
            return
        try:
            days = parse_day_delta(message.text or "")
        except ValueError as exc:
            await message.answer(str(exc))
            return

        token, mode, page, email = await _state_client_context(state, sessions)
        try:
            current = await api.get_client(email)
            if current.expiry_time == 0:
                await message.answer(
                    "У пользователя сейчас бессрочная подписка. Сначала задайте "
                    "конкретную дату — API 3x-ui не прибавляет дни к безлимиту."
                )
                return
            await api.adjust_client_expiry(email, days)
        except PanelApiError as exc:
            await _message_api_error(message, exc)
            return
        await state.clear()
        try:
            client, traffic, devices, subscription_url = await _client_card_data(
                api, email
            )
        except PanelApiError as exc:
            await _message_refresh_error(message, exc)
            return
        action = "добавлено" if days > 0 else "убрано"
        await message.answer(
            f"✅ Дней {action}: <b>{abs(days)}</b>.\n\n"
            f"{client_text(client, traffic, devices)}",
            parse_mode="HTML",
            reply_markup=client_keyboard(
                token=token,
                mode=mode,
                page=page,
                has_devices=bool(devices),
                subscription_url=subscription_url,
            ),
        )

    @router.callback_query(F.data.startswith("adm:"))
    async def admin_callback(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("Доступ запрещён", show_alert=True)
            return
        if callback.message is None or callback.message.chat.type != ChatType.PRIVATE:
            await callback.answer(
                "Откройте админ-панель в личном чате", show_alert=True
            )
            return
        await callback.answer()
        data = callback.data or ""
        try:
            await _dispatch_callback(
                callback,
                state,
                api,
                subscriptions,
                ledger,
                node_history,
                sessions,
                page_size,
                data,
            )
        except sqlite3.Error:
            logger.exception("Admin database request failed")
            await callback.message.answer("⚠️ Не удалось прочитать данные бота.")
        except (PanelApiError, ValueError) as exc:
            logger.warning("Admin panel request failed: %s", exc)
            await callback.message.answer(
                "⚠️ Не удалось получить данные 3x-ui. Подробности записаны в лог."
            )

    return router


async def _dispatch_callback(
    callback: CallbackQuery,
    state: FSMContext,
    api: PanelApiClient,
    subscriptions: SubscriptionService,
    ledger: PaymentLedger,
    node_history: NodeHistory,
    sessions: AdminSessions,
    page_size: int,
    data: str,
) -> None:
    message = callback.message
    if message is None or data == "adm:noop":
        return
    if data == "adm:fin" or data.startswith(("adm:fin:", "adm:fchart:")):
        await state.clear()
        await _show_finances(message, ledger, data)
        return
    if data.startswith(("adm:pay:", "adm:ph:")):
        action = data.split(":", 2)[1]
        token, mode, page = _parse_context(data, f"adm:{action}:")
        email = _resolve_email(sessions, token)
        keyboard = cancel_input_keyboard(token=token, mode=mode, page=page)
        await state.clear()
        if action == "pay":
            await _edit(
                message,
                f"<b>Оплата за {escape(email)}</b>\n\n"
                "Введите полученную сумму в рублях. Например, <code>499,90</code>.",
                keyboard,
            )
            await _set_client_state(
                state,
                AdminSearch.waiting_for_payment,
                token=token,
                mode=mode,
                page=page,
            )
        else:
            await _edit(
                message,
                report_text(
                    f"Оплаты · {escape(email)}",
                    ledger.report(email=email),
                    monthly=False,
                ),
                cancel_input_keyboard(
                    token=token, mode=mode, page=page, text="↩️ К клиенту"
                ),
            )
        return
    if data == "adm:new":
        await state.clear()
        await state.set_state(AdminSearch.waiting_for_new_expiry)
        await _edit(
            message,
            "<b>Новая подписка</b>\n\nВведите дату окончания в формате "
            "<code>ДД.ММ.ГГ</code> или <code>-1</code> для бессрочной подписки.",
            cancel_search_keyboard(),
        )
        return
    if data == "adm:menu":
        await state.clear()
        overview = await api.get_overview()
        await _edit(message, overview_text(overview), main_keyboard())
        return
    if data == "adm:nodes":
        nodes = await api.get_nodes()
        await _edit(message, locations_text(nodes), nodes_keyboard(nodes))
        return
    if data.startswith("adm:online:"):
        period_key, target = _chart_selection(data)
        period = ONLINE_PERIODS[period_key]
        node_id = _positive_int(target)
        node = await api.get_node(node_id)
        now = datetime.now(MSK)
        points, peak = await asyncio.to_thread(
            node_history.connected, node_id, now, period
        )
        chart = await asyncio.to_thread(
            render_online_chart, node.name, points, now, period
        )
        caption = f"Онлайн за {period.label}. Шаг усреднения — {period.bucket_label}."
        if peak is not None:
            caption += f"\nПик по замерам: {peak}"
        await _show_chart(
            message,
            BufferedInputFile(chart, filename=f"node-{node_id}-online.png"),
            caption,
            chart_period_keyboard("online", target, period_key),
        )
        return
    if data == "adm:uptime" or data.startswith("adm:uptime:"):
        period_key, target = _chart_selection(data)
        period = UPTIME_PERIODS[period_key]
        now = datetime.now(MSK)
        history = await asyncio.to_thread(node_history.uptime, now, period)
        if target == "all":
            nodes = tuple(node for node in await api.get_nodes() if node.enabled)
        else:
            nodes = (await api.get_node(_positive_int(target)),)
        if not nodes:
            await message.answer("Нет включённых локаций для графика.")
            return
        locations = [
            (node.name, history.get(node.id, [None] * period.buckets)) for node in nodes
        ]
        chart = await asyncio.to_thread(render_uptime_chart, locations, now, period)
        photo = BufferedInputFile(chart, filename="uptime.png")
        caption = (
            "🟢 Доступно   🟡 Недоступно из РФ   🔴 Офлайн\n"
            "Тёмно-зелёный — онлайн по панели, доступ из РФ не подтверждён.\n"
            "Серый — нет данных о состоянии.\n"
            f"Одна полоска — {period.bucket_label}. "
            "Сбои имеют приоритет над статусом «онлайн»."
        )
        await _show_chart(
            message, photo, caption, chart_period_keyboard("uptime", target, period_key)
        )
        return
    if data.startswith("adm:n:"):
        node_id = _positive_int(data.removeprefix("adm:n:"))
        node = await api.get_node(node_id)
        await _edit(message, node_text(node), node_keyboard(node_id))
        return
    if data.startswith("adm:g:"):
        node_id = _positive_int(data.removeprefix("adm:g:"))
        node, history = await asyncio.gather(
            api.get_node(node_id),
            api.get_node_load_history(node_id),
        )
        cpu, memory, bucket = history
        chart = await asyncio.to_thread(render_node_chart, node, cpu, memory)
        await message.answer_photo(
            BufferedInputFile(chart, filename=f"node-{node_id}-load.png"),
            caption=history_caption(node, cpu, memory, bucket),
            parse_mode="HTML",
        )
        return
    if data == "adm:find":
        await state.set_state(AdminSearch.waiting_for_query)
        await _edit(
            message,
            "<b>Поиск пользователя</b>\n\n"
            "Пришлите email, Sub ID, UUID или полную ссылку подписки.",
            cancel_search_keyboard(),
        )
        return
    if data.startswith("adm:u:"):
        page_number = _positive_int(data.removeprefix("adm:u:"))
        page = await api.get_clients_page(page=page_number, page_size=page_size)
        await _edit(
            message,
            users_text(page),
            users_keyboard(page, sessions, mode="u"),
        )
        return
    if data.startswith("adm:s:"):
        page_number = _positive_int(data.removeprefix("adm:s:"))
        query = sessions.for_user(callback.from_user.id).search_query
        if not query:
            raise ValueError("search session expired")
        page = await api.get_clients_page(
            page=page_number,
            page_size=page_size,
            search=query,
        )
        await _edit(
            message,
            users_text(page, search=query),
            users_keyboard(page, sessions, mode="s"),
        )
        return
    if data.startswith("adm:t:"):
        token, mode, page = _parse_context(data, "adm:t:")
        email = _resolve_email(sessions, token)
        await _set_client_state(
            state,
            AdminSearch.waiting_for_traffic_limit,
            token=token,
            mode=mode,
            page=page,
        )
        await _edit(
            message,
            f"<b>Лимит трафика для {escape(email)}</b>\n\n"
            "Пришлите новый лимит в ГиБ. Можно использовать дробное число "
            "(например <code>25.5</code>).\n"
            "Для безлимита пришлите <code>-1</code>.",
            cancel_input_keyboard(token=token, mode=mode, page=page),
        )
        return
    if data.startswith("adm:eiy:"):
        token, mode, page = _parse_context(data, "adm:eiy:")
        email = _resolve_email(sessions, token)
        await api.update_client_fields(email, expiryTime=0)
        await state.clear()
        client, traffic, devices, subscription_url = await _client_card_data(api, email)
        await _edit(
            message,
            "✅ Подписка сделана бессрочной.\n\n"
            f"{client_text(client, traffic, devices)}",
            client_keyboard(
                token=token,
                mode=mode,
                page=page,
                has_devices=bool(devices),
                subscription_url=subscription_url,
            ),
        )
        return
    if data.startswith("adm:ei:"):
        token, mode, page = _parse_context(data, "adm:ei:")
        email = _resolve_email(sessions, token)
        await state.clear()
        await _edit(
            message,
            f"<b>Сделать подписку {escape(email)} бессрочной?</b>\n\n"
            "Текущая дата окончания будет удалена.",
            confirm_unlimited_keyboard(token=token, mode=mode, page=page),
        )
        return
    if data.startswith("adm:ed:"):
        token, mode, page = _parse_context(data, "adm:ed:")
        email = _resolve_email(sessions, token)
        await _set_client_state(
            state,
            AdminSearch.waiting_for_expiry_date,
            token=token,
            mode=mode,
            page=page,
        )
        await _edit(
            message,
            f"<b>Новая дата окончания для {escape(email)}</b>\n\n"
            "Пришлите дату в формате <code>ДД.ММ.ГГ</code>, например "
            "<code>16.09.26</code>. Подписка будет действовать до конца "
            "указанного дня.",
            cancel_input_keyboard(token=token, mode=mode, page=page),
        )
        return
    if data.startswith("adm:ea:"):
        token, mode, page = _parse_context(data, "adm:ea:")
        email = _resolve_email(sessions, token)
        await _set_client_state(
            state,
            AdminSearch.waiting_for_expiry_days,
            token=token,
            mode=mode,
            page=page,
        )
        await _edit(
            message,
            f"<b>Изменить срок для {escape(email)}</b>\n\n"
            "Пришлите целое число: <code>30</code>, чтобы добавить 30 дней, "
            "или <code>-7</code>, чтобы убрать 7 дней.",
            cancel_input_keyboard(token=token, mode=mode, page=page),
        )
        return
    if data.startswith("adm:e:"):
        token, mode, page = _parse_context(data, "adm:e:")
        email = _resolve_email(sessions, token)
        await state.clear()
        await _edit(
            message,
            f"<b>Срок подписки {escape(email)}</b>\n\nВыберите действие:",
            expiry_keyboard(token=token, mode=mode, page=page),
        )
        return
    if data.startswith("adm:c:"):
        token, mode, page = _parse_context(data, "adm:c:")
        email = _resolve_email(sessions, token)
        await state.clear()
        client, traffic, devices, subscription_url = await _client_card_data(api, email)
        await _edit(
            message,
            client_text(client, traffic, devices),
            client_keyboard(
                token=token,
                mode=mode,
                page=page,
                has_devices=bool(devices),
                subscription_url=subscription_url,
            ),
        )
        return
    if data.startswith(("adm:rotate:", "adm:rotateyes:")):
        action = data.split(":", 2)[1]
        token, mode, page = _parse_context(data, f"adm:{action}:")
        email = _resolve_email(sessions, token)
        await state.clear()
        if action == "rotate":
            await _edit(
                message,
                f"<b>Обновить подписку {escape(email)}?</b>\n\n"
                "У новой ссылки будут новые ключи подключения. "
                "Старая будет удалена через 24 часа. "
                "Дата окончания сохранится.",
                confirm_keyboard(
                    "Создать новую ссылку",
                    confirm=f"adm:rotateyes:{token}:{mode}:{page}",
                    cancel=f"adm:c:{token}:{mode}:{page}",
                ),
            )
            return
        result = await subscriptions.rotate(email)
        text, keyboard = _subscription_result(result, sessions, mode, page)
        await _edit(message, text, keyboard)
        return
    if data.startswith("adm:h:"):
        token, mode, page = _parse_context(data, "adm:h:")
        email = _resolve_email(sessions, token)
        devices = await api.get_hwids(email)
        await _edit(
            message,
            devices_text(email, devices),
            devices_keyboard(
                devices,
                token=token,
                mode=mode,
                page=page,
            ),
        )
        return
    if data.startswith(("adm:d:", "adm:hwdel:", "adm:hwyes:")):
        _, action, token, raw_device_id, mode, raw_page = data.split(":")
        if mode not in {"u", "s"}:
            raise ValueError("invalid device callback")
        device_id = _positive_int(raw_device_id)
        page = _positive_int(raw_page)
        email = _resolve_email(sessions, token)
        devices = await api.get_hwids(email)
        device = next((item for item in devices if item.id == device_id), None)
        if action == "hwyes":
            if device is not None:
                await api.delete_hwid(email, device_id)
                devices = await api.get_hwids(email)
            await _edit(
                message,
                "✅ Устройство удалено.\n\n" + devices_text(email, devices),
                devices_keyboard(devices, token=token, mode=mode, page=page),
            )
            return
        if device is None:
            raise ValueError("HWID device no longer exists")
        if action == "hwdel":
            context = f"{token}:{device_id}:{mode}:{page}"
            await _edit(
                message,
                f"Удалить <b>{escape(device.device_model)}</b> у {escape(email)}?",
                confirm_keyboard(
                    "Удалить устройство",
                    confirm=f"adm:hwyes:{context}",
                    cancel=f"adm:d:{context}",
                ),
            )
            return
        await _edit(
            message,
            device_text(device),
            device_keyboard(token=token, mode=mode, page=page, device_id=device_id),
        )


async def _show_finances(message: Message, ledger: PaymentLedger, data: str) -> None:
    current = month_start(datetime.now(MSK))
    month = current
    if data != "adm:fin":
        selected = data.split(":")[2]
        if len(selected) != 6 or not selected.isascii() or not selected.isdecimal():
            raise ValueError("invalid finance month")
        month = date(int(selected[:4]), int(selected[4:]), 1)
        if not date(2000, 1, 1) <= month <= current:
            raise ValueError("invalid finance month")
    if data.startswith("adm:fchart:"):
        totals = ledger.monthly_totals(month)
        image = await asyncio.to_thread(render_revenue_chart, totals)
        await message.answer_photo(
            BufferedInputFile(image, filename="revenue.png"),
            caption=(
                f"Выручка за {totals[0][0]:%m.%Y} — {month:%m.%Y}: "
                f"{rubles(sum(amount for _, amount in totals))}"
            ),
        )
        return
    await _edit(
        message,
        report_text(
            f"Финансы · {month:%m.%Y}", ledger.report(month=month), monthly=True
        ),
        finance_keyboard(month, current),
    )


async def _edit(
    message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:
    try:
        await message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


async def _message_api_error(message: Message, error: PanelApiError) -> None:
    logger.warning("Admin panel request failed: %s", error)
    await message.answer(
        "⚠️ Не удалось получить данные 3x-ui. Проверьте API-токен и журнал бота."
    )


async def _message_refresh_error(message: Message, error: PanelApiError) -> None:
    logger.warning("Client was updated, but its card refresh failed: %s", error)
    await message.answer(
        "✅ Изменение отправлено в 3x-ui, но обновить карточку не удалось. "
        "Откройте пользователя заново."
    )


async def _show_chart(
    message: Message,
    photo: BufferedInputFile,
    caption: str,
    keyboard: InlineKeyboardMarkup,
) -> None:
    if not message.photo:
        await message.answer_photo(photo, caption=caption, reply_markup=keyboard)
        return
    try:
        await message.edit_media(
            InputMediaPhoto(media=photo, caption=caption), reply_markup=keyboard
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


def _chart_selection(data: str) -> tuple[str, str]:
    parts = data.split(":")
    uptime = parts[1] == "uptime"
    periods = UPTIME_PERIODS if uptime else ONLINE_PERIODS
    if len(parts) == 2 and uptime:
        return "30d", "all"
    if len(parts) == 3:
        return "30d" if uptime else "24h", str(_positive_int(parts[2]))
    if len(parts) == 4 and parts[2] in periods:
        target = "all" if uptime and parts[3] == "all" else str(_positive_int(parts[3]))
        return parts[2], target
    raise ValueError("invalid chart callback")


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise ValueError("expected a positive integer")
    return parsed


def _parse_context(data: str, prefix: str) -> tuple[str, str, int]:
    parts = data.removeprefix(prefix).split(":")
    if len(parts) != 3 or parts[1] not in {"u", "s"}:
        raise ValueError("invalid callback context")
    return parts[0], parts[1], _positive_int(parts[2])


def _resolve_email(sessions: AdminSessions, token: str) -> str:
    email = sessions.resolve_client(token)
    if email is None:
        raise ValueError("client button expired; reopen the list")
    return email


def _is_private_admin_message(
    message: Message,
    is_admin: Callable[[int | None], bool],
) -> bool:
    user_id = message.from_user.id if message.from_user else None
    return message.chat.type == ChatType.PRIVATE and is_admin(user_id)


async def _set_client_state(
    state: FSMContext,
    next_state: State,
    *,
    token: str,
    mode: str,
    page: int,
) -> None:
    await state.set_state(next_state)
    await state.set_data({"token": token, "mode": mode, "page": page})


async def _state_client_context(
    state: FSMContext,
    sessions: AdminSessions,
) -> tuple[str, str, int, str]:
    data = await state.get_data()
    token = str(data.get("token") or "")
    mode = str(data.get("mode") or "")
    try:
        page = int(data.get("page"))
    except (TypeError, ValueError) as exc:
        raise ValueError("admin input context expired") from exc
    if not token or mode not in {"u", "s"} or page <= 0:
        raise ValueError("admin input context expired")
    return token, mode, page, _resolve_email(sessions, token)


async def _client_card_data(
    api: PanelApiClient,
    email: str,
) -> tuple[ClientDetails, TrafficStats, tuple[HwidDevice, ...], str | None]:
    client, traffic, devices = await asyncio.gather(
        api.get_client(email),
        api.get_client_traffic(email),
        api.get_hwids(email),
    )
    subscription_url = None
    if client.sub_id:
        try:
            subscription_url = await api.get_subscription_url(client.sub_id)
        except PanelApiError as exc:
            logger.debug("Subscription URL is unavailable for %s: %s", email, exc)
    return client, traffic, devices, subscription_url


def _subscription_result(
    result: Subscription, sessions: AdminSessions, mode: str, page: int
) -> tuple[str, InlineKeyboardMarkup]:
    client = result.client
    expiry = "Бессрочно"
    if client.expiry_time > 0:
        expiry = datetime.fromtimestamp(client.expiry_time / 1000, MSK).strftime(
            "%d.%m.%Y %H:%M"
        )
    elif client.expiry_time < 0:
        expiry = f"{-client.expiry_time // 86400000} д. с первого подключения"
    lines = [
        (
            "✅ <b>Подписка создана</b>"
            if not result.delete_after
            else "✅ <b>Новая ссылка создана</b>"
        ),
        f"Email: <code>{escape(client.email)}</code>",
        f"Срок: <b>{expiry}</b>",
        f"Входящих: {len(client.inbound_ids)}",
    ]
    if result.delete_after:
        deadline = datetime.fromtimestamp(result.delete_after, MSK)
        lines.append(f"Удаление старой ссылки: {deadline:%d.%m.%Y %H:%M}")
        if not result.traffic_copied:
            lines.append("Перенос счётчиков трафика не подтверждён.")
    if result.node_pending:
        lines.append("Часть узлов получит настройки при следующей синхронизации.")
    return "\n".join(lines), subscription_keyboard(
        result.url, token=sessions.client_token(client.email), mode=mode, page=page
    )
