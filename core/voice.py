"""Распознавание речи: облачный Whisper или локальный faster-whisper.

STT_ENGINE=auto    — облако, если есть ключ, иначе локальная модель
STT_ENGINE=openai  — только облако (OpenAI-совместимый /audio/transcriptions)
STT_ENGINE=local   — только локально (pip install faster-whisper)
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx

from .config import settings

log = logging.getLogger("voice")


class VoiceNotConfigured(RuntimeError):
    """Нет ни облачного ключа, ни локальной модели."""


def _cloud_key() -> str:
    # отдельный ключ необязателен: годится ключ LLM, если провайдер умеет аудио
    return settings.stt_api_key or settings.llm_api_key


def _local_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def engine() -> str:
    if settings.stt_engine in {"openai", "local"}:
        return settings.stt_engine
    if _cloud_key():
        return "openai"
    if _local_available():
        return "local"
    return "none"


async def transcribe(data: bytes, filename: str, content_type: str = "") -> dict:
    if not data:
        raise ValueError("пустой аудиофайл")
    limit = settings.stt_max_mb * 1024 * 1024
    if len(data) > limit:
        raise ValueError(f"файл больше {settings.stt_max_mb} МБ")

    chosen = engine()
    if chosen == "openai":
        text = await _cloud(data, filename, content_type)
    elif chosen == "local":
        text = await _local(data)
    else:
        raise VoiceNotConfigured(
            "распознавание речи не настроено: задайте STT_API_KEY "
            "или установите faster-whisper (pip install faster-whisper)"
        )

    return {"text": text.strip(), "engine": chosen, "bytes": len(data)}


# ───────────────────────────── облако ─────────────────────────────


async def _cloud(data: bytes, filename: str, content_type: str) -> str:
    key = _cloud_key()
    if not key:
        raise VoiceNotConfigured("не задан STT_API_KEY")

    url = f"{settings.stt_base_url}/audio/transcriptions"
    files = {"file": (filename or "audio.webm", data, content_type or "application/octet-stream")}
    payload = {"model": settings.stt_model}
    if settings.stt_language:
        payload["language"] = settings.stt_language

    headers = {"Authorization": f"Bearer {key}"}
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(url, data=payload, files=files, headers=headers)
        if response.status_code >= 400:
            raise RuntimeError(f"STT ответил {response.status_code}: {response.text[:300]}")
        return response.json().get("text", "")


# ───────────────────────────── локально ─────────────────────────────

_models: dict[str, object] = {}


def _load_model(size: str):
    if size in _models:
        return _models[size]
    from faster_whisper import WhisperModel

    log.info("загружаю локальную модель Whisper «%s» (первый запуск долгий)", size)
    model = WhisperModel(size, device=os.getenv("STT_DEVICE", "cpu"),
                         compute_type=os.getenv("STT_COMPUTE", "int8"))
    _models[size] = model
    return model


def _transcribe_sync(data: bytes) -> str:
    import tempfile

    size = os.getenv("STT_LOCAL_MODEL", "small")
    model = _load_model(size)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
        handle.write(data)
        path = handle.name
    try:
        segments, _info = model.transcribe(path, language=settings.stt_language or None,
                                           vad_filter=True)
        return " ".join(segment.text.strip() for segment in segments)
    finally:
        os.unlink(path)


async def _local(data: bytes) -> str:
    if not _local_available():
        raise VoiceNotConfigured("faster-whisper не установлен")
    return await asyncio.to_thread(_transcribe_sync, data)
