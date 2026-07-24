"""Requests handlers."""

from aiogram import F, types
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from database import get_pending_requests, get_request, update_request_status

from .. import router
from ..constants import ADMIN_PERMISSION_REQUESTS
from ..permissions import _ensure_callback_access
from ..flows import _start_add_movie_flow
from ..request_utils import _request_text, _request_existing_match_text, _find_existing_content_for_request, _send_existing_content_to_user, _request_rejected_text
from keyboards import request_review_keyboard, request_existing_match_keyboard


async def show_requests(message: types.Message) -> None:
    requests = await get_pending_requests()
    if not requests:
        await message.answer("📭 So'rov yo'q")
        return

    for request_id, user_id, text, file_id in requests:
        await send_request_review(
            message,
            request_id=request_id,
            user_id=user_id,
            text=text,
            file_id=file_id,
        )


async def send_request_review(
    message: types.Message,
    *,
    request_id: int,
    user_id: int,
    text: str,
    file_id: str | None,
) -> None:
    review_text = _request_text(request_id, user_id, text)
    keyboard = request_review_keyboard(request_id)

    if not file_id:
        await message.answer(review_text, reply_markup=keyboard)
        return

    try:
        await message.answer_photo(
            photo=file_id, caption=review_text, reply_markup=keyboard
        )
    except TelegramBadRequest:
        await message.answer_video(
            video=file_id, caption=review_text, reply_markup=keyboard
        )


@router.callback_query(F.data.startswith("accept_"))
async def accept_request(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_callback_access(
        callback, permission=ADMIN_PERMISSION_REQUESTS
    ):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    message = callback.message
    if message is None:
        await callback.answer("Xabar topilmadi", show_alert=True)
        return

    try:
        rid = int(callback.data.split("_", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Noto'g'ri so'rov", show_alert=True)
        return
    request = await get_request(rid)
    if not request:
        await callback.answer("Bu so'rov topilmadi", show_alert=True)
        return

    _, user_id, text, _file_id, status = request
    if status not in {"pending", "accepted"}:
        await callback.answer("Bu so'rov allaqachon yopilgan", show_alert=True)
        return

    if status == "pending":
        await update_request_status(rid, "accepted")

    existing_match = await _find_existing_content_for_request(text)
    if existing_match is not None:
        code, title, content_kind = existing_match
        try:
            await message.edit_reply_markup()
        except TelegramBadRequest:
            pass

        await message.answer(
            _request_existing_match_text(
                request_text=text or "",
                code=code,
                title=title,
                content_kind=content_kind,
            ),
            reply_markup=request_existing_match_keyboard(rid, code),
            parse_mode="HTML",
        )
        await callback.answer("Mos kontent topildi")
        return

    await _start_add_movie_flow(
        message,
        state,
        content_kind="movie",
        request_id=rid,
        request_user_id=user_id,
        request_text=text,
        request_chat_id=message.chat.id,
        request_message_id=message.message_id,
    )
    await callback.answer("Qo'shish boshlandi")


@router.callback_query(F.data.startswith("req_use:"))
async def request_use_existing(callback: types.CallbackQuery) -> None:
    if not await _ensure_callback_access(
        callback, permission=ADMIN_PERMISSION_REQUESTS
    ):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    message = callback.message
    if message is None:
        await callback.answer("Xabar topilmadi", show_alert=True)
        return

    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Noto'g'ri buyruq", show_alert=True)
        return

    try:
        rid = int(parts[1])
    except ValueError:
        await callback.answer("Noto'g'ri so'rov", show_alert=True)
        return

    code = parts[2]
    request = await get_request(rid)
    if not request:
        await callback.answer("So'rov topilmadi", show_alert=True)
        return

    _, user_id, _text, _file_id, status = request
    if status not in {"pending", "accepted"}:
        await callback.answer("Bu so'rov allaqachon yopilgan", show_alert=True)
        return

    try:
        sent = await _send_existing_content_to_user(callback.bot, user_id, code)
    except (TelegramBadRequest, TelegramForbiddenError):
        await callback.answer(
            "Foydalanuvchiga yuborib bo'lmadi", show_alert=True
        )
        return

    if not sent:
        await callback.answer("Kontent topilmadi", show_alert=True)
        return

    await update_request_status(rid, "completed")
    try:
        await message.edit_reply_markup()
    except TelegramBadRequest:
        pass
    await callback.answer("Mavjud kontent foydalanuvchiga yuborildi")


@router.callback_query(F.data.startswith("req_new:"))
async def request_add_new(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_callback_access(
        callback, permission=ADMIN_PERMISSION_REQUESTS
    ):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    message = callback.message
    if message is None:
        await callback.answer("Xabar topilmadi", show_alert=True)
        return

    parts = callback.data.split(":")
    if len(parts) < 2:
        await callback.answer("Noto'g'ri buyruq", show_alert=True)
        return

    try:
        rid = int(parts[1])
    except ValueError:
        await callback.answer("Noto'g'ri so'rov", show_alert=True)
        return

    request = await get_request(rid)
    if not request:
        await callback.answer("So'rov topilmadi", show_alert=True)
        return

    _, user_id, text, _file_id, status = request
    if status not in {"pending", "accepted"}:
        await callback.answer("Bu so'rov allaqachon yopilgan", show_alert=True)
        return

    if status == "pending":
        await update_request_status(rid, "accepted")

    await _start_add_movie_flow(
        message,
        state,
        content_kind="movie",
        request_id=rid,
        request_user_id=user_id,
        request_text=text,
        request_chat_id=message.chat.id,
        request_message_id=message.message_id,
    )
    await callback.answer("Yangi qo'shish boshlandi")


@router.callback_query(F.data.startswith("reject_"))
async def reject_request(callback: types.CallbackQuery) -> None:
    if not await _ensure_callback_access(
        callback, permission=ADMIN_PERMISSION_REQUESTS
    ):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    message = callback.message
    if message is None:
        await callback.answer("Xabar topilmadi", show_alert=True)
        return

    try:
        rid = int(callback.data.split("_", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Noto'g'ri so'rov", show_alert=True)
        return
    request = await get_request(rid)
    if not request:
        await callback.answer("Bu so'rov topilmadi", show_alert=True)
        return

    _, user_id, request_text, _file_id, status = request
    if status not in {"pending", "accepted"}:
        await callback.answer("Bu so'rov allaqachon yopilgan", show_alert=True)
        return

    await update_request_status(rid, "rejected")

    try:
        await callback.bot.send_message(
            user_id,
            _request_rejected_text(),
            parse_mode="HTML",
        )
        await callback.answer("So'rov bo'yicha xabar yuborildi")
    except (TelegramBadRequest, TelegramForbiddenError):
        await callback.answer("So'rov yopildi, lekin foydalanuvchiga xabar yuborilmadi")

    await message.edit_reply_markup()
