import logging

from aiogram import F, Router, types

from database import mark_user_blocked, touch_user

logger = logging.getLogger(__name__)

router = Router()

_ACTIVE_MEMBER_STATUSES = {"member", "administrator", "creator", "restricted"}


@router.my_chat_member(F.chat.type == "private")
async def sync_private_chat_membership(event: types.ChatMemberUpdated) -> None:
    user = event.from_user
    user_id = user.id if user is not None else event.chat.id
    username = user.username if user is not None else None
    full_name = (
        user.full_name
        if user is not None and user.full_name
        else event.chat.full_name or f"User {user_id}"
    )
    status = event.new_chat_member.status
    new_status = getattr(status, "value", str(status)).casefold()

    if new_status == "kicked":
        await mark_user_blocked(user_id, username, full_name)
        return

    if new_status in _ACTIVE_MEMBER_STATUSES:
        await touch_user(user_id, username, full_name, force=True)
