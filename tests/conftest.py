"""Pytest fixtures for Cinema-bot tests."""

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest

# Test environment
os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("ADMIN_ID", "12345")


@pytest.fixture
def mock_bot():
    """Aiogram Bot mock."""
    bot = MagicMock()
    bot.send_message = AsyncMock()
    bot.send_video = AsyncMock()
    bot.send_photo = AsyncMock()
    bot.send_document = AsyncMock()
    bot.delete_message = AsyncMock()
    bot.get_me = AsyncMock()
    bot.get_chat_member = AsyncMock()
    bot.session = MagicMock()
    bot.session.close = AsyncMock()
    return bot


@pytest.fixture
def mock_message():
    """Aiogram Message mock."""
    msg = MagicMock()
    msg.from_user = MagicMock()
    msg.from_user.id = 999
    msg.from_user.username = "testuser"
    msg.from_user.full_name = "Test User"
    msg.chat = MagicMock()
    msg.chat.id = 999
    msg.text = "/start"
    msg.answer = AsyncMock()
    msg.answer_video = AsyncMock()
    msg.answer_photo = AsyncMock()
    msg.answer_document = AsyncMock()
    msg.bot = MagicMock()
    return msg


@pytest.fixture
def mock_message_no_user():
    """from_user None bo'lgan message."""
    msg = MagicMock()
    msg.from_user = None
    msg.chat = MagicMock()
    msg.chat.id = 999
    msg.text = "/start"
    msg.answer = AsyncMock()
    msg.bot = MagicMock()
    return msg


@pytest.fixture
def mock_callback():
    """Aiogram CallbackQuery mock."""
    cb = MagicMock()
    cb.from_user = MagicMock()
    cb.from_user.id = 999
    cb.from_user.username = "testuser"
    cb.from_user.full_name = "Test User"
    cb.data = "test_data"
    cb.message = MagicMock()
    cb.message.chat = MagicMock()
    cb.message.chat.id = 999
    cb.answer = AsyncMock()
    cb.bot = MagicMock()
    return cb


@pytest.fixture
def mock_callback_no_user():
    """from_user None bo'lgan callback."""
    cb = MagicMock()
    cb.from_user = None
    cb.data = "test_data"
    cb.message = MagicMock()
    cb.answer = AsyncMock()
    cb.bot = MagicMock()
    return cb
