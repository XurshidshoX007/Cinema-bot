"""Helper admin management."""

from typing import Any

from config import ADMIN_ID
from .cache import view_tracking_exclusion_cache
from .cache import _trim_cache
from .connection import _get_db, _execute
from .constants import CACHE_MAX_VIEW_EXCLUSION, ADMIN_PERMISSIONS, ADMIN_PERMISSION_COLUMNS
from .utils import _helper_admin_row_to_dict, _normalize_admin_permission, _format_timestamp


async def get_admin_permissions(user_id: int) -> set[str]:
    if user_id == ADMIN_ID:
        return {*ADMIN_PERMISSIONS, "helpers", "owner"}

    helper_admin = await get_helper_admin(user_id)
    if helper_admin is None:
        return set()

    return {
        permission
        for permission, enabled in helper_admin["permissions"].items()
        if enabled
    }


async def is_admin_user(user_id: int) -> bool:
    return bool(await get_admin_permissions(user_id))


async def get_helper_admin(user_id: int) -> dict[str, Any] | None:
    connection = _get_db()
    async with connection.execute(
        """
        SELECT
            user_id,
            username,
            full_name,
            can_manage_movies,
            can_manage_requests,
            can_view_stats,
            can_manage_ads,
            can_manage_channels,
            added_at,
            added_by
        FROM helper_admins
        WHERE user_id=?
        """,
        (user_id,),
    ) as cursor:
        row = await cursor.fetchone()

    return _helper_admin_row_to_dict(row)


async def list_helper_admins() -> list[dict[str, Any]]:
    connection = _get_db()
    async with connection.execute(
        """
        SELECT
            user_id,
            username,
            full_name,
            can_manage_movies,
            can_manage_requests,
            can_view_stats,
            can_manage_ads,
            can_manage_channels,
            added_at,
            added_by
        FROM helper_admins
        ORDER BY added_at DESC, user_id DESC
        """
    ) as cursor:
        rows = await cursor.fetchall()

    return [
        item for row in rows if (item := _helper_admin_row_to_dict(row)) is not None
    ]


async def upsert_helper_admin(
    user_id: int,
    *,
    username: str | None,
    full_name: str,
    added_by: int,
) -> None:
    connection = _get_db()
    now_text = _format_timestamp()
    await connection.execute(
        """
        INSERT INTO helper_admins (
            user_id,
            username,
            full_name,
            added_at,
            added_by
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET
            username=excluded.username,
            full_name=excluded.full_name
        """,
        (user_id, username, full_name, now_text, added_by),
    )
    await connection.commit()
    view_tracking_exclusion_cache[user_id] = True
    _trim_cache(view_tracking_exclusion_cache, CACHE_MAX_VIEW_EXCLUSION)


async def set_helper_admin_permission(
    user_id: int,
    permission: str,
    enabled: bool,
) -> bool:
    normalized_permission = _normalize_admin_permission(permission)
    column_name = ADMIN_PERMISSION_COLUMNS[normalized_permission]
    connection = _get_db()
    cursor = await connection.execute(
        f"UPDATE helper_admins SET {column_name}=? WHERE user_id=?",
        (1 if enabled else 0, user_id),
    )
    await connection.commit()
    return cursor.rowcount > 0


async def remove_helper_admin(user_id: int) -> None:
    await _execute("DELETE FROM helper_admins WHERE user_id=?", (user_id,))
    view_tracking_exclusion_cache.pop(user_id, None)
