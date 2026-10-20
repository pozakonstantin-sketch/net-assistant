"""Синтез речи для ответов ассистента.

Движок — нейроголоса Microsoft Edge (`edge-tts`, без ключа и регистрации).
Результат кэшируется в `data/tts` как OGG/Opus: такой файл Telegram отправляет
как голосовое сообщение, а браузер проигрывает напрямую.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import subprocess
import time
from pathlib import Path

from .config import DATA_DIR, settings

log = logging.getLogger(__name__)

CACHE_DIR = DATA_DIR / "tts"
MAX_CACHE_FILES = 300

# Имена для API/панели: понятные псевдонимы → голоса edge-tts.
VOICES: dict[str, str] = {
    "dmitry": "ru-RU-DmitryNeural",
    "svetlana": "ru-RU-SvetlanaNeural",
}
DEFAULT_VOICE = "ru-RU-DmitryNeural"

_TAG_RE = re.compile(r"<[^>]+>")
_MARK_RE = re.compile(r"[*_`~#]+")
_SPACE_RE = re.compile(r"\s+")


class TTSNotConfigured(RuntimeError):
    """Синтез отключён в конфиге."""


class TTSError(RuntimeError):
    """Не удалось синтезировать речь."""


def enabled() -> bool:
    return settings.tts_engine not in {"", "off", "none", "disabled"}


def resolve_voice(name: str | None = None) -> str:
    """Псевдоним или полное имя → голос edge-tts."""
    if not name:
        return settings.tts_voice or DEFAULT_VOICE
    name = name.strip()
    if name in VOICES:
        return VOICES[name]
    if name in VOICES.values():
        return name
    return settings.tts_voice or DEFAULT_VOICE


def clean_text(text: str) -> str:
    """Убрать разметку и служебные символы, чтобы голос не читал их вслух."""
    text = _TAG_RE.sub(" ", text)
    text = _MARK_RE.sub(" ", text)
    text = text.replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = _SPACE_RE.sub(" ", text).strip()
    return text


def _cache_paths(text: str, voice: str) -> tuple[Path, Path]:
    key = hashlib.sha1(f"{voice}|{settings.tts_rate}|{text}".encode("utf-8")).hexdigest()[:20]
    return CACHE_DIR / f"{key}.mp3", CACHE_DIR / f"{key}.ogg"


async def _to_ogg(source: Path, target: Path) -> None:
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source), "-c:a", "libopus", "-b:a", "48k",
        "-ar", "48000", "-ac", "1", str(target),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0 or not target.exists():
        raise TTSError(f"ffmpeg: {(stderr or b'').decode('utf-8', 'ignore')[:300]}")


def cleanup() -> None:
    """Оставить только свежие файлы кэша."""
    files = sorted(CACHE_DIR.glob("*.ogg"), key=lambda item: item.stat().st_mtime, reverse=True)
    for path in files[MAX_CACHE_FILES:]:
        for suffix in (".ogg", ".mp3"):
            try:
                path.with_suffix(suffix).unlink(missing_ok=True)
            except OSError:
                pass


async def synthesize(text: str, voice: str | None = None) -> Path:
    """Синтезировать текст и вернуть путь к OGG/Opus."""
    if not enabled():
        raise TTSNotConfigured("синтез речи отключён (TTS_ENGINE=off)")
    text = clean_text(text)
    if not text:
        raise ValueError("пустой текст")
    if len(text) > settings.tts_max_chars:
        text = text[: settings.tts_max_chars]

    speaker = resolve_voice(voice)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    mp3, ogg = _cache_paths(text, speaker)
    if ogg.exists() and ogg.stat().st_size > 0:
        return ogg

    try:
        import edge_tts
    except ImportError as exc:  # pragma: no cover
        raise TTSNotConfigured("edge-tts не установлен (pip install edge-tts)") from exc

    started = time.monotonic()
    try:
        await edge_tts.Communicate(
            text, speaker, rate=settings.tts_rate or "+0%"
        ).save(str(mp3))
        await _to_ogg(mp3, ogg)
    except Exception as exc:  # noqa: BLE001
        raise TTSError(f"синтез не удался: {exc}") from exc
    finally:
        mp3.unlink(missing_ok=True)

    log.info("озвучка: %d символов, %.1f с, %s", len(text), time.monotonic() - started, speaker)
    cleanup()
    return ogg


def info() -> dict:
    return {
        "engine": settings.tts_engine,
        "voice": resolve_voice(),
        "rate": settings.tts_rate,
        "voices": [{"id": key, "voice": value} for key, value in VOICES.items()],
        "enabled": enabled(),
    }
