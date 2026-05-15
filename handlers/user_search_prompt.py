from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext

from keyboards import LEGACY_SEARCH_BUTTON, SEARCH_BUTTON, main_menu
from repositories.users import is_admin_user
from services.telegram_context import touch_message_user

from .user import KinoState

router = Router()
SEARCH_PROMPT_TEXT = "🎬 Film kodini yuboring!"


@router.message(F.text.in_((SEARCH_BUTTON, LEGACY_SEARCH_BUTTON)))
async def search_prompt(message: types.Message, state: FSMContext) -> None:
    await touch_message_user(message)
    await state.clear()

    show_admin_panel = await is_admin_user(message.from_user.id)
    await message.answer(
        SEARCH_PROMPT_TEXT,
        reply_markup=main_menu(
            message.from_user.id,
            show_admin_panel=show_admin_panel,
        ),
    )
    await state.set_state(KinoState.waiting_for_code)
