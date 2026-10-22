"""Планировщик ежедневных задач — без внешнего cron и без systemd-таймеров.

Зачем внутри ядра: задачи должны уметь обращаться к агентам (роутер, ПК,
телефон), складывать результат в общую историю и присылать его в Telegram.
Внешний cron пришлось бы снабжать токеном, путями и отдельной копией логики.

Задачи лежат в ``data/schedules.json``::

    {"tasks": [{"id": "a1b2c3", "title": "...", "time": "10:00",
                "kind": "router_report", "enabled": true, ...}]}

Поддерживаются два вида:
    router_report — собрать отчёт по роутеру (сеть, диски, прокси, журнал);
    prompt        — выполнить произвольную задачу через планировщик ИИ.

Проверка срабатывания — раз в 15 секунд. Если ядро было выключено в момент
запуска задачи, она выполнится при старте, но только в пределах окна
``SCHEDULE_CATCH_UP_MINUTES`` (по умолчанию 120 минут), чтобы не присылать
вчерашний отчёт вечером.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from . import history as chat_history
from .config import DATA_DIR, canonical_session, settings

log = logging.getLogger("scheduler")

SCHEDULES_PATH = DATA_DIR / "schedules.json"
TICK_SECONDS = 15
DEFAULT_TITLE = "Ежедневный отчёт по роутеру"

WEEKDAY_ALIASES = {
    "mon": 0, "monday": 0, "пн": 0, "понедельник": 0,
    "tue": 1, "tuesday": 1, "вт": 1, "вторник": 1,
    "wed": 2, "wednesday": 2, "ср": 2, "среда": 2,
    "thu": 3, "thursday": 3, "чт": 3, "четверг": 3,
    "fri": 4, "friday": 4, "пт": 4, "пятница": 4,
    "sat": 5, "saturday": 5, "сб": 5, "суббота": 5,
    "sun": 6, "sunday": 6, "вс": 6, "воскресенье": 6,
}
WEEKDAY_SHORT = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")

_lock = threading.RLock()
_task: asyncio.Task | None = None


class ScheduleError(ValueError):
    """Некорректная задача расписания."""


# ─────────────────────────── файл задач ───────────────────────────


def _load() -> list[dict]:
    with _lock:
        if not SCHEDULES_PATH.exists():
            return []
        try:
            data = json.loads(SCHEDULES_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("не удалось прочитать %s: %s", SCHEDULES_PATH, exc)
            return []
    raw = data.get("tasks") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict) and item.get("id")]


def _save(tasks: list[dict]) -> None:
    payload = json.dumps({"tasks": tasks}, ensure_ascii=False, indent=2)
    tmp = SCHEDULES_PATH.with_suffix(".tmp")
    with _lock:
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(SCHEDULES_PATH)
        except OSError as exc:  # noqa: BLE001 - расписание не должно ронять ядро
            log.warning("не удалось сохранить %s: %s", SCHEDULES_PATH, exc)


# ─────────────────────────── время и дни ───────────────────────────


def _zone():
    name = (settings.schedule_tz or "Europe/Moscow").strip()
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - нет tzdata/опечатка: работаем по локальному времени
        log.warning("неизвестная таймзона %s — использую локальное время", name)
        return datetime.now().astimezone().tzinfo


def timezone_name() -> str:
    zone = _zone()
    return getattr(zone, "key", None) or time.strftime("%Z") or "local"


def _norm_time(value: str) -> str:
    match = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", str(value or ""))
    if not match:
        raise ScheduleError("время должно быть в формате ЧЧ:ММ, например 10:00")
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ScheduleError("часы 0–23, минуты 0–59")
    return f"{hour:02d}:{minute:02d}"


def _norm_days(days: list[str] | None) -> list[int]:
    if not days:
        return []
    result: list[int] = []
    for raw in days:
        key = str(raw).strip().lower()
        if key not in WEEKDAY_ALIASES:
            raise ScheduleError(
                f"неизвестный день «{raw}»; используйте mon/tue/wed/thu/fri/sat/sun"
            )
        index = WEEKDAY_ALIASES[key]
        if index not in result:
            result.append(index)
    return sorted(result)


def _days_text(days: list[int]) -> str:
    if not days:
        return "каждый день"
    if set(days) >= {0, 1, 2, 3, 4} and len(days) == 5:
        return "по будням"
    if set(days) == {5, 6}:
        return "по выходным"
    return ", ".join(WEEKDAY_SHORT[i] for i in days if 0 <= i < len(WEEKDAY_SHORT))


def _next_run(task: dict, now: datetime | None = None) -> str:
    now = now or datetime.now(_zone())
    try:
        hour, minute = (int(part) for part in str(task.get("time", "10:00")).split(":"))
    except ValueError:
        return ""
    days = set(task.get("days") or [])
    for offset in range(0, 8):
        day = now.date() + timedelta(days=offset)
        if days and day.weekday() not in days:
            continue
        candidate = datetime(day.year, day.month, day.day, hour, minute,
                             tzinfo=now.tzinfo)
        if candidate > now:
            return candidate.strftime("%d.%m.%Y %H:%M")
    return ""


def _public(task: dict) -> dict:
    days = [int(x) for x in task.get("days") or [] if isinstance(x, int)]
    return {
        "id": task.get("id"),
        "title": task.get("title"),
        "time": task.get("time"),
        "kind": task.get("kind"),
        "prompt": task.get("prompt") or "",
        "days": days,
        "days_text": _days_text(days),
        "enabled": bool(task.get("enabled", True)),
        "last_run": task.get("last_run") or "",
        "last_status": task.get("last_status") or "",
        "last_error": task.get("last_error") or "",
        "next_run": _next_run(task),
    }


# ─────────────────────────── создание и поиск ───────────────────────────


def list_tasks() -> list[dict]:
    return [_public(task) for task in _load()]


def find_task(id_or_title: str) -> dict | None:
    needle = str(id_or_title or "").strip().lower()
    if not needle:
        return None
    tasks = _load()
    for task in tasks:
        if str(task.get("id", "")).lower() == needle:
            return task
    for task in tasks:
        if str(task.get("title", "")).strip().lower() == needle:
            return task
    for task in tasks:
        if needle in str(task.get("title", "")).lower():
            return task
    return None


def add_task(title: str, time_str: str, kind: str = "router_report",
             prompt: str = "", days: list[str] | None = None,
             catch_up_minutes: int | None = None) -> dict:
    title = str(title or "").strip()
    if not title:
        raise ScheduleError("нужно название задачи")
    if len(title) > 128:
        raise ScheduleError("название длиннее 128 символов")

    kind = str(kind or "router_report").strip().lower()
    if kind not in {"router_report", "prompt"}:
        raise ScheduleError("kind должен быть router_report или prompt")

    prompt = str(prompt or "").strip()
    if kind == "prompt" and not prompt:
        raise ScheduleError("для kind=prompt нужен текст prompt")
    if len(prompt) > 2000:
        raise ScheduleError("prompt длиннее 2000 символов")

    at = _norm_time(time_str)
    weekdays = _norm_days(days)
    if catch_up_minutes is None:
        catch_up_minutes = settings.schedule_catch_up_minutes
    catch_up_minutes = max(0, min(int(catch_up_minutes), 24 * 60))

    with _lock:
        tasks = _load()
        existing = next(
            (task for task in tasks
             if str(task.get("title", "")).strip().lower() == title.lower()),
            None,
        )
        task = existing or {
            "id": uuid.uuid4().hex[:6],
            "created": time.time(),
            "last_run": "",
            "last_status": "",
            "last_error": "",
            "last_report_ts": 0,
        }
        task.update({
            "title": title,
            "time": at,
            "kind": kind,
            "prompt": prompt if kind == "prompt" else "",
            "days": weekdays,
            "enabled": True,
            "catch_up_minutes": catch_up_minutes,
        })
        if existing is None:
            tasks.append(task)
        _save(tasks)
    log.info("задача сохранена: %s, %s %s, %s", title, _days_text(weekdays), at, kind)
    return _public(task)


def remove_task(id_or_title: str) -> dict:
    with _lock:
        tasks = _load()
        target = find_task(id_or_title)
        if target is None:
            raise ScheduleError(f"задача «{id_or_title}» не найдена")
        tasks = [task for task in tasks if task.get("id") != target.get("id")]
        _save(tasks)
    log.info("задача удалена: %s", target.get("title"))
    return _public(target)


def set_enabled(id_or_title: str, enabled: bool) -> dict:
    task = find_task(id_or_title)
    if task is None:
        raise ScheduleError(f"задача «{id_or_title}» не найдена")
    with _lock:
        tasks = _load()
        for item in tasks:
            if item.get("id") == task.get("id"):
                item["enabled"] = bool(enabled)
        _save(tasks)
    return _public(task)


def ensure_defaults() -> None:
    """При первом запуске создать ежедневный отчёт в 10:00."""
    if SCHEDULES_PATH.exists():
        return
    try:
        add_task(DEFAULT_TITLE, "10:00", kind="router_report")
        log.info("создана задача по умолчанию: %s в 10:00", DEFAULT_TITLE)
    except ScheduleError as exc:
        log.warning("не удалось создать задачу по умолчанию: %s", exc)


# ─────────────────────────── выполнение ───────────────────────────


async def _collect_router_report() -> dict:
    from .hub import DeviceOffline, hub

    last: Exception | None = None
    # Агент роутера живёт в той же ВМ, но connection мог быть ровно на
    # переподключении (это уже срывало ежедневный отчёт в 10:16): ждём
    # подключения до пары минут, прежде чем признавать отказ честным.
    deadline = asyncio.get_event_loop().time() + 120
    while True:
        try:
            result = await hub.dispatch("router", "router.report",
                                        {"log_lines": 400}, timeout=120)
            break
        except DeviceOffline as exc:
            last = exc
            if asyncio.get_event_loop().time() > deadline:
                raise
            log.info("роутер сейчас не отвечает (%s) — ждём подключения", exc)
            await asyncio.sleep(5)
    if not isinstance(result, dict):
        raise RuntimeError("роутер вернул неожиданный формат отчёта")
    return result


async def _run_prompt(prompt: str) -> str:
    from . import llm

    history = chat_history.llm_messages(canonical_session())
    outcome = await llm.run_turn(prompt, history)
    reply = str(outcome.get("reply") or "").strip()
    return reply or "Задача выполнена, но модель не вернула текст."


def _fmt_size(kilobytes: float) -> str:
    megabytes = float(kilobytes or 0) / 1024
    if megabytes < 1024:
        return f"{megabytes:.0f} МБ"
    return f"{megabytes / 1024:.1f} ГБ"


def _fmt_uptime(seconds: float) -> str:
    total = int(seconds or 0)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts: list[str] = []
    if days:
        parts.append(f"{days} дн.")
    if hours:
        parts.append(f"{hours} ч")
    if minutes or not parts:
        parts.append(f"{minutes} мин")
    return " ".join(parts)


def format_router_report(data: dict, task: dict | None = None,
                         scheduled: bool = False) -> str:
    now = datetime.now(_zone())
    model = str(data.get("model") or "неизвестно")
    wan = data.get("wan") or {}
    internet = data.get("internet") or {}
    dns = data.get("dns") or {}
    proxy = data.get("proxy") or {}
    tailscale = data.get("tailscale") or {}
    clients = data.get("clients") or {}
    logs = data.get("logs") or {}
    memory = data.get("memory") or {}
    disks = data.get("disks") or []

    alerts: list[str] = []
    lines = [
        f"🛰 Отчёт по роутеру — {now:%d.%m.%Y %H:%M}",
        f"Модель: {model}",
        "",
        f"⏱ Аптайм: {data.get('uptime_text') or '—'}",
    ]

    load = data.get("load") or []
    if load:
        lines.append("📊 Нагрузка: " + " / ".join(f"{float(x):.2f}" for x in load[:3]))

    if memory:
        used = float(memory.get("used_mb") or 0)
        total = float(memory.get("total_mb") or 0)
        percent = int(memory.get("percent") or 0)
        lines.append(f"🧠 Память: {used:.0f} / {total:.0f} МБ ({percent}%)")
        if percent >= 90:
            alerts.append(f"память занята на {percent}%")

    if disks:
        parts = []
        for disk in disks:
            label = {"система": "система", "SSD": "SSD", "tmpfs": "tmpfs"}.get(
                str(disk.get("label") or ""), str(disk.get("mount") or "")
            )
            parts.append(
                f"{label} {_fmt_size(float(disk.get('used_kb') or 0))} из "
                f"{_fmt_size(float(disk.get('total_kb') or 0))} "
                f"({int(disk.get('percent') or 0)}%)"
            )
            if str(disk.get("mount") or "") == "/" and int(disk.get("percent") or 0) >= 90:
                alerts.append(f"системный диск занят на {disk.get('percent')}%")
        lines.append("💾 Диски: " + "; ".join(parts))

    lines.append("")
    if wan.get("up"):
        wan_text = str(wan.get("ip") or "без IP")
        if wan.get("device"):
            wan_text += f" ({wan['device']})"
        lines.append(f"🌐 WAN: {wan_text}")
    else:
        lines.append("🌐 WAN: ❌ интерфейс не поднят")
        alerts.append("WAN не поднят")

    if internet.get("ok"):
        latency = internet.get("avg_ms")
        loss = int(internet.get("loss_percent") or 0)
        extra = f", потери {loss}%"
        if latency is not None:
            extra += f", пинг {float(latency):.1f} мс"
        lines.append(f"📡 Интернет: ✅ есть{extra}")
    else:
        loss = int(internet.get("loss_percent") or 100)
        lines.append(f"📡 Интернет: ❌ нет связи (потери {loss}%)")
        alerts.append("нет интернета")

    if dns.get("ok"):
        addresses = ", ".join((dns.get("addresses") or [])[:3])
        lines.append(f"🔎 DNS: ✅ работает ({addresses})")
    else:
        lines.append("🔎 DNS: ❌ не разрешает имена")
        alerts.append("DNS не работает")

    proxy_ok = bool(proxy.get("sing_box")) and bool(proxy.get("nft_table"))
    proxy_text = "✅ работает" if proxy_ok else "❌ не работает"
    details = []
    if proxy.get("sing_box"):
        details.append(f"sing-box pid {proxy.get('singbox')}")
    if proxy.get("nft_table"):
        details.append("nft ok")
    if proxy.get("watchdog"):
        details.append("watchdog ok")
    if proxy.get("fix_loop"):
        details.append("fix-loop ok")
    if details:
        proxy_text += " (" + ", ".join(details) + ")"
    lines.append(f"🛡 Прокси tachyon: {proxy_text}")
    if not proxy.get("sing_box"):
        alerts.append("sing-box не запущен")
    if not proxy.get("nft_table"):
        alerts.append("нет таблицы nft TachyonTable")

    ts_ip = str(tailscale.get("ip") or "")
    if ts_ip:
        extra = ", раздаёт exit-node" if tailscale.get("exit_node") else ""
        lines.append(f"🔗 Tailscale: ✅ {ts_ip}{extra}")
    else:
        lines.append("🔗 Tailscale: ❌ нет адреса")
        alerts.append("Tailscale не отвечает")

    dhcp = int(clients.get("dhcp_leases") or 0)
    wifi = [item for item in (clients.get("wifi") or [])
            if int(item.get("clients") or 0) > 0]
    wifi_total = sum(int(item.get("clients") or 0) for item in wifi)
    wifi_text = ", ".join(
        f"{item.get('interface')}: {item.get('clients')}" for item in wifi[:3]
    )
    lines.append(f"👥 Клиенты: DHCP {dhcp}" + (f", Wi-Fi {wifi_total} ({wifi_text})" if wifi else ""))

    lines.append("")
    checked = int(logs.get("checked") or 0)
    errors = int(logs.get("errors") or 0)
    warnings = int(logs.get("warnings") or 0)
    critical_raw = logs.get("critical_errors")
    critical = int(critical_raw) if critical_raw is not None else errors
    known = logs.get("known") or []
    if checked:
        lines.append(
            f"📜 Журнал: проверено {checked} строк, критичных ошибок {critical}, "
            f"предупреждений {warnings}"
        )
    else:
        lines.append("📜 Журнал: не удалось прочитать logread")
    for item in known[:4]:
        note = str(item.get("note") or "").strip()
        example = ""
        examples = item.get("examples") or []
        if examples and "DNS-rebind" in str(examples[0]):
            sample = str(examples[0])
            example = sample.split(":", 1)[1].strip() if ":" in sample else ""
        suffix = f" Например: {example[:80]}." if example else ""
        lines.append(
            f"  • [известно] {item.get('title')}: {item.get('count')} сообщ. — "
            f"{note}{suffix}"
        )
    for item in (logs.get("top") or [])[:4]:
        message = str(item.get("message") or "").strip().replace("\n", " ")
        source = str(item.get("source") or "").strip()
        count = int(item.get("count") or 1)
        suffix = f" ×{count}" if count > 1 else ""
        lines.append(f"  • [{item.get('level') or '?'}] {source}: {message[:170]}{suffix}")
    if critical:
        alerts.append(f"в журнале {critical} критичных ошибок")

    lines.append("")
    if alerts:
        lines.append("⚠️ Замечания: " + "; ".join(alerts) + ".")
    else:
        tail = " Есть только известные предупреждения." if known else ""
        lines.append("✅ Критичных проблем нет." + tail)

    if task:
        schedule_text = f"Расписание: {_days_text(task.get('days') or [])} в {task.get('time')}"
        lines.append(schedule_text + f" ({timezone_name()})")
    if scheduled:
        lines.append("Если нужны подробности по ошибкам — спросите.")
    return "\n".join(lines)


async def run_task(task: dict, scheduled: bool = False) -> dict:
    """Выполнить задачу и вернуть текст для отправки."""
    kind = str(task.get("kind") or "").strip().lower()
    if kind == "router_report":
        data = await _collect_router_report()
        return {"text": format_router_report(data, task=task, scheduled=scheduled),
                "data": data}
    if kind == "prompt":
        reply = await _run_prompt(str(task.get("prompt") or ""))
        text = f"⏰ Плановая задача «{task.get('title')}»\n\n{reply}"
        return {"text": text, "data": {}}
    raise ScheduleError(f"неизвестный вид задачи: {kind}")


async def run_now(id_or_title: str, deliver: bool = False) -> dict:
    task = find_task(id_or_title)
    if task is None:
        raise ScheduleError(f"задача «{id_or_title}» не найдена")
    outcome = await run_task(task, scheduled=False)
    delivered = await _deliver(outcome["text"]) if deliver else False
    return {
        "task": _public(task),
        "report": outcome["text"],
        "summary": (outcome["data"] or {}).get("summary", ""),
        "delivered": delivered,
    }


# ─────────────────────────── отправка ───────────────────────────


def _owner_chat() -> int | None:
    raw = (settings.schedule_chat_id or "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            log.warning("SCHEDULE_CHAT_ID должен быть числом: %s", raw)
    for item in settings.telegram_chats:
        try:
            return int(item)
        except ValueError:
            continue
    return None


def _split_message(text: str, limit: int = 4000) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in text.splitlines():
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if not current:
            current = line
        elif len(current) + 1 + len(line) <= limit:
            current += "\n" + line
        else:
            chunks.append(current)
            current = line
    if current:
        chunks.append(current)
    return chunks or [""]


async def _deliver(text: str, source: str = "schedule") -> bool:
    """Сохранить отчёт в истории и отправить владельцу в Telegram."""
    chat_history.append(canonical_session(), "assistant", text, source=source)
    chat_id = _owner_chat()
    if not settings.telegram_token or chat_id is None:
        log.info("Telegram не настроен — отчёт только в истории")
        return False

    ok = True
    url = f"https://api.telegram.org/bot{settings.telegram_token}/sendMessage"
    async with httpx.AsyncClient(timeout=30) as client:
        for chunk in _split_message(text):
            payload = {"chat_id": chat_id, "text": chunk,
                       "disable_web_page_preview": True}
            try:
                response = await client.post(url, json=payload)
                if response.status_code >= 400:
                    raise RuntimeError(
                        f"Telegram {response.status_code}: {response.text[:200]}"
                    )
            except Exception as exc:  # noqa: BLE001 - сеть/Telegram могут быть недоступны
                ok = False
                log.warning("не удалось отправить отчёт в Telegram: %s", exc)
                break
    return ok


# ─────────────────────────── цикл ───────────────────────────


def _grace(task: dict) -> timedelta:
    minutes = task.get("catch_up_minutes")
    if minutes is None:
        minutes = settings.schedule_catch_up_minutes
    return timedelta(minutes=max(0, int(minutes)))


def _due_state(task: dict, now: datetime) -> str:
    """Вернуть idle | due | skipped для текущего момента."""
    if not bool(task.get("enabled", True)):
        return "idle"
    today = now.date().isoformat()
    if task.get("last_run") == today:
        return "idle"
    days = set(task.get("days") or [])
    if days and now.weekday() not in days:
        return "idle"
    try:
        hour, minute = (int(part) for part in str(task.get("time", "")).split(":"))
    except ValueError:
        return "idle"
    due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now < due:
        return "idle"
    return "due" if now - due <= _grace(task) else "skipped"


def _apply_updates(updates: dict[str, dict]) -> None:
    """Сохранить поля выполнения, не затерев параллельные правки задач.

    tick держит список задач снятым задолго до завершения (run_task может
    занимать минуту): за это время владелец мог добавить или удалить задачу
    через чат/API. Повторно читаем файл и обновляем только поля выполнения.
    """
    if not updates:
        return
    fresh = {str(task.get("id")): task for task in _load()}
    for task_id, update in updates.items():
        task = fresh.get(task_id)
        if task is not None:
            task.update(update)
    _save(list(fresh.values()))


async def tick(now: datetime | None = None) -> None:
    now = now or datetime.now(_zone())
    tasks = _load()
    updates: dict[str, dict] = {}
    for task in tasks:
        state = _due_state(task, now)
        if state == "idle":
            continue
        today = now.date().isoformat()
        if state == "skipped":
            updates[task["id"]] = {
                "last_run": today,
                "last_status": "skipped",
                "last_error": "пропущено: ядро было выключено",
            }
            continue

        updates.setdefault(task["id"], {})["last_status"] = "running"
        updates.setdefault(task["id"], {})["last_error"] = ""
        _apply_updates(updates)
        try:
            outcome = await run_task(task, scheduled=True)
            delivered = await _deliver(outcome["text"])
            task["last_status"] = "ok" if delivered else "ok-no-telegram"
            task["last_error"] = "" if delivered else "Telegram недоступен"
            task["last_report_ts"] = time.time()
            log.info("задача «%s» выполнена (Telegram: %s)",
                     task.get("title"), "да" if delivered else "нет")
        except Exception as exc:  # noqa: BLE001 - одна задача не должна останавливать цикл
            log.exception("задача «%s» не выполнена", task.get("title"))
            task["last_status"] = "error"
            task["last_error"] = str(exc)[:300]
            await _deliver(
                f"⚠️ Задача «{task.get('title')}» не выполнена: {exc}"
            )
        task["last_run"] = today
        updates[task["id"]] = {
            key: task[key]
            for key in ("last_run", "last_status", "last_error", "last_report_ts")
            if key in task
        }
    _apply_updates(updates)


async def _loop() -> None:
    if not settings.schedule_enabled:
        log.info("планировщик выключен (SCHEDULE_ENABLED=false)")
        return
    log.info("планировщик запущен: %s, файл %s", timezone_name(), SCHEDULES_PATH)
    await asyncio.sleep(5)
    while True:
        try:
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - цикл должен жить
            log.exception("ошибка планировщика")
        await asyncio.sleep(TICK_SECONDS)


def start() -> asyncio.Task:
    global _task
    ensure_defaults()
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop(), name="net-assistant-scheduler")
    return _task


async def stop(task: asyncio.Task | None = None) -> None:
    target = task or _task
    if target is None or target.done():
        return
    target.cancel()
    try:
        await target
    except asyncio.CancelledError:
        pass


# ─────────────────────────── действия для ИИ и API ───────────────────────────


async def core_action(action: str, args: dict) -> dict:
    """Локальные действия ``core::schedule.*``."""
    if action == "schedule.list":
        return {"tasks": list_tasks(), "timezone": timezone_name()}

    if action == "schedule.add":
        task = add_task(
            title=str(args.get("title") or ""),
            time_str=str(args.get("time") or ""),
            kind=str(args.get("kind") or "router_report"),
            prompt=str(args.get("prompt") or ""),
            days=args.get("days") or [],
        )
        return {
            "status": "ok",
            "task": task,
            "message": (f"Задача «{task['title']}» сохранена: "
                        f"{task['days_text']} в {task['time']} ({timezone_name()})."),
        }

    if action == "schedule.remove":
        task = remove_task(str(args.get("id_or_title") or ""))
        return {"status": "ok", "removed": task,
                "message": f"Задача «{task['title']}» удалена."}

    if action == "schedule.run":
        outcome = await run_now(str(args.get("id_or_title") or ""),
                                deliver=bool(args.get("deliver")))
        return {"status": "ok", **outcome}

    raise ScheduleError(f"неизвестное действие расписания: {action}")
