"""Журнал аудита: каждая команда и её результат пишутся в JSONL."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from .config import settings


class AuditLog:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or settings.audit_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, **fields: Any) -> None:
        record = {"ts": time.time(), "iso": time.strftime("%Y-%m-%d %H:%M:%S"), **fields}
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def tail(self, limit: int = 50) -> list[dict]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()[-limit:]
        records: list[dict] = []
        for line in lines:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return list(reversed(records))


audit = AuditLog()
