"""Получение текста из YouTube-видео: сначала субтитры, потом распознавание речи."""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass, field

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    AgeRestricted,
    InvalidVideoId,
    IpBlocked,
    NoTranscriptFound,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
    VideoUnplayable,
)
from youtube_transcript_api.proxies import GenericProxyConfig

from . import config

ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/|embed/|v/)|youtu\.be/)([A-Za-z0-9_-]{11})"
)

CHUNK_SECONDS = 600  # куски по 10 минут для Whisper (~2.4 МБ каждый)
PREFERRED_LANGS = ["ru", "en", "uk", "kk"]


class UserError(Exception):
    """Ошибка, текст которой можно показать пользователю."""


@dataclass
class Segment:
    start: float
    text: str


@dataclass
class Result:
    video_id: str
    title: str
    source: str  # "subtitles" | "whisper"
    language: str
    segments: list[Segment] = field(default_factory=list)


def extract_video_id(text: str) -> str | None:
    m = ID_RE.search(text)
    return m.group(1) if m else None


def _proxy_url() -> str | None:
    return config.YT_PROXY


def get_title(video_id: str) -> str:
    url = (
        "https://www.youtube.com/oembed?format=json&url="
        f"https://www.youtube.com/watch?v={video_id}"
    )
    try:
        handlers = []
        if _proxy_url():
            handlers.append(urllib.request.ProxyHandler({"http": _proxy_url(), "https": _proxy_url()}))
        opener = urllib.request.build_opener(*handlers)
        with opener.open(url, timeout=10) as r:
            return json.load(r).get("title") or video_id
    except Exception:
        return video_id


# ---------- 1. Субтитры ----------

def fetch_subtitles(video_id: str) -> tuple[str, list[Segment]] | None:
    """Возвращает (язык, сегменты) или None, если субтитров нет."""
    proxy = None
    if _proxy_url():
        proxy = GenericProxyConfig(http_url=_proxy_url(), https_url=_proxy_url())
    api = YouTubeTranscriptApi(proxy_config=proxy)

    try:
        transcripts = list(api.list(video_id))
    except (TranscriptsDisabled, NoTranscriptFound):
        return None
    except (VideoUnavailable, InvalidVideoId):
        raise UserError("Видео недоступно или ссылка неверная.")
    except AgeRestricted:
        raise UserError("У видео возрастное ограничение — без входа в аккаунт YouTube его не открыть.")
    except VideoUnplayable:
        raise UserError("YouTube не даёт воспроизвести это видео (приватное, удалено или заблокировано).")
    except (RequestBlocked, IpBlocked):
        raise UserError(
            "YouTube заблокировал запрос с сервера. Нужен прокси (YT_PROXY в настройках бота)."
        )

    if not transcripts:
        return None

    # Порядок выбора: ручные на ru/en… → автосубтитры (язык оригинала) → любые ручные
    manual = [t for t in transcripts if not t.is_generated]
    generated = [t for t in transcripts if t.is_generated]
    chosen = None
    for lang in PREFERRED_LANGS:
        chosen = next((t for t in manual if t.language_code.split("-")[0] == lang), None)
        if chosen:
            break
    chosen = chosen or (generated[0] if generated else None) or manual[0]

    fetched = chosen.fetch()
    segments = [
        Segment(start=s.start, text=s.text.replace("\n", " ").strip())
        for s in fetched
        if s.text.strip()
    ]
    if not segments:
        return None
    return chosen.language_code, segments


# ---------- 2. Распознавание речи ----------

def _ydl_opts(extra: dict) -> dict:
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
    if _proxy_url():
        opts["proxy"] = _proxy_url()
    opts.update(extra)
    return opts


def get_duration(video_id: str) -> int:
    import yt_dlp

    with yt_dlp.YoutubeDL(_ydl_opts({"skip_download": True})) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    if info.get("is_live"):
        raise UserError("Это идущий прямой эфир — дождитесь окончания трансляции.")
    return int(info.get("duration") or 0)


def transcribe(video_id: str) -> tuple[str, list[Segment]]:
    import yt_dlp
    from openai import OpenAI

    if not config.WHISPER_API_KEY:
        raise UserError(
            "У этого видео нет субтитров, а распознавание речи не подключено."
        )

    duration = get_duration(video_id)
    if duration and duration > config.MAX_WHISPER_MINUTES * 60:
        raise UserError(
            f"У видео нет субтитров, а распознавать голосом можно ролики до "
            f"{config.MAX_WHISPER_MINUTES} мин. Это видео — {duration // 60} мин."
        )

    client = OpenAI(api_key=config.WHISPER_API_KEY, base_url=config.WHISPER_BASE_URL)

    with tempfile.TemporaryDirectory() as tmp:
        with yt_dlp.YoutubeDL(
            _ydl_opts({"format": "bestaudio/best", "outtmpl": os.path.join(tmp, "audio.%(ext)s")})
        ) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
        src = next(iter(glob.glob(os.path.join(tmp, "audio.*"))), None)
        if not src:
            raise UserError("Не удалось скачать звук из видео.")

        # Моно 16 кГц 32 кбит/с, нарезка по 10 минут — каждый кусок меньше лимита API в 25 МБ
        subprocess.run(
            [
                "ffmpeg", "-loglevel", "error", "-i", src,
                "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k",
                "-f", "segment", "-segment_time", str(CHUNK_SECONDS),
                os.path.join(tmp, "part%03d.mp3"),
            ],
            check=True,
        )
        parts = sorted(glob.glob(os.path.join(tmp, "part*.mp3")))

        segments: list[Segment] = []
        language = ""
        for i, part in enumerate(parts):
            offset = i * CHUNK_SECONDS
            with open(part, "rb") as f:
                try:
                    resp = client.audio.transcriptions.create(
                        model=config.WHISPER_MODEL, file=f, response_format="verbose_json"
                    )
                except Exception:
                    f.seek(0)  # некоторые модели не умеют verbose_json
                    resp = client.audio.transcriptions.create(model=config.WHISPER_MODEL, file=f)
            language = language or (getattr(resp, "language", "") or "")
            segs = getattr(resp, "segments", None)
            if segs:
                for s in segs:
                    start = s["start"] if isinstance(s, dict) else s.start
                    text = s["text"] if isinstance(s, dict) else s.text
                    if text.strip():
                        segments.append(Segment(start=offset + start, text=text.strip()))
            elif resp.text.strip():
                segments.append(Segment(start=offset, text=resp.text.strip()))

    if not segments:
        raise UserError("В видео не удалось распознать речь.")
    return language, segments


# ---------- Общая точка входа ----------

def get_text(video_id: str, allow_whisper: bool = True) -> Result:
    title = get_title(video_id)
    subs = fetch_subtitles(video_id)
    if subs:
        lang, segs = subs
        return Result(video_id, title, "subtitles", lang, segs)
    if not allow_whisper:
        raise UserError("У этого видео нет субтитров.")
    lang, segs = transcribe(video_id)
    return Result(video_id, title, "whisper", lang, segs)


# ---------- Форматирование ----------

NOISE_RE = re.compile(r"\[(?:музыка|music|аплодисменты|applause|смех|laughter)\]", re.I)


def _ts(sec: float) -> str:
    sec = int(sec)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", NOISE_RE.sub("", text)).strip()


def plain_text(result: Result, paragraph_seconds: int = 60) -> str:
    """Сплошной текст, разбитый на абзацы примерно по минуте."""
    paragraphs, current, start = [], [], 0.0
    for seg in result.segments:
        if current:
            elapsed = seg.start - start
            ends_sentence = current[-1].rstrip()[-1:] in ".!?…"
            if (elapsed >= paragraph_seconds and ends_sentence) or elapsed >= paragraph_seconds * 2:
                paragraphs.append(_clean(" ".join(current)))
                current = []
        if not current:
            start = seg.start
        current.append(seg.text)
    if current:
        paragraphs.append(_clean(" ".join(current)))
    return "\n\n".join(p for p in paragraphs if p)


def timecoded_text(result: Result, block_seconds: int = 30) -> str:
    """Текст блоками по ~30 секунд с таймкодами."""
    lines, current, start = [], [], None
    for seg in result.segments:
        if start is None:
            start = seg.start
        if current and seg.start - start >= block_seconds:
            text = _clean(" ".join(current))
            if text:
                lines.append(f"[{_ts(start)}] {text}")
            current, start = [], seg.start
        current.append(seg.text)
    if current:
        text = _clean(" ".join(current))
        if text:
            lines.append(f"[{_ts(start)}] {text}")
    return "\n".join(lines)


def header(result: Result) -> str:
    src = "субтитры YouTube" if result.source == "subtitles" else "распознавание речи"
    return (
        f"{result.title}\nhttps://youtu.be/{result.video_id}\n"
        f"Источник: {src}" + (f", язык: {result.language}" if result.language else "") + "\n\n"
    )
