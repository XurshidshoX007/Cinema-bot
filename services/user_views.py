"""Presentation helpers for user-facing content flows."""

from html import escape

from aiogram import types
from aiogram.exceptions import TelegramBadRequest

from keyboards import movie_buttons

LIST_PAGE_SIZE = 10
SERIAL_HUB_PAGE_SIZE = 12
TEXT_ITEM_LIMIT = 160
CAPTION_TITLE_LIMIT = 120
CAPTION_DESCRIPTION_LIMIT = 760


def _truncate_text(value: str | None, limit: int) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def content_kind_icon(content_kind: str) -> str:
    return "📺" if content_kind == "serial" else "🎬"


def content_kind_label(content_kind: str) -> str:
    return "Serial" if content_kind == "serial" else "Kino"


def parse_page(data: str) -> int:
    try:
        return max(0, int(data.rsplit("_", 1)[1]))
    except (IndexError, ValueError):
        return 0


def total_pages(total_items: int) -> int:
    return max(1, (total_items + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)


def collection_keyboard(
    prefix: str, page: int, page_total: int
) -> types.InlineKeyboardMarkup | None:
    if page_total <= 1:
        return None

    buttons: list[types.InlineKeyboardButton] = []
    if page > 0:
        buttons.append(
            types.InlineKeyboardButton(
                text="‹ Oldingi", callback_data=f"{prefix}_{page - 1}"
            )
        )
    buttons.append(
        types.InlineKeyboardButton(
            text=f"{page + 1}/{page_total}", callback_data="noop"
        )
    )
    if page + 1 < page_total:
        buttons.append(
            types.InlineKeyboardButton(
                text="Keyingi ›", callback_data=f"{prefix}_{page + 1}"
            )
        )

    return types.InlineKeyboardMarkup(inline_keyboard=[buttons])


async def send_or_edit_text(
    message: types.Message,
    text: str,
    *,
    reply_markup=None,
    edit: bool = False,
    parse_mode: str | None = None,
) -> None:
    if edit:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        return

    await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)


def collection_item_text(
    *,
    index: int,
    code: str,
    movie: tuple[str, str, str, str] | None,
    meta_layout: str = "kind_first",
) -> str:
    safe_code = escape(code)
    if movie is None:
        return f"<b>{index}.</b> <code>{safe_code}</code>"

    title = escape(_truncate_text(movie[0], TEXT_ITEM_LIMIT))
    content_kind = movie[3]
    meta_text = (
        f"<code>{safe_code}</code> • {content_kind_label(content_kind)}"
        if meta_layout == "code_first"
        else f"{content_kind_label(content_kind)} • <code>{safe_code}</code>"
    )
    return f"<b>{index}.</b> {title}\n{meta_text}"


def render_codes_page_text(
    *,
    page_title: str,
    subtitle: str | None = None,
    page: int,
    page_total: int,
    total_items: int,
    offset: int,
    codes: list[str],
    movies_map: dict[str, tuple[str, str, str, str]],
    meta_layout: str = "kind_first",
    show_page_meta: bool = True,
) -> str:
    lines = [page_title]
    if subtitle:
        lines.append(f"<i>{subtitle}</i>")
    if show_page_meta:
        lines.append(f"<i>{total_items} ta • {page + 1}/{page_total}</i>")
    lines.append("")

    for index, code in enumerate(codes, start=offset + 1):
        lines.append(
            collection_item_text(
                index=index,
                code=code,
                movie=movies_map.get(code),
                meta_layout=meta_layout,
            )
        )
        lines.append("")

    return "\n".join(lines).rstrip()


def help_text() -> str:
    return (
        "❓ <b>Yordam</b>\n"
        "<i>Botdan qulay foydalanish uchun asosiy bo'limlar</i>\n\n"
        "🔎 <b>Qidiruv</b>\n"
        "Kino yoki serialni kod orqali bir zumda toping.\n\n"
        "✍️ <b>So'rov</b>\n"
        "Kerakli kontent topilmasa, adminga so'rov qoldiring.\n\n"
        "❤️ <b>Sevimlilar</b>\n"
        "Yoqgan kontentlaringizni saqlab, istalgan payt qayta oching.\n\n"
        "🕘 <b>Tarix</b>\n"
        "Avval ko'rilgan kontentlarni osongina qayta toping."
    )


def render_serial_hub_text(
    *,
    title: str,
    code: str,
    description: str | None,
    total_episodes: int,
    page: int,
    page_total: int,
    visible_from: int,
    visible_to: int,
) -> str:
    safe_title = escape(_truncate_text(title, CAPTION_TITLE_LIMIT))
    safe_code = escape(code)
    safe_description = escape(_truncate_text(description, CAPTION_DESCRIPTION_LIMIT))

    lines = [
        f"{content_kind_icon('serial')} <b>{safe_title}</b>",
        "",
        "<i>Serial markazi</i>",
        f"<i>Serial kodi</i>: <code>{safe_code}</code> • {total_episodes} qism",
    ]

    if page_total > 1:
        lines.append(f"<i>{visible_from}-{visible_to}-qismlar • {page + 1}/{page_total}</i>")

    if safe_description:
        lines.extend(["", safe_description])

    lines.extend(["", "<i>Pastdagi tugmalar orqali qismni tanlang.</i>"])
    return "\n".join(lines)


async def send_movie_card(
    message: types.Message,
    code: str,
    title: str,
    file_id: str,
    *,
    content_kind: str = "movie",
    description: str | None = None,
    favorite_state: bool | None = None,
    force_video: bool = False,
    share_url: str | None = None,
    share_query: str | None = None,
    share_callback_data: str | None = None,
    protect_content: bool = True,
) -> str:
    safe_title = escape(_truncate_text(title, CAPTION_TITLE_LIMIT))
    safe_code = escape(code)
    safe_description = escape(_truncate_text(description, CAPTION_DESCRIPTION_LIMIT))
    code_label = f"{content_kind_label(content_kind)} kodi"

    caption_lines = [
        f"{content_kind_icon(content_kind)} <b>{safe_title}</b>",
        f"<i>{code_label}</i>: <code>{safe_code}</code>",
    ]

    if safe_description:
        caption_lines.extend(["", "", safe_description])

    reply_markup = None
    if favorite_state is not None:
        reply_markup = movie_buttons(
            code,
            is_fav=favorite_state,
            share_url=share_url,
            share_query=share_query,
            share_callback_data=share_callback_data,
        )

    final_caption = _truncate_text("\n".join(caption_lines), 1024)
    try:
        await message.answer_video(
            video=file_id,
            caption=final_caption,
            parse_mode="HTML",
            reply_markup=reply_markup,
            protect_content=protect_content,
        )
        return "video"
    except TelegramBadRequest:
        if force_video:
            raise
        await message.answer_document(
            document=file_id,
            caption=final_caption,
            parse_mode="HTML",
            reply_markup=reply_markup,
            protect_content=protect_content,
        )
        return "document"
