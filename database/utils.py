"""Utility helpers for database package."""

import re
from datetime import UTC, datetime, time as dt_time, timedelta
from typing import Any, Sequence

from .constants import (
    APP_TIMEZONE,
    APP_TIMEZONE_LABEL,
    MAX_AD_DURATION_SECONDS,
    MIN_AD_DURATION_SECONDS,
    ADMIN_PERMISSION_COLUMNS,
    TIMESTAMP_FORMAT,
    UNBLOCKED_USER_SQL,
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


def _base_actor_filter(user_id_column: str = "user_id") -> str:
    return (
        f"{user_id_column} != ? "
        f"AND {user_id_column} NOT IN (SELECT user_id FROM helper_admins)"
    )
