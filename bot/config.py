import os

from dotenv import load_dotenv

load_dotenv()


def _ids(value: str) -> set[int]:
    return {int(x) for x in value.replace(" ", "").split(",") if x}


BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)

# Пусто = бот открыт для всех. Иначе — список Telegram ID через запятую.
ALLOWED_IDS = _ids(os.getenv("ALLOWED_IDS", ""))

# Прокси для запросов к YouTube (если сервер заблокирован), напр. http://user:pass@host:port
YT_PROXY = os.getenv("YT_PROXY", "") or None

# Распознавание речи (Whisper) — запасной путь, когда у видео нет субтитров.
# Работает с любым OpenAI-совместимым API: OpenAI, Groq, ProxyAPI и т.п.
WHISPER_API_KEY = os.getenv("WHISPER_API_KEY", "")
WHISPER_BASE_URL = os.getenv("WHISPER_BASE_URL", "") or None
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "whisper-1")

# Максимальная длина видео для распознавания речи, минуты (субтитры — без ограничений)
MAX_WHISPER_MINUTES = int(os.getenv("MAX_WHISPER_MINUTES", "90"))

# Сколько видео обрабатывать одновременно
MAX_PARALLEL = int(os.getenv("MAX_PARALLEL", "2"))
