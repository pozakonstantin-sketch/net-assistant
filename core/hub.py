"""Хаб: держит соединения с агентами и раздаёт им команды."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from . import catalog, documents, scheduler
from .audit import audit
from .config import settings
from .files import attach_artifacts
from .protocol import new_command, validate_args

log = logging.getLogger("hub")

HEARTBEAT_SECONDS = 20.0


class DeviceOffline(RuntimeError):
    pass


class CommandFailed(RuntimeError):
    pass


@dataclass
class Device:
    device_id: str
    name: str
    platform: str
    actions: set[str] = field(default_factory=set)
    connected_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    send: Any = None  # callable для отправки JSON агенту (задаётся в main.py)

    @property
    def online(self) -> bool:
        return self.send is not None

    def public(self) -> dict:
        return {
            "device_id": self.device_id,
            "name": self.name,
            "platform": self.platform,
            "online": self.online,
            "actions": sorted(self.actions),
            "last_seen": self.last_seen,
        }


class Hub:
    def __init__(self) -> None:
        self.devices: dict[str, Device] = {}
        self._pending: dict[str, asyncio.Future] = {}

    # ─────────────────────────── регистрация ───────────────────────────

    def register(self, device: Device) -> None:
        known = self.devices.get(device.device_id)
        if known and known.online:
            log.warning("устройство %s переподключилось, старое соединение теряется",
                        device.device_id)
        self.devices[device.device_id] = device

    def unregister(self, device_id: str, send: Any) -> None:
        device = self.devices.get(device_id)
        if device and device.send is send:
            device.send = None
            log.info("устройство %s отключилось", device_id)

    def resolve(self, device_id: str) -> Device:
        device = self.devices.get(device_id)
        if device is None:
            raise DeviceOffline(f"устройство «{device_id}» неизвестно")
        if not device.online:
            raise DeviceOffline(f"устройство «{device_id}» офлайн")
        return device

    def online_devices(self) -> list[Device]:
        return [d for d in self.devices.values() if d.online]

    # ─────────────────────────── исполнение ───────────────────────────

    async def dispatch(self, device_id: str, action: str, args: Any,
                       timeout: float | None = None) -> dict:
        """Проверить команду, отправить агенту и дождаться результата."""
        if device_id == catalog.CORE:
            return await self._local(action, args)

        device = self.resolve(device_id)

        entry = catalog.get(action)
        if entry is None:
            raise ValueError(f"команда «{action}» не разрешена каталогом")
        if device.platform not in entry.get("devices", []):
            raise ValueError(f"устройство «{device_id}» не поддерживает «{action}»")
        if action not in device.actions:
            raise ValueError(f"агент «{device_id}» не умеет «{action}»")
        if entry.get("full_only") and settings.access_mode != "full":
            raise ValueError(
                f"команда «{action}» доступна только при ACCESS_MODE=full"
            )

        clean_args = validate_args(entry["args"], args)

        command = new_command(action, clean_args, ttl=settings.command_timeout)
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[command["id"]] = future

        audit.write(event="command", device=device_id, action=action, args=clean_args,
                    command_id=command["id"])
        started = time.perf_counter()
        try:
            try:
                await device.send(command)
            except Exception as exc:  # noqa: BLE001 - соединение могло оборваться
                # WS-агент отвалился ровно в момент отправки: у сообщения
                # об ошибке не должно быть причины «ConnectionClosed», которую
                # никто не понимает — это тот самый DeviceOffline.
                raise DeviceOffline(
                    f"агент «{device_id}» недоступен ({type(exc).__name__})") from exc
            payload = await asyncio.wait_for(future, timeout or settings.command_timeout)
        except asyncio.TimeoutError:
            audit.write(event="timeout", device=device_id, action=action)
            raise CommandFailed(f"«{action}» не ответил за {timeout or settings.command_timeout:.0f} с")
        finally:
            self._pending.pop(command["id"], None)

        elapsed = int((time.perf_counter() - started) * 1000)
        audit.write(event="result", device=device_id, action=action,
                    ok=payload.get("ok"), duration_ms=payload.get("duration_ms", elapsed))
        if not payload.get("ok"):
            raise CommandFailed(payload.get("error", "агент вернул ошибку"))
        return attach_artifacts(payload.get("result"))

    def complete(self, message: dict) -> None:
        """Агент прислал результат — разбудить ожидающего."""
        future = self._pending.get(message.get("id", ""))
        if future and not future.done():
            future.set_result(message)

    async def _local(self, action: str, args: Any) -> dict:
        """Действия самого ядра (файлы пользователя), без агента и сети."""
        entry = catalog.get(action)
        if entry is None or catalog.CORE not in entry.get("devices", []):
            raise ValueError(f"команда «{action}» не разрешена каталогом")
        clean_args = validate_args(entry["args"], args)
        audit.write(event="command", device=catalog.CORE, action=action, args=clean_args)
        started = time.perf_counter()
        if action.startswith("schedule."):
            try:
                result = await scheduler.core_action(action, clean_args)
            except scheduler.ScheduleError as exc:
                audit.write(event="error", device=catalog.CORE, action=action, error=str(exc))
                raise ValueError(str(exc)) from exc
        else:
            try:
                result = await asyncio.to_thread(documents.core_action, action, clean_args)
            except documents.FileError as exc:
                audit.write(event="error", device=catalog.CORE, action=action, error=str(exc))
                raise ValueError(str(exc)) from exc
        audit.write(event="result", device=catalog.CORE, action=action, ok=True,
                    duration_ms=int((time.perf_counter() - started) * 1000))
        return result

    def mark_seen(self, device_id: str) -> None:
        device = self.devices.get(device_id)
        if device:
            device.last_seen = time.time()

    # ─────────────────────────── для LLM ───────────────────────────

    def tools_for_llm(self) -> list[dict]:
        """OpenAI-совместимое описание инструментов по онлайн-устройствам."""
        full = settings.access_mode == "full"
        tools: list[dict] = []
        for device in self.online_devices():
            for action in catalog.for_device(device.platform, device.actions, full_access=full):
                tools.append(self._tool(device.device_id, device.name, action))
        for action in catalog.for_device(catalog.CORE):
            tools.append(self._tool(catalog.CORE, "ядро (файлы)", action))
        return tools

    @staticmethod
    def _tool(device_id: str, device_name: str, action: str) -> dict:
        entry = catalog.CATALOG[action]
        properties = {
            name: {"type": rule.get("type", "str") if rule.get("type") != "list" else "array",
                   "description": rule.get("description", "")}
            for name, rule in entry["args"].items()
        }
        return {
            "type": "function",
            "function": {
                "name": f"{device_id}::{action}",
                "description": f"[{device_name}] {catalog.describe(action)}",
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": [n for n, r in entry["args"].items() if r.get("required")],
                },
            },
        }


hub = Hub()
