"""Advertising campaigns."""

from datetime import timedelta
from typing import Any, Iterable, Sequence

from config import ADMIN_ID
from .connection import _get_db, _ensure_users_tracking_columns, _execute
from .utils import _ad_row_to_dict, _format_timestamp, _normalize_ad_duration, _placeholders, _utc_now, _visible_user_filter


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
        f"SELECT COUNT(*) FROM users WHERE {visible_users_filter}",
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
