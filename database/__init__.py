"""Modular database package - backward compatible with old database.py"""

# Re-export core constants and paths
from .constants import (
    ADMIN_PERMISSIONS,
    ADMIN_PERMISSION_COLUMNS,
    APP_TIMEZONE,
    APP_TIMEZONE_LABEL,
    APP_TIMEZONE_NAME,
    CACHE_MAX_FAVORITES,
    CACHE_MAX_MOVIES,
    CACHE_MAX_SERIAL_GROUPS,
    CACHE_MAX_USER_ACTIVITY,
    CACHE_MAX_VIEW_EXCLUSION,
    MAX_HISTORY_ITEMS,
    MIN_AD_DURATION_SECONDS,
    MAX_AD_DURATION_SECONDS,
    SQLITE_BUSY_TIMEOUT_MS,
    SQLITE_CACHE_SIZE_KIB,
    SQLITE_MMAP_SIZE,
    TIMESTAMP_FORMAT,
    UNBLOCKED_USER_SQL,
    USER_TOUCH_TTL_SECONDS,
)
from .paths import DB_PATH, PRIMARY_DB_PATH, RUNTIME_DB_PATH, INSPECT_DB_PATH, COPY_DB_PATH

from .cache import (
    movie_cache,
    serial_group_cache,
    fav_cache,
    user_activity_cache,
    view_tracking_exclusion_cache,
    channels_cache,
    _trim_cache,
    clear_all_caches,
)

from .utils import (
    _ad_row_to_dict,
    _base_actor_filter,
    _display_title,
    _format_timestamp,
    _helper_admin_row_to_dict,
    _normalize_admin_permission,
    _normalize_content_kind,
    _normalize_title_for_match,
    _placeholders,
    _serial_base_title,
    _serial_group_entry,
    _today_key,
    _visible_user_filter,
    format_local_timestamp,
    local_day_keys,
    local_day_start_utc,
    local_now,
    local_now_text,
    parse_utc_timestamp,
)

from .connection import (
    _get_db,
    _get_table_columns,
    _ensure_users_tracking_columns,
    _ensure_movie_views_columns,
    _ensure_stats_event_tables,
    _execute,
    init_db,
    close_db,
)

# Alias db variable for backward compat (some code accesses database.db)
from .connection import _db as db

# Movies
from .movies import (
    add_movie,
    add_movie_auto_code,
    get_movie,
    update_movie_title,
    update_movie_description,
    update_movie_file_id,
    get_movies_by_codes,
    get_all_movies,
    search_movies_by_text,
    is_content_code_taken,
    delete_movie,
)

# Serials
from .serials import (
    _clear_serial_group_cache,
    _content_code_exists,
    _next_available_numeric_code,
    _pick_serial_group_code,
    _sync_serial_group,
    _sync_all_serial_groups,
    _normalize_collection_serial_codes,
    get_serial_titles,
    get_serial_groups,
    get_serial_group,
    get_serial_group_for_lookup,
    get_serial_episodes,
    get_serial_episode,
    get_next_serial_episode_number,
    get_movies_for_serial_base,
)

# Users
from .users import (
    touch_user,
    mark_user_blocked,
    delete_blocked_users,
    has_feature_trial_used,
    mark_feature_trial_used,
    get_user_snapshot,
)

# Admins
from .admins import (
    get_admin_permissions,
    is_admin_user,
    get_helper_admin,
    list_helper_admins,
    upsert_helper_admin,
    set_helper_admin_permission,
    remove_helper_admin,
)

# Favorites
from .favorites import (
    add_favorite,
    remove_favorite,
    is_favorite,
    get_favorites,
    count_favorites,
    get_favorites_page,
)

# History
from .history import (
    add_history,
    get_history,
    count_history,
    get_history_page,
)

# Requests
from .requests import (
    add_request,
    get_pending_requests,
    get_request,
    update_request_status,
    delete_request,
)

# Views
from .views import (
    _is_view_tracking_excluded,
    record_movie_view,
    log_user_search_event,
)

# Dashboard
from .dashboard import (
    get_dashboard_summary_snapshot,
    get_request_status_counts_snapshot,
    get_daily_metric_series_snapshot,
    get_dashboard_summary,
    get_request_status_counts,
    get_daily_metric_series,
    get_top_viewed_movies,
    get_recent_users,
)

# Ads
from .ads import (
    create_ad_campaign,
    get_ad_campaign,
    get_ad_status,
    get_ads_by_status,
    get_recent_ads,
    get_pending_ad_recipients,
    record_ad_delivery_batch,
    finish_ad_broadcast,
    request_stop_ad,
    claim_ad_for_cleanup,
    get_ad_delete_batch,
    record_ad_delete_batch,
    finish_ad_cleanup,
)

# Sponsors
from .sponsors import (
    _normalize_sponsor_channel_id,
    get_sponsor_channels,
    add_sponsor_channel,
    remove_sponsor_channel,
)

# For compatibility with old imports that used _trim_cache from database
__all__ = [
    # constants
    "ADMIN_PERMISSIONS",
    "ADMIN_PERMISSION_COLUMNS",
    "APP_TIMEZONE",
    "APP_TIMEZONE_LABEL",
    "APP_TIMEZONE_NAME",
    "CACHE_MAX_FAVORITES",
    "CACHE_MAX_MOVIES",
    "CACHE_MAX_SERIAL_GROUPS",
    "CACHE_MAX_USER_ACTIVITY",
    "CACHE_MAX_VIEW_EXCLUSION",
    "MAX_HISTORY_ITEMS",
    "MIN_AD_DURATION_SECONDS",
    "MAX_AD_DURATION_SECONDS",
    "SQLITE_BUSY_TIMEOUT_MS",
    "SQLITE_CACHE_SIZE_KIB",
    "SQLITE_MMAP_SIZE",
    "TIMESTAMP_FORMAT",
    "UNBLOCKED_USER_SQL",
    "USER_TOUCH_TTL_SECONDS",
    # paths
    "DB_PATH",
    "PRIMARY_DB_PATH",
    "RUNTIME_DB_PATH",
    "INSPECT_DB_PATH",
    "COPY_DB_PATH",
    # cache
    "movie_cache",
    "serial_group_cache",
    "fav_cache",
    "user_activity_cache",
    "view_tracking_exclusion_cache",
    "channels_cache",
    "_trim_cache",
    "clear_all_caches",
    "db",
    # utils
    "format_local_timestamp",
    "local_day_keys",
    "local_day_start_utc",
    "local_now",
    "local_now_text",
    "parse_utc_timestamp",
    "_format_timestamp",
    "_display_title",
    "_serial_base_title",
    # connection
    "_get_db",
    "init_db",
    "close_db",
    # movies
    "add_movie",
    "add_movie_auto_code",
    "get_movie",
    "update_movie_title",
    "update_movie_description",
    "update_movie_file_id",
    "get_movies_by_codes",
    "get_all_movies",
    "search_movies_by_text",
    "is_content_code_taken",
    "delete_movie",
    # serials
    "get_serial_titles",
    "get_serial_groups",
    "get_serial_group",
    "get_serial_group_for_lookup",
    "get_serial_episodes",
    "get_serial_episode",
    "get_next_serial_episode_number",
    "get_movies_for_serial_base",
    # users
    "touch_user",
    "mark_user_blocked",
    "delete_blocked_users",
    "has_feature_trial_used",
    "mark_feature_trial_used",
    "get_user_snapshot",
    # admins
    "get_admin_permissions",
    "is_admin_user",
    "get_helper_admin",
    "list_helper_admins",
    "upsert_helper_admin",
    "set_helper_admin_permission",
    "remove_helper_admin",
    # favorites
    "add_favorite",
    "remove_favorite",
    "is_favorite",
    "get_favorites",
    "count_favorites",
    "get_favorites_page",
    # history
    "add_history",
    "get_history",
    "count_history",
    "get_history_page",
    # requests
    "add_request",
    "get_pending_requests",
    "get_request",
    "update_request_status",
    "delete_request",
    # views
    "record_movie_view",
    "log_user_search_event",
    # dashboard
    "get_dashboard_summary_snapshot",
    "get_request_status_counts_snapshot",
    "get_daily_metric_series_snapshot",
    "get_dashboard_summary",
    "get_request_status_counts",
    "get_daily_metric_series",
    "get_top_viewed_movies",
    "get_recent_users",
    # ads
    "create_ad_campaign",
    "get_ad_campaign",
    "get_ad_status",
    "get_ads_by_status",
    "get_recent_ads",
    "get_pending_ad_recipients",
    "record_ad_delivery_batch",
    "finish_ad_broadcast",
    "request_stop_ad",
    "claim_ad_for_cleanup",
    "get_ad_delete_batch",
    "record_ad_delete_batch",
    "finish_ad_cleanup",
    # sponsors
    "get_sponsor_channels",
    "add_sponsor_channel",
    "remove_sponsor_channel",
    "_normalize_sponsor_channel_id",
]
