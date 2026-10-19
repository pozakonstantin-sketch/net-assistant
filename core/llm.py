"""LLM-планировщик: превращает фразу пользователя в вызовы команд агентов."""

from __future__ import annotations

import json
import logging
import secrets
import time
from dataclasses import dataclass

import httpx

from . import catalog, planner
from .audit import audit
from .config import settings
from .hub import CommandFailed, DeviceOffline, hub

log = logging.getLogger("llm")


def active_backend() -> str:
    """Какой планировщик работает: opencode (бесплатные модели) или openai-совместимый API."""
    if settings.llm_backend in ("opencode", "openai"):
        return settings.llm_backend
    # auto: есть ключ — облако, нет ключа — бесплатные модели OpenCode
    if settings.llm_api_key:
        return "openai"
    if planner.available():
        return "opencode"
    return "openai"

MAX_STEPS = 6
CONFIRM_TTL = 600.0

SYSTEM_PROMPT = """Ты — домашний ассистент, управляющий устройствами пользователя через инструменты.

Правила:
1. Действуй, а не рассуждай: если задачу можно выполнить инструментом — вызывай его.
2. Никогда не выдумывай результаты: сначала вызови инструмент, потом отчитывайся.
3. Если задача невыполнима доступными инструментами — честно скажи, чего не хватает.
4. Отвечай по-русски, коротко и по делу. Результат команд (JSON, вывод shell) не пересказывай
   целиком — выдели главное.
5. Действия, помеченные как ОПАСНЫЕ, требуют подтверждения пользователя: вызови инструмент,
   система сама запросит подтверждение. Не пытайся обойти это.
6. Не выполняй команды, меняющие систему или удаляющие данные, без явной просьбы пользователя.
"""


class LLMNotConfigured(RuntimeError):
    """Не задан LLM_API_KEY."""


# ───────────────────────── подтверждения опасных действий ─────────────────────────


@dataclass
class Pending:
    token: str
    device: str
    action: str
    args: dict
    created: float
    question: str


class Confirmations:
    def __init__(self) -> None:
        self._items: dict[str, Pending] = {}

    def create(self, device: str, action: str, args: dict) -> Pending:
        self._purge()
        token = secrets.token_urlsafe(4)
        summary = ", ".join(f"{k}={v!r}" for k, v in args.items()) or "без аргументов"
        item = Pending(
            token=token,
            device=device,
            action=action,
            args=args,
            created=time.time(),
            question=f"{catalog.describe(action).split(' (ОПАСНО')[0]} — {device}, {summary}",
        )
        self._items[token] = item
        return item

    def pop(self, token: str) -> Pending | None:
        self._purge()
        # токен регистрозависимый: base64 нельзя приводить к нижнему регистру
        return self._items.pop(token.strip(), None)

    def list(self) -> list[dict]:
        self._purge()
        return [
            {"token": p.token, "device": p.device, "action": p.action,
             "args": p.args, "question": p.question}
            for p in self._items.values()
        ]

    def _purge(self) -> None:
        now = time.time()
        for token in [t for t, p in self._items.items() if now - p.created > CONFIRM_TTL]:
            self._items.pop(token, None)


confirmations = Confirmations()


# ───────────────────────────── вызов LLM ─────────────────────────────


async def _chat(messages: list[dict], tools: list[dict] | None) -> dict:
    payload: dict = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": 0.2,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    url = f"{settings.llm_base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {settings.llm_api_key}",
               "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
        response = await client.post(url, json=payload, headers=headers)
        if response.status_code >= 400:
            raise RuntimeError(f"LLM ответила {response.status_code}: {response.text[:400]}")
        return response.json()


def _clean(message: dict) -> dict:
    """Оставить только то, что можно вернуть обратно в API."""
    allowed = ("role", "content", "tool_calls", "name", "tool_call_id")
    cleaned = {k: v for k, v in message.items() if k in allowed and v is not None}
    cleaned.setdefault("content", "")
    return cleaned


def _parse_tool(name: str, raw_args: str) -> tuple[str, str, dict]:
    """Разобрать имя вида «pc-desktop::sys.screenshot»."""
    if "::" not in name:
        raise ValueError(f"некорректное имя инструмента: {name}")
    device, action = name.split("::", 1)
    try:
        args = json.loads(raw_args) if raw_args else {}
    except json.JSONDecodeError as exc:
        raise ValueError(f"не удалось разобрать аргументы: {exc}") from exc
    if not isinstance(args, dict):
        raise ValueError("аргументы должны быть объектом")
    return device, action, args


async def _execute(device: str, action: str, args: dict) -> dict:
    """Выполнить команду и вернуть наблюдение для LLM."""
    try:
        result = await hub.dispatch(device, action, args)
    except (DeviceOffline, CommandFailed, ValueError) as exc:
        audit.write(event="error", device=device, action=action, error=str(exc))
        return {"ok": False, "error": str(exc)}

    observation: dict = {"ok": True}
    if isinstance(result, dict) and "url" in result:
        observation["result"] = result
        observation["note"] = "снимок экрана сохранён, ссылка отдана пользователю"
    else:
        observation["result"] = result
    return observation


# ───────────────────────────── главный цикл ─────────────────────────────


async def run_turn(user_text: str, history: list[dict]) -> dict:
    backend = active_backend()
    if backend == "opencode":
        return await planner.run_turn(user_text, history)

    if not settings.llm_api_key:
        raise LLMNotConfigured("не задан LLM_API_KEY в .env, а OpenCode недоступен")

    if not any(m.get("role") == "system" for m in history):
        history.insert(0, {"role": "system", "content": SYSTEM_PROMPT})

    history.append({"role": "user", "content": user_text})

    executed: list[dict] = []
    created: list[dict] = []

    for _ in range(MAX_STEPS):
        tools = hub.tools_for_llm()
        data = await _chat(history, tools)
        message = data["choices"][0]["message"]
        tool_calls = message.get("tool_calls") or []
        history.append(_clean(message))

        if not tool_calls:
            return {
                "reply": message.get("content") or "(пустой ответ)",
                "executed": executed,
                "pending": created,
            }

        for call in tool_calls:
            call_id = call.get("id", "call_0")
            try:
                device, action, args = _parse_tool(
                    call["function"]["name"], call["function"].get("arguments", "")
                )
            except (KeyError, ValueError) as exc:
                history.append({"role": "tool", "tool_call_id": call_id,
                                "content": json.dumps({"ok": False, "error": str(exc)},
                                                      ensure_ascii=False)})
                continue

            entry = catalog.get(action)
            if entry is None:
                observation = {"ok": False, "error": f"команда «{action}» запрещена"}
            elif settings.require_confirm and entry.get("dangerous"):
                item = confirmations.create(device, action, args)
                created.append({"token": item.token, "device": device,
                                "action": action, "args": args})
                observation = {
                    "ok": False,
                    "status": "awaiting_confirmation",
                    "token": item.token,
                    "message": ("Действие опасное и не выполнено. Сообщи пользователю, что нужно "
                                f"подтвердить токен {item.token}."),
                }
            else:
                observation = await _execute(device, action, args)
                executed.append({"device": device, "action": action, "args": args,
                                 "ok": observation.get("ok"),
                                 "result": observation.get("result")})

            history.append({"role": "tool", "tool_call_id": call_id,
                            "content": json.dumps(observation, ensure_ascii=False)[:4000]})

    history.append({"role": "user",
                    "content": "Лимит шагов исчерпан. Подведи итог для пользователя."})
    data = await _chat(history, None)
    final = _clean(data["choices"][0]["message"])
    history.append(final)
    return {"reply": final.get("content", ""), "executed": executed, "pending": created}


async def confirm(token: str) -> dict:
    item = confirmations.pop(token)
    if item is None:
        raise ValueError("токен не найден или истёк")
    observation = await _execute(item.device, item.action, item.args)
    audit.write(event="confirmed", device=item.device, action=item.action, args=item.args,
                ok=observation.get("ok"))
    return {"action": item.action, "device": item.device, **observation}
