"""Panel text builders for ads and dashboard."""

from aiogram import Bot, types

from database import (
    APP_TIMEZONE_LABEL,
    get_ads_by_status,
    get_dashboard_summary,
    get_daily_metric_series,
    get_recent_ads,
    get_recent_users,
    get_request_status_counts,
    get_top_viewed_movies,
    get_all_movies,
    list_helper_admins,
    get_serial_titles,
)
from keyboards import (
    ads_panel_keyboard,
    content_list_keyboard,
    delete_movie_keyboard,
    helper_admin_detail_keyboard,
    helper_admins_keyboard,
    serial_continue_keyboard,
    serial_mode_keyboard,
    serial_titles_keyboard,
    stats_dashboard_keyboard,
    stats_webapp_keyboard,
)
from dashboard import render_dashboard
from config import STATS_WEBAPP_URL
from services.stats_webapp_auth import build_signed_stats_webapp_url
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from .constants import (
    ACTIVE_AD_STATUSES,
    STATS_PANELS,
    SERIAL_TITLES_PAGE_SIZE,
    DELETE_PANEL_PAGE_SIZE,
    CONTENT_LIST_PAGE_SIZE,
)
from .render import (
    _ad_status_label,
    _ad_preview,
    _ads_button_rows,
    _format_time_left,
    _format_duration,
    _compact_number,
    _sparkline,
    _bar_chart,
    _format_recent_users,
    _render_helper_admins_panel,
    _render_helper_admin_detail,
    _render_content_section_page,
    _render_content_picker_text,
    _render_delete_panel_text,
    _helper_admin_label,
)
from .content_utils import _safe_html, _content_list_filter_label
from .states import AddMovieState, DeleteMovieState


async def _build_ads_panel_view() -> tuple[str, types.InlineKeyboardMarkup]:
    active_ads = await get_ads_by_status(ACTIVE_AD_STATUSES, limit=6)
    recent_ads = await get_recent_ads(limit=4)
    text = _build_ads_panel_text(active_ads, recent_ads)
    keyboard = ads_panel_keyboard(_ads_button_rows(active_ads))
    return text, keyboard


def _build_ads_panel_text(active_ads: list[dict], recent_ads: list[dict]) -> str:
    lines = [
        "📣 Reklama Markazi",
        "",
        "Admin shu bo'limdan bitta xabarni barcha foydalanuvchilarga yuboradi.",
        "Reklama muddati tugaganda bot uni avtomatik o'chirishga harakat qiladi.",
        "",
        f"Faol kampaniyalar: {len(active_ads)}",
    ]

    if active_ads:
        for ad in active_ads:
            lines.extend(
                [
                    f"• #{ad['id']} {_ad_status_label(ad['status'])}",
                    f"  {_ad_preview(ad['content_type'], ad['text'])}",
                    f"  Yuborildi: {ad['delivered_total']}/{ad['recipient_total']} | Xato: {ad['failed_total']}",
                    f"  Qolgan vaqt: {_format_time_left(ad['expires_at'])}",
                ]
            )
    else:
        lines.append("• Hozircha faol reklama yo'q")

    lines.extend(["", "Oxirgi yakunlanganlar:"])
    if recent_ads:
        for ad in recent_ads:
            lines.append(
                f"• #{ad['id']} {_ad_status_label(ad['status'])} | "
                f"{ad['deleted_total']}/{ad['delivered_total']} o'chirilgan"
            )
    else:
        lines.append("• Hali yakunlangan reklama yo'q")

    lines.extend(
        [
            "",
            "Qo'llab-quvvatlanadi: matn, rasm, video, fayl.",
            "Reklama muddati qo'lda yoziladi: 45m, 2h, 1d, 1d 6h.",
            f"Ruxsat etilgan oraliq: {_ad_duration_limits_text()}.",
        ]
    )
    return "\n".join(lines)


def _ad_duration_limits_text() -> str:
    from database import MIN_AD_DURATION_SECONDS, MAX_AD_DURATION_SECONDS

    minimum_minutes = MIN_AD_DURATION_SECONDS // 60
    maximum_days = MAX_AD_DURATION_SECONDS // 86400
    return f"{minimum_minutes} daqiqadan {maximum_days} kungacha"


async def _refresh_saved_ads_panel(
    bot: Bot,
    panel_chat_id: int | str | None,
    panel_message_id: int | str | None,
) -> None:
    if panel_chat_id is None or panel_message_id is None:
        return

    try:
        text, keyboard = await _build_ads_panel_view()
        await bot.edit_message_text(
            text,
            chat_id=int(panel_chat_id),
            message_id=int(panel_message_id),
            reply_markup=keyboard,
        )
    except (TelegramBadRequest, TelegramForbiddenError, ValueError):
        return


async def _load_dashboard_payload(panel: str) -> dict:
    panel = panel if panel in STATS_PANELS else "overview"
    return {
        "summary": await get_dashboard_summary(),
        "trends": await get_daily_metric_series(days=7),
        "request_counts": await get_request_status_counts(),
        "top_movies": await get_top_viewed_movies(limit=5),
        "recent_users": await get_recent_users(limit=6),
    }


def _build_dashboard_caption(panel: str, payload: dict) -> str:
    from database import local_now_text

    panel = panel if panel in STATS_PANELS else "overview"
    summary = payload["summary"]
    trends = payload["trends"]
    updated_at = f"{local_now_text()} {APP_TIMEZONE_LABEL}"
    known_request_total = (
        summary["pending_requests"]
        + summary["completed_requests"]
        + summary["rejected_requests"]
    )
    other_requests = max(0, summary["total_requests"] - known_request_total)
    other_requests_line = (
        f"• Boshqa status: {other_requests}\n" if other_requests else ""
    )

    if panel == "overview":
        return (
            "📊 Statistika Dashboard\n"
            f"Yangilandi: {updated_at}\n\n"
            "👥 Foydalanuvchilar\n"
            f"• Faol obunachilar: {summary['total_users']} (bloklamaganlar)\n"
            f"• Jami kirganlar: {summary['all_time_users']} (admin/helperlarsiz)\n"
            f"• Bugun kirganlar: {summary['entered_today']} (bugungi oxirgi faollik)\n"
            f"• Bugun yangi obunachilar: {summary['new_subscribers_today']}\n"
            f"• Bloklaganlar: {summary['blocked_users']}\n"
            f"• 24 soat faol: {summary['active_today']} (bloklamaganlar)\n"
            f"• 7 kun faol: {summary['active_week']}\n\n"
            "🎬 Kontent\n"
            f"• Kinolar: {summary['total_movies']}\n"
            f"• Ko'rishlar: {summary['total_views']}\n"
            f"• Sevimlilar: {summary['total_favorites']}\n\n"
            "📥 So'rovlar\n"
            f"• Jami: {summary['total_requests']}\n"
            f"• Ochiq: {summary['pending_requests']}\n"
            f"• Bajarilgan: {summary['completed_requests']}\n"
            f"• Rad etilgan: {summary['rejected_requests']}\n"
            f"{other_requests_line}\n"
            "📈 7 kunlik trend\n"
            f"• So'rovlar: {_sparkline(trends['requests'])}\n"
            f"• Ko'rishlar: {_sparkline(trends['movie_views'])}\n"
            f"• Yangi users: {_sparkline(trends['new_users'])}\n\n"
            "🕘 So'nggi faollar\n"
            f"{_format_recent_users(payload['recent_users'])}"
        )

    if panel == "traffic":
        labels = " ".join(label[8:] for label in trends["labels"])
        return (
            "📈 Trafik Dashboard\n"
            f"Yangilandi: {updated_at}\n\n"
            f"Kunlar: {labels}\n\n"
            f"So'rovlar   {_sparkline(trends['requests'])}   {' '.join(_compact_number(v) for v in trends['requests'])}\n"
            f"Ko'rishlar  {_sparkline(trends['movie_views'])}   {' '.join(_compact_number(v) for v in trends['movie_views'])}\n"
            f"Yangi user  {_sparkline(trends['new_users'])}   {' '.join(_compact_number(v) for v in trends['new_users'])}\n\n"
            "Qisqa xulosa\n"
            f"• 7 kunda ko'rishlar: {sum(trends['movie_views'])}\n"
            f"• 7 kunda so'rovlar: {sum(trends['requests'])}\n"
            f"• 7 kunda yangi users: {sum(trends['new_users'])}"
        )

    if panel == "movies":
        top_movies = payload["top_movies"]
        if not top_movies:
            ranking = "Ko'rishlar hali yo'q"
        else:
            max_views = max(views for _, _, views, _ in top_movies)
            views_label = "Ko'rish"
            ranking = "\n".join(
                f"{index}. {title} ({code})\n"
                f"{_bar_chart(views_label, views, max_views)} • Unique user: {unique_views}"
                for index, (code, title, views, unique_views) in enumerate(
                    top_movies,
                    start=1,
                )
            )

        return (
            "🎬 Top Kinolar Dashboard\n"
            f"Yangilandi: {updated_at}\n\n"
            f"• Jami kinolar: {summary['total_movies']}\n"
            f"• Jami ko'rishlar: {summary['total_views']}\n"
            f"• Jami sevimlilar: {summary['total_favorites']}\n\n"
            "🏆 Eng ko'p ko'rilgan kinolar\n"
            f"{ranking}"
        )

    request_counts = payload["request_counts"]
    maximum = max(request_counts.values()) if request_counts else 0
    other_status_line = (
        f"{_bar_chart('Boshqa', request_counts.get('other', 0), maximum)}\n"
        if request_counts.get("other", 0)
        else ""
    )
    other_status_summary = (
        f"\n• Boshqa status jami: {request_counts.get('other', 0)}"
        if request_counts.get("other", 0)
        else ""
    )
    return (
        "📥 So'rovlar Dashboard\n"
        f"Yangilandi: {updated_at}\n\n"
        f"{_bar_chart('Yangi', request_counts.get('pending', 0), maximum)}\n"
        f"{_bar_chart('Jarayonda', request_counts.get('accepted', 0), maximum)}\n"
        f"{_bar_chart('Bajarildi', request_counts.get('completed', 0), maximum)}\n"
        f"{_bar_chart('Rad etildi', request_counts.get('rejected', 0), maximum)}\n"
        f"{other_status_line}\n"
        "Qisqa xulosa\n"
        f"• Ochiq navbat: {summary['pending_requests']}\n"
        f"• Bajarilgan jami: {summary['completed_requests']}\n"
        f"• Rad etilgan jami: {summary['rejected_requests']}"
        f"{other_status_summary}"
    )


async def _show_stats_dashboard(
    message: types.Message,
    *,
    panel: str,
    edit: bool = False,
) -> None:
    panel = panel if panel in STATS_PANELS else "overview"
    payload = await _load_dashboard_payload(panel)
    text = _build_dashboard_caption(panel, payload)
    image_bytes = render_dashboard(panel, payload)
    keyboard = stats_dashboard_keyboard(panel)
    dashboard_file = types.BufferedInputFile(
        image_bytes, filename=f"dashboard_{panel}.png"
    )

    if edit:
        if message.photo or message.document:
            media = types.InputMediaDocument(media=dashboard_file, caption=text)
            try:
                await message.edit_media(media=media, reply_markup=keyboard)
                return
            except TelegramBadRequest as error:
                message_text = str(error).lower()
                if "message is not modified" in message_text:
                    return
                await message.answer_document(
                    document=dashboard_file, caption=text, reply_markup=keyboard
                )
                return

        try:
            await message.edit_text(text, reply_markup=keyboard)
        except TelegramBadRequest as error:
            if "message is not modified" not in str(error).lower():
                raise
        return

    await message.answer_document(
        document=dashboard_file, caption=text, reply_markup=keyboard
    )


def _build_stats_insights(summary: dict[str, int]) -> str:
    recommendations = []
    if summary["pending_requests"] >= 10:
        recommendations.append(
            "🔔 Ko'p kutayotgan so'rovlar bor — so'rovlar bo'limini tekshiring."
        )
    if summary["entered_today"] <= max(10, summary["total_users"] // 20):
        recommendations.append(
            "📣 Bugungi faol foydalanuvchilar soni past — foydalanuvchi rag'batlantirish kampaniyasini ko'rib chiqing."
        )
    if summary["total_views"] <= 100:
        recommendations.append(
            "🎬 Ko'rishlar kam — kontentni ommalashtirish va yangi filmlar qo'shish foydali bo'ladi."
        )
    if summary["rejected_requests"] >= summary["total_requests"] * 0.3:
        recommendations.append(
            "⚠️ So'rovlarning katta qismi rad etilmoqda — qoidalarni va moderatsiyani qayta ko'rib chiqing."
        )
    if not recommendations:
        recommendations.append(
            "✅ Hozircha statistika barqaror. Keyingi bosqich uchun reklama va kontent yangilanishini kuzatib boring."
        )

    return "\n".join(recommendations)


async def _show_stats_webapp(message: types.Message) -> None:
    summary = await get_dashboard_summary()
    text = (
        "🌟 Premium Statistika\n"
        f"• Faol obunachilar: {summary['total_users']} (bloklamaganlar)\n"
        f"• Jami kirganlar: {summary['all_time_users']} (admin/helperlarsiz)\n"
        f"• Bugun kirganlar: {summary['entered_today']} (bugungi oxirgi faollik)\n"
        f"• 24 soat faol: {summary['active_today']} (bloklamaganlar)\n"
        f"• 7 kun davomida faol: {summary['active_week']}\n"
        f"• Jami kinolar: {summary['total_movies']}\n"
        f"• Jami ko'rishlar: {summary['total_views']}\n"
        f"• Jami so'rovlar: {summary['total_requests']}\n"
        f"• Kutayotgan/aktual so'rovlar: {summary['pending_requests']}\n"
        f"• Bajarilgan so'rovlar: {summary['completed_requests']}\n"
        f"• Rad etilgan so'rovlar: {summary['rejected_requests']}\n\n"
        "📌 Quyidagi tavsiyalar boshqaruv uchun foydali bo'lishi mumkin:\n"
        f"{_build_stats_insights(summary)}"
    )

    if STATS_WEBAPP_URL:
        user_id = message.from_user.id if message.from_user is not None else 0
        keyboard = stats_webapp_keyboard(
            build_signed_stats_webapp_url(STATS_WEBAPP_URL, user_id)
        )
        await message.answer(text, reply_markup=keyboard)
        return

    await message.answer(
        text
        + "\n\nPremium mini app URL sozlanmagan. Iltimos .env ichiga STATS_WEBAPP_URL ni qo'shing.",
    )


async def _show_ads_panel(
    message: types.Message,
    *,
    edit: bool = False,
) -> None:
    text, keyboard = await _build_ads_panel_view()

    if edit:
        await message.edit_text(text, reply_markup=keyboard)
        return

    await message.answer(text, reply_markup=keyboard)


async def _show_helper_admins_panel(
    message: types.Message,
    *,
    edit: bool = False,
) -> None:
    helper_admins = await list_helper_admins()
    text = _render_helper_admins_panel(helper_admins)
    keyboard = helper_admins_keyboard(helper_admins)

    if edit:
        await message.edit_text(text, reply_markup=keyboard)
        return

    await message.answer(text, reply_markup=keyboard)


async def _show_helper_admin_detail(
    message: types.Message,
    helper_admin: dict[str, object],
    *,
    edit: bool = False,
) -> None:
    text = _render_helper_admin_detail(helper_admin)
    keyboard = helper_admin_detail_keyboard(
        int(helper_admin["user_id"]),
        helper_admin["permissions"],
    )

    if edit:
        await message.edit_text(text, reply_markup=keyboard)
        return

    await message.answer(text, reply_markup=keyboard)


async def _show_content_list(
    message: types.Message,
    *,
    filter_key: str | None = None,
    page: int = 0,
    edit: bool = False,
) -> None:
    movies = await get_all_movies()
    if not movies:
        text = "📭 Kino va seriallar ro'yxati bo'sh"
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
                title="📚 Kontent ro'yxati",
                movie_count=len(movie_items),
                serial_count=len(serial_items),
                empty_text="📭 Kino va seriallar ro'yxati bo'sh",
                hint_text="Ko'rish uchun pastdan bo'limni tanlang.",
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
        text = "📭 O'chirish uchun kino yoki serial topilmadi"
        reply_markup = None
        if state is not None:
            await state.clear()
    else:
        movie_items = [movie for movie in movies if movie[2] == "movie"]
        serial_items = [movie for movie in movies if movie[2] == "serial"]
        active_filter = filter_key if filter_key in {"movie", "serial"} else None

        if active_filter is None:
            text = _render_content_picker_text(
                title="🗑 Kontentni o'chirish",
                movie_count=len(movie_items),
                serial_count=len(serial_items),
                empty_text="📭 O'chirish uchun kino yoki serial topilmadi",
                hint_text="O'chirish uchun pastdan bo'limni tanlang.",
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
            end = start + DELETE_PANEL_PAGE_SIZE
            page_items = filtered_items[start:end]

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


async def _show_serial_mode_picker(
    message: types.Message,
    state: FSMContext,
    *,
    edit: bool = False,
) -> None:
    await state.clear()
    has_existing_serials = bool(await get_serial_titles())
    text = "📺 Serial qo'shish turini tanlang."
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
    subtitle: str | None = None,
    edit: bool = False,
) -> None:
    titles = titles or await get_serial_titles()
    if not titles:
        await _show_serial_mode_picker(message, state, edit=edit)
        return

    await state.clear()
    await state.set_state(AddMovieState.waiting_for_serial_pick)
    await state.update_data(serial_titles=titles, serial_page=page)

    text = "📺 Davomini qo'shmoqchi bo'lgan serialni tanlang yoki nomini yozing."
    if subtitle:
        text = subtitle
    keyboard = serial_titles_keyboard(
        titles,
        page=page,
        page_size=SERIAL_TITLES_PAGE_SIZE,
    )

    if edit:
        await message.edit_text(text, reply_markup=keyboard)
        return

    await message.answer(text, reply_markup=keyboard)


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
        f"📺 {title} uchun qism qo'shildi.\nKeyingi qism: {next_episode_number}-qism\nYana shu serialga qism qo'shasizmi?",
        reply_markup=serial_continue_keyboard(),
    )
