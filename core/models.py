"""Выбор модели планировщика OpenCode.

Бесплатные модели OpenCode перечисляются командой `opencode models`. Список
кэшируется на диск, а выбранная модель хранится отдельным файлом — так её можно
менять на ходу (Telegram, панель, API) без правки .env и перезапуска ядра.

Приоритет: файл состояния → OPENCODE_MODEL из .env.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from .config import DATA_DIR, settings

log = logging.getLogger("models")

STATE_PATH = DATA_DIR / "llm_model.txt"
CACHE_PATH = DATA_DIR / "opencode_models.json"
CACHE_TTL = 900.0  # 15 минут: список моделей меняется редко
LIST_TIMEOUT = 60.0

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
MODEL_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._:-]+$")


# ─────────────────────────── выбранная модель ───────────────────────────


def _read_state() -> str | None:
    try:
        value = STATE_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def current() -> str:
    """Модель, которой сейчас пользуется планировщик."""
    return _read_state() or settings.opencode_model


def set_current(model: str) -> str:
    """Запомнить выбранную модель. Возвращает её же."""
    model = model.strip()
    if not MODEL_RE.match(model):
        raise ValueError(f"не похоже на имя модели: {model!r} (ожидается вид «провайдер/модель»)")

    known = available(refresh=False)
    if known and model not in known:
        raise ValueError(f"модель «{model}» не найдена. Доступны: {', '.join(known)}")

    from . import planner  # локальный импорт: избегаем цикла модулей

    if planner.binary_path() is None:
        raise RuntimeError("CLI opencode не найден — переключение недоступно")

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(model + "\n", encoding="utf-8")
    log.info("модель планировщика переключена на %s", model)
    return model


def reset() -> str:
    """Вернуться к модели из .env."""
    try:
        STATE_PATH.unlink()
    except OSError:
        pass
    return current()


# ─────────────────────────── список моделей ───────────────────────────


def _binary() -> str | None:
    from . import planner

    binary = planner.binary_path()
    if binary:
        return binary
    return shutil.which("opencode")


def _parse(text: str) -> list[str]:
    found: list[str] = []
    for line in ANSI_RE.sub("", text).splitlines():
        candidate = line.strip()
        # строка вида «opencode/deepseek-v4.1-flash-free», возможно с пометкой
        token = candidate.split()[0] if candidate else ""
        if token and MODEL_RE.match(token) and token not in found:
            found.append(token)
    return found


def _read_cache(allow_stale: bool) -> list[str]:
    try:
        payload = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    models = payload.get("models") or []
    if not isinstance(models, list):
        return []
    age = time.time() - float(payload.get("time") or 0)
    if not allow_stale and age > CACHE_TTL:
        return []
    return [str(m) for m in models]


def _write_cache(models: list[str]) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(
            json.dumps({"time": time.time(), "models": models}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:  # кэш — не критичная вещь
        log.warning("не удалось сохранить кэш моделей: %s", exc)


def _query() -> list[str]:
    binary = _binary()
    if binary is None:
        return []
    try:
        process = subprocess.run(
            [binary, "models"],
            capture_output=True,
            timeout=LIST_TIMEOUT,
            stdin=subprocess.DEVNULL,
            env=dict(os.environ),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("не удалось получить список моделей: %s", exc)
        return []
    return _parse(process.stdout.decode("utf-8", "replace"))


def available(refresh: bool = False) -> list[str]:
    """Список моделей. Сеть дёргаем только при refresh или пустом/устаревшем кэше."""
    if not refresh:
        cached = _read_cache(allow_stale=False)
        if cached:
            return cached

    models = _query()
    if models:
        _write_cache(models)
        return models

    # запрос не удался — отдаём устаревший кэш, если он есть
    stale = _read_cache(allow_stale=True)
    if stale:
        log.warning("список моделей взят из устаревшего кэша (%d шт.)", len(stale))
    return stale


def info() -> dict:
    """Состояние для API и панели."""
    models = available()
    active = current()
    if models and active not in models:
        models = sorted([*models, active])
    return {
        "current": active,
        "models": models,
        "default": settings.opencode_model,
        "custom": bool(_read_state()),
        "backend": settings.llm_backend,
    }
