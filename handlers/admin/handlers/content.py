"""Content list and delete handlers."""

from aiogram import F, types
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest

from database import get_movie, delete_movie, get_all_movies

from .. import router
from ..constants import ADMIN_PERMISSION_MOVIES, DELETE_PANEL_PAGE_SIZE
from ..permissions import _ensure_message_access, _ensure_callback_access, _is_admin
from ..states import DeleteMovieState
from ..content_utils import _content_kind_key, _content_kind_label
from ..panels import _show_content_list, _show_delete_panel
from keyboards import delete_confirm_keyboard, ADMIN_ACTIONS


@router.callback_query(F.data.startswith("content_list:"))
async def content_list_callback(callback: types.CallbackQuery) -> None:
    if not await _ensure_callback_access(callback, permission=ADMIN_PERMISSION_MOVIES):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    from database import touch_user

    await touch_user(
        callback.from_user.id,
        callback.from_user.username,
        callback.from_user.full_name,
    )

    message = callback.message
    if message is None:
        await callback.answer()
        return

    parts = callback.data.split(":")
    if len(parts) < 2:
        await callback.answer("Noto'g'ri buyruq", show_alert=True)
        return

    action = parts[1]
    filter_key = None
    page = 0
    notice = "Ro'yxat yangilandi" if action == "refresh" else None

    if action == "refresh":
        if len(parts) >= 3:
            filter_key = parts[2]
        if len(parts) >= 4:
            try:
                page = int(parts[3])
            except ValueError:
                page = 0
    else:
        filter_key = action
        if len(parts) >= 3:
            try:
                page = int(parts[2])
            except ValueError:
                page = 0

    try:
        await _show_content_list(message, filter_key=filter_key, page=page, edit=True)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise

    await callback.answer(notice)


@router.callback_query(F.data.startswith("delete_panel:"))
async def delete_panel_callback(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _ensure_callback_access(callback, permission=ADMIN_PERMISSION_MOVIES):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    if callback.message is None:
        await callback.answer()
        return

    parts = callback.data.split(":")
    filter_key = parts[1] if len(parts) >= 2 else "movie"
    page = 0
    if len(parts) >= 3:
        try:
            page = int(parts[2])
        except ValueError:
            page = 0

    try:
        await _show_delete_panel(
            callback.message,
            state=state,
            filter_key=filter_key,
            page=page,
            edit=True,
        )
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise

    await callback.answer()


@router.callback_query(F.data.startswith(("del:", "del_")))
async def delete_callback(callback: types.CallbackQuery) -> None:
    if not await _ensure_callback_access(callback, permission=ADMIN_PERMISSION_MOVIES):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    code = ""
    filter_key: str | None = None
    page = 0
    if callback.data.startswith("del:"):
        parts = callback.data.split(":")
        if len(parts) >= 2:
            code = parts[1]
        if len(parts) >= 3:
            filter_key = parts[2]
        if len(parts) >= 4:
            try:
                page = int(parts[3])
            except ValueError:
                page = 0
    else:
        code = callback.data.split("_", 1)[1]

    if not code:
        await callback.answer("Noto'g'ri kod", show_alert=True)
        return

    movie = await get_movie(code)
    if movie is None:
        await callback.answer(
            "Kontent topilmadi yoki allaqachon o'chirilgan", show_alert=True
        )
        return

    title, _description, _file_id, content_kind = movie
    content_label = _content_kind_label(content_kind)
    filter_key = filter_key or _content_kind_key(content_kind)
    if callback.message is not None and filter_key:
        await callback.message.edit_text(
            "⚠️ <b>O'chirishni tasdiqlang</b>\n\n"
            f"{title}\n"
            f"<code>{code}</code> • {content_label}\n\n"
            "<i>Bu amalni ortga qaytarib bo'lmaydi.</i>",
            reply_markup=delete_confirm_keyboard(
                code,
                filter_key=filter_key,
                page=page,
            ),
            parse_mode="HTML",
        )
    await callback.answer("Tasdiqlang")


@router.message(DeleteMovieState.waiting_for_code)
async def receive_delete_code(message: types.Message, state: FSMContext) -> None:
    if not await _ensure_message_access(message, permission=ADMIN_PERMISSION_MOVIES):
        return

    if not message.text or not message.text.strip():
        await message.answer(
            "⚠️ <b>Kod yuboring</b>\n\n<i>O'chirish uchun kontent kodini kiriting.</i>",
            parse_mode="HTML",
        )
        return

    code = message.text.strip()
    if not code.isdigit():
        await message.answer("⚠️ Kod faqat raqamlardan iborat bo'lishi kerak.")
        return

    movie = await get_movie(code)
    if movie is None:
        await message.answer("🔎 Bunday kodli kontent topilmadi.")
        return

    data = await state.get_data()
    filter_key = data.get("delete_filter") or _content_kind_key(movie[3])
    page = int(data.get("delete_page") or 0)
    title, _description, _file_id, content_kind = movie
    content_label = _content_kind_label(content_kind)

    await message.answer(
        "⚠️ <b>O'chirishni tasdiqlang</b>\n\n"
        f"{title}\n"
        f"<code>{code}</code> • {content_label}\n\n"
        "<i>Bu amalni ortga qaytarib bo'lmaydi.</i>",
        reply_markup=delete_confirm_keyboard(
            code,
            filter_key=filter_key,
            page=page,
        ),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("delete_confirm:"))
async def delete_confirm_callback(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _ensure_callback_access(callback, permission=ADMIN_PERMISSION_MOVIES):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    if callback.message is None:
        await callback.answer()
        return

    parts = callback.data.split(":")
    if len(parts) < 4:
        await callback.answer("Noto'g'ri buyruq", show_alert=True)
        return

    code = parts[1]
    filter_key = parts[2]
    try:
        page = int(parts[3])
    except ValueError:
        page = 0

    movie = await get_movie(code)
    if movie is None:
        await callback.answer("Kontent allaqachon o'chirilgan", show_alert=True)
        try:
            await _show_delete_panel(
                callback.message,
                state=state,
                filter_key=filter_key,
                page=page,
                edit=True,
            )
        except TelegramBadRequest as error:
            if "message is not modified" not in str(error).lower():
                raise
        return

    title, _description, _file_id, content_kind = movie
    content_label = _content_kind_label(content_kind)
    await delete_movie(code)

    try:
        await _show_delete_panel(
            callback.message,
            state=state,
            filter_key=filter_key,
            page=page,
            edit=True,
        )
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise

    await callback.answer(f"✅ {content_label} o'chirildi: {title}")


async def movie_list_handler(message: types.Message) -> None:
    await _show_content_list(message)


async def delete_start_handler(message: types.Message, state: FSMContext) -> None:
    await _show_delete_panel(message, state=state)
