"""from_user None guard testlari."""

import pytest
from services.telegram_context import touch_message_user, touch_callback_user


@pytest.mark.asyncio
async def test_touch_message_user_none(mock_message_no_user):
    """from_user None bo'lganda crash qilmasligi kerak."""
    await touch_message_user(mock_message_no_user)
    # Hech qanday exception chiqmasa — test o'tdi


@pytest.mark.asyncio
async def test_touch_callback_user_none(mock_callback_no_user):
    """from_user None bo'lganda crash qilmasligi kerak."""
    await touch_callback_user(mock_callback_no_user)
