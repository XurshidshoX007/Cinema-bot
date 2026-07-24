"""Final admin text overrides live in this file; edit here first for admin copy changes."""

import logging
from html import escape
from pathlib import Path

logger = logging.getLogger(__name__)

from aiogram import types
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext

from config import get_stats_webapp_url, is_telegram_compatible_stats_webapp_url
from services.stats_webapp_auth import build_signed_stats_webapp_url

from . import admin


def _request_text(request_id: int, user_id: int, text: str) -> str:
    return f"📩 So'rov #{request_id}\n" f"User ID: {user_id}\n" f"Matn: {text or '-'}"


def _request_added_text(code: str, content_kind: str = "movie") -> str:
    content_label = admin._content_kind_label(content_kind)
    return f"✅ Siz so'ragan {content_label.lower()} qo'shildi.\nKod: {code}"


def _request_rejected_text(
    request_text: str | None = None,
    *,
    request_id: int | None = None,
) -> str:
    lines: list[str] = []

    if request_text:
        lines.extend([f"📝 <b>So'rov:</b> {_safe_html(request_text)}", ""])
    elif request_id is not None:
        lines.extend([f"📝 <b>So'rov #{request_id}</b>", ""])

    lines.extend(
        [
            "Hozircha ushbu so'rov bo'yicha kontent topilmadi.",
            "Qidiruvni davom ettiryapmiz.",
            "",
            "Topilishi bilan botga joylaymiz va sizga albatta xabar beramiz.",
        ]
    )
    return "\n".join(lines)


def _runtime_stats_webapp_url() -> str:
    return get_stats_webapp_url()


def _safe_html(value: object) -> str:
    return escape(str(value))


def _serial_picker_text(
    *,
    subtitle: str,
    count: int,
    page: int,
    total_pages: int,
) -> str:
    lines = [
        "📚 <b>Serial tanlash</b>",
        "",
        f"<i>{subtitle}</i>",
    ]
    if total_pages > 1:
        lines.extend(["", f"<i>{count} ta • {page}/{total_pages}</i>"])
    return "\n".join(lines)


def _serial_code_card(title: str, episode_number: int) -> str:
    return (
        f"🎞 <b>{_safe_html(title)}</b>\n"
        f"<i>{episode_number}-qism qo'shilmoqda</i>\n\n"
        "Tavsif yuboring."
    )


def _content_list_filter_label(filter_key: str) -> str:
    if filter_key == "movie":
        return "Kinolar"
    if filter_key == "serial":
        return "Seriallar"
    return "Hammasi"


def _helper_admin_permission_label(permission: str) -> str:
    return {
        admin.ADMIN_PERMISSION_MOVIES: "Kontent",
        admin.ADMIN_PERMISSION_REQUESTS: "So'rovlar",
        admin.ADMIN_PERMISSION_STATS: "Statistika",
        admin.ADMIN_PERMISSION_ADS: "Reklama",
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
            f"{index}. {admin._helper_admin_label(helper_admin)} ({helper_admin['user_id']})"
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
        1,
        (total_items + admin.CONTENT_LIST_PAGE_SIZE - 1)
        // admin.CONTENT_LIST_PAGE_SIZE,
    )
    page = min(max(page, 0), total_pages - 1)
    start = page * admin.CONTENT_LIST_PAGE_SIZE
    end = min(total_items, start + admin.CONTENT_LIST_PAGE_SIZE)
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
    section_label = _content_list_filter_label(filter_key)
    lines = [
        "🗑 <b>Kontentni o'chirish</b>",
        "<i>Kerakli kontentni tanlang yoki kod yuboring.</i>",
        "",
        f"<b>{section_label}</b>",
        f"<i>{total_items} ta • {page + 1}/{total_pages}</i>",
    ]

    if total_items:
        lines.extend(["", "<i>Pastdagi tugma yoki kod orqali o'chiring.</i>", ""])
        for index, (code, title, _content_kind) in enumerate(
            page_items, start=start_index + 1
        ):
            content_label = "Serial" if _content_kind == "serial" else "Kino"
            lines.append(f"{index}. {title}")
            lines.append(f"<code>{code}</code> • {content_label}")
            lines.append("")
    else:
        lines.extend(["", "📭 O'chirish uchun kontent topilmadi."])

    return "\n".join(lines).rstrip()


def _build_ads_panel_text(active_ads: list[dict], recent_ads: list[dict]) -> str:
    lines = ["📣 Reklama paneli", f"Faol kampaniyalar: {len(active_ads)}"]

    if active_ads:
        for ad_item in active_ads:
            lines.extend(
                [
                    f"• #{ad_item['id']} - {admin._ad_status_label(ad_item['status'])}",
                    f"  Kontent: {admin._ad_preview(ad_item['content_type'], ad_item['text'])}",
                    f"  Yetkazildi: {ad_item['delivered_total']}/{ad_item['recipient_total']} | Xato: {ad_item['failed_total']}",
                    f"  Qolgan vaqt: {admin._format_time_left(ad_item['expires_at'])}",
                ]
            )
    else:
        lines.append("• Hozircha faol reklama yo'q")

    lines.extend(["", "Yakunlangan kampaniyalar:"])
    if recent_ads:
        for ad_item in recent_ads:
            lines.append(
                f"• #{ad_item['id']} - {admin._ad_status_label(ad_item['status'])} | "
                f"O'chirildi: {ad_item['deleted_total']}/{ad_item['delivered_total']}"
            )
    else:
        lines.append("• Hozircha yakunlangan reklama yo'q")

    lines.extend(
        ["", "Masalan: 45m, 2h, 1d.", f"Oraliq: {admin._ad_duration_limits_text()}."]
    )
    return "\n".join(lines)


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
    panel = panel if panel in admin.STATS_PANELS else "overview"
    summary = payload["summary"]
    trends = payload["trends"]
    updated_at = f"{admin.local_now_text()} {admin.APP_TIMEZONE_LABEL}"
    known_request_total = (
        summary["pending_requests"]
        + summary["completed_requests"]
        + summary["rejected_requests"]
    )
    other_requests = max(0, summary["total_requests"] - known_request_total)
    other_requests_text = f" | Boshqa: {other_requests}" if other_requests else ""

    if panel == "overview":
        return (
            "Statistika\n"
            f"Yangilandi: {updated_at}\n\n"
            f"Faol obunachilar: {summary['total_users']} (bloklamaganlar) | Jami kirgan: {summary['all_time_users']} (admin/helperlarsiz)\n"
            f"Bugun kirgan: {summary['entered_today']} (bugungi oxirgi faollik)\n"
            f"Bugun yangi obunachi: {summary['new_subscribers_today']} | Bloklaganlar: {summary['blocked_users']}\n"
            f"Faollik: 24 soat {summary['active_today']} (bloklamaganlar) | 7 kun {summary['active_week']}\n"
            f"Kontent: {summary['total_movies']} | Ko'rishlar: {summary['total_views']} | Sevimlilar: {summary['total_favorites']}\n"
            f"So'rovlar: {summary['total_requests']} | Ochiq: {summary['pending_requests']} | Tayyor: {summary['completed_requests']} | Rad: {summary['rejected_requests']}{other_requests_text}\n\n"
            "7 kunlik trend\n"
            f"• So'rovlar: {admin._sparkline(trends['requests'])}\n"
            f"• Ko'rishlar: {admin._sparkline(trends['movie_views'])}\n"
            f"• Yangi userlar: {admin._sparkline(trends['new_users'])}\n\n"
            "Oxirgi faol foydalanuvchilar\n"
            f"{admin._format_recent_users(payload['recent_users'])}"
        )

    if panel == "traffic":
        labels = " ".join(label[8:] for label in trends["labels"])
        return (
            "Trafik\n"
            f"Yangilandi: {updated_at}\n\n"
            f"Kunlar: {labels}\n\n"
            f"So'rovlar  {admin._sparkline(trends['requests'])}   {' '.join(admin._compact_number(v) for v in trends['requests'])}\n"
            f"Ko'rishlar {admin._sparkline(trends['movie_views'])}   {' '.join(admin._compact_number(v) for v in trends['movie_views'])}\n"
            f"Yangi user {admin._sparkline(trends['new_users'])}   {' '.join(admin._compact_number(v) for v in trends['new_users'])}\n\n"
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
            views_label = "Ko'rish"
            ranking = "\n".join(
                f"{index}. {title} ({code})\n"
                f"{admin._bar_chart(views_label, views, max_views)} • Unique user: {unique_views}"
                for index, (code, title, views, unique_views) in enumerate(top_movies, start=1)
            )

        return (
            "Kontent statistikasi\n"
            f"Yangilandi: {updated_at}\n\n"
            f"Jami kontent: {summary['total_movies']}\n"
            f"Jami ko'rishlar: {summary['total_views']}\n"
            f"Jami sevimlilar: {summary['total_favorites']}\n\n"
            "Eng ko'p ko'rilganlar\n"
            f"{ranking}"
        )

    request_counts = payload["request_counts"]
    maximum = max(request_counts.values()) if request_counts else 0
    other_status_line = (
        f"{admin._bar_chart('Boshqa', request_counts.get('other', 0), maximum)}\n"
        if request_counts.get("other", 0)
        else ""
    )
    other_status_summary = (
        f"\nBoshqa status jami: {request_counts.get('other', 0)}"
        if request_counts.get("other", 0)
        else ""
    )
    return (
        "So'rovlar statistikasi\n"
        f"Yangilandi: {updated_at}\n\n"
        f"{admin._bar_chart('Yangi', request_counts.get('pending', 0), maximum)}\n"
        f"{admin._bar_chart('Jarayonda', request_counts.get('accepted', 0), maximum)}\n"
        f"{admin._bar_chart('Tayyor', request_counts.get('completed', 0), maximum)}\n"
        f"{admin._bar_chart('Rad', request_counts.get('rejected', 0), maximum)}\n"
        f"{other_status_line}\n"
        f"Ochiq: {summary['pending_requests']}\n"
        f"Tayyor: {summary['completed_requests']}\n"
        f"Rad: {summary['rejected_requests']}"
        f"{other_status_summary}"
    )


async def _show_stats_webapp(message: types.Message) -> None:
    # Avval bot ichidagi dashboard chiqadi, shunda webapp havolasi ishlamasa ham bo'lim ishlaydi.
    try:
        await admin._show_stats_dashboard(message, panel="overview", edit=False)
    except Exception:
        summary = await admin.get_dashboard_summary()
        await message.answer(
            "\n".join(
                [
                    "📊 Statistika",
                    f"Faol obunachilar: {summary['total_users']}",
                    f"Jami kirganlar: {summary['all_time_users']}",
                    f"Bugun kirganlar: {summary['entered_today']}",
                    f"Bugun yangi obunachilar: {summary['new_subscribers_today']}",
                    f"Bloklaganlar: {summary['blocked_users']}",
                    f"24 soat faol: {summary['active_today']}",
                    f"7 kun faol: {summary['active_week']}",
                    f"Kontent: {summary['total_movies']}",
                    f"So'rovlar: {summary['total_requests']}",
                ]
            )
        )

    stats_url = _runtime_stats_webapp_url()
    if stats_url:
        user_id = message.from_user.id if message.from_user is not None else 0
        await message.answer(
            "🌐 Batafsil web-panel uchun tugmani bosing:",
            reply_markup=admin.stats_webapp_keyboard(
                build_signed_stats_webapp_url(stats_url, user_id)
            ),
        )


async def _show_serial_mode_picker(
    message: types.Message,
    state: FSMContext,
    *,
    edit: bool = False,
) -> None:
    await state.clear()
    has_existing_serials = bool(await admin.get_serial_titles())
    text = "📺 <b>Serial qo'shish</b>\n\n<i>Kerakli rejimni tanlang</i>"
    keyboard = admin.serial_mode_keyboard(has_existing_serials=has_existing_serials)

    if edit:
        await message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        return

    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


async def _show_serial_titles_picker(
    message: types.Message,
    state: FSMContext,
    *,
    page: int = 0,
    titles: list[str] | None = None,
    subtitle: str | None = None,
    edit: bool = False,
) -> None:
    titles = titles or await admin.get_serial_titles()
    if not titles:
        await _show_serial_mode_picker(message, state, edit=edit)
        return

    total_items = len(titles)
    total_pages = max(1, (total_items + admin.SERIAL_TITLES_PAGE_SIZE - 1) // admin.SERIAL_TITLES_PAGE_SIZE)
    current_page = min(max(page, 0), total_pages - 1)
    current_subtitle = (
        subtitle or "Davomini qo'shish uchun serialni tanlang yoki nomini yuboring"
    )

    await state.clear()
    await state.set_state(admin.AddMovieState.waiting_for_serial_pick)
    await state.update_data(
        serial_titles=titles,
        serial_page=current_page,
        serial_pick_subtitle=current_subtitle,
    )

    text = _serial_picker_text(
        subtitle=current_subtitle,
        count=total_items,
        page=current_page + 1,
        total_pages=total_pages,
    )
    keyboard = admin.serial_titles_keyboard(
        titles,
        page=current_page,
        page_size=admin.SERIAL_TITLES_PAGE_SIZE,
    )

    if edit:
        await message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        return

    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


async def _start_serial_continuation_flow(
    message: types.Message,
    state: FSMContext,
    title: str,
    *,
    episode_number: int | None = None,
    edit: bool = False,
) -> None:
    if episode_number is None:
        next_season_title = admin._next_season_title(title)
        if next_season_title:
            await state.clear()
            await state.set_state(admin.AddMovieState.waiting_for_serial_pick)
            await state.update_data(
                serial_pick_title=title,
                serial_pick_next_season=next_season_title,
            )

            text = (
                f"📚 <b>Serial tanlandi:</b> {_safe_html(title)}\n\n"
                "<i>Qaysi rejimni tanlaysiz?</i>\n"
                "• Shu faslni davom ettirish\n"
                f"• {_safe_html(next_season_title)} ni 1-qismdan boshlash"
            )

            if edit:
                await message.edit_text(
                    text,
                    reply_markup=admin.serial_season_choice_keyboard(next_season_title),
                    parse_mode="HTML",
                )
                return

            await message.answer(
                text,
                reply_markup=admin.serial_season_choice_keyboard(next_season_title),
                parse_mode="HTML",
            )
            return

    next_episode_number = episode_number or await admin.get_next_serial_episode_number(
        title
    )
    await state.clear()
    await state.set_state(admin.AddMovieState.waiting_for_video)
    await state.update_data(
        content_kind="serial",
        series_title=title,
        episode_number=next_episode_number,
        code=None,
        description="",
        serial_quick_upload=True,
    )

    text = admin._serial_video_prompt_text(title, next_episode_number)

    if edit:
        await message.edit_text(
            text,
            reply_markup=admin.serial_upload_finish_keyboard(),
            parse_mode="HTML",
        )
        return

    await message.answer(
        text,
        reply_markup=admin.serial_upload_finish_keyboard(),
        parse_mode="HTML",
    )


async def _show_serial_continue_prompt(
    message: types.Message,
    state: FSMContext,
    title: str,
    next_episode_number: int,
) -> None:
    await state.clear()
    await state.set_state(admin.AddMovieState.waiting_for_serial_continue)
    await state.update_data(
        content_kind="serial",
        series_title=title,
        episode_number=next_episode_number,
    )
    await message.answer(
        (
            "✅ <b>Qism qo'shildi</b>\n\n"
            f"🎞 {_safe_html(title)}\n"
            f"📌 Keyingi qism: {next_episode_number}\n\n"
            "<i>Davom ettirasizmi?</i>"
        ),
        reply_markup=admin.serial_continue_keyboard(),
        parse_mode="HTML",
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
    normalized_kind = admin._content_kind_key(content_kind)
    content_label = admin._content_kind_label(normalized_kind)
    payload: dict[str, int | str] = {"content_kind": normalized_kind}
    prompt = f"{content_label} kodi yuboring."
    parse_mode: str | None = None

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

        if normalized_kind == "serial":
            prompt = (
                f"📩 <b>So'rov #{request_id}</b>\n\n"
                f"<i>{_safe_html(request_text or '-')}</i>\n\n"
                "🆕 <b>Yangi serial</b>\n"
                "\n<i>Avval serial uchun asosiy kod yuboring</i>"
            )
            parse_mode = "HTML"
        else:
            prompt = (
                f"📩 So'rov #{request_id}\n"
                f"Matn: {request_text or '-'}\n\n"
                f"Endi {content_label.lower()} kodi yuboring."
            )
    elif normalized_kind == "serial":
        prompt = "🆕 <b>Yangi serial</b>\n\n<i>Avval serial uchun asosiy kod yuboring</i>"
        parse_mode = "HTML"

    await state.set_state(admin.AddMovieState.waiting_for_code)
    if payload:
        await state.update_data(**payload)
    await message.answer(prompt, parse_mode=parse_mode)


def _ad_duration_prompt() -> str:
    return (
        "⏳ Muddatni yuboring.\n"
        "Masalan: 45m, 2h, 1d.\n"
        f"Oraliq: {admin._ad_duration_limits_text()}."
    )


async def _show_content_list(
    message: types.Message,
    *,
    filter_key: str | None = None,
    page: int = 0,
    edit: bool = False,
) -> None:
    movies = await admin.get_all_movies()
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

        reply_markup = admin.content_list_keyboard(
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
    movies = await admin.get_all_movies()
    if not movies:
        text = "📭 O'chirish uchun kontent topilmadi."
        reply_markup = None
        if state is not None:
            await state.clear()
    else:
        movie_items = [movie for movie in movies if movie[2] == "movie"]
        serial_items = [movie for movie in movies if movie[2] == "serial"]
        active_filter = filter_key if filter_key in {"movie", "serial"} else None

        if active_filter is None:
            text = _render_content_picker_text(
                title="🗑 <b>Kontentni o'chirish</b>",
                movie_count=len(movie_items),
                serial_count=len(serial_items),
                empty_text="📭 O'chirish uchun kontent topilmadi.",
                hint_text="<i>Kerakli bo'limni tanlang.</i>",
            )
            reply_markup = admin.delete_movie_keyboard(
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
                1,
                (total_items + admin.DELETE_PANEL_PAGE_SIZE - 1)
                // admin.DELETE_PANEL_PAGE_SIZE,
            )
            current_page = min(max(page, 0), total_pages - 1)
            start = current_page * admin.DELETE_PANEL_PAGE_SIZE
            page_items = filtered_items[start : start + admin.DELETE_PANEL_PAGE_SIZE]

            text = _render_delete_panel_text(
                filter_key=active_filter,
                total_items=total_items,
                page=current_page,
                total_pages=total_pages,
                page_items=page_items,
                start_index=start,
            )
            reply_markup = admin.delete_movie_keyboard(
                active_filter=active_filter,
                movie_count=len(movie_items),
                serial_count=len(serial_items),
                page_items=page_items,
                page=current_page,
                total_pages=total_pages,
            )

            if state is not None:
                await state.set_state(admin.DeleteMovieState.waiting_for_code)
                await state.update_data(
                    delete_filter=active_filter, delete_page=current_page
                )

    if edit:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
        return

    await message.answer(text, reply_markup=reply_markup, parse_mode="HTML")


async def _show_stats_webapp_restored(message: types.Message) -> None:
    summary = await admin.get_dashboard_summary()
    stats_url = _runtime_stats_webapp_url()
    text = (
        "Premium statistika\n"
        f"• Faol obunachilar: {summary['total_users']} (bloklamaganlar)\n"
        f"• Jami kirganlar: {summary['all_time_users']} (admin/helperlarsiz)\n"
        f"• Bugun kirganlar: {summary['entered_today']} (bugungi oxirgi faollik)\n"
        f"• Bugun yangi obunachilar: {summary['new_subscribers_today']}\n"
        f"• Bloklaganlar: {summary['blocked_users']}\n"
        f"• 24 soat faol: {summary['active_today']} (bloklamaganlar)\n"
        f"• 7 kun faol: {summary['active_week']}\n"
        f"• Jami kontent: {summary['total_movies']}\n"
        f"• Ko'rishlar: {summary['total_views']}\n"
        f"• Jami so'rovlar: {summary['total_requests']}\n"
        f"• Ochiq so'rovlar: {summary['pending_requests']}\n"
        f"• Bajarilgan: {summary['completed_requests']}\n"
        f"• Rad etilgan: {summary['rejected_requests']}"
    )

    if stats_url and is_telegram_compatible_stats_webapp_url(stats_url):
        user_id = message.from_user.id if message.from_user is not None else 0
        signed_url = build_signed_stats_webapp_url(stats_url, user_id)
        await message.answer(
            text,
            reply_markup=admin.stats_webapp_keyboard(signed_url),
        )
        return

    if stats_url:
        await message.answer(
            text
            + "\n\nMini app vaqtincha o'chirilgan: joriy public URL Telegram ichida ochilmaydi. "
            "Tunnel yangilangandan keyin tugma yana ishlaydi."
        )
        return

    await message.answer(
        text
        + "\n\nMini app URL tayyor emas. Public HTTPS tunnel ishga tushgach tugma avtomatik ishlaydi."
    )


async def show_requests(message: types.Message) -> None:
    requests = await admin.get_pending_requests()
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
    keyboard = admin.request_review_keyboard(request_id)

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


def _render_content_section_page(
    items: list[tuple[str, str, str]],
    *,
    filter_key: str,
    page: int,
) -> tuple[str, int, int]:
    total_items = len(items)
    total_pages = max(
        1,
        (total_items + admin.CONTENT_LIST_PAGE_SIZE - 1)
        // admin.CONTENT_LIST_PAGE_SIZE,
    )
    page = min(max(page, 0), total_pages - 1)
    start = page * admin.CONTENT_LIST_PAGE_SIZE
    end = min(total_items, start + admin.CONTENT_LIST_PAGE_SIZE)
    page_items = items[start:end]

    lines = [
        "📚 <b>Kontent ro'yxati</b>",
        f"<i>{_content_list_filter_label(filter_key)} bo'limi</i>",
    ]

    if total_items:
        lines.extend(["", f"<i>{total_items} ta • {page + 1}/{total_pages}</i>", ""])
        for absolute_index, (code, title, content_kind) in enumerate(
            page_items, start=start + 1
        ):
            content_label = "Serial" if content_kind == "serial" else "Kino"
            lines.append(f"{absolute_index}. <b>{_safe_html(title)}</b>")
            lines.append(f"<code>{code}</code> • {content_label}")
            lines.append("")
    else:
        lines.extend(["", "📭 <i>Hozircha kontent yo'q.</i>"])

    return "\n".join(lines).rstrip(), total_pages, page


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
            "<i>Ko'rish uchun bo'limni tanlang</i>",
            "",
            f"🎬 Kinolar: <b>{movie_count}</b>",
            f"📺 Seriallar: <b>{serial_count}</b>",
            "",
            f"<i>Jami: {total_count} ta</i>",
            "",
            hint_text,
        ]
    )


async def _show_content_list(
    message: types.Message,
    *,
    filter_key: str | None = None,
    page: int = 0,
    edit: bool = False,
) -> None:
    movies = await admin.get_all_movies()
    if not movies:
        text = "📭 <b>Kontent ro'yxati bo'sh</b>\n\n<i>Hozircha kontent qo'shilmagan.</i>"
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
                title="📚 <b>Kontent ro'yxati</b>",
                movie_count=len(movie_items),
                serial_count=len(serial_items),
                empty_text="📭 <b>Kontent ro'yxati bo'sh</b>\n\n<i>Hozircha kontent qo'shilmagan.</i>",
                hint_text="<i>Pastdagi bo'limdan tanlang.</i>",
            )
            total_pages = 1
            current_page = 0

        reply_markup = admin.content_list_keyboard(
            active_filter=normalized_filter,
            movie_count=len(movie_items),
            serial_count=len(serial_items),
            page=current_page,
            total_pages=total_pages,
        )

    if edit:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
        return

    await message.answer(text, reply_markup=reply_markup, parse_mode="HTML")


async def _show_serial_continue_prompt(
    message: types.Message,
    state: FSMContext,
    title: str,
    next_episode_number: int,
) -> None:
    await state.clear()
    await state.set_state(admin.AddMovieState.waiting_for_serial_continue)
    await state.update_data(
        content_kind="serial",
        series_title=title,
        episode_number=next_episode_number,
    )
    await message.answer(
        (
            "✅ <b>Qism qo'shildi</b>\n\n"
            f"🎞 <b>{_safe_html(title)}</b>\n"
            f"📌 Keyingi qism: {next_episode_number}\n\n"
            "<i>Davom ettirasizmi?</i>"
        ),
        reply_markup=admin.serial_continue_keyboard(),
        parse_mode="HTML",
    )


admin._request_text = _request_text
admin._request_added_text = _request_added_text
admin._request_rejected_text = _request_rejected_text
admin._content_list_filter_label = _content_list_filter_label
admin._helper_admin_permission_label = _helper_admin_permission_label
admin._render_helper_admins_panel = _render_helper_admins_panel
admin._render_helper_admin_detail = _render_helper_admin_detail
admin._render_content_section_page = _render_content_section_page
admin._render_content_picker_text = _render_content_picker_text
admin._render_delete_panel_text = _render_delete_panel_text
admin._build_ads_panel_text = _build_ads_panel_text
admin._build_stats_insights = _build_stats_insights
admin._build_dashboard_caption = _build_dashboard_caption
admin._show_stats_webapp = _show_stats_webapp_restored
admin._show_serial_mode_picker = _show_serial_mode_picker
admin._show_serial_titles_picker = _show_serial_titles_picker
admin._start_serial_continuation_flow = _start_serial_continuation_flow
admin._show_serial_continue_prompt = _show_serial_continue_prompt
admin._start_add_movie_flow = _start_add_movie_flow
admin._ad_duration_prompt = _ad_duration_prompt
admin._show_content_list = _show_content_list
admin._show_delete_panel = _show_delete_panel
admin.show_requests = show_requests
admin.send_request_review = send_request_review
