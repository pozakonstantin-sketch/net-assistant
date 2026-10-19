"""Ядро ассистента: HTTP API + WebSocket-приём агентов.

Запуск:
    python -m core.main
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import secrets
import tarfile
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import catalog, llm, voice
from .audit import audit
from .config import BASE_DIR, settings
from .files import SHOTS_DIR
from .hub import CommandFailed, Device, DeviceOffline, hub

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("core")

CONFIRM_RE = re.compile(r"^\s*(?:подтверждаю|подтвердить|confirm)\s+([A-Za-z0-9_\-]+)\s*$", re.I)
HISTORY_LIMIT = 24


@asynccontextmanager
async def lifespan(app: FastAPI):
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    log.info("ядро запущено: http://%s:%s (устройств в памяти: %d)",
             settings.host, settings.port, len(hub.devices))
    if settings.token in {"", "change-me"}:
        log.warning("ASSISTANT_TOKEN не задан — задайте его в .env, иначе агенты не подключатся")
    yield
    log.info("ядро остановлено")


class UTF8JSONResponse(JSONResponse):
    """Явный charset: иначе PowerShell 5.1 читает UTF-8 как Latin-1 и ломает кириллицу."""

    media_type = "application/json; charset=utf-8"


app = FastAPI(title="Net Assistant Core", version="0.1.0", lifespan=lifespan,
              default_response_class=UTF8JSONResponse)
app.mount("/shots", StaticFiles(directory=str(SHOTS_DIR)), name="shots")

WEB_DIR = BASE_DIR / "web"
WEB_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


# ─────────────────────────── авторизация ───────────────────────────


def require_token(authorization: str | None = Header(default=None)) -> None:
    if not settings.token or settings.token == "change-me":
        raise HTTPException(500, "не задан ASSISTANT_TOKEN (см. .env)")
    if not authorization or not secrets.compare_digest(authorization, f"Bearer {settings.token}"):
        raise HTTPException(401, "неверный токен")


# ─────────────────────────── модели запросов ───────────────────────────


class CommandRequest(BaseModel):
    device: str = Field(min_length=1, max_length=64)
    action: str = Field(min_length=1, max_length=64)
    args: dict[str, Any] = Field(default_factory=dict)
    confirm: bool = False
    timeout: float | None = Field(default=None, ge=1, le=300)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session: str = Field(default="default", max_length=64)


_sessions: dict[str, list[dict]] = {}


def _trim(history: list[dict]) -> list[dict]:
    if len(history) <= HISTORY_LIMIT:
        return history
    cut = len(history) - HISTORY_LIMIT
    while cut < len(history) and history[cut].get("role") != "user":
        cut += 1
    return history[cut:]


# ─────────────────────────── служебные ───────────────────────────


@app.get("/health")
async def health() -> dict:
    backend = llm.active_backend()
    return {
        "status": "ok",
        "devices_online": len(hub.online_devices()),
        "stt_engine": voice.engine(),
        "llm_backend": backend,
        "llm_model": settings.opencode_model if backend == "opencode" else settings.llm_model,
        "llm_configured": backend == "opencode" or bool(settings.llm_api_key),
    }


@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


# ─────────────────────── установка агента на Android ───────────────────────

ANDROID_BUNDLE_FILES = (
    "agents/__init__.py",
    "agents/common.py",
    "agents/android_agent.py",
    "core/__init__.py",
    "core/config.py",
    "core/protocol.py",
    "requirements-termux.txt",
)

INSTALL_TEMPLATE = r"""#!/data/data/com.termux/files/usr/bin/bash
# Установка агента Net Assistant на Android (Termux, без root).
set -e

BASE="__BASE__"
WS="__WS__"
TOKEN="__TOKEN__"
DIR="$HOME/net-assistant"

command -v pkg >/dev/null 2>&1 || {
    echo "Этот скрипт нужно запускать в Termux (F-Droid), а не в обычной оболочке." >&2
    exit 1
}

echo "== Пакеты Termux =="
pkg update -y >/dev/null 2>&1 || true
pkg install -y python termux-api curl tar >/dev/null

echo "== Файлы агента =="
mkdir -p "$DIR"
curl -fsSL "$BASE/android/bundle.tar.gz?token=$TOKEN" | tar -xzf - -C "$DIR"

cat > "$DIR/.env" <<EOF
CORE_URL=$WS
ASSISTANT_TOKEN=$TOKEN
ANDROID_DEVICE_ID=phone
ANDROID_DEVICE_NAME=Honor
EOF
chmod 600 "$DIR/.env"

echo "== Python-зависимости =="
# В Termux pip ставить/обновлять нельзя: "Installing pip is forbidden".
# python-pip уже приходит вместе с пакетом python.
python -m pip install -q --no-input -r "$DIR/requirements-termux.txt"

echo "== Автозапуск =="
mkdir -p "$HOME/.termux/boot"
cat > "$HOME/.termux/boot/start-agent.sh" <<EOF
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
cd "$DIR" && exec python -m agents.android_agent
EOF
chmod +x "$HOME/.termux/boot/start-agent.sh"

termux-wake-lock >/dev/null 2>&1 || true
pkill -f 'agents.android_agent' >/dev/null 2>&1 || true
cd "$DIR"
nohup python -m agents.android_agent >> "$HOME/agent.log" 2>&1 &
sleep 2

echo
echo "Готово. Агент запущен."
echo "Лог: tail -f ~/agent.log"
echo "Автозапуск после перезагрузки — если установлен Termux:Boot из F-Droid."
"""


def _check_query_token(token: str) -> None:
    if not settings.token or not secrets.compare_digest(token, settings.token):
        raise HTTPException(401, "неверный токен")


@app.get("/android/install.sh", include_in_schema=False)
async def android_install(request: Request, token: str = "") -> PlainTextResponse:
    """Скрипт установки для Termux: подставляет адрес ядра и токен."""
    _check_query_token(token)
    base = str(request.base_url).rstrip("/")
    ws_base = base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    script = (
        INSTALL_TEMPLATE
        .replace("__BASE__", base)
        .replace("__WS__", ws_base)
        .replace("__TOKEN__", settings.token)
    )
    return PlainTextResponse(script, media_type="text/x-shellscript")


@app.get("/android/bundle.tar.gz", include_in_schema=False)
async def android_bundle(token: str = "") -> Response:
    """Архив с агентом и минимальными модулями ядра."""
    _check_query_token(token)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for relative in ANDROID_BUNDLE_FILES:
            path = BASE_DIR / relative
            if path.exists():
                archive.add(path, arcname=relative)
    return Response(
        content=buffer.getvalue(),
        media_type="application/gzip",
        headers={"Content-Disposition": 'attachment; filename="net-assistant-android.tar.gz"'},
    )


@app.get("/devices", dependencies=[Depends(require_token)])
async def devices() -> dict:
    return {
        "devices": [
            {**d.public(), "available_actions": catalog.for_device(d.platform, d.actions)}
            for d in hub.devices.values()
        ]
    }


@app.get("/actions", dependencies=[Depends(require_token)])
async def actions() -> dict:
    return {
        name: {"desc": entry.get("desc"), "args": entry.get("args", {}),
               "dangerous": bool(entry.get("dangerous")),
               "devices": entry.get("devices", [])}
        for name, entry in sorted(catalog.CATALOG.items())
    }


@app.get("/audit", dependencies=[Depends(require_token)])
async def audit_tail(limit: int = 50) -> dict:
    return {"records": audit.tail(max(1, min(limit, 500)))}


@app.get("/pending", dependencies=[Depends(require_token)])
async def pending() -> dict:
    return {"pending": llm.confirmations.list()}


# ─────────────────────────── прямой вызов команды ───────────────────────────


@app.post("/cmd", dependencies=[Depends(require_token)])
async def run_command(request: CommandRequest) -> dict:
    entry = catalog.get(request.action)
    if entry is None:
        raise HTTPException(400, f"команда «{request.action}» запрещена каталогом")

    if entry.get("dangerous") and settings.require_confirm and not request.confirm:
        item = llm.confirmations.create(request.device, request.action, request.args)
        return {"status": "awaiting_confirmation", "token": item.token,
                "question": item.question,
                "hint": f"повторите запрос с confirm=true или вызовите POST /confirm/{item.token}"}

    try:
        result = await hub.dispatch(request.device, request.action, request.args,
                                    timeout=request.timeout)
    except DeviceOffline as exc:
        raise HTTPException(503, str(exc)) from exc
    except (CommandFailed, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"status": "ok", "device": request.device, "action": request.action, "result": result}


@app.post("/confirm/{token}", dependencies=[Depends(require_token)])
async def confirm(token: str) -> dict:
    try:
        return {"status": "ok", **await llm.confirm(token)}
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (DeviceOffline, CommandFailed) as exc:
        raise HTTPException(400, str(exc)) from exc


# ─────────────────────────── диалог с LLM ───────────────────────────


@app.post("/chat", dependencies=[Depends(require_token)])
async def chat(request: ChatRequest) -> dict:
    text = request.message.strip()

    match = CONFIRM_RE.match(text)
    if match:
        try:
            outcome = await llm.confirm(match.group(1))
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (DeviceOffline, CommandFailed) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"reply": f"Готово: {outcome.get('action')} → {outcome.get('result')}",
                "executed": [outcome], "pending": []}

    if text.lower() in {"отмена", "нет", "cancel"}:
        cleared = len(llm.confirmations.list())
        llm.confirmations._items.clear()
        return {"reply": f"Отменено. Снято ожидающих подтверждений: {cleared}",
                "executed": [], "pending": []}

    history = _sessions.setdefault(request.session, [])
    try:
        outcome = await llm.run_turn(text, history)
    except llm.LLMNotConfigured as exc:
        raise HTTPException(500, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    _sessions[request.session] = _trim(history)
    return outcome


@app.delete("/session/{session}", dependencies=[Depends(require_token)])
async def reset_session(session: str) -> dict:
    _sessions.pop(session, None)
    return {"status": "ok", "session": session, "note": "контекст диалога очищен"}


# ─────────────────────────── голос ───────────────────────────


@app.post("/voice", dependencies=[Depends(require_token)])
async def voice_input(
    file: UploadFile = File(...),
    chat: bool = False,
    session: str = "voice",
) -> dict:
    """Распознать аудио и, если chat=true, сразу выполнить как реплику в диалоге."""
    data = await file.read()
    try:
        heard = await voice.transcribe(
            data, file.filename or "audio.webm", file.content_type or ""
        )
    except voice.VoiceNotConfigured as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc

    result: dict = {"text": heard["text"], "engine": heard["engine"]}
    if chat and heard["text"]:
        history = _sessions.setdefault(session, [])
        try:
            turn = await llm.run_turn(heard["text"], history)
        except llm.LLMNotConfigured as exc:
            raise HTTPException(500, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from exc
        _sessions[session] = _trim(history)
        result.update(turn)
    return result


# ─────────────────────────── приём агентов ───────────────────────────


@app.websocket("/ws/agent")
async def agent_socket(websocket: WebSocket) -> None:
    token = websocket.query_params.get("token", "")
    if not settings.token or not secrets.compare_digest(token, settings.token):
        await websocket.close(code=4401, reason="неверный токен")
        return

    await websocket.accept()
    send_lock = asyncio.Lock()
    device_id: str | None = None

    async def send(payload: dict) -> None:
        async with send_lock:
            await websocket.send_json(payload)

    try:
        hello = await asyncio.wait_for(websocket.receive_json(), timeout=15)
    except (asyncio.TimeoutError, WebSocketDisconnect):
        await websocket.close(code=4400, reason="ожидалось hello")
        return

    if hello.get("type") != "hello" or not hello.get("device_id"):
        await send({"type": "error", "error": "первое сообщение должно быть hello"})
        await websocket.close(code=4400)
        return

    device_id = str(hello["device_id"])
    if settings.allowed_devices and device_id not in settings.allowed_devices:
        await send({"type": "error", "error": "устройство не в ALLOWED_DEVICES"})
        await websocket.close(code=4403)
        return

    device = Device(
        device_id=device_id,
        name=str(hello.get("name") or device_id),
        platform=str(hello.get("platform") or "unknown"),
        actions=set(hello.get("actions") or []),
        send=send,
    )
    hub.register(device)
    audit.write(event="agent_connected", device=device_id, platform=device.platform,
                actions=sorted(device.actions))
    await send({"type": "welcome", "heartbeat": 20.0, "protocol": 1})
    log.info("агент подключён: %s (%s), команд: %d",
             device_id, device.platform, len(device.actions))

    async def keepalive() -> None:
        while True:
            await asyncio.sleep(20)
            await send({"type": "ping"})

    pinger = asyncio.create_task(keepalive())
    try:
        while True:
            message = await websocket.receive_json()
            kind = message.get("type")
            hub.mark_seen(device_id)
            if kind == "result":
                hub.complete(message)
            elif kind == "ping":
                await send({"type": "pong"})
            elif kind == "log":
                log.info("[%s] %s", device_id, message.get("message", ""))
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001 - соединение может оборваться как угодно
        log.warning("соединение с %s разорвано: %s", device_id, exc)
    finally:
        pinger.cancel()
        hub.unregister(device_id, send)
        audit.write(event="agent_disconnected", device=device_id)
        log.info("агент отключён: %s", device_id)


def main() -> None:
    import uvicorn

    uvicorn.run("core.main:app", host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
