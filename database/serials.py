"""Serial groups and episode handling."""

from typing import Sequence
import aiosqlite

from .cache import serial_group_cache
from .connection import _get_db
from .utils import _serial_base_title, _display_title, _serial_group_entry, _normalize_content_kind


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


async def get_serial_titles() -> list[str]:
    connection = _get_db()
    async with connection.execute(
        """
        SELECT COALESCE(NULLIF(series_title, ''), title) AS base_title
        FROM movies
        WHERE COALESCE(content_kind, 'movie') = 'serial'
        GROUP BY base_title
        ORDER BY MAX(id) DESC, base_title COLLATE NOCASE ASC
        """
    ) as cursor:
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


async def get_serial_group(code: str) -> tuple[str, str, str] | None:
    from .cache import serial_group_cache
    from .constants import CACHE_MAX_SERIAL_GROUPS
    from .cache import _trim_cache

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
    _trim_cache(serial_group_cache, CACHE_MAX_SERIAL_GROUPS)
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
