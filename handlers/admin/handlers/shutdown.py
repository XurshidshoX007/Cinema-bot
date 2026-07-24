"""Shutdown command."""

from aiogram import Dispatcher, F
from aiogram.filters import Command
from aiogram import types

from .. import router
from ..permissions import _is_owner


@router.message(Command("shutdown"))
async def shutdown_bot(message: types.Message, dispatcher: Dispatcher) -> None:
    if message.from_user is None or not _is_owner(message.from_user.id):
        return

    stop_event = dispatcher.get("owner_stop_event")
    if stop_event is None:
        await message.answer("❌ To'xtatish mexanizmi topilmadi")
        return

    await message.answer("🛑 Bot to'xtatilmoqda")
    stop_event.set()
