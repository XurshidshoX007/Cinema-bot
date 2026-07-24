import asyncio
import ctypes
import logging
import os
import signal
from contextlib import suppress
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramNetworkError

from advertising import run_ad_maintenance, shutdown_ad_tasks
from config import BOT_TOKEN
from database import DB_PATH, close_db, get_all_movies, init_db
from handlers import ROUTERS
from middlewares.security import InputSanitizationMiddleware
from middlewares.throttling import AntiSpamMiddleware

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(message)s"
RETRY_DELAY_SECONDS = 3
POLLING_TASK_LIMIT = 128
INSTANCE_LOCK_PATH = Path(__file__).resolve().with_name(".bot-instance.pid")


class SingleInstanceError(RuntimeError):
    def __init__(self, existing_pid: str | None = None) -> None:
        self.existing_pid = existing_pid

        if existing_pid:
            message = f"Bot allaqachon ishga tushgan (PID: {existing_pid})"
        else:
            message = "Bot allaqachon ishga tushgan"

        super().__init__(message)


def _read_pid(lock_path: Path) -> str | None:
    with suppress(OSError, ValueError):
        pid = lock_path.read_text(encoding="utf-8").strip()
        if pid:
            int(pid)
            return pid

    return None


def _pid_is_running(pid: str) -> bool:
    pid_value = int(pid)

    if os.name == "nt":
        process_handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid_value)
        if process_handle == 0:
            return False

        ctypes.windll.kernel32.CloseHandle(process_handle)
        return True

    try:
        os.kill(pid_value, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False

    return True


class SingleInstanceLock:
    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path

    def acquire(self) -> None:
        if self._try_create_lock():
            return

        existing_pid = _read_pid(self.lock_path)
        if existing_pid and _pid_is_running(existing_pid):
            raise SingleInstanceError(existing_pid)

        with suppress(FileNotFoundError):
            self.lock_path.unlink()

        if not self._try_create_lock():
            raise SingleInstanceError(_read_pid(self.lock_path))

    def release(self) -> None:
        existing_pid = _read_pid(self.lock_path)
        if existing_pid != str(os.getpid()):
            return

        with suppress(FileNotFoundError):
            self.lock_path.unlink()

    def _try_create_lock(self) -> bool:
        try:
            file_descriptor = os.open(
                self.lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError:
            return False

        with os.fdopen(file_descriptor, "w", encoding="utf-8") as lock_file:
            lock_file.write(str(os.getpid()))

        return True


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)


def create_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher()

    # Xavfsizlik: kiruvchi matnlarni sanitizatsiya qilish (birinchi bo'lib ishlaydi)
    dispatcher.message.middleware(InputSanitizationMiddleware())

    # Anti-spam: tezlikni cheklash
    dispatcher.message.middleware(AntiSpamMiddleware(rate_limit=0.5))
    dispatcher.callback_query.middleware(AntiSpamMiddleware(rate_limit=0.5))

    for router in ROUTERS:
        dispatcher.include_router(router)
    return dispatcher


def configure_process_signals(
    stop_event: asyncio.Event, logger: logging.Logger
) -> None:
    def request_shutdown(signum: int, _frame: object) -> None:
        if stop_event.is_set():
            return

        signal_name = signal.Signals(signum).name
        logger.info("%s signali olindi, bot to'xtatilmoqda", signal_name)
        stop_event.set()

    for signal_name in ("SIGINT", "SIGTERM"):
        current_signal = getattr(signal, signal_name, None)
        if current_signal is not None:
            signal.signal(current_signal, request_shutdown)


async def run_polling_until_stopped(
    dispatcher: Dispatcher,
    bot: Bot,
    stop_event: asyncio.Event,
    logger: logging.Logger,
) -> None:
    while not stop_event.is_set():
        polling_task = asyncio.create_task(
            dispatcher.start_polling(
                bot,
                close_bot_session=False,
                handle_signals=False,
                allowed_updates=dispatcher.resolve_used_update_types(),
                tasks_concurrency_limit=POLLING_TASK_LIMIT,
            )
        )
        stop_task = asyncio.create_task(stop_event.wait())

        try:
            done, pending = await asyncio.wait(
                {polling_task, stop_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if stop_task in done:
                logger.info("Bot foydalanuvchi so'rovi bilan to'xtatilmoqda")

                if not polling_task.done():
                    with suppress(RuntimeError):
                        await dispatcher.stop_polling()

                with suppress(asyncio.CancelledError):
                    await polling_task

                break

            for task in pending:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

            try:
                await polling_task
            except TelegramNetworkError as error:
                logger.warning(
                    "Telegram bilan aloqa uzildi: %s. %s soniyadan keyin qayta urinish.",
                    error,
                    RETRY_DELAY_SECONDS,
                )
            except Exception:
                logger.exception(
                    "Polling kutilmaganda to'xtadi, qayta ishga tushirilmoqda"
                )
            else:
                logger.warning(
                    "Polling kutilmaganda yakunlandi. %s soniyadan keyin qayta ishga tushiriladi.",
                    RETRY_DELAY_SECONDS,
                )
        finally:
            stop_task.cancel()
            with suppress(asyncio.CancelledError):
                await stop_task

        if not stop_event.is_set():
            await asyncio.sleep(RETRY_DELAY_SECONDS)


async def main() -> None:
    setup_logging()
    logger = logging.getLogger(__name__)
    stop_event = asyncio.Event()
    dispatcher = create_dispatcher()
    dispatcher["owner_stop_event"] = stop_event
    instance_lock = SingleInstanceLock(INSTANCE_LOCK_PATH)
    bot: Bot | None = None
    maintenance_task: asyncio.Task[None] | None = None

    try:
        instance_lock.acquire()
    except SingleInstanceError as error:
        logger.error(
            "%s. Eski jarayonni to'xtating yoki admin akkauntdan /shutdown yuboring.",
            error,
        )
        return

    configure_process_signals(stop_event, logger)

    try:
        await init_db()
        all_content = await get_all_movies()
        movie_count = sum(1 for item in all_content if item[2] == "movie")
        serial_count = sum(1 for item in all_content if item[2] == "serial")
        bot = Bot(token=BOT_TOKEN)
        maintenance_task = asyncio.create_task(
            run_ad_maintenance(bot, stop_event, logger)
        )
        logger.info("Baza manzili: %s", DB_PATH)
        logger.info(
            "Kontent yuklandi: %s ta kino, %s ta serial",
            movie_count,
            serial_count,
        )
        logger.info("Bot ishga tushdi")
        await run_polling_until_stopped(dispatcher, bot, stop_event, logger)
    finally:
        stop_event.set()

        if maintenance_task is not None:
            maintenance_task.cancel()
            with suppress(asyncio.CancelledError):
                await maintenance_task

        await shutdown_ad_tasks()
        await close_db()

        if bot is not None:
            await bot.session.close()

        instance_lock.release()
        logger.info("Bot sessiyasi yopildi")


if __name__ == "__main__":
    asyncio.run(main())
