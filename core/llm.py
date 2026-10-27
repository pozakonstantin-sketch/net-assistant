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
7. Экран телефона: инструменты устройства с типом android-adb (screen.ui, screen.tap, screen.swipe, screen.shot) работают через adb из ВМ. Сначала screen.ui — там тексты и координаты; нажимай screen.tap по тексту. Экран должен быть разблокирован.
8. Устройства в сети: net.devices показывает ВСЁ, что видно — агента ассистента, клиентов роутера (LAN) и узлы Tailscale; router.clients — подробнее по LAN, tailscale.peers — подробнее по Tailscale, net.ping <host> — проверка доступности любого адреса (пинг с роутера). Если пользователь спрашивает «что в сети» — сначала net.devices, потом уточняй.
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
        token = secrets.token_urlsafe(16)
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

    def clear(self) -> None:
        """Сбросить все неподтверждённые действия (используется при /reset)."""
        self._items.clear()


confirmations = Confirmations()


# ───────────────────────────── вызов LLM ─────────────────────────────


async def _chat_at(messages: list[dict], tools: list[dict] | None,
                   base_url: str, api_key: str, model: str,
                   timeout: float | None = None,
                   max_tokens: int | None = None,
                   no_think: bool = False) -> dict:
    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if no_think:
        # Отключить скрытый ход мыслей: иначе qwen3 тратит весь бюджет
        # токенов на <think>…</think>, пользователю приходит пустой ответ.
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    url = f"{base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}",
               "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=timeout or settings.llm_timeout) as client:
        response = await client.post(url, json=payload, headers=headers)
        if response.status_code >= 400:
            raise RuntimeError(f"LLM ответила {response.status_code}: {response.text[:400]}")
        data = response.json()
        try:
            ch = data["choices"][0]
            msg = ch.get("message") or {}
            usage = data.get("usage") or {}
            log.info("llm: finish=%s tool_calls=%d content_len=%d prompt_tok=%s gen_tok=%s",
                     ch.get("finish_reason"), len(msg.get("tool_calls") or []),
                     len(msg.get("content") or ""), usage.get("prompt_tokens"),
                     usage.get("completion_tokens"))
        except Exception:
            log.warning("llm: непонятный ответ: %s", str(data)[:300])
        return data


def _is_local(base_url: str | None) -> bool:
    return bool(base_url) and ("127.0.0.1" in base_url or "localhost" in base_url)


def _trim_history(history: list[dict], max_messages: int = 6,
                  max_chars: int = 3500) -> list[dict]:
    """Короткий контекст для локальной модели: ВМ обрабатывает промпт минутами."""
    def size(items):
        return sum(len(str(m.get("content", ""))) for m in items)

    # наблюдения инструментов объёмны: оставляем только два последних
    tool_idx = [i for i, m in enumerate(history) if m.get("role") == "tool"]
    drop_idx = set(tool_idx[:-2])
    slim = [m for i, m in enumerate(history) if i not in drop_idx]

    items = slim[-max_messages:]
    while len(items) > 2 and size(items) > max_chars:
        items = items[1:]
    while items and items[0].get("role") == "tool":
        items = items[1:]
    return items


async def _chat(messages: list[dict], tools: list[dict] | None) -> dict:
    # Нормализация схемы и короткие ответы для локальной модели:
    # llama.cpp строг к типам («int» → «integer») и медленно генерирует.
    if _is_local(settings.llm_base_url):
        mt, to = 700, 600.0  # ВМ обрабатывает промпт минутами
    else:
        mt, to = None, None
    tools = _json_schema_tools(tools or [])
    if mt is not None:
        tools = _compact_tools(tools)
    return await _chat_at(messages, tools,
                          settings.llm_base_url,
                          settings.llm_api_key, settings.llm_model,
                          timeout=to, max_tokens=mt, no_think=mt is not None)


_SCHEMA_TYPES = {"int": "integer", "float": "number", "bool": "boolean",
                 "str": "string", "any": "string"}


def _json_schema_tools(tools: list[dict]) -> list[dict]:
    """Привести схемы аргументов к каноничному JSON Schema.

    Каталог команд использует короткие типы («int»), облачные API это прощают,
    а llama.cpp генерирует грамматику строго по JSON Schema и отвечает
    400 «unrecognized type int» на нестандартное имя типа.
    """
    off = [json.loads(json.dumps(t, ensure_ascii=False)) for t in tools]  # глубокая копия
    stack: list = [((t.get("function") or {}).get("parameters") or {}) for t in off]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if isinstance(node.get("type"), str):
                node["type"] = _SCHEMA_TYPES.get(node["type"], node["type"])
            stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
        elif isinstance(node, list):
            stack.extend(v for v in node if isinstance(v, (dict, list)))
    return off


def _compact_tools(tools: list[dict]) -> list[dict]:
    """Короткие описания инструментов для локальной модели.

    Полные описания каталога дают ~8-10 тыс. токенов промпта, а ВМ
    обрабатывает их по 15 токенов/с — ход занимает десятки минут.
    """
    out: list[dict] = []
    for t in tools:
        t = json.loads(json.dumps(t, ensure_ascii=False))
        fn = t.get("function") or {}
        full = fn.get("description") or ""
        short = full.split(". ")[0].strip()
        if "ОПАСНО" in full:
            short += " (ОПАСНО)"
        if len(short) > 120:
            short = short[:117] + "…"
        fn["description"] = short
        out.append(t)
    return out


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
    max_steps: int | None = None
    if backend == "openai" and _is_local(settings.llm_base_url):
        # Локальная llama как основной планировщик: короткий контекст и
        # минимум шагов, иначе ход занимает десятки минут.
        history = _trim_history(history)
        max_steps = 2
    snapshot = list(history)  # fallback начинает с чистого слепка истории
    remote_error: Exception | None = None
    try:
        if backend == "opencode":
            return await planner.run_turn(user_text, history)
        # openai-совместимый API: идём на удалённый провайдер
        return await _turn_openai(user_text, history, max_steps=max_steps)
    except (httpx.HTTPError, RuntimeError, LLMNotConfigured) as exc:
        remote_error = exc

    # ── Резерв: локальная модель на ВМ (llama.cpp, 127.0.0.1) ──
    try:
        result = await _turn_openai(
            user_text, list(snapshot),
            base_url=settings.llm_local_url,
            api_key="local", model=settings.llm_local_model,
            timeout=480.0, max_steps=2, max_tokens=400,
        )
        audit.write(event="llm_fallback", backend="local", ok=True)
        result["reply"] = f"🧩 (резерв: локальная модель)\n{result.get('reply') or ''}"
        return result
    except (httpx.HTTPError, RuntimeError) as local_exc:
        audit.write(event="llm_fallback", backend="local", ok=False,
                    error=str(local_exc))
        if remote_error is not None:
            raise remote_error from local_exc
        raise LLMNotConfigured(
            "LLM недоступны: облачный провайдер недоступен, а локальной модели "
            "нет на ВМ (нет службы net-assistant-llama)") from local_exc


async def _turn_openai(user_text: str, history: list[dict],
                       base_url: str | None = None, api_key: str | None = None,
                       model: str | None = None, timeout: float | None = None,
                       max_steps: int | None = None,
                       max_tokens: int | None = None) -> dict:
    """Цикл planning-эксплуатации для любого OpenAI-совместимого API.

    base_url=None — штатный облачный провайдер; иное — локальный резерв.
    """
    async def call(messages, tools):
        if base_url is None:
            return await _chat(messages, tools)
        return await _chat_at(messages, _json_schema_tools(tools or []),
                              base_url, api_key, model, timeout,
                              max_tokens=max_tokens, no_think=True)

    if not any(m.get("role") == "system" for m in history):
        history.insert(0, {"role": "system", "content": SYSTEM_PROMPT})

    history.append({"role": "user", "content": user_text})

    executed: list[dict] = []
    created: list[dict] = []

    for step in range(max_steps or MAX_STEPS):
        tools = hub.tools_for_llm()
        log.info("turn %s: шаг %d/%d, инструментов=%d", user_text[:40], step + 1, max_steps or MAX_STEPS, len(tools))
        data = await call(history, tools)
        message = data["choices"][0]["message"]
        tool_calls = message.get("tool_calls") or []
        history.append(_clean(message))

        if not tool_calls:
            return {
                "reply": message.get("content") or "(пустой ответ)",
                "executed": executed,
                "pending": created,
            }

        for tc in tool_calls:
            call_id = tc.get("id", "call_0")
            try:
                device, action, args = _parse_tool(
                    tc["function"]["name"], tc["function"].get("arguments", "")
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
    data = await call(history, None)
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
