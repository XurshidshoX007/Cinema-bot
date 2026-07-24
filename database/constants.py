"""Shared constants for database package."""

from datetime import timedelta, timezone
from contextlib import suppress

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

SQLITE_BUSY_TIMEOUT_MS = 5000
SQLITE_CACHE_SIZE_KIB = 32768
SQLITE_MMAP_SIZE = 268435456
MAX_HISTORY_ITEMS = 50
USER_TOUCH_TTL_SECONDS = 300
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
APP_TIMEZONE_NAME = "Asia/Tashkent"
APP_TIMEZONE_LABEL = "UZT"

if ZoneInfo is not None:
    with suppress(Exception):
        APP_TIMEZONE = ZoneInfo(APP_TIMEZONE_NAME)

if "APP_TIMEZONE" not in globals():
    APP_TIMEZONE = timezone(timedelta(hours=5), name=APP_TIMEZONE_LABEL)

MIN_AD_DURATION_SECONDS = 600
MAX_AD_DURATION_SECONDS = 30 * 86400
UNBLOCKED_USER_SQL = "COALESCE(is_blocked, 0) = 0"
ADMIN_PERMISSIONS = ("movies", "requests", "stats", "ads", "channels")
ADMIN_PERMISSION_COLUMNS = {
    "movies": "can_manage_movies",
    "requests": "can_manage_requests",
    "stats": "can_view_stats",
    "ads": "can_manage_ads",
    "channels": "can_manage_channels",
}

CACHE_MAX_MOVIES = 10_000
CACHE_MAX_SERIAL_GROUPS = 2_000
CACHE_MAX_FAVORITES = 20_000
CACHE_MAX_USER_ACTIVITY = 10_000
CACHE_MAX_VIEW_EXCLUSION = 5_000

_SAFE_TABLE_NAMES = frozenset(
    {
        "users",
        "movies",
        "serial_groups",
        "movie_views",
        "favorites",
        "history",
        "requests",
        "ads",
        "ad_deliveries",
        "helper_admins",
        "sponsor_channels",
        "content_view_events",
        "user_search_events",
        "feature_trials",
    }
)
