import re
from html import escape
from urllib.parse import quote

from aiogram import Bot, Router, types
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
)

from repositories.content import get_movie, get_serial_group_for_lookup, search_movies_by_text

router = Router()
CODE_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]+")
SHARE_QUERY_PREFIX = "share:"
DEEP_LINK_WATCH_PREFIX = "watch_"
_bot_username_cache: str | None = None


def _extract_lookup_code(raw_text: str | None) -> str:
    value = (raw_text or "").strip()
    if not value:
        return ""

    tokens = CODE_TOKEN_RE.findall(value)
    if not tokens:
        return value

    if len(tokens) == 1:
        return tokens[0]

    numeric_tokens = [token for token in tokens if token.isdigit()]
    if numeric_tokens:
        return max(numeric_tokens, key=len)

    return tokens[0]


def _truncate_text(value: str | None, limit: int) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 3)].rstrip() + "..."


async def _bot_username(bot: Bot) -> str | None:
    global _bot_username_cache
    if _bot_username_cache:
        return _bot_username_cache

    me = await bot.get_me()
    username = (me.username or "").strip()
    if not username:
        return None

    _bot_username_cache = username
    return username


def _content_icon(kind: str) -> str:
    return "📺" if kind == "serial" else "🎬"


async def _build_share_result(
    *,
    bot: Bot,
    raw_code: str,
) -> InlineQueryResultArticle | None:
    code = _extract_lookup_code(raw_code)
    if not code:
        return None

    username = await _bot_username(bot)
    if not username:
        return None

    deep_link = f"https://t.me/{username}?start={DEEP_LINK_WATCH_PREFIX}{quote(code, safe='')}"

    content_kind = "movie"
    content = await get_movie(code)
    if content is not None:
        title, description, _file_id, detected_kind = content
        content_kind = "serial" if detected_kind == "serial" else "movie"
    else:
        serial = await get_serial_group_for_lookup(code)
        if serial is None:
            return None
        code, title, description = serial
        content_kind = "serial"
        deep_link = f"https://t.me/{username}?start={DEEP_LINK_WATCH_PREFIX}{quote(code, safe='')}"

    clean_title = _truncate_text(title, 90)
    clean_description = _truncate_text(description, 280)
    icon = _content_icon(content_kind)
    message_lines = [f"{icon} <b>{escape(clean_title)}</b>"]
    if clean_description:
        message_lines.extend(["", escape(clean_description)])

    return InlineQueryResultArticle(
        id=f"share:{code}",
        title=f"Ulashish: {clean_title}",
        description=_truncate_text(clean_description or f"Kod: {code}", 90),
        input_message_content=InputTextMessageContent(
            message_text="\n".join(message_lines),
            parse_mode="HTML",
            disable_web_page_preview=True,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="▶️ Botda ko'rish", url=deep_link)]
            ]
        ),
    )


@router.inline_query()
async def inline_search(inline_query: types.InlineQuery, bot: Bot) -> None:
    query = inline_query.query.strip()

    if query.startswith(SHARE_QUERY_PREFIX):
        raw_code = query[len(SHARE_QUERY_PREFIX) :].strip()
        share_result = await _build_share_result(bot=bot, raw_code=raw_code)
        if share_result is None:
            await inline_query.answer([], cache_time=5, is_personal=True)
            return
        await inline_query.answer([share_result], cache_time=5, is_personal=True)
        return

    if not query or len(query) < 2:
        await inline_query.answer([], cache_time=300)
        return

    movies = await search_movies_by_text(query, limit=10)

    results = []
    for code, title, description, _file_id in movies:
        preview = description or f"Kod: {code}"
        results.append(
            InlineQueryResultArticle(
                id=str(code),
                title=title,
                description=preview[:80],
                input_message_content=InputTextMessageContent(message_text=f"{code}"),
            )
        )

    await inline_query.answer(results, cache_time=60, is_personal=False)
