"""Helper admin management handlers."""

from aiogram import F, types
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest

from database import get_helper_admin, list_helper_admins, remove_helper_admin, set_helper_admin_permission, get_user_snapshot, upsert_helper_admin
from config import ADMIN_ID

from .. import router
from ..permissions import _ensure_message_access, _ensure_callback_access
from ..panels import _show_helper_admins_panel, _show_helper_admin_detail
from ..flows import _extract_helper_admin_candidate
from ..states import HelperAdminState


@router.callback_query(F.data == "helper_admins:back")
async def helper_admins_back(callback: types.CallbackQuery) -> None:
    if not await _ensure_callback_access(callback, owner_only=True):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    if callback.message is None:
        await callback.answer()
        return

    try:
        await _show_helper_admins_panel(callback.message, edit=True)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise

    await callback.answer()


@router.callback_query(F.data == "helper_admins:add")
async def helper_admins_add(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_callback_access(callback, owner_only=True):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    await state.clear()
    await state.set_state(HelperAdminState.waiting_for_user)
    await callback.answer()

    if callback.message is not None:
        await state.update_data(
            helper_panel_chat_id=callback.message.chat.id,
            helper_panel_message_id=callback.message.message_id,
        )
        await callback.message.answer(
            "👥 Yordamchi admin qo'shish.\n"
            "User ID yuboring yoki o'sha odamning xabarini forward qiling."
        )


@router.callback_query(F.data.startswith("helper_admins:open:"))
async def helper_admins_open(callback: types.CallbackQuery) -> None:
    if not await _ensure_callback_access(callback, owner_only=True):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    if callback.message is None:
        await callback.answer()
        return

    try:
        user_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("Noto'g'ri admin ID", show_alert=True)
        return

    helper_admin = await get_helper_admin(user_id)
    if helper_admin is None:
        await callback.answer("Yordamchi admin topilmadi", show_alert=True)
        try:
            await _show_helper_admins_panel(callback.message, edit=True)
        except TelegramBadRequest as error:
            if "message is not modified" not in str(error).lower():
                raise
        return

    try:
        await _show_helper_admin_detail(callback.message, helper_admin, edit=True)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise

    await callback.answer()


@router.callback_query(F.data.startswith("helper_admins:toggle:"))
async def helper_admins_toggle(callback: types.CallbackQuery) -> None:
    if not await _ensure_callback_access(callback, owner_only=True):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    if callback.message is None:
        await callback.answer()
        return

    parts = callback.data.split(":")
    if len(parts) < 4:
        await callback.answer("Noto'g'ri buyruq", show_alert=True)
        return

    try:
        user_id = int(parts[2])
    except ValueError:
        await callback.answer("Noto'g'ri admin ID", show_alert=True)
        return

    permission = parts[3]
    helper_admin = await get_helper_admin(user_id)
    if helper_admin is None:
        await callback.answer("Yordamchi admin topilmadi", show_alert=True)
        return

    enabled = bool(helper_admin["permissions"].get(permission))
    try:
        updated = await set_helper_admin_permission(user_id, permission, not enabled)
    except ValueError:
        await callback.answer("Noto'g'ri ruxsat", show_alert=True)
        return

    if not updated:
        await callback.answer("Yordamchi admin topilmadi", show_alert=True)
        return

    helper_admin = await get_helper_admin(user_id)
    if helper_admin is None:
        await callback.answer("Yordamchi admin topilmadi", show_alert=True)
        return

    try:
        await _show_helper_admin_detail(callback.message, helper_admin, edit=True)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise

    await callback.answer("Ruxsat yangilandi")


@router.callback_query(F.data.startswith("helper_admins:remove:"))
async def helper_admins_remove(callback: types.CallbackQuery) -> None:
    if not await _ensure_callback_access(callback, owner_only=True):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    if callback.message is None:
        await callback.answer()
        return

    try:
        user_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("Noto'g'ri admin ID", show_alert=True)
        return

    await remove_helper_admin(user_id)

    try:
        await _show_helper_admins_panel(callback.message, edit=True)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise

    await callback.answer("Yordamchi admin olib tashlandi")


@router.message(HelperAdminState.waiting_for_user)
async def receive_helper_admin_user(message: types.Message, state: FSMContext) -> None:
    if not await _ensure_message_access(message, owner_only=True):
        return

    candidate = _extract_helper_admin_candidate(message)
    if candidate is None:
        await message.answer(
            "❌ User ID yuboring yoki foydalanuvchining xabarini forward qiling."
        )
        return

    user_id, username, full_name = candidate
    if user_id == ADMIN_ID:
        await message.answer("Asosiy admin allaqachon mavjud.")
        return

    user_snapshot = await get_user_snapshot(user_id)
    if user_snapshot is not None:
        username = user_snapshot[0] or username
        full_name = user_snapshot[1] or full_name

    await upsert_helper_admin(
        user_id,
        username=username,
        full_name=full_name,
        added_by=message.from_user.id,
    )
    await state.clear()

    helper_admin = await get_helper_admin(user_id)
    if helper_admin is not None:
        await message.answer(
            "✅ Yordamchi admin qo'shildi.\n" "Endi unga ruxsatlarni belgilang."
        )
        await _show_helper_admin_detail(message, helper_admin)
