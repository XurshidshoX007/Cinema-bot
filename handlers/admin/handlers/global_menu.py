"""Global admin menu handler (fallback)."""

from aiogram import F, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

from keyboards import (
    ADMIN_ACTIONS,
    ADMIN_PANEL_BUTTON,
    ADS_BUTTON,
    BACK_TO_ADMIN_BUTTON,
    BACK_TO_MAIN_BUTTON,
    CHANNELS_BUTTON,
    DELETE_MOVIE_BUTTON,
    EDIT_MOVIE_BUTTON,
    HELPER_ADMINS_BUTTON,
    MOVIES_LIST_BUTTON,
    MOVIE_MANAGEMENT_BUTTON,
    NEW_MOVIE_BUTTON,
    NEW_SERIAL_BUTTON,
    REQUESTS_BUTTON,
    STATS_BUTTON,
    LEGACY_NEW_MOVIE_BUTTON,
    admin_menu,
    main_menu,
    movie_menu,
)

from .. import router
from ..constants import (
    ADMIN_PERMISSION_MOVIES,
    ADMIN_PERMISSION_REQUESTS,
    ADMIN_PERMISSION_STATS,
    ADMIN_PERMISSION_ADS,
    LEGACY_STATS_BUTTON,
)
from ..permissions import _is_admin, _admin_permissions, _is_owner, _ensure_message_access
from ..flows import _start_add_movie_flow, _show_serial_mode_picker
from ..panels import _show_ads_panel, _show_helper_admins_panel, _show_content_list, _show_delete_panel, _show_stats_webapp
from .requests import show_requests
from .edit import start_edit_movie
from .content import movie_list_handler, delete_start_handler
from database import touch_user


@router.message(StateFilter("*"), F.text.in_(ADMIN_ACTIONS))
async def admin_global_handler(message: types.Message, state: FSMContext) -> None:
    user = message.from_user
    if user is None:
        return

    if not await _is_admin(user.id):
        return

    await touch_user(user.id, user.username, user.full_name)

    text = message.text
    await state.clear()
    permissions = await _admin_permissions(user.id)
    is_owner = _is_owner(user.id)

    if text == ADMIN_PANEL_BUTTON:
        await message.answer(
            "👨‍💻 <b>Boshqaruv Paneliga Xush Kelibsiz!</b>\n\n<i>Kerakli bo'limni tanlang:</i>",
            parse_mode="HTML",
            reply_markup=admin_menu(permissions=permissions, is_owner=is_owner),
        )
    elif text == MOVIE_MANAGEMENT_BUTTON:
        if ADMIN_PERMISSION_MOVIES not in permissions:
            await message.answer("Sizda bu bo'limga ruxsat yo'q")
            return
        await message.answer(
            "🎬 <b>Kino va Seriallar Boshqaruvi</b>\n\n<i>Amalni tanlang:</i>",
            parse_mode="HTML",
            reply_markup=movie_menu(),
        )
    elif text in {NEW_MOVIE_BUTTON, LEGACY_NEW_MOVIE_BUTTON}:
        if ADMIN_PERMISSION_MOVIES not in permissions:
            await message.answer("Sizda bu amalga ruxsat yo'q")
            return
        await _start_add_movie_flow(message, state, content_kind="movie")
    elif text == NEW_SERIAL_BUTTON:
        if ADMIN_PERMISSION_MOVIES not in permissions:
            await message.answer("Sizda bu amalga ruxsat yo'q")
            return
        await _show_serial_mode_picker(message, state)
    elif text == EDIT_MOVIE_BUTTON:
        if ADMIN_PERMISSION_MOVIES not in permissions:
            await message.answer("Sizda bu amalga ruxsat yo'q")
            return
        await start_edit_movie(message, state)
    elif text == DELETE_MOVIE_BUTTON:
        if ADMIN_PERMISSION_MOVIES not in permissions:
            await message.answer("Sizda bu amalga ruxsat yo'q")
            return
        await delete_start_handler(message, state)
    elif text == MOVIES_LIST_BUTTON:
        if ADMIN_PERMISSION_MOVIES not in permissions:
            await message.answer("Sizda bu bo'limga ruxsat yo'q")
            return
        await movie_list_handler(message)
    elif text == REQUESTS_BUTTON:
        if ADMIN_PERMISSION_REQUESTS not in permissions:
            await message.answer("Sizda bu bo'limga ruxsat yo'q")
            return
        await show_requests(message)
    elif text in {STATS_BUTTON, LEGACY_STATS_BUTTON}:
        if ADMIN_PERMISSION_STATS not in permissions:
            await message.answer("Sizda bu bo'limga ruxsat yo'q")
            return
        await _show_stats_webapp(message)
    elif text == ADS_BUTTON:
        if ADMIN_PERMISSION_ADS not in permissions:
            await message.answer("Sizda bu bo'limga ruxsat yo'q")
            return
        await _show_ads_panel(message)
    elif text == CHANNELS_BUTTON:
        if "channels" not in permissions and not is_owner:
            await message.answer("Sizda bu bo'limga ruxsat yo'q")
            return
        from handlers import channel_fix

        await channel_fix.admin_channels_menu(message, state)
    elif text == HELPER_ADMINS_BUTTON:
        if not is_owner:
            await message.answer("Sizda bu bo'limga ruxsat yo'q")
            return
        await _show_helper_admins_panel(message)
    elif text == BACK_TO_ADMIN_BUTTON:
        await message.answer(
            "⚙️ <b>Admin Panel</b>\n\n<i>Kerakli bo'limni tanlang:</i>",
            parse_mode="HTML",
            reply_markup=admin_menu(permissions=permissions, is_owner=is_owner),
        )
    elif text == BACK_TO_MAIN_BUTTON:
        await message.answer(
            "⬅️ <b>Asosiy Menyu</b>\n\n<i>Nimani tanlaysiz?</i>",
            parse_mode="HTML",
            reply_markup=main_menu(
                user.id, show_admin_panel=bool(permissions)
            ),
        )
