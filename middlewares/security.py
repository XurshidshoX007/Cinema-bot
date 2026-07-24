import logging

logger = logging.getLogger(__name__)

import re
import time
from typing import Any, Awaitable, Callable, Dict, Set, Pattern
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from aiogram.exceptions import TelegramBadRequest

# Optional: load allowed commands from environment variable as comma-separated
# e.g., ALLOWED_COMMANDS=start,help,favorite,history,myinfo,stats,ads,etc.
import os

def _get_allowed_commands() -> Set[str]:
    env_val = os.getenv("ALLOWED_COMMANDS")
    if env_val:
        # split by comma, strip, lower
        return {cmd.strip().lower() for cmd in env_val.split(",") if cmd.strip()}
    # Default set based on observed handlers
    return {
        "start",
        "help",
        "favorite",
        "fav",
        "unfav",
        "history",
        "myinfo",
        "stats",
        "ads",
        "channels",
        "admin",  # maybe admin panel command? we'll see
        "serial", # maybe not a command but we keep
        "movie",
        "request",
        "cancel",
        "shutdown", # admin command
        # Add any others as needed
    }

# Pre-compile regex for removing markdown special characters if needed
# We'll just strip and limit length; for safety we can escape telegram markdown v2 characters
# But if bot doesn't use markdown parsing, it's fine.
# We'll apply a simple sanitization: remove control characters, limit length.
_CONTROL_CHARS_RE: Pattern = re.compile(r'[\x00-\x1f\x7f]')

class InputSanitizationMiddleware(BaseMiddleware):
    """
    Sanitizes incoming text from users:
    - Removes control characters
    - Limits length to a safe maximum (e.g., 500 characters for messages, 100 for callback data)
    - Optionally, can escape markdown special characters if bot uses MarkdownV2 parse mode.
    For now, we just clean and truncate.
    """

    def __init__(self, max_message_length: int = 500, max_callback_length: int = 100):
        self.max_message_length = max_message_length
        self.max_callback_length = max_callback_length

    async def __call__(
        self,
        handler: Callable[[Message | CallbackQuery, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.text:
            # Sanitize text
            text = event.text
            # Remove control characters
            text = _CONTROL_CHARS_RE.sub('', text)
            # Trim whitespace
            text = text.strip()
            # Truncate if too long
            if len(text) > self.max_message_length:
                text = text[:self.max_message_length]
                # Optionally notify user? We'll just truncate silently.
            # Update the event's text
            event.text = text
        elif isinstance(event, CallbackQuery) and event.data:
            # Callback data should be short, but we still sanitize
            data_str = event.data
            data_str = _CONTROL_CHARS_RE.sub('', data_str)
            data_str = data_str.strip()
            if len(data_str) > self.max_callback_length:
                data_str = data_str[:self.max_callback_length]
            event.data = data_str

        return await handler(event, data)


class CommandAllowlistMiddleware(BaseMiddleware):
    """
    Only allows messages that are commands (starting with '/') and whose command
    is in the allowed set. All other messages are ignored (silently dropped).
    This helps prevent unexpected text from being processed as commands.
    """

    def __init__(self, allowed_commands: Set[str] | None = None):
        if allowed_commands is None:
            allowed_commands = _get_allowed_commands()
        self.allowed_commands = allowed_commands

    async def __call__(
        self,
        handler: Callable[[Message | CallbackQuery, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any],
    ) -> Any:
        # We only check Message objects; CallbackQuery are triggered by inline buttons,
        # which we assume are safe (they come from our own keyboards). However,
        # we could also validate callback data format if needed.
        if isinstance(event, Message):
            text = event.text or ""
            if not text.startswith('/'):
                # Not a command, ignore silently
                return
            # Extract command name (first word after '/', split by whitespace or @)
            # Example: /start@botname -> start
            command = text[1:].split()[0].split('@')[0].lower()
            if command not in self.allowed_commands:
                # Optionally, we could reply with unknown command, but to avoid
                # information leakage we just ignore.
                # However, for better UX we can send a generic message in private?
                # We'll just ignore.
                return
        # For CallbackQuery, we could also validate that data matches expected pattern,
        # but we'll leave it to specific handlers.
        return await handler(event, data)


class CallbackSignatureMiddleware(BaseMiddleware):
    """
    Verifies that callback_data is signed with a secret key to prevent tampering.
    Expects callback_data in the format: "payload|signature" where signature is
    HMAC-SHA256(secret_key, payload).
    If verification fails, the callback is answered with an alert and not processed.
    """

    def __init__(self, secret_key: str | None = None):
        import os
        import hmac
        import hashlib

        if secret_key is None:
            secret_key = os.getenv("CALLBACK_SECRET", "")
        self.secret_key = secret_key.encode() if isinstance(secret_key, str) else secret_key
        self.hmac = hmac
        self.hashlib = hashlib

    async def __call__(
        self,
        handler: Callable[[CallbackQuery, Dict[str, Any]], Awaitable[Any]],
        event: CallbackQuery,
        data: Dict[str, Any],
    ) -> Any:
        # If no secret key set, skip verification (for backward compatibility)
        if not self.secret_key:
            return await handler(event, data)

        # Callback data should be a string
        payload_sig = event.data or ""
        if '|' not in payload_sig:
            # Invalid format, reject
            await event.answer("Noto'g'ri callback format", show_alert=True)
            return
        payload, signature = payload_sig.split('|', 1)
        # Compute expected signature
        expected = self.hmac.new(self.secret_key, payload.encode(), self.hashlib.sha256).hexdigest()
        # Compare securely
        if not self.hmac.compare_digest(expected, signature):
            await event.answer("Callback imzosini tekshirib bo'lmadi", show_alert=True)
            return
        # Optionally, we can replace event.data with just payload for handlers
        event.data = payload
        return await handler(event, data)