"""Cache storages and helpers."""

from .constants import (
    CACHE_MAX_FAVORITES,
    CACHE_MAX_MOVIES,
    CACHE_MAX_SERIAL_GROUPS,
    CACHE_MAX_USER_ACTIVITY,
    CACHE_MAX_VIEW_EXCLUSION,
)

db = None  # will be set by connection module; shared reference
movie_cache: dict[str, tuple[str, str, str, str]] = {}
serial_group_cache: dict[str, tuple[str, str, str, str]] = {}
fav_cache: dict[int, set[str]] = {}
user_activity_cache: dict[int, tuple[float, str]] = {}
view_tracking_exclusion_cache: dict[int, bool] = {}
channels_cache: list[dict[str, str]] | None = None


def _trim_cache(cache: dict, max_size: int) -> None:
    """Cache hajmi chegaradan oshsa, eng eski yozuvlarni o'chiradi."""
    if len(cache) <= max_size:
        return
    excess = len(cache) - max_size
    keys_to_remove = list(cache.keys())[:excess]
    for key in keys_to_remove:
        cache.pop(key, None)


def clear_all_caches():
    movie_cache.clear()
    serial_group_cache.clear()
    fav_cache.clear()
    user_activity_cache.clear()
    view_tracking_exclusion_cache.clear()
    global channels_cache
    channels_cache = None
