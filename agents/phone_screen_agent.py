"""Экран телефона через adb: агент работает в ВМ и управляет Honor по сети.

Зачем отдельный агент: Android запрещает обычным приложениям вводить касания
(INJECT_EVENTS) и снимать экран (READ_FRAME_BUFFER), поэтому агент Termux на
самом телефоне этого не умеет. Умеет adb — но adb нужен на компьютере.
Этот агент и есть «компьютер»: он живёт в ВМ и говорит с телефоном по сети
(adb по TCP), то есть управление экраном выполняет ВМ, а не ПК.

Запуск:
    python -m agents.phone_screen_agent

Нужно:
    * adb в ВМ (platform-tools) и ключ отладки, разрешённый на телефоне;
    * на телефоне включена отладка по сети (adb tcpip 5555);
    * в .env: PHONE_ADB_TARGETS=192.168.1.242:5555,100.65.67.89:5555

Ограничения (честно сообщаются пользователю):
    * экран должен быть разблокирован — PIN ассистент ввести не может;
    * кириллица вводится через ADBKeyboard (ставится отдельно, см. ниже).
"""

from __future__ import annotations

import base64
import logging
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
import xml.etree.ElementTree as ET

from agents.common import AgentClient

log = logging.getLogger("phone-screen")

ADB = shutil.which("adb") or "/opt/android/platform-tools/adb"
TARGETS = [
    item.strip()
    for item in (os.getenv("PHONE_ADB_TARGETS") or "192.168.1.242:5555").split(",")
    if item.strip()
]
CONNECT_TIMEOUT = 12
WATCHDOG_SECONDS = 30

BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")

KEY_CODES = {
    "back": "KEYCODE_BACK",
    "home": "KEYCODE_HOME",
    "recents": "KEYCODE_APP_SWITCH",
    "enter": "KEYCODE_ENTER",
    "wakeup": "KEYCODE_WAKEUP",
    "sleep": "KEYCODE_SLEEP",
    "power": "KEYCODE_POWER",
    "volume_up": "KEYCODE_VOLUME_UP",
    "volume_down": "KEYCODE_VOLUME_DOWN",
    "delete": "KEYCODE_DEL",
    "tab": "KEYCODE_TAB",
}


class Phone:
    """Одно подключение к телефону по adb, переиспользуется между командами."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.target: str | None = None
        self._size: tuple[int, int] | None = None

    # ─────────────────────────── adb ───────────────────────────

    def _call(self, args: list[str], timeout: float = 30) -> subprocess.CompletedProcess:
        return subprocess.run(
            [ADB, *args], capture_output=True, timeout=timeout, check=False
        )

    def _states(self) -> dict[str, str]:
        result = self._call(["devices"], timeout=20)
        states: dict[str, str] = {}
        for line in result.stdout.decode("utf-8", "replace").splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                states[parts[0]] = parts[1]
        return states

    def _discover(self) -> list[str]:
        """Найти телефон через mDNS.

        Когда на телефоне включена отладка по сети (или беспроводная отладка),
        adbd объявляет сервис `_adb._tcp` / `_adb-tls-connect._tcp` — так телефон
        находится, даже если его IP сменился.
        """
        try:
            result = self._call(["mdns", "services"], timeout=10)
        except subprocess.TimeoutExpired:
            return []
        found: list[str] = []
        for line in result.stdout.decode("utf-8", "replace").splitlines():
            if "_adb._tcp" not in line and "_adb-tls-connect._tcp" not in line:
                continue
            endpoint = line.split()[-1]
            if ":" in endpoint and endpoint not in found:
                found.append(endpoint)
        return found

    def connect(self) -> str:
        """Вернуть адрес, по которому телефон доступен; при необходимости подключиться."""
        with self._lock:
            states = self._states()
            for target in ([self.target] if self.target else []) + TARGETS:
                if target and states.get(target) == "device":
                    if target != self.target:
                        log.info("телефон доступен: %s", target)
                    self.target = target
                    return target

            for target in TARGETS:
                if states.get(target) == "unauthorized":
                    raise RuntimeError(
                        f"телефон {target} не подтвердил ключ ВМ: разблокируйте экран "
                        "и нажмите «Разрешить отладку»"
                    )

            for target in TARGETS:
                try:
                    self._call(["connect", target], timeout=CONNECT_TIMEOUT)
                except subprocess.TimeoutExpired:
                    log.warning("adb connect %s: таймаут", target)

            states = self._states()
            for target in TARGETS:
                if states.get(target) == "device":
                    self.target = target
                    log.info("телефон подключён по adb: %s", target)
                    return target

            # Последняя попытка — найти телефон через mDNS (IP мог измениться).
            discovered = [item for item in self._discover() if item not in TARGETS]
            for target in discovered:
                try:
                    self._call(["connect", target], timeout=CONNECT_TIMEOUT)
                except subprocess.TimeoutExpired:
                    log.warning("adb connect %s: таймаут", target)
            if discovered:
                log.info("mDNS предложил адреса: %s", ", ".join(discovered))
                states = self._states()
                for target in discovered:
                    if states.get(target) == "device":
                        self.target = target
                        log.info("телефон найден через mDNS: %s", target)
                        return target

            detail = ", ".join(f"{t}={states.get(t, 'нет')}" for t in TARGETS)
            raise RuntimeError(
                "телефон недоступен по adb (" + detail + "). Обычно помогает "
                "включить отладку по сети на телефоне (adb tcpip 5555) и "
                "разблокировать экран."
            )

    def _raw(self, args: list[str], timeout: float = 30) -> bytes:
        target = self.target or self.connect()
        result = self._call(["-s", target, *args], timeout=timeout)
        noise = (result.stdout + result.stderr).decode("utf-8", "replace")
        if "device not found" in noise or "device offline" in noise or "unauthorized" in noise:
            log.warning("связь с телефоном потеряна, переподключаюсь")
            with self._lock:
                self.target = None
            target = self.connect()
            result = self._call(["-s", target, *args], timeout=timeout)
            noise = (result.stdout + result.stderr).decode("utf-8", "replace")
            if "device not found" in noise or "device offline" in noise:
                raise RuntimeError("телефон отключился от adb посреди команды")
        if result.returncode != 0:
            message = result.stderr.decode("utf-8", "replace").strip()
            raise RuntimeError(message[:400] or "adb вернул ошибку")
        return result.stdout

    def shell(self, command: str, timeout: float = 30) -> str:
        return self._raw(["shell", command], timeout=timeout).decode("utf-8", "replace")

    def exec_out(self, command: str, timeout: float = 30) -> bytes:
        return self._raw(["exec-out", command], timeout=timeout)

    # ─────────────────────────── утилиты ───────────────────────────

    def screen_size(self) -> tuple[int, int]:
        if self._size:
            return self._size
        text = self.shell("wm size")
        match = re.search(r"(\d+)\s*x\s*(\d+)", text)
        if not match:
            raise RuntimeError(f"не удалось определить размер экрана: {text.strip()[:200]}")
        self._size = (int(match.group(1)), int(match.group(2)))
        return self._size

    def dump_ui(self) -> str:
        remote = "/sdcard/net-assistant-ui.xml"
        self.shell(f"uiautomator dump --compressed {remote} >/dev/null 2>&1")
        return self.exec_out(f"cat {remote}").decode("utf-8", "replace")


phone = Phone()


# ───────────────────────────── разбор интерфейса ─────────────────────────────


def parse_elements(xml_text: str, find: str | None = None,
                   limit: int = 40) -> list[dict]:
    root = ET.fromstring(xml_text)
    items: list[dict] = []
    needle = (find or "").strip().lower()

    for node in root.iter("node"):
        text = (node.get("text") or "").strip()
        desc = (node.get("content-desc") or "").strip()
        clickable = node.get("clickable") == "true"
        if not (text or desc or clickable):
            continue
        match = BOUNDS_RE.search(node.get("bounds") or "")
        if not match:
            continue
        x1, y1, x2, y2 = (int(value) for value in match.groups())
        if x2 <= x1 or y2 <= y1:
            continue
        label = text or desc
        if needle and needle not in label.lower():
            continue
        items.append({
            "text": label[:120] or "(без подписи)",
            "x": (x1 + x2) // 2,
            "y": (y1 + y2) // 2,
            "clickable": clickable,
            "type": (node.get("class") or "").split(".")[-1],
        })

    items.sort(key=lambda item: (item["y"], item["x"]))
    unique: list[dict] = []
    seen: set[tuple] = set()
    for item in items:
        key = (item["text"], item["x"], item["y"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique[:limit]


def find_element(needle: str, limit: int = 40) -> dict:
    elements = parse_elements(phone.dump_ui(), find=needle, limit=limit)
    if not elements:
        everything = parse_elements(phone.dump_ui(), limit=15)
        visible = ", ".join(f"«{item['text']}»" for item in everything) or "ничего"
        raise ValueError(f"на экране нет элемента со словом «{needle}». Видно: {visible}")
    # предпочитаем кликабельный элемент
    for item in elements:
        if item["clickable"]:
            return item
    return elements[0]


# ───────────────────────────── команды ─────────────────────────────


def _foreground() -> str:
    """Текущее окно.

    На Honor в выводе `dumpsys window` несколько блоков (по дисплеям), и первый
    `mCurrentFocus` обычно `null` — поэтому берём последний непустой. Если
    такого нет, спрашиваем активность у `dumpsys activity`.
    """
    text = phone.shell("dumpsys window 2>/dev/null | grep mCurrentFocus")
    values = [
        line.split("=", 1)[1].strip()
        for line in text.splitlines()
        if "=" in line
    ]
    values = [value for value in values if value and value.lower() != "null"]
    if values:
        return values[-1][:200]
    text = phone.shell("dumpsys activity activities 2>/dev/null | grep -m1 topResumedActivity")
    if "=" in text:
        return text.split("=", 1)[1].strip()[:200]
    return "неизвестно"


def _package_of(window: str) -> str:
    """Пакет из строки вида «Window{4249e2 u0 org.telegram.messenger/...}»."""
    match = re.search(r"\s([A-Za-z0-9_.]+)/[A-Za-z0-9_.$]+", window)
    return match.group(1) if match else ""


def screen_info(_args: dict) -> dict:
    width, height = phone.screen_size()
    density = re.search(r"(\d+)", phone.shell("wm density"))
    focus = _foreground()
    wake = phone.shell("dumpsys power 2>/dev/null | grep -m1 mWakefulness=").strip()
    interactive = phone.shell("dumpsys power 2>/dev/null | grep -m1 mHoldingDisplaySuspendBlocker")
    return {
        "target": phone.target,
        "size": f"{width}x{height}",
        "density": density.group(1) if density else "",
        "screen": "включён" if "Awake" in wake else "выключен или в спящем режиме",
        "foreground": focus,
        "package": _package_of(focus),
        "note": "если экран выключен — screen.key key=wakeup",
        "display_holder": interactive.strip()[:120],
    }


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _png_size(raw: bytes) -> tuple[int, int] | None:
    if len(raw) < 24 or not raw.startswith(PNG_MAGIC):
        return None
    return int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")


def display_ids() -> list[int]:
    text = phone.shell("dumpsys SurfaceFlinger --display-id")
    return [int(value) for value in re.findall(r"Display (\d+)", text)]


def _shot(png: bytes) -> dict:
    return {"image_b64": base64.b64encode(png).decode("ascii"),
            "bytes": len(png), "size": _png_size(png)}


def _png_body(raw: bytes) -> bytes | None:
    start = raw.find(PNG_MAGIC)
    return raw[start:] if start >= 0 else None


def screen_shot(_args: dict) -> dict:
    """Снимок активного дисплея.

    Honor — складной: у него основной и внешний дисплеи. `screencap -d <id>`
    для выключенного дисплея отдаёт чёрный кадр, а в сложенном состоянии
    размеры дисплеев могут совпадать, поэтому выбор «по размеру» ошибается.
    Сначала просим `screencap` без -d: Android сам снимает активный дисплей.
    Перебор id — только запасной вариант.
    """
    expected = phone.screen_size()

    default = _png_body(phone.exec_out("screencap -p", timeout=45))
    if default and _png_size(default) == expected:
        return _shot(default)

    candidates: list[bytes] = [default] if default else []
    for display in display_ids():
        plain = _png_body(phone.exec_out(f"screencap -d {display} -p", timeout=45))
        if plain:
            candidates.append(plain)

    if not candidates:
        raise RuntimeError(
            "телефон не отдал снимок экрана (экран может быть защищён)"
        )

    matching = [item for item in candidates if _png_size(item) == expected]
    chosen = max(matching or candidates, key=len)
    return _shot(chosen)


def screen_ui(args: dict) -> dict:
    elements = parse_elements(
        phone.dump_ui(), find=args.get("find"), limit=int(args.get("limit") or 40)
    )
    return {
        "count": len(elements),
        "elements": elements,
        "note": ("текст и координаты для screen.tap. Если нужного элемента нет — "
                 "прокрутите экран через screen.swipe"),
    }


def screen_tap(args: dict) -> dict:
    if args.get("text"):
        element = find_element(str(args["text"]))
        x, y, label = element["x"], element["y"], element["text"]
    else:
        x, y, label = int(args.get("x") or 0), int(args.get("y") or 0), "по координатам"
    if x <= 0 and y <= 0:
        raise ValueError("нужны координаты (x, y) или текст элемента (text)")

    if args.get("long"):
        phone.shell(f"input swipe {x} {y} {x} {y} 900")
    else:
        phone.shell(f"input tap {x} {y}")
    time.sleep(0.6)
    focus = phone.shell("dumpsys window 2>/dev/null | grep -m1 mCurrentFocus").strip()
    return {
        "tapped": {"label": label, "x": x, "y": y, "long": bool(args.get("long"))},
        "foreground": focus.replace("mCurrentFocus=", "").strip()[:160],
    }


def screen_swipe(args: dict) -> dict:
    duration = int(args.get("duration") or 300)
    direction = args.get("direction")
    if direction:
        width, height = phone.screen_size()
        if direction in ("up", "down"):
            x = width // 2
            y_from, y_to = (int(height * 0.75), int(height * 0.30)) if direction == "up" \
                else (int(height * 0.30), int(height * 0.75))
            x1, y1, x2, y2 = x, y_from, x, y_to
        else:
            y = height // 2
            x_from, x_to = (int(width * 0.85), int(width * 0.20)) if direction == "left" \
                else (int(width * 0.20), int(width * 0.85))
            x1, y1, x2, y2 = x_from, y, x_to, y
    else:
        if None in (args.get("x1"), args.get("y1"), args.get("x2"), args.get("y2")):
            raise ValueError("нужно направление (direction) или координаты x1,y1,x2,y2")
        x1, y1, x2, y2 = (int(args[key]) for key in ("x1", "y1", "x2", "y2"))

    phone.shell(f"input swipe {x1} {y1} {x2} {y2} {duration}")
    time.sleep(0.5)
    return {"swipe": {"from": [x1, y1], "to": [x2, y2], "duration_ms": duration,
                      "direction": direction}}


ADB_IME = "com.android.adbkeyboard/.AdbIME"


def _ime_ready() -> bool:
    listing = phone.shell("ime list -s 2>/dev/null")
    if "com.android.adbkeyboard" in listing:
        return True
    # После установки IME выключена — включаем один раз (adb-оболочке это можно).
    phone.shell(f"ime enable {ADB_IME} 2>/dev/null")
    return "com.android.adbkeyboard" in phone.shell("ime list -s 2>/dev/null")


def _type_via_adb_keyboard(text: str, clear: bool) -> bool:
    """Ввод через ADBKeyboard: понимает кириллицу — текст уходит в base64.

    Клавиатуру включаем только на время ввода и возвращаем прежнюю: иначе
    у владельца телефона пропадёт обычная клавиатура.
    """
    if not _ime_ready():
        return False
    saved = phone.shell("settings get secure default_input_method").strip()
    phone.shell(f"ime enable {ADB_IME}")
    phone.shell(f"ime set {ADB_IME}")
    time.sleep(0.4)
    try:
        if clear:
            phone.shell("am broadcast -a ADB_CLEAR_TEXT")
        payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
        phone.shell(f"am broadcast -a ADB_INPUT_B64 --es msg {payload}")
    finally:
        if saved and "adbkeyboard" not in saved:
            phone.shell(f"ime set {saved}")
    time.sleep(0.3)
    return True


def screen_text(args: dict) -> dict:
    text = str(args["text"])
    clear = bool(args.get("clear", False))
    if _type_via_adb_keyboard(text, clear):
        return {"entered": text, "method": "adbkeyboard", "cleared": clear}
    if not text.isascii():
        raise ValueError(
            "ADBKeyboard не установлен, а `input text` умеет только латиницу. "
            "Поставьте ADBKeyboard (docs/PHONE_SETUP.md) или введите текст вручную"
        )
    quoted = shlex.quote(text.replace(" ", "%s"))
    phone.shell(f"input text {quoted}")
    time.sleep(0.4)
    return {"entered": text, "method": "input"}


def screen_key(args: dict) -> dict:
    key = str(args["key"])
    code = KEY_CODES.get(key)
    if code is None:
        raise ValueError(f"неизвестная кнопка «{key}», доступны: {', '.join(KEY_CODES)}")
    phone.shell(f"input keyevent {code}")
    time.sleep(0.5)
    return {"pressed": key, "keycode": code}


RU_HINTS = {
    "часы": "clock", "будильник": "clock", "секундомер": "clock", "таймер": "clock",
    "камера": "camera", "фото": "camera|photos|gallery",
    "настройки": "settings", "параметры": "settings",
    "браузер": "browser|chrome", "интернет": "browser|chrome",
    "календарь": "calendar", "галерея": "gallery|photos",
    "музыка": "music|audio", "плеер": "music|audio",
    "телефон": "dialer|contacts|phone", "контакты": "contacts",
    "смс": "messaging|mms", "сообщения": "messaging|mms",
    "телеграм": "telegram", "ютуб": "youtube", "погода": "weather",
    "карты": "maps", "заметки": "notes", "почта": "mail|gmail",
}


def _packages() -> list[str]:
    listing = phone.shell("pm list packages 2>/dev/null")
    return [
        line.split(":", 1)[1].strip()
        for line in listing.splitlines()
        if line.startswith("package:")
    ]


def _resolve_package(name: str) -> str | None:
    """Пакет по имени или по русскому названию приложения («Часы» → ...deskclock).

    Порядок поиска: точное имя пакета → последний сегмент совпадает с шаблоном
    (settings → com.android.settings, а не com.android.settings.intelligence) →
    пакет заканчивается на шаблон → вхождение (берём самый короткий).
    """
    value = name.strip()
    if not value or " " in value:
        return None
    listing = _packages()
    if value in listing:
        return value

    hint = RU_HINTS.get(value.lower())
    patterns = (hint or value.lower()).split("|")
    for pattern in patterns:
        for package in listing:
            if package.rsplit(".", 1)[-1] == pattern:
                return package
    for pattern in patterns:
        for package in listing:
            if package.endswith(f".{pattern}"):
                return package
    best: str | None = None
    for pattern in patterns:
        for package in listing:
            if pattern in package.lower() and (best is None or len(package) < len(best)):
                best = package
        if best:
            return best
    return None


def app_open(args: dict) -> dict:
    wanted = str(args["package"]).strip()
    package = _resolve_package(wanted) or wanted
    output = phone.shell(
        f"monkey -p {shlex.quote(package)} -c android.intent.category.LAUNCHER 1 2>&1 | tail -3",
        timeout=45,
    )
    if "No activities found" in output or "aborted" in output:
        raise ValueError(
            f"не удалось запустить «{wanted}» (пакет {package}): {output.strip()[:200]}"
        )
    time.sleep(1.5)
    return {
        "launched": package,
        "requested": wanted,
        "foreground": _foreground(),
        "output": output.strip()[:200],
    }


def app_current(_args: dict) -> dict:
    value = _foreground()
    package = _package_of(value) or (value.split("/")[0].strip() if "/" in value else value)
    return {"package": package or "неизвестно", "window": value[:160]}


# ───────────────────────────── фоновая проверка ─────────────────────────────


def watchdog() -> None:
    """Держим подключение к телефону живым: после перезагрузки телефона adb теряет его."""
    while True:
        time.sleep(WATCHDOG_SECONDS)
        try:
            phone.connect()
        except Exception as exc:  # noqa: BLE001 - телефон может быть выключен
            log.warning("телефон недоступен: %s", exc)


HANDLERS = {
    "screen.info": screen_info,
    "screen.shot": screen_shot,
    "screen.ui": screen_ui,
    "screen.tap": screen_tap,
    "screen.swipe": screen_swipe,
    "screen.text": screen_text,
    "screen.key": screen_key,
    "app.open": app_open,
    "app.current": app_current,
}


def main() -> None:
    threading.Thread(target=watchdog, name="phone-watchdog", daemon=True).start()
    client = AgentClient(
        device_id=os.getenv("PHONE_SCREEN_DEVICE_ID", "phone-screen"),
        name=os.getenv("PHONE_SCREEN_DEVICE_NAME", "Honor — экран (adb)"),
        platform="android-adb",
        handlers=HANDLERS,
    )
    client.run()


if __name__ == "__main__":
    main()
