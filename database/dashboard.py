"""Dashboard snapshots and stats."""

import asyncio
import sqlite3
from contextlib import closing
from datetime import timedelta

from config import ADMIN_ID
from .connection import _get_db, _ensure_users_tracking_columns
from .paths import DB_PATH
from .utils import (
    _base_actor_filter,
    _format_timestamp,
    _placeholders,
    _utc_now,
    _visible_user_filter,
    local_day_keys,
    local_day_start_utc,
)


def _open_snapshot_connection() -> closing[sqlite3.Connection]:
    connection = sqlite3.connect(DB_PATH, timeout=5, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return closing(connection)


def _has_users_block_tracking_sync(connection: sqlite3.Connection) -> bool:
    try:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(users)").fetchall()
            if len(row) > 1
        }
    except sqlite3.Error:
        return False
    return "is_blocked" in columns


def get_dashboard_summary_snapshot() -> dict[str, int]:
    default_summary = {
        "total_users": 0,
        "all_time_users": 0,
        "entered_today": 0,
        "new_subscribers_today": 0,
        "blocked_users": 0,
        "joined_today": 0,
        "active_today": 0,
        "active_week": 0,
        "total_movies": 0,
        "total_favorites": 0,
        "total_views": 0,
        "total_requests": 0,
        "pending_requests": 0,
        "completed_requests": 0,
        "rejected_requests": 0,
    }
    if not DB_PATH.exists():
        return default_summary

    today_start = _format_timestamp(local_day_start_utc())
    today_cutoff = _format_timestamp(_utc_now() - timedelta(days=1))
    week_cutoff = _format_timestamp(local_day_start_utc(6))

    with _open_snapshot_connection() as connection:
        has_block_tracking = _has_users_block_tracking_sync(connection)
        visible_users_filter = _visible_user_filter(
            has_block_tracking=has_block_tracking,
            from_users_table=True,
        )
        visible_actor_filter = _visible_user_filter(
            has_block_tracking=has_block_tracking,
            from_users_table=False,
        )
        request_actor_filter = _base_actor_filter()
        queries = {
            "total_users": (
                f"SELECT COUNT(*) FROM users WHERE {visible_users_filter}",
                (ADMIN_ID,),
            ),
            "all_time_users": (
                f"SELECT COUNT(*) FROM users WHERE {_base_actor_filter()}",
                (ADMIN_ID,),
            ),
            "entered_today": (
                f"SELECT COUNT(*) FROM users WHERE last_seen >= ? AND {_base_actor_filter()}",
                (today_start, ADMIN_ID),
            ),
            "new_subscribers_today": (
                f"SELECT COUNT(*) FROM users WHERE first_seen >= ? AND {_base_actor_filter()}",
                (today_start, ADMIN_ID),
            ),
            "blocked_users": (
                (
                    "SELECT COUNT(*) FROM users "
                    "WHERE COALESCE(is_blocked, 0) = 1 "
                    f"AND {_base_actor_filter()}"
                )
                if has_block_tracking
                else "SELECT 0",
                (ADMIN_ID,) if has_block_tracking else (),
            ),
            "active_today": (
                f"SELECT COUNT(*) FROM users WHERE last_seen >= ? AND {visible_users_filter}",
                (today_cutoff, ADMIN_ID),
            ),
            "active_week": (
                f"SELECT COUNT(*) FROM users WHERE last_seen >= ? AND {visible_users_filter}",
                (week_cutoff, ADMIN_ID),
            ),
            "total_movies": ("SELECT COUNT(*) FROM movies", ()),
            "total_favorites": (
                f"SELECT COUNT(*) FROM favorites WHERE {visible_actor_filter}",
                (ADMIN_ID,),
            ),
            "total_views": ("SELECT COALESCE(SUM(views), 0) FROM movie_views", ()),
            "total_requests": (
                f"SELECT COUNT(*) FROM requests WHERE {request_actor_filter}",
                (ADMIN_ID,),
            ),
            "pending_requests": (
                "SELECT COUNT(*) FROM requests "
                "WHERE status IN ('pending', 'accepted') "
                f"AND {request_actor_filter}",
                (ADMIN_ID,),
            ),
            "completed_requests": (
                "SELECT COUNT(*) FROM requests "
                "WHERE status = 'completed' "
                f"AND {request_actor_filter}",
                (ADMIN_ID,),
            ),
            "rejected_requests": (
                "SELECT COUNT(*) FROM requests "
                "WHERE status = 'rejected' "
                f"AND {request_actor_filter}",
                (ADMIN_ID,),
            ),
        }

        result = default_summary.copy()
        for key, (query, params) in queries.items():
            row = connection.execute(query, params).fetchone()
            result[key] = int(row[0] or 0) if row and row[0] is not None else 0

    result["joined_today"] = result["entered_today"]
    return result


def get_request_status_counts_snapshot() -> dict[str, int]:
    counts = {
        "pending": 0,
        "accepted": 0,
        "completed": 0,
        "rejected": 0,
        "other": 0,
    }
    if not DB_PATH.exists():
        return counts

    with _open_snapshot_connection() as connection:
        rows = connection.execute(
            """
            SELECT status, COUNT(*) AS total
            FROM requests
            WHERE """
            + _base_actor_filter()
            + """
            GROUP BY status
            """,
            (ADMIN_ID,),
        ).fetchall()

    for row in rows:
        status = str(row["status"] or "").strip().lower()
        if status in counts:
            counts[status] = int(row["total"] or 0)
        else:
            counts["other"] += int(row["total"] or 0)
    return counts


def get_daily_metric_series_snapshot(days: int = 7) -> dict[str, list[int]]:
    labels = local_day_keys(days)
    series = {
        "requests": [0] * days,
        "movie_views": [0] * days,
        "new_users": [0] * days,
    }
    if not DB_PATH.exists():
        return {"labels": labels, **series}

    with _open_snapshot_connection() as connection:
        rows = connection.execute(
            """
            SELECT day, metric, value
            FROM daily_stats
            WHERE day >= ?
              AND metric IN (?, ?, ?)
            ORDER BY day ASC
            """,
            (labels[0], "requests", "movie_views", "new_users"),
        ).fetchall()

    label_index = {label: index for index, label in enumerate(labels)}
    seen_new_user_days: set[str] = set()
    for row in rows:
        day = str(row["day"] or "")
        metric = str(row["metric"] or "")
        if day in label_index and metric in series:
            series[metric][label_index[day]] = int(row["value"] or 0)
            if metric == "new_users":
                seen_new_user_days.add(day)

    missing_new_user_days = [label for label in labels if label not in seen_new_user_days]
    if missing_new_user_days:
        placeholders = _placeholders(missing_new_user_days)
        with _open_snapshot_connection() as connection:
            new_user_rows = connection.execute(
                f"""
                SELECT SUBSTR(first_seen, 1, 10) AS day, COUNT(*) AS total
                FROM users
                WHERE first_seen IS NOT NULL
                  AND SUBSTR(first_seen, 1, 10) IN ({placeholders})
                  AND {_base_actor_filter()}
                GROUP BY SUBSTR(first_seen, 1, 10)
                ORDER BY day ASC
                """,
                (*missing_new_user_days, ADMIN_ID),
            ).fetchall()

        for row in new_user_rows:
            day = str(row["day"] or "")
            if day in label_index:
                series["new_users"][label_index[day]] = int(row["total"] or 0)

    return {"labels": labels, **series}


async def get_dashboard_summary() -> dict[str, int]:
    return await asyncio.to_thread(get_dashboard_summary_snapshot)


async def get_request_status_counts() -> dict[str, int]:
    return await asyncio.to_thread(get_request_status_counts_snapshot)


async def get_daily_metric_series(days: int = 7) -> dict[str, list[int]]:
    return await asyncio.to_thread(get_daily_metric_series_snapshot, days)


async def get_top_viewed_movies(limit: int = 5) -> list[tuple[str, str, int, int]]:
    connection = _get_db()
    async with connection.execute(
        """
        WITH grouped_views AS (
            SELECT
                CASE
                    WHEN COALESCE(m.content_kind, 'movie') = 'serial'
                    THEN COALESCE(sg.code, COALESCE(NULLIF(m.series_title, ''), m.title), mv.code)
                    ELSE mv.code
                END AS item_code,
                CASE
                    WHEN COALESCE(m.content_kind, 'movie') = 'serial'
                    THEN COALESCE(NULLIF(m.series_title, ''), m.title, 'Noma''lum serial')
                    ELSE COALESCE(m.title, 'Noma''lum kino')
                END AS display_title,
                SUM(COALESCE(mv.views, 0)) AS views,
                SUM(COALESCE(mv.unique_views, mv.views)) AS unique_views,
                MAX(COALESCE(mv.last_viewed_at, '')) AS last_viewed_at
            FROM movie_views mv
            LEFT JOIN movies m ON m.code = mv.code
            LEFT JOIN serial_groups sg
              ON COALESCE(m.content_kind, 'movie') = 'serial'
             AND sg.title = COALESCE(NULLIF(m.series_title, ''), m.title)
            GROUP BY
                CASE WHEN COALESCE(m.content_kind, 'movie') = 'serial' THEN 'serial' ELSE 'movie' END,
                item_code,
                display_title
        )
        SELECT
            item_code,
            display_title,
            views,
            unique_views
        FROM grouped_views
        ORDER BY
            views DESC,
            unique_views DESC,
            last_viewed_at DESC,
            item_code ASC
        LIMIT ?
        """,
        (limit,),
    ) as cursor:
        return await cursor.fetchall()


async def get_recent_users(limit: int = 5) -> list[tuple[int, str | None, str, str]]:
    connection = _get_db()
    week_cutoff = _format_timestamp(local_day_start_utc(6))
    has_block_tracking = await _ensure_users_tracking_columns(connection)
    visible_users_filter = _visible_user_filter(
        has_block_tracking=has_block_tracking,
        from_users_table=True,
    )
    async with connection.execute(
        f"""
        SELECT user_id, username, full_name, last_seen
        FROM users
        WHERE last_seen >= ?
          AND {visible_users_filter}
        ORDER BY last_seen DESC
        LIMIT ?
        """,
        (week_cutoff, ADMIN_ID, limit),
    ) as cursor:
        return await cursor.fetchall()
