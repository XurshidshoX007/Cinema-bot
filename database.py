import shutil
import sqlite3
import re
import shutil
from contextlib import suppress
from datetime import UTC, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from time import monotonic
from typing import Any, Iterable, Sequence

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - fallback for older Python builds
    ZoneInfo = None

import aiosqlite
from config import ADMIN_ID

PRIMARY_DB_PATH = Path(__file__).resolve().with_name("movies.db")
RUNTIME_DB_PATH = Path(__file__).resolve().with_name("movies.runtime.db")
INSPECT_DB_PATH = Path(__file__).resolve().with_name("movies.inspect.db")
COPY_DB_PATH = Path(__file__).resolve().with_name("movies_copy.db")
SQLITE_BUSY_TIMEOUT_MS = 5000
SQLITE_CACHE_SIZE_KIB = 32768
SQLITE_MMAP_SIZE = 268435456
MAX_HISTORY_ITEMS = 50
USER_TOUCH_TTL_SECONDS = 300
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
APP_TIMEZONE_NAME = "Asia/Tashkent"
APP_TIMEZONE_LABEL = "UZT"
if ZoneInfo is not None:
    with suppress(Exception):
        APP_TIMEZONE = ZoneInfo(APP_TIMEZONE_NAME)
if "APP_TIMEZONE" not in globals():
    APP_TIMEZONE = timezone(timedelta(hours=5), name=APP_TIMEZONE_LABEL)
MIN_AD_DURATION_SECONDS = 600
MAX_AD_DURATION_SECONDS = 30 * 86400
UNBLOCKED_USER_SQL = "COALESCE(is_blocked, 0) = 0"
ADMIN_PERMISSIONS = ("movies", "requests", "stats", "ads", "channels")
ADMIN_PERMISSION_COLUMNS = {
    "movies": "can_manage_movies",
    "requests": "can_manage_requests",
    "stats": "can_view_stats",
    "ads": "can_manage_ads",
    "channels": "can_manage_channels",
}

db: aiosqlite.Connection | None = None
movie_cache: dict[str, tuple[str, str, str, str]] = {}
serial_group_cache: dict[str, tuple[str, str, str, str]] = {}
fav_cache: dict[int, set[str]] = {}
user_activity_cache: dict[int, float] = {}
view_tracking_exclusion_cache: dict[int, bool] = {}
channels_cache: list[dict[str, str]] | None = None


def _normalize_sponsor_channel_id(value: str) -> str:
    channel_id = (value or "").strip()
    if not channel_id:
        return channel_id

    if channel_id.startswith("https://t.me/"):
        channel_id = "@" + channel_id.rsplit("/", 1)[-1].strip()

    if channel_id.startswith("@"):
        tail = channel_id[1:]
        if tail.isdigit():
            return f"-{tail}" if tail.startswith("100") else tail
        return channel_id

    if channel_id.startswith("100") and channel_id.isdigit():
        return f"-{channel_id}"

    return channel_id


def _copy_db_sidecar(source: Path, destination: Path) -> None:
    if not source.exists():
        return

    with suppress(OSError, PermissionError):
        shutil.copy2(source, destination)


def _can_open_sqlite(path: Path) -> bool:
    try:
        with sqlite3.connect(path, timeout=1) as connection:
            connection.execute("SELECT 1")
        return True
    except sqlite3.Error:
        return False


def _inspect_sqlite(path: Path) -> dict[str, int | Path] | None:
    if not path.exists():
        return None

    try:
        with sqlite3.connect(path, timeout=1) as connection:
            connection.execute("SELECT 1")
            has_movies_table = connection.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'table' AND name = 'movies'
                LIMIT 1
                """
            ).fetchone()
            movie_count = 0
            if has_movies_table is not None:
                row = connection.execute("SELECT COUNT(*) FROM movies").fetchone()
                movie_count = int(row[0] or 0)
            has_serial_groups_table = connection.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'table' AND name = 'serial_groups'
                LIMIT 1
                """
            ).fetchone()
            serial_group_count = 0
            if has_serial_groups_table is not None:
                row = connection.execute("SELECT COUNT(*) FROM serial_groups").fetchone()
                serial_group_count = int(row[0] or 0)
    except (sqlite3.Error, OSError):
        return None

    try:
        modified_at_ns = path.stat().st_mtime_ns
    except OSError:
        modified_at_ns = 0

    return {
        "path": path,
        "movie_count": movie_count,
        "serial_group_count": serial_group_count,
        "modified_at_ns": modified_at_ns,
    }


def _prepare_runtime_db(primary_path: Path, runtime_path: Path) -> Path:
    candidate_paths = [
        primary_path,
        runtime_path,
        INSPECT_DB_PATH,
        COPY_DB_PATH,
    ]
    inspected: list[tuple[int, dict[str, int | Path]]] = []

    for priority, candidate_path in enumerate(candidate_paths):
        info = _inspect_sqlite(candidate_path)
        if info is not None:
            inspected.append((priority, info))

    if inspected:
        _priority, best_info = max(
            inspected,
            key=lambda item: (
                int(item[1]["movie_count"]),
                int(item[1]["serial_group_count"]),
                int(item[1]["modified_at_ns"]),
                -item[0],
            ),
        )
        return Path(best_info["path"])

    if primary_path.exists():
        shutil.copy2(primary_path, runtime_path)
        _copy_db_sidecar(
            primary_path.with_name(f"{primary_path.name}-wal"),
            runtime_path.with_name(f"{runtime_path.name}-wal"),
        )
        _copy_db_sidecar(
            primary_path.with_name(f"{primary_path.name}-shm"),
            runtime_path.with_name(f"{runtime_path.name}-shm"),
        )

    if _can_open_sqlite(runtime_path):
        return runtime_path

    return primary_path


DB_PATH = _prepare_runtime_db(PRIMARY_DB_PATH, RUNTIME_DB_PATH)


def _get_db() -> aiosqlite.Connection:
    if db is None:
        raise RuntimeError("Database hali ishga tushirilmagan")

    return db


async def _get_table_columns(
    connection: aiosqlite.Connection,
    table_name: str,
) -> set[str]:
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


def _visible_user_filter(
    *,
    has_block_tracking: bool,
    user_id_column: str = "user_id",
    from_users_table: bool,
) -> str:
    base_filter = (
        f"{user_id_column} != ? "
        f"AND {user_id_column} NOT IN (SELECT user_id FROM helper_admins)"
    )
    if not has_block_tracking:
        return base_filter

    if from_users_table:
        return f"{base_filter} AND {UNBLOCKED_USER_SQL}"

    return (
        f"{base_filter} AND {user_id_column} IN ("
        "SELECT user_id FROM users WHERE COALESCE(is_blocked, 0) = 0"
        ")"
    )


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


def local_now() -> datetime:
    return datetime.now(APP_TIMEZONE).replace(microsecond=0)


def local_now_text(fmt: str = "%d.%m.%Y %H:%M") -> str:
    return local_now().strftime(fmt)


def parse_utc_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None

    try:
        return datetime.strptime(value, TIMESTAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def format_local_timestamp(
    value: str | None,
    fmt: str = "%d.%m.%Y %H:%M",
    *,
    fallback: str = "Noma'lum",
) -> str:
    parsed = parse_utc_timestamp(value)
    if parsed is None:
        cleaned = (value or "").strip()
        return cleaned or fallback

    return parsed.astimezone(APP_TIMEZONE).strftime(fmt)


def local_day_start_utc(days_ago: int = 0) -> datetime:
    target_date = local_now().date() - timedelta(days=days_ago)
    local_start = datetime.combine(target_date, dt_time.min, tzinfo=APP_TIMEZONE)
    return local_start.astimezone(UTC).replace(tzinfo=None, microsecond=0)


def local_day_keys(days: int) -> list[str]:
    today = local_now().date()
    return [
        (today - timedelta(days=offset)).strftime("%Y-%m-%d")
        for offset in range(days - 1, -1, -1)
    ]


def _format_timestamp(value: datetime | None = None) -> str:
    return (value or _utc_now()).strftime(TIMESTAMP_FORMAT)


def _normalize_ad_duration(duration_seconds: int) -> int:
    duration = int(duration_seconds)
    if not MIN_AD_DURATION_SECONDS <= duration <= MAX_AD_DURATION_SECONDS:
        raise ValueError("Reklama muddati ruxsat etilgan oraliqda emas")
    return duration


def _normalize_content_kind(value: str | None) -> str:
    return "serial" if value == "serial" else "movie"


def _normalize_admin_permission(permission: str) -> str:
    normalized = permission.strip().casefold()
    if normalized not in ADMIN_PERMISSION_COLUMNS:
        raise ValueError(f"Noma'lum admin ruxsati: {permission}")
    return normalized


def _helper_admin_row_to_dict(row: tuple[Any, ...] | None) -> dict[str, Any] | None:
    if row is None:
        return None

    (
        user_id,
        username,
        full_name,
        can_manage_movies,
        can_manage_requests,
        can_view_stats,
        can_manage_ads,
        can_manage_channels,
        added_at,
        added_by,
    ) = row

    permissions = {
        "movies": bool(can_manage_movies),
        "requests": bool(can_manage_requests),
        "stats": bool(can_view_stats),
        "ads": bool(can_manage_ads),
        "channels": bool(can_manage_channels),
    }

    return {
        "user_id": user_id,
        "username": username,
        "full_name": full_name,
        "permissions": permissions,
        "added_at": added_at,
        "added_by": added_by,
    }


def _serial_base_title(
    title: str | None,
    series_title: str | None = None,
) -> str:
    return (series_title or title or "").strip()


def _normalize_title_for_match(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return ""

    # Interlude titles can be wrapped in quotes, e.g. `"Qashqirlar Makoni" Falastin`.
    text = text.strip("\"'`«»“”„‟‹›")
    compact = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    compact = re.sub(r"\s+", " ", compact, flags=re.UNICODE).strip()
    return compact.casefold()


def _display_title(
    title: str | None,
    content_kind: str | None,
    *,
    series_title: str | None = None,
    episode_number: int | None = None,
) -> str:
    normalized_kind = _normalize_content_kind(content_kind)
    base_title = _serial_base_title(title, series_title)

    if normalized_kind == "serial" and base_title:
        if episode_number and episode_number > 0:
            return f"{base_title} {episode_number}-qism"
        return base_title

    return (title or "").strip()


def _serial_group_entry(
    title: str,
    description: str | None = None,
) -> tuple[str, str, str, str]:
    return (title, description or "", "", "serial")


def _clear_serial_group_cache(
    *,
    title: str | None = None,
    code: str | None = None,
) -> None:
    if code is not None:
        serial_group_cache.pop(code, None)

    if title is None:
        return

    normalized_title = _serial_base_title(title)
    stale_codes = [
        cached_code
        for cached_code, cached_value in serial_group_cache.items()
        if cached_value[0] == normalized_title
    ]
    for stale_code in stale_codes:
        serial_group_cache.pop(stale_code, None)


async def _content_code_exists(
    connection: aiosqlite.Connection,
    code: str,
) -> bool:
    async with connection.execute(
        """
        SELECT 1
        FROM (
            SELECT code FROM movies WHERE code = ?
            UNION ALL
            SELECT code FROM serial_groups WHERE code = ?
        )
        LIMIT 1
        """,
        (code, code),
    ) as cursor:
        row = await cursor.fetchone()

    return row is not None


async def _next_available_numeric_code(connection: aiosqlite.Connection) -> str:
    async with connection.execute(
        "SELECT COALESCE(MAX(CAST(code AS INTEGER)), 0) FROM movies WHERE code GLOB '[0-9]*'"
    ) as cursor:
        movie_row = await cursor.fetchone()

    async with connection.execute(
        "SELECT COALESCE(MAX(CAST(code AS INTEGER)), 0) FROM serial_groups WHERE code GLOB '[0-9]*'"
    ) as cursor:
        serial_row = await cursor.fetchone()

    candidate = max(int(movie_row[0] or 0), int(serial_row[0] or 0)) + 1
    while await _content_code_exists(connection, str(candidate)):
        candidate += 1

    return str(candidate)


async def _pick_serial_group_code(
    connection: aiosqlite.Connection,
    title: str,
    episode_codes: Sequence[str],
) -> str:
    normalized_title = _serial_base_title(title)
    for candidate in episode_codes:
        async with connection.execute(
            "SELECT title FROM serial_groups WHERE code=?",
            (candidate,),
        ) as cursor:
            row = await cursor.fetchone()

        if row is None or row[0] == normalized_title:
            return candidate

    return await _next_available_numeric_code(connection)


async def _sync_serial_group(
    connection: aiosqlite.Connection,
    title: str,
) -> None:
    normalized_title = _serial_base_title(title)
    if not normalized_title:
        return

    async with connection.execute(
        """
        SELECT
            code,
            COALESCE(description, ''),
            COALESCE(episode_number, 0),
            id
        FROM movies
        WHERE COALESCE(content_kind, 'movie') = 'serial'
          AND COALESCE(NULLIF(series_title, ''), title) = ?
        ORDER BY
            CASE WHEN episode_number IS NULL OR episode_number <= 0 THEN 1 ELSE 0 END,
            COALESCE(episode_number, 0) ASC,
            id ASC
        """,
        (normalized_title,),
    ) as cursor:
        episode_rows = await cursor.fetchall()

    async with connection.execute(
        "SELECT code, COALESCE(description, '') FROM serial_groups WHERE title=?",
        (normalized_title,),
    ) as cursor:
        group_row = await cursor.fetchone()

    existing_code = group_row[0] if group_row else None
    existing_description = group_row[1] if group_row else ""

    if not episode_rows:
        if existing_code is not None:
            await connection.execute(
                "DELETE FROM serial_groups WHERE title=?",
                (normalized_title,),
            )
            await connection.execute(
                "DELETE FROM favorites WHERE code=?",
                (existing_code,),
            )
            await connection.execute(
                "DELETE FROM history WHERE code=?",
                (existing_code,),
            )
            _clear_serial_group_cache(title=normalized_title, code=existing_code)
        return

    description = next(
        (row_description for _code, row_description, _episode, _row_id in episode_rows if row_description),
        existing_description,
    )

    if existing_code is None:
        target_code = await _pick_serial_group_code(
            connection,
            normalized_title,
            [episode_code for episode_code, _description, _episode, _row_id in episode_rows],
        )
        while True:
            try:
                await connection.execute(
                    """
                    INSERT INTO serial_groups (title, code, description)
                    VALUES (?, ?, ?)
                    """,
                    (normalized_title, target_code, description),
                )
                break
            except aiosqlite.IntegrityError:
                target_code = await _next_available_numeric_code(connection)
    else:
        await connection.execute(
            """
            UPDATE serial_groups
            SET description=?
            WHERE title=?
            """,
            (description, normalized_title),
        )

    _clear_serial_group_cache(title=normalized_title, code=existing_code)


async def _sync_all_serial_groups(connection: aiosqlite.Connection) -> None:
    async with connection.execute(
        """
        SELECT DISTINCT COALESCE(NULLIF(series_title, ''), title) AS base_title
        FROM movies
        WHERE COALESCE(content_kind, 'movie') = 'serial'
        """
    ) as cursor:
        movie_titles = [title for (title,) in await cursor.fetchall() if title]

    async with connection.execute("SELECT title FROM serial_groups") as cursor:
        group_titles = [title for (title,) in await cursor.fetchall() if title]

    for title in dict.fromkeys([*movie_titles, *group_titles]):
        await _sync_serial_group(connection, title)


async def _normalize_collection_serial_codes(
    connection: aiosqlite.Connection,
    table_name: str,
) -> None:
    async with connection.execute(
        f"""
        SELECT
            source.rowid,
            source.user_id,
            source.code,
            groups.code
        FROM {table_name} AS source
        JOIN movies
          ON movies.code = source.code
         AND COALESCE(movies.content_kind, 'movie') = 'serial'
        JOIN serial_groups AS groups
          ON groups.title = COALESCE(NULLIF(movies.series_title, ''), movies.title)
        WHERE source.code != groups.code
        ORDER BY source.rowid ASC
        """
    ) as cursor:
        rows = await cursor.fetchall()

    for row_id, user_id, old_code, new_code in rows:
        if table_name == "history":
            await connection.execute(
                f"INSERT OR REPLACE INTO {table_name} (user_id, code) VALUES (?, ?)",
                (user_id, new_code),
            )
        else:
            await connection.execute(
                f"INSERT OR IGNORE INTO {table_name} (user_id, code) VALUES (?, ?)",
                (user_id, new_code),
            )

        await connection.execute(
            f"DELETE FROM {table_name} WHERE rowid=?",
            (row_id,),
        )


def _today_key() -> str:
    return local_now().strftime("%Y-%m-%d")


def _day_keys(days: int) -> list[str]:
    return local_day_keys(days)


def _placeholders(values: Sequence[Any]) -> str:
    return ", ".join("?" for _ in values)


def _ad_row_to_dict(row: tuple[Any, ...] | None) -> dict[str, Any] | None:
    if row is None:
        return None

    (
        ad_id,
        admin_id,
        content_type,
        text,
        file_id,
        duration_seconds,
        status,
        created_at,
        started_at,
        expires_at,
        closed_at,
        recipient_total,
        delivered_total,
        failed_total,
        deleted_total,
    ) = row
    return {
        "id": ad_id,
        "admin_id": admin_id,
        "content_type": content_type,
        "text": text,
        "file_id": file_id,
        "duration_seconds": duration_seconds,
        "status": status,
        "created_at": created_at,
        "started_at": started_at,
        "expires_at": expires_at,
        "closed_at": closed_at,
        "recipient_total": recipient_total,
        "delivered_total": delivered_total,
        "failed_total": failed_total,
        "deleted_total": deleted_total,
    }


async def _execute(query: str, params: tuple[Any, ...] = ()) -> None:
    connection = _get_db()
    await connection.execute(query, params)
    await connection.commit()


async def init_db() -> None:
    global db

    if db is not None:
        return

    db = await aiosqlite.connect(
        DB_PATH,
        timeout=30,
        cached_statements=512,
    )

    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA synchronous=NORMAL")
    await db.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    await db.execute("PRAGMA temp_store=MEMORY")
    await db.execute(f"PRAGMA cache_size=-{SQLITE_CACHE_SIZE_KIB}")

    with suppress(aiosqlite.Error):
        await db.execute(f"PRAGMA mmap_size={SQLITE_MMAP_SIZE}")

    with suppress(aiosqlite.Error):
        await db.execute("PRAGMA wal_autocheckpoint=1000")

    with suppress(aiosqlite.Error):
        await db.execute("PRAGMA journal_size_limit=67108864")

    await db.execute("""
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
        """)
    async with db.execute("PRAGMA table_info(movies)") as cursor:
        movie_columns = {row[1] for row in await cursor.fetchall()}

    if "content_kind" not in movie_columns:
        await db.execute(
            "ALTER TABLE movies ADD COLUMN content_kind TEXT NOT NULL DEFAULT 'movie'"
        )

    if "series_title" not in movie_columns:
        await db.execute("ALTER TABLE movies ADD COLUMN series_title TEXT")

    if "episode_number" not in movie_columns:
        await db.execute("ALTER TABLE movies ADD COLUMN episode_number INTEGER")

    async with db.execute("""
        SELECT id, title, series_title, episode_number
        FROM movies
        WHERE COALESCE(content_kind, 'movie') = 'serial'
        ORDER BY COALESCE(series_title, title) COLLATE NOCASE ASC, id ASC
        """) as cursor:
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
        await db.executemany(
            """
            UPDATE movies
            SET series_title = COALESCE(NULLIF(series_title, ''), ?),
                episode_number = COALESCE(episode_number, ?)
            WHERE id = ?
            """,
            serial_updates,
        )

    await db.execute("""
        CREATE TABLE IF NOT EXISTS favorites (
            user_id INTEGER,
            code TEXT,
            UNIQUE(user_id, code)
        )
        """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS history (
            user_id INTEGER,
            code TEXT,
            UNIQUE(user_id, code)
        )
        """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS serial_groups (
            title TEXT PRIMARY KEY,
            code TEXT UNIQUE NOT NULL,
            description TEXT
        )
        """)
    await _sync_all_serial_groups(db)
    await _normalize_collection_serial_codes(db, "favorites")
    await _normalize_collection_serial_codes(db, "history")
    await db.execute("""
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            text TEXT,
            file_id TEXT,
            status TEXT DEFAULT 'pending'
        )
        """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            first_seen TEXT,
            last_seen TEXT
        )
        """)
    await _ensure_users_tracking_columns(db)
    await db.execute("""
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
        """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS movie_views (
            code TEXT PRIMARY KEY,
            views INTEGER DEFAULT 0,
            last_viewed_at TEXT
        )
        """)
    await _ensure_movie_views_columns(db)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS movie_unique_views (
            code TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            first_viewed_at TEXT NOT NULL,
            PRIMARY KEY(code, user_id)
        )
        """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS daily_stats (
            day TEXT,
            metric TEXT,
            value INTEGER DEFAULT 0,
            UNIQUE(day, metric)
        )
        """)
    await db.execute("""
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
        """)
    await db.execute("""
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
        """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS sponsor_channels (
            id TEXT PRIMARY KEY,
            url TEXT,
            name TEXT
        )
        """)

    async with db.execute("SELECT id, url, name FROM sponsor_channels") as cursor:
        channel_rows = await cursor.fetchall()

    for channel_id, channel_url, channel_name in channel_rows:
        normalized_id = _normalize_sponsor_channel_id(channel_id)
        if normalized_id == channel_id or not normalized_id:
            continue

        await db.execute(
            """
            INSERT OR REPLACE INTO sponsor_channels (id, url, name)
            VALUES (?, ?, ?)
            """,
            (normalized_id, channel_url, channel_name),
        )
        await db.execute("DELETE FROM sponsor_channels WHERE id=?", (channel_id,))
    await db.execute("""
        CREATE TABLE IF NOT EXISTS user_feature_trials (
            user_id INTEGER NOT NULL,
            feature TEXT NOT NULL,
            used_at TEXT NOT NULL,
            PRIMARY KEY(user_id, feature)
        )
        """)

    async with db.execute("PRAGMA table_info(helper_admins)") as cursor:
        helper_columns = {row[1] for row in await cursor.fetchall()}
    if "can_manage_channels" not in helper_columns:
        await db.execute(
            "ALTER TABLE helper_admins ADD COLUMN can_manage_channels INTEGER NOT NULL DEFAULT 0"
        )

    await db.execute("CREATE INDEX IF NOT EXISTS idx_movies_code ON movies(code)")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status)"
    )
    await db.execute("CREATE INDEX IF NOT EXISTS idx_fav_user ON favorites(user_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_hist_user ON history(user_id)")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_users_last_seen ON users(last_seen)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_users_blocked_last_seen ON users(is_blocked, last_seen)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_helper_admins_added_at ON helper_admins(added_at)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_daily_stats_metric_day ON daily_stats(metric, day)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_movie_unique_views_user ON movie_unique_views(user_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_ads_status_expires ON ads(status, expires_at)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_ad_deliveries_status ON ad_deliveries(ad_id, status)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_feature_trials_feature ON user_feature_trials(feature)"
    )

    now_text = _format_timestamp()
    await db.execute(
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
    await db.commit()


async def close_db() -> None:
    global db

    if db is None:
        return

    with suppress(aiosqlite.Error):
        await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    await db.close()
    db = None
    movie_cache.clear()
    serial_group_cache.clear()
    fav_cache.clear()
    user_activity_cache.clear()
    view_tracking_exclusion_cache.clear()
    global channels_cache
    channels_cache = None


async def get_sponsor_channels() -> list[dict[str, str]]:
    global channels_cache
    if channels_cache is not None:
        return channels_cache

    connection = _get_db()
    async with connection.execute(
        "SELECT id, url, name FROM sponsor_channels"
    ) as cursor:
        rows = await cursor.fetchall()

    channels_cache = [{"id": row[0], "url": row[1], "name": row[2]} for row in rows]
    return channels_cache


async def add_sponsor_channel(channel_id: str, url: str, name: str) -> None:
    normalized_id = _normalize_sponsor_channel_id(channel_id)
    connection = _get_db()
    await connection.execute(
        """
        INSERT INTO sponsor_channels (id, url, name) 
        VALUES (?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET url=excluded.url, name=excluded.name
        """,
        (normalized_id, url, name),
    )
    await connection.commit()
    global channels_cache
    channels_cache = None


async def remove_sponsor_channel(channel_id: str) -> None:
    await _execute("DELETE FROM sponsor_channels WHERE id=?", (channel_id,))
    global channels_cache
    channels_cache = None


async def add_movie(
    code: str,
    title: str,
    description: str,
    file_id: str,
    content_kind: str = "movie",
    *,
    series_title: str | None = None,
    episode_number: int | None = None,
) -> bool:
    connection = _get_db()
    normalized_kind = _normalize_content_kind(content_kind)
    normalized_series_title = (
        _serial_base_title(title, series_title) if normalized_kind == "serial" else None
    )
    normalized_episode_number = (
        episode_number
        if normalized_kind == "serial" and episode_number and episode_number > 0
        else None
    )

    try:
        await connection.execute(
            """
            INSERT INTO movies (
                code,
                title,
                description,
                file_id,
                content_kind,
                series_title,
                episode_number
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                code,
                title,
                description,
                file_id,
                normalized_kind,
                normalized_series_title,
                normalized_episode_number,
            ),
        )
    except aiosqlite.IntegrityError:
        return False

    if normalized_kind == "serial" and normalized_series_title:
        await _sync_serial_group(connection, normalized_series_title)

    await connection.commit()

    movie_cache[code] = (
        _display_title(
            title,
            normalized_kind,
            series_title=normalized_series_title,
            episode_number=normalized_episode_number,
        ),
        description,
        file_id,
        normalized_kind,
    )
    return True


async def add_movie_auto_code(
    title: str,
    description: str,
    file_id: str,
    content_kind: str = "movie",
    *,
    series_title: str | None = None,
    episode_number: int | None = None,
) -> str | None:
    for _ in range(100):
        code = await _next_available_numeric_code(_get_db())
        ok = await add_movie(
            code,
            title,
            description,
            file_id,
            content_kind=content_kind,
            series_title=series_title,
            episode_number=episode_number,
        )
        if ok:
            return code

    return None


async def _increment_daily_stat(
    metric: str,
    amount: int = 1,
    *,
    connection: aiosqlite.Connection | None = None,
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
    now_monotonic = monotonic()
    last_touch = user_activity_cache.get(user_id)
    if (
        not force
        and last_touch is not None
        and now_monotonic - last_touch < USER_TOUCH_TTL_SECONDS
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
    user_activity_cache[user_id] = now_monotonic


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
    async with connection.execute("""
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
        """) as cursor:
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
    return excluded


async def record_movie_view(
    code: str,
    *,
    viewer_user_id: int | None = None,
) -> bool:
    if viewer_user_id is None:
        return False

    if (
        await _is_view_tracking_excluded(viewer_user_id)
    ):
        return False

    connection = _get_db()
    await _ensure_movie_views_columns(connection)
    now_text = _format_timestamp()
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


async def get_movie(code: str) -> tuple[str, str, str, str] | None:
    if code in movie_cache:
        return movie_cache[code]

    connection = _get_db()
    async with connection.execute(
        """
        SELECT
            title,
            description,
            file_id,
            COALESCE(content_kind, 'movie'),
            series_title,
            episode_number
        FROM movies
        WHERE code=?
        """,
        (code,),
    ) as cursor:
        data = await cursor.fetchone()

    if data:
        movie_cache[code] = (
            _display_title(
                data[0],
                data[3],
                series_title=data[4],
                episode_number=data[5],
            ),
            data[1],
            data[2],
            _normalize_content_kind(data[3]),
        )

    return movie_cache.get(code)


async def update_movie_title(code: str, title: str) -> bool:
    normalized_code = (code or "").strip()
    normalized_title = (title or "").strip()
    if not normalized_code or not normalized_title:
        return False

    connection = _get_db()
    async with connection.execute(
        """
        SELECT
            title,
            COALESCE(content_kind, 'movie'),
            series_title
        FROM movies
        WHERE code=?
        """,
        (normalized_code,),
    ) as cursor:
        row = await cursor.fetchone()

    if row is None:
        return False

    current_title, content_kind, series_title = row
    normalized_kind = _normalize_content_kind(content_kind)

    if normalized_kind == "serial":
        current_base_title = _serial_base_title(current_title, series_title)
        if not current_base_title:
            return False

        await connection.execute(
            """
            UPDATE movies
            SET title=?, series_title=?
            WHERE COALESCE(content_kind, 'movie') = 'serial'
              AND COALESCE(NULLIF(series_title, ''), title) = ?
            """,
            (normalized_title, normalized_title, current_base_title),
        )
        await connection.execute(
            """
            UPDATE serial_groups
            SET title=?
            WHERE title=?
            """,
            (normalized_title, current_base_title),
        )
        await _sync_serial_group(connection, normalized_title)
        _clear_serial_group_cache(title=current_base_title)
        _clear_serial_group_cache(title=normalized_title)
    else:
        cursor = await connection.execute(
            "UPDATE movies SET title=? WHERE code=?",
            (normalized_title, normalized_code),
        )
        if cursor.rowcount <= 0:
            return False

    await connection.commit()
    movie_cache.clear()
    return True


async def update_movie_description(code: str, description: str) -> bool:
    normalized_code = (code or "").strip()
    normalized_description = (description or "").strip()
    if not normalized_code or not normalized_description:
        return False

    connection = _get_db()
    async with connection.execute(
        """
        SELECT
            title,
            COALESCE(content_kind, 'movie'),
            series_title
        FROM movies
        WHERE code=?
        """,
        (normalized_code,),
    ) as cursor:
        row = await cursor.fetchone()

    if row is None:
        return False

    current_title, content_kind, series_title = row
    normalized_kind = _normalize_content_kind(content_kind)
    cursor = await connection.execute(
        "UPDATE movies SET description=? WHERE code=?",
        (normalized_description, normalized_code),
    )
    if cursor.rowcount <= 0:
        return False

    if normalized_kind == "serial":
        current_base_title = _serial_base_title(current_title, series_title)
        if current_base_title:
            await _sync_serial_group(connection, current_base_title)
            _clear_serial_group_cache(title=current_base_title)

    await connection.commit()
    movie_cache.clear()
    return True


async def update_movie_file_id(code: str, file_id: str) -> bool:
    normalized_code = (code or "").strip()
    normalized_file_id = (file_id or "").strip()
    if not normalized_code or not normalized_file_id:
        return False

    connection = _get_db()
    cursor = await connection.execute(
        "UPDATE movies SET file_id=? WHERE code=?",
        (normalized_file_id, normalized_code),
    )
    await connection.commit()

    if cursor.rowcount <= 0:
        return False

    cached_movie = movie_cache.get(normalized_code)
    if cached_movie is not None:
        cached_title, cached_description, _cached_file_id, cached_kind = cached_movie
        movie_cache[normalized_code] = (
            cached_title,
            cached_description,
            normalized_file_id,
            cached_kind,
        )

    return True


async def get_movies_by_codes(
    codes: Sequence[str],
) -> dict[str, tuple[str, str, str, str]]:
    ordered_codes = list(dict.fromkeys(codes))
    if not ordered_codes:
        return {}

    missing_group_codes = [
        code
        for code in ordered_codes
        if code not in serial_group_cache
    ]

    if missing_group_codes:
        placeholders = _placeholders(missing_group_codes)
        connection = _get_db()
        async with connection.execute(
            f"""
            SELECT code, title, COALESCE(description, '')
            FROM serial_groups
            WHERE code IN ({placeholders})
            """,
            tuple(missing_group_codes),
        ) as cursor:
            rows = await cursor.fetchall()

        for code, title, description in rows:
            serial_group_cache[code] = _serial_group_entry(title, description)

    group_codes = {code for code in ordered_codes if code in serial_group_cache}
    missing_codes = [
        code
        for code in ordered_codes
        if code not in group_codes and code not in movie_cache
    ]

    if missing_codes:
        placeholders = _placeholders(missing_codes)
        connection = _get_db()
        async with connection.execute(
            f"""
            SELECT
                code,
                title,
                description,
                file_id,
                COALESCE(content_kind, 'movie'),
                series_title,
                episode_number
            FROM movies
            WHERE code IN ({placeholders})
            """,
            tuple(missing_codes),
        ) as cursor:
            rows = await cursor.fetchall()

        for (
            code,
            title,
            description,
            file_id,
            content_kind,
            series_title,
            episode_number,
        ) in rows:
            movie_cache[code] = (
                _display_title(
                    title,
                    content_kind,
                    series_title=series_title,
                    episode_number=episode_number,
                ),
                description,
                file_id,
                _normalize_content_kind(content_kind),
            )

    resolved: dict[str, tuple[str, str, str, str]] = {}
    for code in ordered_codes:
        if code in serial_group_cache:
            resolved[code] = serial_group_cache[code]
        elif code in movie_cache:
            resolved[code] = movie_cache[code]

    return resolved


async def get_all_movies() -> list[tuple[str, str, str]]:
    connection = _get_db()
    async with connection.execute("""
        SELECT
            code,
            title,
            COALESCE(content_kind, 'movie'),
            series_title,
            episode_number
        FROM movies
        ORDER BY id DESC
        """) as cursor:
        rows = await cursor.fetchall()

    return [
        (
            code,
            _display_title(
                title,
                content_kind,
                series_title=series_title,
                episode_number=episode_number,
            ),
            _normalize_content_kind(content_kind),
        )
        for code, title, content_kind, series_title, episode_number in rows
    ]


async def search_movies_by_text(
    query: str, limit: int = 50
) -> list[tuple[str, str, str, str]]:
    connection = _get_db()
    raw_query = (query or "").strip()
    if not raw_query or limit <= 0:
        return []

    normalized_query = _normalize_title_for_match(raw_query)
    query_tokens = [token for token in normalized_query.split() if token]

    search_terms: list[str] = []
    for candidate in [raw_query, normalized_query, *query_tokens]:
        value = (candidate or "").strip()
        if value and value not in search_terms:
            search_terms.append(value)

    if not search_terms:
        return []

    oversample_limit = max(limit * 8, 40)

    def _search_where(columns: Sequence[str]) -> tuple[str, list[str]]:
        clauses: list[str] = []
        params: list[str] = []
        for term in search_terms:
            pattern = f"%{term.casefold()}%"
            for column in columns:
                clauses.append(f"LOWER(COALESCE({column}, '')) LIKE ?")
                params.append(pattern)
        return " OR ".join(clauses), params

    def _contains_all_tokens(text: str) -> bool:
        return bool(query_tokens) and all(token in text for token in query_tokens)

    def _score_candidate(
        *,
        code: str,
        title: str,
        description: str,
        content_kind: str,
        series_title: str | None = None,
        episode_number: int | None = None,
    ) -> int:
        normalized_code = (code or "").strip().casefold()
        normalized_title = _normalize_title_for_match(title)
        normalized_series = _normalize_title_for_match(series_title)
        normalized_description = _normalize_title_for_match(description)
        display_title = _display_title(
            title,
            content_kind,
            series_title=series_title,
            episode_number=episode_number,
        )
        normalized_display = _normalize_title_for_match(display_title)

        score = 0

        if normalized_query:
            if normalized_code == normalized_query:
                score += 1200
            elif normalized_code.startswith(normalized_query):
                score += 900
            elif normalized_query in normalized_code:
                score += 700

            for normalized_text, exact_weight, prefix_weight, token_weight in (
                (normalized_display, 1000, 760, 90),
                (normalized_title, 940, 700, 80),
                (normalized_series, 920, 680, 70),
            ):
                if not normalized_text:
                    continue
                if normalized_text == normalized_query:
                    score = max(score, exact_weight)
                elif normalized_text.startswith(normalized_query):
                    score = max(score, prefix_weight)
                elif normalized_query in normalized_text:
                    score = max(score, prefix_weight - 120)

                if _contains_all_tokens(normalized_text):
                    score += token_weight + (10 * len(query_tokens))

                score += sum(18 for token in query_tokens if token in normalized_text)

        if normalized_description and _contains_all_tokens(normalized_description):
            score += 25

        if content_kind == "serial" and episode_number:
            score -= 20

        return score

    serial_where, serial_params = _search_where(("code", "title"))
    async with connection.execute(
        f"""
        SELECT code, title, COALESCE(description, '')
        FROM serial_groups
        WHERE {serial_where}
        ORDER BY rowid DESC, title COLLATE NOCASE ASC
        LIMIT ?
        """,
        (*serial_params, oversample_limit),
    ) as cursor:
        serial_rows = await cursor.fetchall()

    movie_where, movie_params = _search_where(("code", "title", "series_title"))
    async with connection.execute(
        f"""
        SELECT
            code,
            title,
            description,
            file_id,
            COALESCE(content_kind, 'movie'),
            series_title,
            episode_number
        FROM movies
        WHERE {movie_where}
        ORDER BY id DESC
        LIMIT ?
        """,
        (*movie_params, oversample_limit),
    ) as cursor:
        movie_rows = await cursor.fetchall()

    candidates: list[tuple[int, int, str, str, str, str, str]] = []
    for code, title, description in serial_rows:
        serial_group_cache[code] = _serial_group_entry(title, description)
        candidates.append(
            (
                _score_candidate(
                    code=code,
                    title=title,
                    description=description,
                    content_kind="serial",
                ),
                0,
                code,
                title,
                description,
                "",
                _normalize_title_for_match(title),
            )
        )

    for (
        code,
        title,
        description,
        file_id,
        content_kind,
        series_title,
        episode_number,
    ) in movie_rows:
        normalized_kind = _normalize_content_kind(content_kind)
        display_title = _display_title(
            title,
            content_kind,
            series_title=series_title,
            episode_number=episode_number,
        )
        candidates.append(
            (
                _score_candidate(
                    code=code,
                    title=title,
                    description=description,
                    content_kind=content_kind,
                    series_title=series_title,
                    episode_number=episode_number,
                ),
                1,
                code,
                display_title,
                description,
                file_id,
                _normalize_title_for_match(_serial_base_title(title, series_title))
                if normalized_kind == "serial"
                else "",
            )
        )

    candidates.sort(
        key=lambda item: (
            -item[0],
            item[1],
            len(item[3]),
            item[3].casefold(),
            item[2].casefold(),
        )
    )

    results: list[tuple[str, str, str, str]] = []
    seen_codes: set[str] = set()
    seen_serial_titles: set[str] = set()
    for _score, _source_rank, code, title, description, file_id, serial_title_key in candidates:
        if code in seen_codes:
            continue
        if serial_title_key and serial_title_key in seen_serial_titles:
            continue

        results.append((code, title, description, file_id))
        seen_codes.add(code)
        if serial_title_key:
            seen_serial_titles.add(serial_title_key)

        if len(results) >= limit:
            break

    return results


async def get_serial_titles() -> list[str]:
    connection = _get_db()
    async with connection.execute("""
        SELECT COALESCE(NULLIF(series_title, ''), title) AS base_title
        FROM movies
        WHERE COALESCE(content_kind, 'movie') = 'serial'
        GROUP BY base_title
        ORDER BY MAX(id) DESC, base_title COLLATE NOCASE ASC
        """) as cursor:
        rows = await cursor.fetchall()

    return [title for (title,) in rows if title]


async def get_serial_groups() -> list[tuple[str, str, str]]:
    connection = _get_db()
    async with connection.execute(
        """
        SELECT
            code,
            title,
            COALESCE(description, '')
        FROM serial_groups
        ORDER BY title COLLATE NOCASE ASC
        """
    ) as cursor:
        rows = await cursor.fetchall()

    for code, title, description in rows:
        serial_group_cache[code] = _serial_group_entry(title, description)

    return [(code, title, description) for code, title, description in rows]


async def get_movies_for_serial_base(
    base_title: str,
    *,
    limit: int = 50,
) -> list[tuple[str, str, str, str]]:
    normalized_base = _serial_base_title(base_title)
    if not normalized_base:
        return []

    like_query = f"{normalized_base}%"
    connection = _get_db()
    async with connection.execute(
        """
        SELECT
            code,
            title,
            COALESCE(description, ''),
            file_id
        FROM movies
        WHERE COALESCE(content_kind, 'movie') = 'movie'
          AND title LIKE ? COLLATE NOCASE
        ORDER BY id ASC
        LIMIT ?
        """,
        (like_query, limit),
    ) as cursor:
        rows = await cursor.fetchall()

    return [
        (
            str(code),
            str(title),
            str(description),
            str(file_id),
        )
        for code, title, description, file_id in rows
    ]


async def get_serial_group(code: str) -> tuple[str, str, str] | None:
    if code in serial_group_cache:
        cached = serial_group_cache[code]
        return code, cached[0], cached[1]

    connection = _get_db()
    async with connection.execute(
        """
        SELECT code, title, COALESCE(description, '')
        FROM serial_groups
        WHERE code=?
        """,
        (code,),
    ) as cursor:
        row = await cursor.fetchone()

    if row is None:
        return None

    serial_group_cache[row[0]] = _serial_group_entry(row[1], row[2])
    return row[0], row[1], row[2]


async def get_serial_group_for_lookup(code: str) -> tuple[str, str, str] | None:
    direct_group = await get_serial_group(code)
    if direct_group is not None:
        return direct_group

    connection = _get_db()
    async with connection.execute(
        """
        SELECT
            groups.code,
            groups.title,
            COALESCE(groups.description, '')
        FROM movies
        JOIN serial_groups AS groups
          ON groups.title = COALESCE(NULLIF(movies.series_title, ''), movies.title)
        WHERE movies.code=?
          AND COALESCE(movies.content_kind, 'movie') = 'serial'
        LIMIT 1
        """,
        (code,),
    ) as cursor:
        row = await cursor.fetchone()

    if row is None:
        return None

    serial_group_cache[row[0]] = _serial_group_entry(row[1], row[2])
    return row[0], row[1], row[2]


async def get_serial_episodes(
    group_code: str,
) -> list[tuple[str, int, str, str]]:
    group = await get_serial_group(group_code)
    if group is None:
        return []

    _, title, _description = group
    connection = _get_db()
    async with connection.execute(
        """
        WITH ranked AS (
            SELECT
                code,
                COALESCE(episode_number, 0) AS episode_number,
                COALESCE(description, '') AS description,
                COALESCE(file_id, '') AS file_id,
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY COALESCE(episode_number, 0)
                    ORDER BY
                        CASE WHEN TRIM(COALESCE(file_id, '')) = '' THEN 1 ELSE 0 END,
                        id DESC
                ) AS row_rank
            FROM movies
            WHERE COALESCE(content_kind, 'movie') = 'serial'
              AND COALESCE(NULLIF(series_title, ''), title) = ?
        )
        SELECT
            code,
            episode_number,
            description,
            file_id
        FROM ranked
        WHERE row_rank = 1
        ORDER BY
            CASE WHEN episode_number <= 0 THEN 1 ELSE 0 END,
            episode_number ASC,
            id DESC
        """,
        (title,),
    ) as cursor:
        rows = await cursor.fetchall()

    return [
        (
            code,
            int(episode_number or 0),
            description,
            file_id or "",
        )
        for code, episode_number, description, file_id in rows
    ]


async def get_serial_episode(
    group_code: str,
    episode_code: str,
) -> tuple[str, str, str, str] | None:
    group = await get_serial_group(group_code)
    if group is None:
        return None

    _, title, _description = group
    connection = _get_db()
    async with connection.execute(
        """
        SELECT
            title,
            description,
            file_id,
            COALESCE(content_kind, 'movie'),
            series_title,
            episode_number
        FROM movies
        WHERE code=?
          AND COALESCE(content_kind, 'movie') = 'serial'
          AND COALESCE(NULLIF(series_title, ''), title) = ?
        LIMIT 1
        """,
        (episode_code, title),
    ) as cursor:
        row = await cursor.fetchone()

    if row is None:
        return None

    return (
        _display_title(
            row[0],
            row[3],
            series_title=row[4],
            episode_number=row[5],
        ),
        row[1],
        row[2],
        _normalize_content_kind(row[3]),
    )


async def is_content_code_taken(code: str) -> bool:
    connection = _get_db()
    return await _content_code_exists(connection, code)


async def get_next_serial_episode_number(series_title: str) -> int:
    connection = _get_db()
    normalized_title = _serial_base_title(series_title)
    async with connection.execute(
        """
        SELECT COALESCE(MAX(episode_number), 0)
        FROM movies
        WHERE COALESCE(content_kind, 'movie') = 'serial'
          AND COALESCE(NULLIF(series_title, ''), title) = ?
        """,
        (normalized_title,),
    ) as cursor:
        row = await cursor.fetchone()

    current_max = row[0] if row and row[0] is not None else 0
    return int(current_max) + 1


async def delete_movie(code: str) -> None:
    connection = _get_db()
    async with connection.execute(
        """
        SELECT
            title,
            COALESCE(content_kind, 'movie'),
            series_title
        FROM movies
        WHERE code=?
        """,
        (code,),
    ) as cursor:
        row = await cursor.fetchone()

    await connection.execute("DELETE FROM movies WHERE code=?", (code,))
    await connection.execute("DELETE FROM favorites WHERE code=?", (code,))
    await connection.execute("DELETE FROM history WHERE code=?", (code,))
    await connection.execute("DELETE FROM movie_views WHERE code=?", (code,))
    await connection.execute("DELETE FROM movie_unique_views WHERE code=?", (code,))

    if row is not None and _normalize_content_kind(row[1]) == "serial":
        await _sync_serial_group(connection, _serial_base_title(row[0], row[2]))

    await connection.commit()
    movie_cache.pop(code, None)
    serial_group_cache.pop(code, None)
    for favorites in fav_cache.values():
        favorites.discard(code)


async def add_favorite(user_id: int, code: str) -> None:
    await _execute(
        "INSERT OR IGNORE INTO favorites (user_id, code) VALUES (?, ?)",
        (user_id, code),
    )
    fav_cache.setdefault(user_id, set()).add(code)


async def remove_favorite(user_id: int, code: str) -> None:
    await _execute(
        "DELETE FROM favorites WHERE user_id=? AND code=?",
        (user_id, code),
    )

    if user_id in fav_cache:
        fav_cache[user_id].discard(code)


async def is_favorite(user_id: int, code: str) -> bool:
    if code in fav_cache.get(user_id, set()):
        return True

    connection = _get_db()
    async with connection.execute(
        "SELECT 1 FROM favorites WHERE user_id=? AND code=?",
        (user_id, code),
    ) as cursor:
        result = await cursor.fetchone()

    if result:
        fav_cache.setdefault(user_id, set()).add(code)

    return result is not None


async def get_favorites(user_id: int) -> list[tuple[str]]:
    connection = _get_db()
    async with connection.execute(
        "SELECT code FROM favorites WHERE user_id=? ORDER BY rowid DESC",
        (user_id,),
    ) as cursor:
        data = await cursor.fetchall()

    fav_cache[user_id] = {code for (code,) in data}
    return data


async def count_favorites(user_id: int) -> int:
    connection = _get_db()
    async with connection.execute(
        "SELECT COUNT(*) FROM favorites WHERE user_id=?",
        (user_id,),
    ) as cursor:
        count = await cursor.fetchone()

    return count[0] if count else 0


async def get_favorites_page(user_id: int, limit: int, offset: int = 0) -> list[str]:
    connection = _get_db()
    async with connection.execute(
        "SELECT code FROM favorites WHERE user_id=? ORDER BY rowid DESC LIMIT ? OFFSET ?",
        (user_id, limit, offset),
    ) as cursor:
        rows = await cursor.fetchall()

    codes = [code for (code,) in rows]
    if codes:
        fav_cache.setdefault(user_id, set()).update(codes)

    return codes


async def add_history(user_id: int, code: str) -> None:
    connection = _get_db()
    await connection.execute(
        "INSERT OR REPLACE INTO history (user_id, code) VALUES (?, ?)",
        (user_id, code),
    )
    await connection.execute(
        """
        DELETE FROM history
        WHERE user_id=?
          AND rowid NOT IN (
              SELECT rowid FROM history
              WHERE user_id=?
              ORDER BY rowid DESC
              LIMIT ?
          )
        """,
        (user_id, user_id, MAX_HISTORY_ITEMS),
    )
    await connection.commit()


async def get_history(user_id: int) -> list[tuple[str]]:
    connection = _get_db()
    async with connection.execute(
        "SELECT code FROM history WHERE user_id=? ORDER BY rowid DESC",
        (user_id,),
    ) as cursor:
        return await cursor.fetchall()


async def count_history(user_id: int) -> int:
    connection = _get_db()
    async with connection.execute(
        "SELECT COUNT(*) FROM history WHERE user_id=?",
        (user_id,),
    ) as cursor:
        count = await cursor.fetchone()

    return count[0] if count else 0


async def get_history_page(user_id: int, limit: int, offset: int = 0) -> list[str]:
    connection = _get_db()
    async with connection.execute(
        "SELECT code FROM history WHERE user_id=? ORDER BY rowid DESC LIMIT ? OFFSET ?",
        (user_id, limit, offset),
    ) as cursor:
        rows = await cursor.fetchall()

    return [code for (code,) in rows]


async def add_request(user_id: int, text: str, file_id: str | None) -> None:
    connection = _get_db()
    await connection.execute(
        "INSERT INTO requests (user_id, text, file_id) VALUES (?, ?, ?)",
        (user_id, text, file_id),
    )
    await _increment_daily_stat("requests", connection=connection)
    await connection.commit()


async def get_pending_requests() -> list[tuple[int, int, str, str | None]]:
    connection = _get_db()
    async with connection.execute(
        "SELECT id, user_id, text, file_id FROM requests WHERE status IN ('pending', 'accepted') ORDER BY id DESC"
    ) as cursor:
        return await cursor.fetchall()


async def get_request(request_id: int) -> tuple[int, int, str, str | None, str] | None:
    connection = _get_db()
    async with connection.execute(
        "SELECT id, user_id, text, file_id, status FROM requests WHERE id=?",
        (request_id,),
    ) as cursor:
        return await cursor.fetchone()


async def update_request_status(request_id: int, status: str) -> None:
    await _execute(
        "UPDATE requests SET status=? WHERE id=?",
        (status, request_id),
    )


async def delete_request(request_id: int) -> None:
    await _execute("DELETE FROM requests WHERE id=?", (request_id,))


async def get_dashboard_summary() -> dict[str, int]:
    connection = _get_db()
    today_start = _format_timestamp(local_day_start_utc())
    today_cutoff = _format_timestamp(_utc_now() - timedelta(days=1))
    week_cutoff = _format_timestamp(local_day_start_utc(6))
    has_block_tracking = await _ensure_users_tracking_columns(connection)
    visible_users_filter = _visible_user_filter(
        has_block_tracking=has_block_tracking,
        from_users_table=True,
    )
    visible_actor_filter = _visible_user_filter(
        has_block_tracking=has_block_tracking,
        from_users_table=False,
    )

    queries = {
        "total_users": (
            f"SELECT COUNT(*) FROM users WHERE {visible_users_filter}",
            (ADMIN_ID,),
        ),
        "all_time_users": (
            "SELECT COUNT(*) FROM users "
            "WHERE user_id != ? "
            "AND user_id NOT IN (SELECT user_id FROM helper_admins)",
            (ADMIN_ID,),
        ),
        "entered_today": (
            "SELECT COUNT(*) FROM users "
            "WHERE last_seen >= ? "
            "AND user_id != ? "
            "AND user_id NOT IN (SELECT user_id FROM helper_admins)",
            (today_start, ADMIN_ID),
        ),
        "new_subscribers_today": (
            "SELECT COUNT(*) FROM users "
            "WHERE first_seen >= ? "
            "AND user_id != ? "
            "AND user_id NOT IN (SELECT user_id FROM helper_admins)",
            (today_start, ADMIN_ID),
        ),
        "blocked_users": (
            (
                "SELECT COUNT(*) FROM users "
                "WHERE COALESCE(is_blocked, 0) = 1 "
                "AND user_id != ? "
                "AND user_id NOT IN (SELECT user_id FROM helper_admins)"
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
            f"SELECT COUNT(*) FROM requests WHERE {visible_actor_filter}",
            (ADMIN_ID,),
        ),
        "pending_requests": (
            "SELECT COUNT(*) FROM requests "
            "WHERE status IN ('pending', 'accepted') "
            f"AND {visible_actor_filter}",
            (ADMIN_ID,),
        ),
        "completed_requests": (
            "SELECT COUNT(*) FROM requests "
            "WHERE status='completed' "
            f"AND {visible_actor_filter}",
            (ADMIN_ID,),
        ),
        "rejected_requests": (
            "SELECT COUNT(*) FROM requests "
            "WHERE status='rejected' "
            f"AND {visible_actor_filter}",
            (ADMIN_ID,),
        ),
    }

    result: dict[str, int] = {}
    for key, (query, params) in queries.items():
        async with connection.execute(query, params) as cursor:
            row = await cursor.fetchone()
        result[key] = row[0] if row and row[0] is not None else 0

    # Legacy key kept for existing callers that still read joined_today.
    result["joined_today"] = result["entered_today"]
    return result


async def get_request_status_counts() -> dict[str, int]:
    connection = _get_db()
    has_block_tracking = await _ensure_users_tracking_columns(connection)
    visible_actor_filter = _visible_user_filter(
        has_block_tracking=has_block_tracking,
        from_users_table=False,
    )
    counts = {
        "pending": 0,
        "accepted": 0,
        "completed": 0,
        "rejected": 0,
    }

    async with connection.execute(
        """
        SELECT status, COUNT(*)
        FROM requests
        WHERE """
        + visible_actor_filter
        + """
        GROUP BY status
        """,
        (ADMIN_ID,),
    ) as cursor:
        rows = await cursor.fetchall()

    for status, value in rows:
        counts[status] = value

    return counts


async def get_daily_metric_series(days: int = 7) -> dict[str, list[int]]:
    metrics = ("requests", "movie_views", "new_users")
    labels = _day_keys(days)
    connection = _get_db()
    series = {metric: {label: 0 for label in labels} for metric in metrics}

    async with connection.execute(
        """
        SELECT day, metric, value
        FROM daily_stats
        WHERE day >= ?
          AND metric IN (?, ?, ?)
        ORDER BY day ASC
        """,
        (labels[0], *metrics),
    ) as cursor:
        rows = await cursor.fetchall()

    for day, metric, value in rows:
        if metric in series and day in series[metric]:
            series[metric][day] = value

    return {
        "labels": labels,
        **{metric: [series[metric][label] for label in labels] for metric in metrics},
    }


async def get_top_viewed_movies(limit: int = 5) -> list[tuple[str, str, int, int]]:
    connection = _get_db()
    async with connection.execute(
        """
        SELECT
            mv.code,
            CASE
                WHEN COALESCE(m.content_kind, 'movie') = 'serial'
                     AND COALESCE(m.episode_number, 0) > 0
                THEN COALESCE(NULLIF(m.series_title, ''), m.title) || ' ' || m.episode_number || '-qism'
                ELSE COALESCE(m.title, 'Noma''lum kino')
            END,
            mv.views,
            COALESCE(mv.unique_views, mv.views) AS unique_views
        FROM movie_views mv
        LEFT JOIN movies m ON m.code = mv.code
        ORDER BY
            mv.views DESC,
            unique_views DESC,
            COALESCE(mv.last_viewed_at, '') DESC,
            mv.code ASC
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


async def create_ad_campaign(
    admin_id: int,
    content_type: str,
    text: str | None,
    file_id: str | None,
    duration_seconds: int,
) -> int:
    connection = _get_db()
    normalized_duration = _normalize_ad_duration(duration_seconds)
    now = _utc_now()
    expires_at = now + timedelta(seconds=normalized_duration)
    has_block_tracking = await _ensure_users_tracking_columns(connection)
    visible_users_filter = _visible_user_filter(
        has_block_tracking=has_block_tracking,
        from_users_table=True,
    )

    async with connection.execute(
        f"SELECT COUNT(*) FROM users WHERE {visible_users_filter}"
        ,
        (ADMIN_ID,),
    ) as cursor:
        row = await cursor.fetchone()
    recipient_total = row[0] if row else 0

    cursor = await connection.execute(
        """
        INSERT INTO ads (
            admin_id,
            content_type,
            text,
            file_id,
            duration_seconds,
            status,
            created_at,
            started_at,
            expires_at,
            recipient_total
        )
        VALUES (?, ?, ?, ?, ?, 'broadcasting', ?, ?, ?, ?)
        """,
        (
            admin_id,
            content_type,
            text,
            file_id,
            normalized_duration,
            _format_timestamp(now),
            _format_timestamp(now),
            _format_timestamp(expires_at),
            recipient_total,
        ),
    )
    await connection.commit()
    return int(cursor.lastrowid)


async def get_ad_campaign(ad_id: int) -> dict[str, Any] | None:
    connection = _get_db()
    async with connection.execute(
        """
        SELECT
            id,
            admin_id,
            content_type,
            text,
            file_id,
            duration_seconds,
            status,
            created_at,
            started_at,
            expires_at,
            closed_at,
            recipient_total,
            delivered_total,
            failed_total,
            deleted_total
        FROM ads
        WHERE id=?
        """,
        (ad_id,),
    ) as cursor:
        row = await cursor.fetchone()

    return _ad_row_to_dict(row)


async def get_ad_status(ad_id: int) -> str | None:
    connection = _get_db()
    async with connection.execute(
        "SELECT status FROM ads WHERE id=?",
        (ad_id,),
    ) as cursor:
        row = await cursor.fetchone()

    return row[0] if row else None


async def get_ads_by_status(
    statuses: Iterable[str], limit: int = 10
) -> list[dict[str, Any]]:
    status_list = tuple(dict.fromkeys(statuses))
    if not status_list:
        return []

    connection = _get_db()
    placeholders = _placeholders(status_list)
    async with connection.execute(
        f"""
        SELECT
            id,
            admin_id,
            content_type,
            text,
            file_id,
            duration_seconds,
            status,
            created_at,
            started_at,
            expires_at,
            closed_at,
            recipient_total,
            delivered_total,
            failed_total,
            deleted_total
        FROM ads
        WHERE status IN ({placeholders})
        ORDER BY
            CASE status
                WHEN 'broadcasting' THEN 0
                WHEN 'stop_requested' THEN 1
                WHEN 'stopping' THEN 2
                WHEN 'active' THEN 3
                WHEN 'cleaning' THEN 4
                ELSE 5
            END,
            COALESCE(expires_at, created_at) ASC,
            id DESC
        LIMIT ?
        """,
        (*status_list, limit),
    ) as cursor:
        rows = await cursor.fetchall()

    return [item for row in rows if (item := _ad_row_to_dict(row)) is not None]


async def get_recent_ads(limit: int = 5) -> list[dict[str, Any]]:
    connection = _get_db()
    async with connection.execute(
        """
        SELECT
            id,
            admin_id,
            content_type,
            text,
            file_id,
            duration_seconds,
            status,
            created_at,
            started_at,
            expires_at,
            closed_at,
            recipient_total,
            delivered_total,
            failed_total,
            deleted_total
        FROM ads
        WHERE status IN ('expired', 'stopped')
        ORDER BY COALESCE(closed_at, expires_at, created_at) DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    ) as cursor:
        rows = await cursor.fetchall()

    return [item for row in rows if (item := _ad_row_to_dict(row)) is not None]


async def get_pending_ad_recipients(ad_id: int, limit: int = 100) -> list[int]:
    connection = _get_db()
    has_block_tracking = await _ensure_users_tracking_columns(connection)
    visible_users_filter = _visible_user_filter(
        has_block_tracking=has_block_tracking,
        user_id_column="u.user_id",
        from_users_table=True,
    )
    async with connection.execute(
        f"""
        SELECT u.user_id
        FROM users u
        WHERE {visible_users_filter}
          AND NOT EXISTS (
            SELECT 1
            FROM ad_deliveries d
            WHERE d.ad_id = ?
              AND d.user_id = u.user_id
        )
        ORDER BY u.user_id ASC
        LIMIT ?
        """,
        (ADMIN_ID, ad_id, limit),
    ) as cursor:
        rows = await cursor.fetchall()

    return [user_id for (user_id,) in rows]


async def record_ad_delivery_batch(
    ad_id: int,
    results: Sequence[tuple[int, int | None, str, str | None]],
) -> None:
    if not results:
        return

    connection = _get_db()
    now_text = _format_timestamp()
    delivered_total = sum(1 for _, _, status, _ in results if status == "sent")
    failed_total = len(results) - delivered_total

    await connection.executemany(
        """
        INSERT OR REPLACE INTO ad_deliveries (
            ad_id,
            user_id,
            message_id,
            status,
            error_text,
            sent_at,
            deleted_at
        )
        VALUES (?, ?, ?, ?, ?, ?, NULL)
        """,
        [
            (
                ad_id,
                user_id,
                message_id,
                status,
                error_text,
                now_text if status == "sent" else None,
            )
            for user_id, message_id, status, error_text in results
        ],
    )
    await connection.execute(
        """
        UPDATE ads
        SET delivered_total = delivered_total + ?,
            failed_total = failed_total + ?
        WHERE id=?
        """,
        (delivered_total, failed_total, ad_id),
    )
    await connection.commit()


async def finish_ad_broadcast(ad_id: int) -> None:
    await _execute(
        """
        UPDATE ads
        SET status = CASE
            WHEN status='broadcasting' THEN 'active'
            WHEN status='stop_requested' THEN 'stopping'
            ELSE status
        END
        WHERE id=?
        """,
        (ad_id,),
    )


async def request_stop_ad(ad_id: int) -> str | None:
    connection = _get_db()
    async with connection.execute(
        "SELECT status FROM ads WHERE id=?",
        (ad_id,),
    ) as cursor:
        row = await cursor.fetchone()

    if not row:
        return None

    status = row[0]
    if status == "broadcasting":
        await connection.execute(
            "UPDATE ads SET status='stop_requested' WHERE id=?",
            (ad_id,),
        )
        await connection.commit()
        return "stop_requested"

    if status == "active":
        await connection.execute(
            "UPDATE ads SET status='stopping' WHERE id=?",
            (ad_id,),
        )
        await connection.commit()
        return "stopping"

    return status


async def claim_ad_for_cleanup() -> dict[str, Any] | None:
    connection = _get_db()
    now_text = _format_timestamp()

    async with connection.execute(
        """
        SELECT id, status
        FROM ads
        WHERE status='stopping'
           OR (status='active' AND expires_at IS NOT NULL AND expires_at <= ?)
        ORDER BY
            CASE WHEN status='stopping' THEN 0 ELSE 1 END,
            COALESCE(expires_at, created_at) ASC,
            id ASC
        LIMIT 1
        """,
        (now_text,),
    ) as cursor:
        row = await cursor.fetchone()

    if row is None:
        return None

    ad_id, current_status = row
    cursor = await connection.execute(
        "UPDATE ads SET status='cleaning' WHERE id=? AND status=?",
        (ad_id, current_status),
    )
    await connection.commit()

    if cursor.rowcount == 0:
        return None

    return {
        "id": ad_id,
        "final_status": "stopped" if current_status == "stopping" else "expired",
    }


async def get_ad_delete_batch(ad_id: int, limit: int = 100) -> list[tuple[int, int]]:
    connection = _get_db()
    async with connection.execute(
        """
        SELECT user_id, message_id
        FROM ad_deliveries
        WHERE ad_id=?
          AND status='sent'
          AND message_id IS NOT NULL
        ORDER BY user_id ASC
        LIMIT ?
        """,
        (ad_id, limit),
    ) as cursor:
        return await cursor.fetchall()


async def record_ad_delete_batch(
    ad_id: int,
    deleted_user_ids: Sequence[int],
    failed_items: Sequence[tuple[int, str]],
) -> None:
    if not deleted_user_ids and not failed_items:
        return

    connection = _get_db()
    now_text = _format_timestamp()

    if deleted_user_ids:
        await connection.executemany(
            """
            UPDATE ad_deliveries
            SET status='deleted',
                error_text=NULL,
                deleted_at=?
            WHERE ad_id=?
              AND user_id=?
            """,
            [(now_text, ad_id, user_id) for user_id in deleted_user_ids],
        )

    if failed_items:
        await connection.executemany(
            """
            UPDATE ad_deliveries
            SET status='delete_failed',
                error_text=?,
                deleted_at=?
            WHERE ad_id=?
              AND user_id=?
            """,
            [
                (error_text, now_text, ad_id, user_id)
                for user_id, error_text in failed_items
            ],
        )

    await connection.execute(
        """
        UPDATE ads
        SET deleted_total = deleted_total + ?
        WHERE id=?
        """,
        (len(deleted_user_ids), ad_id),
    )
    await connection.commit()


async def finish_ad_cleanup(ad_id: int, final_status: str) -> None:
    await _execute(
        """
        UPDATE ads
        SET status=?,
            closed_at=?
        WHERE id=?
        """,
        (final_status, _format_timestamp(), ad_id),
    )
