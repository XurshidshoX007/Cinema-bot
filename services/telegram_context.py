"""Helpers for syncing Telegram user context with storage."""

from aiogram import types

from repositories.users import touch_user


async def touch_message_user(message: types.Message) -> None:
    await touch_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.full_name,
    )


async def touch_callback_user(callback: types.CallbackQuery) -> None:
    await touch_user(
        callback.from_user.id,
        callback.from_user.username,
        callback.from_user.full_name,
    )
