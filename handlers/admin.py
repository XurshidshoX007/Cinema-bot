import logging
import re
from datetime import UTC, datetime
from html import escape

logger = logging.getLogger(__name__)

from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from advertising import extract_ad_payload, schedule_ad_broadcast
from config import ADMIN_ID, STATS_WEBAPP_URL
from dashboard import render_dashboard
from database import (
    APP_TIMEZONE_LABEL,
    MAX_AD_DURATION_SECONDS,
    MIN_AD_DURATION_SECONDS,
    TIMESTAMP_FORMAT,
    add_history,
    add_movie,
    add_movie_auto_code,
    create_ad_campaign,
    delete_movie,
    get_admin_permissions,
    get_ad_campaign,
    get_ads_by_status,
    get_all_movies,
    get_dashboard_summary,
    get_daily_metric_series,
    get_helper_admin,
    get_movie,
    get_pending_requests,
    get_next_serial_episode_number,
    get_recent_ads,
    get_recent_users,
    get_request,
    get_request_status_counts,
    get_serial_episodes,
    get_serial_group_for_lookup,
    get_serial_titles,
    search_movies_by_text,
    get_top_viewed_movies,
    get_user_snapshot,
    is_admin_user,
    is_content_code_taken,
    list_helper_admins,
    remove_helper_admin,
    request_stop_ad,
    record_movie_view,
    set_helper_admin_permission,
    touch_user,
    upsert_helper_admin,
    update_request_status,
    get_sponsor_channels,
    add_sponsor_channel,
    format_local_timestamp,
    remove_sponsor_channel,
    is_favorite,
    local_now_text,
    update_movie_description,
    update_movie_file_id,
    update_movie_title,
)
from services.legacy_media import (
    legacy_media_enabled,
    renew_file_id_from_legacy,
)
from keyboards import (
    ADMIN_ACTIONS,
    ADMIN_PANEL_BUTTON,
    ADS_BUTTON,
    BACK_TO_ADMIN_BUTTON,
    BACK_TO_MAIN_BUTTON,
    DELETE_MOVIE_BUTTON,
    EDIT_MOVIE_BUTTON,
    LEGACY_NEW_MOVIE_BUTTON,
    HELPER_ADMINS_BUTTON,
    MOVIES_LIST_BUTTON,
    MOVIE_MANAGEMENT_BUTTON,
    NEW_MOVIE_BUTTON,
    NEW_SERIAL_BUTTON,
    REQUESTS_BUTTON,
    STATS_BUTTON,
    USER_ACTIONS,
    ad_duration_keyboard,
    admin_menu,
    ads_panel_keyboard,
    content_list_keyboard,
    delete_confirm_keyboard,
    delete_movie_keyboard,
    edit_movie_action_keyboard,
    helper_admin_detail_keyboard,
    helper_admins_keyboard,
    main_menu,
    movie_menu,
    request_review_keyboard,
    request_existing_match_keyboard,
    serial_hub_keyboard,
    serial_continue_keyboard,
    serial_mode_keyboard,
    serial_season_choice_keyboard,
    serial_upload_finish_keyboard,
    serial_titles_keyboard,
    stats_dashboard_keyboard,
    stats_webapp_keyboard,
    CHANNELS_BUTTON,
)

from .admin_states import (
    AddMovieState,
    AdState,
    DeleteMovieState,
    EditMovieState,
    HelperAdminState,
    RefreshMediaState,
)
from services.stats_webapp_auth import build_signed_stats_webapp_url

router = Router()
SPARKLINE_BARS = "▁▂▃▄▅▆▇█"
STATS_PANELS = {"overview", "traffic", "movies", "requests"}
ACTIVE_AD_STATUSES = (
    "broadcasting",
    "stop_requested",
    "active",
    "stopping",
    "cleaning",
)
SERIAL_TITLES_PAGE_SIZE = 8
CONTENT_LIST_PAGE_SIZE = 8
CONTENT_LIST_PREVIEW_SIZE = 5
DELETE_PANEL_PAGE_SIZE = 8
ADMIN_PERMISSION_MOVIES = "movies"
ADMIN_PERMISSION_REQUESTS = "requests"
ADMIN_PERMISSION_STATS = "stats"
ADMIN_PERMISSION_ADS = "ads"
ADMIN_PERMISSION_HELPERS = "helpers"
LEGACY_STATS_BUTTON = "📊 Statistika"
AD_DURATION_UNIT_SECONDS = {
    "m": 60,
    "min": 60,
    "mins": 60,
    "minute": 60,
    "minutes": 60,
    "minut": 60,
    "minuta": 60,
    "daq": 60,
    "daqiqa": 60,
    "h": 3600,
    "hr": 3600,
    "hrs": 3600,
    "hour": 3600,
    "hours": 3600,
    "soat": 3600,
    "d": 86400,
    "day": 86400,
    "days": 86400,
    "kun": 86400,
}
AD_DURATION_PATTERN = re.compile(r"(\d+)\s*([a-z]+)", re.IGNORECASE)
SEASON_TITLE_PATTERN = re.compile(
    r"^\s*(?P<base>.+?)\s+(?P<season>\d+)\s*-\s*fasl\s*$",
    re.IGNORECASE,
)


class AdminChannelState(StatesGroup):
    waiting_for_channel = State()


def _is_owner(user_id: int) -> bool:
    return user_id == ADMIN_ID


async def _admin_permissions(user_id: int) -> set[str]:
    return await get_admin_permissions(user_id)


async def _is_admin(user_id: int) -> bool:
    return await is_admin_user(user_id)


async def _has_permission(user_id: int, permission: str) -> bool:
    return permission in await _admin_permissions(user_id)


async def _ensure_message_access(
    message: types.Message,
    *,
    permission: str | None = None,
    owner_only: bool = False,
) -> bool:
    if message.from_user is None:
        return False
    user_id = message.from_user.id
    if owner_only:
        return _is_owner(user_id)
    if permission is None:
        return await _is_admin(user_id)
    return await _has_permission(user_id, permission)


async def _ensure_callback_access(
    callback: types.CallbackQuery,
    *,
    permission: str | None = None,
    owner_only: bool = False,
) -> bool:
    if callback.from_user is None:
        return False
    user_id = callback.from_user.id
    if owner_only:
        return _is_owner(user_id)
    if permission is None:
        return await _is_admin(user_id)
    return await _has_permission(user_id, permission)


async def _admin_menu_markup(user_id: int) -> types.ReplyKeyboardMarkup:
    permissions = await _admin_permissions(user_id)
    return admin_menu(permissions=permissions, is_owner=_is_owner(user_id))


def _request_text(request_id: int, user_id: int, text: str) -> str:
    return f"📌 ID: {request_id}\n👤 User ID: {user_id}\n📝 {text}"


def _content_kind_key(content_kind: str | None) -> str:
    return "serial" if content_kind == "serial" else "movie"


def _content_kind_label(content_kind: str | None) -> str:
    return "Serial" if _content_kind_key(content_kind) == "serial" else "Kino"


def _content_kind_icon(content_kind: str | None) -> str:
    return "📺" if _content_kind_key(content_kind) == "serial" else "🎬"


VIDEO_DOCUMENT_EXTENSIONS = (
    ".mp4",
    ".mkv",
    ".avi",
    ".mov",
    ".webm",
    ".m4v",
    ".mpeg",
    ".mpg",
)


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


async def _refresh_edit_movie_state(
    state: FSMContext,
    code: str,
) -> tuple[str, str, str, str] | None:
    movie = await get_movie(code)
    if movie is None:
        return None

    title, description, file_id, content_kind = movie
    serial_title = ""
    if _content_kind_key(content_kind) == "serial":
        serial_group = await get_serial_group_for_lookup(code)
        if serial_group is not None:
            serial_title = serial_group[1]

    await state.update_data(
        edit_movie_code=code,
        edit_movie_title=title,
        edit_movie_description=description,
        edit_movie_kind=content_kind,
        edit_movie_serial_title=serial_title,
        edit_movie_has_file=bool(file_id),
    )
    return movie


def _build_edit_movie_panel_text(data: dict[str, object]) -> str:
    code = str(data.get("edit_movie_code") or "").strip()
    content_kind = _content_kind_key(str(data.get("edit_movie_kind") or "movie"))
    title = str(data.get("edit_movie_title") or "Kontent").strip() or "Kontent"
    serial_title = str(data.get("edit_movie_serial_title") or "").strip()
    description = str(data.get("edit_movie_description") or "")
    lines = [
        "✏️ <b>Kontent tahrirlash</b>",
        "",
        f"📌 Kod: <code>{_safe_html(code)}</code>",
        f"{_content_kind_icon(content_kind)} Turi: <b>{_safe_html(_content_kind_label(content_kind))}</b>",
    ]

    if content_kind == "serial" and serial_title:
        lines.append(f"📺 Serial: <b>{_safe_html(serial_title)}</b>")
        lines.append(f"🎞 Qism: <b>{_safe_html(title)}</b>")
    else:
        lines.append(f"🎬 Nomi: <b>{_safe_html(title)}</b>")

    lines.append(f"📄 Tavsif: {_safe_html(_compact_text_preview(description, limit=220))}")

    if content_kind == "serial":
        lines.extend(
            [
                "",
                "<i>Serial nomi yangilansa shu guruhdagi qismlar birga yangilanadi.</i>",
            ]
        )

    lines.extend(["", "Kerakli maydonni pastdagi tugmalardan tanlang."])
    return "\n".join(lines)


async def _show_edit_movie_panel(
    message: types.Message,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    content_kind = _content_kind_key(str(data.get("edit_movie_kind") or "movie"))
    await state.set_state(EditMovieState.waiting_for_action)
    await message.answer(
        _build_edit_movie_panel_text(data),
        reply_markup=edit_movie_action_keyboard(content_kind=content_kind),
        parse_mode="HTML",
    )


async def _select_edit_movie_target(
    message: types.Message,
    state: FSMContext,
    raw_code: str,
) -> bool:
    code = raw_code.strip()
    if not code:
        await message.answer("Tahrirlash uchun kod yuboring.")
        return False

    movie = await _refresh_edit_movie_state(state, code)
    if movie is None:
        serial_group = await get_serial_group_for_lookup(code)
        if serial_group is not None:
            await message.answer(
                "Bu serialning umumiy kodi.\nTahrirlash uchun qism kodini yuboring."
            )
        else:
            await message.answer("Bunday kodli kontent topilmadi.")
        return False

    await _show_edit_movie_panel(message, state)
    return True


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


def _helper_admin_label(helper_admin: dict[str, object]) -> str:
    username = helper_admin.get("username")
    if username:
        return f"@{username}"
    return str(helper_admin.get("full_name") or f"Admin {helper_admin['user_id']}")


def _helper_admin_permission_label(permission: str) -> str:
    return {
        ADMIN_PERMISSION_MOVIES: "🎬 Kinolar",
        ADMIN_PERMISSION_REQUESTS: "📨 So'rovlar",
        ADMIN_PERMISSION_STATS: "📊 Statistika",
        ADMIN_PERMISSION_ADS: "📣 Reklama",
    }.get(permission, permission)


def _render_helper_admins_panel(helper_admins: list[dict[str, object]]) -> str:
    lines = [
        "👥 Yordamchi adminlar",
        "",
        f"Jami: {len(helper_admins)} ta",
    ]

    if not helper_admins:
        lines.extend(["", "Hali yordamchi admin qo'shilmagan."])
        return "\n".join(lines)

    lines.append("")
    for index, helper_admin in enumerate(helper_admins, start=1):
        permissions = helper_admin["permissions"]
        enabled = [
            _helper_admin_permission_label(permission)
            for permission, is_enabled in permissions.items()
            if is_enabled
        ]
        summary = ", ".join(enabled) if enabled else "Ruxsat berilmagan"
        lines.append(
            f"{index}. {_helper_admin_label(helper_admin)} ({helper_admin['user_id']})"
        )
        lines.append(f"   └ {summary}")

    return "\n".join(lines)


def _render_helper_admin_detail(helper_admin: dict[str, object]) -> str:
    permissions = helper_admin["permissions"]
    enabled = [
        _helper_admin_permission_label(permission)
        for permission, is_enabled in permissions.items()
        if is_enabled
    ]
    permission_text = ", ".join(enabled) if enabled else "Hali ruxsat berilmagan"
    username = helper_admin.get("username")

    lines = [
        "👤 Yordamchi admin",
        "",
        f"ID: {helper_admin['user_id']}",
        f"Ism: {helper_admin.get('full_name') or '-'}",
        f"Username: @{username}" if username else "Username: -",
        f"Ruxsatlar: {permission_text}",
    ]
    return "\n".join(lines)


def _render_content_overview_section(
    title: str,
    items: list[tuple[str, str, str]],
    *,
    empty_text: str,
) -> str:
    lines = [title]
    if not items:
        lines.append(empty_text)
        return "\n".join(lines)

    for index, (code, item_title, _content_kind) in enumerate(
        items[:CONTENT_LIST_PREVIEW_SIZE],
        start=1,
    ):
        lines.append(f"{index}. {item_title}")
        lines.append(f"   └ Kod: {code}")

    remaining = len(items) - CONTENT_LIST_PREVIEW_SIZE
    if remaining > 0:
        lines.append(f"   ... yana {remaining} ta kontent bor")

    return "\n".join(lines)


def _render_content_section_page(
    items: list[tuple[str, str, str]],
    *,
    filter_key: str,
    page: int,
) -> tuple[str, int, int]:
    total_items = len(items)
    total_pages = max(
        1, (total_items + CONTENT_LIST_PAGE_SIZE - 1) // CONTENT_LIST_PAGE_SIZE
    )
    page = min(max(page, 0), total_pages - 1)
    start = page * CONTENT_LIST_PAGE_SIZE
    end = min(total_items, start + CONTENT_LIST_PAGE_SIZE)
    page_items = items[start:end]

    lines = [
        "📚 Kontent ro'yxati",
        f"{_content_list_filter_label(filter_key)} bo'limi",
        "",
        f"Jami: {total_items} ta",
    ]

    if total_items:
        lines.extend(
            [
                f"Sahifa: {page + 1}/{total_pages}",
                f"Ko'rsatilmoqda: {start + 1}-{end}",
                "",
            ]
        )
        for absolute_index, (code, title, _content_kind) in enumerate(
            page_items, start=start + 1
        ):
            lines.append(f"{absolute_index}. {title}")
            lines.append(f"   └ Kod: {code}")
    else:
        lines.extend(["", "Bu bo'limda hali kontent yo'q."])

    return "\n".join(lines), total_pages, page


def _render_content_list_overview(
    movie_items: list[tuple[str, str, str]],
    serial_items: list[tuple[str, str, str]],
) -> str:
    total_count = len(movie_items) + len(serial_items)
    sections = [
        "📚 Kontent ro'yxati",
        "",
        f"📦 Jami: {total_count} ta",
        f"🎬 Kinolar: {len(movie_items)} ta",
        f"📺 Seriallar: {len(serial_items)} ta",
        "",
        _render_content_overview_section(
            "🎬 So'nggi kinolar",
            movie_items,
            empty_text="Hali kino qo'shilmagan",
        ),
        "",
        _render_content_overview_section(
            "📺 So'nggi seriallar",
            serial_items,
            empty_text="Hali serial qo'shilmagan",
        ),
        "",
        "Kerakli bo'limni pastdagi tugmalardan tanlang.",
    ]
    return "\n".join(sections)


def _render_content_picker_text(
    *,
    title: str,
    movie_count: int,
    serial_count: int,
    empty_text: str,
    hint_text: str,
) -> str:
    total_count = movie_count + serial_count
    if total_count == 0:
        return empty_text

    return "\n".join(
        [
            title,
            "",
            f"📦 Jami: {total_count} ta",
            f"🎬 Kinolar: {movie_count} ta",
            f"📺 Seriallar: {serial_count} ta",
            "",
            hint_text,
        ]
    )


def _render_delete_panel_text(
    *,
    filter_key: str,
    total_items: int,
    page: int,
    total_pages: int,
    page_items: list[tuple[str, str, str]],
    start_index: int,
) -> str:
    lines = [
        "🗑 Kontentni o'chirish",
        f"{_content_list_filter_label(filter_key)} bo'limi",
        "",
        f"Jami: {total_items} ta",
    ]

    if total_items:
        lines.extend(
            [
                f"Sahifa: {page + 1}/{total_pages}",
                "",
                "O'chirish uchun kodni yuboring.",
                "",
            ]
        )
        for index, (code, title, content_kind) in enumerate(
            page_items, start=start_index + 1
        ):
            lines.append(f"{index}. {_content_kind_icon(content_kind)} {title}")
            lines.append(f"   └ Kod: {code}")
    else:
        lines.extend(["", "Bu bo'limda o'chirish uchun kontent yo'q."])

    return "\n".join(lines)


def _compact_number(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def _sparkline(values: list[int]) -> str:
    if not values:
        return ""

    maximum = max(values)
    if maximum <= 0:
        return "·" * len(values)

    last_index = len(SPARKLINE_BARS) - 1
    return "".join(
        SPARKLINE_BARS[max(0, round((value / maximum) * last_index))]
        for value in values
    )


def _bar_chart(label: str, value: int, maximum: int, width: int = 10) -> str:
    if maximum <= 0 or value <= 0:
        filled = 0
    else:
        filled = max(1, round((value / maximum) * width))

    filled = min(width, filled)
    empty = width - filled
    return f"{label:<12} {'█' * filled}{'░' * empty} {value}"


def _format_recent_users(users: list[tuple[int, str | None, str, str]]) -> str:
    if not users:
        return "Hali ma'lumot yo'q"

    lines = []
    for user_id, username, full_name, last_seen in users:
        label = f"@{username}" if username else full_name
        if len(label) > 24:
            label = f"{label[:21]}..."
        seen_time = format_local_timestamp(last_seen, "%H:%M")
        lines.append(f"• {label} ({user_id}) - {seen_time} {APP_TIMEZONE_LABEL}")

    return "\n".join(lines)


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None

    try:
        return datetime.strptime(value, TIMESTAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def _format_duration(seconds: int) -> str:
    if seconds % 86400 == 0:
        days = seconds // 86400
        return f"{days} kun"
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours} soat"
    minutes = max(1, seconds // 60)
    return f"{minutes} daqiqa"


def _format_time_left(expires_at: str | None) -> str:
    expires = _parse_timestamp(expires_at)
    if expires is None:
        return "Noma'lum"

    remaining = int((expires - datetime.now(UTC)).total_seconds())
    if remaining <= 0:
        return "Yakunlanmoqda"

    days, rest = divmod(remaining, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, _ = divmod(rest, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days} kun")
    if hours:
        parts.append(f"{hours} soat")
    if minutes and not days:
        parts.append(f"{minutes} daqiqa")

    return " ".join(parts[:2]) or "1 daqiqadan kam"


def _ad_status_label(status: str) -> str:
    return {
        "broadcasting": "Yuborilmoqda",
        "stop_requested": "To'xtatilmoqda",
        "active": "Faol",
        "stopping": "To'xtatilmoqda",
        "cleaning": "O'chirilmoqda",
        "expired": "Muddat tugagan",
        "stopped": "Qo'lda to'xtatilgan",
    }.get(status, status)


def _ad_type_label(content_type: str) -> str:
    return {
        "text": "Matn",
        "photo": "Rasm",
        "video": "Video",
        "document": "Fayl",
    }.get(content_type, content_type)


def _ad_preview(content_type: str, text: str | None, limit: int = 42) -> str:
    compact = " ".join((text or "").split())
    if not compact:
        return _ad_type_label(content_type)
    if len(compact) > limit:
        compact = f"{compact[: limit - 3]}..."
    return f"{_ad_type_label(content_type)}: {compact}"


def _ads_button_rows(active_ads: list[dict]) -> list[tuple[int, str]]:
    rows: list[tuple[int, str]] = []
    for ad in active_ads:
        if ad["status"] not in {"broadcasting", "active"}:
            continue
        rows.append((ad["id"], f"{_ad_type_label(ad['content_type'])}"))
    return rows


def _build_ads_panel_text(active_ads: list[dict], recent_ads: list[dict]) -> str:
    lines = [
        "📣 Reklama Markazi",
        "",
        "Admin shu bo'limdan bitta xabarni barcha foydalanuvchilarga yuboradi.",
        "Reklama muddati tugaganda bot uni avtomatik o'chirishga harakat qiladi.",
        "",
        f"Faol kampaniyalar: {len(active_ads)}",
    ]

    if active_ads:
        for ad in active_ads:
            lines.extend(
                [
                    f"• #{ad['id']} {_ad_status_label(ad['status'])}",
                    f"  {_ad_preview(ad['content_type'], ad['text'])}",
                    f"  Yuborildi: {ad['delivered_total']}/{ad['recipient_total']} | Xato: {ad['failed_total']}",
                    f"  Qolgan vaqt: {_format_time_left(ad['expires_at'])}",
                ]
            )
    else:
        lines.append("• Hozircha faol reklama yo'q")

    lines.extend(["", "Oxirgi yakunlanganlar:"])
    if recent_ads:
        for ad in recent_ads:
            lines.append(
                f"• #{ad['id']} {_ad_status_label(ad['status'])} | "
                f"{ad['deleted_total']}/{ad['delivered_total']} o'chirilgan"
            )
    else:
        lines.append("• Hali yakunlangan reklama yo'q")

    lines.extend(
        [
            "",
            "Qo'llab-quvvatlanadi: matn, rasm, video, fayl.",
            "Reklama muddati qo'lda yoziladi: 45m, 2h, 1d, 1d 6h.",
            f"Ruxsat etilgan oraliq: {_ad_duration_limits_text()}.",
        ]
    )
    return "\n".join(lines)


async def _build_ads_panel_view() -> tuple[str, types.InlineKeyboardMarkup]:
    active_ads = await get_ads_by_status(ACTIVE_AD_STATUSES, limit=6)
    recent_ads = await get_recent_ads(limit=4)
    text = _build_ads_panel_text(active_ads, recent_ads)
    keyboard = ads_panel_keyboard(_ads_button_rows(active_ads))
    return text, keyboard


async def _refresh_saved_ads_panel(
    bot: Bot,
    panel_chat_id: int | str | None,
    panel_message_id: int | str | None,
) -> None:
    if panel_chat_id is None or panel_message_id is None:
        return

    try:
        text, keyboard = await _build_ads_panel_view()
        await bot.edit_message_text(
            text,
            chat_id=int(panel_chat_id),
            message_id=int(panel_message_id),
            reply_markup=keyboard,
        )
    except (TelegramBadRequest, TelegramForbiddenError, ValueError):
        return


async def _load_dashboard_payload(panel: str) -> dict:
    panel = panel if panel in STATS_PANELS else "overview"
    return {
        "summary": await get_dashboard_summary(),
        "trends": await get_daily_metric_series(days=7),
        "request_counts": await get_request_status_counts(),
        "top_movies": await get_top_viewed_movies(limit=5),
        "recent_users": await get_recent_users(limit=6),
    }


def _build_dashboard_caption(panel: str, payload: dict) -> str:
    panel = panel if panel in STATS_PANELS else "overview"
    summary = payload["summary"]
    trends = payload["trends"]
    updated_at = f"{local_now_text()} {APP_TIMEZONE_LABEL}"
    known_request_total = (
        summary["pending_requests"]
        + summary["completed_requests"]
        + summary["rejected_requests"]
    )
    other_requests = max(0, summary["total_requests"] - known_request_total)
    other_requests_line = (
        f"• Boshqa status: {other_requests}\n" if other_requests else ""
    )

    if panel == "overview":
        return (
            "📊 Statistika Dashboard\n"
            f"Yangilandi: {updated_at}\n\n"
            "👥 Foydalanuvchilar\n"
            f"• Faol obunachilar: {summary['total_users']} (bloklamaganlar)\n"
            f"• Jami kirganlar: {summary['all_time_users']} (admin/helperlarsiz)\n"
            f"• Bugun kirganlar: {summary['entered_today']} (bugungi oxirgi faollik)\n"
            f"• Bugun yangi obunachilar: {summary['new_subscribers_today']}\n"
            f"• Bloklaganlar: {summary['blocked_users']}\n"
            f"• 24 soat faol: {summary['active_today']} (bloklamaganlar)\n"
            f"• 7 kun faol: {summary['active_week']}\n\n"
            "🎬 Kontent\n"
            f"• Kinolar: {summary['total_movies']}\n"
            f"• Ko'rishlar: {summary['total_views']}\n"
            f"• Sevimlilar: {summary['total_favorites']}\n\n"
            "📥 So'rovlar\n"
            f"• Jami: {summary['total_requests']}\n"
            f"• Ochiq: {summary['pending_requests']}\n"
            f"• Bajarilgan: {summary['completed_requests']}\n"
            f"• Rad etilgan: {summary['rejected_requests']}\n"
            f"{other_requests_line}\n"
            "📈 7 kunlik trend\n"
            f"• So'rovlar: {_sparkline(trends['requests'])}\n"
            f"• Ko'rishlar: {_sparkline(trends['movie_views'])}\n"
            f"• Yangi users: {_sparkline(trends['new_users'])}\n\n"
            "🕘 So'nggi faollar\n"
            f"{_format_recent_users(payload['recent_users'])}"
        )

    if panel == "traffic":
        labels = " ".join(label[8:] for label in trends["labels"])
        return (
            "📈 Trafik Dashboard\n"
            f"Yangilandi: {updated_at}\n\n"
            f"Kunlar: {labels}\n\n"
            f"So'rovlar   {_sparkline(trends['requests'])}   {' '.join(_compact_number(v) for v in trends['requests'])}\n"
            f"Ko'rishlar  {_sparkline(trends['movie_views'])}   {' '.join(_compact_number(v) for v in trends['movie_views'])}\n"
            f"Yangi user  {_sparkline(trends['new_users'])}   {' '.join(_compact_number(v) for v in trends['new_users'])}\n\n"
            "Qisqa xulosa\n"
            f"• 7 kunda ko'rishlar: {sum(trends['movie_views'])}\n"
            f"• 7 kunda so'rovlar: {sum(trends['requests'])}\n"
            f"• 7 kunda yangi users: {sum(trends['new_users'])}"
        )

    if panel == "movies":
        top_movies = payload["top_movies"]
        if not top_movies:
            ranking = "Ko'rishlar hali yo'q"
        else:
            max_views = max(views for _, _, views, _ in top_movies)
            views_label = "Ko'rish"
            ranking = "\n".join(
                f"{index}. {title} ({code})\n"
                f"{_bar_chart(views_label, views, max_views)} • Unique user: {unique_views}"
                for index, (code, title, views, unique_views) in enumerate(
                    top_movies,
                    start=1,
                )
            )

        return (
            "🎬 Top Kinolar Dashboard\n"
            f"Yangilandi: {updated_at}\n\n"
            f"• Jami kinolar: {summary['total_movies']}\n"
            f"• Jami ko'rishlar: {summary['total_views']}\n"
            f"• Jami sevimlilar: {summary['total_favorites']}\n\n"
            "🏆 Eng ko'p ko'rilgan kinolar\n"
            f"{ranking}"
        )

    request_counts = payload["request_counts"]
    maximum = max(request_counts.values()) if request_counts else 0
    other_status_line = (
        f"{_bar_chart('Boshqa', request_counts.get('other', 0), maximum)}\n"
        if request_counts.get("other", 0)
        else ""
    )
    other_status_summary = (
        f"\n• Boshqa status jami: {request_counts.get('other', 0)}"
        if request_counts.get("other", 0)
        else ""
    )
    return (
        "📥 So'rovlar Dashboard\n"
        f"Yangilandi: {updated_at}\n\n"
        f"{_bar_chart('Yangi', request_counts.get('pending', 0), maximum)}\n"
        f"{_bar_chart('Jarayonda', request_counts.get('accepted', 0), maximum)}\n"
        f"{_bar_chart('Bajarildi', request_counts.get('completed', 0), maximum)}\n"
        f"{_bar_chart('Rad etildi', request_counts.get('rejected', 0), maximum)}\n"
        f"{other_status_line}\n"
        "Qisqa xulosa\n"
        f"• Ochiq navbat: {summary['pending_requests']}\n"
        f"• Bajarilgan jami: {summary['completed_requests']}\n"
        f"• Rad etilgan jami: {summary['rejected_requests']}"
        f"{other_status_summary}"
    )


async def _show_stats_dashboard(
    message: types.Message,
    *,
    panel: str,
    edit: bool = False,
) -> None:
    panel = panel if panel in STATS_PANELS else "overview"
    payload = await _load_dashboard_payload(panel)
    text = _build_dashboard_caption(panel, payload)
    image_bytes = render_dashboard(panel, payload)
    keyboard = stats_dashboard_keyboard(panel)
    dashboard_file = types.BufferedInputFile(
        image_bytes, filename=f"dashboard_{panel}.png"
    )

    if edit:
        if message.photo or message.document:
            media = types.InputMediaDocument(media=dashboard_file, caption=text)
            try:
                await message.edit_media(media=media, reply_markup=keyboard)
                return
            except TelegramBadRequest as error:
                message_text = str(error).lower()
                if "message is not modified" in message_text:
                    return
                await message.answer_document(
                    document=dashboard_file, caption=text, reply_markup=keyboard
                )
                return

        try:
            await message.edit_text(text, reply_markup=keyboard)
        except TelegramBadRequest as error:
            if "message is not modified" not in str(error).lower():
                raise
        return

    await message.answer_document(
        document=dashboard_file, caption=text, reply_markup=keyboard
    )


def _build_stats_insights(summary: dict[str, int]) -> str:
    recommendations = []
    if summary["pending_requests"] >= 10:
        recommendations.append(
            "🔔 Ko'p kutayotgan so'rovlar bor — so'rovlar bo'limini tekshiring."
        )
    if summary["entered_today"] <= max(10, summary["total_users"] // 20):
        recommendations.append(
            "📣 Bugungi faol foydalanuvchilar soni past — foydalanuvchi rag'batlantirish kampaniyasini ko'rib chiqing."
        )
    if summary["total_views"] <= 100:
        recommendations.append(
            "🎬 Ko'rishlar kam — kontentni ommalashtirish va yangi filmlar qo'shish foydali bo'ladi."
        )
    if summary["rejected_requests"] >= summary["total_requests"] * 0.3:
        recommendations.append(
            "⚠️ So'rovlarning katta qismi rad etilmoqda — qoidalarni va moderatsiyani qayta ko'rib chiqing."
        )
    if not recommendations:
        recommendations.append(
            "✅ Hozircha statistika barqaror. Keyingi bosqich uchun reklama va kontent yangilanishini kuzatib boring."
        )

    return "\n".join(recommendations)


async def _show_stats_webapp(message: types.Message) -> None:
    summary = await get_dashboard_summary()
    text = (
        "🌟 Premium Statistika\n"
        f"• Faol obunachilar: {summary['total_users']} (bloklamaganlar)\n"
        f"• Jami kirganlar: {summary['all_time_users']} (admin/helperlarsiz)\n"
        f"• Bugun kirganlar: {summary['entered_today']} (bugungi oxirgi faollik)\n"
        f"• 24 soat faol: {summary['active_today']} (bloklamaganlar)\n"
        f"• 7 kun davomida faol: {summary['active_week']}\n"
        f"• Jami kinolar: {summary['total_movies']}\n"
        f"• Jami ko'rishlar: {summary['total_views']}\n"
        f"• Jami so'rovlar: {summary['total_requests']}\n"
        f"• Kutayotgan/aktual so'rovlar: {summary['pending_requests']}\n"
        f"• Bajarilgan so'rovlar: {summary['completed_requests']}\n"
        f"• Rad etilgan so'rovlar: {summary['rejected_requests']}\n\n"
        "📌 Quyidagi tavsiyalar boshqaruv uchun foydali bo'lishi mumkin:\n"
        f"{_build_stats_insights(summary)}"
    )

    if STATS_WEBAPP_URL:
        user_id = message.from_user.id if message.from_user is not None else 0
        keyboard = stats_webapp_keyboard(
            build_signed_stats_webapp_url(STATS_WEBAPP_URL, user_id)
        )
        await message.answer(text, reply_markup=keyboard)
        return

    await message.answer(
        text
        + "\n\nPremium mini app URL sozlanmagan. Iltimos .env ichiga STATS_WEBAPP_URL ni qo'shing.",
    )


async def _show_ads_panel(
    message: types.Message,
    *,
    edit: bool = False,
) -> None:
    text, keyboard = await _build_ads_panel_view()

    if edit:
        await message.edit_text(text, reply_markup=keyboard)
        return

    await message.answer(text, reply_markup=keyboard)


async def _show_helper_admins_panel(
    message: types.Message,
    *,
    edit: bool = False,
) -> None:
    helper_admins = await list_helper_admins()
    text = _render_helper_admins_panel(helper_admins)
    keyboard = helper_admins_keyboard(helper_admins)

    if edit:
        await message.edit_text(text, reply_markup=keyboard)
        return

    await message.answer(text, reply_markup=keyboard)


async def _show_helper_admin_detail(
    message: types.Message,
    helper_admin: dict[str, object],
    *,
    edit: bool = False,
) -> None:
    text = _render_helper_admin_detail(helper_admin)
    keyboard = helper_admin_detail_keyboard(
        int(helper_admin["user_id"]),
        helper_admin["permissions"],
    )

    if edit:
        await message.edit_text(text, reply_markup=keyboard)
        return

    await message.answer(text, reply_markup=keyboard)


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


def _ad_duration_limits_text() -> str:
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


@router.message(Command("shutdown"))
async def shutdown_bot(message: types.Message, dispatcher: Dispatcher) -> None:
    if message.from_user is None or not _is_owner(message.from_user.id):
        return

    stop_event = dispatcher.get("owner_stop_event")
    if stop_event is None:
        await message.answer("❌ To'xtatish mexanizmi topilmadi")
        return

    await message.answer("🛑 Bot to'xtatilmoqda")
    stop_event.set()


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
    ~F.text.in_(ADMIN_ACTIONS),
)
async def receive_edit_movie_code(
    message: types.Message,
    state: FSMContext,
) -> None:
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
    ~F.text.in_(ADMIN_ACTIONS),
)
async def receive_edit_movie_action_text(
    message: types.Message,
    state: FSMContext,
) -> None:
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
    ~F.text.in_(ADMIN_ACTIONS),
)
async def receive_edit_movie_title(
    message: types.Message,
    state: FSMContext,
) -> None:
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
    ~F.text.in_(ADMIN_ACTIONS),
)
async def receive_edit_movie_description(
    message: types.Message,
    state: FSMContext,
) -> None:
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


@router.message(Command("migrate_media"))
async def migrate_single_media(message: types.Message) -> None:
    if not await _ensure_message_access(message, permission=ADMIN_PERMISSION_MOVIES):
        return

    if not legacy_media_enabled():
        await message.answer(
            "LEGACY_BOT_TOKEN topilmadi.\n.env ga eski bot tokenini qo'shing va qayta urinib ko'ring."
        )
        return

    code = _command_argument(message.text)
    if not code:
        await message.answer("Foydalanish: /migrate_media <kod>")
        return

    movie = await get_movie(code)
    if movie is None:
        await message.answer("Kod topilmadi.")
        return

    title, _description, file_id, _content_kind = movie
    if not file_id:
        await message.answer("Bu kodda media fayl biriktirilmagan.")
        return

    renewed = await renew_file_id_from_legacy(
        code,
        current_bot=message.bot,
        fallback_file_id=file_id,
    )
    if renewed is None:
        await message.answer(
            "Media avtomatik ko'chmadi.\nEski bot tokeni yoki eski faylga kirish imkonini tekshiring."
        )
        return

    if renewed != file_id:
        await message.answer(
            "✅ Media yangilandi\n\n"
            f"📌 Kod: <code>{escape(code)}</code>\n"
            f"🎬 Nomi: <b>{escape(title)}</b>",
            parse_mode="HTML",
        )
        return

    await message.answer(
        "ℹ️ Ushbu kod uchun media allaqachon yangilanganga o'xshaydi."
    )


@router.message(Command("migrate_serial"))
async def migrate_serial_media(message: types.Message) -> None:
    if not await _ensure_message_access(message, permission=ADMIN_PERMISSION_MOVIES):
        return

    if not legacy_media_enabled():
        await message.answer(
            "LEGACY_BOT_TOKEN topilmadi.\n.env ga eski bot tokenini qo'shing va qayta urinib ko'ring."
        )
        return

    raw_code = _command_argument(message.text)
    if not raw_code:
        await message.answer("Foydalanish: /migrate_serial <serial-kodi>")
        return

    group = await get_serial_group_for_lookup(raw_code)
    if group is None:
        await message.answer("Serial kodi topilmadi.")
        return

    group_code, group_title, _group_description = group
    episodes = await get_serial_episodes(group_code)
    if not episodes:
        await message.answer("Bu serialda qism topilmadi.")
        return

    await message.answer(
        f"⏳ {escape(group_title)} uchun media migratsiyasi boshlandi ({len(episodes)} qism).",
        parse_mode="HTML",
    )

    updated = 0
    unchanged = 0
    failed = 0

    for episode_code, _episode_number, _description, file_id in episodes:
        if not file_id:
            failed += 1
            continue

        renewed = await renew_file_id_from_legacy(
            episode_code,
            current_bot=message.bot,
            fallback_file_id=file_id,
        )
        if renewed is None:
            failed += 1
        elif renewed == file_id:
            unchanged += 1
        else:
            updated += 1

    await message.answer(
        "✅ Migratsiya yakunlandi\n\n"
        f"📺 Serial: <b>{escape(group_title)}</b>\n"
        f"🔄 Yangilandi: <b>{updated}</b>\n"
        f"♻️ O'zgarmadi: <b>{unchanged}</b>\n"
        f"⚠️ Xato: <b>{failed}</b>",
        parse_mode="HTML",
    )


@router.message(Command("migrate_all_media"))
async def migrate_all_media(message: types.Message) -> None:
    if not await _ensure_message_access(message, permission=ADMIN_PERMISSION_MOVIES):
        return

    if not legacy_media_enabled():
        await message.answer(
            "LEGACY_BOT_TOKEN topilmadi.\n.env ga eski bot tokenini qo'shing va qayta urinib ko'ring."
        )
        return

    all_items = await get_all_movies()
    if not all_items:
        await message.answer("Bazada kontent topilmadi.")
        return

    await message.answer(
        f"⏳ Barcha kontent uchun media migratsiyasi boshlandi ({len(all_items)} ta kod)."
    )

    updated = 0
    unchanged = 0
    failed = 0

    for code, _title, _kind in all_items:
        movie = await get_movie(code)
        if movie is None:
            failed += 1
            continue

        _movie_title, _description, file_id, _content_kind = movie
        if not file_id:
            failed += 1
            continue

        renewed = await renew_file_id_from_legacy(
            code,
            current_bot=message.bot,
            fallback_file_id=file_id,
        )
        if renewed is None:
            failed += 1
        elif renewed == file_id:
            unchanged += 1
        else:
            updated += 1

    await message.answer(
        "✅ Umumiy migratsiya tugadi\n\n"
        f"🔄 Yangilandi: <b>{updated}</b>\n"
        f"♻️ O'zgarmadi: <b>{unchanged}</b>\n"
        f"⚠️ Xato: <b>{failed}</b>",
        parse_mode="HTML",
    )


@router.message(
    StateFilter("*"),
    F.text.in_(ADMIN_ACTIONS),
)
async def admin_global_handler(message: types.Message, state: FSMContext) -> None:
    user = message.from_user
    if user is None:
        return

    if not await _is_admin(user.id):
        return

    await touch_user(user.id, user.username, user.full_name)

    text = message.text
    await state.clear()
    permissions = await _admin_permissions(user.id)
    is_owner = _is_owner(user.id)

    if text == ADMIN_PANEL_BUTTON:
        await message.answer(
            "👨‍💻 <b>Boshqaruv Paneliga Xush Kelibsiz!</b>\n\n<i>Kerakli bo'limni tanlang:</i>",
            parse_mode="HTML",
            reply_markup=admin_menu(permissions=permissions, is_owner=is_owner),
        )
    elif text == MOVIE_MANAGEMENT_BUTTON:
        if ADMIN_PERMISSION_MOVIES not in permissions:
            await message.answer("Sizda bu bo'limga ruxsat yo'q")
            return
        await message.answer(
            "🎬 <b>Kino va Seriallar Boshqaruvi</b>\n\n<i>Amalni tanlang:</i>",
            parse_mode="HTML",
            reply_markup=movie_menu(),
        )
    elif text in {NEW_MOVIE_BUTTON, LEGACY_NEW_MOVIE_BUTTON}:
        if ADMIN_PERMISSION_MOVIES not in permissions:
            await message.answer("Sizda bu amalga ruxsat yo'q")
            return
        await _start_add_movie_flow(message, state, content_kind="movie")
    elif text == NEW_SERIAL_BUTTON:
        if ADMIN_PERMISSION_MOVIES not in permissions:
            await message.answer("Sizda bu amalga ruxsat yo'q")
            return
        await _show_serial_mode_picker(message, state)
    elif text == EDIT_MOVIE_BUTTON:
        if ADMIN_PERMISSION_MOVIES not in permissions:
            await message.answer("Sizda bu amalga ruxsat yo'q")
            return
        await start_edit_movie(message, state)
    elif text == DELETE_MOVIE_BUTTON:
        if ADMIN_PERMISSION_MOVIES not in permissions:
            await message.answer("Sizda bu amalga ruxsat yo'q")
            return
        await delete_start(message, state)
    elif text == MOVIES_LIST_BUTTON:
        if ADMIN_PERMISSION_MOVIES not in permissions:
            await message.answer("Sizda bu bo'limga ruxsat yo'q")
            return
        await movie_list(message)
    elif text == REQUESTS_BUTTON:
        if ADMIN_PERMISSION_REQUESTS not in permissions:
            await message.answer("Sizda bu bo'limga ruxsat yo'q")
            return
        await show_requests(message)
    elif text in {STATS_BUTTON, LEGACY_STATS_BUTTON}:
        if ADMIN_PERMISSION_STATS not in permissions:
            await message.answer("Sizda bu bo'limga ruxsat yo'q")
            return
        await _show_stats_webapp(message)
    elif text == ADS_BUTTON:
        if ADMIN_PERMISSION_ADS not in permissions:
            await message.answer("Sizda bu bo'limga ruxsat yo'q")
            return
        await _show_ads_panel(message)
    elif text == CHANNELS_BUTTON:
        if "channels" not in permissions and not is_owner:
            await message.answer("Sizda bu bo'limga ruxsat yo'q")
            return
        await admin_channels_menu(message, state)
    elif text == HELPER_ADMINS_BUTTON:
        if not is_owner:
            await message.answer("Sizda bu bo'limga ruxsat yo'q")
            return
        await _show_helper_admins_panel(message)
    elif text == BACK_TO_ADMIN_BUTTON:
        await message.answer(
            "⚙️ <b>Admin Panel</b>\n\n<i>Kerakli bo'limni tanlang:</i>",
            parse_mode="HTML",
            reply_markup=admin_menu(permissions=permissions, is_owner=is_owner),
        )
    elif text == BACK_TO_MAIN_BUTTON:
        await message.answer(
            "⬅️ <b>Asosiy Menyu</b>\n\n<i>Nimani tanlaysiz?</i>",
            parse_mode="HTML",
            reply_markup=main_menu(
                user.id, show_admin_panel=bool(permissions)
            ),
        )


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


@router.callback_query(F.data == "serial_mode_new")
async def serial_mode_new(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_callback_access(callback, permission=ADMIN_PERMISSION_MOVIES):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    await callback.answer()

    if callback.message is not None:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await _start_add_movie_flow(callback.message, state, content_kind="serial")


@router.callback_query(F.data == "serial_mode_continue")
async def serial_mode_continue(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _ensure_callback_access(callback, permission=ADMIN_PERMISSION_MOVIES):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    await callback.answer()

    if callback.message is not None:
        await _show_serial_titles_picker(callback.message, state, page=0, edit=True)


@router.callback_query(F.data == "serial_mode_cancel")
async def serial_mode_cancel(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_callback_access(callback, permission=ADMIN_PERMISSION_MOVIES):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    await state.clear()
    await callback.answer("Bekor qilindi")

    if callback.message is not None:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass


@router.callback_query(
    AddMovieState.waiting_for_serial_pick, F.data.startswith("serial_page_")
)
async def serial_titles_page(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_callback_access(callback, permission=ADMIN_PERMISSION_MOVIES):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    if callback.message is None:
        await callback.answer()
        return

    try:
        page = int(callback.data.rsplit("_", 1)[1])
    except ValueError:
        await callback.answer("Tanlov eskirgan", show_alert=True)
        return

    data = await state.get_data()
    titles = data.get("serial_titles") or await get_serial_titles()
    await callback.answer()
    await _show_serial_titles_picker(
        callback.message,
        state,
        page=page,
        titles=titles,
        edit=True,
    )


@router.callback_query(
    AddMovieState.waiting_for_serial_pick, F.data.startswith("serial_pick_")
)
async def serial_pick(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_callback_access(callback, permission=ADMIN_PERMISSION_MOVIES):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    if callback.message is None:
        await callback.answer()
        return

    try:
        index = int(callback.data.rsplit("_", 1)[1])
    except ValueError:
        await callback.answer("Tanlov eskirgan", show_alert=True)
        return

    data = await state.get_data()
    titles = data.get("serial_titles") or await get_serial_titles()
    if not 0 <= index < len(titles):
        await callback.answer("Serial topilmadi", show_alert=True)
        return

    await callback.answer()
    await _start_serial_continuation_flow(
        callback.message,
        state,
        titles[index],
        edit=True,
    )


@router.callback_query(
    AddMovieState.waiting_for_serial_pick, F.data == "serial_season_continue"
)
async def serial_pick_continue_same_season(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _ensure_callback_access(callback, permission=ADMIN_PERMISSION_MOVIES):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    if callback.message is None:
        await callback.answer()
        return

    data = await state.get_data()
    selected_title = data.get("serial_pick_title")
    if not selected_title:
        await callback.answer("Tanlov eskirgan", show_alert=True)
        return

    await callback.answer("Shu fasl davom ettiriladi")
    next_episode_number = await get_next_serial_episode_number(str(selected_title))
    await _start_serial_continuation_flow(
        callback.message,
        state,
        str(selected_title),
        episode_number=int(next_episode_number),
        edit=True,
    )


@router.callback_query(
    AddMovieState.waiting_for_serial_pick, F.data == "serial_season_new"
)
async def serial_pick_start_new_season(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _ensure_callback_access(callback, permission=ADMIN_PERMISSION_MOVIES):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    if callback.message is None:
        await callback.answer()
        return

    data = await state.get_data()
    next_season_title = data.get("serial_pick_next_season")
    if not next_season_title:
        await callback.answer("Tanlov eskirgan", show_alert=True)
        return

    await callback.answer("Yangi fasl boshlandi")
    await _start_serial_continuation_flow(
        callback.message,
        state,
        str(next_season_title),
        episode_number=1,
        edit=True,
    )


@router.message(AddMovieState.waiting_for_serial_pick)
async def receive_existing_serial_name(
    message: types.Message, state: FSMContext
) -> None:
    if not await _ensure_message_access(message, permission=ADMIN_PERMISSION_MOVIES):
        return

    if not message.text or not message.text.strip():
        await message.answer(
            "⚠️ <b>Serial nomi kerak</b>\n\n<i>Nom yozing yoki ro'yxatdan tanlang</i>",
            parse_mode="HTML",
        )
        return

    titles = await get_serial_titles()
    matches = _match_serial_titles(titles, message.text.strip())
    if not matches:
        await message.answer(
            "🔎 <b>Serial topilmadi</b>\n\n"
            "<i>Nomni qayta yuboring yoki ro'yxatdan tanlang</i>",
            parse_mode="HTML",
        )
        return

    if len(matches) == 1:
        await _start_serial_continuation_flow(message, state, matches[0])
        return

    await _show_serial_titles_picker(
        message,
        state,
        page=0,
        titles=matches,
    )


@router.callback_query(
    AddMovieState.waiting_for_serial_continue, F.data == "serial_more_yes"
)
async def serial_more_yes(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_callback_access(callback, permission=ADMIN_PERMISSION_MOVIES):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    data = await state.get_data()
    series_title = data.get("series_title")
    episode_number = data.get("episode_number")
    if not series_title or not episode_number:
        await state.clear()
        await callback.answer("Tanlov eskirgan", show_alert=True)
        return

    await callback.answer("Davom etamiz")

    if callback.message is not None:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await _start_serial_continuation_flow(
            callback.message,
            state,
            str(series_title),
            episode_number=int(episode_number),
        )


@router.callback_query(
    AddMovieState.waiting_for_serial_continue, F.data == "serial_more_no"
)
async def serial_more_no(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_callback_access(callback, permission=ADMIN_PERMISSION_MOVIES):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    await state.clear()
    await callback.answer("Yakunlandi")

    if callback.message is not None:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass


@router.callback_query(AddMovieState.waiting_for_video, F.data == "serial_upload_finish")
async def serial_upload_finish(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _ensure_callback_access(callback, permission=ADMIN_PERMISSION_MOVIES):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    data = await state.get_data()
    if _content_kind_key(data.get("content_kind")) != "serial":
        await callback.answer()
        return

    await state.clear()
    await callback.answer("Yakunlandi")

    if callback.message is not None:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await callback.message.answer(
            "✅ <b>Serial yuklash yakunlandi</b>\n\n<i>Qismlar saqlandi.</i>",
            parse_mode="HTML",
        )


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
        f"{_ad_duration_prompt()}",
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


async def movie_list(message: types.Message) -> None:
    await _show_content_list(message)


async def _show_content_list(
    message: types.Message,
    *,
    filter_key: str | None = None,
    page: int = 0,
    edit: bool = False,
) -> None:
    movies = await get_all_movies()
    if not movies:
        text = "📭 Kino va seriallar ro'yxati bo'sh"
        reply_markup = None
    else:
        movie_items = [movie for movie in movies if movie[2] == "movie"]
        serial_items = [movie for movie in movies if movie[2] == "serial"]

        normalized_filter = filter_key if filter_key in {"movie", "serial"} else None
        if normalized_filter == "movie":
            text, total_pages, current_page = _render_content_section_page(
                movie_items,
                filter_key="movie",
                page=page,
            )
        elif normalized_filter == "serial":
            text, total_pages, current_page = _render_content_section_page(
                serial_items,
                filter_key="serial",
                page=page,
            )
        else:
            text = _render_content_picker_text(
                title="📚 Kontent ro'yxati",
                movie_count=len(movie_items),
                serial_count=len(serial_items),
                empty_text="📭 Kino va seriallar ro'yxati bo'sh",
                hint_text="Ko'rish uchun pastdan bo'limni tanlang.",
            )
            total_pages = 1
            current_page = 0

        reply_markup = content_list_keyboard(
            active_filter=normalized_filter,
            movie_count=len(movie_items),
            serial_count=len(serial_items),
            page=current_page,
            total_pages=total_pages,
        )

    if edit:
        await message.edit_text(text, reply_markup=reply_markup)
        return

    await message.answer(text, reply_markup=reply_markup)


@router.callback_query(F.data.startswith("content_list:"))
async def content_list_callback(callback: types.CallbackQuery) -> None:
    if not await _ensure_callback_access(callback, permission=ADMIN_PERMISSION_MOVIES):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

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


async def _show_delete_panel(
    message: types.Message,
    *,
    state: FSMContext | None = None,
    filter_key: str | None = None,
    page: int = 0,
    edit: bool = False,
) -> None:
    movies = await get_all_movies()
    if not movies:
        text = "📭 O'chirish uchun kino yoki serial topilmadi"
        reply_markup = None
        if state is not None:
            await state.clear()
    else:
        movie_items = [movie for movie in movies if movie[2] == "movie"]
        serial_items = [movie for movie in movies if movie[2] == "serial"]
        active_filter = filter_key if filter_key in {"movie", "serial"} else None

        if active_filter is None:
            text = _render_content_picker_text(
                title="🗑 Kontentni o'chirish",
                movie_count=len(movie_items),
                serial_count=len(serial_items),
                empty_text="📭 O'chirish uchun kino yoki serial topilmadi",
                hint_text="O'chirish uchun pastdan bo'limni tanlang.",
            )
            reply_markup = delete_movie_keyboard(
                active_filter=None,
                movie_count=len(movie_items),
                serial_count=len(serial_items),
                page=0,
                total_pages=1,
            )
            if state is not None:
                await state.clear()
        else:
            filtered_items = movie_items if active_filter == "movie" else serial_items

            total_items = len(filtered_items)
            total_pages = max(
                1, (total_items + DELETE_PANEL_PAGE_SIZE - 1) // DELETE_PANEL_PAGE_SIZE
            )
            current_page = min(max(page, 0), total_pages - 1)
            start = current_page * DELETE_PANEL_PAGE_SIZE
            end = start + DELETE_PANEL_PAGE_SIZE
            page_items = filtered_items[start:end]

            text = _render_delete_panel_text(
                filter_key=active_filter,
                total_items=total_items,
                page=current_page,
                total_pages=total_pages,
                page_items=page_items,
                start_index=start,
            )
            reply_markup = delete_movie_keyboard(
                active_filter=active_filter,
                movie_count=len(movie_items),
                serial_count=len(serial_items),
                page_items=page_items,
                page=current_page,
                total_pages=total_pages,
            )

            if state is not None:
                await state.set_state(DeleteMovieState.waiting_for_code)
                await state.update_data(
                    delete_filter=active_filter, delete_page=current_page
                )

    if edit:
        await message.edit_text(text, reply_markup=reply_markup)
        return

    await message.answer(text, reply_markup=reply_markup)


async def delete_start(message: types.Message, state: FSMContext) -> None:
    await _show_delete_panel(message, state=state)


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


@router.callback_query(F.data.startswith("stats_"))
async def stats_callback(callback: types.CallbackQuery) -> None:
    if not await _ensure_callback_access(callback, permission=ADMIN_PERMISSION_STATS):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    await touch_user(
        callback.from_user.id,
        callback.from_user.username,
        callback.from_user.full_name,
    )

    if callback.message is None:
        await callback.answer()
        return

    _, action, *rest = callback.data.split("_")
    panel = "overview" if action == "refresh" else action
    if action == "refresh" and rest:
        panel = rest[0]
    panel = panel if panel in STATS_PANELS else "overview"
    notice = "Dashboard yangilandi" if action == "refresh" else None

    try:
        await _show_stats_dashboard(callback.message, panel=panel, edit=True)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise

    await callback.answer(notice)


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
            _request_rejected_text(request_text, request_id=rid),
            parse_mode="HTML",
        )
        await callback.answer("So'rov bo'yicha xabar yuborildi")
    except (TelegramBadRequest, TelegramForbiddenError):
        await callback.answer("So'rov yopildi, lekin foydalanuvchiga xabar yuborilmadi")

    await message.edit_reply_markup()


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


# Balanced premium text overrides
# ======================
# 📢 MAJBURIY KANALLAR
# ======================


@router.message(F.text == CHANNELS_BUTTON)
async def admin_channels_menu(message: types.Message, state: FSMContext) -> None:
    if not await _ensure_message_access(message, permission="channels"):
        return

    await state.clear()
    channels = await get_sponsor_channels()

    text = "📢 <b>Majburiy Kanallar (Force Subscription)</b>\n\n"
    if not channels:
        text += "<i>Hozircha hech qanday homiy kanal kiritilmagan.</i>\n\n"
    else:
        for idx, ch in enumerate(channels, 1):
            text += f"<b>{idx}.</b> {ch['name']} — (<pre>{ch['id']}</pre>)\n🔗 {ch['url']}\n\n"

    text += "➕ <b>Yangi kanal qo'shish</b>\n\n<i>Ma'lumotlarni bitta xabarda quyidagi ketma-ketlikda yuboring:</i>\n\n"
    text += "<pre>@KanalID https://t.me/Link Kanal Nomi</pre>\n\n"
    text += "Masalan:\n<pre>@PrimeCinema https://t.me/PrimeCinema Zo'r Kinolar</pre>"

    # Inline buttons for deletes
    buttons = []
    for ch in channels:
        buttons.append(
            [
                types.InlineKeyboardButton(
                    text=f"🗑 {ch['name']}", callback_data=f"del_channel:{ch['id']}"
                )
            ]
        )

    markup = types.InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None

    await message.answer(
        text, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True
    )
    await state.set_state(AdminChannelState.waiting_for_channel)


@router.message(AdminChannelState.waiting_for_channel)
async def receive_new_channel(message: types.Message, state: FSMContext) -> None:
    if not await _ensure_message_access(message, permission="channels"):
        return

    if not message.text:
        await message.answer(
            "❌ Iltimos, matn yuboring.\n@KanalID https://t.me/Link Kanal Nomi"
        )
        return

    if message.text in ADMIN_ACTIONS + USER_ACTIONS:
        await state.clear()
        return  # user pressed another menu button

    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer(
            "❌ Noto'g'ri format. Iltimos ko'rsatilganidek kiriting:\n@KanalID https://t.me/Link Kanal Nomi"
        )
        return

    ch_id, ch_url, ch_name = parts
    if not ch_url.startswith("http"):
        await message.answer("❌ Link 'http' ishtirok etishi shart.")
        return

    await add_sponsor_channel(ch_id, ch_url, ch_name)
    await message.answer(
        f"✅ Kanal ajoyib qo'shildi:\nID: {ch_id}\nLink: {ch_url}\nNomi: {ch_name}"
    )
    await state.clear()

    # Refresh menu (message mutatsiyasiz)
    await admin_channels_menu(message, state)


@router.callback_query(F.data.startswith("del_channel:"))
async def handle_delete_channel(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _ensure_callback_access(callback, permission="channels"):
        await callback.answer("Ruxsatingiz yo'q", show_alert=True)
        return

    ch_id = callback.data.split(":", 1)[1]
    await remove_sponsor_channel(ch_id)
    await callback.answer("✅ Kanal o'chirildi")

    # Kanal menyusini yangilash (message mutatsiyasiz)
    if callback.message is not None:
        channels = await get_sponsor_channels()
        text = "📢 <b>Majburiy Kanallar (Force Subscription)</b>\n\n"
        if not channels:
            text += "<i>Hozircha hech qanday homiy kanal kiritilmagan.</i>\n\n"
        else:
            for idx, ch in enumerate(channels, 1):
                text += f"<b>{idx}.</b> {ch['name']} — (<pre>{ch['id']}</pre>)\n🔗 {ch['url']}\n\n"
        text += "➕ <b>Yangi kanal qo'shish</b>\n\n<i>Ma'lumotlarni bitta xabarda quyidagi ketma-ketlikda yuboring:</i>\n\n"
        text += "<pre>@KanalID https://t.me/Link Kanal Nomi</pre>\n\n"
        text += "Masalan:\n<pre>@PrimeCinema https://t.me/PrimeCinema Zo'r Kinolar</pre>"

        buttons = []
        for ch in channels:
            buttons.append(
                [
                    types.InlineKeyboardButton(
                        text=f"🗑 {ch['name']}", callback_data=f"del_channel:{ch['id']}"
                    )
                ]
            )
        markup = types.InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None

        try:
            await callback.message.edit_text(
                text, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True
            )
        except TelegramBadRequest:
            pass
    await state.set_state(AdminChannelState.waiting_for_channel)


# UI overrides

from .admin_runtime_helpers import (
    _ad_duration_prompt,
    _build_ads_panel_text,
    _build_dashboard_caption,
    _build_stats_insights,
    _content_kind_icon,
    _content_list_filter_label,
    _format_recent_users,
    _helper_admin_permission_label,
    _render_content_picker_text,
    _render_content_section_page,
    _render_delete_panel_text,
    _render_helper_admin_detail,
    _render_helper_admins_panel,
    _request_added_text,
    _request_rejected_text,
    _request_text,
    _show_content_list,
    _show_delete_panel,
    _show_serial_continue_prompt,
    _show_serial_mode_picker,
    _show_serial_titles_picker,
    _show_stats_webapp,
    _start_add_movie_flow,
    _start_serial_continuation_flow,
    send_request_review,
    show_requests,
)
