"""Serial flow handlers."""

from aiogram import F, types
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest

from database import get_next_serial_episode_number, get_serial_titles

from .. import router
from ..constants import ADMIN_PERMISSION_MOVIES
from ..permissions import _ensure_message_access, _ensure_callback_access
from ..states import AddMovieState
from ..flows import _show_serial_mode_picker, _show_serial_titles_picker, _start_serial_continuation_flow
from ..content_utils import _match_serial_titles


@router.callback_query(F.data == "serial_mode_new")
async def serial_mode_new(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_callback_access(callback, permission=ADMIN_PERMISSION_MOVIES):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    await callback.answer()

    if callback.message is not None:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await _start_add_movie_flow_wrapper(callback.message, state, content_kind="serial")


async def _start_add_movie_flow_wrapper(message, state, content_kind="movie"):
    from ..flows import _start_add_movie_flow

    await _start_add_movie_flow(message, state, content_kind=content_kind)


@router.callback_query(F.data == "serial_mode_continue")
async def serial_mode_continue(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _ensure_callback_access(callback, permission=ADMIN_PERMISSION_MOVIES):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    await callback.answer()

    if callback.message is not None:
        await _show_serial_titles_picker(callback.message, state, page=0, edit=True)


@router.callback_query(F.data == "serial_mode_cancel")
async def serial_mode_cancel(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_callback_access(callback, permission=ADMIN_PERMISSION_MOVIES):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    await state.clear()
    await callback.answer("Bekor qilindi")

    if callback.message is not None:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass


@router.callback_query(
    AddMovieState.waiting_for_serial_pick, F.data.startswith("serial_page_")
)
async def serial_titles_page(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_callback_access(callback, permission=ADMIN_PERMISSION_MOVIES):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    if callback.message is None:
        await callback.answer()
        return

    try:
        page = int(callback.data.rsplit("_", 1)[1])
    except ValueError:
        await callback.answer("Tanlov eskirgan", show_alert=True)
        return

    data = await state.get_data()
    titles = data.get("serial_titles") or await get_serial_titles()
    await callback.answer()
    await _show_serial_titles_picker(
        callback.message,
        state,
        page=page,
        titles=titles,
        edit=True,
    )


@router.callback_query(
    AddMovieState.waiting_for_serial_pick, F.data.startswith("serial_pick_")
)
async def serial_pick(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_callback_access(callback, permission=ADMIN_PERMISSION_MOVIES):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    if callback.message is None:
        await callback.answer()
        return

    try:
        index = int(callback.data.rsplit("_", 1)[1])
    except ValueError:
        await callback.answer("Tanlov eskirgan", show_alert=True)
        return

    data = await state.get_data()
    titles = data.get("serial_titles") or await get_serial_titles()
    if not 0 <= index < len(titles):
        await callback.answer("Serial topilmadi", show_alert=True)
        return

    await callback.answer()
    await _start_serial_continuation_flow(
        callback.message,
        state,
        titles[index],
        edit=True,
    )


@router.callback_query(
    AddMovieState.waiting_for_serial_pick, F.data == "serial_season_continue"
)
async def serial_pick_continue_same_season(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _ensure_callback_access(callback, permission=ADMIN_PERMISSION_MOVIES):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    if callback.message is None:
        await callback.answer()
        return

    data = await state.get_data()
    selected_title = data.get("serial_pick_title")
    if not selected_title:
        await callback.answer("Tanlov eskirgan", show_alert=True)
        return

    await callback.answer("Shu fasl davom ettiriladi")
    next_episode_number = await get_next_serial_episode_number(str(selected_title))
    await _start_serial_continuation_flow(
        callback.message,
        state,
        str(selected_title),
        episode_number=int(next_episode_number),
        edit=True,
    )


@router.callback_query(
    AddMovieState.waiting_for_serial_pick, F.data == "serial_season_new"
)
async def serial_pick_start_new_season(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _ensure_callback_access(callback, permission=ADMIN_PERMISSION_MOVIES):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    if callback.message is None:
        await callback.answer()
        return

    data = await state.get_data()
    next_season_title = data.get("serial_pick_next_season")
    if not next_season_title:
        await callback.answer("Tanlov eskirgan", show_alert=True)
        return

    await callback.answer("Yangi fasl boshlandi")
    await _start_serial_continuation_flow(
        callback.message,
        state,
        str(next_season_title),
        episode_number=1,
        edit=True,
    )


@router.message(AddMovieState.waiting_for_serial_pick)
async def receive_existing_serial_name(
    message: types.Message, state: FSMContext
) -> None:
    if not await _ensure_message_access(message, permission=ADMIN_PERMISSION_MOVIES):
        return

    if not message.text or not message.text.strip():
        await message.answer(
            "⚠️ <b>Serial nomi kerak</b>\n\n<i>Nom yozing yoki ro'yxatdan tanlang</i>",
            parse_mode="HTML",
        )
        return

    titles = await get_serial_titles()
    matches = _match_serial_titles(titles, message.text.strip())
    if not matches:
        await message.answer(
            "🔎 <b>Serial topilmadi</b>\n\n"
            "<i>Nomni qayta yuboring yoki ro'yxatdan tanlang</i>",
            parse_mode="HTML",
        )
        return

    if len(matches) == 1:
        await _start_serial_continuation_flow(message, state, matches[0])
        return

    await _show_serial_titles_picker(
        message,
        state,
        page=0,
        titles=matches,
    )


@router.callback_query(
    AddMovieState.waiting_for_serial_continue, F.data == "serial_more_yes"
)
async def serial_more_yes(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_callback_access(callback, permission=ADMIN_PERMISSION_MOVIES):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    data = await state.get_data()
    series_title = data.get("series_title")
    episode_number = data.get("episode_number")
    if not series_title or not episode_number:
        await state.clear()
        await callback.answer("Tanlov eskirgan", show_alert=True)
        return

    await callback.answer("Davom etamiz")

    if callback.message is not None:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await _start_serial_continuation_flow(
            callback.message,
            state,
            str(series_title),
            episode_number=int(episode_number),
        )


@router.callback_query(
    AddMovieState.waiting_for_serial_continue, F.data == "serial_more_no"
)
async def serial_more_no(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_callback_access(callback, permission=ADMIN_PERMISSION_MOVIES):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    await state.clear()
    await callback.answer("Yakunlandi")

    if callback.message is not None:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass


@router.callback_query(AddMovieState.waiting_for_video, F.data == "serial_upload_finish")
async def serial_upload_finish(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    if not await _ensure_callback_access(callback, permission=ADMIN_PERMISSION_MOVIES):
        await callback.answer("Sizga ruxsat yo'q", show_alert=True)
        return

    data = await state.get_data()
    from ..content_utils import _content_kind_key

    if _content_kind_key(data.get("content_kind")) != "serial":
        await callback.answer()
        return

    await state.clear()
    await callback.answer("Yakunlandi")

    if callback.message is not None:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await callback.message.answer(
            "✅ <b>Serial yuklash yakunlandi</b>\n\n<i>Qismlar saqlandi.</i>",
            parse_mode="HTML",
        )
