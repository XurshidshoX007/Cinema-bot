"""Edit movie handlers."""

from aiogram import F, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest

from database import get_serial_titles, update_movie_title, update_movie_description, update_movie_file_id, get_serial_group_for_lookup

from .. import router
from ..constants import ADMIN_PERMISSION_MOVIES
from ..permissions import _ensure_message_access, _ensure_callback_access
from ..states import EditMovieState
from ..content_utils import _content_kind_key, _safe_html, _normalized_lookup_text, _extract_uploaded_video_file_id, _compact_text_preview
from ..edit_helpers import _refresh_edit_movie_state, _show_edit_movie_panel, _select_edit_movie_target
from ..panels import _show_content_list  # not needed


@router.message(Command(commands={"edit_movie", "edit_content", "editcontent"}))
async def start_edit_movie(message: types.Message, state: FSMContext) -> None:
    if not await _ensure_message_access(message, permission=ADMIN_PERMISSION_MOVIES):
        return

    await state.clear()
    await state.set_state(EditMovieState.waiting_for_code)
    await message.answer(
        "✏️ <b>Kontent tahrirlash rejimi</b>\n\n"
        "Tahrirlamoqchi bo'lgan kontent kodini yuboring.",
        parse_mode="HTML",
    )


@router.message(
    StateFilter(EditMovieState.waiting_for_code),
    F.text,
)
async def receive_edit_movie_code(
    message: types.Message,
    state: FSMContext,
) -> None:
    from keyboards import ADMIN_ACTIONS

    if message.text in ADMIN_ACTIONS:
        return
    if not await _ensure_message_access(message, permission=ADMIN_PERMISSION_MOVIES):
        return

    await _select_edit_movie_target(message, state, message.text or "")


@router.callback_query(
    EditMovieState.waiting_for_action,
    F.data.startswith("edit_movie:"),
)
async def edit_movie_action_callback(
    callback: types.CallbackQuery,
    state: FSMContext,
) -> None:
    if not await _ensure_callback_access(callback, permission=ADMIN_PERMISSION_MOVIES):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    message = callback.message
    if message is None:
        await callback.answer()
        return

    payload = await state.get_data()
    code = str(payload.get("edit_movie_code") or "").strip()
    if not code:
        await state.clear()
        await callback.answer("Seans tugagan", show_alert=True)
        return

    action = callback.data.split(":", 1)[1]

    try:
        await message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass

    if action == "done":
        await state.clear()
        await callback.answer("Tahrirlash yakunlandi")
        await message.answer("✅ Tahrirlash rejimi yakunlandi.")
        return

    content_kind = _content_kind_key(str(payload.get("edit_movie_kind") or "movie"))
    display_title = str(payload.get("edit_movie_title") or "Kontent").strip() or "Kontent"
    serial_title = (
        str(payload.get("edit_movie_serial_title") or "").strip() or display_title
    )

    if action == "title":
        await state.set_state(EditMovieState.waiting_for_title)
        if content_kind == "serial":
            await callback.answer("Yangi serial nomini yuboring")
            await message.answer(
                "📝 <b>Serial nomi</b>\n\n"
                f"Joriy nom: <b>{_safe_html(serial_title)}</b>\n\n"
                "Yangi serial nomini yuboring.\n"
                "<i>Shu guruhdagi qismlar birga yangilanadi.</i>",
                parse_mode="HTML",
            )
        else:
            await callback.answer("Yangi nomni yuboring")
            await message.answer(
                "📝 <b>Kontent nomi</b>\n\n"
                f"Joriy nom: <b>{_safe_html(display_title)}</b>\n\n"
                "Yangi nomni yuboring.",
                parse_mode="HTML",
            )
        return

    if action == "description":
        await state.set_state(EditMovieState.waiting_for_description)
        await callback.answer("Yangi tavsifni yuboring")
        await message.answer(
            "📄 <b>Kontent tavsifi</b>\n\n"
            f"Joriy tavsif: {_safe_html(_compact_text_preview(str(payload.get('edit_movie_description') or ''), limit=260))}\n\n"
            "Yangi tavsifni matn ko'rinishida yuboring.",
            parse_mode="HTML",
        )
        return

    if action == "media":
        await state.set_state(EditMovieState.waiting_for_media)
        await callback.answer("Yangi media yuboring")
        await message.answer(
            "🎞 <b>Yangi media</b>\n\n"
            f"📌 Kod: <code>{_safe_html(code)}</code>\n"
            f"🎬 Nomi: <b>{_safe_html(display_title)}</b>\n\n"
            "Video yoki video-faylni document ko'rinishida yuboring.",
            parse_mode="HTML",
        )
        return

    await callback.answer("Noto'g'ri amal", show_alert=True)


@router.message(
    StateFilter(EditMovieState.waiting_for_action),
    F.text,
)
async def receive_edit_movie_action_text(
    message: types.Message,
    state: FSMContext,
) -> None:
    from keyboards import ADMIN_ACTIONS

    if message.text in ADMIN_ACTIONS:
        return
    if not await _ensure_message_access(message, permission=ADMIN_PERMISSION_MOVIES):
        return

    raw_text = (message.text or "").strip()
    if raw_text.isdigit():
        await _select_edit_movie_target(message, state, raw_text)
        return

    await message.answer(
        "Pastdagi tugmalardan birini tanlang.\n"
        "Yoki boshqa kontentni tahrirlash uchun uning kodini yuboring."
    )


@router.message(
    StateFilter(EditMovieState.waiting_for_title),
    F.text,
)
async def receive_edit_movie_title(
    message: types.Message,
    state: FSMContext,
) -> None:
    from keyboards import ADMIN_ACTIONS

    if message.text in ADMIN_ACTIONS:
        return
    if not await _ensure_message_access(message, permission=ADMIN_PERMISSION_MOVIES):
        return

    new_title = (message.text or "").strip()
    if not new_title:
        await message.answer("Nomni matn ko'rinishida yuboring.")
        return

    payload = await state.get_data()
    code = str(payload.get("edit_movie_code") or "").strip()
    if not code:
        await state.clear()
        await message.answer("Seans tugadi. Qayta /edit_movie yuboring.")
        return

    content_kind = _content_kind_key(str(payload.get("edit_movie_kind") or "movie"))
    current_serial_title = (
        str(payload.get("edit_movie_serial_title") or "").strip()
        or str(payload.get("edit_movie_title") or "").strip()
        or "Serial"
    )
    if (
        content_kind == "serial"
        and _normalized_lookup_text(new_title)
        != _normalized_lookup_text(current_serial_title)
    ):
        serial_titles = await get_serial_titles()
        if any(
            _normalized_lookup_text(existing_title) == _normalized_lookup_text(new_title)
            for existing_title in serial_titles
        ):
            await message.answer("Bu serial nomi allaqachon mavjud. Boshqa nom yuboring.")
            return

    ok = await update_movie_title(code, new_title)
    if not ok:
        await message.answer("Nom yangilanmadi. Kod yoki kiritilgan qiymatni tekshirib qayta urinib ko'ring.")
        return

    refreshed = await _refresh_edit_movie_state(state, code)
    if refreshed is None:
        await state.clear()
        await message.answer("Kontent topilmadi. Qayta /edit_movie yuboring.")
        return

    await message.answer("✅ Nom yangilandi.")
    await _show_edit_movie_panel(message, state)


@router.message(
    StateFilter(EditMovieState.waiting_for_description),
    F.text,
)
async def receive_edit_movie_description(
    message: types.Message,
    state: FSMContext,
) -> None:
    from keyboards import ADMIN_ACTIONS

    if message.text in ADMIN_ACTIONS:
        return
    if not await _ensure_message_access(message, permission=ADMIN_PERMISSION_MOVIES):
        return

    new_description = (message.text or "").strip()
    if not new_description:
        await message.answer("Tavsifni matn ko'rinishida yuboring.")
        return

    payload = await state.get_data()
    code = str(payload.get("edit_movie_code") or "").strip()
    if not code:
        await state.clear()
        await message.answer("Seans tugadi. Qayta /edit_movie yuboring.")
        return

    ok = await update_movie_description(code, new_description)
    if not ok:
        await message.answer(
            "Tavsif yangilanmadi. Kod yoki kiritilgan qiymatni tekshirib qayta urinib ko'ring."
        )
        return

    refreshed = await _refresh_edit_movie_state(state, code)
    if refreshed is None:
        await state.clear()
        await message.answer("Kontent topilmadi. Qayta /edit_movie yuboring.")
        return

    await message.answer("✅ Tavsif yangilandi.")
    await _show_edit_movie_panel(message, state)


@router.message(
    StateFilter(EditMovieState.waiting_for_media),
    F.video | F.document,
)
async def receive_edit_movie_media(
    message: types.Message,
    state: FSMContext,
) -> None:
    if not await _ensure_message_access(message, permission=ADMIN_PERMISSION_MOVIES):
        return

    payload = await state.get_data()
    code = str(payload.get("edit_movie_code") or "").strip()
    if not code:
        await state.clear()
        await message.answer("Seans tugadi. Qayta /edit_movie yuboring.")
        return

    file_id = _extract_uploaded_video_file_id(message)
    if not file_id:
        await message.answer(
            "Video yuboring.\nMP4 kabi video-faylni document ko'rinishida ham yuborishingiz mumkin."
        )
        return

    ok = await update_movie_file_id(code, file_id)
    if not ok:
        await message.answer("Media yangilanmadi. Kodni tekshirib qayta urinib ko'ring.")
        return

    refreshed = await _refresh_edit_movie_state(state, code)
    if refreshed is None:
        await state.clear()
        await message.answer("Kontent topilmadi. Qayta /edit_movie yuboring.")
        return

    await message.answer("✅ Media yangilandi.")
    await _show_edit_movie_panel(message, state)


@router.message(StateFilter(EditMovieState.waiting_for_media))
async def receive_edit_movie_media_invalid(
    message: types.Message,
    state: FSMContext,
) -> None:
    if not await _ensure_message_access(message, permission=ADMIN_PERMISSION_MOVIES):
        return

    await message.answer(
        "Video yoki video-fayl yuboring.\nBekor qilish uchun menyudan boshqa bo'limni tanlang."
    )
