"""Media refresh handlers."""

from html import escape

from aiogram import F, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext

from database import get_movie, get_serial_episodes, get_serial_group_for_lookup, update_movie_file_id

from .. import router
from ..constants import ADMIN_PERMISSION_MOVIES
from ..permissions import _ensure_message_access
from ..states import RefreshMediaState


@router.message(Command(commands={"refresh_media", "fixmedia", "mediafix"}))
async def start_media_refresh(message: types.Message, state: FSMContext) -> None:
    if not await _ensure_message_access(message, permission=ADMIN_PERMISSION_MOVIES):
        return

    await state.clear()
    await state.set_state(RefreshMediaState.waiting_for_code)
    await message.answer(
        "🔁 <b>Media yangilash rejimi</b>\n\n"
        "Yangi media bog'lamoqchi bo'lgan kodni yuboring.",
        parse_mode="HTML",
    )


@router.message(
    StateFilter(RefreshMediaState.waiting_for_code),
    F.text,
)
async def receive_media_refresh_code(
    message: types.Message,
    state: FSMContext,
) -> None:
    if not await _ensure_message_access(message, permission=ADMIN_PERMISSION_MOVIES):
        return

    code = message.text.strip()
    if not code:
        await message.answer("Kod yuboring.")
        return

    resolved_code = code
    movie = await get_movie(resolved_code)
    if movie is None:
        serial_group = await get_serial_group_for_lookup(code)
        if serial_group is not None:
            episodes = await get_serial_episodes(serial_group[0])
            if episodes:
                resolved_code = episodes[0][0]
                movie = await get_movie(resolved_code)

    if movie is None:
        await message.answer(
            "Kod topilmadi. Iltimos, mavjud kino yoki serial qismi kodini yuboring."
        )
        return

    title, _description, _file_id, content_kind = movie
    await state.update_data(
        refresh_media_code=resolved_code,
        refresh_media_title=title,
        refresh_media_kind=content_kind,
    )
    await state.set_state(RefreshMediaState.waiting_for_media)

    if resolved_code != code:
        note = f"\n<i>Guruh kodi {escape(code)} -> qism kodi {escape(resolved_code)} olindi.</i>"
    else:
        note = ""

    await message.answer(
        "📥 <b>Yangi media yuboring</b>\n\n"
        f"📌 Kod: <code>{escape(resolved_code)}</code>\n"
        f"🎬 Nomi: <b>{escape(title)}</b>\n"
        f"{note}\n\n"
        "Video yoki dokument yuboring. Yuborilgach, eski media darhol yangilanadi.",
        parse_mode="HTML",
    )


@router.message(
    StateFilter(RefreshMediaState.waiting_for_media),
    F.video | F.document,
)
async def receive_media_refresh_file(
    message: types.Message,
    state: FSMContext,
) -> None:
    if not await _ensure_message_access(message, permission=ADMIN_PERMISSION_MOVIES):
        return

    payload = await state.get_data()
    code = str(payload.get("refresh_media_code") or "").strip()
    title = str(payload.get("refresh_media_title") or "Kontent")
    if not code:
        await state.clear()
        await message.answer("Seans tugadi. Qayta /refresh_media yuboring.")
        return

    file_id = message.video.file_id if message.video else message.document.file_id
    source_kind = "video" if message.video else "document"
    ok = await update_movie_file_id(code, file_id)
    await state.clear()

    if not ok:
        await message.answer(
            "Media yangilanmadi. Iltimos, kodni qayta tekshirib yana urinib ko'ring."
        )
        return

    await message.answer(
        "✅ <b>Media yangilandi</b>\n\n"
        f"📌 Kod: <code>{escape(code)}</code>\n"
        f"🎬 Nomi: <b>{escape(title)}</b>\n"
        f"📎 Manba turi: <b>{source_kind}</b>\n\n"
        "Endi foydalanuvchi shu kodni yuborsa media normal ketadi.",
        parse_mode="HTML",
    )


@router.message(StateFilter(RefreshMediaState.waiting_for_media))
async def receive_media_refresh_invalid(
    message: types.Message,
    state: FSMContext,
) -> None:
    if not await _ensure_message_access(message, permission=ADMIN_PERMISSION_MOVIES):
        return

    await message.answer(
        "Video yoki dokument yuboring.\nBekor qilish uchun menyudan boshqa bo'limni tanlang."
    )
