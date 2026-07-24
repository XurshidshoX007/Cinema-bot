"""Presentation and parsing helpers for sponsor channels."""

import logging

logger = logging.getLogger(__name__)

from aiogram import types

from database import get_sponsor_channels

CHANNEL_INPUT_EXAMPLE = "<pre>@kanal_id https://t.me/link Kanal nomi</pre>"
CHANNEL_INPUT_ERROR_TEXT = "Format noto'g'ri.\n" f"{CHANNEL_INPUT_EXAMPLE}"


def normalize_channel_id(raw_value: str) -> str:
    value = (raw_value or "").strip()
    if not value:
        return value

    if value.startswith("https://t.me/"):
        value = "@" + value.rsplit("/", 1)[-1].strip()

    if value.startswith("@"):
        tail = value[1:]
        if tail.isdigit():
            # @100... formatini Telegram chat id formatiga o'tkazamiz.
            return f"-{tail}" if tail.startswith("100") else tail
        return value

    if value.lstrip("-").isdigit():
        if value.startswith("100"):
            return f"-{value}"
        return value

    return value


def parse_channel_submission(text: str | None) -> tuple[str, str, str] | None:
    if not text:
        return None

    parts = text.split(maxsplit=2)
    if len(parts) < 3:
        return None

    return normalize_channel_id(parts[0]), parts[1], parts[2]


def is_valid_channel_url(url: str) -> bool:
    return url.startswith("http://") or url.startswith("https://")


def build_channels_markup(
    channels: list[dict[str, str]],
) -> types.InlineKeyboardMarkup | None:
    buttons = [
        [
            types.InlineKeyboardButton(
                text=f"🗑 {channel['name']}",
                callback_data=f"del_channel:{channel['id']}",
            )
        ]
        for channel in channels
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None


def build_channels_text(channels: list[dict[str, str]]) -> str:
    lines = ["📡 <b>Kanallar</b>", "<i>Majburiy obuna ro'yxati</i>", ""]
    if not channels:
        lines.extend(["Hozircha kanal qo'shilmagan.", ""])
    else:
        for index, channel in enumerate(channels, start=1):
            lines.append(
                f"<b>{index}.</b> {channel['name']}\n"
                f"ID: <code>{channel['id']}</code>\n"
                f"Link: {channel['url']}"
            )
            lines.append("")

    lines.extend(["➕ <b>Yangi kanal</b>", CHANNEL_INPUT_EXAMPLE])
    return "\n".join(lines)


async def build_channels_menu_view() -> tuple[str, types.InlineKeyboardMarkup | None]:
    channels = await get_sponsor_channels()
    return build_channels_text(channels), build_channels_markup(channels)
