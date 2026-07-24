"""Edit movie state helpers."""

from aiogram import types
from aiogram.fsm.context import FSMContext
from keyboards import edit_movie_action_keyboard

from database import get_movie, get_serial_group_for_lookup

from .content_utils import _content_kind_key, _content_kind_label, _content_kind_icon, _safe_html, _compact_text_preview


async def _refresh_edit_movie_state(
    state: FSMContext,
    code: str,
) -> tuple[str, str, str, str] | None:
    movie = await get_movie(code)
    if movie is None:
        return None

    title, description, file_id, content_kind = movie
    serial_title = ""
    if _content_kind_key(content_kind) == "serial":
        serial_group = await get_serial_group_for_lookup(code)
        if serial_group is not None:
            serial_title = serial_group[1]

    await state.update_data(
        edit_movie_code=code,
        edit_movie_title=title,
        edit_movie_description=description,
        edit_movie_kind=content_kind,
        edit_movie_serial_title=serial_title,
        edit_movie_has_file=bool(file_id),
    )
    return movie


def _build_edit_movie_panel_text(data: dict[str, object]) -> str:
    code = str(data.get("edit_movie_code") or "").strip()
    content_kind = _content_kind_key(str(data.get("edit_movie_kind") or "movie"))
    title = str(data.get("edit_movie_title") or "Kontent").strip() or "Kontent"
    serial_title = str(data.get("edit_movie_serial_title") or "").strip()
    description = str(data.get("edit_movie_description") or "")
    lines = [
        "✏️ <b>Kontent tahrirlash</b>",
        "",
        f"📌 Kod: <code>{_safe_html(code)}</code>",
        f"{_content_kind_icon(content_kind)} Turi: <b>{_safe_html(_content_kind_label(content_kind))}</b>",
    ]

    if content_kind == "serial" and serial_title:
        lines.append(f"📺 Serial: <b>{_safe_html(serial_title)}</b>")
        lines.append(f"🎞 Qism: <b>{_safe_html(title)}</b>")
    else:
        lines.append(f"🎬 Nomi: <b>{_safe_html(title)}</b>")

    lines.append(f"📄 Tavsif: {_safe_html(_compact_text_preview(description, limit=220))}")

    if content_kind == "serial":
        lines.extend(
            [
                "",
                "<i>Serial nomi yangilansa shu guruhdagi qismlar birga yangilanadi.</i>",
            ]
        )

    lines.extend(["", "Kerakli maydonni pastdagi tugmalardan tanlang."])
    return "\n".join(lines)


async def _show_edit_movie_panel(
    message: types.Message,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    content_kind = _content_kind_key(str(data.get("edit_movie_kind") or "movie"))
    from .states import EditMovieState

    await state.set_state(EditMovieState.waiting_for_action)
    await message.answer(
        _build_edit_movie_panel_text(data),
        reply_markup=edit_movie_action_keyboard(content_kind=content_kind),
        parse_mode="HTML",
    )


async def _select_edit_movie_target(
    message: types.Message,
    state: FSMContext,
    raw_code: str,
) -> bool:
    code = raw_code.strip()
    if not code:
        await message.answer("Tahrirlash uchun kod yuboring.")
        return False

    movie = await _refresh_edit_movie_state(state, code)
    if movie is None:
        serial_group = await get_serial_group_for_lookup(code)
        if serial_group is not None:
            await message.answer(
                "Bu serialning umumiy kodi.\nTahrirlash uchun qism kodini yuboring."
            )
        else:
            await message.answer("Bunday kodli kontent topilmadi.")
        return False

    await _show_edit_movie_panel(message, state)
    return True
