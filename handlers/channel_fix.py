"""Channel management text lives in this file."""

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext

from keyboards import ADMIN_ACTIONS, CHANNELS_BUTTON, USER_ACTIONS
from repositories.channels import add_sponsor_channel, remove_sponsor_channel
from services.channel_service import (
    CHANNEL_INPUT_ERROR_TEXT,
    build_channels_menu_view,
    is_valid_channel_url,
    parse_channel_submission,
)

from . import admin_facade, user

router = Router()


async def _show_channels_menu(
    message: types.Message,
    state: FSMContext,
    *,
    edit: bool = False,
) -> None:
    text, markup = await build_channels_menu_view()
    if edit:
        await message.edit_text(
            text,
            reply_markup=markup,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    else:
        await message.answer(
            text,
            reply_markup=markup,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    await state.set_state(admin_facade.AdminChannelState.waiting_for_channel)


@router.message(F.text == CHANNELS_BUTTON)
async def admin_channels_menu(message: types.Message, state: FSMContext) -> None:
    if not await admin_facade.ensure_message_access(message, permission="channels"):
        return

    await state.clear()
    await _show_channels_menu(message, state)


@router.message(admin_facade.AdminChannelState.waiting_for_channel)
async def receive_new_channel(message: types.Message, state: FSMContext) -> None:
    if not await admin_facade.ensure_message_access(message, permission="channels"):
        return

    if message.text in ADMIN_ACTIONS:
        await state.clear()
        await admin_facade.admin_global_handler(message, state)
        return

    if message.text in USER_ACTIONS:
        await state.clear()
        await user.user_global_handler(message, state)
        return

    parsed = parse_channel_submission(message.text)
    if parsed is None:
        await message.answer(CHANNEL_INPUT_ERROR_TEXT, parse_mode="HTML")
        return

    channel_id, channel_url, channel_name = parsed
    if not is_valid_channel_url(channel_url):
        await message.answer(
            "Link `http` yoki `https` bilan boshlanishi kerak.", parse_mode="Markdown"
        )
        return

    await add_sponsor_channel(channel_id, channel_url, channel_name)
    await message.answer(
        f"✅ <b>Kanal qo'shildi</b>\nID: <code>{channel_id}</code>\nLink: {channel_url}\nNomi: {channel_name}",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    await _show_channels_menu(message, state)


@router.callback_query(F.data.startswith("del_channel:"))
async def handle_delete_channel(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await admin_facade.ensure_callback_access(callback, permission="channels"):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return

    if callback.message is None:
        await callback.answer("Xabar topilmadi", show_alert=True)
        return

    channel_id = callback.data.split(":", 1)[1]
    await remove_sponsor_channel(channel_id)

    try:
        await _show_channels_menu(callback.message, state, edit=True)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise

    await callback.answer("Kanal o'chirildi")
