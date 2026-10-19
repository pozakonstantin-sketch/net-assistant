"""Конфигурация ядра, читается из .env / переменных окружения."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:  # необязательная зависимость
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "да"}


def _csv(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    token: str = os.getenv("ASSISTANT_TOKEN", "change-me")
    host: str = os.getenv("CORE_HOST", "127.0.0.1")
    port: int = int(os.getenv("CORE_PORT", "8787"))

    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "deepseek-chat")
    llm_timeout: float = float(os.getenv("LLM_TIMEOUT", "60"))

    # Планировщик на бесплатных моделях OpenCode (CLI внутри этой же машины).
    # backend: auto | opencode | openai
    llm_backend: str = os.getenv("LLM_BACKEND", "auto").strip().lower()
    opencode_bin: str = os.getenv("OPENCODE_BIN", "opencode")
    opencode_model: str = os.getenv("OPENCODE_MODEL", "opencode/deepseek-v4.1-flash-free")
    # ВАЖНО: пользовательские агенты шлюз OpenCode для бесплатного тарифа отклоняет (403),
    # поэтому по умолчанию агент не задаём и отключаем инструменты конфигом рабочей папки.
    opencode_agent: str = os.getenv("OPENCODE_AGENT", "")
    opencode_timeout: float = float(os.getenv("OPENCODE_TIMEOUT", "150"))
    opencode_dir: str = os.getenv("OPENCODE_DIR", "")

    require_confirm: bool = _bool("REQUIRE_CONFIRM", True)
    allowed_devices: tuple[str, ...] = tuple(_csv("ALLOWED_DEVICES"))

    # Режим доступа агентов:
    #   restricted — только белые списки команд и разрешённые каталоги (по умолчанию);
    #   full       — произвольные команды оболочки, запись/удаление файлов, процессы.
    # В full агент получает права того пользователя, от которого запущен.
    access_mode: str = os.getenv("ACCESS_MODE", "restricted").strip().lower()

    # Распознавание речи. engine: auto | openai | local
    stt_engine: str = os.getenv("STT_ENGINE", "auto").strip().lower()
    stt_base_url: str = os.getenv("STT_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    stt_api_key: str = os.getenv("STT_API_KEY", "")
    stt_model: str = os.getenv("STT_MODEL", "whisper-1")
    stt_language: str = os.getenv("STT_LANGUAGE", "ru")
    stt_max_mb: int = int(os.getenv("STT_MAX_MB", "20"))

    command_timeout: float = float(os.getenv("COMMAND_TIMEOUT", "30"))
    audit_path: Path = DATA_DIR / "audit.jsonl"


settings = Settings()
