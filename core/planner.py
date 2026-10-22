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

from . import catalog, documents, models, ui_map
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
6. Для безопасной диагностики (версии, обновления, процессы, диск, сеть, список файлов) используй shell.run — он не требует подтверждения. exec.run — только когда нужно изменить систему или без него действительно не обойтись.
7. Действия с пометкой ОПАСНО ядро дополнительно подтвердит у пользователя.
8. Экран телефона: устройство с типом android-adb управляет экраном Honor по adb (агент работает в ВМ). Чтобы понять, что на экране, вызови screen.ui — он вернёт тексты и координаты; нажимай screen.tap с полем text; прокрутка — screen.swipe direction=up; ввод — screen.text (латиницей). Приложения открывай app.open и называй по-русски («Часы», «Настройки»). Экран должен быть разблокирован, кириллицу вводить нельзя. Если в поле поиска остался старый текст, сначала тапни «Удалить».
9. Инструментов у тебя нет: не читай файлы, не «смотри» изображения и не запускай команды. Все данные — только из результатов действий. Снимок показывай пользователю ссылкой из результата; чтобы понять, что на экране, вызывай screen.ui.
10. Расписание: ты умеешь создавать ежедневные задачи через core::schedule.add. Для отчёта по роутеру используй kind="router_report" и нужное time (например, "10:00"); для произвольной задачи — kind="prompt" и текст в prompt. Если задача с таким title уже есть, она обновится. Посмотреть задачи — core::schedule.list, выполнить сейчас — core::schedule.run, удалить — core::schedule.remove. Не отвечай, что не умеешь ставить расписание: сначала вызови действие. Все времена — Europe/Moscow.
11. Отчёт по роутеру и проверку журнала можно получить действием router.report (только чтение, подтверждение не нужно)."""


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


def _self_text() -> str:
    """Факты о самой ВМ: чтобы модель не выдумывала, где она работает."""
    import platform

    pretty = ""
    version = ""
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if line.startswith("PRETTY_NAME="):
                pretty = line.split("=", 1)[1].strip().strip('"')
            elif line.startswith("VERSION_ID="):
                version = line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    if not pretty:
        pretty = f"{platform.system()} {platform.release()}"
    if version:
        pretty = f"{pretty} {version.replace('_x86-64', '')}"

    memory = ""
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                memory = f"{int(line.split()[1]) / 1024 / 1024:.1f} ГБ"
                break
    except (OSError, ValueError, IndexError):
        pass

    parts = [
        f"Ты — ядро ассистента Net Assistant. Живёшь в виртуальной машине "
        f"на Hyper-V, а не на телефоне, ПК или роутере.",
        f"ОС ядра: {pretty}, ядро {platform.release()}, машина {platform.node()} "
        f"({platform.machine()})",
    ]
    if os.cpu_count():
        parts.append(f"Ресурсы ВМ: {os.cpu_count()} vCPU" + (f", ОЗУ {memory}" if memory else ""))
    parts.append(
        "Панель управления — на этой же машине: http://127.0.0.1:8787 "
        "и https://astra-hub.taile4385b.ts.net (домашняя сеть)."
    )
    parts.append(
        "Устройство vm-desktop («Рабочий стол ВМ») — графическая сессия этой же "
        "ВМ: через него ты открываешь панель, кликаешь и вводишь текст. "
        "ПК (pc-desktop) — отдельная Windows-машина, телефон и роутер — тоже отдельно."
    )
    parts.append(
        "На вопросы о своей ОС отвечай этими данными. Не выдумывай контейнеры, "
        "каталоги или другие системы."
    )
    return "\n".join(parts)


def _devices_text() -> str:
    devices = hub.online_devices()
    lines = [f"- {d.device_id} — {d.name} ({d.platform})" for d in devices]
    lines.append("- core — ядро ассистента (присланные файлы на SSD роутера)")
    return "\n".join(lines) if lines else "(нет устройств онлайн)"


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
        f"О тебе:\n{_self_text()}\n\n"
        f"Устройства:\n{_devices_text()}\n\n"
        f"Действия:\n{_actions_text()}\n\n"
        f"Файлы пользователя:\n{documents.prompt_hint()}\n\n"
    )


# ─────────────────────────── вызов OpenCode ───────────────────────────


def _strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


async def _ask_opencode(prompt: str, attempt: int = 0) -> str:
    binary = binary_path()
    if binary is None:
        raise RuntimeError(
            "не найден CLI opencode; установите его командой "
            "curl -fsSL https://opencode.ai/install | bash"
        )

    text = prompt
    if attempt:
        # Бесплатную модель иногда тянет к инструментам (например, «посмотреть»
        # снимок экрана). Инструментов у неё нет, поэтому повторяем запрос с
        # явным запретом — иначе CLI обрывает ответ на запросе прав.
        text = (
            prompt
            + "\n\nВАЖНО: инструментов у тебя нет — не вызывай Read, Bash, Glob, Edit "
            "и прочее. Ответь только текстом по-русски (или одним JSON-объектом, "
            "если это план действий)."
        )

    model = models.current()
    command = [binary, "run", "-m", model]
    if settings.opencode_agent:
        command += ["--agent", settings.opencode_agent]
    command.append(text)

    log.info("openCode: запрос к %s (%d символов)", model, len(text))
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
        if attempt == 0:
            log.warning("OpenCode вернул пустой ответ, повтор запроса: %s", detail[-200:])
            return await _ask_opencode(prompt, attempt=1)
        raise RuntimeError(
            "OpenCode вернул пустой ответ" + (f": {detail}" if detail else "")
        )
    return answer


# ─────────────────────── распознавание изображений ───────────────────────

VISION_PROMPT = """Посмотри на изображение и опиши его по-русски.

1. Скажи, что изображено: экран телефона, окно программы, фото.
2. Перепиши дословно весь важный текст: сообщения об ошибках, заголовки,
   названия кнопок, адреса, числа. Если это скриншот ошибки — обязательно
   приведи её текст.
3. Если на картинке есть вопрос или просьба — перескажи их.
4. Ответь только текстом. Инструментов у тебя нет: не вызывай Read, Bash,
   Glob и прочее."""


UI_PROMPT = """Это снимок экрана компьютера с интерфейсом программы.

На изображении нанесена координатная сетка: красные линии каждые 100 пикселей,
жёлтые подписи — значения x (сверху) и y (слева). Опирайся на сетку, чтобы
точнее указывать координаты.

Опиши по-русски, что открыто и что можно нажать:
1. Назови активное окно и его содержимое.
2. Перечисли видимые кнопки, поля ввода, ссылки, пункты меню — для каждого
   укажи примерные координаты центра в пикселях: (x=..., y=...) от левого
   верхнего угла изображения.
3. Если видно поле ввода — скажи, что в нём написано.
4. Не вызывай инструменты и не предлагай код. Только текст."""


def _with_grid(path: Path) -> Path:
    """Копия снимка с координатной сеткой — модели проще попадать по кнопкам."""
    from PIL import Image, ImageDraw

    out = path.with_name(f"{path.stem}_grid{path.suffix}")
    image = Image.open(path).convert("RGB")
    draw = ImageDraw.Draw(image)
    width, height = image.size
    for x in range(100, width, 100):
        draw.line([(x, 0), (x, height)], fill=(255, 64, 64), width=1)
        draw.text((x + 2, 2), str(x), fill=(255, 255, 0), stroke_width=1,
                  stroke_fill=(0, 0, 0))
    for y in range(100, height, 100):
        draw.line([(0, y), (width, y)], fill=(255, 64, 64), width=1)
        draw.text((2, y + 1), str(y), fill=(255, 255, 0), stroke_width=1,
                  stroke_fill=(0, 0, 0))
    image.save(out)
    return out


async def describe_image(path: Path, hint: str = "", prompt: str | None = None) -> str:
    """Распознать скриншот/фото моделью со зрением и вернуть описание."""
    binary = binary_path()
    if binary is None:
        raise RuntimeError(
            "не найден CLI opencode — распознавание изображений недоступно"
        )

    prompt = prompt or VISION_PROMPT
    if hint:
        prompt += f"\n\nПодпись пользователя к изображению: {hint}"

    model = settings.vision_model
    # ВАЖНО: сообщение идёт первым. У `--file` массив-аргумент, и если флаг
    # стоит раньше текста, CLI пытается открыть сам текст как файл.
    command = [binary, "run", prompt, "-m", model, f"--file={path}"]
    log.info("openCode vision: %s, файл %s", model, path.name)

    async with _run_lock:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
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
                f"модель зрения не ответила за {settings.opencode_timeout:.0f} с"
            ) from None

    answer = _strip_ansi(stdout.decode("utf-8", "replace"))
    # CLI печатает служебную строку «> build · <модель>» — убираем её.
    lines = [line for line in answer.splitlines()
             if not (line.lstrip().startswith(">") and "·" in line)]
    answer = "\n".join(lines).strip()
    if not answer:
        detail = _strip_ansi(stderr.decode("utf-8", "replace")).strip()[-500:]
        raise RuntimeError(
            "модель зрения вернула пустой ответ" + (f": {detail}" if detail else "")
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
    """Обрезать длинные результаты и убрать локальные пути к файлам.

    Путь к снимку («file») модели не нужен: она либо показывала бы его в ответе,
    либо тянулась бы за инструментом чтения файлов, которого у неё нет.
    """
    if isinstance(value, dict):
        clean = {key: item for key, item in value.items() if key != "file"}
        if "url" in clean:
            return clean
        value = clean
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
                try:
                    result = await hub.dispatch(device, action, args)
                except DeviceOffline:
                    # Агент мог переподключаться после перезапуска ядра.
                    # Даём ему несколько секунд и повторяем один раз.
                    log.info("«%s» офлайн — повтор через 8 с", device)
                    await asyncio.sleep(8)
                    result = await hub.dispatch(device, action, args)
                # Снимок экрана ВМ сразу распознаём моделью зрения: планировщик
                # получает текстовое описание с координатами и может нажимать.
                if action == "vm.screenshot" and isinstance(result, dict) and result.get("file"):
                    try:
                        result = dict(result)
                        shot = Path(result["file"])
                        gridded = await asyncio.to_thread(_with_grid, shot)
                        try:
                            result["description"] = await describe_image(
                                gridded, prompt=UI_PROMPT
                            )
                        finally:
                            gridded.unlink(missing_ok=True)
                        hints = ui_map.as_text()
                        if hints:
                            result["description"] += (
                                "\n\nТочные координаты элементов панели (из браузера, "
                                "нажимай по ним):\n" + hints
                            )
                    except (RuntimeError, OSError) as exc:
                        log.warning("описание снимка ВМ не получено: %s", exc)
                        result = dict(result)
                        result["description_error"] = str(exc)
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

    # Шаги закончились, а модель всё ещё выполняла действия: её «открываю…»
    # — это обещание, а не ответ. Просим итог отдельным запросом без действий.
    try:
        final = await _final_answer(prefix, observations)
    except RuntimeError as exc:
        log.warning("итоговый ответ не получен: %s", exc)
        final = ""
    final = final or reply or _fallback_reply(observations) or "Готово."
    _remember(history, user_text, final)
    return {"reply": final, "executed": executed, "pending": created}


async def _final_answer(prefix: str, observations: list[dict]) -> str:
    """Последний шаг: действия запрещены, нужен только вывод по результатам."""
    prompt = (
        prefix
        + "\nРезультаты уже выполненных действий (JSON):\n"
        + json.dumps(observations, ensure_ascii=False)[:6000]
        + "\n\nДействия больше не выполняй (actions должен быть пустым списком). "
        "Ответь пользователю коротко по-русски: что получилось, с конкретными данными из результатов."
    )
    answer = await _ask_opencode(prompt)
    plan = _parse_plan(answer)
    if plan is None:
        return answer.strip()[:ANSWER_LIMIT]
    return str(plan.get("reply") or "").strip()


def _fallback_reply(observations: list[dict]) -> str:
    failures = [o for o in observations if not o.get("ok")]
    if failures:
        return "Не получилось: " + "; ".join(str(o.get("error") or o.get("status")) for o in failures)
    return "Готово."


def _remember(history: list[dict], user_text: str, reply: str) -> None:
    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": reply})
