"""Постоянная история диалога с ИИ.

Каждая сессия — отдельный JSONL-файл в ``data/history``. Формат строки:

    {"role": "user", "text": "…", "ts": 1760880000.0}
    {"role": "assistant", "text": "…", "ts": …, "executed": […], "pending": […]}

Зачем файл, а не память: панель и Telegram-бот должны показывать переписку
после перезапуска ядра. Контекст для LLM (последние реплики) собирается из
этой же истории, поэтому отдельное хранилище не нужно.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path

from .config import DATA_DIR

log = logging.getLogger("history")

HISTORY_DIR = DATA_DIR / "history"
MAX_ENTRIES = 400          # сколько реплик храним на сессию
MAX_FILE_BYTES = 512 * 1024  # после этого файл переписывается (ротация)
MAX_TEXT = 20_000          # одна реплика
MAX_FIELD = 4_000          # одно строковое поле внутри результата команды
LLM_LIMIT = 24             # сколько реплик отдаём модели

_lock = threading.RLock()


def _safe(session: str) -> str:
    """Имя файла без выхода из каталога и без спецсимволов."""
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "_", (session or "default").strip())[:64]
    clean = clean.strip("._")
    return clean or "default"


def _path(session: str) -> Path:
    return HISTORY_DIR / f"{_safe(session)}.jsonl"


def _clip(text: object) -> str:
    value = "" if text is None else str(text)
    return value if len(value) <= MAX_TEXT else value[:MAX_TEXT] + "…"


def _shrink(value: object, depth: int = 0) -> object:
    """Обрезаем длинные строки в результатах команд, чтобы файл не пух."""
    if depth > 6:
        return "…"
    if isinstance(value, str):
        return value if len(value) <= MAX_FIELD else value[:MAX_FIELD] + "…"
    if isinstance(value, dict):
        return {str(key): _shrink(item, depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_shrink(item, depth + 1) for item in list(value)[:50]]
    return value


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:  # noqa: BLE001 - битый/недоступный файл не должен ронять ядро
        log.warning("не удалось прочитать %s: %s", path.name, exc)
        return []
    items: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("role") and "text" in item:
            items.append(item)
    return items


def _write(path: Path, items: list[dict]) -> None:
    tmp = path.with_suffix(".tmp")
    payload = "".join(
        json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n"
        for item in items
    )
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def entries(session: str, limit: int | None = None) -> list[dict]:
    """Все сохранённые реплики сессии (или последние ``limit``)."""
    with _lock:
        items = _read(_path(session))
    if limit and limit > 0:
        items = items[-limit:]
    return items


def append(session: str, role: str, text: str,
           executed: list | None = None, pending: list | None = None) -> dict:
    """Добавить реплику и вернуть её в том же виде, в каком она хранится."""
    item: dict = {"role": role, "text": _clip(text), "ts": time.time()}
    if executed:
        item["executed"] = _shrink(executed)
    if pending:
        item["pending"] = _shrink(pending)
    line = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
    path = _path(session)
    with _lock:
        try:
            HISTORY_DIR.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            if path.stat().st_size > MAX_FILE_BYTES:
                _write(path, _read(path)[-MAX_ENTRIES:])
        except OSError as exc:  # noqa: BLE001 - история не должна ломать диалог
            log.warning("не удалось сохранить историю %s: %s", path.name, exc)
    return item


def clear(session: str) -> bool:
    """Удалить историю сессии. True, если файл был."""
    path = _path(session)
    with _lock:
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            log.warning("не удалось удалить %s: %s", path.name, exc)
            return False


def llm_messages(session: str, limit: int = LLM_LIMIT) -> list[dict]:
    """Контекст для модели: только user/assistant, начиная с реплики пользователя."""
    messages = [
        {"role": item["role"], "content": item["text"]}
        for item in entries(session)
        if item.get("role") in {"user", "assistant"} and item.get("text")
    ]
    if len(messages) > limit:
        cut = len(messages) - limit
        while cut < len(messages) and messages[cut]["role"] != "user":
            cut += 1
        messages = messages[cut:]
    return messages


def sessions() -> list[dict]:
    """Краткий список сессий — пригодится панели и диагностике."""
    result: list[dict] = []
    with _lock:
        if not HISTORY_DIR.exists():
            return result
        for path in sorted(HISTORY_DIR.glob("*.jsonl")):
            items = _read(path)
            if not items:
                continue
            result.append({
                "session": path.stem,
                "messages": len(items),
                "updated": items[-1].get("ts", 0),
                "preview": str(items[-1].get("text", ""))[:120],
            })
    return result
