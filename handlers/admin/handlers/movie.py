"""Movie addition flow."""

from aiogram import F, types
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from database import add_movie, add_movie_auto_code, is_content_code_taken, update_request_status

from .. import router
from ..constants import ADMIN_PERMISSION_MOVIES
from ..permissions import _ensure_message_access
from ..states import AddMovieState
from ..content_utils import _content_kind_key, _content_kind_label, _serial_description_prompt_text, _serial_video_prompt_text, _safe_html, _extract_uploaded_video_file_id
from ..flows import _start_add_movie_flow, _show_serial_mode_picker, _show_serial_titles_picker, _start_serial_continuation_flow, _show_serial_continue_prompt
from ..panels import _show_serial_mode_picker as _show_serial_mode_picker_panel
from ..request_utils import _send_existing_content_to_user, _request_added_text
from keyboards import serial_upload_finish_keyboard, serial_continue_keyboard


@router.message(AddMovieState.waiting_for_code)
async def receive_new_movie_code(message: types.Message, state: FSMContext) -> None:
    if not await _ensure_message_access(message, permission=ADMIN_PERMISSION_MOVIES):
        return

    data = await state.get_data()
    content_kind = _content_kind_key(data.get("content_kind"))
    series_title = data.get("series_title")
    episode_number = data.get("episode_number")

    if not message.text:
        await message.answer(
            "⚠️ <b>Kod noto'g'ri</b>\n\n<i>Iltimos, raqamli kod yuboring</i>",
            parse_mode="HTML",
        )
        return

    code = message.text.strip()
    if not code.isdigit():
        await message.answer(
            "⚠️ <b>Kod noto'g'ri</b>\n\n<i>Iltimos, raqamli kod yuboring</i>",
            parse_mode="HTML",
        )
        return

    request_id = data.get("request_id")
    request_user_id = data.get("request_user_id")
    if request_id and request_user_id and await is_content_code_taken(code):
        try:
            sent = await _send_existing_content_to_user(
                message.bot,
                int(request_user_id),
                code,
            )
        except (TelegramBadRequest, TelegramForbiddenError):
            await message.answer(
                "⚠️ Mavjud kontentni foydalanuvchiga yuborib bo'lmadi."
            )
            return

        if not sent:
            await message.answer("⚠️ Kontent topilmadi yoki yuborib bo'lmadi.")
            return

        await update_request_status(int(request_id), "completed")
        request_chat_id = data.get("request_chat_id")
        request_message_id = data.get("request_message_id")
        if request_chat_id and request_message_id:
            try:
                await message.bot.edit_message_reply_markup(
                    chat_id=int(request_chat_id),
                    message_id=int(request_message_id),
                    reply_markup=None,
                )
            except TelegramBadRequest:
                pass

        await state.clear()
        await message.answer("✅ Mavjud kontent foydalanuvchiga yuborildi.")
        return

    if await is_content_code_taken(code):
        await message.answer(
            "⚠️ <b>Bu kod band</b>\n\n<i>Boshqa kod yuboring</i>",
            parse_mode="HTML",
        )
        return

    await state.update_data(code=code)
    if content_kind == "serial" and series_title and episode_number:
        await state.set_state(AddMovieState.waiting_for_description)
        await message.answer(
            _serial_description_prompt_text(str(series_title), int(episode_number)),
            parse_mode="HTML",
        )
        return

    await state.set_state(AddMovieState.waiting_for_title)
    if content_kind == "serial":
        await message.answer(
            "📺 <b>Serial nomi</b>\n\n<i>Serial nomini yuboring</i>",
            parse_mode="HTML",
        )
        return

    await message.answer("Nomini yuboring.")


@router.message(AddMovieState.waiting_for_title)
async def receive_new_movie_title(message: types.Message, state: FSMContext) -> None:
    if not await _ensure_message_access(message, permission=ADMIN_PERMISSION_MOVIES):
        return

    data = await state.get_data()
    content_kind = _content_kind_key(data.get("content_kind"))

    if not message.text or not message.text.strip():
        if content_kind == "serial":
            await message.answer(
                "⚠️ <b>Serial nomi kerak</b>\n\n"
                "<i>Nomni matn ko'rinishida yuboring</i>",
                parse_mode="HTML",
            )
            return

        await message.answer("Nomni matn ko'rinishida yuboring.")
        return

    title = message.text.strip()
    if content_kind == "serial":
        await state.update_data(series_title=title, episode_number=1)
    else:
        await state.update_data(title=title)
    await state.set_state(AddMovieState.waiting_for_description)
    if content_kind == "serial":
        await message.answer(
            "📝 <b>1-qism tavsifi</b>\n\n<i>Qisqa tavsif yuboring</i>",
            parse_mode="HTML",
        )
    else:
        await message.answer("Tavsif:")


@router.message(AddMovieState.waiting_for_description)
async def receive_new_movie_description(
    message: types.Message, state: FSMContext
) -> None:
    if not await _ensure_message_access(message, permission=ADMIN_PERMISSION_MOVIES):
        return

    data = await state.get_data()
    content_kind = _content_kind_key(data.get("content_kind"))
    series_title = data.get("series_title")
    episode_number = data.get("episode_number")

    if not message.text or not message.text.strip():
        if content_kind == "serial":
            await message.answer(
                "⚠️ <b>Tavsif kerak</b>\n\n"
                "<i>Tavsifni matn ko'rinishida yuboring</i>",
                parse_mode="HTML",
            )
            return

        await message.answer("Tavsifni matn ko'rinishida yuboring.")
        return

    await state.update_data(description=message.text.strip())
    await state.set_state(AddMovieState.waiting_for_video)
    if content_kind == "serial":
        title = str(series_title or "Serial")
        part_number = int(episode_number or 1)
        await message.answer(
            _serial_video_prompt_text(title, part_number),
            parse_mode="HTML",
        )
    else:
        await message.answer("Videoni yuboring.")


@router.message(AddMovieState.waiting_for_video, F.video | F.document)
async def receive_new_movie_video(message: types.Message, state: FSMContext) -> None:
    if not await _ensure_message_access(message, permission=ADMIN_PERMISSION_MOVIES):
        return

    data = await state.get_data()
    content_kind = _content_kind_key(data.get("content_kind"))
    content_label = _content_kind_label(content_kind)
    uploaded_file_id = _extract_uploaded_video_file_id(message)
    if not uploaded_file_id:
        await message.answer(
            f"{content_label} uchun video yuboring.\n"
            "MP4 kabi video-faylni oddiy fayl (document) qilib ham yuborishingiz mumkin."
        )
        return

    series_title = data.get("series_title")
    episode_number = data.get("episode_number")
    title = series_title if content_kind == "serial" else data["title"]
    content_code = data.get("code")
    description = str(data.get("description") or "")
    quick_serial_upload = (
        content_kind == "serial"
        and not content_code
        and bool(data.get("serial_quick_upload"))
    )

    if content_code:
        ok = await add_movie(
            str(content_code),
            title,
            description,
            uploaded_file_id,
            content_kind=content_kind,
            series_title=series_title,
            episode_number=episode_number,
        )
        saved_code = str(content_code) if ok else None
    else:
        saved_code = await add_movie_auto_code(
            title,
            description,
            uploaded_file_id,
            content_kind=content_kind,
            series_title=series_title,
            episode_number=episode_number,
        )
        ok = saved_code is not None

    if not ok or saved_code is None:
        await message.answer("Bu kod allaqachon mavjud.")
        await state.clear()
        return

    await state.update_data(code=saved_code)

    request_id = data.get("request_id")
    request_user_id = data.get("request_user_id")

    if request_id and request_user_id:
        await update_request_status(int(request_id), "completed")

        request_chat_id = data.get("request_chat_id")
        request_message_id = data.get("request_message_id")

        if request_chat_id and request_message_id:
            try:
                await message.bot.edit_message_reply_markup(
                    chat_id=int(request_chat_id),
                    message_id=int(request_message_id),
                    reply_markup=None,
                )
            except TelegramBadRequest:
                pass

        try:
            await message.bot.send_message(
                int(request_user_id),
                _request_added_text(saved_code, content_kind=content_kind),
            )
            await message.answer(
                f"{content_label} qo'shildi.\nKod foydalanuvchiga yuborildi."
            )
        except (TelegramBadRequest, TelegramForbiddenError):
            await message.answer(
                f"{content_label} qo'shildi.\nFoydalanuvchiga xabar yuborilmadi."
            )
    else:
        if content_kind != "serial":
            await message.answer(f"{content_label} qo'shildi.")

    if content_kind == "serial":
        next_episode_number = int(episode_number or 0) + 1
        if quick_serial_upload:
            await state.set_state(AddMovieState.waiting_for_video)
            await state.update_data(
                content_kind="serial",
                series_title=series_title or title,
                episode_number=next_episode_number,
                code=None,
                description="",
                serial_quick_upload=True,
            )
            await message.answer(
                (
                    f"✅ <b>{int(episode_number or 0)}-qism qo'shildi</b>\n\n"
                    f"🎞 <b>{_safe_html(series_title or title)}</b>\n"
                    f"📌 Keyingi qism: {next_episode_number}\n\n"
                    "<i>Navbatdagi video yuboring yoki yakunlang.</i>"
                ),
                reply_markup=serial_upload_finish_keyboard(),
                parse_mode="HTML",
            )
            return

        await _show_serial_continue_prompt(
            message,
            state,
            series_title or title,
            next_episode_number,
        )
        return

    await state.clear()


@router.message(AddMovieState.waiting_for_video)
async def receive_invalid_movie_video(
    message: types.Message, state: FSMContext
) -> None:
    if not await _ensure_message_access(message, permission=ADMIN_PERMISSION_MOVIES):
        return

    data = await state.get_data()
    content_kind = _content_kind_key(data.get("content_kind"))
    if content_kind == "serial":
        await message.answer(
            "⚠️ <b>Video yuboring</b>\n\n"
            "<i>Serial qismi uchun video kerak</i>",
            parse_mode="HTML",
        )
        return

    content_label = _content_kind_label(content_kind)
    await message.answer(f"{content_label} uchun video yuboring.")
