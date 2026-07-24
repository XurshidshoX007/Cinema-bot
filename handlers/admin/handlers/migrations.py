"""Legacy media migration commands."""

from html import escape

from aiogram.filters import Command
from aiogram import types

from database import get_movie, get_all_movies, get_serial_group_for_lookup, get_serial_episodes
from services.legacy_media import legacy_media_enabled, renew_file_id_from_legacy

from .. import router
from ..constants import ADMIN_PERMISSION_MOVIES
from ..permissions import _ensure_message_access
from ..content_utils import _command_argument


@router.message(Command("migrate_media"))
async def migrate_single_media(message: types.Message) -> None:
    if not await _ensure_message_access(message, permission=ADMIN_PERMISSION_MOVIES):
        return

    if not legacy_media_enabled():
        await message.answer(
            "LEGACY_BOT_TOKEN topilmadi.\n.env ga eski bot tokenini qo'shing va qayta urinib ko'ring."
        )
        return

    code = _command_argument(message.text)
    if not code:
        await message.answer("Foydalanish: /migrate_media <kod>")
        return

    movie = await get_movie(code)
    if movie is None:
        await message.answer("Kod topilmadi.")
        return

    title, _description, file_id, _content_kind = movie
    if not file_id:
        await message.answer("Bu kodda media fayl biriktirilmagan.")
        return

    renewed = await renew_file_id_from_legacy(
        code,
        current_bot=message.bot,
        fallback_file_id=file_id,
    )
    if renewed is None:
        await message.answer(
            "Media avtomatik ko'chmadi.\nEski bot tokeni yoki eski faylga kirish imkonini tekshiring."
        )
        return

    if renewed != file_id:
        await message.answer(
            "✅ Media yangilandi\n\n"
            f"📌 Kod: <code>{escape(code)}</code>\n"
            f"🎬 Nomi: <b>{escape(title)}</b>",
            parse_mode="HTML",
        )
        return

    await message.answer(
        "ℹ️ Ushbu kod uchun media allaqachon yangilanganga o'xshaydi."
    )


@router.message(Command("migrate_serial"))
async def migrate_serial_media(message: types.Message) -> None:
    if not await _ensure_message_access(message, permission=ADMIN_PERMISSION_MOVIES):
        return

    if not legacy_media_enabled():
        await message.answer(
            "LEGACY_BOT_TOKEN topilmadi.\n.env ga eski bot tokenini qo'shing va qayta urinib ko'ring."
        )
        return

    raw_code = _command_argument(message.text)
    if not raw_code:
        await message.answer("Foydalanish: /migrate_serial <serial-kodi>")
        return

    group = await get_serial_group_for_lookup(raw_code)
    if group is None:
        await message.answer("Serial kodi topilmadi.")
        return

    group_code, group_title, _group_description = group
    episodes = await get_serial_episodes(group_code)
    if not episodes:
        await message.answer("Bu serialda qism topilmadi.")
        return

    await message.answer(
        f"⏳ {escape(group_title)} uchun media migratsiyasi boshlandi ({len(episodes)} qism).",
        parse_mode="HTML",
    )

    updated = 0
    unchanged = 0
    failed = 0

    for episode_code, _episode_number, _description, file_id in episodes:
        if not file_id:
            failed += 1
            continue

        renewed = await renew_file_id_from_legacy(
            episode_code,
            current_bot=message.bot,
            fallback_file_id=file_id,
        )
        if renewed is None:
            failed += 1
        elif renewed == file_id:
            unchanged += 1
        else:
            updated += 1

    await message.answer(
        "✅ Migratsiya yakunlandi\n\n"
        f"📺 Serial: <b>{escape(group_title)}</b>\n"
        f"🔄 Yangilandi: <b>{updated}</b>\n"
        f"♻️ O'zgarmadi: <b>{unchanged}</b>\n"
        f"⚠️ Xato: <b>{failed}</b>",
        parse_mode="HTML",
    )


@router.message(Command("migrate_all_media"))
async def migrate_all_media(message: types.Message) -> None:
    if not await _ensure_message_access(message, permission=ADMIN_PERMISSION_MOVIES):
        return

    if not legacy_media_enabled():
        await message.answer(
            "LEGACY_BOT_TOKEN topilmadi.\n.env ga eski bot tokenini qo'shing va qayta urinib ko'ring."
        )
        return

    all_items = await get_all_movies()
    if not all_items:
        await message.answer("Bazada kontent topilmadi.")
        return

    await message.answer(
        f"⏳ Barcha kontent uchun media migratsiyasi boshlandi ({len(all_items)} ta kod)."
    )

    updated = 0
    unchanged = 0
    failed = 0

    for code, _title, _kind in all_items:
        movie = await get_movie(code)
        if movie is None:
            failed += 1
            continue

        _movie_title, _description, file_id, _content_kind = movie
        if not file_id:
            failed += 1
            continue

        renewed = await renew_file_id_from_legacy(
            code,
            current_bot=message.bot,
            fallback_file_id=file_id,
        )
        if renewed is None:
            failed += 1
        elif renewed == file_id:
            unchanged += 1
        else:
            updated += 1

    await message.answer(
        "✅ Umumiy migratsiya tugadi\n\n"
        f"🔄 Yangilandi: <b>{updated}</b>\n"
        f"♻️ O'zgarmadi: <b>{unchanged}</b>\n"
        f"⚠️ Xato: <b>{failed}</b>",
        parse_mode="HTML",
    )
