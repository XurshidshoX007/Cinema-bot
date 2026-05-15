from aiogram import F, Router, types

from keyboards import movie_buttons, serial_hub_keyboard
from repositories.content import (
    add_favorite,
    get_serial_episodes,
    get_serial_group_for_lookup,
    is_favorite,
    remove_favorite,
)
from services.telegram_context import touch_callback_user
from services.user_views import SERIAL_HUB_PAGE_SIZE

router = Router()


@router.callback_query(F.data.startswith(("fav_", "unfav_")))
async def toggle_favorite(callback: types.CallbackQuery) -> None:
    await touch_callback_user(callback)
    raw_code = callback.data.split("_")[1]
    serial_group = await get_serial_group_for_lookup(raw_code)
    code = serial_group[0] if serial_group is not None else raw_code
    user_id = callback.from_user.id

    if await is_favorite(user_id, code):
        await remove_favorite(user_id, code)
        await callback.answer("Sevimlilardan olib tashlandi")

        try:
            await callback.message.edit_reply_markup(
                reply_markup=movie_buttons(code, is_fav=False)
            )
        except Exception:
            pass
    else:
        await add_favorite(user_id, code)
        await callback.answer("Sevimlilarga qo'shildi")

        try:
            await callback.message.edit_reply_markup(
                reply_markup=movie_buttons(code, is_fav=True)
            )
        except Exception:
            pass


@router.callback_query(F.data.startswith(("serial_fav:", "serial_unfav:")))
async def toggle_serial_favorite(callback: types.CallbackQuery) -> None:
    await touch_callback_user(callback)
    try:
        action, code, page_text = callback.data.split(":", 2)
        page_value = int(page_text or 0)
    except ValueError:
        await callback.answer("Bo'limni qayta oching", show_alert=True)
        return

    user_id = callback.from_user.id

    if action == "serial_unfav":
        await remove_favorite(user_id, code)
        await callback.answer("Sevimlilardan olib tashlandi")
        is_fav = False
    else:
        await add_favorite(user_id, code)
        await callback.answer("Sevimlilarga qo'shildi")
        is_fav = True

    if callback.message is None:
        return

    episodes = await get_serial_episodes(code)
    if not episodes:
        return

    total_pages = max(1, (len(episodes) + SERIAL_HUB_PAGE_SIZE - 1) // SERIAL_HUB_PAGE_SIZE)
    page = min(max(page_value, 0), total_pages - 1)
    start = page * SERIAL_HUB_PAGE_SIZE
    visible = episodes[start : start + SERIAL_HUB_PAGE_SIZE]

    try:
        await callback.message.edit_reply_markup(
            reply_markup=serial_hub_keyboard(
                code,
                [
                    (episode_code, f"{max(1, int(episode_number or 1))}-qism")
                    for episode_code, episode_number, _description, _file_id in visible
                ],
                page=page,
                total_pages=total_pages,
                is_fav=is_fav,
            )
        )
    except Exception:
        pass
