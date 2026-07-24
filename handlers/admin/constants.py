"""Shared constants for admin handlers."""

import re
from aiogram.fsm.state import State, StatesGroup

SPARKLINE_BARS = "▁▂▃▄▅▆▇█"
STATS_PANELS = {"overview", "traffic", "movies", "requests"}
ACTIVE_AD_STATUSES = (
    "broadcasting",
    "stop_requested",
    "active",
    "stopping",
    "cleaning",
)
SERIAL_TITLES_PAGE_SIZE = 8
CONTENT_LIST_PAGE_SIZE = 8
CONTENT_LIST_PREVIEW_SIZE = 5
DELETE_PANEL_PAGE_SIZE = 8

ADMIN_PERMISSION_MOVIES = "movies"
ADMIN_PERMISSION_REQUESTS = "requests"
ADMIN_PERMISSION_STATS = "stats"
ADMIN_PERMISSION_ADS = "ads"
ADMIN_PERMISSION_HELPERS = "helpers"
LEGACY_STATS_BUTTON = "📊 Statistika"

AD_DURATION_UNIT_SECONDS = {
    "m": 60,
    "min": 60,
    "mins": 60,
    "minute": 60,
    "minutes": 60,
    "minut": 60,
    "minuta": 60,
    "daq": 60,
    "daqiqa": 60,
    "h": 3600,
    "hr": 3600,
    "hrs": 3600,
    "hour": 3600,
    "hours": 3600,
    "soat": 3600,
    "d": 86400,
    "day": 86400,
    "days": 86400,
    "kun": 86400,
}
AD_DURATION_PATTERN = re.compile(r"(\d+)\s*([a-z]+)", re.IGNORECASE)
SEASON_TITLE_PATTERN = re.compile(
    r"^\s*(?P<base>.+?)\s+(?P<season>\d+)\s*-\s*fasl\s*$",
    re.IGNORECASE,
)

VIDEO_DOCUMENT_EXTENSIONS = (
    ".mp4",
    ".mkv",
    ".avi",
    ".mov",
    ".webm",
    ".m4v",
    ".mpeg",
    ".mpg",
)


class AdminChannelState(StatesGroup):
    waiting_for_channel = State()
