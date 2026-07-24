"""Modular admin package - router and re-exports."""

from aiogram import Router

router = Router()

# Re-export constants and helpers for backward compatibility
from .constants import (
    SPARKLINE_BARS,
    STATS_PANELS,
    ACTIVE_AD_STATUSES,
    SERIAL_TITLES_PAGE_SIZE,
    CONTENT_LIST_PAGE_SIZE,
    CONTENT_LIST_PREVIEW_SIZE,
    DELETE_PANEL_PAGE_SIZE,
    ADMIN_PERMISSION_MOVIES,
    ADMIN_PERMISSION_REQUESTS,
    ADMIN_PERMISSION_STATS,
    ADMIN_PERMISSION_ADS,
    ADMIN_PERMISSION_HELPERS,
    LEGACY_STATS_BUTTON,
    AD_DURATION_UNIT_SECONDS,
    AD_DURATION_PATTERN,
    SEASON_TITLE_PATTERN,
    VIDEO_DOCUMENT_EXTENSIONS,
    AdminChannelState,
)

from .permissions import (
    _is_owner,
    _admin_permissions,
    _is_admin,
    _has_permission,
    _ensure_message_access,
    _ensure_callback_access,
    _admin_menu_markup,
)

from .content_utils import (
    _content_kind_key,
    _content_kind_label,
    _content_kind_icon,
    _is_video_document,
    _extract_uploaded_video_file_id,
    _command_argument,
    _content_list_filter_label,
    _pick_available_filter,
    _normalized_lookup_text,
    _match_serial_titles,
    _safe_html,
    _serial_code_prompt_text,
    _serial_description_prompt_text,
    _serial_video_prompt_text,
    _next_season_title,
    _compact_text_preview,
)

from .render import (
    _helper_admin_label,
    _helper_admin_permission_label,
    _render_helper_admins_panel,
    _render_helper_admin_detail,
    _render_content_overview_section,
    _render_content_section_page,
    _render_content_picker_text,
    _render_delete_panel_text,
    _compact_number,
    _sparkline,
    _bar_chart,
    _format_recent_users,
    _parse_timestamp,
    _format_duration,
    _format_time_left,
    _ad_status_label,
    _ad_type_label,
    _ad_preview,
    _ads_button_rows,
)

from .panels import (
    _build_ads_panel_text,
    _build_ads_panel_view,
    _refresh_saved_ads_panel,
    _load_dashboard_payload,
    _build_dashboard_caption,
    _show_stats_dashboard,
    _build_stats_insights,
    _show_stats_webapp,
    _show_ads_panel,
    _show_helper_admins_panel,
    _show_helper_admin_detail,
    _show_content_list,
    _show_delete_panel,
    _ad_duration_limits_text,
)

from .flows import (
    _ad_duration_prompt,
    _parse_custom_duration,
    _extract_helper_admin_candidate,
    _launch_ad_campaign,
    _start_add_movie_flow,
    _show_serial_mode_picker,
    _show_serial_titles_picker,
    _start_serial_continuation_flow,
    _show_serial_continue_prompt,
)

from .edit_helpers import (
    _refresh_edit_movie_state,
    _build_edit_movie_panel_text,
    _show_edit_movie_panel,
    _select_edit_movie_target,
)

from .request_utils import (
    _request_text,
    _request_added_text,
    _request_rejected_text,
    _request_existing_match_text,
    _find_existing_content_for_request,
    _send_existing_content_to_user,
)

from .states import (
    AddMovieState,
    AdState,
    DeleteMovieState,
    EditMovieState,
    HelperAdminState,
    RefreshMediaState,
)

# Import handlers to register them on router
# Order matters - more specific first
from .handlers import (
    shutdown as _h_shutdown,
    media_refresh as _h_media_refresh,
    edit as _h_edit,
    migrations as _h_migrations,
    helper_admins as _h_helper_admins,
    serial as _h_serial,
    movie as _h_movie,
    ads as _h_ads,
    content as _h_content,
    requests as _h_requests,
    stats as _h_stats,
    global_menu as _h_global_menu,
)


# Compatibility: expose frequently used functions at package level
async def movie_list(message):
    from .panels import _show_content_list
    await _show_content_list(message)


async def delete_start(message, state):
    from .panels import _show_delete_panel
    await _show_delete_panel(message, state=state)


async def show_requests(message):
    from database import get_pending_requests
    from keyboards import request_review_keyboard

    async def send_request_review(message, *, request_id, user_id, text, file_id):
        review_text = _request_text(request_id, user_id, text)
        keyboard = request_review_keyboard(request_id)
        if not file_id:
            await message.answer(review_text, reply_markup=keyboard)
            return
        try:
            await message.answer_photo(photo=file_id, caption=review_text, reply_markup=keyboard)
        except Exception:
            await message.answer_video(video=file_id, caption=review_text, reply_markup=keyboard)

    requests = await get_pending_requests()
    if not requests:
        await message.answer("📭 So'rov yo'q")
        return
    for request_id, user_id, text, file_id in requests:
        await send_request_review(
            message,
            request_id=request_id,
            user_id=user_id,
            text=text,
            file_id=file_id,
        )


async def send_request_review(message, *, request_id, user_id, text, file_id):
    from keyboards import request_review_keyboard

    review_text = _request_text(request_id, user_id, text)
    keyboard = request_review_keyboard(request_id)
    if not file_id:
        await message.answer(review_text, reply_markup=keyboard)
        return
    try:
        await message.answer_photo(photo=file_id, caption=review_text, reply_markup=keyboard)
    except Exception:
        await message.answer_video(video=file_id, caption=review_text, reply_markup=keyboard)


# Keep old module-level functions that might be imported elsewhere
def _request_text_compat(request_id: int, user_id: int, text: str) -> str:
    return _request_text(request_id, user_id, text)


__all__ = [
    "router",
    "ADMIN_PERMISSION_MOVIES",
    "ADMIN_PERMISSION_REQUESTS",
    "ADMIN_PERMISSION_STATS",
    "ADMIN_PERMISSION_ADS",
    "ADMIN_PERMISSION_HELPERS",
    "LEGACY_STATS_BUTTON",
    "SERIAL_TITLES_PAGE_SIZE",
    "CONTENT_LIST_PAGE_SIZE",
    "DELETE_PANEL_PAGE_SIZE",
    "STATS_PANELS",
    "ACTIVE_AD_STATUSES",
    "_is_owner",
    "_admin_permissions",
    "_is_admin",
    "_has_permission",
    "_ensure_message_access",
    "_ensure_callback_access",
    "_content_kind_key",
    "_content_kind_label",
    "_content_kind_icon",
    "_safe_html",
    "_serial_video_prompt_text",
    "_next_season_title",
    "_compact_number",
    "_sparkline",
    "_bar_chart",
    "_format_recent_users",
    "_ad_status_label",
    "_ad_type_label",
    "_ad_preview",
    "_build_ads_panel_text",
    "_build_dashboard_caption",
    "_build_stats_insights",
    "_content_list_filter_label",
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
    "_ad_duration_prompt",
    "AddMovieState",
    "AdState",
    "DeleteMovieState",
    "EditMovieState",
    "HelperAdminState",
    "RefreshMediaState",
    "movie_list",
    "delete_start",
    "show_requests",
    "send_request_review",
]
