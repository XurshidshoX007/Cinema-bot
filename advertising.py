import asyncio
import logging
from contextlib import suppress
from typing import Any

logger = logging.getLogger(__name__)

from aiogram import Bot, types
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)

from database import (
    claim_ad_for_cleanup,
    finish_ad_broadcast,
    finish_ad_cleanup,
    get_ad_campaign,
    get_ad_delete_batch,
    get_ad_status,
    get_ads_by_status,
    get_pending_ad_recipients,
    mark_user_blocked,
    record_ad_delete_batch,
    record_ad_delivery_batch,
)

AD_BROADCAST_BATCH_SIZE = 25
AD_BROADCAST_INTERVAL_SECONDS = 0.05
AD_DELETE_BATCH_SIZE = 50
AD_DELETE_INTERVAL_SECONDS = 0.03
AD_MAINTENANCE_INTERVAL_SECONDS = 15

_background_tasks: set[asyncio.Task[Any]] = set()
_broadcasting_ads: set[int] = set()
_cleanup_lock = asyncio.Lock()


def extract_ad_payload(message: types.Message) -> dict[str, str | None] | None:
    if message.video:
        return {
            "content_type": "video",
            "text": message.caption or None,
            "file_id": message.video.file_id,
        }

    if message.photo:
        return {
            "content_type": "photo",
            "text": message.caption or None,
            "file_id": message.photo[-1].file_id,
        }

    if message.document:
        return {
            "content_type": "document",
            "text": message.caption or None,
            "file_id": message.document.file_id,
        }

    if message.text and message.text.strip():
        return {
            "content_type": "text",
            "text": message.text.strip(),
            "file_id": None,
        }

    return None


def schedule_ad_broadcast(
    bot: Bot,
    ad_id: int,
    logger: logging.Logger | None = None,
) -> bool:
    if ad_id in _broadcasting_ads:
        return False

    _broadcasting_ads.add(ad_id)
    task = asyncio.create_task(_broadcast_ad_task(bot, ad_id, logger))
    _background_tasks.add(task)
    task.add_done_callback(lambda finished_task: _finalize_task(finished_task, ad_id))
    return True


def _finalize_task(task: asyncio.Task[Any], ad_id: int | None = None) -> None:
    _background_tasks.discard(task)

    if ad_id is not None:
        _broadcasting_ads.discard(ad_id)

    with suppress(asyncio.CancelledError, Exception):
        task.result()


async def shutdown_ad_tasks() -> None:
    tasks = list(_background_tasks)
    for task in tasks:
        task.cancel()

    for task in tasks:
        with suppress(asyncio.CancelledError):
            await task

    _background_tasks.clear()
    _broadcasting_ads.clear()


async def run_ad_maintenance(
    bot: Bot,
    stop_event: asyncio.Event,
    logger: logging.Logger,
) -> None:
    while not stop_event.is_set():
        try:
            await _resume_pending_broadcasts(bot, logger)
            await _process_cleanup_queue(bot, logger)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Reklama worker kutilmaganda xatoga uchradi")

        try:
            await asyncio.wait_for(
                stop_event.wait(), timeout=AD_MAINTENANCE_INTERVAL_SECONDS
            )
        except asyncio.TimeoutError:
            continue


async def _resume_pending_broadcasts(bot: Bot, logger: logging.Logger) -> None:
    for ad in await get_ads_by_status(("broadcasting", "stop_requested"), limit=5):
        schedule_ad_broadcast(bot, ad["id"], logger)


async def _broadcast_ad_task(
    bot: Bot,
    ad_id: int,
    logger: logging.Logger | None,
) -> None:
    try:
        campaign = await get_ad_campaign(ad_id)
        if campaign is None:
            return

        while True:
            status = await get_ad_status(ad_id)
            if status not in {"broadcasting", "stop_requested"}:
                break

            if status == "stop_requested":
                break

            user_ids = await get_pending_ad_recipients(
                ad_id, limit=AD_BROADCAST_BATCH_SIZE
            )
            if not user_ids:
                break

            batch_results: list[tuple[int, int | None, str, str | None]] = []
            stop_requested = False

            for index, user_id in enumerate(user_ids, start=1):
                if index % 5 == 0 and await get_ad_status(ad_id) == "stop_requested":
                    stop_requested = True
                    break

                batch_results.append(await _send_ad_to_user(bot, campaign, user_id))
                await asyncio.sleep(AD_BROADCAST_INTERVAL_SECONDS)

            if batch_results:
                await record_ad_delivery_batch(ad_id, batch_results)

            if stop_requested:
                break

        await finish_ad_broadcast(ad_id)

        campaign = await get_ad_campaign(ad_id)
        if campaign is not None:
            await _notify_admin_broadcast_result(bot, campaign)
    except asyncio.CancelledError:
        raise
    except Exception:
        if logger is not None:
            logger.exception("Reklama broadcast xatosi: #%s", ad_id)


async def _send_ad_to_user(
    bot: Bot,
    campaign: dict[str, Any],
    user_id: int,
) -> tuple[int, int | None, str, str | None]:
    for attempt in range(3):
        try:
            message = await _send_campaign_message(bot, user_id, campaign)
            return (user_id, message.message_id, "sent", None)
        except TelegramRetryAfter as error:
            await asyncio.sleep(float(error.retry_after) + 0.5)
        except TelegramForbiddenError as error:
            await mark_user_blocked(user_id)
            return (user_id, None, "failed_send", _format_error(error))
        except TelegramBadRequest as error:
            return (user_id, None, "failed_send", _format_error(error))
        except TelegramNetworkError as error:
            if attempt == 2:
                return (user_id, None, "failed_send", _format_error(error))
            await asyncio.sleep(1.5 + attempt)
        except Exception as error:
            return (user_id, None, "failed_send", _format_error(error))

    return (user_id, None, "failed_send", "retry_exhausted")


async def _send_campaign_message(
    bot: Bot, user_id: int, campaign: dict[str, Any]
) -> types.Message:
    content_type = campaign["content_type"]
    text = campaign["text"]
    file_id = campaign["file_id"]

    if content_type == "text":
        return await bot.send_message(user_id, text or "")

    if content_type == "photo" and file_id:
        return await bot.send_photo(user_id, photo=file_id, caption=text)

    if content_type == "video" and file_id:
        return await bot.send_video(user_id, video=file_id, caption=text)

    if content_type == "document" and file_id:
        return await bot.send_document(user_id, document=file_id, caption=text)

    raise ValueError(f"Unsupported ad content type: {content_type}")


async def _process_cleanup_queue(bot: Bot, logger: logging.Logger) -> None:
    if _cleanup_lock.locked():
        return

    async with _cleanup_lock:
        while True:
            job = await claim_ad_for_cleanup()
            if job is None:
                break

            await _cleanup_single_ad(bot, job["id"], job["final_status"], logger)


async def _cleanup_single_ad(
    bot: Bot,
    ad_id: int,
    final_status: str,
    logger: logging.Logger,
) -> None:
    while True:
        rows = await get_ad_delete_batch(ad_id, limit=AD_DELETE_BATCH_SIZE)
        if not rows:
            break

        deleted_user_ids: list[int] = []
        failed_items: list[tuple[int, str]] = []

        for user_id, message_id in rows:
            ok, error_text = await _delete_ad_message(bot, user_id, message_id)
            if ok:
                deleted_user_ids.append(user_id)
            else:
                failed_items.append((user_id, error_text or "delete_failed"))
            await asyncio.sleep(AD_DELETE_INTERVAL_SECONDS)

        await record_ad_delete_batch(ad_id, deleted_user_ids, failed_items)

    await finish_ad_cleanup(ad_id, final_status)

    campaign = await get_ad_campaign(ad_id)
    if campaign is not None:
        await _notify_admin_cleanup_result(bot, campaign)

    logger.info("Reklama yopildi: #%s (%s)", ad_id, final_status)


async def _delete_ad_message(
    bot: Bot,
    user_id: int,
    message_id: int,
) -> tuple[bool, str | None]:
    for attempt in range(3):
        try:
            await bot.delete_message(chat_id=user_id, message_id=message_id)
            return True, None
        except TelegramRetryAfter as error:
            await asyncio.sleep(float(error.retry_after) + 0.5)
        except TelegramBadRequest as error:
            error_text = str(error).lower()
            if "message to delete not found" in error_text:
                return True, None
            return False, _format_error(error)
        except TelegramForbiddenError as error:
            await mark_user_blocked(user_id)
            return False, _format_error(error)
        except TelegramNetworkError as error:
            if attempt == 2:
                return False, _format_error(error)
            await asyncio.sleep(1.5 + attempt)
        except Exception as error:
            return False, _format_error(error)

    return False, "retry_exhausted"


async def _notify_admin_broadcast_result(bot: Bot, campaign: dict[str, Any]) -> None:
    status_text = {
        "active": "Faol",
        "stop_requested": "To'xtatilmoqda",
        "stopping": "To'xtatilmoqda",
        "broadcasting": "Yuborilmoqda",
    }.get(campaign["status"], campaign["status"])
    preview = _campaign_preview(campaign)
    text = (
        f"📣 Reklama #{campaign['id']} tayyor\n"
        f"• Holat: {status_text}\n"
        f"• Yuborildi: {campaign['delivered_total']}/{campaign['recipient_total']}\n"
        f"• Xatolar: {campaign['failed_total']}\n"
        f"• Kontent: {preview}"
    )
    await _safe_notify_admin(bot, campaign["admin_id"], text)


async def _notify_admin_cleanup_result(bot: Bot, campaign: dict[str, Any]) -> None:
    status_text = {
        "expired": "Muddat tugadi",
        "stopped": "Muddatidan oldin to'xtatildi",
    }.get(campaign["status"], campaign["status"])
    undeleted = max(0, campaign["delivered_total"] - campaign["deleted_total"])
    text = (
        f"🧹 Reklama #{campaign['id']} yopildi\n"
        f"• Holat: {status_text}\n"
        f"• O'chirildi: {campaign['deleted_total']}/{campaign['delivered_total']}\n"
        f"• O'chmay qolganlar: {undeleted}"
    )
    await _safe_notify_admin(bot, campaign["admin_id"], text)


async def _safe_notify_admin(bot: Bot, admin_id: int, text: str) -> None:
    with suppress(TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError):
        await bot.send_message(admin_id, text)


def _campaign_preview(campaign: dict[str, Any]) -> str:
    base = {
        "text": "Matn",
        "photo": "Rasm",
        "video": "Video",
        "document": "Fayl",
    }.get(campaign["content_type"], campaign["content_type"])
    text = (campaign.get("text") or "").strip()
    if not text:
        return base

    compact = " ".join(text.split())
    if len(compact) > 38:
        compact = f"{compact[:35]}..."
    return f"{base}: {compact}"


def _format_error(error: Exception) -> str:
    text = " ".join(str(error).split())
    if len(text) > 120:
        return f"{text[:117]}..."
    return text or type(error).__name__
