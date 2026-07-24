"""Content and text helpers."""

import re
from html import escape
from aiogram import types

from .constants import VIDEO_DOCUMENT_EXTENSIONS, SEASON_TITLE_PATTERN


def _content_kind_key(content_kind: str | None) -> str:
    return "serial" if content_kind == "serial" else "movie"


def _content_kind_label(content_kind: str | None) -> str:
    return "Serial" if _content_kind_key(content_kind) == "serial" else "Kino"


def _content_kind_icon(content_kind: str | None) -> str:
    return "📺" if _content_kind_key(content_kind) == "serial" else "🎬"


def _is_video_document(document: types.Document | None) -> bool:
    if document is None:
        return False

    mime_type = (document.mime_type or "").strip().lower()
    if mime_type.startswith("video/"):
        return True

    file_name = (document.file_name or "").strip().lower()
    return file_name.endswith(VIDEO_DOCUMENT_EXTENSIONS)


def _extract_uploaded_video_file_id(message: types.Message) -> str | None:
    if message.video and message.video.file_id:
        return message.video.file_id

    if _is_video_document(message.document) and message.document and message.document.file_id:
        return message.document.file_id

    return None


def _command_argument(text: str | None) -> str:
    raw_text = (text or "").strip()
    if not raw_text:
        return ""

    parts = raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return ""

    return parts[1].strip()


def _content_list_filter_label(filter_key: str) -> str:
    if filter_key == "movie":
        return "🎬 Kinolar"
    if filter_key == "serial":
        return "📺 Seriallar"
    return "📚 Hammasi"


def _pick_available_filter(
    movie_items: list[tuple[str, str, str]],
    serial_items: list[tuple[str, str, str]],
    preferred_filter: str | None = None,
) -> str:
    if preferred_filter == "movie" and movie_items:
        return "movie"
    if preferred_filter == "serial" and serial_items:
        return "serial"
    if movie_items:
        return "movie"
    if serial_items:
        return "serial"
    return "movie"


def _normalized_lookup_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _match_serial_titles(titles: list[str], query: str) -> list[str]:
    normalized_query = _normalized_lookup_text(query)
    if not normalized_query:
        return []

    exact_matches = [
        title for title in titles if _normalized_lookup_text(title) == normalized_query
    ]
    if exact_matches:
        return exact_matches

    return [
        title for title in titles if normalized_query in _normalized_lookup_text(title)
    ]


def _safe_html(value: object) -> str:
    return escape(str(value))


def _serial_code_prompt_text(title: str, episode_number: int) -> str:
    return (
        f"🎞 <b>{_safe_html(title)}</b>\n"
        f"\n<i>{episode_number}-qism qo'shilmoqda</i>\n\n"
        "Kod yuboring."
    )


def _serial_description_prompt_text(title: str, episode_number: int) -> str:
    return (
        f"📝 <b>{_safe_html(title)}</b>\n"
        f"\n<i>{episode_number}-qism uchun tavsif yuboring</i>"
    )


def _serial_video_prompt_text(title: str, episode_number: int) -> str:
    return (
        f"🎬 <b>{_safe_html(title)}</b>\n"
        f"\n<i>{episode_number}-qism videosini yuboring</i>\n\n"
        "Kanaldan forward qilsangiz ham bo'ladi."
    )


def _next_season_title(series_title: str) -> str | None:
    match = SEASON_TITLE_PATTERN.match((series_title or "").strip())
    if not match:
        return None

    base_title = match.group("base").strip()
    season_number = int(match.group("season"))
    if not base_title or season_number <= 0:
        return None

    return f"{base_title} {season_number + 1}-fasl"


def _compact_text_preview(text: str | None, *, limit: int = 160) -> str:
    compact = " ".join((text or "").split())
    if not compact:
        return "Yo'q"
    if len(compact) > limit:
        return f"{compact[: limit - 3].rstrip()}..."
    return compact
