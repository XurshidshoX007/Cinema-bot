"""Helpers for syncing Telegram user context with storage."""

from aiogram import types

from database import touch_user


async def touch_message_user(message: types.Message) -> None:
    user = message.from_user
    if user is None:
        return
    await touch_user(user.id, user.username, user.full_name)


async def touch_callback_user(callback: types.CallbackQuery) -> None:
    user = callback.from_user
    if user is None:
        return
    await touch_user(user.id, user.username, user.full_name)
