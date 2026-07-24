"""Helpers for restoring media from a previous bot token."""

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import Message

from config import ADMIN_ID, LEGACY_BOT_TOKEN, LEGACY_MEDIA_BRIDGE_CHAT_ID
from database import get_movie, update_movie_file_id

logger = logging.getLogger(__name__)
_legacy_bot: Bot | None = None
_repair_locks: dict[str, asyncio.Lock] = {}


def legacy_media_enabled() -> bool:
    return bool((LEGACY_BOT_TOKEN or "").strip())


def legacy_bridge_chat_id() -> int | str | None:
    raw_value = (LEGACY_MEDIA_BRIDGE_CHAT_ID or "").strip()
    if not raw_value:
        return None

    if raw_value.lstrip("-").isdigit():
        return int(raw_value)

    return raw_value


def legacy_bridge_enabled() -> bool:
    return legacy_media_enabled() and legacy_bridge_chat_id() is not None


def _legacy_bot_instance() -> Bot | None:
    global _legacy_bot

    if not legacy_media_enabled():
        return None

    if _legacy_bot is None:
        _legacy_bot = Bot(token=(LEGACY_BOT_TOKEN or "").strip())

    return _legacy_bot


def _legacy_file_url(file_path: str) -> str:
    token = (LEGACY_BOT_TOKEN or "").strip()
    return f"https://api.telegram.org/file/bot{token}/{file_path.lstrip('/')}"


def _current_file_url(token: str, file_path: str) -> str:
    return f"https://api.telegram.org/file/bot{token}/{file_path.lstrip('/')}"


def _resolved_file_id(message: Message | None) -> str:
    if message is None:
        return ""

    if message.video:
        return (message.video.file_id or "").strip()

    if message.document:
        return (message.document.file_id or "").strip()

    return ""


async def close_legacy_bot() -> None:
    global _legacy_bot
    if _legacy_bot is None:
        return

    await _legacy_bot.session.close()
    _legacy_bot = None


async def _import_via_legacy_file_url(
    *,
    legacy_bot: Bot,
    current_bot: Bot,
    file_id: str,
) -> str | None:
    for _attempt in range(6):
        imported_message: Message | None = None

        try:
            legacy_file = await legacy_bot.get_file(file_id)
        except TelegramRetryAfter as error:
            await asyncio.sleep(max(1, int(error.retry_after or 1)) + 1)
            continue
        except TelegramBadRequest:
            return None

        if not legacy_file.file_path:
            return None

        source_url = _legacy_file_url(legacy_file.file_path)

        try:
            try:
                imported_message = await current_bot.send_video(
                    chat_id=ADMIN_ID,
                    video=source_url,
                    disable_notification=True,
                )
            except (TelegramBadRequest, TelegramForbiddenError):
                imported_message = await current_bot.send_document(
                    chat_id=ADMIN_ID,
                    document=source_url,
                    disable_notification=True,
                )
        except TelegramRetryAfter as error:
            await asyncio.sleep(max(1, int(error.retry_after or 1)) + 1)
            continue
        except (TelegramBadRequest, TelegramForbiddenError):
            return None
        finally:
            if imported_message is not None:
                try:
                    await current_bot.delete_message(
                        chat_id=ADMIN_ID,
                        message_id=imported_message.message_id,
                    )
                except TelegramBadRequest:
                    pass

        resolved = _resolved_file_id(imported_message)
        return resolved or None

    return None


async def _import_via_bridge_chat(
    *,
    legacy_bot: Bot,
    current_bot: Bot,
    file_id: str,
) -> str | None:
    bridge_chat = legacy_bridge_chat_id()
    if bridge_chat is None:
        return None

    first_error: TelegramBadRequest | TelegramForbiddenError | None = None
    second_error: TelegramBadRequest | TelegramForbiddenError | None = None

    for _attempt in range(8):
        legacy_message: Message | None = None
        forwarded_message: Message | None = None

        try:
            try:
                legacy_message = await legacy_bot.send_video(
                    chat_id=bridge_chat,
                    video=file_id,
                    disable_notification=True,
                )
            except (TelegramBadRequest, TelegramForbiddenError) as error:
                first_error = error
                legacy_message = await legacy_bot.send_document(
                    chat_id=bridge_chat,
                    document=file_id,
                    disable_notification=True,
                )

            forwarded_message = await current_bot.forward_message(
                chat_id=ADMIN_ID,
                from_chat_id=bridge_chat,
                message_id=legacy_message.message_id,
                disable_notification=True,
            )

            resolved = _resolved_file_id(forwarded_message)
            return resolved or None
        except TelegramRetryAfter as error:
            await asyncio.sleep(max(1, int(error.retry_after or 1)) + 1)
            continue
        except (TelegramBadRequest, TelegramForbiddenError) as error:
            second_error = error
            logger.warning(
                "Bridge media import xato: bridge=%s first=%s second=%s",
                bridge_chat,
                first_error,
                second_error,
            )
            return None
        finally:
            if forwarded_message is not None:
                try:
                    await current_bot.delete_message(
                        chat_id=ADMIN_ID,
                        message_id=forwarded_message.message_id,
                    )
                except TelegramBadRequest:
                    pass

            if legacy_message is not None:
                try:
                    await legacy_bot.delete_message(
                        chat_id=bridge_chat,
                        message_id=legacy_message.message_id,
                    )
                except TelegramBadRequest:
                    pass

    return None


async def promote_file_id_to_video(
    *,
    current_bot: Bot,
    file_id: str,
    chat_id: int | str = ADMIN_ID,
) -> str | None:
    normalized_file_id = (file_id or "").strip()
    if not normalized_file_id:
        return None

    first_error: TelegramBadRequest | TelegramForbiddenError | None = None
    second_error: TelegramBadRequest | TelegramForbiddenError | None = None

    for _attempt in range(6):
        imported_message: Message | None = None

        try:
            try:
                imported_message = await current_bot.send_video(
                    chat_id=chat_id,
                    video=normalized_file_id,
                    disable_notification=True,
                    supports_streaming=True,
                )
            except (TelegramBadRequest, TelegramForbiddenError) as error:
                first_error = error
                current_file = await current_bot.get_file(normalized_file_id)
                if not current_file.file_path:
                    return None

                source_url = _current_file_url(current_bot.token, current_file.file_path)
                imported_message = await current_bot.send_video(
                    chat_id=chat_id,
                    video=source_url,
                    disable_notification=True,
                    supports_streaming=True,
                )
        except TelegramRetryAfter as error:
            await asyncio.sleep(max(1, int(error.retry_after or 1)) + 1)
            continue
        except (TelegramBadRequest, TelegramForbiddenError) as error:
            second_error = error
            logger.warning(
                "Media video formatga ko'tarilmadi: first=%s second=%s",
                first_error,
                second_error,
            )
            return None
        finally:
            if imported_message is not None:
                try:
                    await current_bot.delete_message(
                        chat_id=chat_id,
                        message_id=imported_message.message_id,
                    )
                except TelegramBadRequest:
                    pass

        if imported_message is None:
            continue

        if imported_message.video and imported_message.video.file_id:
            return imported_message.video.file_id.strip()

        resolved = _resolved_file_id(imported_message)
        return resolved or None

    return None


async def renew_file_id_as_video(
    code: str,
    *,
    current_bot: Bot,
    fallback_file_id: str | None = None,
    persist: bool = True,
) -> str | None:
    normalized_code = (code or "").strip()
    if not normalized_code:
        return None

    lock = _repair_locks.setdefault(normalized_code, asyncio.Lock())
    async with lock:
        current_movie = await get_movie(normalized_code)
        current_file_id = (
            current_movie[2] if current_movie is not None else (fallback_file_id or "")
        ).strip()
        if not current_file_id:
            return None

        if fallback_file_id and fallback_file_id.strip():
            if current_file_id != fallback_file_id.strip():
                return current_file_id

        resolved_file_id = await promote_file_id_to_video(
            current_bot=current_bot,
            file_id=current_file_id,
        )
        if not resolved_file_id:
            return None

        if resolved_file_id == current_file_id:
            return current_file_id

        if not persist:
            return resolved_file_id

        updated = await update_movie_file_id(normalized_code, resolved_file_id)
        if not updated:
            return None

        logger.info("Media video formatga yangilandi: code=%s", normalized_code)
        return resolved_file_id


async def renew_file_id_from_legacy(
    code: str,
    *,
    current_bot: Bot,
    fallback_file_id: str | None = None,
) -> str | None:
    normalized_code = (code or "").strip()
    if not normalized_code:
        return None

    legacy_bot = _legacy_bot_instance()
    if legacy_bot is None:
        return None

    lock = _repair_locks.setdefault(normalized_code, asyncio.Lock())
    async with lock:
        current_movie = await get_movie(normalized_code)
        current_file_id = (
            current_movie[2] if current_movie is not None else (fallback_file_id or "")
        ).strip()
        if not current_file_id:
            return None

        if fallback_file_id and fallback_file_id.strip():
            if current_file_id != fallback_file_id.strip():
                return current_file_id

        resolved_file_id = await _import_via_legacy_file_url(
            legacy_bot=legacy_bot,
            current_bot=current_bot,
            file_id=current_file_id,
        )
        if not resolved_file_id:
            resolved_file_id = await _import_via_bridge_chat(
                legacy_bot=legacy_bot,
                current_bot=current_bot,
                file_id=current_file_id,
            )

        if not resolved_file_id:
            return None

        updated = await update_movie_file_id(normalized_code, resolved_file_id)
        if not updated:
            return None

        logger.info("Legacy media yangilandi: code=%s", normalized_code)
        return resolved_file_id
