"""Ads handlers."""

from aiogram import F, types
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest

from advertising import extract_ad_payload
from database import touch_user, request_stop_ad

from .. import router
from ..constants import ADMIN_PERMISSION_ADS
from ..permissions import _ensure_message_access, _ensure_callback_access
from ..states import AdState
from ..panels import _show_ads_panel, _build_ads_panel_view, _refresh_saved_ads_panel, _ad_duration_limits_text
from ..render import _ad_preview, _ad_status_label, _format_duration
from ..flows import _parse_custom_duration, _launch_ad_campaign
from keyboards import ad_duration_keyboard


@router.callback_query(F.data == "ads_new")
async def start_ad_creation(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_callback_access(callback, permission=ADMIN_PERMISSION_ADS):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    await state.clear()
    await state.set_state(AdState.waiting_for_content)
    await callback.answer()

    if callback.message is not None:
        await state.update_data(
            ads_panel_chat_id=callback.message.chat.id,
            ads_panel_message_id=callback.message.message_id,
        )
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await callback.message.answer(
            "📣 Reklama uchun matn, rasm, video yoki fayl yuboring."
        )


@router.message(AdState.waiting_for_content)
async def receive_ad_content(message: types.Message, state: FSMContext) -> None:
    if not await _ensure_message_access(message, permission=ADMIN_PERMISSION_ADS):
        return

    payload = extract_ad_payload(message)
    if payload is None:
        await message.answer("Faqat matn, rasm, video yoki fayl yuboring.")
        return

    await state.update_data(**payload)
    await state.set_state(AdState.waiting_for_duration)
    await message.answer(
        f"📣 Reklama qabul qilindi.\n{_ad_preview(payload['content_type'], payload['text'])}\n"
        f"Muddatni xabar qilib yozing. Masalan: 45m, 2h, 1d.\nOraliq: {_ad_duration_limits_text()}.",
        reply_markup=ad_duration_keyboard(),
    )


@router.callback_query(AdState.waiting_for_duration, F.data == "ads_cancel")
async def cancel_ad_creation(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_callback_access(callback, permission=ADMIN_PERMISSION_ADS):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    data = await state.get_data()
    await state.clear()
    await callback.answer("Bekor qilindi")

    if callback.message is not None:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass

    await _refresh_saved_ads_panel(
        callback.bot,
        data.get("ads_panel_chat_id"),
        data.get("ads_panel_message_id"),
    )


@router.callback_query(AdState.waiting_for_duration, F.data.startswith("ads_duration_"))
async def remind_manual_ad_duration(callback: types.CallbackQuery) -> None:
    if not await _ensure_callback_access(callback, permission=ADMIN_PERMISSION_ADS):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    await callback.answer("Muddat yuboring")
    if callback.message is not None:
        await callback.message.answer(
            "⏳ Muddatni yuboring.\n"
            "Masalan: 45m, 2h, 1d.\n"
            f"Oraliq: {_ad_duration_limits_text()}."
        )


@router.message(AdState.waiting_for_duration)
async def finalize_ad_creation_from_text(
    message: types.Message, state: FSMContext
) -> None:
    if not await _ensure_message_access(message, permission=ADMIN_PERMISSION_ADS):
        return

    await touch_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.full_name,
    )

    if not message.text or not message.text.strip():
        await message.answer(
            "Muddatni matn ko'rinishida yuboring.\n"
            f"Masalan: 45m, 2h, 1d.\n"
            f"Oraliq: {_ad_duration_limits_text()}."
        )
        return

    duration_seconds = _parse_custom_duration(message.text)
    if duration_seconds is None:
        await message.answer(
            "Muddat formati noto'g'ri.\n"
            "Masalan: 45m, 2h, 1d.\n"
            f"Oraliq: {_ad_duration_limits_text()}."
        )
        return

    try:
        ad_id, data = await _launch_ad_campaign(
            bot=message.bot,
            state=state,
            admin_id=message.from_user.id,
            duration_seconds=duration_seconds,
        )
    except ValueError:
        await message.answer("Reklama ma'lumoti topilmadi.")
        await state.clear()
        return

    await message.answer(
        f"✅ Reklama #{ad_id} ishga tushdi.\n"
        f"Kontent: {_ad_preview(data['content_type'], data.get('text'))}\n"
        f"Muddat: {_format_duration(duration_seconds)}"
    )
    await _refresh_saved_ads_panel(
        message.bot,
        data.get("ads_panel_chat_id"),
        data.get("ads_panel_message_id"),
    )


@router.callback_query(F.data == "ads_refresh")
async def refresh_ads_panel(callback: types.CallbackQuery) -> None:
    if not await _ensure_callback_access(callback, permission=ADMIN_PERMISSION_ADS):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    await touch_user(
        callback.from_user.id,
        callback.from_user.username,
        callback.from_user.full_name,
    )

    if callback.message is not None:
        try:
            await _show_ads_panel(callback.message, edit=True)
        except TelegramBadRequest as error:
            if "message is not modified" not in str(error).lower():
                raise

    await callback.answer("Panel yangilandi")


@router.callback_query(F.data.startswith("ads_stop_"))
async def stop_ad(callback: types.CallbackQuery) -> None:
    if not await _ensure_callback_access(callback, permission=ADMIN_PERMISSION_ADS):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    try:
        ad_id = int(callback.data.rsplit("_", 1)[1])
    except ValueError:
        await callback.answer("Noto'g'ri reklama ID", show_alert=True)
        return

    status = await request_stop_ad(ad_id)
    if status is None:
        await callback.answer("Reklama topilmadi", show_alert=True)
        return

    if status in {"stop_requested", "stopping"}:
        text = "Reklama to'xtatish navbatiga qo'yildi"
    elif status in {"cleaning", "stopped", "expired"}:
        text = "Bu reklama allaqachon yopilmoqda yoki yopilgan"
    else:
        text = f"Bu reklama holati: {_ad_status_label(status)}"

    if callback.message is not None:
        try:
            await _show_ads_panel(callback.message, edit=True)
        except TelegramBadRequest as error:
            if "message is not modified" not in str(error).lower():
                raise

    await callback.answer(text, show_alert=False)
