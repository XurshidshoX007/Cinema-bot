"""Serial and movie addition flows."""

import re
from aiogram import Bot, types
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest

from database import (
    get_next_serial_episode_number,
    get_serial_titles,
    create_ad_campaign,
)
from keyboards import (
    serial_continue_keyboard,
    serial_mode_keyboard,
    serial_season_choice_keyboard,
    serial_titles_keyboard,
    serial_upload_finish_keyboard,
)
from advertising import schedule_ad_broadcast

from .constants import (
    SERIAL_TITLES_PAGE_SIZE,
    AD_DURATION_UNIT_SECONDS,
    AD_DURATION_PATTERN,
    SEASON_TITLE_PATTERN,
    ADMIN_PERMISSION_MOVIES,
)
from .content_utils import (
    _content_kind_key,
    _content_kind_label,
    _safe_html,
    _normalized_lookup_text,
    _match_serial_titles,
    _next_season_title,
    _serial_video_prompt_text,
    _serial_description_prompt_text,
)
from .states import AddMovieState


def _ad_duration_limits_text() -> str:
    from database import MIN_AD_DURATION_SECONDS, MAX_AD_DURATION_SECONDS

    minimum_minutes = MIN_AD_DURATION_SECONDS // 60
    maximum_days = MAX_AD_DURATION_SECONDS // 86400
    return f"{minimum_minutes} daqiqadan {maximum_days} kungacha"


def _ad_duration_prompt() -> str:
    return (
        "Muddatni xabar qilib yozing.\n"
        "Masalan: 45m, 2h, 1d, 1d 6h, 30 daqiqa, 2 soat.\n"
        f"Ruxsat etilgan oraliq: {_ad_duration_limits_text()}."
    )


def _parse_custom_duration(text: str) -> int | None:
    from database import MIN_AD_DURATION_SECONDS, MAX_AD_DURATION_SECONDS

    cleaned = " ".join(text.casefold().replace(",", " ").split())
    if not cleaned:
        return None

    matches = list(AD_DURATION_PATTERN.finditer(cleaned))
    if not matches:
        return None

    remainder = AD_DURATION_PATTERN.sub(" ", cleaned)
    if remainder.strip():
        return None

    total_seconds = 0
    for match in matches:
        value = int(match.group(1))
        unit = match.group(2)
        multiplier = AD_DURATION_UNIT_SECONDS.get(unit)
        if multiplier is None or value <= 0:
            return None
        total_seconds += value * multiplier

    if not MIN_AD_DURATION_SECONDS <= total_seconds <= MAX_AD_DURATION_SECONDS:
        return None

    return total_seconds


def _extract_helper_admin_candidate(
    message: types.Message,
) -> tuple[int, str | None, str] | None:
    if message.text and message.text.strip().isdigit():
        user_id = int(message.text.strip())
        return user_id, None, f"User {user_id}"

    forwarded_user = getattr(message, "forward_from", None)
    if forwarded_user is not None:
        return (
            forwarded_user.id,
            forwarded_user.username,
            forwarded_user.full_name,
        )

    forward_origin = getattr(message, "forward_origin", None)
    sender_user = getattr(forward_origin, "sender_user", None)
    if sender_user is not None:
        return (
            sender_user.id,
            sender_user.username,
            sender_user.full_name,
        )

    return None


async def _launch_ad_campaign(
    *,
    bot: Bot,
    state: FSMContext,
    admin_id: int,
    duration_seconds: int,
) -> tuple[int, dict[str, str | None]]:
    data = await state.get_data()
    if not data:
        raise ValueError("Reklama ma'lumoti topilmadi")

    ad_id = await create_ad_campaign(
        admin_id=admin_id,
        content_type=data["content_type"],
        text=data.get("text"),
        file_id=data.get("file_id"),
        duration_seconds=duration_seconds,
    )
    await state.clear()
    schedule_ad_broadcast(bot, ad_id)
    return ad_id, data


async def _start_add_movie_flow(
    message: types.Message,
    state: FSMContext,
    *,
    content_kind: str = "movie",
    request_id: int | None = None,
    request_user_id: int | None = None,
    request_text: str | None = None,
    request_chat_id: int | None = None,
    request_message_id: int | None = None,
) -> None:
    await state.clear()
    normalized_kind = _content_kind_key(content_kind)
    content_label = _content_kind_label(normalized_kind)
    payload: dict[str, int | str] = {"content_kind": normalized_kind}
    prompt = f"{content_label} kodini yuboring."

    if request_id is not None and request_user_id is not None:
        payload.update(
            {
                "request_id": request_id,
                "request_user_id": request_user_id,
            }
        )

        if request_text:
            payload["request_text"] = request_text

        if request_chat_id is not None:
            payload["request_chat_id"] = request_chat_id

        if request_message_id is not None:
            payload["request_message_id"] = request_message_id

        prompt = (
            f"So'rov uchun {content_label.lower()} qo'shish boshlandi.\n"
            f"So'rov ID: {request_id}\n"
            f"So'rov matni: {request_text or '-'}\n\n"
            f"Endi {content_label.lower()} kodini yuboring."
        )

    await state.set_state(AddMovieState.waiting_for_code)
    if payload:
        await state.update_data(**payload)
    await message.answer(prompt)


async def _show_serial_mode_picker(
    message: types.Message,
    state: FSMContext,
    *,
    edit: bool = False,
) -> None:
    await state.clear()
    has_existing_serials = bool(await get_serial_titles())
    text = "📺 Serial qo'shish turini tanlang."
    keyboard = serial_mode_keyboard(has_existing_serials=has_existing_serials)

    if edit:
        await message.edit_text(text, reply_markup=keyboard)
        return

    await message.answer(text, reply_markup=keyboard)


async def _show_serial_titles_picker(
    message: types.Message,
    state: FSMContext,
    *,
    page: int = 0,
    titles: list[str] | None = None,
    subtitle: str | None = None,
    edit: bool = False,
) -> None:
    titles = titles or await get_serial_titles()
    if not titles:
        await _show_serial_mode_picker(message, state, edit=edit)
        return

    await state.clear()
    await state.set_state(AddMovieState.waiting_for_serial_pick)
    await state.update_data(serial_titles=titles, serial_page=page)

    text = "📺 Davomini qo'shmoqchi bo'lgan serialni tanlang yoki nomini yozing."
    if subtitle:
        text = subtitle
    keyboard = serial_titles_keyboard(
        titles,
        page=page,
        page_size=SERIAL_TITLES_PAGE_SIZE,
    )

    if edit:
        await message.edit_text(text, reply_markup=keyboard)
        return

    await message.answer(text, reply_markup=keyboard)


async def _start_serial_continuation_flow(
    message: types.Message,
    state: FSMContext,
    title: str,
    *,
    episode_number: int | None = None,
    edit: bool = False,
) -> None:
    if episode_number is None:
        next_season_title = _next_season_title(title)
        if next_season_title:
            await state.clear()
            await state.set_state(AddMovieState.waiting_for_serial_pick)
            await state.update_data(
                serial_pick_title=title,
                serial_pick_next_season=next_season_title,
            )

            text = (
                f"Serial tanlandi: {title}\n\n"
                "Qaysi rejimni tanlaysiz?\n"
                "• Shu faslni davom ettirish\n"
                f"• {next_season_title} ni 1-qismdan boshlash"
            )

            if edit:
                await message.edit_text(
                    text,
                    reply_markup=serial_season_choice_keyboard(next_season_title),
                )
                return

            await message.answer(
                text,
                reply_markup=serial_season_choice_keyboard(next_season_title),
            )
            return

    next_episode_number = episode_number or await get_next_serial_episode_number(title)
    await state.clear()
    await state.set_state(AddMovieState.waiting_for_code)
    await state.update_data(
        content_kind="serial",
        series_title=title,
        episode_number=next_episode_number,
    )

    text = (
        f"📺 Tanlangan serial: {title}\n"
        f"Qism: {next_episode_number}-qism\n"
        "Endi yangi qism kodini yuboring."
    )

    if edit:
        await message.edit_text(text, reply_markup=None)
        return

    await message.answer(text)


async def _show_serial_continue_prompt(
    message: types.Message,
    state: FSMContext,
    title: str,
    next_episode_number: int,
) -> None:
    await state.clear()
    await state.set_state(AddMovieState.waiting_for_serial_continue)
    await state.update_data(
        content_kind="serial",
        series_title=title,
        episode_number=next_episode_number,
    )
    await message.answer(
        f"📺 {title} uchun qism qo'shildi.\nKeyingi qism: {next_episode_number}-qism\nYana shu serialga qism qo'shasizmi?",
        reply_markup=serial_continue_keyboard(),
    )
