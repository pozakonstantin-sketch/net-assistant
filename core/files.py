"""Сохранение артефактов (скриншоты и прочее) на стороне ядра."""

from __future__ import annotations

import base64
import binascii
import secrets
import time
from pathlib import Path

from .config import DATA_DIR

SHOTS_DIR = DATA_DIR / "shots"
MAX_IMAGE_BYTES = 8 * 1024 * 1024

# каталог нужен уже на этапе импорта: ядро монтирует его как статику
SHOTS_DIR.mkdir(parents=True, exist_ok=True)


def store_image(b64: str, prefix: str = "shot") -> dict:
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"некорректный base64: {exc}") from exc
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("изображение слишком большое")

    name = f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}.png"
    path = SHOTS_DIR / name
    path.write_bytes(raw)
    return {"file": str(path), "url": f"/shots/{name}", "bytes": len(raw)}


def attach_artifacts(result):
    """Вынести картинку из ответа агента в файл, оставив ссылку."""
    if isinstance(result, dict) and result.get("image_b64"):
        result = dict(result)
        payload = result.pop("image_b64")
        try:
            result.update(store_image(payload))
        except ValueError as exc:
            result["artifact_error"] = str(exc)
    return result
