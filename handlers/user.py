"""User-facing text lives in this file; edit strings below to change user copy."""

import asyncio
import logging
import re
from html import escape
from urllib.parse import quote

from aiogram import Bot, F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from middlewares.forcesub import (
    FEATURE_REQUEST,
    FEATURE_SEARCH,
    ensure_feature_access,
    ensure_feature_callback_access,
)
from keyboards import (
    ADMIN_ACTIONS,
    FAVORITES_BUTTON,
    HELP_BUTTON,
    HISTORY_BUTTON,
    LEGACY_SEARCH_BUTTON,
    REQUEST_BUTTON,
    SEARCH_BUTTON,
    USER_ACTIONS,
    main_menu,
    serial_hub_keyboard,
)
from repositories.content import (
    add_history,
    count_favorites,
    count_history,
    get_favorites,
    get_favorites_page,
    get_history,
    get_history_page,
    get_movie,
    get_movies_for_serial_base,
    get_movies_by_codes,
    get_serial_groups,
    get_serial_episodes,
    get_serial_group_for_lookup,
    is_favorite,
    log_user_search_event,
    record_movie_view,
)
from repositories.channels import get_sponsor_channels
from repositories.requests import add_request
from repositories.users import has_feature_trial_used, is_admin_user, mark_feature_trial_used
from services.legacy_media import (
    legacy_media_enabled,
    renew_file_id_as_video,
    renew_file_id_from_legacy,
)
from services.telegram_context import touch_callback_user, touch_message_user
from services.user_views import (
    LIST_PAGE_SIZE,
    SERIAL_HUB_PAGE_SIZE,
    collection_keyboard,
    help_text,
    parse_page,
    render_codes_page_text,
    send_movie_card,
    send_or_edit_text,
    total_pages,
)

router = Router()
FAVORITES_PAGE_PREFIX = "favlist"
HISTORY_PAGE_PREFIX = "histlist"
USER_GLOBAL_ACTIONS = tuple(
    action for action in USER_ACTIONS if action not in {SEARCH_BUTTON, LEGACY_SEARCH_BUTTON}
)
SEASON_TITLE_RE = re.compile(
    r"^\s*(?P<base>.+?)\s+(?P<season>\d+)\s*-\s*fasl\s*$",
    re.IGNORECASE,
)
SEASON_TOKEN_PREFIX = "season_"
GROUP_REF_SEASON_DELIMITER = "|s"
CODE_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]+")
SERIAL_DESCRIPTION_LIMIT = 760
SERIAL_TITLE_LIMIT = 120
SERIAL_INLINE_LIMIT = 96
CHANNEL_SHARE_BUTTON_LIMIT = 96

# code, episode_number, description, file_id, item_type, season_number, item_title
SerialTimelineItem = tuple[str, int, str, str, str, int | None, str]
logger = logging.getLogger(__name__)
_runtime_repair_locks: dict[str, asyncio.Lock] = {}
_runtime_video_preview_cache: dict[str, tuple[str, str]] = {}


async def _should_protect_content(user_id: int) -> bool:
    return not await is_admin_user(user_id)
SHARE_CALLBACK_PREFIX = "share:"
CHANNEL_SHARE_CALLBACK_PREFIX = "sharech:"
SHARE_QUERY_PREFIX = "share:"
DEEP_LINK_WATCH_PREFIX = "watch_"
_bot_username_cache: str | None = None


class KinoState(StatesGroup):
    waiting_for_code = State()


class RequestState(StatesGroup):
    waiting_for_request = State()


class CollectionState(StatesGroup):
    viewing_favorites = State()
    viewing_history = State()


@router.message(StateFilter("*"), F.text.in_(USER_GLOBAL_ACTIONS))
async def user_global_handler(message: types.Message, state: FSMContext) -> None:
    await touch_message_user(message)
    text = message.text
    await state.clear()

    if text == REQUEST_BUTTON:
        await message.answer(
            "✍️ <b>So'rov</b>\n\n"
            "<i>Nom yuboring. Rasm ham qo'shishingiz mumkin.</i>\n"
            "<i>Video qabul qilinmaydi.</i>",
            parse_mode="HTML",
        )
        await state.set_state(RequestState.waiting_for_request)
    elif text == FAVORITES_BUTTON:
        await show_favorites(message, message.from_user.id, state=state)
    elif text == HISTORY_BUTTON:
        await show_history(message, message.from_user.id, state=state)
    elif text == HELP_BUTTON:
        await show_help(message)


@router.message(
    KinoState.waiting_for_code,
    ~F.text.in_(USER_ACTIONS + ADMIN_ACTIONS),
    ~F.text.startswith("/"),
)
async def receive_movie_code(message: types.Message, state: FSMContext) -> None:
    await touch_message_user(message)
    # Keep search mode active so users can send codes continuously
    # without pressing "Qidiruv" before every lookup.
    await _send_movie_by_code(message, state, message.text, clear_state=False)


def _serial_episode_caption(
    *,
    title: str,
    group_code: str,
    episode_number: int,
    total_episodes: int,
    description: str,
) -> str:
    safe_title = escape(_truncate_text(title, SERIAL_TITLE_LIMIT))
    safe_group_code = escape(group_code)
    subtitle = (
        f"{episode_number}-qism • {total_episodes} qism"
        if episode_number > 0
        else f"Serial • {total_episodes} qism"
    )
    title_line = f"📺 <b>{safe_title}</b> <i>{subtitle}</i>"
    footer_line = f"<i>Serial kodi</i>: <code>{safe_group_code}</code>"
    return _compose_html_caption(
        title_line=title_line,
        footer_line=footer_line,
        description=description,
        description_limit=SERIAL_DESCRIPTION_LIMIT,
    )


def _serial_page_payload(
    episodes: list[tuple[str, int, str, str]],
    page: int,
) -> tuple[int, int, list[tuple[str, int, str, str]]]:
    total_pages = max(1, (len(episodes) + SERIAL_HUB_PAGE_SIZE - 1) // SERIAL_HUB_PAGE_SIZE)
    current_page = min(max(page, 0), total_pages - 1)
    start = current_page * SERIAL_HUB_PAGE_SIZE
    return current_page, total_pages, episodes[start : start + SERIAL_HUB_PAGE_SIZE]


async def _serial_hub_reply_markup(
    *,
    bot: Bot,
    user_id: int,
    group_code: str,
    episodes: list[tuple[str, int, str, str]],
    page: int,
) -> types.InlineKeyboardMarkup:
    current_page, total_pages, visible = _serial_page_payload(episodes, page)
    is_fav = await is_favorite(user_id, group_code)
    share_query = None
    share_callback_data = _build_share_callback_data(group_code)
    share_button_text = "🔗 Ulashish"
    channel_callback_data = None
    candidate_callback_data = f"{CHANNEL_SHARE_CALLBACK_PREFIX}{group_code}"
    if await is_admin_user(user_id) and len(candidate_callback_data.encode("utf-8")) <= 64:
        channel_callback_data = candidate_callback_data

    return serial_hub_keyboard(
        group_code,
        [
            (episode_code, f"{max(1, int(episode_number or 1))}-qism")
            for episode_code, episode_number, _description, _file_id in visible
        ],
        page=current_page,
        total_pages=total_pages,
        is_fav=is_fav,
        share_query=share_query,
        share_callback_data=share_callback_data,
        share_button_text=share_button_text,
        channel_callback_data=channel_callback_data,
    )


def _parse_season_title(title: str) -> tuple[str, int] | None:
    match = SEASON_TITLE_RE.match((title or "").strip())
    if not match:
        return None

    base_title = match.group("base").strip()
    season_number = int(match.group("season"))
    if not base_title or season_number <= 0:
        return None

    return base_title, season_number


def _short_inline_label(text: str, limit: int = 16) -> str:
    value = (text or "").strip()
    if not value:
        return "Kontent"
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 3)].rstrip() + "..."


def _truncate_text(value: str | None, limit: int) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _compose_html_caption(
    *,
    title_line: str,
    footer_line: str,
    description: str | None,
    description_limit: int,
) -> str:
    raw_description = (description or "").strip()
    max_description = max(0, description_limit)

    while True:
        safe_description = (
            escape(_truncate_text(raw_description, max_description))
            if raw_description and max_description > 0
            else ""
        )
        lines = [title_line, ""]
        if safe_description:
            lines.extend([safe_description, footer_line])
        else:
            lines.append(footer_line)
        caption = "\n".join(lines)
        if len(caption) <= 1024 or not raw_description or max_description <= 0:
            break
        max_description -= 1

    if len(caption) <= 1024:
        return caption

    return _truncate_text(f"{title_line}\n\n{footer_line}", 1024)


def _is_not_modified_error(error: TelegramBadRequest) -> bool:
    return "message is not modified" in str(error).casefold()


def _extract_interlude_label(base_title: str, movie_title: str) -> str:
    raw_title = (movie_title or "").strip()
    if not raw_title:
        return "Film"

    if raw_title.casefold().startswith(base_title.casefold()):
        short = raw_title[len(base_title) :].strip(" -:|")
        if short:
            return short

    return raw_title


def _season_token(season_number: int) -> str:
    return f"{SEASON_TOKEN_PREFIX}{season_number}"


def _parse_season_token(token: str) -> int | None:
    if not token.startswith(SEASON_TOKEN_PREFIX):
        return None

    value = token[len(SEASON_TOKEN_PREFIX) :].strip()
    if not value.isdigit():
        return None

    number = int(value)
    return number if number > 0 else None


def _build_group_ref(group_code: str, season_number: int | None = None) -> str:
    if season_number is None:
        return group_code
    return f"{group_code}{GROUP_REF_SEASON_DELIMITER}{season_number}"


def _parse_group_ref(group_ref: str) -> tuple[str, int | None]:
    base_code, delimiter, season_text = group_ref.partition(GROUP_REF_SEASON_DELIMITER)
    if not delimiter:
        return group_ref, None

    season_text = season_text.strip()
    if not season_text.isdigit():
        return group_ref, None

    season_number = int(season_text)
    if season_number <= 0:
        return group_ref, None

    return base_code, season_number


def _extract_lookup_code(raw_text: str | None) -> str:
    value = (raw_text or "").strip()
    if not value:
        return ""

    tokens = CODE_TOKEN_RE.findall(value)
    if not tokens:
        return value[:256]  # Prevent excessively long inputs

    if len(tokens) == 1:
        return tokens[0][:256]

    numeric_tokens = [token for token in tokens if token.isdigit()]
    if numeric_tokens:
        return max(numeric_tokens, key=len)[:256]

    return tokens[0][:256]


async def _bot_username(bot: Bot) -> str | None:
    global _bot_username_cache
    if _bot_username_cache:
        return _bot_username_cache

    try:
        me = await bot.get_me()
    except TelegramBadRequest:
        return None

    username = (me.username or "").strip()
    if not username:
        return None

    _bot_username_cache = username
    return username


async def _build_share_links(bot: Bot, raw_code: str) -> tuple[str, str, str] | None:
    code = _extract_lookup_code(raw_code)
    if not code:
        return None

    username = await _bot_username(bot)
    if not username:
        return None

    deep_link = f"https://t.me/{username}?start={DEEP_LINK_WATCH_PREFIX}{quote(code, safe='')}"
    return code, deep_link, deep_link


def _build_inline_share_query(raw_code: str) -> str | None:
    code = _extract_lookup_code(raw_code)
    if not code:
        return None
    return f"{SHARE_QUERY_PREFIX}{code}"


async def _resolve_share_payload(
    raw_code: str,
) -> tuple[str, str, str, str] | None:
    code = _extract_lookup_code(raw_code)
    if not code:
        return None

    movie = await get_movie(code)
    if movie is not None:
        title, description, _file_id, detected_kind = movie
        content_kind = "serial" if detected_kind == "serial" else "movie"
        return code, title, description, content_kind

    serial = await get_serial_group_for_lookup(code)
    if serial is None:
        return None

    serial_code, serial_title, serial_description = serial
    return serial_code, serial_title, serial_description, "serial"


async def _build_share_url(bot: Bot, raw_code: str) -> str | None:
    payload = await _resolve_share_payload(raw_code)
    if payload is None:
        return None

    code, title, description, content_kind = payload
    links = await _build_share_links(bot, code)
    if links is None:
        return None

    _resolved_code, deep_link, _landing_url = links
    title_text = _truncate_text((title or "").strip(), 90) or "Kontent"
    description_text = _truncate_text((description or "").strip(), 260)

    share_text_lines = [f"{_share_icon(content_kind)} {title_text}"]
    if description_text:
        share_text_lines.extend(["", description_text])
    share_text = "\n".join(share_text_lines)

    return (
        f"https://t.me/share/url?url={quote(deep_link, safe='')}"
        f"&text={quote(share_text, safe='')}"
    )


def _build_share_callback_data(raw_code: str) -> str | None:
    code = _extract_lookup_code(raw_code)
    if not code:
        return None
    return f"{SHARE_CALLBACK_PREFIX}{code}"


def _share_icon(content_kind: str) -> str:
    return "📺" if content_kind == "serial" else "🎬"


async def _build_share_message(
    *,
    bot: Bot,
    raw_code: str,
) -> tuple[str, str] | None:
    links = await _build_share_links(bot, raw_code)
    if links is None:
        return None

    code, deep_link, _preview_link = links
    title = ""
    description = ""
    content_kind = "movie"

    movie = await get_movie(code)
    if movie is not None:
        title, description, _file_id, detected_kind = movie
        content_kind = "serial" if detected_kind == "serial" else "movie"
    else:
        serial = await get_serial_group_for_lookup(code)
        if serial is None:
            return None
        serial_code, serial_title, serial_description = serial
        serial_links = await _build_share_links(bot, serial_code)
        if serial_links is None:
            return None
        code, deep_link, _preview_link = serial_links
        title = serial_title
        description = serial_description
        content_kind = "serial"

    safe_title = escape(_truncate_text(title, 90) or "Kontent")
    safe_description = escape(_truncate_text(description, 280))
    lines = [f"{_share_icon(content_kind)} <b>{safe_title}</b>"]
    if safe_description:
        lines.extend(["", safe_description])

    return "\n".join(lines), deep_link


async def _build_admin_serial_share_keyboard(
    *,
    bot: Bot,
    group_code: str,
    deep_link: str,
    channel_callback_data: str | None = None,
) -> types.InlineKeyboardMarkup | None:
    group = await get_serial_group_for_lookup(group_code)
    if group is None:
        return None

    resolved_group_code, group_title, _group_description = group
    username = await _bot_username(bot)
    if not username:
        return None

    group_deep_link = (
        f"https://t.me/{username}?start="
        f"{DEEP_LINK_WATCH_PREFIX}{quote(resolved_group_code, safe='')}"
    )
    _base_title, timeline_items, multi_season = await _build_serial_timeline(
        group_code=resolved_group_code,
        group_title=group_title,
    )

    rows: list[list[types.InlineKeyboardButton]] = []
    current_row: list[types.InlineKeyboardButton] = []
    for item in timeline_items[:CHANNEL_SHARE_BUTTON_LIMIT]:
        item_code = item[0]
        item_type = item[4]
        season_number = item[5]
        label = _timeline_item_label(item, multi_season=multi_season)
        if multi_season and item_type == "episode" and season_number is not None:
            label = f"{season_number}-fasl {label}"

        item_link = (
            f"https://t.me/{username}?start="
            f"{DEEP_LINK_WATCH_PREFIX}{quote(item_code, safe='')}"
        )
        current_row.append(
            types.InlineKeyboardButton(
                text=label,
                url=item_link,
            )
        )
        if len(current_row) == 4:
            rows.append(current_row)
            current_row = []

    if current_row:
        rows.append(current_row)

    action_row = [
        types.InlineKeyboardButton(
            text="▶️ Botda ko'rish",
            url=group_deep_link or deep_link,
        )
    ]
    if channel_callback_data:
        action_row.append(
            types.InlineKeyboardButton(
                text="📣 Kanalga yuborish",
                callback_data=channel_callback_data,
            )
        )
    rows.append(action_row)
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _season_timeline_items(
    items: list[SerialTimelineItem],
    season_number: int,
) -> list[SerialTimelineItem]:
    return [
        item
        for item in items
        if item[4] == "episode" and item[5] == season_number
    ]


def _season_first_item(
    items: list[SerialTimelineItem],
    season_number: int,
) -> SerialTimelineItem | None:
    for item in items:
        if item[4] == "episode" and item[5] == season_number:
            return item
    return None


def _root_hub_entries(items: list[SerialTimelineItem]) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    seen_seasons: set[int] = set()

    for code, _episode_number, _description, _file_id, item_type, season_number, item_title in items:
        if item_type == "episode":
            if season_number is None or season_number in seen_seasons:
                continue
            seen_seasons.add(season_number)
            entries.append((_season_token(season_number), f"{season_number}-fasl"))
            continue

        label = _short_inline_label(item_title or "Film", limit=12)
        entries.append((code, f"🎬 {label}"))

    return entries


def _timeline_item_label(
    item: SerialTimelineItem,
    *,
    multi_season: bool,
) -> str:
    _code, episode_number, _description, _file_id, item_type, _season_number, item_title = item
    if item_type == "movie":
        return _short_inline_label(item_title or "Film", limit=14)

    return f"{max(1, int(episode_number or 1))}-qism"


def _timeline_caption(
    *,
    base_title: str,
    group_code: str,
    item: SerialTimelineItem,
    total_items: int,
    fallback_description: str,
    multi_season: bool,
) -> str:
    (
        _code,
        episode_number,
        description,
        _file_id,
        item_type,
        _season_number,
        item_title,
    ) = item

    if item_type == "movie":
        title_text = item_title or "Oraliq film"
        subtitle = f"Oraliq film • {total_items} kontent"
    else:
        subtitle = f"{max(1, int(episode_number or 1))}-qism • {total_items} kontent"
        title_text = base_title

    safe_title = escape(_truncate_text(title_text, SERIAL_TITLE_LIMIT))
    safe_group_code = escape(group_code)
    title_line = f"📺 <b>{safe_title}</b> <i>{subtitle}</i>"
    footer_line = f"<i>Serial kodi</i>: <code>{safe_group_code}</code>"
    return _compose_html_caption(
        title_line=title_line,
        footer_line=footer_line,
        description=description or fallback_description,
        description_limit=SERIAL_DESCRIPTION_LIMIT,
    )


async def _send_serial_media(
    message: types.Message,
    *,
    file_id: str,
    caption: str,
    fallback_caption: str,
    reply_markup: types.InlineKeyboardMarkup | None,
    edit: bool,
    protect_content: bool,
    allow_document_fallback: bool = True,
) -> tuple[bool, str | None]:
    if edit:
        try:
            await message.edit_media(
                media=types.InputMediaVideo(
                    media=file_id,
                    caption=caption,
                    parse_mode="HTML",
                ),
                reply_markup=reply_markup,
            )
            return True, "video"
        except TelegramBadRequest as first_error:
            if _is_not_modified_error(first_error):
                try:
                    await message.edit_reply_markup(reply_markup=reply_markup)
                except TelegramBadRequest:
                    pass
                return True, "video"
            if allow_document_fallback:
                try:
                    await message.edit_media(
                        media=types.InputMediaDocument(
                            media=file_id,
                            caption=fallback_caption,
                        ),
                        reply_markup=reply_markup,
                    )
                    return True, "document"
                except TelegramBadRequest as document_edit_error:
                    if _is_not_modified_error(document_edit_error):
                        try:
                            await message.edit_reply_markup(reply_markup=reply_markup)
                        except TelegramBadRequest:
                            pass
                        return True, "document"
                    pass
            try:
                await message.answer_video(
                    video=file_id,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                    protect_content=protect_content,
                )
                return True, "video"
            except TelegramBadRequest as second_error:
                if allow_document_fallback:
                    try:
                        await message.answer_document(
                            document=file_id,
                            caption=fallback_caption,
                            reply_markup=reply_markup,
                            protect_content=protect_content,
                        )
                        return True, "document"
                    except TelegramBadRequest as third_error:
                        logger.warning(
                            "Serial media edit/send xato: first=%s second=%s third=%s",
                            first_error,
                            second_error,
                            third_error,
                        )
                        return False, None
                logger.warning(
                    "Serial media edit/send xato: first=%s second=%s",
                    first_error,
                    second_error,
                )
                return False, None

    try:
        await message.answer_video(
            video=file_id,
            caption=caption,
            parse_mode="HTML",
            reply_markup=reply_markup,
            protect_content=protect_content,
        )
        return True, "video"
    except TelegramBadRequest as first_error:
        if not allow_document_fallback:
            return False, None
        try:
            await message.answer_document(
                document=file_id,
                caption=fallback_caption,
                reply_markup=reply_markup,
                protect_content=protect_content,
            )
            return True, "document"
        except TelegramBadRequest as second_error:
            logger.warning(
                "Serial media send xato: first=%s second=%s",
                first_error,
                second_error,
            )
            return False, None


async def _repair_media_file_id(
    *,
    code: str,
    file_id: str,
    bot: Bot,
) -> str | None:
    normalized_code = (code or "").strip()
    normalized_file_id = (file_id or "").strip()
    if not normalized_code or not normalized_file_id:
        return None

    lock = _runtime_repair_locks.setdefault(normalized_code, asyncio.Lock())
    async with lock:
        cached = _runtime_video_preview_cache.get(normalized_code)
        if cached and cached[0] == normalized_file_id:
            return cached[1]

        renewed_video_file_id = await renew_file_id_as_video(
            normalized_code,
            current_bot=bot,
            fallback_file_id=normalized_file_id,
            persist=False,
        )
        if renewed_video_file_id:
            if renewed_video_file_id != normalized_file_id:
                _runtime_video_preview_cache[normalized_code] = (
                    normalized_file_id,
                    renewed_video_file_id,
                )
            return renewed_video_file_id

        if not legacy_media_enabled():
            return None

        return await renew_file_id_from_legacy(
            normalized_code,
            current_bot=bot,
            fallback_file_id=normalized_file_id,
        )


async def _send_original_document_copy(
    message: types.Message,
    *,
    file_id: str,
    protect_content: bool,
) -> None:
    normalized_file_id = (file_id or "").strip()
    if not normalized_file_id:
        return

    try:
        await message.answer_document(
            document=normalized_file_id,
            caption="📎 Asl fayl (siqilmagan)",
            protect_content=protect_content,
        )
    except TelegramBadRequest:
        return


def _timeline_page_payload(
    items: list[tuple[str, str]],
    page: int,
) -> tuple[int, int, list[tuple[str, str]]]:
    total_pages = max(1, (len(items) + SERIAL_HUB_PAGE_SIZE - 1) // SERIAL_HUB_PAGE_SIZE)
    current_page = min(max(page, 0), total_pages - 1)
    start = current_page * SERIAL_HUB_PAGE_SIZE
    return current_page, total_pages, items[start : start + SERIAL_HUB_PAGE_SIZE]


async def _build_serial_timeline(
    *,
    group_code: str,
    group_title: str,
) -> tuple[str, list[SerialTimelineItem], bool]:
    parsed = _parse_season_title(group_title)
    if parsed is None:
        episodes = await get_serial_episodes(group_code)
        items: list[SerialTimelineItem] = [
            (code, episode_number, description, file_id, "episode", None, "")
            for code, episode_number, description, file_id in episodes
        ]
        return group_title, items, False

    base_title, _season_number = parsed
    serial_groups = await get_serial_groups()
    matched_seasons: list[tuple[int, str]] = []
    for serial_code, serial_title, _serial_description in serial_groups:
        season = _parse_season_title(serial_title)
        if season is None:
            continue
        season_base, season_number = season
        if season_base.casefold() != base_title.casefold():
            continue
        matched_seasons.append((season_number, serial_code))

    if not matched_seasons:
        episodes = await get_serial_episodes(group_code)
        items = [
            (code, episode_number, description, file_id, "episode", None, "")
            for code, episode_number, description, file_id in episodes
        ]
        return group_title, items, False

    matched_seasons.sort(key=lambda value: value[0])
    interlude_movies = await get_movies_for_serial_base(base_title, limit=50)
    interlude_items: list[SerialTimelineItem] = []
    for movie_code, movie_title, movie_description, movie_file_id in interlude_movies:
        if _parse_season_title(movie_title):
            continue
        if not movie_title.casefold().startswith(base_title.casefold()):
            continue
        tail = movie_title[len(base_title) :]
        if not tail or tail[0] not in {" ", "-", ":", "|"}:
            continue
        interlude_items.append(
            (
                movie_code,
                0,
                movie_description,
                movie_file_id,
                "movie",
                None,
                _extract_interlude_label(base_title, movie_title),
            )
        )

    timeline_items: list[SerialTimelineItem] = []
    for index, (season_number, serial_code) in enumerate(matched_seasons):
        season_episodes = await get_serial_episodes(serial_code)
        for code, episode_number, description, file_id in season_episodes:
            timeline_items.append(
                (
                    code,
                    episode_number,
                    description,
                    file_id,
                    "episode",
                    season_number,
                    "",
                )
            )

        if index == 0 and interlude_items:
            timeline_items.extend(interlude_items)

    return base_title, timeline_items, True


async def _timeline_hub_reply_markup(
    *,
    bot: Bot,
    user_id: int,
    group_code: str,
    items: list[SerialTimelineItem],
    page: int,
    multi_season: bool,
    season_number: int | None = None,
) -> types.InlineKeyboardMarkup:
    if multi_season and season_number is None:
        root_items = _root_hub_entries(items)
        current_page, total_pages, visible = _timeline_page_payload(root_items, page)
        current_group_ref = group_code
        favorite_group_code = group_code
        back_to_group_code = None
    elif multi_season and season_number is not None:
        season_entries = [
            (item[0], _timeline_item_label(item, multi_season=multi_season))
            for item in _season_timeline_items(items, season_number)
        ]
        current_page, total_pages, visible = _timeline_page_payload(season_entries, page)
        current_group_ref = _build_group_ref(group_code, season_number)
        favorite_group_code = group_code
        back_to_group_code = group_code
    else:
        flat_entries = [
            (item[0], _timeline_item_label(item, multi_season=multi_season))
            for item in items
        ]
        current_page, total_pages, visible = _timeline_page_payload(flat_entries, page)
        current_group_ref = group_code
        favorite_group_code = group_code
        back_to_group_code = None

    is_fav = await is_favorite(user_id, group_code)
    share_query = None
    share_callback_data = _build_share_callback_data(group_code)
    share_button_text = "🔗 Ulashish"
    channel_callback_data = None
    candidate_callback_data = f"{CHANNEL_SHARE_CALLBACK_PREFIX}{group_code}"
    if await is_admin_user(user_id) and len(candidate_callback_data.encode("utf-8")) <= 64:
        channel_callback_data = candidate_callback_data

    return serial_hub_keyboard(
        current_group_ref,
        visible,
        page=current_page,
        total_pages=total_pages,
        is_fav=is_fav,
        favorite_group_code=favorite_group_code,
        back_to_group_code=back_to_group_code,
        back_to_page=0,
        back_text="⬅️ Fasllar",
        share_query=share_query,
        share_callback_data=share_callback_data,
        share_button_text=share_button_text,
        channel_callback_data=channel_callback_data,
    )


async def _send_serial_entry(
    message: types.Message,
    *,
    user_id: int,
    raw_code: str,
    episode_code: str | None = None,
    page: int | None = None,
    edit: bool = False,
) -> bool:
    group = await get_serial_group_for_lookup(raw_code)
    if group is None:
        return False

    group_code, group_title, group_description = group
    protect_content = await _should_protect_content(user_id)
    base_title, timeline_items, multi_season = await _build_serial_timeline(
        group_code=group_code,
        group_title=group_title,
    )
    if not timeline_items:
        return False

    lookup_code = (episode_code or raw_code or "").strip()
    selected_season: int | None = None
    selected_item: SerialTimelineItem | None = None

    if multi_season:
        season_from_token = _parse_season_token(lookup_code)
        if season_from_token is not None:
            selected_season = season_from_token
            selected_item = _season_first_item(timeline_items, season_from_token)
            if selected_item is None:
                selected_season = None

        if selected_item is None:
            selected_item = next(
                (item for item in timeline_items if item[0] == lookup_code),
                None,
            )

        if selected_item is None:
            selected_item = timeline_items[0]

        if (
            episode_code is not None
            and selected_item[4] == "episode"
            and selected_item[5] is not None
            and selected_season is None
        ):
            selected_season = selected_item[5]
    else:
        selected_item = next(
            (item for item in timeline_items if item[0] == lookup_code),
            None,
        )
        if selected_item is None:
            selected_item = timeline_items[0]

    (
        selected_code,
        selected_episode_number,
        selected_description,
        file_id,
        selected_item_type,
        _selected_season_number,
        selected_item_title,
    ) = selected_item
    if page is None:
        if multi_season and selected_season is None:
            root_entries = _root_hub_entries(timeline_items)
            if selected_item[4] == "movie":
                selected_key = selected_item[0]
            else:
                selected_key = (
                    _season_token(selected_item[5])
                    if selected_item[5] is not None
                    else selected_item[0]
                )
            root_index = next(
                (
                    index
                    for index, (entry_code, _entry_label) in enumerate(root_entries)
                    if entry_code == selected_key
                ),
                0,
            )
            current_page = root_index // SERIAL_HUB_PAGE_SIZE
        elif multi_season and selected_season is not None:
            season_items = _season_timeline_items(timeline_items, selected_season)
            season_index = next(
                (
                    index
                    for index, (item_code, *_item_values) in enumerate(season_items)
                    if item_code == selected_code
                ),
                0,
            )
            current_page = season_index // SERIAL_HUB_PAGE_SIZE
        else:
            item_index = next(
                index
                for index, (item_code, *_item_values) in enumerate(timeline_items)
                if item_code == selected_code
            )
            current_page = item_index // SERIAL_HUB_PAGE_SIZE
    else:
        current_page = page

    reply_markup = await _timeline_hub_reply_markup(
        bot=message.bot,
        user_id=user_id,
        group_code=group_code,
        items=timeline_items,
        page=current_page,
        multi_season=multi_season,
        season_number=selected_season,
    )
    if multi_season and selected_season is None:
        visible_total = len(_root_hub_entries(timeline_items))
    elif multi_season and selected_season is not None:
        visible_total = len(_season_timeline_items(timeline_items, selected_season))
    else:
        visible_total = len(timeline_items)

    caption = _timeline_caption(
        base_title=base_title,
        group_code=group_code,
        item=selected_item,
        total_items=max(1, visible_total),
        fallback_description=group_description,
        multi_season=multi_season,
    )

    if selected_item_type == "movie":
        fallback_title = _truncate_text(
            selected_item_title or "Oraliq film",
            SERIAL_INLINE_LIMIT,
        )
        fallback_subtitle = f"Oraliq film • {max(1, visible_total)} kontent"
    else:
        fallback_title = _truncate_text(base_title, SERIAL_INLINE_LIMIT)
        fallback_subtitle = (
            f"{max(1, int(selected_episode_number or 1))}-qism • {max(1, visible_total)} kontent"
        )

    fallback_description = _truncate_text(
        selected_description or group_description,
        760,
    )
    fallback_lines = [f"📺 {fallback_title} {fallback_subtitle}", ""]
    if fallback_description:
        fallback_lines.extend([fallback_description, f"Serial kodi: {group_code}"])
    else:
        fallback_lines.append(f"Serial kodi: {group_code}")
    fallback_caption = _truncate_text("\n".join(fallback_lines), 1024)
    active_file_id = file_id
    sent, media_kind = await _send_serial_media(
        message,
        file_id=active_file_id,
        caption=caption,
        fallback_caption=fallback_caption,
        reply_markup=reply_markup,
        edit=edit,
        protect_content=protect_content,
        allow_document_fallback=False,
    )
    if not sent:
        repaired_file_id = await _repair_media_file_id(
            code=selected_code,
            file_id=active_file_id,
            bot=message.bot,
        )
        if repaired_file_id and repaired_file_id != active_file_id:
            active_file_id = repaired_file_id
            sent, media_kind = await _send_serial_media(
                message,
                file_id=active_file_id,
                caption=caption,
                fallback_caption=fallback_caption,
                reply_markup=reply_markup,
                edit=edit,
                protect_content=protect_content,
                allow_document_fallback=False,
            )
    if not sent:
        sent, media_kind = await _send_serial_media(
            message,
            file_id=active_file_id,
            caption=caption,
            fallback_caption=fallback_caption,
            reply_markup=reply_markup,
            edit=edit,
            protect_content=protect_content,
            allow_document_fallback=True,
        )

    if not sent:
        if edit:
            return False
        await message.answer(
            "📺 Serial topildi, lekin media fayl eskirgan yoki vaqtincha yaroqsiz.\n"
            "Admin media faylni yangilagach, kod normal ishlaydi.",
            reply_markup=reply_markup,
        )
        return True
    if not edit and media_kind == "video" and active_file_id != file_id:
        await _send_original_document_copy(
            message,
            file_id=file_id,
            protect_content=protect_content,
        )
    if media_kind == "document" and not edit:
        await message.answer(
            "ℹ️ Ushbu qism document formatda yuborildi (video emas).",
        )

    await record_movie_view(selected_code, viewer_user_id=user_id)
    await add_history(user_id, group_code)
    return True


async def _send_movie_by_code(
    message: types.Message,
    state: FSMContext | None,
    raw_code: str | None,
    *,
    clear_state: bool = True,
) -> None:
    if not raw_code:
        await message.answer("Iltimos, kod yuboring.")
        return

    code = _extract_lookup_code(raw_code)
    if not code:
        await message.answer("Iltimos, kod yuboring.")
        return

    user_id = message.from_user.id
    protect_content = await _should_protect_content(user_id)
    trial_used = await has_feature_trial_used(user_id, FEATURE_SEARCH)
    if not trial_used:
        await mark_feature_trial_used(user_id, FEATURE_SEARCH)
    else:
        allowed = await ensure_feature_access(message, feature=FEATURE_SEARCH)
        if not allowed:
            return

    try:
        if await _send_serial_entry(message, user_id=user_id, raw_code=code):
            await log_user_search_event(
                user_id=user_id,
                raw_query=raw_code,
                normalized_code=code,
                resolved_code=code,
                result_status="serial",
                content_kind="serial",
            )
            if clear_state and state is not None:
                await state.clear()
            return
    except TelegramBadRequest as error:
        logger.warning(
            "Serial yuborilmadi: user_id=%s code=%s error=%s",
            user_id,
            code,
            error,
        )
        await message.answer(
            "Ushbu serial qismini yuborib bo'lmadi. Iltimos, keyinroq urinib ko'ring."
        )
        await log_user_search_event(
            user_id=user_id,
            raw_query=raw_code,
            normalized_code=code,
            resolved_code=code,
            result_status="error",
            content_kind="serial",
        )
        if clear_state and state is not None:
            await state.clear()
        return

    movie = await get_movie(code)
    if not movie:
        await message.answer("Hech narsa topilmadi.")
        await log_user_search_event(
            user_id=user_id,
            raw_query=raw_code,
            normalized_code=code,
            resolved_code=None,
            result_status="not_found",
            content_kind=None,
        )
        if clear_state and state is not None:
            await state.clear()
        return

    title, description, file_id, content_kind = movie
    is_fav = await is_favorite(user_id, code)
    active_file_id = file_id
    sent_kind: str | None = None
    share_query = _build_inline_share_query(code)

    try:
        sent_kind = await send_movie_card(
            message,
            code,
            title,
            active_file_id,
            content_kind=content_kind,
            description=description,
            favorite_state=is_fav,
            force_video=True,
            share_query=share_query,
            protect_content=protect_content,
        )
    except TelegramBadRequest as error:
        repaired_file_id = await _repair_media_file_id(
            code=code,
            file_id=active_file_id,
            bot=message.bot,
        )
        if repaired_file_id and repaired_file_id != active_file_id:
            active_file_id = repaired_file_id
            try:
                sent_kind = await send_movie_card(
                    message,
                    code,
                    title,
                    active_file_id,
                    content_kind=content_kind,
                    description=description,
                    favorite_state=is_fav,
                    force_video=True,
                    share_query=share_query,
                    protect_content=protect_content,
                )
            except TelegramBadRequest as retry_error:
                try:
                    sent_kind = await send_movie_card(
                        message,
                        code,
                        title,
                        active_file_id,
                        content_kind=content_kind,
                        description=description,
                        favorite_state=is_fav,
                        force_video=False,
                        share_query=share_query,
                        protect_content=protect_content,
                    )
                except TelegramBadRequest as fallback_error:
                    logger.warning(
                        "Kontent yuborilmadi: user_id=%s code=%s first=%s second=%s third=%s",
                        user_id,
                        code,
                        error,
                        retry_error,
                        fallback_error,
                    )
                    await message.answer(
                        "Kontentni yuborib bo'lmadi. Iltimos, keyinroq urinib ko'ring."
                    )
                    await log_user_search_event(
                        user_id=user_id,
                        raw_query=raw_code,
                        normalized_code=code,
                        resolved_code=code,
                        result_status="error",
                        content_kind=content_kind,
                    )
                    if clear_state and state is not None:
                        await state.clear()
                    return
        else:
            try:
                sent_kind = await send_movie_card(
                    message,
                    code,
                    title,
                    active_file_id,
                    content_kind=content_kind,
                    description=description,
                    favorite_state=is_fav,
                    force_video=False,
                    share_query=share_query,
                    protect_content=protect_content,
                )
            except TelegramBadRequest as fallback_error:
                logger.warning(
                    "Kontent yuborilmadi: user_id=%s code=%s first=%s second=%s",
                    user_id,
                    code,
                    error,
                    fallback_error,
                )
                await message.answer(
                    "Kontentni yuborib bo'lmadi. Iltimos, keyinroq urinib ko'ring."
                )
                await log_user_search_event(
                    user_id=user_id,
                    raw_query=raw_code,
                    normalized_code=code,
                    resolved_code=code,
                    result_status="error",
                    content_kind=content_kind,
                )
                if clear_state and state is not None:
                    await state.clear()
                return

    if sent_kind == "video" and active_file_id != file_id:
        await _send_original_document_copy(
            message,
            file_id=file_id,
            protect_content=protect_content,
        )

    await record_movie_view(code, viewer_user_id=user_id)
    await log_user_search_event(
        user_id=user_id,
        raw_query=raw_code,
        normalized_code=code,
        resolved_code=code,
        result_status="movie",
        content_kind=content_kind,
    )
    await add_history(user_id, code)
    if clear_state and state is not None:
        await state.clear()


async def open_shared_content(message: types.Message, raw_code: str | None) -> None:
    await _send_movie_by_code(
        message,
        state=None,
        raw_code=raw_code,
        clear_state=False,
    )


def extract_request_payload(message: types.Message) -> tuple[str | None, str | None]:
    if message.photo:
        return message.caption or "Rasm", message.photo[-1].file_id

    return (message.text.strip() if message.text else None), None


@router.message(
    RequestState.waiting_for_request,
    ~F.text.in_(USER_ACTIONS + ADMIN_ACTIONS),
    ~F.text.startswith("/"),
)
async def send_request(message: types.Message, state: FSMContext) -> None:
    await touch_message_user(message)
    user_id = message.from_user.id
    if message.video:
        await message.answer("So'rov uchun faqat matn yoki rasm yuboring. Video qabul qilinmaydi.")
        return

    text, file_id = extract_request_payload(message)

    if not text and not file_id:
        await message.answer("Matn yoki rasm yuboring.")
        return

    trial_used = await has_feature_trial_used(user_id, FEATURE_REQUEST)
    if not trial_used:
        await mark_feature_trial_used(user_id, FEATURE_REQUEST)
    else:
        allowed = await ensure_feature_access(message, feature=FEATURE_REQUEST)
        if not allowed:
            return

    await add_request(user_id, text, file_id)
    await message.answer("✅ So'rovingiz qabul qilindi.")
    await state.clear()


async def _render_codes_page(
    message: types.Message,
    *,
    page: int,
    title: str,
    subtitle: str | None,
    show_page_meta: bool,
    empty_text: str,
    prefix: str,
    total_items: int,
    codes: list[str],
    edit: bool = False,
) -> None:
    if total_items == 0:
        await send_or_edit_text(message, empty_text, edit=edit, parse_mode="HTML")
        return

    page_total = total_pages(total_items)
    page = min(max(page, 0), page_total - 1)
    offset = page * LIST_PAGE_SIZE
    movies_map = await get_movies_by_codes(codes)

    await send_or_edit_text(
        message,
        render_codes_page_text(
            page_title=title,
            subtitle=subtitle,
            page=page,
            page_total=page_total,
            total_items=total_items,
            offset=offset,
            codes=codes,
            movies_map=movies_map,
            meta_layout="code_first",
            show_page_meta=show_page_meta,
        ),
        reply_markup=collection_keyboard(prefix, page, page_total),
        edit=edit,
        parse_mode="HTML",
    )


async def show_favorites(
    message: types.Message,
    user_id: int,
    *,
    state: FSMContext | None = None,
    page: int = 0,
    edit: bool = False,
) -> None:
    total_items = await count_favorites(user_id)
    empty_text = (
        "❤️ <b>Sevimlilar bo'sh</b>\n\n"
        "<i>Saqlangan kontentlar shu yerda ko'rinadi.</i>"
    )
    if total_items == 0:
        if state is not None:
            await state.clear()
        await send_or_edit_text(message, empty_text, edit=edit, parse_mode="HTML")
        return

    if state is not None:
        await state.set_state(CollectionState.viewing_favorites)
    page = min(max(page, 0), total_pages(total_items) - 1)
    codes = await get_favorites_page(
        user_id,
        limit=LIST_PAGE_SIZE,
        offset=page * LIST_PAGE_SIZE,
    )
    await _render_codes_page(
        message,
        page=page,
        title="❤️ <b>Sevimlilar</b>",
        subtitle=None,
        show_page_meta=False,
        empty_text=empty_text,
        prefix=FAVORITES_PAGE_PREFIX,
        total_items=total_items,
        codes=codes,
        edit=edit,
    )


async def show_history(
    message: types.Message,
    user_id: int,
    *,
    state: FSMContext | None = None,
    page: int = 0,
    edit: bool = False,
) -> None:
    total_items = await count_history(user_id)
    empty_text = (
        "🕘 <b>Tarix bo'sh</b>\n\n"
        "<i>Ko'rilgan kontentlar shu yerda ko'rinadi.</i>"
    )
    if total_items == 0:
        if state is not None:
            await state.clear()
        await send_or_edit_text(message, empty_text, edit=edit, parse_mode="HTML")
        return

    if state is not None:
        await state.set_state(CollectionState.viewing_history)
    page = min(max(page, 0), total_pages(total_items) - 1)
    codes = await get_history_page(
        user_id,
        limit=LIST_PAGE_SIZE,
        offset=page * LIST_PAGE_SIZE,
    )
    await _render_codes_page(
        message,
        page=page,
        title="🕘 <b>Tarix</b>",
        subtitle=None,
        show_page_meta=False,
        empty_text=empty_text,
        prefix=HISTORY_PAGE_PREFIX,
        total_items=total_items,
        codes=codes,
        edit=edit,
    )


@router.callback_query(F.data.startswith(SHARE_CALLBACK_PREFIX))
async def share_content_callback(callback: types.CallbackQuery) -> None:
    await touch_callback_user(callback)
    if callback.message is None:
        await callback.answer()
        return

    raw_code = (callback.data or "")[len(SHARE_CALLBACK_PREFIX) :].strip()
    if not raw_code:
        await callback.answer("Kontent topilmadi", show_alert=True)
        return

    payload = await _build_share_message(bot=callback.bot, raw_code=raw_code)
    if payload is None:
        await callback.answer("Kontent topilmadi", show_alert=True)
        return

    share_text, deep_link = payload
    reply_markup = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="▶️ Botda ko'rish",
                    url=deep_link,
                )
            ]
        ]
    )

    share_payload = await _resolve_share_payload(raw_code)
    if (
        share_payload is not None
        and share_payload[3] == "serial"
        and await is_admin_user(callback.from_user.id)
    ):
        channel_callback_data = None
        channel_code = share_payload[0]
        candidate_callback_data = f"{CHANNEL_SHARE_CALLBACK_PREFIX}{channel_code}"
        if len(candidate_callback_data.encode("utf-8")) <= 64:
            channel_callback_data = candidate_callback_data

        serial_reply_markup = await _build_admin_serial_share_keyboard(
            bot=callback.bot,
            group_code=channel_code,
            deep_link=deep_link,
            channel_callback_data=channel_callback_data,
        )
        if serial_reply_markup is not None:
            reply_markup = serial_reply_markup

    await callback.message.answer(
        share_text,
        parse_mode="HTML",
        disable_web_page_preview=False,
        reply_markup=reply_markup,
    )
    await callback.answer("Ulashish xabari tayyor.")


@router.callback_query(F.data.startswith(CHANNEL_SHARE_CALLBACK_PREFIX))
async def send_serial_share_to_channel(callback: types.CallbackQuery) -> None:
    await touch_callback_user(callback)
    if not await is_admin_user(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return

    raw_code = (callback.data or "")[len(CHANNEL_SHARE_CALLBACK_PREFIX) :].strip()
    if not raw_code:
        await callback.answer("Serial topilmadi", show_alert=True)
        return

    payload = await _resolve_share_payload(raw_code)
    if payload is None or payload[3] != "serial":
        await callback.answer("Serial topilmadi", show_alert=True)
        return

    share_message = await _build_share_message(bot=callback.bot, raw_code=payload[0])
    if share_message is None:
        await callback.answer("Serial topilmadi", show_alert=True)
        return

    share_text, deep_link = share_message
    reply_markup = await _build_admin_serial_share_keyboard(
        bot=callback.bot,
        group_code=payload[0],
        deep_link=deep_link,
    )
    if reply_markup is None:
        await callback.answer("Inline tugmalar tayyorlanmadi", show_alert=True)
        return

    channels = await get_sponsor_channels()
    if not channels:
        await callback.answer("Kanal qo'shilmagan", show_alert=True)
        return

    sent_count = 0
    failed_names: list[str] = []
    for channel in channels:
        chat_id = str(channel.get("id") or "").strip()
        channel_name = str(channel.get("name") or chat_id or "Kanal")
        if not chat_id:
            failed_names.append(channel_name)
            continue
        try:
            if callback.message is not None:
                await callback.bot.copy_message(
                    chat_id=chat_id,
                    from_chat_id=callback.message.chat.id,
                    message_id=callback.message.message_id,
                    reply_markup=reply_markup,
                )
            else:
                await callback.bot.send_message(
                    chat_id=chat_id,
                    text=share_text,
                    parse_mode="HTML",
                    disable_web_page_preview=False,
                    reply_markup=reply_markup,
                )
            sent_count += 1
        except TelegramBadRequest as error:
            logger.warning(
                "Serial share copy kanalga yuborilmadi, text fallback uriniladi: channel=%s code=%s error=%s",
                chat_id,
                payload[0],
                error,
            )
            try:
                await callback.bot.send_message(
                    chat_id=chat_id,
                    text=share_text,
                    parse_mode="HTML",
                    disable_web_page_preview=False,
                    reply_markup=reply_markup,
                )
                sent_count += 1
            except TelegramBadRequest as fallback_error:
                logger.warning(
                    "Serial share kanalga yuborilmadi: channel=%s code=%s error=%s",
                    chat_id,
                    payload[0],
                    fallback_error,
                )
                failed_names.append(channel_name)

    if sent_count and not failed_names:
        await callback.answer(f"{sent_count} ta kanalga yuborildi.")
    elif sent_count:
        await callback.answer(
            f"{sent_count} ta kanalga yuborildi, {len(failed_names)} tasida xato.",
            show_alert=True,
        )
    else:
        await callback.answer(
            "Kanalga yuborilmadi. Bot kanalga admin qilinganini tekshiring.",
            show_alert=True,
        )


@router.callback_query(F.data.startswith(f"{FAVORITES_PAGE_PREFIX}_"))
async def favorites_page(
    callback: types.CallbackQuery,
    state: FSMContext,
) -> None:
    await touch_callback_user(callback)
    if callback.message is None:
        await callback.answer()
        return

    await show_favorites(
        callback.message,
        callback.from_user.id,
        state=state,
        page=parse_page(callback.data),
        edit=True,
    )
    await callback.answer()


@router.callback_query(F.data.startswith(f"{HISTORY_PAGE_PREFIX}_"))
async def history_page(callback: types.CallbackQuery, state: FSMContext) -> None:
    await touch_callback_user(callback)
    if callback.message is None:
        await callback.answer()
        return

    await show_history(
        callback.message,
        callback.from_user.id,
        state=state,
        page=parse_page(callback.data),
        edit=True,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("shub:"))
async def serial_hub_page(callback: types.CallbackQuery) -> None:
    await touch_callback_user(callback)
    if callback.message is None:
        await callback.answer()
        return

    try:
        _prefix, group_ref, page_text = callback.data.split(":", 2)
        page = max(0, int(page_text))
    except ValueError:
        await callback.answer("Bo'limni qayta oching", show_alert=True)
        return

    group_code, season_number = _parse_group_ref(group_ref)

    group = await get_serial_group_for_lookup(group_code)
    if group is None:
        await callback.answer("Serial topilmadi", show_alert=True)
        return

    _group_code, group_title, _group_description = group
    _base_title, timeline_items, multi_season = await _build_serial_timeline(
        group_code=group_code,
        group_title=group_title,
    )
    if not timeline_items:
        await callback.answer("Serial topilmadi", show_alert=True)
        return

    if season_number is not None and not _season_timeline_items(
        timeline_items, season_number
    ):
        season_number = None

    reply_markup = await _timeline_hub_reply_markup(
        bot=callback.bot,
        user_id=callback.from_user.id,
        group_code=group_code,
        items=timeline_items,
        page=page,
        multi_season=multi_season,
        season_number=season_number,
    )
    try:
        await callback.message.edit_reply_markup(reply_markup=reply_markup)
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("sepi:"))
async def serial_episode_open(callback: types.CallbackQuery) -> None:
    await touch_callback_user(callback)
    if callback.message is None:
        await callback.answer()
        return

    try:
        _prefix, group_ref, episode_code, page_text = callback.data.split(":", 3)
        page = max(0, int(page_text))
    except ValueError:
        await callback.answer("Qism topilmadi", show_alert=True)
        return

    group_code, _season_number = _parse_group_ref(group_ref)

    group = await get_serial_group_for_lookup(group_code)
    if group is None:
        await callback.answer("Qism topilmadi", show_alert=True)
        return

    _group_code, group_title, _group_description = group
    _base_title, timeline_items, _multi_season = await _build_serial_timeline(
        group_code=group_code,
        group_title=group_title,
    )
    if not timeline_items:
        await callback.answer("Qism topilmadi", show_alert=True)
        return

    target_code = episode_code
    season_from_token = _parse_season_token(episode_code)
    if season_from_token is not None:
        season_item = _season_first_item(timeline_items, season_from_token)
        if season_item is None:
            await callback.answer("Qism topilmadi", show_alert=True)
            return
        target_code = season_item[0]
        page = 0

    selected_index = next(
        (
            index
            for index, (item_code, *_item_values) in enumerate(timeline_items)
            if item_code == target_code
        ),
        None,
    )
    if selected_index is None:
        await callback.answer("Qism topilmadi", show_alert=True)
        return

    if selected_index > 0:
        allowed = await ensure_feature_callback_access(
            callback,
            feature=FEATURE_SEARCH,
        )
        if not allowed:
            return

    try:
        ok = await _send_serial_entry(
            callback.message,
            user_id=callback.from_user.id,
            raw_code=group_code,
            episode_code=episode_code,
            page=page,
            edit=True,
        )
    except TelegramBadRequest as error:
        logger.warning(
            "Qism ochilmadi: user_id=%s group=%s episode=%s error=%s",
            callback.from_user.id,
            group_code,
            episode_code,
            error,
        )
        await callback.answer(
            "Qismni ochib bo'lmadi. Iltimos, qayta urinib ko'ring.",
            show_alert=True,
        )
        return

    if not ok:
        await callback.answer(
            "Qism topildi, lekin media yuborilmadi.",
            show_alert=True,
        )
        return

    await callback.answer()


@router.callback_query(F.data == "noop")
async def noop_callback(callback: types.CallbackQuery) -> None:
    await callback.answer()


@router.message(
    StateFilter(
        CollectionState.viewing_favorites,
        CollectionState.viewing_history,
    ),
    F.text & ~F.text.startswith("/") & ~F.text.in_(USER_ACTIONS + ADMIN_ACTIONS),
)
async def collection_code_search(message: types.Message, state: FSMContext) -> None:
    await touch_message_user(message)
    raw_code = _extract_lookup_code(message.text)

    if not raw_code:
        await message.answer("Kod yuboring.")
        return

    user_id = message.from_user.id
    current_state = await state.get_state()
    if current_state == CollectionState.viewing_favorites.state:
        rows = await get_favorites(user_id)
        not_found_text = (
            "❤️ <b>Sevimlilar ichida topilmadi</b>\n\n"
            "<i>Faqat sevimlilardagi kodlarni yuboring.</i>"
        )
    else:
        rows = await get_history(user_id)
        not_found_text = (
            "🕘 <b>Tarix ichida topilmadi</b>\n\n"
            "<i>Faqat tarixdagi kodlarni yuboring.</i>"
        )

    allowed_codes = {code for (code,) in rows}
    serial_group = await get_serial_group_for_lookup(raw_code)
    effective_code = serial_group[0] if serial_group is not None else raw_code

    if effective_code not in allowed_codes:
        await message.answer(not_found_text, parse_mode="HTML")
        return

    await _send_movie_by_code(message, state, raw_code, clear_state=False)


async def show_help(message: types.Message) -> None:
    await message.answer(help_text(), parse_mode="HTML")


