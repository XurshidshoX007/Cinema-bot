"""User tracking and feature trials."""

from time import monotonic

from .cache import user_activity_cache, view_tracking_exclusion_cache
from .cache import _trim_cache
from .connection import _get_db, _ensure_users_tracking_columns
from .constants import CACHE_MAX_USER_ACTIVITY, CACHE_MAX_VIEW_EXCLUSION
from .utils import _format_timestamp, _today_key, local_now


async def _increment_daily_stat(
    metric: str,
    amount: int = 1,
    *,
    connection=None,
) -> None:
    conn = connection or _get_db()
    await conn.execute(
        """
        INSERT INTO daily_stats (day, metric, value)
        VALUES (?, ?, ?)
        ON CONFLICT(day, metric)
        DO UPDATE SET value = value + excluded.value
        """,
        (_today_key(), metric, amount),
    )

    if connection is None:
        await conn.commit()


async def touch_user(
    user_id: int,
    username: str | None,
    full_name: str,
    *,
    force: bool = False,
) -> None:
    from .constants import USER_TOUCH_TTL_SECONDS

    now_monotonic = monotonic()
    today_key = local_now().date().strftime("%Y-%m-%d")
    last_touch = user_activity_cache.get(user_id)
    if (
        not force
        and last_touch is not None
        and now_monotonic - last_touch[0] < USER_TOUCH_TTL_SECONDS
        and last_touch[1] == today_key
    ):
        return

    now_text = _format_timestamp()
    connection = _get_db()
    await _ensure_users_tracking_columns(connection)

    cursor = await connection.execute(
        """
        INSERT OR IGNORE INTO users (user_id, username, full_name, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?)
        """,
        (user_id, username, full_name, now_text, now_text),
    )
    is_new_user = cursor.rowcount > 0

    await connection.execute(
        """
        UPDATE users
        SET username=?, full_name=?, last_seen=?, is_blocked=0, blocked_at=NULL
        WHERE user_id=?
        """,
        (username, full_name, now_text, user_id),
    )

    if is_new_user:
        await _increment_daily_stat("new_users", connection=connection)

    await connection.commit()
    user_activity_cache[user_id] = (now_monotonic, today_key)
    _trim_cache(user_activity_cache, CACHE_MAX_USER_ACTIVITY)


async def mark_user_blocked(
    user_id: int,
    username: str | None = None,
    full_name: str | None = None,
) -> None:
    now_text = _format_timestamp()
    display_name = (full_name or "").strip() or f"User {user_id}"
    connection = _get_db()
    await _ensure_users_tracking_columns(connection)

    await connection.execute(
        """
        INSERT OR IGNORE INTO users (
            user_id,
            username,
            full_name,
            first_seen,
            last_seen,
            is_blocked,
            blocked_at
        )
        VALUES (?, ?, ?, ?, ?, 1, ?)
        """,
        (user_id, username, display_name, now_text, now_text, now_text),
    )
    await connection.execute(
        """
        UPDATE users
        SET username = COALESCE(?, username),
            full_name = COALESCE(NULLIF(?, ''), full_name),
            is_blocked = 1,
            blocked_at = ?,
            last_seen = ?
        WHERE user_id = ?
        """,
        (username, display_name, now_text, now_text, user_id),
    )
    await connection.commit()
    user_activity_cache.pop(user_id, None)


async def delete_blocked_users() -> int:
    connection = _get_db()
    await _ensure_users_tracking_columns(connection)
    cursor = await connection.execute(
        "DELETE FROM users WHERE COALESCE(is_blocked, 0) = 1"
    )
    await connection.commit()
    user_activity_cache.clear()
    view_tracking_exclusion_cache.clear()
    return int(cursor.rowcount or 0)


async def has_feature_trial_used(user_id: int, feature: str) -> bool:
    connection = _get_db()
    async with connection.execute(
        """
        SELECT 1
        FROM user_feature_trials
        WHERE user_id=? AND feature=?
        LIMIT 1
        """,
        (user_id, feature),
    ) as cursor:
        row = await cursor.fetchone()

    return row is not None


async def mark_feature_trial_used(user_id: int, feature: str) -> None:
    connection = _get_db()
    await connection.execute(
        """
        INSERT OR IGNORE INTO user_feature_trials (user_id, feature, used_at)
        VALUES (?, ?, ?)
        """,
        (user_id, feature, _format_timestamp()),
    )
    await connection.commit()


async def get_user_snapshot(user_id: int) -> tuple[str | None, str] | None:
    connection = _get_db()
    async with connection.execute(
        "SELECT username, full_name FROM users WHERE user_id=?",
        (user_id,),
    ) as cursor:
        row = await cursor.fetchone()

    if row is None:
        return None

    username, full_name = row
    return username, full_name or f"User {user_id}"
