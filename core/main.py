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
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import catalog
from . import documents
from . import history as chat_history
from . import llm, models, planner, tts, ui_map, voice
from .audit import audit
from .config import BASE_DIR, DATA_DIR, canonical_session, settings
from .files import SHOTS_DIR
from .hub import CommandFailed, Device, DeviceOffline, hub
from .telegram_auth import AuthError, validate_init_data

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("core")

CONFIRM_RE = re.compile(r"^\s*(?:подтверждаю|подтвердить|confirm)\s+([A-Za-z0-9_\-]+)\s*$", re.I)


@asynccontextmanager
async def lifespan(app: FastAPI):
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    chat_history.HISTORY_DIR.mkdir(parents=True, exist_ok=True)
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


class TelegramAuthRequest(BaseModel):
    init_data: str = Field(min_length=1, max_length=8192)


class ModelRequest(BaseModel):
    model: str = Field(min_length=1, max_length=128)


async def _run_chat(session: str, text: str, source: str = "",
                    display_text: str | None = None) -> dict:
    """Один ход диалога с сохранением истории на диск.

    Историю для модели собираем ДО записи новой реплики: ``llm.run_turn``
    сам добавляет в переданный список сообщение пользователя и ответ.

    ``display_text`` — короткая версия реплики для истории и панели, когда
    в модель уходит длинный текст (например, содержимое присланного файла).
    """
    history = chat_history.llm_messages(session)
    chat_history.append(session, "user", display_text or text, source=source)
    try:
        outcome = await llm.run_turn(text, history)
    except Exception as exc:  # noqa: BLE001 - ошибку покажем в истории и вызывающему
        chat_history.append(session, "system", f"ошибка: {exc}", source=source)
        raise
    chat_history.append(
        session, "assistant", str(outcome.get("reply") or ""),
        executed=outcome.get("executed") or None,
        pending=outcome.get("pending") or None,
        source=source,
    )
    return outcome


# ─────────────────── вход из мини-приложения Telegram ───────────────────


@app.post("/tg/auth")
async def telegram_auth(request: TelegramAuthRequest) -> dict:
    """Обменять подписанные данные мини-приложения на токен ассистента.

    Проверяется подпись Telegram (HMAC на токене бота) и то, что пользователь
    в списке TELEGRAM_ALLOWED_CHAT_IDS. Так дашборд внутри Telegram
    открывается без ручного ввода токена.
    """
    if not settings.telegram_token:
        raise HTTPException(503, "Telegram не настроен (TELEGRAM_BOT_TOKEN в .env)")
    try:
        info = validate_init_data(request.init_data)
    except AuthError as exc:
        raise HTTPException(401, str(exc)) from exc
    user = info["user"]
    return {"token": settings.token, "user": user,
            "name": user.get("first_name") or user.get("username") or "пользователь"}


# ─────────────────────────── служебные ───────────────────────────


@app.get("/health")
async def health() -> dict:
    backend = llm.active_backend()
    return {
        "status": "ok",
        "devices_online": len(hub.online_devices()),
        "stt_engine": voice.engine(),
        "tts_engine": settings.tts_engine if tts.enabled() else "none",
        "tts_voice": tts.resolve_voice() if tts.enabled() else "",
        "llm_backend": backend,
        "llm_model": models.current() if backend == "opencode" else settings.llm_model,
        "llm_configured": backend == "opencode" or bool(settings.llm_api_key),
        "files": documents.storage_info(),
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
CORE_URLS=__URLS__
ASSISTANT_TOKEN=$TOKEN
ANDROID_DEVICE_ID=phone
ANDROID_DEVICE_NAME=Honor
EOF
chmod 600 "$DIR/.env"

echo "== Python-зависимости =="
# В Termux pip ставить/обновлять нельзя: "Installing pip is forbidden".
# python-pip уже приходит вместе с пакетом python.
python -m pip install -q --no-input -r "$DIR/requirements-termux.txt"

echo "== Запуск и автозапуск =="
# Отдельный скрипт запуска: работает и после перезагрузки (Termux:Boot),
# и при ручном запуске — важно задать окружение Termux, иначе termux-*
# команды не находятся (FileNotFoundError: termux-battery-status).
cat > "$HOME/start-agent.sh" <<'EOS'
#!/data/data/com.termux/files/usr/bin/sh
export HOME=/data/data/com.termux/files/home
export PREFIX=/data/data/com.termux/files/usr
export PATH="$PREFIX/bin:/system/bin:/system/xbin"
export TMPDIR="$PREFIX/tmp"
export LD_PRELOAD="$PREFIX/lib/libtermux-exec.so"
export LANG=en_US.UTF-8
termux-wake-lock >/dev/null 2>&1 || true
cd "$HOME/net-assistant" || exit 1
exec python -m agents.android_agent
EOS
chmod +x "$HOME/start-agent.sh"

mkdir -p "$HOME/.termux/boot"
cat > "$HOME/.termux/boot/start-agent.sh" <<'EOS'
#!/data/data/com.termux/files/usr/bin/sh
exec "$HOME/start-agent.sh" >> "$HOME/agent.log" 2>&1
EOS
chmod +x "$HOME/.termux/boot/start-agent.sh"

termux-wake-lock >/dev/null 2>&1 || true
pkill -f 'agents.android_agent' >/dev/null 2>&1 || true
nohup "$HOME/start-agent.sh" >> "$HOME/agent.log" 2>&1 &
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
        .replace("__URLS__", ",".join(
            url for url in (ws_base, settings.hub_lan_url) if url
        ))
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
            {**d.public(), "available_actions": catalog.for_device(
                d.platform, d.actions, full_access=settings.access_mode == "full")}
            for d in hub.devices.values()
        ]
    }


@app.post("/ui/map", dependencies=[Depends(require_token)])
async def ui_map_store(payload: dict) -> dict:
    """Панель присылает координаты своих кнопок — подсказка для ИИ."""
    ui_map.store(payload)
    return {"ok": True, "targets": len(ui_map.MAP.get("targets", []))}


@app.get("/ui/map", dependencies=[Depends(require_token)])
async def ui_map_get() -> dict:
    return ui_map.current() or {}



@app.get("/actions", dependencies=[Depends(require_token)])
async def actions() -> dict:
    return {
        name: {"desc": entry.get("desc"), "args": entry.get("args", {}),
               "dangerous": bool(entry.get("dangerous")),
               "full_only": bool(entry.get("full_only")),
               "devices": entry.get("devices", [])}
        for name, entry in sorted(catalog.CATALOG.items())
    }


@app.get("/audit", dependencies=[Depends(require_token)])
async def audit_tail(limit: int = 50) -> dict:
    return {"records": audit.tail(max(1, min(limit, 500)))}


@app.get("/pending", dependencies=[Depends(require_token)])
async def pending() -> dict:
    return {"pending": llm.confirmations.list()}


# ─────────────────────────── выбор модели ИИ ───────────────────────────


@app.get("/models", dependencies=[Depends(require_token)])
async def models_list(refresh: bool = False) -> dict:
    """Список доступных моделей планировщика и текущая выбранная.

    refresh=true заставляет заново опросить CLI opencode (может занять до минуты).
    """
    info = models.info()
    if refresh:
        info["models"] = models.available(refresh=True)
    return info


@app.post("/models", dependencies=[Depends(require_token)])
async def models_switch(request: ModelRequest) -> dict:
    """Переключить модель планировщика на ходу (без перезапуска ядра)."""
    try:
        chosen = models.set_current(request.model)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    audit.write(event="model_switch", model=chosen)
    return {"status": "ok", **models.info()}


@app.delete("/models", dependencies=[Depends(require_token)])
async def models_reset() -> dict:
    """Вернуться к модели из .env (OPENCODE_MODEL)."""
    models.reset()
    audit.write(event="model_reset")
    return {"status": "ok", **models.info()}


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
async def confirm(token: str, session: str | None = None, source: str = "web") -> dict:
    try:
        outcome = await llm.confirm(token)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (DeviceOffline, CommandFailed) as exc:
        raise HTTPException(400, str(exc)) from exc
    if session:
        reply = f"Готово: {outcome.get('action')} → {outcome.get('result')}"
        chat_history.append(session, "user", f"подтверждаю {token}", source=source)
        chat_history.append(session, "assistant", reply, executed=[outcome], source=source)
    return {"status": "ok", **outcome}


# ─────────────────────────── диалог с LLM ───────────────────────────


@app.get("/session", dependencies=[Depends(require_token)])
async def session_default() -> dict:
    """Единая сессия для всех устройств: бот, мини-апп и браузер видят одну переписку."""
    return {"session": canonical_session()}


@app.post("/chat", dependencies=[Depends(require_token)])
async def chat(request: ChatRequest, source: str = "web") -> dict:
    text = request.message.strip()
    if not text:
        raise HTTPException(400, "пустое сообщение")

    match = CONFIRM_RE.match(text)
    if match:
        try:
            outcome = await llm.confirm(match.group(1))
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (DeviceOffline, CommandFailed) as exc:
            raise HTTPException(400, str(exc)) from exc
        reply = f"Готово: {outcome.get('action')} → {outcome.get('result')}"
        chat_history.append(request.session, "user", text, source=source)
        chat_history.append(request.session, "assistant", reply,
                            executed=[outcome], source=source)
        return {"reply": reply, "executed": [outcome], "pending": []}

    if text.lower() in {"отмена", "нет", "cancel"}:
        cleared = len(llm.confirmations.list())
        llm.confirmations._items.clear()
        reply = f"Отменено. Снято ожидающих подтверждений: {cleared}"
        chat_history.append(request.session, "user", text, source=source)
        chat_history.append(request.session, "assistant", reply, source=source)
        return {"reply": reply, "executed": [], "pending": []}

    try:
        return await _run_chat(request.session, text, source)
    except llm.LLMNotConfigured as exc:
        raise HTTPException(500, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/history", dependencies=[Depends(require_token)])
async def history_list(session: str = "default", limit: int = 200) -> dict:
    """Сохранённая переписка с ИИ (для панели и Telegram-бота)."""
    return {
        "session": session,
        "messages": chat_history.entries(session, max(1, min(limit, 500))),
    }


@app.get("/sessions", dependencies=[Depends(require_token)])
async def sessions_list() -> dict:
    return {"sessions": chat_history.sessions(),
            "default": canonical_session(),
            "note": "default — общая переписка для всех устройств"}


@app.delete("/session/{session}", dependencies=[Depends(require_token)])
async def reset_session(session: str) -> dict:
    removed = chat_history.clear(session)
    return {"status": "ok", "session": session, "removed": removed,
            "note": "контекст и история диалога очищены"}


# ─────────────────────────── голос ───────────────────────────


@app.post("/voice", dependencies=[Depends(require_token)])
async def voice_input(
    file: UploadFile = File(...),
    chat: bool = False,
    session: str = "voice",
    source: str = "web",
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
        try:
            turn = await _run_chat(session, heard["text"], source)
        except llm.LLMNotConfigured as exc:
            raise HTTPException(500, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from exc
        result.update(turn)
    return result


# ───────────────────────── озвучка ответов ─────────────────────────


class TtsRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    voice: str | None = None


@app.get("/tts", dependencies=[Depends(require_token)])
async def tts_info() -> dict:
    """Текущий голос и доступные варианты."""
    return tts.info()


@app.post("/tts", dependencies=[Depends(require_token)])
async def tts_speak(request: TtsRequest) -> FileResponse:
    """Синтезировать текст в OGG/Opus (голосовое сообщение / плеер в панели)."""
    try:
        path = await tts.synthesize(request.text, request.voice)
    except tts.TTSNotConfigured as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except tts.TTSError as exc:
        raise HTTPException(502, str(exc)) from exc
    return FileResponse(path, media_type="audio/ogg", filename="reply.ogg",
                        headers={"Cache-Control": "private, max-age=86400"})


# ─────────────────────── изображения (скриншоты) ───────────────────────

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".heic"}


@app.post("/image", dependencies=[Depends(require_token)])
async def image_input(
    file: UploadFile = File(...),
    message: str = "",
    session: str = "image",
    chat: bool = True,
    source: str = "web",
) -> dict:
    """Распознать присланный скриншот и (chat=true) ответить как в диалоге.

    Бесплатная модель со зрением читает картинку и отдаёт текстовое описание,
    которое попадает в обычный диалог — так ассистент «видит» скриншоты ошибок.
    """
    data = await file.read()
    if not data:
        raise HTTPException(400, "пустой файл")

    uploads = DATA_DIR / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        suffix = ".png"
    path = uploads / f"{int(time.time())}_{secrets.token_hex(4)}{suffix}"
    path.write_bytes(data)

    caption = (message or "").strip()
    try:
        description = await planner.describe_image(path, caption)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc

    block = ("[Пользователь прислал скриншот. Вот что на нём распознано:\n"
             f"{description}]")
    text = f"{caption}\n\n{block}" if caption else block

    if not chat:
        return {"status": "ok", "text": description, "file": str(path)}

    try:
        turn = await _run_chat(session, text, source)
    except llm.LLMNotConfigured as exc:
        raise HTTPException(500, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {**turn, "text": description}


# ─────────────────── присланные файлы (Word, Excel, PDF) ───────────────────


@app.post("/file", dependencies=[Depends(require_token)])
async def file_input(
    file: UploadFile = File(...),
    message: str = "",
    session: str = "files",
    chat: bool = True,
    source: str = "web",
) -> dict:
    """Сохранить присланный файл на SSD роутера и разобрать содержимое.

    Оригинал уходит по SFTP в ``ROUTER_FILES_DIR`` на роутере, локально
    остаётся кэш и извлечённый текст. При ``chat=true`` текст попадает
    планировщику как реплика пользователя, ответ — в историю сессии.
    """
    data = await file.read()
    try:
        entry = await asyncio.to_thread(
            documents.save, data, file.filename or "file", session, source, message
        )
    except documents.FileError as exc:
        raise HTTPException(400, str(exc)) from exc

    block = documents.chat_block(entry, message)
    if not chat:
        return {"status": "ok", "file": entry, "text": entry.get("preview", "")}

    try:
        turn = await _run_chat(session, block, source,
                               display_text=documents.display_text(entry, message))
    except llm.LLMNotConfigured as exc:
        raise HTTPException(500, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {**turn, "file": entry}


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
