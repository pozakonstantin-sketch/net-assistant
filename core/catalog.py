"""Каталог команд: единый источник правды о том, что вообще разрешено.

Агент сообщает, какие действия он умеет (``actions`` в hello), но выполнить
можно только то, что есть здесь. Так LLM не может придумать новую команду,
а опасные действия помечены ``dangerous=True`` и требуют подтверждения.

Поля:
    desc        — описание для LLM (что делает команда);
    args        — схема аргументов (см. core.protocol.validate_args);
    dangerous   — требуют подтверждения пользователя;
    devices     — на каких типах устройств доступна (pc/android/router).
"""

from __future__ import annotations

from typing import Any

PC = "pc-desktop"
ANDROID = "android"
# экран телефона через adb: агент работает в ВМ
ANDROID_ADB = "android-adb"
ROUTER = "router"
# рабочий стол Astra Linux внутри ВМ: скриншоты, клики, ввод
VM = "vm-desktop"

CATALOG: dict[str, dict[str, Any]] = {
    # ───────────────────────────── ПК (Windows) ─────────────────────────────
    "sys.info": {
        "desc": "Основные сведения о ПК: ОС, процессор, память, аптайм.",
        "devices": [PC, ANDROID],
        "args": {},
    },
    "sys.screenshot": {
        "desc": "Сделать снимок экрана ПК. Возвращает base64 PNG и путь к файлу.",
        "devices": [PC],
        "args": {},
    },
    "sys.lock": {
        "desc": "Заблокировать экран ПК (как Win+L).",
        "devices": [PC],
        "args": {},
    },
    "proc.list": {
        "desc": "Список процессов ПК, отсортированный по памяти или процессору.",
        "devices": [PC],
        "args": {
            "sort": {"type": "str", "enum": ["memory", "cpu"], "default": "memory"},
            "limit": {"type": "int", "default": 15, "min": 1, "max": 100},
        },
    },
    "shell.run": {
        "desc": ("Выполнить команду в оболочке. В обычном режиме разрешены только "
                 "безобидные диагностические программы из белого списка; в полном "
                 "режиме (ACCESS_MODE=full) — любая команда PowerShell/cmd/sh, "
                 "включая пайпы и перенаправление."),
        "devices": [PC, ANDROID],
        "args": {
            "command": {"type": "str", "required": True, "max_len": 8000},
            "shell": {"type": "str", "enum": ["powershell", "cmd", "sh"],
                      "default": "powershell"},
            "cwd": {"type": "str", "max_len": 512},
            "timeout": {"type": "int", "default": 60, "min": 1, "max": 600},
        },
    },
    "fs.list": {
        "desc": "Показать содержимое каталога.",
        "devices": [PC, ANDROID],
        "args": {"path": {"type": "str", "required": True, "max_len": 512}},
    },
    "fs.read": {
        "desc": "Прочитать текстовый файл (ограниченный размер).",
        "devices": [PC, ANDROID],
        "args": {
            "path": {"type": "str", "required": True, "max_len": 512},
            "max_bytes": {"type": "int", "default": 65536, "min": 1, "max": 1048576},
        },
    },
    "media.volume": {
        "desc": "Управление громкостью: level 0-100 или mute true/false.",
        "devices": [PC],
        "args": {
            "level": {"type": "int", "min": 0, "max": 100},
            "mute": {"type": "bool"},
        },
    },
    "net.status": {
        "desc": "Состояние сети устройства: адреса, шлюз, пинг.",
        "devices": [PC, ANDROID, ROUTER],
        "args": {},
    },

    # ───────────────────── Полный доступ (ACCESS_MODE=full) ─────────────────────
    "exec.run": {
        "desc": ("Выполнить произвольную команду оболочки: PowerShell на ПК, "
                 "sh в Termux, sh на роутере. Поддерживает пайпы и перенаправление. "
                 "Работает с правами пользователя, под которым запущен агент."),
        "devices": [PC, ANDROID, ROUTER],
        "dangerous": True,
        "full_only": True,
        "args": {
            "command": {"type": "str", "required": True, "max_len": 8000},
            "shell": {"type": "str", "enum": ["powershell", "cmd", "sh"], "default": "powershell"},
            "cwd": {"type": "str", "max_len": 512},
            "timeout": {"type": "int", "default": 60, "min": 1, "max": 600},
        },
    },
    "fs.write": {
        "desc": "Создать или перезаписать файл произвольным содержимым.",
        "devices": [PC, ANDROID, ROUTER],
        "dangerous": True,
        "full_only": True,
        "args": {
            "path": {"type": "str", "required": True, "max_len": 512},
            "text": {"type": "str", "required": True, "max_len": 500000},
            "append": {"type": "bool", "default": False},
        },
    },
    "fs.delete": {
        "desc": "Удалить файл или каталог.",
        "devices": [PC, ANDROID, ROUTER],
        "dangerous": True,
        "full_only": True,
        "args": {
            "path": {"type": "str", "required": True, "max_len": 512},
            "recursive": {"type": "bool", "default": False},
        },
    },
    "fs.mkdir": {
        "desc": "Создать каталог (вместе с родительскими).",
        "devices": [PC, ANDROID, ROUTER],
        "full_only": True,
        "args": {"path": {"type": "str", "required": True, "max_len": 512}},
    },
    "proc.kill": {
        "desc": "Завершить процесс по PID (или по имени на ПК).",
        "devices": [PC, ANDROID],
        "dangerous": True,
        "full_only": True,
        "args": {
            "pid": {"type": "int", "min": 1},
            "name": {"type": "str", "max_len": 128},
            "force": {"type": "bool", "default": False},
        },
    },
    "power.action": {
        "desc": "Управление питанием ПК: shutdown, restart, sleep, hibernate, logoff, cancel.",
        "devices": [PC],
        "dangerous": True,
        "full_only": True,
        "args": {
            "action": {"type": "str", "required": True,
                       "enum": ["shutdown", "restart", "sleep", "hibernate", "logoff", "cancel"]},
            "delay_sec": {"type": "int", "default": 30, "min": 0, "max": 3600},
        },
    },

    # ───────────────────────────── Android ─────────────────────────────
    "battery.status": {
        "desc": "Заряд аккумулятора и состояние питания телефона.",
        "devices": [ANDROID],
        "args": {},
    },
    "sms.list": {
        "desc": "Последние SMS-сообщения.",
        "devices": [ANDROID],
        "args": {"limit": {"type": "int", "default": 5, "min": 1, "max": 50}},
    },
    "sms.send": {
        "desc": "Отправить SMS на номер.",
        "devices": [ANDROID],
        "dangerous": True,
        "args": {
            "number": {"type": "str", "required": True, "max_len": 32},
            "text": {"type": "str", "required": True, "max_len": 480},
        },
    },
    "notify.send": {
        "desc": "Показать уведомление на телефоне.",
        "devices": [ANDROID],
        "args": {
            "title": {"type": "str", "required": True, "max_len": 120},
            "text": {"type": "str", "required": True, "max_len": 480},
        },
    },
    "tts.speak": {
        "desc": "Произнести текст вслух на телефоне.",
        "devices": [ANDROID],
        "args": {"text": {"type": "str", "required": True, "max_len": 480}},
    },
    "torch.set": {
        "desc": "Включить или выключить фонарик.",
        "devices": [ANDROID],
        "args": {"on": {"type": "bool", "required": True}},
    },
    "wifi.info": {
        "desc": "Информация о текущей WiFi-сети (SSID, IP, скорость).",
        "devices": [ANDROID],
        "args": {},
    },
    "location.get": {
        "desc": ("Текущее местоположение телефона (по сети или GPS). В помещении может быть"
                 " недоступно — тогда команда честно сообщит, что точки нет."),
        "devices": [ANDROID],
        "args": {},
    },
    "app.open": {
        "desc": ("Запустить приложение. Указывайте имя пакета (org.telegram.messenger); "
                 "на телефоне через adb можно и русское название («Часы», «Настройки») — "
                 "пакет подберётся автоматически."),
        "devices": [ANDROID, ANDROID_ADB],
        "args": {"package": {"type": "str", "required": True, "max_len": 128}},
    },
    "apps.list": {
        "desc": ("Список установленных приложений Android. third_party=true — только "
                 "пользовательские, false — системные и пользовательские."),
        "devices": [ANDROID],
        "args": {
            "third_party": {"type": "bool", "default": True},
            "limit": {"type": "int", "default": 100, "min": 1, "max": 300},
        },
    },
    "app.info": {
        "desc": "Сведения о приложении: версия, путь APK, дата установки.",
        "devices": [ANDROID],
        "args": {"package": {"type": "str", "required": True, "max_len": 128}},
    },
    "app.stop": {
        "desc": "Принудительно остановить приложение. Несохранённые данные могут пропасть.",
        "devices": [ANDROID],
        "dangerous": True,
        "args": {"package": {"type": "str", "required": True, "max_len": 128}},
    },
    "app.current": {
        "desc": "Какое приложение сейчас на переднем плане (пакет и активность).",
        "devices": [ANDROID, ANDROID_ADB],
        "args": {},
    },

    # ──────────────── Экран Android через adb (агент в ВМ) ────────────────
    "screen.info": {
        "desc": ("Состояние экрана телефона: размер, плотность, включён ли экран, "
                 "какое приложение на переднем плане. Только чтение."),
        "devices": [ANDROID_ADB],
        "args": {},
    },
    "screen.shot": {
        "desc": "Снимок экрана телефона (PNG). Ядро вернёт ссылку на файл.",
        "devices": [ANDROID_ADB],
        "args": {},
    },
    "screen.ui": {
        "desc": ("Разбор текущего экрана телефона: видимые элементы с текстом и "
                 "координатами. Вызывай перед нажатием, чтобы увидеть, что на экране."),
        "devices": [ANDROID_ADB],
        "args": {
            "find": {"type": "str", "max_len": 128,
                     "description": "показать только элементы, содержащие эту подстроку"},
            "limit": {"type": "int", "default": 40, "min": 1, "max": 200},
        },
    },
    "screen.tap": {
        "desc": ("Нажать на экране телефона: по тексту элемента (text) — координаты "
                 "найдутся сами, либо по координатам (x, y)."),
        "devices": [ANDROID_ADB],
        "args": {
            "x": {"type": "int", "min": 0, "description": "координата по горизонтали"},
            "y": {"type": "int", "min": 0, "description": "координата по вертикали"},
            "text": {"type": "str", "max_len": 128,
                     "description": "текст элемента на экране (надёжнее координат)"},
            "long": {"type": "bool", "default": False,
                     "description": "долгое нажатие (удержание)"},
        },
    },
    "screen.swipe": {
        "desc": ("Провести пальцем: по направлению (direction) или по координатам. "
                 "Прокрутка вниз по странице — direction=up."),
        "devices": [ANDROID_ADB],
        "args": {
            "direction": {"type": "str", "enum": ["up", "down", "left", "right"]},
            "x1": {"type": "int", "min": 0},
            "y1": {"type": "int", "min": 0},
            "x2": {"type": "int", "min": 0},
            "y2": {"type": "int", "min": 0},
            "duration": {"type": "int", "default": 300, "min": 50, "max": 3000},
        },
    },
    "screen.text": {
        "desc": ("Ввести текст в активное поле на телефоне (кириллица и латиница — через "
                 "ADBKeyboard). clear=true — сначала очистить поле."),
        "devices": [ANDROID_ADB],
        "args": {
            "text": {"type": "str", "required": True, "max_len": 300},
            "clear": {"type": "bool", "default": False,
                      "description": "очистить поле перед вводом"},
        },
    },
    "screen.key": {
        "desc": ("Нажать кнопку телефона: back, home, recents, enter, wakeup (разбудить "
                 "экран), sleep, power, volume_up, volume_down, delete, tab."),
        "devices": [ANDROID_ADB],
        "args": {
            "key": {"type": "str", "required": True,
                    "enum": ["back", "home", "recents", "enter", "wakeup", "sleep",
                             "power", "volume_up", "volume_down", "delete", "tab"]},
        },
    },

    # ─────────────────────── Рабочий стол ВМ (Astra) ───────────────────────
    "vm.info": {
        "desc": "Сведения об экране ВМ: разрешение, дисплей, пользователь сессии.",
        "devices": [VM],
        "args": {},
    },
    "vm.screenshot": {
        "desc": ("Снимок экрана ВМ (рабочий стол Astra Linux). Ядро вернёт ссылку "
                 "на файл — по ней видно, что открыто и где нажимать."),
        "devices": [VM],
        "args": {},
    },
    "vm.windows": {
        "desc": "Список видимых окон на рабочем столе ВМ с именами и координатами.",
        "devices": [VM],
        "args": {},
    },
    "vm.open": {
        "desc": ("Открыть ссылку или файл на ВМ в приложении по умолчанию "
                 "(например, панель в браузере)."),
        "devices": [VM],
        "args": {"target": {"type": "str", "required": True, "max_len": 512}},
    },
    "vm.panel": {
        "desc": ("Открыть панель ассистента в браузере ВМ. Токен подставляется "
                 "сам — вводить ничего не нужно."),
        "devices": [VM],
        "args": {},
    },
    "vm.move": {
        "desc": "Навести курсор на рабочем столе ВМ (без клика).",
        "devices": [VM],
        "args": {
            "x": {"type": "int", "required": True, "min": 0, "max": 8000},
            "y": {"type": "int", "required": True, "min": 0, "max": 8000},
        },
    },
    "vm.click": {
        "desc": ("Клик мышью на рабочем столе ВМ по координатам (x, y из снимка "
                 "экрана). button: 1 — левая, 3 — правая. count=2 — двойной клик."),
        "devices": [VM],
        "args": {
            "x": {"type": "int", "required": True, "min": 0, "max": 8000},
            "y": {"type": "int", "required": True, "min": 0, "max": 8000},
            "button": {"type": "int", "default": 1, "min": 1, "max": 3},
            "count": {"type": "int", "default": 1, "min": 1, "max": 3},
        },
    },
    "vm.scroll": {
        "desc": "Прокрутить колесом на рабочем столе ВМ: amount>0 — вниз, <0 — вверх.",
        "devices": [VM],
        "args": {
            "amount": {"type": "int", "default": 3, "min": -15, "max": 15},
            "x": {"type": "int", "default": None},
            "y": {"type": "int", "default": None},
        },
    },
    "vm.type": {
        "desc": ("Ввести текст в активное поле на ВМ (кириллица и латиница — "
                 "через буфер обмена)."),
        "devices": [VM],
        "args": {"text": {"type": "str", "required": True, "max_len": 2000}},
    },
    "vm.key": {
        "desc": ("Нажать клавишу или сочетание на ВМ: Return, Escape, Tab, "
                 "BackSpace, Up/Down/Left/Right, F5, ctrl+l, ctrl+alt+t."),
        "devices": [VM],
        "args": {"key": {"type": "str", "required": True, "max_len": 64}},
    },

    # ───────────────────────────── Роутер ─────────────────────────────
    "router.clients": {
        "desc": "Подключённые к роутеру устройства (DHCP-аренды, WiFi-клиенты).",
        "devices": [ROUTER],
        "args": {},
    },
    "router.wifi": {
        "desc": "Включить или выключить радиомодуль WiFi.",
        "devices": [ROUTER],
        "dangerous": True,
        "args": {"enabled": {"type": "bool", "required": True}},
    },
    "router.kick": {
        "desc": "Отключить или заблокировать клиента по MAC-адресу.",
        "devices": [ROUTER],
        "dangerous": True,
        "args": {
            "mac": {"type": "str", "required": True, "max_len": 32},
            "block": {"type": "bool", "default": True},
        },
    },
    "router.reboot": {
        "desc": "Перезагрузить роутер. Связь пропадёт на 1-2 минуты.",
        "devices": [ROUTER],
        "dangerous": True,
        "args": {},
    },
}


def get(action: str) -> dict[str, Any] | None:
    return CATALOG.get(action)


def is_dangerous(action: str) -> bool:
    entry = CATALOG.get(action)
    return bool(entry and entry.get("dangerous"))


def is_full_access_only(action: str) -> bool:
    """Действия, доступные только в режиме ACCESS_MODE=full."""
    entry = CATALOG.get(action)
    return bool(entry and entry.get("full_only"))


def for_device(device_type: str, available: set[str] | None = None,
               full_access: bool = False) -> list[str]:
    """Действия, доступные типу устройства и реально поддерживаемые агентом."""
    names = [
        name
        for name, entry in CATALOG.items()
        if device_type in entry.get("devices", [])
        and (full_access or not entry.get("full_only"))
    ]
    if available is not None:
        names = [n for n in names if n in available]
    return sorted(names)


def describe(action: str) -> str:
    entry = CATALOG.get(action) or {}
    hint = entry.get("desc", "без описания")
    if entry.get("dangerous"):
        hint += " (ОПАСНО: требует подтверждения пользователя)"
    return hint
