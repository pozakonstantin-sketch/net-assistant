"""Виртуальное устройство для лаборатории: проверяет всю цепочку без риска.

Регистрируется в ядре как обычное устройство и отдаёт синтетические данные,
включая опасные действия — их можно безопасно тестировать.

    set FAKE_KIND=router   && python -m agents.fake_agent      (Windows)
    FAKE_KIND=android python -m agents.fake_agent              (Linux)

FAKE_KIND: pc | android | router (по умолчанию pc)
"""

from __future__ import annotations

import base64
import io
import os
import platform

from agents.common import AgentClient

FAKE_KIND = os.getenv("FAKE_KIND", "pc").strip().lower()
FAKE_ID = os.getenv("FAKE_DEVICE_ID", f"fake-{FAKE_KIND}")


def _fake_image() -> dict | None:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    image = Image.new("RGB", (640, 360), "#101820")
    draw = ImageDraw.Draw(image)
    draw.rectangle([20, 20, 620, 340], outline="#39d353", width=3)
    draw.text((40, 40), f"FAKE SCREENSHOT\n{FAKE_ID}\n{platform.platform()}", fill="#39d353")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return {"image_b64": base64.b64encode(buffer.getvalue()).decode("ascii"),
            "width": image.width, "height": image.height, "bytes": len(buffer.getvalue())}


# ── pc ───────────────────────────────────────────────────────────────

PC_HANDLERS = {
    "sys.info": lambda args: {
        "host": f"{FAKE_ID}-host", "os": "Fake OS 1.0 (лаборатория)",
        "cpu_count": 8, "uptime_hours": 3.5, "memory": {"total_gb": 32.0,
                                                        "available_gb": 19.4, "used_percent": 39},
    },
    "sys.screenshot": lambda args: _fake_image() or {"note": "Pillow не установлен"},
    "sys.lock": lambda args: {"locked": True, "note": "заглушка: экран не блокировался"},
    "proc.list": lambda args: {
        "sort": args.get("sort", "memory"),
        "processes": [
            {"name": "chrome.exe", "pid": "1234", "memory_mb": 812.5},
            {"name": "python.exe", "pid": "4321", "memory_mb": 145.2},
            {"name": "explorer.exe", "pid": "777", "memory_mb": 98.0},
        ][: int(args.get("limit", 3))],
    },
    "shell.run": lambda args: {"command": args["command"], "exit_code": 0,
                               "stdout": f"это заглушка, реальная команда «{args['command']}» не выполнялась"},
    "fs.list": lambda args: {"path": args["path"], "entries": [
        {"name": "Отчёты", "type": "dir"}, {"name": "note.txt", "type": "file"}]},
    "fs.read": lambda args: {"path": args["path"], "text": "заглушка: содержимое файла"},
    "media.volume": lambda args: {"level_requested": args.get("level"), "approximate": True},
    "net.status": lambda args: {"host": FAKE_ID, "addresses": ["192.0.2.10"],
                                "internet": True, "ping_sample": ["rtt min/avg = 12/14 ms"]},
}

# ── android ──────────────────────────────────────────────────────────

ANDROID_HANDLERS = {
    "sys.info": lambda args: {"device": {"model": "Fake Phone", "release": "15"}},
    "net.status": lambda args: {"internet": True},
    "shell.run": lambda args: {"command": args["command"], "exit_code": 0, "stdout": "заглушка"},
    "fs.list": lambda args: {"path": args["path"], "entries": [{"name": "Download", "type": "dir"}]},
    "fs.read": lambda args: {"path": args["path"], "text": "заглушка"},
    "battery.status": lambda args: {"percentage": 74, "plugged": "UNPLUGGED", "temperature": 29.5},
    "sms.list": lambda args: {"count": 1, "messages": [
        {"number": "+7 900 000-00-00", "body": "заглушка SMS", "received": "2026-10-18T10:00:00"}]},
    "sms.send": lambda args: {"sent": True, "to": args["number"], "note": "заглушка: SMS не отправлялось"},
    "notify.send": lambda args: {"shown": True, "title": args["title"]},
    "tts.speak": lambda args: {"spoken": True},
    "torch.set": lambda args: {"torch": args["on"]},
    "wifi.info": lambda args: {"ssid": "FakeWiFi", "ip": "192.0.2.20", "link_speed": "433 Mbps"},
    "location.get": lambda args: {"latitude": 55.75, "longitude": 37.62, "accuracy": 25},
    "app.open": lambda args: {"launched": args["package"], "note": "заглушка"},
}

# ── router ───────────────────────────────────────────────────────────

ROUTER_HANDLERS = {
    "net.status": lambda args: {"host": FAKE_ID, "kind": "fake", "uptime": "5 days",
                                "board": "Fake Router 3000", "wan": "up, 100 Mbps"},
    "router.clients": lambda args: {"host": FAKE_ID, "raw": "AA:BB:CC:DD:EE:01  phone  -48dBm\n"
                                                          "AA:BB:CC:DD:EE:02  laptop -61dBm"},
    "router.wifi": lambda args: {"wifi_enabled": args["enabled"], "note": "заглушка"},
    "router.kick": lambda args: {"mac": args["mac"], "block": args.get("block", True),
                                 "output": "заглушка: клиент не отключался"},
    "router.reboot": lambda args: {"rebooting": True, "note": "заглушка: роутер не перезагружался"},
}

TABLES = {"pc": PC_HANDLERS, "android": ANDROID_HANDLERS, "router": ROUTER_HANDLERS}

# имя платформы для каталога команд (в каталоге ПК называется pc-desktop)
PLATFORMS = {"pc": "pc-desktop", "android": "android", "router": "router"}


def main() -> None:
    handlers = TABLES.get(FAKE_KIND)
    if handlers is None:
        raise SystemExit(f"FAKE_KIND должен быть одним из: {', '.join(TABLES)}")
    client = AgentClient(
        device_id=FAKE_ID,
        name=f"Тестовое устройство ({FAKE_KIND})",
        platform=PLATFORMS[FAKE_KIND],
        handlers=handlers,
    )
    client.run()


if __name__ == "__main__":
    main()
