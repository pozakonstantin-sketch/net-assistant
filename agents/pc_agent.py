"""Агент ПК. Основная цель — Windows, но на Linux/macOS большая часть работает.

Запуск:
    python -m agents.pc_agent

Два режима (ACCESS_MODE в .env):
    restricted — только белые списки команд и разрешённые каталоги;
    full       — произвольные команды PowerShell/cmd, запись и удаление файлов,
                 управление процессами и питанием. Права = права пользователя,
                 от которого запущен агент.
"""

from __future__ import annotations

import base64
import csv
import ctypes
import io
import logging
import os
import platform
import socket
import subprocess
import time
from pathlib import Path

from agents.common import AgentClient
from core.config import settings

log = logging.getLogger("pc-agent")

SHELL_TIMEOUT = 25
MAX_OUTPUT = 64_000
FULL_ACCESS = settings.access_mode == "full"

DEFAULT_SHELL_ALLOW = (
    "dir,ipconfig,ping,tasklist,whoami,systeminfo,netstat,arp,hostname,where,"
    "echo,type,findstr,tree,nslookup,tracert,driverquery,Get-Date,Get-Process,"
    "Get-Service,Get-Volume,Get-CimInstance,Get-NetIPAddress,Get-NetRoute"
)

# Символы, разрешающие цепочки и перенаправление — запрещены намеренно.
FORBIDDEN_CHARS = (";", "&&", "||", "|", ">", "<", "`", "$(", "\n", "&")


# ───────────────────────────── ограничения доступа ─────────────────────────────


def allowed_commands() -> list[str]:
    raw = os.getenv("PC_SHELL_ALLOW", "").strip()
    items = [item.strip().lower() for item in raw.split(",") if item.strip()]
    return items or [c.lower() for c in DEFAULT_SHELL_ALLOW.split(",")]


def allowed_roots() -> list[Path]:
    raw = os.getenv("PC_FS_ROOTS", "").strip()
    roots = [Path(p).expanduser().resolve() for p in raw.split(";") if p.strip()]
    return roots or [Path.home().resolve()]


def guard_path(raw_path: str) -> Path:
    target = Path(raw_path).expanduser()
    if not target.is_absolute():
        target = Path.home() / target
    target = target.resolve()
    if FULL_ACCESS:
        return target
    roots = allowed_roots()
    if not any(target == root or root in target.parents for root in roots):
        raise PermissionError(
            f"путь вне разрешённых каталогов ({', '.join(str(r) for r in roots)})"
        )
    return target


# ───────────────────────────── команды ─────────────────────────────


def sys_info(_args: dict) -> dict:
    info = {
        "host": socket.gethostname(),
        "os": platform.platform(),
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
        "uptime_hours": round(_uptime_seconds() / 3600, 1),
        "arch": platform.machine(),
    }
    memory = _memory_info()
    if memory:
        info["memory"] = memory
    return info


def sys_screenshot(_args: dict) -> dict:
    try:
        from PIL import ImageGrab
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("нужен пакет pillow: pip install pillow") from exc

    image = ImageGrab.grab(all_screens=True) if os.name == "nt" else ImageGrab.grab()
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    raw = buffer.getvalue()
    return {
        "image_b64": base64.b64encode(raw).decode("ascii"),
        "width": image.width,
        "height": image.height,
        "bytes": len(raw),
    }


def sys_lock(_args: dict) -> dict:
    if os.name == "nt":
        if not ctypes.windll.user32.LockWorkStation():  # type: ignore[attr-defined]
            raise RuntimeError("Windows отказалась заблокировать сеанс")
        return {"locked": True}
    raise RuntimeError("блокировка экрана реализована только для Windows")


def proc_list(args: dict) -> dict:
    sort_by = args.get("sort", "memory")
    limit = int(args.get("limit", 15))

    if os.name == "nt":
        output = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"],
            capture_output=True, text=True, timeout=SHELL_TIMEOUT,
            encoding="cp866", errors="ignore",
        ).stdout
        processes = []
        for row in csv.reader(output.splitlines()):
            if len(row) < 5:
                continue
            try:
                memory_kb = int(row[4].replace("\xa0", "").replace(",", "")
                                .replace(" K", "").replace("К", "").strip())
            except ValueError:
                memory_kb = 0
            processes.append({"name": row[0], "pid": row[1], "memory_mb": round(memory_kb / 1024, 1)})
    else:
        output = subprocess.run(
            ["ps", "-eo", "comm,pid,rss", "--sort=-rss"],
            capture_output=True, text=True, timeout=SHELL_TIMEOUT, errors="ignore",
        ).stdout
        processes = []
        for line in output.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 3:
                continue
            try:
                processes.append({"name": parts[0], "pid": parts[1],
                                  "memory_mb": round(int(parts[2]) / 1024, 1)})
            except ValueError:
                continue

    key = "memory_mb" if sort_by == "memory" else "name"
    processes.sort(key=lambda p: p.get(key, 0), reverse=(sort_by == "memory"))
    return {"sort": sort_by, "total": len(processes), "processes": processes[:limit],
            "note": "tasklist не отдаёт загрузку ЦП, сортировка по ЦП доступна через ps"}


def _run_full(command: str, shell: str = "powershell", cwd: str | None = None,
              timeout: int = 60) -> dict:
    """Выполнить произвольную команду в полном режиме."""
    timeout = max(1, min(int(timeout), 600))
    workdir = guard_path(cwd) if cwd else None
    if workdir and not workdir.is_dir():
        raise NotADirectoryError(f"не каталог: {workdir}")

    if os.name == "nt":
        if shell == "cmd":
            argv = ["cmd.exe", "/c", command]
        else:  # powershell по умолчанию
            prelude = ("$ErrorActionPreference='Continue'; "
                       "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
                       "$OutputEncoding=[Text.Encoding]::UTF8; ")
            argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                    prelude + command]
    else:
        argv = ["/bin/sh", "-c", command]

    completed = subprocess.run(
        argv, cwd=str(workdir) if workdir else None,
        capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
    )
    return {
        "command": command,
        "shell": shell,
        "exit_code": completed.returncode,
        "stdout": completed.stdout[:MAX_OUTPUT],
        "stderr": completed.stderr[:MAX_OUTPUT // 4],
        "truncated": len(completed.stdout) > MAX_OUTPUT,
    }


def shell_run(args: dict) -> dict:
    command = str(args["command"]).strip()
    if not command:
        raise ValueError("пустая команда")
    if len(command) > (8000 if FULL_ACCESS else 512):
        raise ValueError("команда слишком длинная")

    if FULL_ACCESS:
        return _run_full(
            command,
            shell=str(args.get("shell", "powershell")),
            cwd=args.get("cwd"),
            timeout=int(args.get("timeout", 60)),
        )

    for char in FORBIDDEN_CHARS:
        if char in command:
            raise PermissionError(f"символ {char!r} запрещён (цепочки и перенаправление отключены)")

    first = command.split()[0].strip('"').strip("'")
    base = Path(first).name
    if base.lower().endswith(".exe"):
        base = base[:-4]
    allowlist = allowed_commands()
    if base.lower() not in allowlist:
        raise PermissionError(
            f"команда «{base}» не в белом списке. Разрешено: {', '.join(allowlist)}"
        )

    if base.lower().startswith("get-"):
        argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    else:
        argv = command

    completed = subprocess.run(
        argv, shell=isinstance(argv, str), capture_output=True, text=True,
        timeout=SHELL_TIMEOUT, encoding="cp866" if os.name == "nt" else None,
        errors="ignore",
    )
    return {
        "command": command,
        "exit_code": completed.returncode,
        "stdout": completed.stdout[:MAX_OUTPUT],
        "stderr": completed.stderr[:2000],
        "truncated": len(completed.stdout) > MAX_OUTPUT,
    }


def exec_run(args: dict) -> dict:
    """Произвольная команда оболочки (только полный режим)."""
    if not FULL_ACCESS:
        raise PermissionError("exec.run доступен только при ACCESS_MODE=full")
    command = str(args["command"]).strip()
    if not command:
        raise ValueError("пустая команда")
    return _run_full(
        command,
        shell=str(args.get("shell", "powershell")),
        cwd=args.get("cwd"),
        timeout=int(args.get("timeout", 60)),
    )


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
        if not args.get("recursive"):
            target.rmdir()
        else:
            shutil.rmtree(target)
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
    name = args.get("name")
    force = bool(args.get("force"))
    if not pid and not name:
        raise ValueError("нужен pid или name")
    if os.name == "nt":
        if pid:
            argv = ["taskkill", "/PID", str(int(pid))] + (["/F"] if force else [])
        else:
            argv = ["taskkill", "/IM", str(name)] + (["/F"] if force else [])
    else:
        argv = ["kill", "-9" if force else "-15",
                str(int(pid)) if pid else str(name)]
    completed = subprocess.run(argv, capture_output=True, text=True,
                               timeout=30, encoding="utf-8", errors="replace")
    return {"argv": argv, "exit_code": completed.returncode,
            "stdout": completed.stdout[:2000], "stderr": completed.stderr[:2000]}


def power_action(args: dict) -> dict:
    if not FULL_ACCESS:
        raise PermissionError("power.action доступен только при ACCESS_MODE=full")
    action = str(args["action"])
    delay = int(args.get("delay_sec", 30))
    if os.name == "nt":
        if action == "cancel":
            argv = ["shutdown", "/a"]
        elif action in {"shutdown", "restart"}:
            flag = "/s" if action == "shutdown" else "/r"
            argv = ["shutdown", flag, "/t", str(delay)]
        elif action == "logoff":
            argv = ["shutdown", "/l"]
        elif action == "hibernate":
            argv = ["shutdown", "/h"]
        elif action == "sleep":
            argv = ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"]
        else:
            raise ValueError(f"неизвестное действие: {action}")
    else:
        if action == "shutdown":
            argv = ["shutdown", "-h", "now"]
        elif action == "restart":
            argv = ["shutdown", "-r", "now"]
        elif action == "cancel":
            argv = ["shutdown", "-c"]
        else:
            raise ValueError(f"действие {action} не поддержано на этой ОС")
    completed = subprocess.run(argv, capture_output=True, text=True,
                               timeout=30, encoding="utf-8", errors="replace")
    return {"action": action, "delay_sec": delay, "exit_code": completed.returncode,
            "stdout": completed.stdout[:1000], "stderr": completed.stderr[:1000]}


def fs_list(args: dict) -> dict:
    target = guard_path(str(args["path"]))
    if not target.is_dir():
        raise NotADirectoryError(f"не каталог: {target}")
    entries = []
    for item in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))[:200]:
        try:
            stat = item.stat()
            entries.append({
                "name": item.name,
                "type": "dir" if item.is_dir() else "file",
                "size": stat.st_size,
                "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
            })
        except OSError:
            entries.append({"name": item.name, "type": "?", "error": "нет доступа"})
    return {"path": str(target), "count": len(entries), "entries": entries}


def fs_read(args: dict) -> dict:
    target = guard_path(str(args["path"]))
    max_bytes = int(args.get("max_bytes", 65536))
    if not target.is_file():
        raise FileNotFoundError(f"не файл: {target}")
    with target.open("rb") as handle:
        raw = handle.read(max_bytes)
    return {
        "path": str(target),
        "bytes_read": len(raw),
        "sha": __import__("hashlib").sha256(raw).hexdigest()[:16],
        "text": raw.decode("utf-8", errors="replace"),
    }


def media_volume(args: dict) -> dict:
    if os.name != "nt":
        raise RuntimeError("громкость реализована только для Windows")
    VK_VOLUME_MUTE, VK_VOLUME_DOWN, VK_VOLUME_UP = 0xAD, 0xAE, 0xAF
    KEYEVENTF_KEYUP = 0x0002

    def tap(vk: int, times: int = 1) -> None:
        for _ in range(times):
            ctypes.windll.user32.keybd_event(vk, 0, 0, 0)  # type: ignore[attr-defined]
            ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)  # type: ignore[attr-defined]

    if "mute" in args and args["mute"] is not None:
        tap(VK_VOLUME_MUTE)
        return {"mute_toggled": True}

    level = int(args["level"])
    # Windows не даёт точной установки через клавиши: 2% на нажатие,
    # поэтому сначала уходим в ноль, потом набираем нужное.
    tap(VK_VOLUME_DOWN, 50)
    tap(VK_VOLUME_UP, level // 2)
    return {"level_requested": level, "approximate": True}


def net_status(_args: dict) -> dict:
    result: dict = {"host": socket.gethostname()}
    try:
        result["addresses"] = socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError as exc:
        result["addresses_error"] = str(exc)

    ping_flag = "-n" if os.name == "nt" else "-c"
    probe = subprocess.run(["ping", ping_flag, "1", "1.1.1.1"],
                           capture_output=True, text=True, timeout=10, errors="ignore")
    result["internet"] = probe.returncode == 0
    result["ping_sample"] = probe.stdout.strip().splitlines()[-1:] or [""]
    return result


# ───────────────────────────── вспомогательное ─────────────────────────────


def _uptime_seconds() -> float:
    if os.name == "nt":
        return ctypes.windll.kernel32.GetTickCount64() / 1000  # type: ignore[attr-defined]
    try:
        return float(Path("/proc/uptime").read_text().split()[0])
    except OSError:
        return 0.0


def _memory_info() -> dict | None:
    if os.name != "nt":
        return None

    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatusEx()
    status.dwLength = ctypes.sizeof(MemoryStatusEx)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
        return None
    gigabyte = 1024 ** 3
    return {
        "total_gb": round(status.ullTotalPhys / gigabyte, 1),
        "available_gb": round(status.ullAvailPhys / gigabyte, 1),
        "used_percent": status.dwMemoryLoad,
    }


HANDLERS = {
    "sys.info": sys_info,
    "sys.screenshot": sys_screenshot,
    "sys.lock": sys_lock,
    "proc.list": proc_list,
    "proc.kill": proc_kill,
    "shell.run": shell_run,
    "exec.run": exec_run,
    "fs.list": fs_list,
    "fs.read": fs_read,
    "fs.write": fs_write,
    "fs.delete": fs_delete,
    "fs.mkdir": fs_mkdir,
    "power.action": power_action,
    "media.volume": media_volume,
    "net.status": net_status,
}


def main() -> None:
    client = AgentClient(
        device_id=os.getenv("PC_DEVICE_ID", "pc-desktop"),
        name=os.getenv("PC_DEVICE_NAME", "Мой ПК"),
        platform="pc-desktop",
        handlers=HANDLERS,
    )
    client.run()


if __name__ == "__main__":
    main()
