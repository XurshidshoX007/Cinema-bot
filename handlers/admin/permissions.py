"""Admin permission helpers."""

from aiogram import types

from config import ADMIN_ID
from database import get_admin_permissions, is_admin_user
from keyboards import admin_menu


def _is_owner(user_id: int) -> bool:
    return user_id == ADMIN_ID


async def _admin_permissions(user_id: int) -> set[str]:
    return await get_admin_permissions(user_id)


async def _is_admin(user_id: int) -> bool:
    return await is_admin_user(user_id)


async def _has_permission(user_id: int, permission: str) -> bool:
    return permission in await _admin_permissions(user_id)


async def _ensure_message_access(
    message: types.Message,
    *,
    permission: str | None = None,
    owner_only: bool = False,
) -> bool:
    if message.from_user is None:
        return False
    user_id = message.from_user.id
    if owner_only:
        return _is_owner(user_id)
    if permission is None:
        return await _is_admin(user_id)
    return await _has_permission(user_id, permission)


async def _ensure_callback_access(
    callback: types.CallbackQuery,
    *,
    permission: str | None = None,
    owner_only: bool = False,
) -> bool:
    if callback.from_user is None:
        return False
    user_id = callback.from_user.id
    if owner_only:
        return _is_owner(user_id)
    if permission is None:
        return await _is_admin(user_id)
    return await _has_permission(user_id, permission)


async def _admin_menu_markup(user_id: int) -> types.ReplyKeyboardMarkup:
    permissions = await _admin_permissions(user_id)
    return admin_menu(permissions=permissions, is_owner=_is_owner(user_id))
