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
ROUTER = "router"

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
        "desc": ("Выполнить разрешённую команду в оболочке. Разрешены только "
                 "безобидные диагностические программы из белого списка."),
        "devices": [PC, ANDROID],
        "args": {"command": {"type": "str", "required": True, "max_len": 512}},
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
        "desc": "Текущее местоположение телефона.",
        "devices": [ANDROID],
        "args": {},
    },
    "app.open": {
        "desc": "Запустить приложение по имени пакета, например org.telegram.messenger.",
        "devices": [ANDROID],
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
        "devices": [ANDROID],
        "args": {},
    },
    "screen.shot": {
        "desc": "Снимок экрана телефона. Возвращает ссылку на PNG.",
        "devices": [ANDROID],
        "args": {},
    },
    "ui.dump": {
        "desc": ("Дерево интерфейса: тексты, подписи и координаты центров элементов. "
                 "Помогает понять, что сейчас на экране, и куда нажать."),
        "devices": [ANDROID],
        "args": {"compact": {"type": "bool", "default": True}},
    },
    "screen.tap": {
        "desc": "Коснуться экрана телефона в точке (x, y).",
        "devices": [ANDROID],
        "args": {
            "x": {"type": "int", "required": True, "min": 0, "max": 10000},
            "y": {"type": "int", "required": True, "min": 0, "max": 10000},
        },
    },
    "screen.swipe": {
        "desc": "Свайп по экрану телефона из точки в точку за указанное время.",
        "devices": [ANDROID],
        "args": {
            "x1": {"type": "int", "required": True, "min": 0, "max": 10000},
            "y1": {"type": "int", "required": True, "min": 0, "max": 10000},
            "x2": {"type": "int", "required": True, "min": 0, "max": 10000},
            "y2": {"type": "int", "required": True, "min": 0, "max": 10000},
            "duration_ms": {"type": "int", "default": 300, "min": 50, "max": 5000},
        },
    },
    "screen.text": {
        "desc": "Ввести текст в активное поле. Только латиница и цифры (ограничение Android).",
        "devices": [ANDROID],
        "args": {"text": {"type": "str", "required": True, "max_len": 200}},
    },
    "screen.key": {
        "desc": "Нажать системную кнопку телефона.",
        "devices": [ANDROID],
        "args": {
            "key": {
                "type": "str",
                "required": True,
                "enum": ["home", "back", "recents", "enter", "delete", "power",
                         "volume_up", "volume_down", "wakeup", "sleep", "menu",
                         "search", "play_pause"],
            }
        },
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


def for_device(device_type: str, available: set[str] | None = None) -> list[str]:
    """Действия, доступные типу устройства и реально поддерживаемые агентом."""
    names = [
        name
        for name, entry in CATALOG.items()
        if device_type in entry.get("devices", [])
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
