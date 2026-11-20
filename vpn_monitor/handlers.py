from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import Message

from .formatting import summary
from .state import MonitorState


def build_router(state: MonitorState) -> Router:
    router = Router(name="private-summary")

    @router.message(F.chat.type == ChatType.PRIVATE)
    async def private_summary(message: Message) -> None:
        await message.answer(summary(await state.snapshot()), parse_mode="HTML")

    return router
