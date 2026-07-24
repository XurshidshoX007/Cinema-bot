"""Middleware tests."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from middlewares.throttling import AntiSpamMiddleware


@pytest.mark.asyncio
async def test_antispam_allows_first_message():
    """Birinchi xabar o'tishi kerak."""
    mw = AntiSpamMiddleware(rate_limit=1.0)
    handler = AsyncMock()
    event = MagicMock()
    event.from_user = MagicMock()
    event.from_user.id = 1
    data = {}

    await mw(handler, event, data)
    handler.assert_called_once()


@pytest.mark.asyncio
async def test_antispam_blocks_fast_repeat():
    """Tez takrorlangan xabar bloklanishi kerak."""
    mw = AntiSpamMiddleware(rate_limit=10.0)  # 10 soniya — testda garantiya
    handler = AsyncMock()
    event = MagicMock()
    event.from_user = MagicMock()
    event.from_user.id = 2
    data = {}

    await mw(handler, event, data)  # 1-chi — o'tadi
    handler.reset_mock()
    await mw(handler, event, data)  # 2-chi — bloklanadi
    handler.assert_not_called()


@pytest.mark.asyncio
async def test_antispam_none_user():
    """from_user None bo'lganda crash qilmasligi kerak."""
    mw = AntiSpamMiddleware(rate_limit=1.0)
    handler = AsyncMock()
    event = MagicMock()
    event.from_user = None
    data = {}

    await mw(handler, event, data)
    handler.assert_called_once()
