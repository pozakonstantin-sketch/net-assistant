"""Агент Android без root — на базе Termux + Termux:API.

Установка на телефоне (однократно):
    1. F-Droid: Termux и Termux:API (оба из F-Droid, не из Play).
    2. В Termux:  pkg update && pkg install python termux-api openssh
    3. Разрешения Termux:API: телефон, SMS, местоположение, уведомления, камера.
    4. Скопировать проект на телефон, затем:  python -m agents.android_agent

Root не нужен: всё, что умеет Termux:API, доступно обычному пользователю.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import shlex
import subprocess
from pathlib import Path

from agents.common import AgentClient

log = logging.getLogger("android-agent")

TIMEOUT = 20
MAX_OUTPUT = 32_000

ALLOWED_SHELL = {
    "ls", "cat", "pwd", "df", "du", "free", "uname", "uptime", "date", "whoami",
    "ip", "ifconfig", "ping", "termux-battery-status", "termux-wifi-connectioninfo",
    "termux-notification", "termux-toast", "termux-tts-speak", "termux-torch",
    "termux-sms-list", "getprop", "dumpsys", "ps", "top", "sensors", "logcat",
}


def guard_path(raw_path: str) -> Path:
    """Разрешаем только домашний каталог Termux и общие хранилища."""
    home = Path(os.path.expanduser("~")).resolve()
    target = Path(raw_path).expanduser()
    if not target.is_absolute():
        target = home / target
    target = target.resolve()
    allowed = [home, Path("/sdcard"), Path("/storage/emulated/0"), Path("/data/data/com.termux")]
    if not any(target == root or root in target.parents for root in allowed):
        raise PermissionError(f"путь вне разрешённых: {target}")
    return target


def termux(*args: str, timeout: int = TIMEOUT) -> str:
    """Вызвать termux-* утилиту и вернуть её вывод."""
    completed = subprocess.run(
        ["termux-" + args[0], *args[1:]] if not args[0].startswith("termux-") else list(args),
        capture_output=True, text=True, timeout=timeout, errors="ignore",
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"{args[0]} вернул код {completed.returncode}: "
            f"{completed.stderr.strip() or completed.stdout.strip() or 'нет вывода'}"
        )
    return completed.stdout.strip()


def termux_json(*args: str) -> object:
    output = termux(*args)
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return {"raw": output}


# ───────────────────────────── команды ─────────────────────────────


def sys_info(_args: dict) -> dict:
    properties = {}
    for key in ("ro.product.model", "ro.product.manufacturer", "ro.build.version.release",
                "ro.build.version.sdk", "ro.product.cpu.abi"):
        try:
            properties[key.split(".")[-1]] = termux("getprop", key)
        except Exception:  # noqa: BLE001
            continue
    return {"device": properties, "termux_prefix": os.environ.get("PREFIX", ""),
            "home": os.path.expanduser("~")}


def battery_status(_args: dict) -> dict:
    return termux_json("battery-status")


def sms_list(args: dict) -> dict:
    limit = int(args.get("limit", 5))
    messages = termux_json("sms-list", "-l", str(limit))
    if isinstance(messages, list):
        trimmed = [
            {"number": m.get("number"), "received": m.get("received"),
             "body": (m.get("body") or "")[:160]}
            for m in messages
        ]
        return {"count": len(trimmed), "messages": trimmed}
    return {"messages": messages}


def sms_send(args: dict) -> dict:
    termux("sms-send", "-n", str(args["number"]), str(args["text"]))
    return {"sent": True, "to": args["number"], "chars": len(str(args["text"]))}


def notify_send(args: dict) -> dict:
    termux("notification", "--title", str(args["title"]), "--content", str(args["text"]))
    return {"shown": True}


def tts_speak(args: dict) -> dict:
    termux("tts-speak", str(args["text"]))
    return {"spoken": True, "chars": len(str(args["text"]))}


def torch_set(args: dict) -> dict:
    termux("torch", "on" if args["on"] else "off")
    return {"torch": bool(args["on"])}


def wifi_info(_args: dict) -> dict:
    return termux_json("wifi-connectioninfo")


def location_get(_args: dict) -> dict:
    return termux_json("location", "-p", "network")


def app_open(args: dict) -> dict:
    """Запуск приложения. Сначала пробуем явную активность, затем monkey."""
    package = _package(args["package"])
    if not _run("pm", "path", package).stdout.strip():
        raise ValueError(f"приложение {package} не установлено")

    resolved = _run(
        "cmd", "package", "resolve-activity", "--brief",
        "-c", "android.intent.category.LAUNCHER", package,
    ).stdout
    component = ""
    for line in resolved.splitlines():
        line = line.strip()
        if "/" in line and not line.startswith(("priority", "No activity")):
            component = line

    if component:
        completed = _run("am", "start", "-n", component)
    else:
        completed = _run("monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1")

    output = (completed.stdout + completed.stderr).strip()
    if "Error" in output or "No activities found" in output:
        raise RuntimeError(output[:400] or f"не удалось запустить {package}")
    return {"launched": package, "component": component or None}


# ───────────────────────── приложения и экран ─────────────────────────


def _package(raw: object) -> str:
    package = str(raw).strip()
    if not re.fullmatch(r"[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)+", package):
        raise ValueError(f"недопустимый пакет приложения: {package!r}")
    return package


def _run(*command: str, timeout: int = TIMEOUT, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(list(command), capture_output=True, text=True,
                          timeout=timeout, errors="ignore", env=env)


def _system_env() -> dict:
    """Окружение без Termux-библиотек.

    Системные бинарники Android (screencap, uiautomator) собраны под системный
    bionic. Если оставить LD_LIBRARY_PATH из Termux, они подхватывают
    несовместимые библиотеки и падают с «cannot locate symbol ...».
    """
    env = {
        "PATH": "/system/bin:/system/xbin",
        "ANDROID_ROOT": os.environ.get("ANDROID_ROOT", "/system"),
        "ANDROID_DATA": os.environ.get("ANDROID_DATA", "/data"),
        "HOME": os.environ.get("HOME", "/data/data/com.termux/files/home"),
    }
    for key in ("TMPDIR", "EXTERNAL_STORAGE"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def apps_list(args: dict) -> dict:
    third_party = bool(args.get("third_party", True))
    limit = int(args.get("limit", 100))
    flag = "-3" if third_party else "-e"
    output = _run("pm", "list", "packages", flag).stdout
    packages = sorted({
        line.split(":", 1)[1].strip()
        for line in output.splitlines()
        if line.startswith("package:")
    })
    return {
        "count": len(packages),
        "packages": packages[:limit],
        "truncated": len(packages) > limit,
        "filter": "third_party" if third_party else "enabled",
    }


def app_info(args: dict) -> dict:
    package = _package(args["package"])
    apk = _run("pm", "path", package).stdout.strip()
    if not apk:
        return {"package": package, "installed": False}

    dump = _run("dumpsys", "package", package).stdout
    details = {}
    for key in ("versionName", "versionCode", "firstInstallTime", "lastUpdateTime", "minSdk"):
        match = re.search(rf"\b{key}=([^\s]+)", dump)
        if match:
            details[key] = match.group(1)
    return {"package": package, "installed": True,
            "apk": apk.splitlines()[0], **details}


def app_stop(args: dict) -> dict:
    package = _package(args["package"])
    completed = _run("am", "force-stop", package)
    return {"stopped": package, "exit_code": completed.returncode,
            "stderr": completed.stderr.strip()[:300]}


def app_current(_args: dict) -> dict:
    for command in (("dumpsys", "activity", "activities"), ("dumpsys", "window", "windows")):
        output = _run(*command).stdout
        match = re.search(r"(?:mResumedActivity|mCurrentFocus)[^\n]*?"
                          r"([A-Za-z0-9_.]+/[A-Za-z0-9_.$]+)", output)
        if match:
            component = match.group(1)
            return {"package": component.split("/")[0], "activity": component}
    return {"package": None, "activity": None,
            "note": "не удалось определить активность (Android 12+ может скрывать её)"}


def screen_shot(_args: dict) -> dict:
    tmp = Path(os.environ.get("TMPDIR", "/data/data/com.termux/files/usr/tmp")) / "screen.png"
    completed = _run("/system/bin/screencap", "-p", str(tmp), env=_system_env())
    if completed.returncode != 0 or not tmp.exists():
        detail = (completed.stderr or completed.stdout).strip()
        # Android 16 ругается на несколько дисплеев, но снимок всё равно делает —
        # поэтому проверяем только наличие файла.
        if not tmp.exists():
            raise RuntimeError(detail[:300] or "screencap не сработал")
    raw = tmp.read_bytes()
    tmp.unlink(missing_ok=True)
    return {"image_b64": base64.b64encode(raw).decode("ascii"),
            "bytes": len(raw), "format": "png"}


def ui_dump(args: dict) -> dict:
    tmp = Path(os.environ.get("TMPDIR", "/data/data/com.termux/files/usr/tmp")) / "window.xml"
    completed = _run("/system/bin/uiautomator", "dump", "--compressed", str(tmp),
                     timeout=30, env=_system_env())
    if completed.returncode != 0 or not tmp.exists():
        detail = (completed.stderr or completed.stdout).strip()[:300]
        raise RuntimeError(
            "uiautomator не смог снять дерево интерфейса. " + (detail or
            "На Android 12+ это часто требует отладки по USB/adb.")
        )
    xml = tmp.read_text(errors="ignore")
    tmp.unlink(missing_ok=True)

    if not args.get("compact", True):
        return {"xml": xml[:20000]}

    elements = []
    for node in re.findall(r"<node[^>]*>", xml):
        text = re.search(r'text="([^"]*)"', node)
        desc = re.search(r'content-desc="([^"]*)"', node)
        bounds = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', node)
        label = (text.group(1) if text else "") or (desc.group(1) if desc else "")
        if not label or not bounds:
            continue
        x1, y1, x2, y2 = (int(value) for value in bounds.groups())
        elements.append({"text": label[:80], "center": [(x1 + x2) // 2, (y1 + y2) // 2]})
    return {"count": len(elements), "elements": elements[:60]}


def screen_tap(args: dict) -> dict:
    x, y = int(args["x"]), int(args["y"])
    completed = _run("input", "tap", str(x), str(y))
    return {"tapped": [x, y], "exit_code": completed.returncode}


def screen_swipe(args: dict) -> dict:
    duration = int(args.get("duration_ms", 300))
    values = [str(int(args[key])) for key in ("x1", "y1", "x2", "y2")]
    completed = _run("input", "swipe", *values, str(duration))
    return {"swipe": values, "duration_ms": duration, "exit_code": completed.returncode}


def screen_text(args: dict) -> dict:
    text = str(args["text"])
    if not text.isascii():
        raise ValueError("input text поддерживает только латиницу; для кириллицы нужен adb или буфер обмена")
    completed = _run("input", "text", text.replace(" ", "%s"))
    return {"typed_chars": len(text), "exit_code": completed.returncode}


KEYCODES = {
    "home": "KEYCODE_HOME",
    "back": "KEYCODE_BACK",
    "recents": "KEYCODE_APP_SWITCH",
    "enter": "KEYCODE_ENTER",
    "delete": "KEYCODE_DEL",
    "power": "KEYCODE_POWER",
    "volume_up": "KEYCODE_VOLUME_UP",
    "volume_down": "KEYCODE_VOLUME_DOWN",
    "wakeup": "KEYCODE_WAKEUP",
    "sleep": "KEYCODE_SLEEP",
    "menu": "KEYCODE_MENU",
    "search": "KEYCODE_SEARCH",
    "play_pause": "KEYCODE_MEDIA_PLAY_PAUSE",
}


def screen_key(args: dict) -> dict:
    key = str(args["key"]).strip().lower()
    code = KEYCODES.get(key)
    if code is None:
        raise ValueError(f"неизвестная кнопка: {key}")
    completed = _run("input", "keyevent", code)
    return {"key": key, "keycode": code, "exit_code": completed.returncode}


def shell_run(args: dict) -> dict:
    command = str(args["command"]).strip()
    parts = shlex.split(command)
    if not parts:
        raise ValueError("пустая команда")
    program = Path(parts[0]).name
    if program not in ALLOWED_SHELL:
        raise PermissionError(f"«{program}» не разрешён. Разрешено: {', '.join(sorted(ALLOWED_SHELL))}")
    completed = subprocess.run(parts, capture_output=True, text=True, timeout=TIMEOUT,
                               errors="ignore")
    return {"command": command, "exit_code": completed.returncode,
            "stdout": completed.stdout[:MAX_OUTPUT], "stderr": completed.stderr[:2000]}


def fs_list(args: dict) -> dict:
    target = guard_path(str(args["path"]))
    entries = [
        {"name": item.name, "type": "dir" if item.is_dir() else "file"}
        for item in sorted(target.iterdir())[:200]
    ]
    return {"path": str(target), "count": len(entries), "entries": entries}


def fs_read(args: dict) -> dict:
    target = guard_path(str(args["path"]))
    max_bytes = int(args.get("max_bytes", 65536))
    raw = target.read_bytes()[:max_bytes]
    return {"path": str(target), "bytes_read": len(raw),
            "text": raw.decode("utf-8", errors="replace")}


def net_status(_args: dict) -> dict:
    addresses = subprocess.run(["ip", "-j", "addr"], capture_output=True, text=True,
                               timeout=TIMEOUT, errors="ignore").stdout
    ping = subprocess.run(["ping", "-c", "1", "-W", "3", "1.1.1.1"],
                          capture_output=True, text=True, timeout=TIMEOUT, errors="ignore")
    return {"interfaces": addresses[:4000], "internet": ping.returncode == 0}


HANDLERS = {
    "sys.info": sys_info,
    "shell.run": shell_run,
    "fs.list": fs_list,
    "fs.read": fs_read,
    "net.status": net_status,
    "battery.status": battery_status,
    "sms.list": sms_list,
    "sms.send": sms_send,
    "notify.send": notify_send,
    "tts.speak": tts_speak,
    "torch.set": torch_set,
    "wifi.info": wifi_info,
    "location.get": location_get,
    "app.open": app_open,
    "apps.list": apps_list,
    "app.info": app_info,
    "app.stop": app_stop,
    "app.current": app_current,
    "screen.shot": screen_shot,
    "ui.dump": ui_dump,
    "screen.tap": screen_tap,
    "screen.swipe": screen_swipe,
    "screen.text": screen_text,
    "screen.key": screen_key,
}


def main() -> None:
    client = AgentClient(
        device_id=os.getenv("ANDROID_DEVICE_ID", "phone"),
        name=os.getenv("ANDROID_DEVICE_NAME", "Мой телефон"),
        platform="android",
        handlers=HANDLERS,
    )
    client.run()


if __name__ == "__main__":
    main()
