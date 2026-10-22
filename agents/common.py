"""Общая основа агента: подключение к ядру, приём и выполнение команд.

Агент сам идёт к ядру (исходящее соединение), поэтому ядру не нужны
открытые порты на устройствах. Соединения автоматически переподключаются.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import platform as platform_mod
import time
import urllib.parse
from typing import Any, Callable

import websockets

from core import protocol
from core.config import settings

log = logging.getLogger("agent")

Handler = Callable[[dict], Any]


class AgentClient:
    def __init__(
        self,
        device_id: str,
        name: str,
        platform: str,
        handlers: dict[str, Handler],
        core_url: str | None = None,
        token: str | None = None,
        max_message_mb: int = 16,
    ) -> None:
        self.device_id = device_id
        self.name = name
        self.platform = platform
        self.handlers = handlers
        # CORE_URLS — адреса через запятую (например tailnet и локальная сеть).
        # Агент по очереди пробует их: если после перезагрузки не поднялся VPN,
        # устройство всё равно остаётся на связи через домашнюю сеть.
        raw = core_url or os.getenv("CORE_URLS") or os.getenv("CORE_URL", "ws://127.0.0.1:8787")
        bases = [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]
        secret = urllib.parse.quote(token or settings.token, safe="")
        self.urls = [f"{base}/ws/agent?token={secret}" for base in bases] or [
            f"ws://127.0.0.1:8787/ws/agent?token={secret}"
        ]
        self.url = self.urls[0]
        self.max_size = max_message_mb * 1024 * 1024
        self._send_lock = asyncio.Lock()
        self._stop = asyncio.Event()
        # Актуальное websocket-соединение: результат долгой команды мог
        # дозревать после переподключения, слать его нужно в живой сокет.
        self._ws = None
        self._tasks: set[asyncio.Task] = set()

    # ─────────────────────────── подключение ───────────────────────────

    async def _send(self, websocket, payload: dict) -> None:
        async with self._send_lock:
            await websocket.send(json.dumps(payload, ensure_ascii=False))

    async def _send_current(self, payload: dict) -> None:
        """Отправить результат в актуальное соединение (или дождаться нового).

        Если старый сокет закрылся, пока команда выполнялась, память о ней
        висит в ядре: пересылаем результат по новому соединению, чтобы ядро
        разбудило ожидающего, а не ждало таймаута.
        """
        last_error: Exception | None = None
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            websocket = self._ws
            if websocket is not None:
                try:
                    await self._send(websocket, payload)
                    return
                except Exception as exc:  # noqa: BLE001 - сокет закрылся под ногами
                    last_error = exc
            await asyncio.sleep(1.0)
        raise last_error or RuntimeError("нет активного соединения с ядром")

    async def _session(self, url: str) -> None:
        async with websockets.connect(
            url,
            ping_interval=20,
            ping_timeout=20,
            max_size=self.max_size,
            open_timeout=15,
        ) as websocket:
            await self._send(websocket, protocol.hello(
                self.device_id, self.name, self.platform, sorted(self.handlers)
            ))
            welcome = json.loads(await asyncio.wait_for(websocket.recv(), timeout=15))
            if welcome.get("type") != "welcome":
                raise RuntimeError(f"ядро отказало: {welcome.get('error', welcome)}")
            log.info("подключено к ядру как «%s» (%d команд)",
                     self.device_id, len(self.handlers))
            self._ws = websocket

            while not self._stop.is_set():
                try:
                    raw = await asyncio.wait_for(websocket.recv(), timeout=45)
                except asyncio.TimeoutError:
                    await self._send(websocket, {"type": "ping"})
                    continue

                message = json.loads(raw)
                kind = message.get("type")

                if kind == "command":
                    task = asyncio.create_task(self._execute(message))
                    # держим ссылку, иначе GC может собрать задачу с ошибкой
                    self._tasks.add(task)
                    task.add_done_callback(
                        lambda done: self._tasks.discard(done))
                elif kind == "ping":
                    await self._send(websocket, {"type": "pong"})
                elif kind == "error":
                    raise RuntimeError(message.get("error", "ошибка ядра"))
        self._ws = None

    async def run_forever(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            connected = False
            for url in self.urls:
                if self._stop.is_set():
                    break
                try:
                    await self._session(url)
                    connected = True
                    backoff = 1.0
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - сеть может отказать как угодно
                    log.warning("нет связи с ядром (%s: %s) по %s",
                                type(exc).__name__, exc, url.split("/ws/")[0])
            if self._stop.is_set():
                break
            await asyncio.sleep(1.0 if connected else backoff)
            backoff = 1.0 if connected else min(backoff * 2, 30.0)

    def stop(self) -> None:
        self._stop.set()

    # ─────────────────────────── выполнение ───────────────────────────

    async def _execute(self, command: dict) -> None:
        action = command.get("action", "")
        args = command.get("args") or {}
        command_id = command.get("id", "?")
        started = time.perf_counter()

        # TTL из ядра: устаревшую команду не выполняем
        ttl = float(command.get("ttl") or 0)
        sent_at = float(command.get("sent_at") or time.time())
        if ttl and time.time() - sent_at > ttl:
            await self._send_current(protocol.result(
                command_id, False, error=f"команда устарела (TTL {ttl:.0f} с)",
                duration_ms=int((time.perf_counter() - started) * 1000)))
            return

        handler = self.handlers.get(action)
        if handler is None:
            await self._send_current(protocol.result(
                command_id, False, error=f"агент не умеет «{action}»"))
            return

        log.info("→ %s %s", action, args or "")
        try:
            if inspect.iscoroutinefunction(handler):
                outcome = await handler(args)
            else:
                # блокирующие команды (subprocess, SSH, файлы) — в отдельный поток
                outcome = await asyncio.to_thread(handler, args)
            if not isinstance(outcome, (dict, list, str, int, float, bool, type(None))):
                outcome = {"value": str(outcome)}
        except Exception as exc:  # noqa: BLE001 - любая ошибка уходит ядру
            log.warning("! %s: %s: %s", action, type(exc).__name__, exc)
            await self._send_current(protocol.result(
                command_id, False, error=f"{type(exc).__name__}: {exc}",
                duration_ms=int((time.perf_counter() - started) * 1000)))
            return

        elapsed = int((time.perf_counter() - started) * 1000)
        log.info("← %s ok за %d мс", action, elapsed)
        await self._send_current(protocol.result(command_id, True, outcome,
                                                 duration_ms=elapsed))

    # ─────────────────────────── утилиты ───────────────────────────

    @staticmethod
    def platform_name() -> str:
        return platform_mod.system().lower()

    def run(self) -> None:
        log_file = settings.audit_path.parent / f"agent-{self.device_id}.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler(log_file, encoding="utf-8"),
            ],
        )
        log.info("агент %s, лог: %s", self.device_id, log_file)
        try:
            asyncio.run(self.run_forever())
        except KeyboardInterrupt:
            log.info("агент остановлен")
