import asyncio
import logging
import re
from collections import OrderedDict

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from . import config
from .youtube import (
    Result,
    UserError,
    extract_video_id,
    get_text,
    header,
    plain_text,
    timecoded_text,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("yt-text-bot")

dp = Dispatcher()
semaphore = asyncio.Semaphore(config.MAX_PARALLEL)
busy_users: set[int] = set()

# Кэш последних результатов, чтобы кнопки «с таймкодами» и повторные ссылки работали мгновенно
CACHE_SIZE = 100
cache: "OrderedDict[str, Result]" = OrderedDict()

MAX_MESSAGE = 3800  # лимит Telegram 4096, с запасом на заголовок

START_TEXT = (
    "Привет! Пришлите ссылку на видео с YouTube — я пришлю его текст.\n\n"
    "Подходят обычные видео, Shorts и записи эфиров. "
    "Если у видео есть субтитры, текст будет почти сразу; "
    "если нет — распознаю речь, это займёт пару минут."
)


def allowed(user_id: int) -> bool:
    return not config.ALLOWED_IDS or user_id in config.ALLOWED_IDS or user_id == config.ADMIN_ID


def safe_filename(title: str) -> str:
    name = re.sub(r"[^\w\s-]", "", title, flags=re.U).strip()
    return (re.sub(r"\s+", "_", name)[:60] or "transcript")


def keyboard(video_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📄 Файлом .txt", callback_data=f"txt:{video_id}"),
                InlineKeyboardButton(text="⏱ С таймкодами", callback_data=f"tc:{video_id}"),
            ]
        ]
    )


def remember(result: Result) -> None:
    cache[result.video_id] = result
    cache.move_to_end(result.video_id)
    while len(cache) > CACHE_SIZE:
        cache.popitem(last=False)


async def send_result(message: Message, result: Result) -> None:
    text = plain_text(result)
    head = header(result)
    if len(head) + len(text) <= MAX_MESSAGE:
        await message.answer(head + text, reply_markup=keyboard(result.video_id))
        return
    file = BufferedInputFile((head + text).encode("utf-8"), filename=f"{safe_filename(result.title)}.txt")
    words = len(text.split())
    await message.answer_document(
        file,
        caption=f"{result.title}\n\n≈{words:,} слов — текст длинный, поэтому файлом.".replace(",", " "),
        reply_markup=keyboard(result.video_id),
    )


@dp.message(CommandStart())
@dp.message(Command("help"))
async def cmd_start(message: Message) -> None:
    await message.answer(START_TEXT)


@dp.message(F.text)
async def on_link(message: Message) -> None:
    user_id = message.from_user.id
    if not allowed(user_id):
        await message.answer("Бот пока работает в закрытом режиме.")
        return

    video_id = extract_video_id(message.text)
    if not video_id:
        await message.answer("Не вижу ссылки на YouTube. Пришлите ссылку вида youtube.com/watch?v=… или youtu.be/…")
        return

    if video_id in cache:
        await send_result(message, cache[video_id])
        return

    if user_id in busy_users:
        await message.answer("Ещё обрабатываю предыдущее видео — подождите немного.")
        return

    busy_users.add(user_id)
    status = await message.answer("⏳ Получаю текст…")
    try:
        async with semaphore:
            # Субтитры приходят за секунды; если их нет — предупреждаем, что будет дольше
            slow_notice = asyncio.create_task(_slow_notice(status))
            try:
                result = await asyncio.to_thread(get_text, video_id)
            finally:
                slow_notice.cancel()
        remember(result)
        await status.delete()
        await send_result(message, result)
        log.info("ok %s user=%s source=%s segs=%d", video_id, user_id, result.source, len(result.segments))
    except UserError as e:
        await status.edit_text(f"⚠️ {e}")
        log.info("user error %s: %s", video_id, e)
    except Exception:
        log.exception("failed %s", video_id)
        await status.edit_text("⚠️ Что-то пошло не так. Попробуйте ещё раз чуть позже.")
    finally:
        busy_users.discard(user_id)


async def _slow_notice(status: Message) -> None:
    await asyncio.sleep(8)
    try:
        await status.edit_text("🎧 Субтитров нет — распознаю речь. Обычно 1–3 минуты…")
    except Exception:
        pass


@dp.callback_query(F.data.startswith(("txt:", "tc:")))
async def on_button(cb: CallbackQuery) -> None:
    kind, video_id = cb.data.split(":", 1)
    result = cache.get(video_id)
    if not result:
        await cb.answer("Результат устарел — пришлите ссылку ещё раз.", show_alert=True)
        return
    if kind == "tc":
        body, suffix = timecoded_text(result), "_таймкоды"
    else:
        body, suffix = plain_text(result), ""
    file = BufferedInputFile(
        (header(result) + body).encode("utf-8"),
        filename=f"{safe_filename(result.title)}{suffix}.txt",
    )
    await cb.message.answer_document(file)
    await cb.answer()


async def main() -> None:
    if not config.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN не задан в .env")
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(link_preview_is_disabled=True))
    log.info("Whisper: %s", "on" if config.WHISPER_API_KEY else "off (only subtitles)")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
