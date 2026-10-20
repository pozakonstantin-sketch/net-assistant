"""Карта элементов панели.

Браузер присылает координаты кнопок и карточек (getBoundingClientRect), чтобы
ИИ не угадывал пиксели по снимку экрана, а брал точные значения. Живёт в памяти:
это подсказка для планировщика, а не данные для хранения.
"""

from __future__ import annotations

import time

MAP: dict = {"at": 0.0, "screen": "", "targets": []}
MAX_TARGETS = 120
MAX_LABEL = 80


def store(payload: dict) -> None:
    global MAP
    targets = []
    raw = payload.get("targets")
    if isinstance(raw, list):
        for item in raw[:MAX_TARGETS]:
            if not isinstance(item, dict):
                continue
            try:
                x = int(item["x"])
                y = int(item["y"])
            except (KeyError, TypeError, ValueError):
                continue
            label = str(item.get("label") or "").strip()[:MAX_LABEL]
            if not label:
                continue
            targets.append({"label": label, "x": x, "y": y})
    MAP = {
        "at": time.time(),
        "screen": str(payload.get("screen") or "")[:32],
        "width": int(payload.get("width") or 0),
        "height": int(payload.get("height") or 0),
        "targets": targets,
    }


def current(max_age: float = 180.0) -> dict:
    if time.time() - MAP["at"] > max_age:
        return {}
    return MAP


def as_text(max_items: int = 40) -> str:
    data = current()
    if not data or not data["targets"]:
        return ""
    lines = [f"{item['label']} — x={item['x']}, y={item['y']}" for item in data["targets"][:max_items]]
    return "\n".join(lines)
