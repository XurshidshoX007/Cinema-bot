"""Watch history."""

from .connection import _get_db
from .constants import MAX_HISTORY_ITEMS


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
