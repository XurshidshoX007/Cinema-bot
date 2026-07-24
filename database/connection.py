"""DB connection management and schema initialisation."""

import asyncio
import sqlite3
from contextlib import suppress
from typing import Any

import aiosqlite

from .cache import (
    movie_cache,
    serial_group_cache,
    fav_cache,
    user_activity_cache,
    view_tracking_exclusion_cache,
)
from .constants import (
    CACHE_MAX_FAVORITES,
    CACHE_MAX_MOVIES,
    _SAFE_TABLE_NAMES,
    SQLITE_BUSY_TIMEOUT_MS,
    SQLITE_CACHE_SIZE_KIB,
    SQLITE_MMAP_SIZE,
)
from .paths import DB_PATH
from .utils import (
    _format_timestamp,
    _serial_base_title,
)

# Global connection holder (mirrors old database.db)
_db: aiosqlite.Connection | None = None


def _get_db() -> aiosqlite.Connection:
    if _db is None:
        raise RuntimeError("Database hali ishga tushirilmagan")
    return _db


async def _get_table_columns(
    connection: aiosqlite.Connection,
    table_name: str,
) -> set[str]:
    if table_name not in _SAFE_TABLE_NAMES:
        raise ValueError(f"Noma'lum jadval nomi: {table_name}")
    async with connection.execute(f"PRAGMA table_info({table_name})") as cursor:
        return {str(row[1]) for row in await cursor.fetchall() if len(row) > 1}


async def _ensure_users_tracking_columns(
    connection: aiosqlite.Connection,
) -> bool:
    user_columns = await _get_table_columns(connection, "users")
    schema_updated = False

    if "is_blocked" not in user_columns:
        await connection.execute(
            "ALTER TABLE users ADD COLUMN is_blocked INTEGER NOT NULL DEFAULT 0"
        )
        schema_updated = True

    if "blocked_at" not in user_columns:
        await connection.execute("ALTER TABLE users ADD COLUMN blocked_at TEXT")
        schema_updated = True

    if schema_updated:
        await connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_blocked_last_seen ON users(is_blocked, last_seen)"
        )
        await connection.commit()
        user_columns = await _get_table_columns(connection, "users")

    return "is_blocked" in user_columns


async def _ensure_movie_views_columns(connection: aiosqlite.Connection) -> None:
    movie_view_columns = await _get_table_columns(connection, "movie_views")
    if "unique_views" in movie_view_columns:
        return

    await connection.execute(
        "ALTER TABLE movie_views ADD COLUMN unique_views INTEGER NOT NULL DEFAULT 0"
    )
    await connection.execute(
        """
        UPDATE movie_views
        SET unique_views = views
        WHERE COALESCE(unique_views, 0) = 0 AND COALESCE(views, 0) > 0
        """
    )
    await connection.commit()


async def _ensure_stats_event_tables(connection: aiosqlite.Connection) -> None:
    await connection.execute(
        """
        CREATE TABLE IF NOT EXISTS content_view_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            viewed_at TEXT NOT NULL
        )
        """
    )
    await connection.execute(
        """
        CREATE TABLE IF NOT EXISTS user_search_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            raw_query TEXT,
            normalized_code TEXT,
            resolved_code TEXT,
            result_status TEXT NOT NULL,
            content_kind TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    await connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_content_view_events_viewed_at
        ON content_view_events(viewed_at, code)
        """
    )
    await connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_content_view_events_user_time
        ON content_view_events(user_id, viewed_at)
        """
    )
    await connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_user_search_events_user_time
        ON user_search_events(user_id, created_at)
        """
    )
    await connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_user_search_events_time_status
        ON user_search_events(created_at, result_status)
        """
    )


async def _execute(query: str, params: tuple[Any, ...] = ()) -> None:
    connection = _get_db()
    await connection.execute(query, params)
    await connection.commit()


async def init_db() -> None:
    global _db

    if _db is not None:
        return

    # Local imports to avoid circular at module load time
    from .serials import _sync_all_serial_groups, _normalize_collection_serial_codes
    from .sponsors import _normalize_sponsor_channel_id

    _db = await aiosqlite.connect(
        DB_PATH,
        timeout=30,
        cached_statements=512,
    )

    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA synchronous=NORMAL")
    await _db.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    await _db.execute("PRAGMA temp_store=MEMORY")
    await _db.execute(f"PRAGMA cache_size=-{SQLITE_CACHE_SIZE_KIB}")

    with suppress(aiosqlite.Error):
        await _db.execute(f"PRAGMA mmap_size={SQLITE_MMAP_SIZE}")

    with suppress(aiosqlite.Error):
        await _db.execute("PRAGMA wal_autocheckpoint=1000")

    with suppress(aiosqlite.Error):
        await _db.execute("PRAGMA journal_size_limit=67108864")

    await _db.execute(
        """
        CREATE TABLE IF NOT EXISTS movies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE,
            title TEXT,
            description TEXT,
            file_id TEXT,
            content_kind TEXT NOT NULL DEFAULT 'movie',
            series_title TEXT,
            episode_number INTEGER
        )
        """
    )
    async with _db.execute("PRAGMA table_info(movies)") as cursor:
        movie_columns = {row[1] for row in await cursor.fetchall()}

    if "content_kind" not in movie_columns:
        await _db.execute(
            "ALTER TABLE movies ADD COLUMN content_kind TEXT NOT NULL DEFAULT 'movie'"
        )

    if "series_title" not in movie_columns:
        await _db.execute("ALTER TABLE movies ADD COLUMN series_title TEXT")

    if "episode_number" not in movie_columns:
        await _db.execute("ALTER TABLE movies ADD COLUMN episode_number INTEGER")

    async with _db.execute(
        """
        SELECT id, title, series_title, episode_number
        FROM movies
        WHERE COALESCE(content_kind, 'movie') = 'serial'
        ORDER BY COALESCE(series_title, title) COLLATE NOCASE ASC, id ASC
        """
    ) as cursor:
        serial_rows = await cursor.fetchall()

    serial_updates: list[tuple[str, int, int]] = []
    next_episode_by_title: dict[str, int] = {}
    for movie_id, title, series_title, episode_number in serial_rows:
        base_title = _serial_base_title(title, series_title)
        if not base_title:
            continue

        next_episode = next_episode_by_title.get(base_title, 1)
        normalized_episode = (
            episode_number
            if isinstance(episode_number, int) and episode_number > 0
            else next_episode
        )
        next_episode_by_title[base_title] = max(next_episode, normalized_episode + 1)
        serial_updates.append((base_title, normalized_episode, movie_id))

    if serial_updates:
        await _db.executemany(
            """
            UPDATE movies
            SET series_title = COALESCE(NULLIF(series_title, ''), ?),
                episode_number = COALESCE(episode_number, ?)
            WHERE id = ?
            """,
            serial_updates,
        )

    await _db.execute(
        """
        CREATE TABLE IF NOT EXISTS favorites (
            user_id INTEGER,
            code TEXT,
            UNIQUE(user_id, code)
        )
        """
    )
    await _db.execute(
        """
        CREATE TABLE IF NOT EXISTS history (
            user_id INTEGER,
            code TEXT,
            UNIQUE(user_id, code)
        )
        """
    )
    await _db.execute(
        """
        CREATE TABLE IF NOT EXISTS serial_groups (
            title TEXT PRIMARY KEY,
            code TEXT UNIQUE NOT NULL,
            description TEXT
        )
        """
    )
    await _sync_all_serial_groups(_db)
    await _normalize_collection_serial_codes(_db, "favorites")
    await _normalize_collection_serial_codes(_db, "history")
    await _db.execute(
        """
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            text TEXT,
            file_id TEXT,
            status TEXT DEFAULT 'pending'
        )
        """
    )
    await _db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            first_seen TEXT,
            last_seen TEXT
        )
        """
    )
    await _ensure_users_tracking_columns(_db)
    await _db.execute(
        """
        CREATE TABLE IF NOT EXISTS helper_admins (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            can_manage_movies INTEGER NOT NULL DEFAULT 0,
            can_manage_requests INTEGER NOT NULL DEFAULT 0,
            can_view_stats INTEGER NOT NULL DEFAULT 0,
            can_manage_ads INTEGER NOT NULL DEFAULT 0,
            can_manage_channels INTEGER NOT NULL DEFAULT 0,
            added_at TEXT NOT NULL,
            added_by INTEGER
        )
        """
    )
    await _db.execute(
        """
        CREATE TABLE IF NOT EXISTS movie_views (
            code TEXT PRIMARY KEY,
            views INTEGER DEFAULT 0,
            last_viewed_at TEXT
        )
        """
    )
    await _ensure_movie_views_columns(_db)
    await _db.execute(
        """
        CREATE TABLE IF NOT EXISTS movie_unique_views (
            code TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            first_viewed_at TEXT NOT NULL,
            PRIMARY KEY(code, user_id)
        )
        """
    )
    await _db.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_stats (
            day TEXT,
            metric TEXT,
            value INTEGER DEFAULT 0,
            UNIQUE(day, metric)
        )
        """
    )
    await _ensure_stats_event_tables(_db)
    await _db.execute(
        """
        CREATE TABLE IF NOT EXISTS ads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER NOT NULL,
            content_type TEXT NOT NULL,
            text TEXT,
            file_id TEXT,
            duration_seconds INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'broadcasting',
            created_at TEXT NOT NULL,
            started_at TEXT,
            expires_at TEXT,
            closed_at TEXT,
            recipient_total INTEGER DEFAULT 0,
            delivered_total INTEGER DEFAULT 0,
            failed_total INTEGER DEFAULT 0,
            deleted_total INTEGER DEFAULT 0
        )
        """
    )
    await _db.execute(
        """
        CREATE TABLE IF NOT EXISTS ad_deliveries (
            ad_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            message_id INTEGER,
            status TEXT NOT NULL,
            error_text TEXT,
            sent_at TEXT,
            deleted_at TEXT,
            PRIMARY KEY(ad_id, user_id)
        )
        """
    )

    await _db.execute(
        """
        CREATE TABLE IF NOT EXISTS sponsor_channels (
            id TEXT PRIMARY KEY,
            url TEXT,
            name TEXT
        )
        """
    )

    async with _db.execute("SELECT id, url, name FROM sponsor_channels") as cursor:
        channel_rows = await cursor.fetchall()

    for channel_id, channel_url, channel_name in channel_rows:
        normalized_id = _normalize_sponsor_channel_id(channel_id)
        if normalized_id == channel_id or not normalized_id:
            continue

        await _db.execute(
            """
            INSERT OR REPLACE INTO sponsor_channels (id, url, name)
            VALUES (?, ?, ?)
            """,
            (normalized_id, channel_url, channel_name),
        )
        await _db.execute("DELETE FROM sponsor_channels WHERE id=?", (channel_id,))
    await _db.execute(
        """
        CREATE TABLE IF NOT EXISTS user_feature_trials (
            user_id INTEGER NOT NULL,
            feature TEXT NOT NULL,
            used_at TEXT NOT NULL,
            PRIMARY KEY(user_id, feature)
        )
        """
    )

    async with _db.execute("PRAGMA table_info(helper_admins)") as cursor:
        helper_columns = {row[1] for row in await cursor.fetchall()}
    if "can_manage_channels" not in helper_columns:
        await _db.execute(
            "ALTER TABLE helper_admins ADD COLUMN can_manage_channels INTEGER NOT NULL DEFAULT 0"
        )

    await _db.execute("CREATE INDEX IF NOT EXISTS idx_movies_code ON movies(code)")
    await _db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_movies_admin_serial_order
        ON movies(content_kind, series_title, episode_number, id)
        """
    )
    await _db.execute(
        "CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status)"
    )
    await _db.execute("CREATE INDEX IF NOT EXISTS idx_fav_user ON favorites(user_id)")
    await _db.execute("CREATE INDEX IF NOT EXISTS idx_hist_user ON history(user_id)")
    await _db.execute(
        "CREATE INDEX IF NOT EXISTS idx_users_last_seen ON users(last_seen)"
    )
    await _db.execute(
        "CREATE INDEX IF NOT EXISTS idx_users_first_seen ON users(first_seen)"
    )
    await _db.execute(
        "CREATE INDEX IF NOT EXISTS idx_users_blocked_last_seen ON users(is_blocked, last_seen)"
    )
    await _db.execute(
        "CREATE INDEX IF NOT EXISTS idx_helper_admins_added_at ON helper_admins(added_at)"
    )
    await _db.execute(
        "CREATE INDEX IF NOT EXISTS idx_daily_stats_metric_day ON daily_stats(metric, day)"
    )
    await _db.execute(
        "CREATE INDEX IF NOT EXISTS idx_movie_unique_views_user ON movie_unique_views(user_id)"
    )
    await _ensure_stats_event_tables(_db)
    await _db.execute(
        "CREATE INDEX IF NOT EXISTS idx_ads_status_expires ON ads(status, expires_at)"
    )
    await _db.execute(
        "CREATE INDEX IF NOT EXISTS idx_ad_deliveries_status ON ad_deliveries(ad_id, status)"
    )
    await _db.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_feature_trials_feature ON user_feature_trials(feature)"
    )

    now_text = _format_timestamp()
    await _db.execute(
        """
        INSERT OR IGNORE INTO users (user_id, username, full_name, first_seen, last_seen)
        SELECT user_id, NULL, 'User ' || user_id, ?, ?
        FROM (
            SELECT user_id FROM requests
            UNION
            SELECT user_id FROM favorites
            UNION
            SELECT user_id FROM history
        )
        """,
        (now_text, now_text),
    )
    await _db.commit()


async def close_db() -> None:
    global _db

    if _db is None:
        return

    with suppress(aiosqlite.Error):
        await _db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    await _db.close()
    _db = None
    movie_cache.clear()
    serial_group_cache.clear()
    fav_cache.clear()
    user_activity_cache.clear()
    view_tracking_exclusion_cache.clear()
    # Clear channels_cache via sponsors module if loaded
    try:
        from .sponsors import clear_sponsor_cache

        clear_sponsor_cache()
    except Exception:
        pass
