"""Top-level admin menu text lives in this file."""

from aiogram import F, Router, types
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
    LEGACY_NEW_MOVIE_BUTTON,
    MOVIES_LIST_BUTTON,
    MOVIE_MANAGEMENT_BUTTON,
    NEW_MOVIE_BUTTON,
    NEW_SERIAL_BUTTON,
    REQUESTS_BUTTON,
    STATS_BUTTON,
    admin_menu,
    main_menu,
    movie_menu,
)
from services.telegram_context import touch_message_user

from . import admin_facade, channel_fix

router = Router()


def _denied_text() -> str:
    return "Bu bo'lim uchun ruxsat yo'q."


@router.message(StateFilter("*"), F.text.in_(ADMIN_ACTIONS))
async def admin_global_ui_handler(message: types.Message, state: FSMContext) -> None:
    if not await admin_facade.is_admin(message.from_user.id):
        return

    await touch_message_user(message)

    text = message.text
    await state.clear()
    permissions = await admin_facade.get_permissions(message.from_user.id)
    is_owner = admin_facade.is_owner(message.from_user.id)

    if text == ADMIN_PANEL_BUTTON:
        await message.answer(
            "⚙️ <b>Admin panel</b>\n\n<i>Kerakli bo'limni tanlang.</i>",
            parse_mode="HTML",
            reply_markup=admin_menu(permissions=permissions, is_owner=is_owner),
        )
        return

    if text == MOVIE_MANAGEMENT_BUTTON:
        if admin_facade.ADMIN_PERMISSION_MOVIES not in permissions and not is_owner:
            await message.answer(_denied_text())
            return
        await message.answer(
            "🎬 <b>Kontent</b>\n\n<i>Amalni tanlang.</i>",
            parse_mode="HTML",
            reply_markup=movie_menu(),
        )
        return

    if text in {NEW_MOVIE_BUTTON, LEGACY_NEW_MOVIE_BUTTON}:
        if admin_facade.ADMIN_PERMISSION_MOVIES not in permissions and not is_owner:
            await message.answer(_denied_text())
            return
        await admin_facade.start_add_movie_flow(message, state, content_kind="movie")
        return

    if text == NEW_SERIAL_BUTTON:
        if admin_facade.ADMIN_PERMISSION_MOVIES not in permissions and not is_owner:
            await message.answer(_denied_text())
            return
        await admin_facade.show_serial_mode_picker(message, state)
        return

    if text == EDIT_MOVIE_BUTTON:
        if admin_facade.ADMIN_PERMISSION_MOVIES not in permissions and not is_owner:
            await message.answer(_denied_text())
            return
        await admin_facade.start_edit_movie(message, state)
        return

    if text == DELETE_MOVIE_BUTTON:
        if admin_facade.ADMIN_PERMISSION_MOVIES not in permissions and not is_owner:
            await message.answer(_denied_text())
            return
        await admin_facade.delete_start(message, state)
        return

    if text == MOVIES_LIST_BUTTON:
        if admin_facade.ADMIN_PERMISSION_MOVIES not in permissions and not is_owner:
            await message.answer(_denied_text())
            return
        await admin_facade.movie_list(message)
        return

    if text == REQUESTS_BUTTON:
        if admin_facade.ADMIN_PERMISSION_REQUESTS not in permissions and not is_owner:
            await message.answer(_denied_text())
            return
        await admin_facade.show_requests(message)
        return

    if text == STATS_BUTTON or text == admin_facade.LEGACY_STATS_BUTTON:
        if admin_facade.ADMIN_PERMISSION_STATS not in permissions and not is_owner:
            await message.answer(_denied_text())
            return
        await admin_facade.show_stats_webapp(message)
        return

    if text == ADS_BUTTON:
        if admin_facade.ADMIN_PERMISSION_ADS not in permissions and not is_owner:
            await message.answer(_denied_text())
            return
        await admin_facade.show_ads_panel(message)
        return

    if text == CHANNELS_BUTTON:
        if "channels" not in permissions and not is_owner:
            await message.answer(_denied_text())
            return
        await channel_fix.admin_channels_menu(message, state)
        return

    if text == HELPER_ADMINS_BUTTON:
        if not is_owner:
            await message.answer(_denied_text())
            return
        await admin_facade.show_helper_admins_panel(message)
        return

    if text == BACK_TO_ADMIN_BUTTON:
        await message.answer(
            "⚙️ <b>Admin panel</b>\n\n<i>Kerakli bo'limni tanlang.</i>",
            parse_mode="HTML",
            reply_markup=admin_menu(permissions=permissions, is_owner=is_owner),
        )
        return

    if text == BACK_TO_MAIN_BUTTON:
        await message.answer(
            "🏠 <b>Asosiy menyu</b>\n\n<i>Kerakli bo'limni tanlang.</i>",
            parse_mode="HTML",
            reply_markup=main_menu(
                message.from_user.id, show_admin_panel=bool(permissions)
            ),
        )
