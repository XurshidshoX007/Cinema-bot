"""Requests handling."""

from .connection import _get_db, _execute
from .users import _increment_daily_stat


async def _is_view_tracking_excluded(user_id: int) -> bool:
    # Lazy import to avoid circular
    from .views import _is_view_tracking_excluded as _excluded

    return await _excluded(user_id)


async def add_request(user_id: int, text: str, file_id: str | None) -> None:
    connection = _get_db()
    should_track_daily_stats = not await _is_view_tracking_excluded(user_id)
    await connection.execute(
        "INSERT INTO requests (user_id, text, file_id) VALUES (?, ?, ?)",
        (user_id, text, file_id),
    )
    if should_track_daily_stats:
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
