from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

import re

from config import ADMIN_ID

SEARCH_BUTTON = "🔎 Qidiruv"
LEGACY_SEARCH_BUTTON = "🔎 Qidirish"
REQUEST_BUTTON = "📝 So'rov yuborish"
FAVORITES_BUTTON = "❤️ Sevimlilar"
HISTORY_BUTTON = "🕘 Tarix"
HELP_BUTTON = "❓ Yordam"
ADMIN_PANEL_BUTTON = "🛠 Admin panel"

MOVIE_MANAGEMENT_BUTTON = "🎬 Kontent"
REQUESTS_BUTTON = "📥 So'rovlar"
STATS_BUTTON = "📊 Statistika"
ADS_BUTTON = "📣 Reklama"
CHANNELS_BUTTON = "📡 Kanallar"
HELPER_ADMINS_BUTTON = "👥 Adminlar"
BACK_TO_MAIN_BUTTON = "⬅️ Asosiy menu"

NEW_MOVIE_BUTTON = "🎬 Kino qo'shish"
NEW_SERIAL_BUTTON = "📺 Serial qo'shish"
LEGACY_NEW_MOVIE_BUTTON = "➕ Qo'shish"
EDIT_MOVIE_BUTTON = "✏️ Tahrirlash"
DELETE_MOVIE_BUTTON = "🗑 O'chirish"
MOVIES_LIST_BUTTON = "📚 Ro'yxat"
BACK_TO_ADMIN_BUTTON = "⬅️ Admin"

USER_ACTIONS = (
    SEARCH_BUTTON,
    REQUEST_BUTTON,
    FAVORITES_BUTTON,
    HISTORY_BUTTON,
    HELP_BUTTON,
)

ADMIN_ACTIONS = (
    ADMIN_PANEL_BUTTON,
    MOVIE_MANAGEMENT_BUTTON,
    NEW_MOVIE_BUTTON,
    NEW_SERIAL_BUTTON,
    LEGACY_NEW_MOVIE_BUTTON,
    EDIT_MOVIE_BUTTON,
    DELETE_MOVIE_BUTTON,
    MOVIES_LIST_BUTTON,
    REQUESTS_BUTTON,
    STATS_BUTTON,
    ADS_BUTTON,
    CHANNELS_BUTTON,
    HELPER_ADMINS_BUTTON,
    BACK_TO_ADMIN_BUTTON,
    BACK_TO_MAIN_BUTTON,
)


def _reply_keyboard(
    rows: list[list[str]],
    *,
    placeholder: str | None = None,
) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=text) for text in row] for row in rows],
        resize_keyboard=True,
        input_field_placeholder=placeholder,
    )


def _truncate_inline_text(text: str, limit: int = 56) -> str:
    value = text.strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _normalize_serial_inline_part(text: str) -> str:
    compact = re.sub(r"\s+", " ", text.strip())
    return re.sub(r"\s*-\s*", "-", compact)


def _delete_button_text(code: str, title: str, content_kind: str) -> str:
    if content_kind == "serial":
        parts: list[str] = []
        season_match = re.search(r"\d+\s*-\s*fasl", title, flags=re.IGNORECASE)
        episode_match = re.search(r"\d+\s*-\s*qism", title, flags=re.IGNORECASE)
        if season_match:
            parts.append(_normalize_serial_inline_part(season_match.group(0)))
        if episode_match:
            episode_part = _normalize_serial_inline_part(episode_match.group(0))
            if episode_part not in parts:
                parts.append(episode_part)
        if parts:
            return f"🗑 {code} - {' '.join(parts)}"

    short_title = title if len(title) <= 26 else f"{title[:23]}..."
    return f"🗑 {code} - {short_title}"


def main_menu(
    user_id: int, *, show_admin_panel: bool | None = None
) -> ReplyKeyboardMarkup:
    rows = [
        [SEARCH_BUTTON, REQUEST_BUTTON],
        [FAVORITES_BUTTON, HISTORY_BUTTON],
        [HELP_BUTTON],
    ]

    if show_admin_panel is None:
        show_admin_panel = user_id == ADMIN_ID

    if show_admin_panel:
        rows.append([ADMIN_PANEL_BUTTON])

    return _reply_keyboard(rows, placeholder="Bo'limni tanlang")


def admin_menu(
    *,
    permissions: set[str] | None = None,
    is_owner: bool = False,
) -> ReplyKeyboardMarkup:
    allowed = permissions or set()
    rows: list[list[str]] = []

    first_row = [
        button
        for button, permission in (
            (MOVIE_MANAGEMENT_BUTTON, "movies"),
            (REQUESTS_BUTTON, "requests"),
        )
        if permission in allowed or is_owner
    ]
    if first_row:
        rows.append(first_row)

    second_row = [
        button
        for button, permission in (
            (STATS_BUTTON, "stats"),
            (ADS_BUTTON, "ads"),
            (CHANNELS_BUTTON, "channels"),
        )
        if permission in allowed or is_owner
    ]
    if second_row:
        rows.append(second_row)

    if is_owner:
        rows.append([HELPER_ADMINS_BUTTON])

    rows.append([BACK_TO_MAIN_BUTTON])
    return _reply_keyboard(rows, placeholder="Admin bo'limini tanlang")


def movie_menu() -> ReplyKeyboardMarkup:
    return _reply_keyboard(
        [
            [NEW_MOVIE_BUTTON, NEW_SERIAL_BUTTON],
            [EDIT_MOVIE_BUTTON, DELETE_MOVIE_BUTTON],
            [MOVIES_LIST_BUTTON],
            [BACK_TO_ADMIN_BUTTON],
        ],
        placeholder="Amalni tanlang",
    )


def _content_filter_rows(
    *,
    prefix: str,
    active_filter: str | None,
    movie_count: int,
    serial_count: int,
) -> list[list[InlineKeyboardButton]]:
    def _tab_text(filter_key: str, text: str) -> str:
        return f"• {text}" if active_filter == filter_key else text

    buttons: list[InlineKeyboardButton] = []
    if movie_count:
        buttons.append(
            InlineKeyboardButton(
                text=_tab_text("movie", f"🎬 Kinolar {movie_count}"),
                callback_data=f"{prefix}:movie:0",
            )
        )
    if serial_count:
        buttons.append(
            InlineKeyboardButton(
                text=_tab_text("serial", f"📺 Seriallar {serial_count}"),
                callback_data=f"{prefix}:serial:0",
            )
        )

    return [buttons] if buttons else []


def delete_movie_keyboard(
    *,
    active_filter: str | None,
    movie_count: int,
    serial_count: int,
    page_items: list[tuple[str, str, str]] | None = None,
    page: int = 0,
    total_pages: int = 1,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    rows.extend(
        _content_filter_rows(
            prefix="delete_panel",
            active_filter=active_filter,
            movie_count=movie_count,
            serial_count=serial_count,
        )
    )

    if active_filter in {"movie", "serial"} and page_items:
        for code, title, content_kind in page_items:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=_delete_button_text(code, title, content_kind),
                        callback_data=f"del:{code}:{active_filter}:{page}",
                    )
                ]
            )

    nav_buttons: list[InlineKeyboardButton] = []
    if active_filter in {"movie", "serial"} and total_pages > 1:
        if page > 0:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="⬅️ Oldingi",
                    callback_data=f"delete_panel:{active_filter}:{page - 1}",
                )
            )
        if page + 1 < total_pages:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="Keyingi ➡️",
                    callback_data=f"delete_panel:{active_filter}:{page + 1}",
                )
            )
    if nav_buttons:
        rows.append(nav_buttons)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def delete_confirm_keyboard(
    code: str,
    *,
    filter_key: str,
    page: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Ha, o'chirish",
                    callback_data=f"delete_confirm:{code}:{filter_key}:{page}",
                ),
                InlineKeyboardButton(
                    text="↩️ Bekor qilish",
                    callback_data=f"delete_panel:{filter_key}:{page}",
                ),
            ]
        ]
    )


def edit_movie_action_keyboard(*, content_kind: str) -> InlineKeyboardMarkup:
    title_button = "📝 Serial nomi" if content_kind == "serial" else "📝 Nomi"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=title_button,
                    callback_data="edit_movie:title",
                ),
                InlineKeyboardButton(
                    text="📄 Tavsif",
                    callback_data="edit_movie:description",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🎞 Media",
                    callback_data="edit_movie:media",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Yakunlash",
                    callback_data="edit_movie:done",
                )
            ],
        ]
    )


def content_list_keyboard(
    *,
    active_filter: str | None,
    movie_count: int,
    serial_count: int,
    page: int = 0,
    total_pages: int = 1,
) -> InlineKeyboardMarkup:
    rows = _content_filter_rows(
        prefix="content_list",
        active_filter=active_filter,
        movie_count=movie_count,
        serial_count=serial_count,
    )

    nav_buttons: list[InlineKeyboardButton] = []
    if active_filter in {"movie", "serial"} and total_pages > 1:
        if page > 0:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="⬅️ Oldingi",
                    callback_data=f"content_list:{active_filter}:{page - 1}",
                )
            )
        if page + 1 < total_pages:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="Keyingi ➡️",
                    callback_data=f"content_list:{active_filter}:{page + 1}",
                )
            )
    if nav_buttons:
        rows.append(nav_buttons)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def serial_mode_keyboard(*, has_existing_serials: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="🆕 Yangi serial", callback_data="serial_mode_new")]
    ]

    if has_existing_serials:
        rows.append(
            [
                InlineKeyboardButton(
                    text="➕ Davomini qo'shish", callback_data="serial_mode_continue"
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="❌ Bekor qilish", callback_data="serial_mode_cancel"
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def serial_titles_keyboard(
    titles: list[str],
    *,
    page: int = 0,
    page_size: int = 8,
) -> InlineKeyboardMarkup:
    total_items = len(titles)
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    page = min(max(page, 0), total_pages - 1)
    start = page * page_size
    end = start + page_size

    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=_truncate_inline_text(title),
                callback_data=f"serial_pick_{start + index}",
            )
        ]
        for index, title in enumerate(titles[start:end])
    ]

    nav_buttons: list[InlineKeyboardButton] = []
    if page > 0:
        nav_buttons.append(
            InlineKeyboardButton(
                text="⬅️ Oldingi", callback_data=f"serial_page_{page - 1}"
            )
        )
    if page + 1 < total_pages:
        nav_buttons.append(
            InlineKeyboardButton(
                text="Keyingi ➡️", callback_data=f"serial_page_{page + 1}"
            )
        )
    if nav_buttons:
        rows.append(nav_buttons)

    rows.append(
        [
            InlineKeyboardButton(
                text="🆕 Yangi serial", callback_data="serial_mode_new"
            ),
            InlineKeyboardButton(
                text="❌ Bekor qilish", callback_data="serial_mode_cancel"
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def serial_season_choice_keyboard(next_season_title: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Shu faslni davom ettirish",
                    callback_data="serial_season_continue",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"🆕 {_truncate_inline_text(next_season_title, limit=24)}",
                    callback_data="serial_season_new",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Bekor qilish",
                    callback_data="serial_mode_cancel",
                )
            ],
        ]
    )


def serial_continue_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Yana qism qo'shish", callback_data="serial_more_yes"
                ),
                InlineKeyboardButton(
                    text="✅ Yakunlash", callback_data="serial_more_no"
                ),
            ]
        ]
    )


def serial_upload_finish_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Yakunlash", callback_data="serial_upload_finish"
                )
            ]
        ]
    )


def request_review_keyboard(request_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Qabul qilish", callback_data=f"accept_{request_id}"
                ),
                InlineKeyboardButton(
                    text="🚫 Rad etish", callback_data=f"reject_{request_id}"
                ),
            ]
        ]
    )


def request_existing_match_keyboard(request_id: int, code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Mavjudini yuborish",
                    callback_data=f"req_use:{request_id}:{code}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="➕ Yangi qo'shish",
                    callback_data=f"req_new:{request_id}",
                )
            ],
        ]
    )


def paged_codes_keyboard(
    prefix: str, page: int, total_pages: int
) -> InlineKeyboardMarkup | None:
    buttons: list[InlineKeyboardButton] = []

    if page > 0:
        buttons.append(
            InlineKeyboardButton(
                text="⬅️ Oldingi", callback_data=f"{prefix}_{page - 1}"
            )
        )

    if page + 1 < total_pages:
        buttons.append(
            InlineKeyboardButton(
                text="Keyingi ➡️", callback_data=f"{prefix}_{page + 1}"
            )
        )

    if not buttons:
        return None

    return InlineKeyboardMarkup(inline_keyboard=[buttons])


def stats_dashboard_keyboard(active_panel: str) -> InlineKeyboardMarkup:
    def label(panel: str, text: str) -> str:
        return f"• {text}" if panel == active_panel else text

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=label("overview", "📊 Asosiy"), callback_data="stats_overview"
                ),
                InlineKeyboardButton(
                    text=label("traffic", "📈 Trafik"), callback_data="stats_traffic"
                ),
            ],
            [
                InlineKeyboardButton(
                    text=label("movies", "🎬 Kontent"), callback_data="stats_movies"
                ),
                InlineKeyboardButton(
                    text=label("requests", "📥 So'rovlar"),
                    callback_data="stats_requests",
                ),
            ],
        ]
    )


def stats_webapp_keyboard(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🌐 Statistika panelini ochish",
                    web_app=WebAppInfo(url=url),
                )
            ]
        ]
    )


def ads_panel_keyboard(active_ads: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="➕ Yangi reklama", callback_data="ads_new"),
        ]
    ]

    for ad_id, label in active_ads[:6]:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"⏹ #{ad_id} {label}", callback_data=f"ads_stop_{ad_id}"
                )
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def helper_admins_keyboard(
    helper_admins: list[dict[str, object]],
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="➕ Admin qo'shish", callback_data="helper_admins:add"
            )
        ]
    ]

    for helper_admin in helper_admins:
        user_id = int(helper_admin["user_id"])
        full_name = str(helper_admin.get("full_name") or f"Admin {user_id}")
        username = helper_admin.get("username")
        label = f"@{username}" if username else full_name
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"👤 {label[:40]}",
                    callback_data=f"helper_admins:open:{user_id}",
                )
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def helper_admin_detail_keyboard(
    user_id: int, permissions: dict[str, bool]
) -> InlineKeyboardMarkup:
    def toggle_text(label: str, enabled: bool) -> str:
        return f"{'✅' if enabled else '❌'} {label}"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=toggle_text("🎬 Kontent", permissions.get("movies", False)),
                    callback_data=f"helper_admins:toggle:{user_id}:movies",
                ),
                InlineKeyboardButton(
                    text=toggle_text(
                        "📥 So'rovlar", permissions.get("requests", False)
                    ),
                    callback_data=f"helper_admins:toggle:{user_id}:requests",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=toggle_text("📊 Statistika", permissions.get("stats", False)),
                    callback_data=f"helper_admins:toggle:{user_id}:stats",
                ),
                InlineKeyboardButton(
                    text=toggle_text("📣 Reklama", permissions.get("ads", False)),
                    callback_data=f"helper_admins:toggle:{user_id}:ads",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=toggle_text("📡 Kanallar", permissions.get("channels", False)),
                    callback_data=f"helper_admins:toggle:{user_id}:channels",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗑 Olib tashlash",
                    callback_data=f"helper_admins:remove:{user_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga", callback_data="helper_admins:back"
                )
            ],
        ]
    )


def ad_duration_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Bekor qilish", callback_data="ads_cancel"
                ),
            ]
        ]
    )


def movie_buttons(
    code: str,
    is_fav: bool = False,
    *,
    share_url: str | None = None,
    share_query: str | None = None,
    share_callback_data: str | None = None,
) -> InlineKeyboardMarkup:
    text = "💔 Sevimlidan olib tashlash" if is_fav else "❤️ Sevimliga qo'shish"
    callback = f"unfav_{code}" if is_fav else f"fav_{code}"

    action_row: list[InlineKeyboardButton] = [
        InlineKeyboardButton(text=text, callback_data=callback)
    ]
    if share_callback_data:
        action_row.append(
            InlineKeyboardButton(
                text="🔗 Ulashish",
                callback_data=share_callback_data,
            )
        )
    elif share_query:
        action_row.append(
            InlineKeyboardButton(
                text="🔗 Ulashish",
                switch_inline_query=share_query,
            )
        )
    elif share_url:
        action_row.append(InlineKeyboardButton(text="🔗 Ulashish", url=share_url))

    return InlineKeyboardMarkup(inline_keyboard=[action_row])


def serial_hub_keyboard(
    group_code: str,
    episodes: list[tuple[str, str]],
    *,
    page: int,
    total_pages: int,
    is_fav: bool,
    favorite_group_code: str | None = None,
    back_to_group_code: str | None = None,
    back_to_page: int = 0,
    back_text: str = "⬅️ Fasllar",
    share_url: str | None = None,
    share_query: str | None = None,
    share_callback_data: str | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    current_row: list[InlineKeyboardButton] = []

    for episode_code, label in episodes:
        current_row.append(
            InlineKeyboardButton(
                text=label,
                callback_data=f"sepi:{group_code}:{episode_code}:{page}",
            )
        )
        if len(current_row) == 4:
            rows.append(current_row)
            current_row = []

    if current_row:
        rows.append(current_row)

    back_button: InlineKeyboardButton | None = None
    if back_to_group_code:
        back_button = InlineKeyboardButton(
            text=back_text,
            callback_data=f"shub:{back_to_group_code}:{max(0, back_to_page)}",
        )

    if back_button is not None:
        navigation: list[InlineKeyboardButton] = [back_button]
        if total_pages > 1 and page > 0:
            navigation.append(
                InlineKeyboardButton(
                    text="‹ Oldingi",
                    callback_data=f"shub:{group_code}:{page - 1}",
                )
            )
        navigation.append(
            InlineKeyboardButton(
                text=f"{page + 1}/{total_pages}",
                callback_data="noop",
            )
        )
        if total_pages > 1 and page + 1 < total_pages:
            navigation.append(
                InlineKeyboardButton(
                    text="Keyingi ›",
                    callback_data=f"shub:{group_code}:{page + 1}",
                )
            )
        rows.append(navigation)
    elif total_pages > 1:
        rows.append(
            [
                *(
                    [
                        InlineKeyboardButton(
                            text="‹ Oldingi",
                            callback_data=f"shub:{group_code}:{page - 1}",
                        )
                    ]
                    if page > 0
                    else []
                ),
                InlineKeyboardButton(
                    text=f"{page + 1}/{total_pages}",
                    callback_data="noop",
                ),
                *(
                    [
                        InlineKeyboardButton(
                            text="Keyingi ›",
                            callback_data=f"shub:{group_code}:{page + 1}",
                        )
                    ]
                    if page + 1 < total_pages
                    else []
                ),
            ]
        )

    favorite_code = favorite_group_code or group_code

    favorite_text = (
        "💔 Sevimlidan olib tashlash"
        if is_fav
        else "❤️ Sevimliga qo'shish"
    )
    favorite_callback = (
        f"serial_unfav:{favorite_code}:{page}"
        if is_fav
        else f"serial_fav:{favorite_code}:{page}"
    )
    action_row: list[InlineKeyboardButton] = [
        InlineKeyboardButton(
            text=favorite_text,
            callback_data=favorite_callback,
        )
    ]
    if share_callback_data:
        action_row.append(
            InlineKeyboardButton(
                text="🔗 Ulashish",
                callback_data=share_callback_data,
            )
        )
    elif share_query:
        action_row.append(
            InlineKeyboardButton(
                text="🔗 Ulashish",
                switch_inline_query=share_query,
            )
        )
    elif share_url:
        action_row.append(InlineKeyboardButton(text="🔗 Ulashish", url=share_url))
    rows.append(action_row)

    return InlineKeyboardMarkup(inline_keyboard=rows)

