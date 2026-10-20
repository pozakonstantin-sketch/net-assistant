"""Агент рабочего стола ВМ (Astra Linux, X11).

Даёт ИИ «глаза и руки» внутри графической сессии: снимок экрана, клики,
ввод с клавиатуры, прокрутка, запуск приложений. Так ассистент может сам
открыть панель в браузере ВМ, нажать кнопку и проверить результат.

Запуск (в сессии пользователя assistant):
    DISPLAY=:0 XAUTHORITY=~/.Xauthority python -m agents.vm_agent

Ввод текста идёт через буфер обмена (xsel) + Ctrl+V — поэтому работает
кириллица при любой раскладке.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import re
import subprocess
import time
from pathlib import Path

from agents.common import AgentClient

log = logging.getLogger("vm-agent")

DISPLAY = os.getenv("DISPLAY", ":0")
XAUTHORITY = os.getenv("XAUTHORITY", str(Path.home() / ".Xauthority"))
os.environ["DISPLAY"] = DISPLAY
os.environ["XAUTHORITY"] = XAUTHORITY

MODIFIERS = {
    "ctrl": "Control_L",
    "control": "Control_L",
    "alt": "Alt_L",
    "shift": "Shift_L",
    "super": "Super_L",
    "win": "Super_L",
}

KEYS = {
    "enter": "Return",
    "return": "Return",
    "esc": "Escape",
    "escape": "Escape",
    "tab": "Tab",
    "space": "space",
    "backspace": "BackSpace",
    "delete": "Delete",
    "insert": "Insert",
    "home": "Home",
    "end": "End",
    "pageup": "Prior",
    "pagedown": "Next",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "printscreen": "Print",
}
for _index in range(1, 13):
    KEYS[f"f{_index}"] = f"F{_index}"


# ───────────────────────────── X11 ─────────────────────────────


def _display():
    from Xlib import display

    return display.Display(os.environ["DISPLAY"])


def _keycode(dpy, name: str) -> int:
    from Xlib import XK

    code = dpy.keysym_to_keycode(XK.string_to_keysym(name))
    if not code:
        raise ValueError(f"неизвестная клавиша: {name}")
    return code


def _tap(dpy, keys: list[str]) -> None:
    from Xlib import X
    from Xlib.ext import xtest

    codes = [_keycode(dpy, KEYS.get(item.lower(), item)) for item in keys]
    for code in codes:
        xtest.fake_input(dpy, X.KeyPress, code)
    for code in reversed(codes):
        xtest.fake_input(dpy, X.KeyRelease, code)
    dpy.sync()


# ───────────────────────────── действия ─────────────────────────────


def vm_info(_args: dict) -> dict:
    dpy = _display()
    screen = dpy.screen()
    return {
        "display": os.environ["DISPLAY"],
        "width": screen.width_in_pixels,
        "height": screen.height_in_pixels,
        "session": os.getenv("DESKTOP_SESSION", ""),
        "user": os.getenv("USER", ""),
    }


def vm_screenshot(_args: dict) -> dict:
    """Снимок экрана ВМ — модель видит, что происходит на рабочем столе."""
    from PIL import Image

    completed = subprocess.run(
        ["import", "-display", os.environ["DISPLAY"], "-window", "root", "png:-"],
        capture_output=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"снимок не удался: {completed.stderr.decode('utf-8', 'ignore')[:300]}"
        )
    raw = completed.stdout
    image = Image.open(io.BytesIO(raw))
    return {
        "image_b64": base64.b64encode(raw).decode("ascii"),
        "width": image.width,
        "height": image.height,
        "bytes": len(raw),
    }


def vm_move(args: dict) -> dict:
    from Xlib import X
    from Xlib.ext import xtest

    x, y = int(args["x"]), int(args["y"])
    dpy = _display()
    xtest.fake_input(dpy, X.MotionNotify, x=x, y=y)
    dpy.sync()
    return {"x": x, "y": y}


def vm_click(args: dict) -> dict:
    from Xlib import X
    from Xlib.ext import xtest

    x, y = int(args["x"]), int(args["y"])
    button = int(args.get("button", 1))
    count = max(1, min(int(args.get("count", 1)), 3))
    dpy = _display()
    xtest.fake_input(dpy, X.MotionNotify, x=x, y=y)
    for _ in range(count):
        xtest.fake_input(dpy, X.ButtonPress, button)
        xtest.fake_input(dpy, X.ButtonRelease, button)
        dpy.sync()
        time.sleep(0.05)
    return {"x": x, "y": y, "button": button, "count": count}


def vm_scroll(args: dict) -> dict:
    from Xlib import X
    from Xlib.ext import xtest

    amount = int(args.get("amount", 3))
    amount = max(-15, min(amount, 15))
    x = args.get("x")
    y = args.get("y")
    dpy = _display()
    if x is not None and y is not None:
        xtest.fake_input(dpy, X.MotionNotify, x=int(x), y=int(y))
    button = 5 if amount > 0 else 4
    for _ in range(abs(amount)):
        xtest.fake_input(dpy, X.ButtonPress, button)
        xtest.fake_input(dpy, X.ButtonRelease, button)
        dpy.sync()
        time.sleep(0.03)
    return {"amount": amount, "button": button}


def vm_type(args: dict) -> dict:
    """Ввод текста: буфер обмена + Ctrl+V (кириллица работает при любой раскладке)."""
    text = str(args["text"])[:4000]
    if not text:
        raise ValueError("пустой текст")
    completed = subprocess.run(
        ["xsel", "--clipboard", "--input"],
        input=text.encode("utf-8"),
        capture_output=True,
        timeout=10,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"буфер обмена недоступен: {completed.stderr.decode('utf-8', 'ignore')[:200]}"
        )
    time.sleep(0.15)
    dpy = _display()
    _tap(dpy, ["Control_L", "v"])
    return {"typed": text[:200], "length": len(text), "method": "clipboard"}


def vm_key(args: dict) -> dict:
    """Нажать клавишу или сочетание: Return, Escape, ctrl+l, ctrl+alt+t, F5."""
    raw = str(args["key"]).strip()
    if not raw:
        raise ValueError("пустая клавиша")
    parts = [item for item in re.split(r"\s*\+\s*", raw) if item]
    keys: list[str] = []
    for part in parts:
        lowered = part.lower()
        if lowered in MODIFIERS:
            keys.append(MODIFIERS[lowered])
        else:
            keys.append(KEYS.get(lowered, part))
    dpy = _display()
    _tap(dpy, keys)
    return {"key": raw, "sent": keys}


def vm_windows(_args: dict) -> dict:
    """Список видимых окон: имя и координаты (чтобы понять, что открыто)."""
    from Xlib import X

    dpy = _display()
    root = dpy.screen().root

    def name_of(window) -> str:
        try:
            name = window.get_wm_name()
        except Exception:  # noqa: BLE001
            name = None
        if not name:
            try:
                prop = window.get_full_property(dpy.intern_atom("_NET_WM_NAME"), 0)
                if prop and prop.value:
                    name = prop.value
                    if isinstance(name, bytes):
                        name = name.decode("utf-8", "ignore")
            except Exception:  # noqa: BLE001
                name = None
        return str(name).strip() if name else ""

    # Сначала пробуем EWMH-список клиентов: он не зависит от переподчинения окон.
    ids: list[int] = []
    try:
        prop = root.get_full_property(dpy.intern_atom("_NET_CLIENT_LIST"), X.AnyPropertyType)
        if prop:
            ids = list(prop.value)
    except Exception:  # noqa: BLE001
        ids = []

    windows = []
    if ids:
        for wid in ids:
            try:
                window = dpy.create_resource_object("window", wid)
                attrs = window.get_attributes()
                if attrs.map_state != X.IsViewable:
                    continue
                name = name_of(window)
                if not name:
                    continue
                geometry = window.get_geometry()
                position = window.translate_coords(root, 0, 0)
                windows.append({
                    "name": name[:120],
                    "x": position.x,
                    "y": position.y,
                    "width": geometry.width,
                    "height": geometry.height,
                    "active": bool(attrs.map_state == X.IsViewable),
                })
            except Exception:  # noqa: BLE001 - окно могло исчезнуть
                continue

    # Запасной путь: обходим дерево окон (без EWMH).
    if not windows:
        stack = [root]
        while stack:
            node = stack.pop()
            try:
                children = node.query_tree().children
            except Exception:  # noqa: BLE001
                continue
            for child in children:
                stack.append(child)
                try:
                    attrs = child.get_attributes()
                    if attrs.map_state != X.IsViewable:
                        continue
                    geometry = child.get_geometry()
                    if geometry.width < 50 or geometry.height < 50:
                        continue
                    name = name_of(child)
                    if not name or name == "fly-wm":
                        continue
                    position = child.translate_coords(root, 0, 0)
                    windows.append({
                        "name": name[:120],
                        "x": position.x,
                        "y": position.y,
                        "width": geometry.width,
                        "height": geometry.height,
                    })
                except Exception:  # noqa: BLE001
                    continue

    return {"count": len(windows), "windows": windows[:30]}


def vm_panel(_args: dict) -> dict:
    """Открыть панель ассистента в браузере ВМ с токеном (без ввода пароля)."""
    from core.config import settings

    url = f"http://127.0.0.1:8787/?token={settings.token}"
    subprocess.Popen(
        ["xdg-open", url],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {"opened": "панель ассистента", "url": "http://127.0.0.1:8787/"}


def vm_open(args: dict) -> dict:
    """Открыть URL или файл приложением по умолчанию (браузер, просмотрщик)."""
    target = str(args["target"]).strip()
    if not target:
        raise ValueError("пустая цель")
    allowed = target.startswith(("http://", "https://", "mailto:")) or target.startswith("/")
    if not allowed and not Path(target).exists():
        raise ValueError("нужен URL (http/https) или существующий путь")
    subprocess.Popen(
        ["xdg-open", target],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    log.info("открываю: %s", target)
    return {"opened": target}


HANDLERS = {
    "vm.info": vm_info,
    "vm.screenshot": vm_screenshot,
    "vm.move": vm_move,
    "vm.click": vm_click,
    "vm.scroll": vm_scroll,
    "vm.type": vm_type,
    "vm.key": vm_key,
    "vm.windows": vm_windows,
    "vm.open": vm_open,
    "vm.panel": vm_panel,
}


def main() -> None:
    client = AgentClient(
        device_id=os.getenv("VM_DEVICE_ID", "vm-desktop"),
        name=os.getenv("VM_DEVICE_NAME", "Рабочий стол ВМ"),
        platform="vm-desktop",
        handlers=HANDLERS,
    )
    client.run()


if __name__ == "__main__":
    main()
