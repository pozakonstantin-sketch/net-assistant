"""Протокол обмена ядро <-> агент и проверка аргументов команд.

Все сообщения — JSON-объекты с полем ``type``::

    агент -> ядро   hello    {device_id, name, platform, version, actions[]}
    ядро  -> агент  welcome  {session, heartbeat}
    ядро  -> агент  command  {id, action, args, ttl}
    агент -> ядро   result   {id, ok, result|error, duration_ms}
    агент -> ядро   ping     {}
    ядро  -> агент  pong     {}

Аргументы команд описываются в :mod:`core.catalog` и строго проверяются
здесь: никакого ``eval``, только разрешённые поля и типы.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

PROTOCOL_VERSION = 1


class ValidationError(ValueError):
    """Аргументы команды не соответствуют схеме."""


# ─────────────────────────── проверка аргументов ───────────────────────────

_TYPES = {
    "str": str,
    "int": int,
    "float": (int, float),
    "bool": bool,
    "list": list,
    "dict": dict,
}


def _check(name: str, rule: dict, value: Any) -> Any:
    expected = rule.get("type", "str")
    py_type = _TYPES[expected]

    if expected in {"int", "float"} and isinstance(value, bool):
        raise ValidationError(f"{name}: ожидалось число, получено bool")

    if expected == "bool" and not isinstance(value, bool):
        # удобно принимать "true"/"on"/"да" от LLM
        if isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on", "да"}:
            value = True
        elif isinstance(value, str) and value.strip().lower() in {"0", "false", "no", "off", "нет"}:
            value = False
        else:
            raise ValidationError(f"{name}: ожидалось true/false")

    if not isinstance(value, py_type):
        raise ValidationError(f"{name}: ожидался тип {expected}, получено {type(value).__name__}")

    if expected == "str":
        if len(value) > rule.get("max_len", 2048):
            raise ValidationError(f"{name}: строка длиннее {rule['max_len']} символов")
        enum = rule.get("enum")
        if enum and value not in enum:
            raise ValidationError(f"{name}: допустимые значения {enum}")
    elif expected in {"int", "float"}:
        if "min" in rule and value < rule["min"]:
            raise ValidationError(f"{name}: значение меньше {rule['min']}")
        if "max" in rule and value > rule["max"]:
            raise ValidationError(f"{name}: значение больше {rule['max']}")
    elif expected == "list":
        if len(value) > rule.get("max_items", 64):
            raise ValidationError(f"{name}: слишком много элементов")
        if rule.get("items") == "str" and not all(isinstance(i, str) for i in value):
            raise ValidationError(f"{name}: ожидался список строк")

    return value


def validate_args(schema: dict[str, dict], args: Any) -> dict[str, Any]:
    """Привести аргументы к схеме: отбросить лишнее, проверить типы, подставить default."""
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ValidationError("args должен быть объектом")

    unknown = set(args) - set(schema)
    if unknown:
        raise ValidationError(f"неизвестные аргументы: {', '.join(sorted(unknown))}")

    result: dict[str, Any] = {}
    for name, rule in schema.items():
        if name in args and args[name] is not None:
            result[name] = _check(name, rule, args[name])
        elif rule.get("required"):
            raise ValidationError(f"не хватает обязательного аргумента: {name}")
        elif "default" in rule:
            result[name] = rule["default"]
    return result


# ─────────────────────────── конструкторы сообщений ───────────────────────────


def new_command(action: str, args: dict, ttl: float = 30.0) -> dict:
    return {
        "type": "command",
        "id": uuid.uuid4().hex[:12],
        "action": action,
        "args": args,
        "ttl": ttl,
        "sent_at": time.time(),
    }


def hello(device_id: str, name: str, platform: str, actions: list[str]) -> dict:
    return {
        "type": "hello",
        "device_id": device_id,
        "name": name,
        "platform": platform,
        "version": PROTOCOL_VERSION,
        "actions": actions,
    }


def result(command_id: str, ok: bool, payload: Any = None, error: str | None = None,
           duration_ms: int = 0) -> dict:
    message: dict[str, Any] = {"type": "result", "id": command_id, "ok": ok,
                               "duration_ms": duration_ms}
    if ok:
        message["result"] = payload
    else:
        message["error"] = error or "неизвестная ошибка"
    return message
