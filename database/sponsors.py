"""Sponsor channels repository."""

from .cache import channels_cache
from .connection import _get_db, _execute
from . import cache as cache_module


def _normalize_sponsor_channel_id(value: str) -> str:
    channel_id = (value or "").strip()
    if not channel_id:
        return channel_id

    if channel_id.startswith("https://t.me/"):
        channel_id = "@" + channel_id.rsplit("/", 1)[-1].strip()

    if channel_id.startswith("@"):
        tail = channel_id[1:]
        if tail.isdigit():
            return f"-{tail}" if tail.startswith("100") else tail
        return channel_id

    if channel_id.startswith("100") and channel_id.isdigit():
        return f"-{channel_id}"

    return channel_id


def clear_sponsor_cache():
    cache_module.channels_cache = None


async def get_sponsor_channels() -> list[dict[str, str]]:
    if cache_module.channels_cache is not None:
        return cache_module.channels_cache

    connection = _get_db()
    async with connection.execute(
        "SELECT id, url, name FROM sponsor_channels"
    ) as cursor:
        rows = await cursor.fetchall()

    cache_module.channels_cache = [
        {"id": row[0], "url": row[1], "name": row[2]} for row in rows
    ]
    return cache_module.channels_cache


async def add_sponsor_channel(channel_id: str, url: str, name: str) -> None:
    normalized_id = _normalize_sponsor_channel_id(channel_id)
    connection = _get_db()
    await connection.execute(
        """
        INSERT INTO sponsor_channels (id, url, name) 
        VALUES (?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET url=excluded.url, name=excluded.name
        """,
        (normalized_id, url, name),
    )
    await connection.commit()
    cache_module.channels_cache = None


async def remove_sponsor_channel(channel_id: str) -> None:
    await _execute("DELETE FROM sponsor_channels WHERE id=?", (channel_id,))
    cache_module.channels_cache = None
