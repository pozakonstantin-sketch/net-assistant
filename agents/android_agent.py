"""Агент Android без root — на базе Termux + Termux:API.

Установка на телефоне (однократно):
    1. F-Droid: Termux и Termux:API (оба из F-Droid, не из Play).
    2. В Termux:  pkg update && pkg install python termux-api openssh
    3. Разрешения Termux:API: телефон, SMS, местоположение, уведомления, камера.
    4. Скопировать проект на телефон, затем:  python -m agents.android_agent

Root не нужен: всё, что умеет Termux:API, доступно обычному пользователю.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
from pathlib import Path

from agents.common import AgentClient
from core.config import settings

log = logging.getLogger("android-agent")

TIMEOUT = 20
MAX_OUTPUT = 32_000
FULL_ACCESS = settings.access_mode == "full"

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


def termux_json(*args: str, timeout: int = TIMEOUT) -> object:
    output = termux(*args, timeout=timeout)
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return {"raw": output}


# ───────────────────────────── команды ─────────────────────────────


def sys_info(_args: dict) -> dict:
    """Сведения о телефоне. getprop — системный бинарник, а не termux-утилита."""
    properties = {}
    for key in ("ro.product.model", "ro.product.manufacturer", "ro.product.brand",
                "ro.build.version.release", "ro.build.version.sdk", "ro.product.cpu.abi"):
        try:
            value = _run("getprop", key, env=_system_env()).stdout.strip()
        except Exception:  # noqa: BLE001
            continue
        if value:
            properties[key.split(".")[-1]] = value
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
    """Последняя известная точка.

    Режим ``-r last`` отвечает мгновенно и не занимает очередь Termux:API.
    Запрос свежего фикса (``-r once``/без ``-r``) на этой модели может висеть
    минуты и заодно ломать остальные команды — он сознательно не используется.
    """
    data = termux_json("location", "-p", "network", "-r", "last", timeout=15)
    if isinstance(data, dict) and data.get("API_ERROR"):
        return {"available": False,
                "note": ("телефон не отдал координаты: " + str(data["API_ERROR"]) +
                         ". Проверьте, что геолокация включена, а Termux:API разрешено "
                         "определять местоположение.")}
    if not data:
        return {"available": False, "note": "координаты недоступны"}
    return {"available": True, "location": data}


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


# Снимок экрана, разбор интерфейса и ввод касаний требуют системных разрешений
# READ_FRAME_BUFFER и INJECT_EVENTS, которые обычному приложению не выдают.
# Поэтому эти действия на телефоне недоступны без root или отладки по USB (adb),
# и агент их даже не объявляет — иначе LLM обещал бы пользователю невозможное.
_NO_SCREEN = ("Управление экраном и снимки экрана на этой модели недоступны: "
              "Android запрещает обычным приложениям ввод касаний и захват экрана "
              "без root или отладки по USB (adb).")


def screen_shot(_args: dict) -> dict:
    raise RuntimeError(_NO_SCREEN)


def ui_dump(_args: dict) -> dict:
    raise RuntimeError(_NO_SCREEN)


def screen_tap(_args: dict) -> dict:
    raise RuntimeError(_NO_SCREEN)


def screen_swipe(_args: dict) -> dict:
    raise RuntimeError(_NO_SCREEN)


def screen_text(_args: dict) -> dict:
    raise RuntimeError(_NO_SCREEN)


def screen_key(_args: dict) -> dict:
    raise RuntimeError(_NO_SCREEN)


def shell_run(args: dict) -> dict:
    command = str(args["command"]).strip()
    if not command:
        raise ValueError("пустая команда")
    if FULL_ACCESS:
        return _run_full(command, cwd=args.get("cwd"),
                         timeout=int(args.get("timeout", 60)))
    parts = shlex.split(command)
    if not parts:
        raise ValueError("пустая команда")
    program = Path(parts[0]).name
    if program not in ALLOWED_SHELL:
        raise PermissionError(f"«{program}» не разрешён. Разрешено: {', '.join(sorted(ALLOWED_SHELL))}")
    completed = subprocess.run(parts, capture_output=True, text=True, timeout=TIMEOUT,
                               errors="replace")
    return {"command": command, "exit_code": completed.returncode,
            "stdout": completed.stdout[:MAX_OUTPUT], "stderr": completed.stderr[:2000]}


def _sh_bin() -> str:
    """Путь к sh: в Termux нет /bin/sh — есть $PREFIX/bin/sh."""
    prefix = os.environ.get("PREFIX", "/data/data/com.termux/files/usr")
    for candidate in (os.path.join(prefix, "bin", "sh"), "/system/bin/sh"):
        if os.path.exists(candidate):
            return candidate
    return "/bin/sh"


def _run_full(command: str, cwd: str | None = None, timeout: int = 60) -> dict:
    """Произвольная команда в Termux (полный режим)."""
    timeout = max(1, min(int(timeout), 600))
    workdir = str(guard_path(cwd)) if cwd else None
    completed = subprocess.run(
        [_sh_bin(), "-c", command], cwd=workdir, capture_output=True, text=True,
        timeout=timeout, encoding="utf-8", errors="replace",
    )
    return {"command": command, "exit_code": completed.returncode,
            "stdout": completed.stdout[:MAX_OUTPUT],
            "stderr": completed.stderr[:MAX_OUTPUT // 4],
            "truncated": len(completed.stdout) > MAX_OUTPUT}


def exec_run(args: dict) -> dict:
    if not FULL_ACCESS:
        raise PermissionError("exec.run доступен только при ACCESS_MODE=full")
    command = str(args["command"]).strip()
    if not command:
        raise ValueError("пустая команда")
    return _run_full(command, cwd=args.get("cwd"),
                     timeout=int(args.get("timeout", 60)))


def fs_write(args: dict) -> dict:
    if not FULL_ACCESS:
        raise PermissionError("fs.write доступен только при ACCESS_MODE=full")
    target = guard_path(str(args["path"]))
    text = str(args["text"])
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if args.get("append") else "w"
    with target.open(mode, encoding="utf-8", newline="") as handle:
        handle.write(text)
    return {"path": str(target), "bytes": len(text.encode("utf-8")),
            "mode": "append" if args.get("append") else "overwrite"}


def fs_delete(args: dict) -> dict:
    if not FULL_ACCESS:
        raise PermissionError("fs.delete доступен только при ACCESS_MODE=full")
    target = guard_path(str(args["path"]))
    if not target.exists():
        raise FileNotFoundError(f"нет такого пути: {target}")
    import shutil
    if target.is_dir():
        if args.get("recursive"):
            shutil.rmtree(target)
        else:
            target.rmdir()
        kind = "dir"
    else:
        target.unlink()
        kind = "file"
    return {"deleted": str(target), "type": kind}


def fs_mkdir(args: dict) -> dict:
    if not FULL_ACCESS:
        raise PermissionError("fs.mkdir доступен только при ACCESS_MODE=full")
    target = guard_path(str(args["path"]))
    target.mkdir(parents=True, exist_ok=True)
    return {"created": str(target)}


def proc_kill(args: dict) -> dict:
    if not FULL_ACCESS:
        raise PermissionError("proc.kill доступен только при ACCESS_MODE=full")
    pid = args.get("pid")
    if not pid:
        raise ValueError("нужен pid")
    completed = _run("kill", "-9" if args.get("force") else "-15", str(int(pid)))
    return {"pid": int(pid), "exit_code": completed.returncode,
            "stderr": completed.stderr[:1000]}


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
    """Сеть телефона: адреса интерфейсов, наличие интернета, сведения о WiFi."""
    addresses = ""
    for argv in (("ip", "-j", "addr"), ("ip", "addr"), ("ifconfig",)):
        try:
            completed = subprocess.run(list(argv), capture_output=True, text=True,
                                       timeout=TIMEOUT, errors="ignore")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        if completed.stdout.strip():
            addresses = completed.stdout.strip()
            break

    wifi: object = {}
    try:
        wifi = termux_json("wifi-connectioninfo")
    except Exception:  # noqa: BLE001
        wifi = {}

    try:
        ping = subprocess.run(["ping", "-c", "1", "-W", "3", "1.1.1.1"],
                              capture_output=True, text=True, timeout=TIMEOUT, errors="ignore")
        internet = ping.returncode == 0
    except Exception:  # noqa: BLE001
        internet = False

    return {"interfaces": addresses[:4000], "internet": internet, "wifi": wifi}


HANDLERS = {
    "sys.info": sys_info,
    "shell.run": shell_run,
    "exec.run": exec_run,
    "fs.list": fs_list,
    "fs.read": fs_read,
    "fs.write": fs_write,
    "fs.delete": fs_delete,
    "fs.mkdir": fs_mkdir,
    "proc.kill": proc_kill,
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
