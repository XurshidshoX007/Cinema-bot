"""Request and helper admin text helpers."""

from aiogram import Bot, types
from aiogram.exceptions import TelegramBadRequest

from database import get_movie, get_serial_episodes, get_serial_group_for_lookup, search_movies_by_text, add_history, record_movie_view

from .content_utils import _content_kind_key, _content_kind_label, _content_kind_icon, _safe_html


def _request_text(request_id: int, user_id: int, text: str) -> str:
    return f"📌 ID: {request_id}\n👤 User ID: {user_id}\n📝 {text}"


def _request_added_text(code: str, content_kind: str = "movie") -> str:
    content_label = _content_kind_label(content_kind)
    return (
        f"Siz so'ragan {content_label.lower()} botga qo'shildi.\n"
        f"{content_label} kodi: {code}\n"
        "Uni 🔍 Qidiruv bo'limidan topishingiz mumkin."
    )


def _request_rejected_text() -> str:
    return (
        "Siz yuborgan kino yoki serial so'rovi admin tomonidan yopildi.\n"
        "Boshqa nom bilan qayta yuborib ko'rishingiz mumkin."
    )


def _request_existing_match_text(
    *,
    request_text: str,
    code: str,
    title: str,
    content_kind: str,
) -> str:
    content_label = _content_kind_label(content_kind)
    safe_request = _safe_html(request_text or "-")
    safe_title = _safe_html(title or "-")
    return (
        "🔎 <b>Mos kontent topildi</b>\n\n"
        f"📝 <b>So'rov:</b> {safe_request}\n"
        f"{_content_kind_icon(content_kind)} <b>{safe_title}</b>\n"
        f"<code>{code}</code> • {content_label}\n\n"
        "<i>Ushbu mavjud kontentni foydalanuvchiga yuboramizmi?</i>"
    )


async def _find_existing_content_for_request(
    request_text: str | None,
) -> tuple[str, str, str] | None:
    query = (request_text or "").strip()
    if len(query) < 2:
        return None

    matches = await search_movies_by_text(query, limit=8)
    if not matches:
        return None

    normalized_query = query.casefold()
    selected_code = ""
    selected_title = ""

    for code, title, _description, _file_id in matches:
        if code == query or title.casefold() == normalized_query:
            selected_code = code
            selected_title = title
            break

    if not selected_code:
        for code, title, _description, _file_id in matches:
            if normalized_query in title.casefold():
                selected_code = code
                selected_title = title
                break

    if not selected_code:
        selected_code, selected_title, _description, _file_id = matches[0]

    serial_group = await get_serial_group_for_lookup(selected_code)
    if serial_group is not None:
        group_code, group_title, group_description = serial_group
        return group_code, group_title, "serial"

    movie = await get_movie(selected_code)
    if movie is None:
        return None

    movie_title, _description, _file_id, content_kind = movie
    return selected_code, movie_title, _content_kind_key(content_kind)


async def _send_existing_content_to_user(
    bot: Bot,
    user_id: int,
    code: str,
) -> bool:
    serial_group = await get_serial_group_for_lookup(code)
    if serial_group is not None:
        group_code, group_title, group_description = serial_group
        episodes = await get_serial_episodes(group_code)
        if not episodes:
            return False

        first_code, first_episode_number, first_description, first_file_id = episodes[0]
        subtitle = (
            f"{first_episode_number}-qism yuborildi"
            if first_episode_number and first_episode_number > 0
            else "Birinchi qism yuborildi"
        )
        caption_lines = [
            f"📺 <b>{_safe_html(group_title)}</b>",
            "",
            f"<i>{subtitle}</i>",
            f"<code>{group_code}</code> • Serial",
        ]
        if first_description or group_description:
            caption_lines.extend(["", first_description or group_description])
        caption_lines.extend(
            ["", "Qolgan qismlar uchun shu kodni yuborishingiz mumkin."]
        )
        try:
            await bot.send_video(
                user_id,
                video=first_file_id,
                caption="\n".join(caption_lines),
                parse_mode="HTML",
            )
        except TelegramBadRequest:
            await bot.send_document(
                user_id,
                document=first_file_id,
                caption="\n".join(caption_lines),
                parse_mode="HTML",
            )
        await record_movie_view(first_code, viewer_user_id=user_id)
        await add_history(user_id, group_code)
        return True

    movie = await get_movie(code)
    if movie is None:
        return False

    title, description, file_id, content_kind = movie
    caption_lines = [
        f"{_content_kind_icon(content_kind)} <b>{_safe_html(title)}</b>",
        f"<i>{_content_kind_label(content_kind)}</i> • <code>{code}</code>",
    ]
    if description:
        caption_lines.extend(["", description])

    try:
        await bot.send_video(
            user_id,
            video=file_id,
            caption="\n".join(caption_lines),
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        await bot.send_document(
            user_id,
            document=file_id,
            caption="\n".join(caption_lines),
            parse_mode="HTML",
        )
    await record_movie_view(code, viewer_user_id=user_id)
    await add_history(user_id, code)
    return True
