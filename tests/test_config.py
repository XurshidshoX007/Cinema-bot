"""Config module tests."""

import os


def test_bot_token_loaded():
    """BOT_TOKEN environment'dan yuklanishi kerak."""
    from config import BOT_TOKEN
    assert BOT_TOKEN is not None
    assert len(BOT_TOKEN) > 0


def test_admin_id_loaded():
    """ADMIN_ID int bo'lishi kerak."""
    from config import ADMIN_ID
    assert isinstance(ADMIN_ID, int)
    assert ADMIN_ID > 0


def test_sponsor_channels_default():
    """SPONSOR_CHANNELS default bo'sh list."""
    from config import SPONSOR_CHANNELS
    assert isinstance(SPONSOR_CHANNELS, list)
