"""Rendering helpers for admin panels."""

from datetime import UTC, datetime

from database import APP_TIMEZONE_LABEL, TIMESTAMP_FORMAT, format_local_timestamp

from .constants import SPARKLINE_BARS, CONTENT_LIST_PAGE_SIZE, CONTENT_LIST_PREVIEW_SIZE
from .content_utils import _content_kind_label, _content_kind_icon, _content_kind_key, _safe_html, _compact_text_preview, _content_list_filter_label


def _helper_admin_label(helper_admin: dict[str, object]) -> str:
    username = helper_admin.get("username")
    if username:
        return f"@{username}"
    return str(helper_admin.get("full_name") or f"Admin {helper_admin['user_id']}")


def _helper_admin_permission_label(permission: str) -> str:
    return {
        "movies": "🎬 Kinolar",
        "requests": "📨 So'rovlar",
        "stats": "📊 Statistika",
        "ads": "📣 Reklama",
        "channels": "Kanallar",
        "helpers": "Yordamchilar",
    }.get(permission, permission)


def _render_helper_admins_panel(helper_admins: list[dict[str, object]]) -> str:
    lines = [
        "👥 Yordamchi adminlar",
        "",
        f"Jami: {len(helper_admins)} ta",
    ]

    if not helper_admins:
        lines.extend(["", "Hali yordamchi admin qo'shilmagan."])
        return "\n".join(lines)

    lines.append("")
    for index, helper_admin in enumerate(helper_admins, start=1):
        permissions = helper_admin["permissions"]
        enabled = [
            _helper_admin_permission_label(permission)
            for permission, is_enabled in permissions.items()
            if is_enabled
        ]
        summary = ", ".join(enabled) if enabled else "Ruxsat berilmagan"
        lines.append(
            f"{index}. {_helper_admin_label(helper_admin)} ({helper_admin['user_id']})"
        )
        lines.append(f"   └ {summary}")

    return "\n".join(lines)


def _render_helper_admin_detail(helper_admin: dict[str, object]) -> str:
    permissions = helper_admin["permissions"]
    enabled = [
        _helper_admin_permission_label(permission)
        for permission, is_enabled in permissions.items()
        if is_enabled
    ]
    permission_text = ", ".join(enabled) if enabled else "Hali ruxsat berilmagan"
    username = helper_admin.get("username")

    lines = [
        "👤 Yordamchi admin",
        "",
        f"ID: {helper_admin['user_id']}",
        f"Ism: {helper_admin.get('full_name') or '-'}",
        f"Username: @{username}" if username else "Username: -",
        f"Ruxsatlar: {permission_text}",
    ]
    return "\n".join(lines)


def _render_content_overview_section(
    title: str,
    items: list[tuple[str, str, str]],
    *,
    empty_text: str,
) -> str:
    lines = [title]
    if not items:
        lines.append(empty_text)
        return "\n".join(lines)

    for index, (code, item_title, _content_kind) in enumerate(
        items[:CONTENT_LIST_PREVIEW_SIZE],
        start=1,
    ):
        lines.append(f"{index}. {item_title}")
        lines.append(f"   └ Kod: {code}")

    remaining = len(items) - CONTENT_LIST_PREVIEW_SIZE
    if remaining > 0:
        lines.append(f"   ... yana {remaining} ta kontent bor")

    return "\n".join(lines)


def _render_content_section_page(
    items: list[tuple[str, str, str]],
    *,
    filter_key: str,
    page: int,
) -> tuple[str, int, int]:
    total_items = len(items)
    total_pages = max(
        1, (total_items + CONTENT_LIST_PAGE_SIZE - 1) // CONTENT_LIST_PAGE_SIZE
    )
    page = min(max(page, 0), total_pages - 1)
    start = page * CONTENT_LIST_PAGE_SIZE
    end = min(total_items, start + CONTENT_LIST_PAGE_SIZE)
    page_items = items[start:end]

    lines = [
        "📚 Kontent ro'yxati",
        f"{_content_list_filter_label(filter_key)} bo'limi",
        "",
        f"Jami: {total_items} ta",
    ]

    if total_items:
        lines.extend(
            [
                f"Sahifa: {page + 1}/{total_pages}",
                f"Ko'rsatilmoqda: {start + 1}-{end}",
                "",
            ]
        )
        for absolute_index, (code, title, _content_kind) in enumerate(
            page_items, start=start + 1
        ):
            lines.append(f"{absolute_index}. {title}")
            lines.append(f"   └ Kod: {code}")
    else:
        lines.extend(["", "Bu bo'limda hali kontent yo'q."])

    return "\n".join(lines), total_pages, page


def _render_content_picker_text(
    *,
    title: str,
    movie_count: int,
    serial_count: int,
    empty_text: str,
    hint_text: str,
) -> str:
    total_count = movie_count + serial_count
    if total_count == 0:
        return empty_text

    return "\n".join(
        [
            title,
            "",
            f"📦 Jami: {total_count} ta",
            f"🎬 Kinolar: {movie_count} ta",
            f"📺 Seriallar: {serial_count} ta",
            "",
            hint_text,
        ]
    )


def _render_delete_panel_text(
    *,
    filter_key: str,
    total_items: int,
    page: int,
    total_pages: int,
    page_items: list[tuple[str, str, str]],
    start_index: int,
) -> str:
    lines = [
        "🗑 Kontentni o'chirish",
        f"{_content_list_filter_label(filter_key)} bo'limi",
        "",
        f"Jami: {total_items} ta",
    ]

    if total_items:
        lines.extend(
            [
                f"Sahifa: {page + 1}/{total_pages}",
                "",
                "O'chirish uchun kodni yuboring.",
                "",
            ]
        )
        for index, (code, title, content_kind) in enumerate(
            page_items, start=start_index + 1
        ):
            lines.append(f"{index}. {_content_kind_icon(content_kind)} {title}")
            lines.append(f"   └ Kod: {code}")
    else:
        lines.extend(["", "Bu bo'limda o'chirish uchun kontent yo'q."])

    return "\n".join(lines)


def _compact_number(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def _sparkline(values: list[int]) -> str:
    if not values:
        return ""

    maximum = max(values)
    if maximum <= 0:
        return "·" * len(values)

    last_index = len(SPARKLINE_BARS) - 1
    return "".join(
        SPARKLINE_BARS[max(0, round((value / maximum) * last_index))]
        for value in values
    )


def _bar_chart(label: str, value: int, maximum: int, width: int = 10) -> str:
    if maximum <= 0 or value <= 0:
        filled = 0
    else:
        filled = max(1, round((value / maximum) * width))

    filled = min(width, filled)
    empty = width - filled
    return f"{label:<12} {'█' * filled}{'░' * empty} {value}"


def _format_recent_users(users: list[tuple[int, str | None, str, str]]) -> str:
    if not users:
        return "Hali ma'lumot yo'q"

    lines = []
    for user_id, username, full_name, last_seen in users:
        label = f"@{username}" if username else full_name
        if len(label) > 24:
            label = f"{label[:21]}..."
        seen_time = format_local_timestamp(last_seen, "%H:%M")
        lines.append(f"• {label} ({user_id}) - {seen_time} {APP_TIMEZONE_LABEL}")

    return "\n".join(lines)


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None

    try:
        return datetime.strptime(value, TIMESTAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def _format_duration(seconds: int) -> str:
    if seconds % 86400 == 0:
        days = seconds // 86400
        return f"{days} kun"
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours} soat"
    minutes = max(1, seconds // 60)
    return f"{minutes} daqiqa"


def _format_time_left(expires_at: str | None) -> str:
    expires = _parse_timestamp(expires_at)
    if expires is None:
        return "Noma'lum"

    remaining = int((expires - datetime.now(UTC)).total_seconds())
    if remaining <= 0:
        return "Yakunlanmoqda"

    days, rest = divmod(remaining, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, _ = divmod(rest, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days} kun")
    if hours:
        parts.append(f"{hours} soat")
    if minutes and not days:
        parts.append(f"{minutes} daqiqa")

    return " ".join(parts[:2]) or "1 daqiqadan kam"


def _ad_status_label(status: str) -> str:
    return {
        "broadcasting": "Yuborilmoqda",
        "stop_requested": "To'xtatilmoqda",
        "active": "Faol",
        "stopping": "To'xtatilmoqda",
        "cleaning": "O'chirilmoqda",
        "expired": "Muddat tugagan",
        "stopped": "Qo'lda to'xtatilgan",
    }.get(status, status)


def _ad_type_label(content_type: str) -> str:
    return {
        "text": "Matn",
        "photo": "Rasm",
        "video": "Video",
        "document": "Fayl",
    }.get(content_type, content_type)


def _ad_preview(content_type: str, text: str | None, limit: int = 42) -> str:
    compact = " ".join((text or "").split())
    if not compact:
        return _ad_type_label(content_type)
    if len(compact) > limit:
        compact = f"{compact[: limit - 3]}..."
    return f"{_ad_type_label(content_type)}: {compact}"


def _ads_button_rows(active_ads: list[dict]) -> list[tuple[int, str]]:
    rows: list[tuple[int, str]] = []
    for ad in active_ads:
        if ad["status"] not in {"broadcasting", "active"}:
            continue
        rows.append((ad["id"], f"{_ad_type_label(ad['content_type'])}"))
    return rows
