"""Public facade for admin internals used by sibling handler modules."""

from aiogram import types
from aiogram.fsm.context import FSMContext

from . import admin as _admin

AdminChannelState = _admin.AdminChannelState
ADMIN_PERMISSION_MOVIES = _admin.ADMIN_PERMISSION_MOVIES
ADMIN_PERMISSION_REQUESTS = _admin.ADMIN_PERMISSION_REQUESTS
ADMIN_PERMISSION_STATS = _admin.ADMIN_PERMISSION_STATS
ADMIN_PERMISSION_ADS = _admin.ADMIN_PERMISSION_ADS
LEGACY_STATS_BUTTON = _admin.LEGACY_STATS_BUTTON


def is_owner(user_id: int) -> bool:
    return _admin._is_owner(user_id)


async def is_admin(user_id: int) -> bool:
    return await _admin._is_admin(user_id)


async def get_permissions(user_id: int) -> set[str]:
    return await _admin._admin_permissions(user_id)


async def ensure_message_access(
    message: types.Message,
    *,
    permission: str | None = None,
    owner_only: bool = False,
) -> bool:
    return await _admin._ensure_message_access(
        message,
        permission=permission,
        owner_only=owner_only,
    )


async def ensure_callback_access(
    callback: types.CallbackQuery,
    *,
    permission: str | None = None,
    owner_only: bool = False,
) -> bool:
    return await _admin._ensure_callback_access(
        callback,
        permission=permission,
        owner_only=owner_only,
    )


async def start_add_movie_flow(
    message: types.Message,
    state: FSMContext,
    *,
    content_kind: str = "movie",
) -> None:
    await _admin._start_add_movie_flow(
        message,
        state,
        content_kind=content_kind,
    )


async def show_serial_mode_picker(
    message: types.Message,
    state: FSMContext,
) -> None:
    await _admin._show_serial_mode_picker(message, state)


async def delete_start(message: types.Message, state: FSMContext) -> None:
    await _admin.delete_start(message, state)


async def start_edit_movie(message: types.Message, state: FSMContext) -> None:
    await _admin.start_edit_movie(message, state)


async def movie_list(message: types.Message) -> None:
    await _admin.movie_list(message)


async def show_requests(message: types.Message) -> None:
    await _admin.show_requests(message)


async def show_stats_webapp(message: types.Message) -> None:
    await _admin._show_stats_webapp(message)


async def show_ads_panel(message: types.Message) -> None:
    await _admin._show_ads_panel(message)


async def show_helper_admins_panel(message: types.Message) -> None:
    await _admin._show_helper_admins_panel(message)


async def admin_global_handler(message: types.Message, state: FSMContext) -> None:
    await _admin.admin_global_handler(message, state)
