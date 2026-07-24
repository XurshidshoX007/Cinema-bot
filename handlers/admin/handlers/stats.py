"""Stats callbacks."""

from aiogram import F, types
from aiogram.exceptions import TelegramBadRequest

from database import touch_user

from .. import router
from ..constants import ADMIN_PERMISSION_STATS, STATS_PANELS
from ..permissions import _ensure_callback_access
from ..panels import _show_stats_dashboard


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
