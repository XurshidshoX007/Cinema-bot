from aiogram import Router, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext

from keyboards import main_menu
from repositories.users import is_admin_user
from services.telegram_context import touch_message_user

router = Router()
DEEP_LINK_WATCH_PREFIX = "watch_"
START_TEXT = (
    "👋 Assalomu alaykum!\n"
    "✨ PrimeCinema botiga xush kelibsiz!\n\n"
    "🎬 Bu yerda siz xohlagan kinongizni bir zumda topishingiz mumkin.\n\n"
    "🎥 Kod yuboring va tomosha dunyosiga qo'shiling.\n\n"
    "💎 Xizmatlarimiz:\n"
    "  └ ⚡️ Tezkor qidiruv\n"
    "  └ 🍿 HD sifatdagi videolar\n\n"
    "🎬 Film kodini yuboring yoki\n"
    "👇 Quyidagi tugmalardan birini bosing:"
)


def _start_payload(text: str | None) -> str:
    raw_text = (text or "").strip()
    if not raw_text:
        return ""

    parts = raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return ""

    return parts[1].strip()


@router.message(CommandStart())
async def start_handler(message: types.Message, state: FSMContext):
    await touch_message_user(message)
    await state.clear()
    payload = _start_payload(message.text)
    if payload.startswith(DEEP_LINK_WATCH_PREFIX):
        code = payload[len(DEEP_LINK_WATCH_PREFIX) :].strip()
        if code:
            from handlers import user as user_handler

            await user_handler.open_shared_content(message, code)
            return

    show_admin_panel = await is_admin_user(message.from_user.id)
    await message.answer(
        START_TEXT,
        reply_markup=main_menu(message.from_user.id, show_admin_panel=show_admin_panel),
    )
    from handlers.user import KinoState

    await state.set_state(KinoState.waiting_for_code)
