"""Movie CRUD and search."""

from typing import Sequence
import aiosqlite

from .cache import movie_cache, serial_group_cache, fav_cache
from .constants import CACHE_MAX_MOVIES
from .cache import _trim_cache
from .connection import _get_db
from .serials import (
    _content_code_exists,
    _next_available_numeric_code,
    _sync_serial_group,
    _clear_serial_group_cache,
)
from .utils import (
    _display_title,
    _normalize_content_kind,
    _normalize_title_for_match,
    _placeholders,
    _serial_base_title,
    _serial_group_entry,
)


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
    _trim_cache(movie_cache, CACHE_MAX_MOVIES)
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
        _trim_cache(movie_cache, CACHE_MAX_MOVIES)

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
    async with connection.execute(
        """
        SELECT
            code,
            title,
            COALESCE(content_kind, 'movie'),
            series_title,
            episode_number
        FROM movies
        ORDER BY
            CASE WHEN COALESCE(content_kind, 'movie') = 'serial' THEN 1 ELSE 0 END,
            CASE
                WHEN COALESCE(content_kind, 'movie') = 'serial'
                THEN COALESCE(NULLIF(series_title, ''), title)
            END COLLATE NOCASE ASC,
            CASE
                WHEN COALESCE(content_kind, 'movie') = 'serial'
                     AND COALESCE(episode_number, 0) > 0
                THEN 0
                WHEN COALESCE(content_kind, 'movie') = 'serial'
                THEN 1
                ELSE 0
            END ASC,
            CASE
                WHEN COALESCE(content_kind, 'movie') = 'serial'
                THEN COALESCE(episode_number, 0)
            END ASC,
            CASE WHEN COALESCE(content_kind, 'movie') = 'serial' THEN id END ASC,
            CASE WHEN COALESCE(content_kind, 'movie') != 'serial' THEN id END DESC
        """
    ) as cursor:
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


async def is_content_code_taken(code: str) -> bool:
    connection = _get_db()
    return await _content_code_exists(connection, code)


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
