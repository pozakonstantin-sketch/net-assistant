"""Планировщик на бесплатных моделях OpenCode.

Бесплатные модели OpenCode отдают ответ только запросам «изнутри OpenCode»,
поэтому ядро не ходит в шлюз напрямую, а запускает CLI `opencode run`, который
установлен на этой же машине.

Модель получает список устройств и разрешённых действий, а возвращает строгий
JSON-план:

    {"reply": "короткий ответ", "actions": [{"device": "...", "action": "...", "args": {}}]}

Ядро само проверяет план по каталогу, спрашивает подтверждение на опасные
действия и исполняет остальное. Модель ничего не выполняет самостоятельно.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any

from . import catalog
from .audit import audit
from .config import DATA_DIR, settings
from .hub import CommandFailed, DeviceOffline, hub

log = logging.getLogger("planner")

MAX_STEPS = 3
MAX_ACTIONS_PER_STEP = 6
HISTORY_MESSAGES = 8
OBSERVATION_LIMIT = 1500
ANSWER_LIMIT = 4000

# OpenCode CLI — тяжёлый процесс: не даём запускам конкурировать между собой.
_run_lock = asyncio.Lock()

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")

SYSTEM_PROMPT = """Ты — планировщик домашнего ассистента. Ты управляешь устройствами пользователя только через перечисленные ниже действия.

Правила:
1. Ответ — РОВНО один JSON-объект. Без markdown, без текста до и после.
2. Формат: {"reply": "короткий ответ пользователю по-русски", "actions": [{"device": "id", "action": "имя", "args": {}}]}
3. Нужны данные с устройства — укажи действие. Никогда не выдумывай результат.
4. Действия не нужны — верни пустой список: "actions": [].
5. Аргументы указывай строго по схеме действия, лишние поля не добавляй.
6. Действия с пометкой ОПАСНО ядро дополнительно подтвердит у пользователя."""


# ─────────────────────────── доступность CLI ───────────────────────────


def binary_path() -> str | None:
    candidates = [
        settings.opencode_bin or "",
        "opencode",
        "~/.opencode/bin/opencode",
        "~/.local/bin/opencode",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        found = shutil.which(candidate)
        if found:
            return found
        expanded = Path(os.path.expanduser(candidate))
        if expanded.is_file() and os.access(expanded, os.X_OK):
            return str(expanded)
    return None


def available() -> bool:
    return binary_path() is not None


def workdir() -> Path:
    path = Path(settings.opencode_dir) if settings.opencode_dir else DATA_DIR / "planner"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ─────────────────────────── описание возможностей ───────────────────────────


def _devices_text() -> str:
    devices = hub.online_devices()
    if not devices:
        return "(нет устройств онлайн)"
    return "\n".join(f"- {d.device_id} — {d.name} ({d.platform})" for d in devices)


def _actions_text() -> str:
    lines: list[str] = []
    for tool in hub.tools_for_llm():
        function = tool["function"]
        device_id, action = function["name"].split("::", 1)
        parameters = function["parameters"].get("properties", {})
        required = set(function["parameters"].get("required", []))
        arguments = ", ".join(
            f"{name}{'' if name in required else '?'}: {rule.get('type', 'str')}"
            for name, rule in parameters.items()
        )
        entry = catalog.get(action) or {}
        danger = " [ОПАСНО]" if entry.get("dangerous") else ""
        lines.append(f"- {device_id}::{action}({arguments}) — {function['description']}{danger}")
    return "\n".join(lines) if lines else "(нет доступных действий)"


def _render_history(history: list[dict]) -> str:
    parts: list[str] = []
    for message in history[-HISTORY_MESSAGES:]:
        role = message.get("role")
        content = message.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str) or not content.strip():
            continue
        who = "Пользователь" if role == "user" else "Ассистент"
        parts.append(f"{who}: {content.strip()[:500]}")
    return "\n".join(parts)


def _base_prompt() -> str:
    return (
        f"{SYSTEM_PROMPT}\n\n"
        f"Устройства:\n{_devices_text()}\n\n"
        f"Действия:\n{_actions_text()}\n\n"
    )


# ─────────────────────────── вызов OpenCode ───────────────────────────


def _strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


async def _ask_opencode(prompt: str) -> str:
    binary = binary_path()
    if binary is None:
        raise RuntimeError(
            "не найден CLI opencode; установите его командой "
            "curl -fsSL https://opencode.ai/install | bash"
        )

    command = [binary, "run", "-m", settings.opencode_model]
    if settings.opencode_agent:
        command += ["--agent", settings.opencode_agent]
    command.append(prompt)

    log.info("openCode: запрос к %s (%d символов)", settings.opencode_model, len(prompt))
    async with _run_lock:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,  # без этого CLI ждёт ввода и висит
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workdir()),
            env=dict(os.environ),
            start_new_session=True,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=settings.opencode_timeout
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError(
                f"OpenCode не ответил за {settings.opencode_timeout:.0f} с"
            ) from None

    answer = _strip_ansi(stdout.decode("utf-8", "replace")).strip()
    if not answer:
        detail = _strip_ansi(stderr.decode("utf-8", "replace")).strip()[-500:]
        raise RuntimeError(
            "OpenCode вернул пустой ответ" + (f": {detail}" if detail else "")
        )
    return answer


# ─────────────────────────── разбор плана ───────────────────────────


def _first_json_object(text: str) -> str | None:
    """Вырезать первый сбалансированный JSON-объект."""
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start:index + 1]
        start = text.find("{", start + 1)
    return None


def _parse_plan(answer: str) -> dict | None:
    candidate = _first_json_object(answer)
    if candidate is None:
        return None
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _trim(value: Any) -> Any:
    if isinstance(value, dict) and "url" in value:
        return value
    dump = json.dumps(value, ensure_ascii=False, default=str)
    if len(dump) <= OBSERVATION_LIMIT:
        return value
    return dump[:OBSERVATION_LIMIT] + "…"


# ─────────────────────────── основной цикл ───────────────────────────


async def run_turn(user_text: str, history: list[dict]) -> dict:
    # подтверждения живут в llm.py; импорт внутри функции, чтобы не зацикливать модули
    from . import llm

    base = _base_prompt()
    dialogue = _render_history(history)
    prefix = f"{base}{dialogue + chr(10) if dialogue else ''}Пользователь: {user_text}\n"

    executed: list[dict] = []
    created: list[dict] = []
    observations: list[dict] = []
    reply = ""

    for step in range(MAX_STEPS):
        if step == 0:
            prompt = prefix
        else:
            prompt = (
                prefix
                + "\nРезультаты уже выполненных действий (JSON):\n"
                + json.dumps(observations, ensure_ascii=False)[:6000]
                + "\n\nОтветь пользователю коротко по-русски. Если нужны ещё действия — "
                "верни их в actions, иначе верни пустой список."
            )

        answer = await _ask_opencode(prompt)
        plan = _parse_plan(answer)
        if plan is None:
            log.info("OpenCode вернул не JSON, отдаю как ответ")
            clean = answer.strip()[:ANSWER_LIMIT] or "(пустой ответ)"
            _remember(history, user_text, clean)
            return {"reply": clean, "executed": executed, "pending": created}

        plan_reply = str(plan.get("reply") or "").strip()
        if plan_reply:
            reply = plan_reply

        actions = plan.get("actions")
        if not isinstance(actions, list) or not actions:
            final = reply or "Готово."
            _remember(history, user_text, final)
            return {"reply": final, "executed": executed, "pending": created}

        observations: list[dict] = []
        for raw_action in actions[:MAX_ACTIONS_PER_STEP]:
            if not isinstance(raw_action, dict):
                continue
            device = str(raw_action.get("device") or "").strip()
            action = str(raw_action.get("action") or "").strip()
            args = raw_action.get("args")
            if not isinstance(args, dict):
                args = {}

            entry = catalog.get(action)
            if entry is None:
                observations.append({"ok": False, "device": device, "action": action,
                                     "error": "такого действия нет в каталоге"})
                continue

            if settings.require_confirm and entry.get("dangerous"):
                item = llm.confirmations.create(device, action, args)
                created.append({"token": item.token, "device": device,
                                "action": action, "args": args})
                audit.write(event="pending", device=device, action=action, args=args)
                observations.append({
                    "ok": False, "device": device, "action": action,
                    "status": "awaiting_confirmation", "token": item.token,
                    "message": f"действие опасное, нужно подтверждение токеном {item.token}",
                })
                continue

            try:
                result = await hub.dispatch(device, action, args)
                observations.append({"ok": True, "device": device, "action": action,
                                     "result": _trim(result)})
                executed.append({"device": device, "action": action, "args": args,
                                 "ok": True, "result": result})
            except (DeviceOffline, CommandFailed, ValueError) as exc:
                audit.write(event="error", device=device, action=action, error=str(exc))
                observations.append({"ok": False, "device": device, "action": action,
                                     "error": str(exc)})
                executed.append({"device": device, "action": action, "args": args,
                                 "ok": False, "result": {"error": str(exc)}})

        if not reply:
            reply = _fallback_reply(observations)

    final = reply or "Готово."
    _remember(history, user_text, final)
    return {"reply": final, "executed": executed, "pending": created}


def _fallback_reply(observations: list[dict]) -> str:
    failures = [o for o in observations if not o.get("ok")]
    if failures:
        return "Не получилось: " + "; ".join(str(o.get("error") or o.get("status")) for o in failures)
    return "Готово."


def _remember(history: list[dict], user_text: str, reply: str) -> None:
    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": reply})
