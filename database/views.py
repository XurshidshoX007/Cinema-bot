"""Movie view tracking and search events."""

from config import ADMIN_ID
from .cache import view_tracking_exclusion_cache
from .cache import _trim_cache
from .connection import _get_db, _ensure_movie_views_columns, _ensure_stats_event_tables
from .constants import CACHE_MAX_VIEW_EXCLUSION
from .users import _increment_daily_stat
from .utils import _format_timestamp


async def _is_view_tracking_excluded(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True

    cached = view_tracking_exclusion_cache.get(user_id)
    if cached is not None:
        return cached

    connection = _get_db()
    async with connection.execute(
        """
        SELECT 1
        FROM helper_admins
        WHERE user_id=?
        LIMIT 1
        """,
        (user_id,),
    ) as cursor:
        row = await cursor.fetchone()

    excluded = row is not None
    view_tracking_exclusion_cache[user_id] = excluded
    _trim_cache(view_tracking_exclusion_cache, CACHE_MAX_VIEW_EXCLUSION)
    return excluded


async def record_movie_view(
    code: str,
    *,
    viewer_user_id: int | None = None,
) -> bool:
    if viewer_user_id is None:
        return False

    if await _is_view_tracking_excluded(viewer_user_id):
        return False

    connection = _get_db()
    await _ensure_movie_views_columns(connection)
    await _ensure_stats_event_tables(connection)
    now_text = _format_timestamp()
    await connection.execute(
        """
        INSERT INTO content_view_events (user_id, code, viewed_at)
        VALUES (?, ?, ?)
        """,
        (viewer_user_id, code, now_text),
    )
    insert_cursor = await connection.execute(
        """
        INSERT OR IGNORE INTO movie_unique_views (code, user_id, first_viewed_at)
        VALUES (?, ?, ?)
        """,
        (code, viewer_user_id, now_text),
    )
    is_unique_view = insert_cursor.rowcount > 0

    await connection.execute(
        """
        INSERT INTO movie_views (code, views, last_viewed_at, unique_views)
        VALUES (?, 1, ?, ?)
        ON CONFLICT(code)
        DO UPDATE SET
            views = movie_views.views + 1,
            last_viewed_at = excluded.last_viewed_at,
            unique_views = movie_views.unique_views + excluded.unique_views
        """,
        (code, now_text, 1 if is_unique_view else 0),
    )
    await _increment_daily_stat("movie_views", connection=connection)
    await connection.commit()
    return True


async def log_user_search_event(
    *,
    user_id: int,
    raw_query: str | None,
    normalized_code: str | None,
    resolved_code: str | None,
    result_status: str,
    content_kind: str | None,
) -> None:
    if await _is_view_tracking_excluded(user_id):
        return

    connection = _get_db()
    await _ensure_stats_event_tables(connection)
    await connection.execute(
        """
        INSERT INTO user_search_events (
            user_id,
            raw_query,
            normalized_code,
            resolved_code,
            result_status,
            content_kind,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            (raw_query or "").strip(),
            (normalized_code or "").strip() or None,
            (resolved_code or "").strip() or None,
            (result_status or "unknown").strip(),
            (content_kind or "").strip() or None,
            _format_timestamp(),
        ),
    )
    await connection.commit()
