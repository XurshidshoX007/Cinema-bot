import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from repositories.channels import get_sponsor_channels
from repositories.users import (
    has_feature_trial_used,
    is_admin_user,
    mark_feature_trial_used,
)

router = Router()
logger = logging.getLogger(__name__)

FEATURE_SEARCH = "search_v2"
FEATURE_REQUEST = "request_v2"
FEATURE_LABELS = {
    FEATURE_SEARCH: "Qidiruv",
    FEATURE_REQUEST: "So'rov",
}


def _normalize_chat_id(raw_value: str) -> str | int:
    value = (raw_value or "").strip()
    if not value:
        return value

    if value.startswith("https://t.me/"):
        value = "@" + value.rsplit("/", 1)[-1].strip()

    if value.startswith("@"):
        tail = value[1:]
        if tail.isdigit():
            value = f"-{tail}" if tail.startswith("100") else tail
        else:
            return value

    if value.lstrip("-").isdigit():
        return int(value)

    return value


def _username_from_channel_url(url: str | None) -> str | None:
    value = (url or "").strip()
    if not value or "t.me/" not in value:
        return None

    tail = value.rstrip("/").rsplit("/", 1)[-1].strip()
    if not tail or tail.startswith("+") or tail.startswith("joinchat"):
        return None

    return f"@{tail.lstrip('@')}"


def _channel_candidates(channel: dict) -> list[str | int]:
    candidates: list[str | int] = []

    normalized_id = _normalize_chat_id(str(channel.get("id") or ""))
    if normalized_id not in ("", None):
        candidates.append(normalized_id)

    username = _username_from_channel_url(channel.get("url"))
    if username and username not in candidates:
        candidates.append(username)

    return candidates


async def _get_channel_subscription_status(
    bot: Bot,
    user_id: int,
    channel: dict,
) -> str:
    last_error: Exception | None = None
    had_unknown_error = False

    for candidate in _channel_candidates(channel):
        try:
            member = await bot.get_chat_member(chat_id=candidate, user_id=user_id)
        except TelegramBadRequest as error:
            last_error = error
            logger.warning(
                "Obuna tekshiruvi xato berdi: channel_id=%s candidate=%s user_id=%s error=%s",
                channel.get("id"),
                candidate,
                user_id,
                error,
            )
            had_unknown_error = True
            continue
        except Exception as error:
            last_error = error
            logger.warning(
                "Obuna tekshiruvida kutilmagan xato: channel_id=%s candidate=%s user_id=%s error=%s",
                channel.get("id"),
                candidate,
                user_id,
                error,
            )
            had_unknown_error = True
            continue

        if member.status in ("left", "kicked"):
            return "unsubscribed"
        return "subscribed"

    logger.error(
        "Kanal obunasini tekshirib bo'lmadi: channel_id=%s user_id=%s error=%s",
        channel.get("id"),
        user_id,
        last_error,
    )
    if had_unknown_error:
        return "unknown"
    return "unsubscribed"


async def get_channel_access_report(
    bot: Bot,
    user_id: int,
) -> tuple[list[dict], list[dict]]:
    channels = await get_sponsor_channels()
    if not channels:
        return [], []

    unsubscribed: list[dict] = []
    unknown: list[dict] = []
    for channel in channels:
        status = await _get_channel_subscription_status(bot, user_id, channel)
        if status == "unsubscribed":
            unsubscribed.append(channel)
        elif status == "unknown":
            unknown.append(channel)

    return unsubscribed, unknown


def get_submission_markup(unsubscribed: list[dict]) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text=channel.get("name", "Obuna bo'lish"),
                url=channel["url"],
            )
        ]
        for channel in unsubscribed
    ]
    buttons.append(
        [InlineKeyboardButton(text="✅ Tasdiqlash", callback_data="check_subscription")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _feature_subscribe_text(feature: str) -> str:
    feature_label = FEATURE_LABELS.get(feature, "Ushbu bo'lim")
    return (
        "🔒 <b>Obuna talab qilinadi</b>\n\n"
        f"<i>{feature_label}</i> bo'limidan foydalanish uchun homiy kanallarga a'zo bo'ling.\n"
        "A'zo bo'lgach, <b>✅ Tasdiqlash</b> tugmasini bosing."
    )


async def ensure_feature_access(
    message: Message,
    *,
    feature: str,
    consume_trial: bool = True,
) -> bool:
    user = message.from_user
    if user is None:
        return True

    user_id = user.id
    if await is_admin_user(user_id):
        return True

    channels = await get_sponsor_channels()
    if not channels:
        return True

    trial_used = await has_feature_trial_used(user_id, feature)
    if not trial_used:
        if consume_trial:
            await mark_feature_trial_used(user_id, feature)
        return True

    unsubscribed, unknown = await get_channel_access_report(message.bot, user_id)
    if not unsubscribed and not unknown:
        return True

    await message.answer(
        _feature_subscribe_text(feature),
        parse_mode="HTML",
        reply_markup=get_submission_markup(unsubscribed + unknown),
    )
    return False


async def ensure_feature_callback_access(
    callback: CallbackQuery,
    *,
    feature: str,
    consume_trial: bool = False,
) -> bool:
    user = callback.from_user
    if user is None:
        return True

    user_id = user.id
    if await is_admin_user(user_id):
        return True

    channels = await get_sponsor_channels()
    if not channels:
        return True

    trial_used = await has_feature_trial_used(user_id, feature)
    if not trial_used:
        if consume_trial:
            await mark_feature_trial_used(user_id, feature)
        return True

    unsubscribed, unknown = await get_channel_access_report(callback.bot, user_id)
    if not unsubscribed and not unknown:
        return True

    if callback.message is not None:
        await callback.message.answer(
            _feature_subscribe_text(feature),
            parse_mode="HTML",
            reply_markup=get_submission_markup(unsubscribed + unknown),
        )

    await callback.answer("Obuna talab qilinadi.", show_alert=True)
    return False


class ForceSubMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        return await handler(event, data)


@router.callback_query(F.data == "check_subscription")
async def check_sub_handler(callback: CallbackQuery, bot: Bot) -> None:
    user_id = callback.from_user.id
    unsubscribed, unknown = await get_channel_access_report(bot, user_id)

    if unsubscribed:
        await callback.answer(
            "❌ Hali barcha kanallarga obuna bo'lmadingiz.",
            show_alert=True,
        )
        return

    if unknown:
        await callback.answer(
            "⚠️ Tekshiruv ishlashi uchun bot homiy kanalga admin bo'lishi kerak.",
            show_alert=True,
        )
        return

    message = callback.message
    if message is not None:
        try:
            await message.delete()
        except TelegramBadRequest:
            pass
        await message.answer(
            "🎉 Tasdiqlash qabul qilindi. Endi davom etishingiz mumkin."
        )

    await callback.answer()
