import time
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import Message, CallbackQuery


class AntiSpamMiddleware(BaseMiddleware):
    def __init__(self, rate_limit: float = 0.5):
        self.rate_limit = rate_limit
        self.users = {}

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any],
    ) -> Any:

        # Odatda rate limit faqat Message lar uchun, lekin CallbackQuery larni ham himoya qilish mumkin.
        user_id = event.from_user.id
        now = time.monotonic()

        last_time = self.users.get(user_id, 0)
        if now - last_time < self.rate_limit:
            # Agar callback bo'lsa javob bermasdan qoldirish Telegramda kutib qolishni (clock icon) yaratadi. Answer qilamiz.
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer(
                        "Juda tez yuboryapsiz, biroz kuting...", show_alert=False
                    )
                except (TelegramBadRequest, TelegramForbiddenError):
                    pass
            return

        self.users[user_id] = now

        # Xotira tozalash
        if len(self.users) > 5000:
            cutoff = now - self.rate_limit
            self.users = {k: v for k, v in self.users.items() if v > cutoff}

        return await handler(event, data)
