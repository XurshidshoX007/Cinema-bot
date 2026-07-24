"""Favorites handling."""

from .cache import fav_cache
from .cache import _trim_cache
from .connection import _get_db, _execute
from .constants import CACHE_MAX_FAVORITES


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
    _trim_cache(fav_cache, CACHE_MAX_FAVORITES)
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
