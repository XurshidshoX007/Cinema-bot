import logging
from datetime import UTC, datetime
from html import escape

logger = logging.getLogger(__name__)

from aiogram import types
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext

from config import STATS_WEBAPP_URL
from database import (
    APP_TIMEZONE_LABEL,
    MAX_AD_DURATION_SECONDS,
    MIN_AD_DURATION_SECONDS,
    TIMESTAMP_FORMAT,
    format_local_timestamp,
    get_all_movies,
    get_daily_metric_series,
    get_dashboard_summary,
    get_next_serial_episode_number,
    get_pending_requests,
    get_recent_ads,
    get_recent_users,
    get_request_status_counts,
    get_serial_titles,
    get_top_viewed_movies,
    local_now_text,
)
from keyboards import (
    content_list_keyboard,
    delete_movie_keyboard,
    request_review_keyboard,
    serial_continue_keyboard,
    serial_mode_keyboard,
    serial_season_choice_keyboard,
    serial_titles_keyboard,
    stats_webapp_keyboard,
)
from services.stats_webapp_auth import build_signed_stats_webapp_url

from .admin_states import AddMovieState, DeleteMovieState

SPARKLINE_BARS = "▁▂▃▄▅▆▇█"
STATS_PANELS = {"overview", "traffic", "movies", "requests"}
SERIAL_TITLES_PAGE_SIZE = 8
CONTENT_LIST_PAGE_SIZE = 8
DELETE_PANEL_PAGE_SIZE = 8
ADMIN_PERMISSION_MOVIES = "movies"
ADMIN_PERMISSION_REQUESTS = "requests"
ADMIN_PERMISSION_STATS = "stats"
ADMIN_PERMISSION_ADS = "ads"


def _content_kind_key(content_kind: str | None) -> str:
    return "serial" if content_kind == "serial" else "movie"


def _content_kind_label(content_kind: str | None) -> str:
    return "Serial" if _content_kind_key(content_kind) == "serial" else "Kino"


def _content_kind_icon(content_kind: str | None) -> str:
    return "📺" if _content_kind_key(content_kind) == "serial" else "🎬"


def _request_text(request_id: int, user_id: int, text: str) -> str:
    return (
        f"📩 So'rov #{request_id}\n"
        f"User ID: {user_id}\n"
        f"Matn: {text or '-'}"
    )


def _request_added_text(code: str, content_kind: str = "movie") -> str:
    content_label = _content_kind_label(content_kind)
    return f"✅ Siz so'ragan {content_label.lower()} qo'shildi.\nKod: {code}"


def _request_rejected_text(
    request_text: str | None = None,
    *,
    request_id: int | None = None,
) -> str:
    lines: list[str] = []
    if request_id is not None:
        lines.append(f"📩 So'rov #{request_id}")
    if request_text:
        lines.append(f"Matn: {escape(request_text)}")
    if lines:
        lines.append("")
    lines.append("Hozircha ushbu so'rov bo'yicha kontent topilmadi.")
    lines.append("Istasangiz boshqa nom bilan qayta yuborishingiz mumkin.")
    return "\n".join(lines)


def _helper_admin_label(helper_admin: dict[str, object]) -> str:
    username = helper_admin.get("username")
    if username:
        return f"@{username}"
    return str(helper_admin.get("full_name") or f"Admin {helper_admin['user_id']}")


def _content_list_filter_label(filter_key: str) -> str:
    if filter_key == "movie":
        return "Kinolar"
    if filter_key == "serial":
        return "Seriallar"
    return "Hammasi"


def _helper_admin_permission_label(permission: str) -> str:
    return {
        ADMIN_PERMISSION_MOVIES: "Kontent",
        ADMIN_PERMISSION_REQUESTS: "So'rovlar",
        ADMIN_PERMISSION_STATS: "Statistika",
        ADMIN_PERMISSION_ADS: "Reklama",
        "channels": "Kanallar",
    }.get(permission, permission)


def _render_helper_admins_panel(helper_admins: list[dict[str, object]]) -> str:
    lines = ["👥 Yordamchi adminlar", f"Jami: {len(helper_admins)}"]
    if not helper_admins:
        lines.extend(["", "Hozircha yordamchi admin qo'shilmagan."])
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
        lines.append(f"   Ruxsatlar: {summary}")

    return "\n".join(lines)


def _render_helper_admin_detail(helper_admin: dict[str, object]) -> str:
    permissions = helper_admin["permissions"]
    enabled = [
        _helper_admin_permission_label(permission)
        for permission, is_enabled in permissions.items()
        if is_enabled
    ]
    permission_text = ", ".join(enabled) if enabled else "Ruxsat berilmagan"
    username = helper_admin.get("username")

    return "\n".join(
        [
            "👤 Admin ma'lumoti",
            f"ID: {helper_admin['user_id']}",
            f"Ism: {helper_admin.get('full_name') or '-'}",
            f"Username: @{username}" if username else "Username: -",
            f"Ruxsatlar: {permission_text}",
        ]
    )


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
        "🎞 Kontent ro'yxati",
        f"Bo'lim: {_content_list_filter_label(filter_key)}",
        f"Jami: {total_items} ta",
    ]

    if total_items:
        lines.extend([f"Sahifa: {page + 1}/{total_pages}", ""])
        for absolute_index, (code, title, _content_kind) in enumerate(
            page_items, start=start + 1
        ):
            lines.append(f"{absolute_index}. {title}")
            lines.append(f"   Kod: {code}")
    else:
        lines.extend(["", "Hozircha kontent yo'q."])

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
            f"Jami: {total_count}",
            f"Kinolar: {movie_count}",
            f"Seriallar: {serial_count}",
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
        f"Bo'lim: {_content_list_filter_label(filter_key)}",
        f"Jami: {total_items} ta",
    ]

    if total_items:
        lines.extend([f"Sahifa: {page + 1}/{total_pages}", "", "Kod yuboring.", ""])
        for index, (code, title, _content_kind) in enumerate(
            page_items, start=start_index + 1
        ):
            lines.append(f"{index}. {title}")
            lines.append(f"   Kod: {code}")
    else:
        lines.extend(["", "O'chirish uchun kontent topilmadi."])

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
        return f"{seconds // 86400} kun"
    if seconds % 3600 == 0:
        return f"{seconds // 3600} soat"
    return f"{max(1, seconds // 60)} daqiqa"


def _format_time_left(expires_at: str | None) -> str:
    expires = _parse_timestamp(expires_at)
    if expires is None:
        return "Noma'lum"

    remaining = int((expires - datetime.now(UTC)).total_seconds())
    if remaining <= 0:
        return "Yakunlanmoqda"

    days, rest = divmod(remaining, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, _seconds = divmod(rest, 60)
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


def _ad_duration_limits_text() -> str:
    return (
        f"{_format_duration(MIN_AD_DURATION_SECONDS)} - "
        f"{_format_duration(MAX_AD_DURATION_SECONDS)}"
    )


def _build_ads_panel_text(active_ads: list[dict], recent_ads: list[dict]) -> str:
    active_count = len(active_ads)
    recent_count = len(recent_ads)
    lines = [
        "📢 Reklama paneli",
        "",
        "━━━━━━━━━━━━",
        f"🟢 Faol kampaniyalar — {active_count} ta",
    ]

    if active_ads:
        for ad in active_ads[:6]:
            lines.extend(
                [
                    f"#{ad['id']} • {_ad_preview(ad['content_type'], ad['text'])}",
                    f"Status: {_ad_status_label(ad['status'])}",
                    f"Yetkazildi: {ad['delivered_total']}/{ad['recipient_total']} • Xato: {ad['failed_total']}",
                    f"Qolgan vaqt: {_format_time_left(ad['expires_at'])}",
                    "",
                ]
            )
        if active_count > 6:
            lines.append(f"Yana {active_count - 6} ta faol kampaniya mavjud")
    else:
        lines.append("Hozirda faol reklama mavjud emas")

    lines.extend(
        [
            "",
            "━━━━━━━━━━━━",
            f"🔴 Yakunlangan kampaniyalar — {recent_count} ta",
        ]
    )
    if recent_ads:
        for ad in recent_ads[:5]:
            lines.extend(
                [
                    f"#{ad['id']} • {_ad_status_label(ad['status'])}",
                    f"O'chirildi: {ad['deleted_total']}/{ad['delivered_total']}",
                    "",
                ]
            )
        if recent_count > 5:
            lines.append(f"Yana {recent_count - 5} ta yakunlangan kampaniya mavjud")
    else:
        lines.append("Hali yakunlangan reklama yo'q")

    lines.extend(
        [
            "",
            "━━━━━━━━━━━━",
            "⏱ Davomiylik: 45m • 2h • 1d",
            f"🗓 Oraliq: {_ad_duration_limits_text().replace(' - ', ' — ')}",
        ]
    )
    return "\n".join(lines).rstrip()


def _build_stats_insights(summary: dict[str, int]) -> str:
    notes = []
    if summary["pending_requests"] >= 10:
        notes.append("So'rovlar navbati ko'paygan.")
    if summary["entered_today"] <= max(10, summary["total_users"] // 20):
        notes.append("Bugungi faollik past.")
    if summary["blocked_users"] >= max(5, summary["all_time_users"] // 5):
        notes.append("Bloklagan foydalanuvchilar ulushi sezilarli.")
    if summary["total_views"] <= 100:
        notes.append("Ko'rishlar past.")
    if summary["rejected_requests"] >= summary["total_requests"] * 0.3:
        notes.append("Rad etilgan so'rovlar ulushi baland.")
    if not notes:
        notes.append("Ko'rsatkichlar barqaror.")
    return "\n".join(f"• {note}" for note in notes)


def _build_dashboard_caption(panel: str, payload: dict) -> str:
    panel = panel if panel in STATS_PANELS else "overview"
    summary = payload["summary"]
    trends = payload["trends"]
    updated_at = f"{local_now_text()} {APP_TIMEZONE_LABEL}"

    if panel == "overview":
        return (
            "📊 Statistika\n"
            f"Yangilandi: {updated_at}\n\n"
            f"Faol obunachilar: {summary['total_users']} | Jami kirgan: {summary['all_time_users']} | Bugun kirgan: {summary['entered_today']}\n"
            f"Bugun yangi obunachi: {summary['new_subscribers_today']} | Bloklaganlar: {summary['blocked_users']}\n"
            f"Faollik: 24 soat {summary['active_today']} | 7 kun {summary['active_week']}\n"
            f"Kontent: {summary['total_movies']} | Ko'rishlar: {summary['total_views']} | Sevimlilar: {summary['total_favorites']}\n"
            f"So'rovlar: {summary['total_requests']} | Ochiq: {summary['pending_requests']} | Tayyor: {summary['completed_requests']} | Rad: {summary['rejected_requests']}\n\n"
            "7 kunlik trend\n"
            f"• So'rovlar: {_sparkline(trends['requests'])}\n"
            f"• Ko'rishlar: {_sparkline(trends['movie_views'])}\n"
            f"• Yangi userlar: {_sparkline(trends['new_users'])}\n\n"
            "Oxirgi faol foydalanuvchilar\n"
            f"{_format_recent_users(payload['recent_users'])}"
        )

    if panel == "traffic":
        labels = " ".join(label[8:] for label in trends["labels"])
        return (
            "📈 Trafik\n"
            f"Yangilandi: {updated_at}\n\n"
            f"Kunlar: {labels}\n\n"
            f"So'rovlar  {_sparkline(trends['requests'])}   {' '.join(_compact_number(v) for v in trends['requests'])}\n"
            f"Ko'rishlar {_sparkline(trends['movie_views'])}   {' '.join(_compact_number(v) for v in trends['movie_views'])}\n"
            f"Yangi user {_sparkline(trends['new_users'])}   {' '.join(_compact_number(v) for v in trends['new_users'])}\n\n"
            f"7 kunlik ko'rishlar: {sum(trends['movie_views'])}\n"
            f"7 kunlik so'rovlar: {sum(trends['requests'])}\n"
            f"7 kunlik yangi userlar: {sum(trends['new_users'])}"
        )

    if panel == "movies":
        top_movies = payload["top_movies"]
        if not top_movies:
            ranking = "Hali ma'lumot yo'q."
        else:
            max_views = max(views for _, _, views, _ in top_movies)
            ranking = "\n".join(
                f"{index}. {title} ({code})\n{_bar_chart('Korish', views, max_views)} • User: {unique_views}"
                for index, (code, title, views, unique_views) in enumerate(
                    top_movies,
                    start=1,
                )
            )

        return (
            "🎬 Kontent statistikasi\n"
            f"Yangilandi: {updated_at}\n\n"
            f"Jami kontent: {summary['total_movies']}\n"
            f"Jami ko'rishlar: {summary['total_views']}\n"
            f"Jami sevimlilar: {summary['total_favorites']}\n\n"
            "Eng ko'p ko'rilganlar\n"
            f"{ranking}"
        )

    request_counts = payload["request_counts"]
    maximum = max(request_counts.values()) if request_counts else 0
    return (
        "📩 So'rovlar statistikasi\n"
        f"Yangilandi: {updated_at}\n\n"
        f"{_bar_chart('Yangi', request_counts.get('pending', 0), maximum)}\n"
        f"{_bar_chart('Jarayonda', request_counts.get('accepted', 0), maximum)}\n"
        f"{_bar_chart('Tayyor', request_counts.get('completed', 0), maximum)}\n"
        f"{_bar_chart('Rad', request_counts.get('rejected', 0), maximum)}\n\n"
        f"Ochiq: {summary['pending_requests']}\n"
        f"Tayyor: {summary['completed_requests']}\n"
        f"Rad: {summary['rejected_requests']}"
    )


async def _show_stats_webapp(message: types.Message) -> None:
    summary = await get_dashboard_summary()
    text = (
        "📊 Statistika\n"
        f"Faol obunachilar: {summary['total_users']}\n"
        f"Jami kirganlar: {summary['all_time_users']}\n"
        f"Bugun kirganlar: {summary['entered_today']}\n"
        f"Bugun yangi obunachilar: {summary['new_subscribers_today']}\n"
        f"Bloklaganlar: {summary['blocked_users']}\n"
        f"24 soat faol: {summary['active_today']}\n"
        f"7 kun faol: {summary['active_week']}\n"
        f"Kontent: {summary['total_movies']}\n"
        f"Ko'rishlar: {summary['total_views']}\n"
        f"So'rovlar: {summary['total_requests']}\n"
        f"Ochiq: {summary['pending_requests']}\n"
        f"Tayyor: {summary['completed_requests']}\n"
        f"Rad: {summary['rejected_requests']}\n\n"
        "Tavsiyalar\n"
        f"{_build_stats_insights(summary)}"
    )

    if STATS_WEBAPP_URL:
        user_id = message.from_user.id if message.from_user is not None else 0
        signed_url = build_signed_stats_webapp_url(STATS_WEBAPP_URL, user_id)
        keyboard = stats_webapp_keyboard(signed_url)
        await message.answer(text, reply_markup=keyboard)
        return

    await message.answer(text + "\n\nSTATS_WEBAPP_URL sozlanmagan.")


async def _show_serial_mode_picker(
    message: types.Message,
    state: FSMContext,
    *,
    edit: bool = False,
) -> None:
    await state.clear()
    has_existing_serials = bool(await get_serial_titles())
    text = "📺 Serial qo'shish\nDavom turini tanlang."
    keyboard = serial_mode_keyboard(has_existing_serials=has_existing_serials)

    if edit:
        await message.edit_text(text, reply_markup=keyboard)
        return

    await message.answer(text, reply_markup=keyboard)


async def _show_serial_titles_picker(
    message: types.Message,
    state: FSMContext,
    *,
    page: int = 0,
    titles: list[str] | None = None,
    edit: bool = False,
) -> None:
    titles = titles or await get_serial_titles()
    if not titles:
        await _show_serial_mode_picker(message, state, edit=edit)
        return

    await state.clear()
    await state.set_state(AddMovieState.waiting_for_serial_pick)
    await state.update_data(serial_titles=titles, serial_page=page)

    text = "📺 Serial tanlang\nDavomini qo'shish uchun ro'yxatdan tanlang yoki nomini yozing."
    keyboard = serial_titles_keyboard(
        titles,
        page=page,
        page_size=SERIAL_TITLES_PAGE_SIZE,
    )

    if edit:
        await message.edit_text(text, reply_markup=keyboard)
        return

    await message.answer(text, reply_markup=keyboard)


def _next_season_title(series_title: str) -> str | None:
    title = (series_title or "").strip()
    if not title:
        return None

    match = None
    try:
        import re

        match = re.search(r"^(?P<base>.+?)\s+(?P<season>\d+)\s*-\s*fasl$", title)
    except re.error:
        match = None

    if match is None:
        return None

    base_title = match.group("base").strip()
    season_number = int(match.group("season"))
    if not base_title or season_number <= 0:
        return None
    return f"{base_title} {season_number + 1}-fasl"


async def _start_serial_continuation_flow(
    message: types.Message,
    state: FSMContext,
    title: str,
    *,
    episode_number: int | None = None,
    edit: bool = False,
) -> None:
    if episode_number is None:
        next_season_title = _next_season_title(title)
        if next_season_title:
            await state.clear()
            await state.set_state(AddMovieState.waiting_for_serial_pick)
            await state.update_data(
                serial_pick_title=title,
                serial_pick_next_season=next_season_title,
            )

            text = (
                f"Serial tanlandi: {title}\n\n"
                "Qaysi rejimni tanlaysiz?\n"
                "• Shu faslni davom ettirish\n"
                f"• {next_season_title} ni 1-qismdan boshlash"
            )

            if edit:
                await message.edit_text(
                    text,
                    reply_markup=serial_season_choice_keyboard(next_season_title),
                )
                return

            await message.answer(
                text,
                reply_markup=serial_season_choice_keyboard(next_season_title),
            )
            return

    next_episode_number = episode_number or await get_next_serial_episode_number(title)
    await state.clear()
    await state.set_state(AddMovieState.waiting_for_code)
    await state.update_data(
        content_kind="serial",
        series_title=title,
        episode_number=next_episode_number,
    )

    text = f"📺 {title}\nQism: {next_episode_number}\nKod yuboring."

    if edit:
        await message.edit_text(text, reply_markup=None)
        return

    await message.answer(text)


async def _show_serial_continue_prompt(
    message: types.Message,
    state: FSMContext,
    title: str,
    next_episode_number: int,
) -> None:
    await state.clear()
    await state.set_state(AddMovieState.waiting_for_serial_continue)
    await state.update_data(
        content_kind="serial",
        series_title=title,
        episode_number=next_episode_number,
    )
    await message.answer(
        f"✅ Qism qo'shildi.\nSerial: {title}\nKeyingi qism: {next_episode_number}\nYana qo'shasizmi?",
        reply_markup=serial_continue_keyboard(),
    )


async def _start_add_movie_flow(
    message: types.Message,
    state: FSMContext,
    *,
    content_kind: str = "movie",
    request_id: int | None = None,
    request_user_id: int | None = None,
    request_text: str | None = None,
    request_chat_id: int | None = None,
    request_message_id: int | None = None,
) -> None:
    await state.clear()
    normalized_kind = _content_kind_key(content_kind)
    content_label = _content_kind_label(normalized_kind)
    payload: dict[str, int | str] = {"content_kind": normalized_kind}
    prompt = f"{content_label} kodi yuboring."

    if request_id is not None and request_user_id is not None:
        payload.update(
            {
                "request_id": request_id,
                "request_user_id": request_user_id,
            }
        )

        if request_text:
            payload["request_text"] = request_text

        if request_chat_id is not None:
            payload["request_chat_id"] = request_chat_id

        if request_message_id is not None:
            payload["request_message_id"] = request_message_id

        prompt = (
            f"📩 So'rov #{request_id}\n"
            f"Matn: {request_text or '-'}\n\n"
            f"Endi {content_label.lower()} kodi yuboring."
        )

    await state.set_state(AddMovieState.waiting_for_code)
    if payload:
        await state.update_data(**payload)
    await message.answer(prompt)


def _ad_duration_prompt() -> str:
    return (
        "⏳ Muddatni yuboring.\n"
        "Masalan: 45m, 2h, 1d.\n"
        f"Oraliq: {_ad_duration_limits_text()}."
    )


async def _show_content_list(
    message: types.Message,
    *,
    filter_key: str | None = None,
    page: int = 0,
    edit: bool = False,
) -> None:
    movies = await get_all_movies()
    if not movies:
        text = "🎞 Hozircha kontent qo'shilmagan."
        reply_markup = None
    else:
        movie_items = [movie for movie in movies if movie[2] == "movie"]
        serial_items = [movie for movie in movies if movie[2] == "serial"]

        normalized_filter = filter_key if filter_key in {"movie", "serial"} else None
        if normalized_filter == "movie":
            text, total_pages, current_page = _render_content_section_page(
                movie_items,
                filter_key="movie",
                page=page,
            )
        elif normalized_filter == "serial":
            text, total_pages, current_page = _render_content_section_page(
                serial_items,
                filter_key="serial",
                page=page,
            )
        else:
            text = _render_content_picker_text(
                title="🎞 Kontent",
                movie_count=len(movie_items),
                serial_count=len(serial_items),
                empty_text="🎞 Hozircha kontent qo'shilmagan.",
                hint_text="Kerakli bo'limni tanlang.",
            )
            total_pages = 1
            current_page = 0

        reply_markup = content_list_keyboard(
            active_filter=normalized_filter,
            movie_count=len(movie_items),
            serial_count=len(serial_items),
            page=current_page,
            total_pages=total_pages,
        )

    if edit:
        await message.edit_text(text, reply_markup=reply_markup)
        return

    await message.answer(text, reply_markup=reply_markup)


async def _show_delete_panel(
    message: types.Message,
    *,
    state: FSMContext | None = None,
    filter_key: str | None = None,
    page: int = 0,
    edit: bool = False,
) -> None:
    movies = await get_all_movies()
    if not movies:
        text = "O'chirish uchun kontent topilmadi."
        reply_markup = None
        if state is not None:
            await state.clear()
    else:
        movie_items = [movie for movie in movies if movie[2] == "movie"]
        serial_items = [movie for movie in movies if movie[2] == "serial"]
        active_filter = filter_key if filter_key in {"movie", "serial"} else None

        if active_filter is None:
            text = _render_content_picker_text(
                title="🗑 O'chirish",
                movie_count=len(movie_items),
                serial_count=len(serial_items),
                empty_text="O'chirish uchun kontent topilmadi.",
                hint_text="Kerakli bo'limni tanlang.",
            )
            reply_markup = delete_movie_keyboard(
                active_filter=None,
                movie_count=len(movie_items),
                serial_count=len(serial_items),
                page=0,
                total_pages=1,
            )
            if state is not None:
                await state.clear()
        else:
            filtered_items = movie_items if active_filter == "movie" else serial_items
            total_items = len(filtered_items)
            total_pages = max(
                1, (total_items + DELETE_PANEL_PAGE_SIZE - 1) // DELETE_PANEL_PAGE_SIZE
            )
            current_page = min(max(page, 0), total_pages - 1)
            start = current_page * DELETE_PANEL_PAGE_SIZE
            page_items = filtered_items[start : start + DELETE_PANEL_PAGE_SIZE]

            text = _render_delete_panel_text(
                filter_key=active_filter,
                total_items=total_items,
                page=current_page,
                total_pages=total_pages,
                page_items=page_items,
                start_index=start,
            )
            reply_markup = delete_movie_keyboard(
                active_filter=active_filter,
                movie_count=len(movie_items),
                serial_count=len(serial_items),
                page_items=page_items,
                page=current_page,
                total_pages=total_pages,
            )

            if state is not None:
                await state.set_state(DeleteMovieState.waiting_for_code)
                await state.update_data(
                    delete_filter=active_filter, delete_page=current_page
                )

    if edit:
        await message.edit_text(text, reply_markup=reply_markup)
        return

    await message.answer(text, reply_markup=reply_markup)


async def show_requests(message: types.Message) -> None:
    requests = await get_pending_requests()
    if not requests:
        await message.answer("📭 Hozircha ochiq so'rovlar yo'q.")
        return

    for request_id, user_id, text, file_id in requests:
        await send_request_review(
            message,
            request_id=request_id,
            user_id=user_id,
            text=text,
            file_id=file_id,
        )


async def send_request_review(
    message: types.Message,
    *,
    request_id: int,
    user_id: int,
    text: str,
    file_id: str | None,
) -> None:
    review_text = _request_text(request_id, user_id, text)
    keyboard = request_review_keyboard(request_id)

    if not file_id:
        await message.answer(review_text, reply_markup=keyboard)
        return

    try:
        await message.answer_photo(
            photo=file_id, caption=review_text, reply_markup=keyboard
        )
    except TelegramBadRequest:
        await message.answer_video(
            video=file_id, caption=review_text, reply_markup=keyboard
        )


__all__ = [
    "_ad_duration_prompt",
    "_build_ads_panel_text",
    "_build_dashboard_caption",
    "_build_stats_insights",
    "_content_kind_icon",
    "_content_list_filter_label",
    "_format_recent_users",
    "_helper_admin_permission_label",
    "_render_content_picker_text",
    "_render_content_section_page",
    "_render_delete_panel_text",
    "_render_helper_admin_detail",
    "_render_helper_admins_panel",
    "_request_added_text",
    "_request_rejected_text",
    "_request_text",
    "_show_content_list",
    "_show_delete_panel",
    "_show_serial_continue_prompt",
    "_show_serial_mode_picker",
    "_show_serial_titles_picker",
    "_show_stats_webapp",
    "_start_add_movie_flow",
    "_start_serial_continuation_flow",
    "send_request_review",
    "show_requests",
]
