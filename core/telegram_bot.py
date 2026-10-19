"""Telegram-бот: пульт к ядру ассистента.

    python -m core.telegram_bot

Работает через long polling — входящие порты не нужны, достаточно исходящего
доступа к api.telegram.org. Бот не содержит логики управления: он пересылает
текст в /chat ядра, показывает кнопки подтверждения и присылает скриншоты.

Настройка (.env):
    TELEGRAM_BOT_TOKEN=123456:ABC...        # получить у @BotFather
    TELEGRAM_ALLOWED_CHAT_IDS=123456789     # кому разрешено (через запятую)
    TELEGRAM_CORE_URL=http://127.0.0.1:8787 # ядро на этой же машине

Если список пуст — бот подскажет chat_id при первом обращении.
"""

from __future__ import annotations

import asyncio
import html
import logging
from pathlib import Path

import httpx

from .config import DATA_DIR, settings

log = logging.getLogger("telegram")

API = "https://api.telegram.org"
POLL_TIMEOUT = 30
MAX_MESSAGE = 3800
SESSION_PREFIX = "tg-"

HELP = """<b>Ассистент</b> — управление ПК, телефоном и роутером.

Просто напишите задачу словами, например:
• «Какая температура на телефоне?»
• «Сделай скриншот ПК»
• «Кто подключён к роутеру»
• «Выключи WiFi на роутере» (спросит подтверждение)

Команды:
/status — ядро и список устройств
/models — выбрать модель ИИ (список кнопками)
/model <имя> — переключить модель сразу
/pending — что ждёт подтверждения
/panel — открыть панель (устройства, чат, журнал)
/reset — очистить контекст диалога
/help — эта справка

Голосовое сообщение тоже понимается (если настроено распознавание)."""


# ─────────────────────────────── утилиты ───────────────────────────────


def _allowed() -> set[str]:
    return {item.strip() for item in settings.telegram_chats if item.strip()}


def _esc(value: object) -> str:
    return html.escape(str(value), quote=False)


def _split(text: str) -> list[str]:
    if len(text) <= MAX_MESSAGE:
        return [text]
    chunks: list[str] = []
    rest = text
    while len(rest) > MAX_MESSAGE:
        cut = rest.rfind("\n", 0, MAX_MESSAGE)
        if cut < MAX_MESSAGE // 2:
            cut = MAX_MESSAGE
        chunks.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")
    chunks.append(rest)
    return chunks


def panel_row() -> list[list[dict]]:
    """Кнопка «открыть панель» — если настроен HTTPS-адрес мини-приложения."""
    url = settings.telegram_webapp_url
    if not url:
        return []
    return [[{"text": "🌐 Открыть панель", "web_app": {"url": url}}]]


# ─────────────────────────────── клиент ───────────────────────────────


class Telegram:
    def __init__(self) -> None:
        self.token = settings.telegram_token
        self.core = settings.telegram_core_url
        self.session_token = settings.token
        self.client = httpx.AsyncClient(timeout=POLL_TIMEOUT + 20)

    # ── Telegram Bot API ──

    async def api(self, method: str, payload: dict | None = None,
                  files: dict | None = None) -> dict:
        url = f"{API}/bot{self.token}/{method}"
        response = await self.client.post(url, json=payload if not files else None,
                                          data=payload if files else None, files=files)
        if response.status_code >= 400:
            raise RuntimeError(f"Telegram {method}: {response.status_code} {response.text[:300]}")
        return response.json()

    async def send(self, chat_id: int, text: str, keyboard: list | None = None,
                   reply_to: int | None = None) -> None:
        for index, chunk in enumerate(_split(text)):
            payload: dict = {"chat_id": chat_id, "text": chunk,
                             "parse_mode": "HTML", "disable_web_page_preview": True}
            if keyboard and index == len(_split(text)) - 1:
                payload["reply_markup"] = {"inline_keyboard": keyboard}
            if reply_to and index == 0:
                payload["reply_to_message_id"] = reply_to
            try:
                await self.api("sendMessage", payload)
            except RuntimeError as exc:
                # разметка могла оказаться некорректной — шлём как обычный текст
                log.warning("%s", exc)
                payload.pop("parse_mode", None)
                await self.api("sendMessage", payload)

    async def send_photo(self, chat_id: int, path: Path, caption: str = "") -> None:
        if not path.is_file():
            await self.send(chat_id, f"⚠️ скриншот не найден: {_esc(path.name)}")
            return
        with path.open("rb") as handle:
            await self.api("sendPhoto", {"chat_id": chat_id, "caption": caption[:900]},
                           files={"photo": (path.name, handle, "image/png")})

    async def typing(self, chat_id: int) -> None:
        try:
            await self.api("sendChatAction", {"chat_id": chat_id, "action": "typing"})
        except RuntimeError:
            pass

    # ── ядро ──

    async def core_call(self, method: str, path: str, payload: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self.session_token}"}
        response = await self.client.request(method, f"{self.core}{path}",
                                             json=payload, headers=headers)
        if response.status_code >= 400:
            detail = response.text[:300]
            try:
                detail = response.json().get("detail", detail)
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(str(detail))
        return response.json()

    # ─────────────────────────── обработка ───────────────────────────

    async def on_text(self, chat_id: int, text: str, message_id: int) -> None:
        await self.typing(chat_id)
        if text.startswith("/"):
            await self.on_command(chat_id, text)
            return
        try:
            data = await self.core_call("POST", "/chat",
                                        {"message": text, "session": f"{SESSION_PREFIX}{chat_id}"})
        except RuntimeError as exc:
            await self.send(chat_id, f"⚠️ Ошибка: {_esc(exc)}", reply_to=message_id)
            return
        await self.reply(chat_id, data)

    async def on_command(self, chat_id: int, text: str) -> None:
        command = text.split()[0].lower().lstrip("/")
        if command in {"start", "help"}:
            await self.send(chat_id, HELP, keyboard=panel_row() or None)
        elif command == "status":
            await self.status(chat_id)
        elif command in {"models", "model"}:
            parts = text.split(None, 1)
            await self.models(chat_id, parts[1].strip() if len(parts) > 1 else "")
        elif command == "pending":
            await self.pending(chat_id)
        elif command == "reset":
            await self.core_call("DELETE", f"/session/{SESSION_PREFIX}{chat_id}")
            await self.send(chat_id, "🧹 Контекст диалога очищен.")
        elif command == "panel":
            if settings.telegram_webapp_url:
                await self.send(chat_id, "Откройте панель кнопкой ниже.",
                                keyboard=panel_row())
            else:
                await self.send(chat_id, "Мини-приложение не настроено "
                                         "(TELEGRAM_WEBAPP_URL в .env).")
        else:
            await self.send(chat_id, "Неизвестная команда. /help")

    async def status(self, chat_id: int) -> None:
        try:
            health = await self.core_call("GET", "/health")
            devices = await self.core_call("GET", "/devices")
        except RuntimeError as exc:
            await self.send(chat_id, f"⚠️ Ядро недоступно: {_esc(exc)}")
            return
        lines = [f"🧠 ИИ: <b>{_esc(health.get('llm_backend'))}</b> "
                 f"({_esc(health.get('llm_model'))})"]
        for device in devices.get("devices", []):
            mark = "🟢" if device.get("online") else "⚪️"
            lines.append(f"{mark} {_esc(device.get('device_id'))} — "
                         f"{_esc(device.get('platform'))}, "
                         f"{len(device.get('available_actions', []))} команд")
        await self.send(chat_id, "\n".join(lines))

    async def models(self, chat_id: int, argument: str = "") -> None:
        """Показать список моделей ИИ или переключиться на указанную."""
        if argument:
            try:
                data = await self.core_call("POST", "/models", {"model": argument})
            except RuntimeError as exc:
                await self.send(chat_id, f"⚠️ {_esc(exc)}")
                return
            await self.send(chat_id, f"🧠 Модель переключена: <b>{_esc(data.get('current'))}</b>")
            return

        try:
            data = await self.core_call("GET", "/models")
        except RuntimeError as exc:
            await self.send(chat_id, f"⚠️ Ядро недоступно: {_esc(exc)}")
            return

        current = str(data.get("current") or "")
        models = [str(item) for item in data.get("models") or []]
        if not models:
            await self.send(chat_id, f"🧠 Текущая модель: <b>{_esc(current)}</b>\n"
                                     "Список получить не удалось — проверьте CLI opencode.")
            return

        lines = [f"🧠 Текущая модель: <b>{_esc(current)}</b>", "", "Нажмите, чтобы переключить:"]
        rows: list[list[dict]] = []
        for item in models:
            mark = "✅ " if item == current else ""
            lines.append(f"{mark}<code>{_esc(item)}</code>")
            rows.append([{"text": f"{mark}{item.split('/', 1)[-1]}",
                          "callback_data": f"model:{item}"}])
        await self.send(chat_id, "\n".join(lines), keyboard=rows[:10])

    async def pending(self, chat_id: int) -> None:
        data = await self.core_call("GET", "/pending")
        items = data.get("pending", [])
        if not items:
            await self.send(chat_id, "Нет ожидающих подтверждений.")
            return
        lines = ["⏳ Ждут подтверждения:"]
        for item in items:
            lines.append(f"• {_esc(item['question'])} — токен <code>{_esc(item['token'])}</code>")
        await self.send(chat_id, "\n".join(lines), keyboard=self.keyboard(items))

    async def reply(self, chat_id: int, data: dict, reply_to: int | None = None) -> None:
        text = str(data.get("reply") or "(пустой ответ)")
        executed = data.get("executed") or []
        pending = data.get("pending") or []

        done = [item for item in executed if item.get("ok")]
        if done:
            names = ", ".join(f"{item.get('device')}::{item.get('action')}" for item in done)
            text += f"\n\n✅ <code>{_esc(names)}</code>"

        await self.send(chat_id, text, keyboard=self.keyboard(pending) if pending else None,
                        reply_to=reply_to)

        for item in executed:
            result = item.get("result") if isinstance(item.get("result"), dict) else {}
            url = result.get("url") if result else None
            if url and str(url).startswith("/shots/"):
                await self.send_photo(chat_id, DATA_DIR / "shots" / Path(str(url)).name,
                                      caption=f"{item.get('device')}::{item.get('action')}")

    @staticmethod
    def keyboard(pending: list) -> list:
        rows = []
        for item in pending[:5]:
            token = str(item.get("token"))
            label = f"{item.get('device')}::{item.get('action')}"
            rows.append([
                {"text": f"✅ {label}", "callback_data": f"confirm:{token}"},
                {"text": "❌ Отмена", "callback_data": "cancel:"},
            ])
        return rows

    async def on_callback(self, chat_id: int, data: str, callback_id: str,
                          message_id: int) -> None:
        try:
            if data.startswith("confirm:"):
                token = data.split(":", 1)[1]
                outcome = await self.core_call("POST", f"/confirm/{token}")
                await self.api("answerCallbackQuery",
                               {"callback_query_id": callback_id, "text": "Выполняю"})
                await self.send(chat_id, f"✅ {_esc(outcome.get('action'))} — "
                                         f"<code>{_esc(str(outcome.get('result'))[:800])}</code>")
                result = outcome.get("result")
                if isinstance(result, dict) and str(result.get("url", "")).startswith("/shots/"):
                    await self.send_photo(chat_id,
                                          DATA_DIR / "shots" / Path(result["url"]).name)
            elif data.startswith("model:"):
                await self.api("answerCallbackQuery",
                               {"callback_query_id": callback_id, "text": "Переключаю"})
                await self.models(chat_id, data.split(":", 1)[1])
            elif data.startswith("cancel:"):
                await self.core_call("POST", "/chat",
                                     {"message": "отмена", "session": f"{SESSION_PREFIX}{chat_id}"})
                await self.api("answerCallbackQuery",
                               {"callback_query_id": callback_id, "text": "Отменено"})
                await self.send(chat_id, "🚫 Отменено.")
        except RuntimeError as exc:
            await self.api("answerCallbackQuery",
                           {"callback_query_id": callback_id, "text": "Ошибка"})
            await self.send(chat_id, f"⚠️ {_esc(exc)}")

    async def on_voice(self, chat_id: int, voice: dict, message_id: int) -> None:
        await self.typing(chat_id)
        try:
            info = await self.api("getFile", {"file_id": voice.get("file_id")})
            remote = info["result"]["file_path"]
            url = f"{API}/file/bot{self.token}/{remote}"
            response = await self.client.get(url)
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            await self.send(chat_id, f"⚠️ Не смог скачать голосовое: {_esc(exc)}")
            return

        headers = {"Authorization": f"Bearer {self.session_token}"}
        files = {"file": ("voice.ogg", response.content, "audio/ogg")}
        params = {"chat": "true", "session": f"{SESSION_PREFIX}{chat_id}"}
        core = await self.client.post(f"{self.core}/voice", headers=headers,
                                      params=params, files=files)
        if core.status_code >= 400:
            detail = core.text[:300]
            try:
                detail = core.json().get("detail", detail)
            except Exception:  # noqa: BLE001
                pass
            await self.send(chat_id, f"⚠️ Распознавание недоступно: {_esc(detail)}")
            return
        data = core.json()
        await self.send(chat_id, f"🎤 <i>{_esc(data.get('text'))}</i>")
        await self.reply(chat_id, data)

    # ─────────────────────── мини-приложение ───────────────────────

    async def set_menu_button(self) -> None:
        """Кнопка «Панель» у поля ввода — открывает мини-приложение."""
        url = settings.telegram_webapp_url
        if not url:
            return
        try:
            await self.api("setChatMenuButton", {
                "menu_button": {"type": "web_app", "text": "Панель",
                                "web_app": {"url": url}},
            })
            log.info("меню-кнопка мини-приложения: %s", url)
        except RuntimeError as exc:
            log.warning("не удалось поставить меню-кнопку: %s", exc)

    # ─────────────────────────── цикл опроса ───────────────────────────

    async def handle(self, update: dict) -> None:
        message = update.get("message") or update.get("edited_message")
        if message:
            chat_id = message["chat"]["id"]
            text = (message.get("text") or "").strip()
            message_id = message.get("message_id", 0)
            if message.get("voice"):
                await self.guard(chat_id, message_id, lambda: self.on_voice(
                    chat_id, message["voice"], message_id))
                return
            if not text:
                return
            await self.guard(chat_id, message_id, lambda: self.on_text(chat_id, text, message_id))
            return

        callback = update.get("callback_query")
        if callback:
            chat_id = callback["message"]["chat"]["id"]
            await self.guard(chat_id, 0, lambda: self.on_callback(
                chat_id, callback.get("data", ""), callback["id"],
                callback["message"].get("message_id", 0)))

    async def guard(self, chat_id: int, message_id: int, action) -> None:
        allowed = _allowed()
        if allowed and str(chat_id) not in allowed:
            log.warning("сообщение от чужого чата %s", chat_id)
            await self.send(chat_id, f"⛔️ Доступ запрещён.\nВаш chat_id: <code>{chat_id}</code>")
            return
        if not allowed:
            log.info("неизвестный чат %s: добавьте в TELEGRAM_ALLOWED_CHAT_IDS", chat_id)
            await self.send(chat_id, f"ℹ️ Ваш chat_id: <code>{chat_id}</code>\n"
                                     f"Добавьте его в <code>TELEGRAM_ALLOWED_CHAT_IDS</code> "
                                     f"в .env и перезапустите бота, чтобы закрыть доступ.")
            return
        try:
            await action()
        except Exception as exc:  # noqa: BLE001 - бот не должен падать
            log.exception("сбой обработки")
            await self.send(chat_id, f"⚠️ Внутренняя ошибка: {_esc(exc)}")

    async def run(self) -> None:
        if not self.token:
            raise SystemExit("не задан TELEGRAM_BOT_TOKEN в .env")
        me = await self.api("getMe")
        name = me["result"].get("username")
        if not _allowed():
            log.warning("TELEGRAM_ALLOWED_CHAT_IDS пуст — бот отвечает всем; "
                        "добавьте свой chat_id в .env")
        if settings.telegram_webapp_url:
            await self.set_menu_button()
        log.info("бот @%s запущен, ядро: %s", name, self.core)

        offset = 0
        while True:
            try:
                data = await self.api("getUpdates", {"offset": offset,
                                                     "timeout": POLL_TIMEOUT,
                                                     "allowed_updates": ["message", "callback_query"]})
            except Exception as exc:  # noqa: BLE001
                log.warning("getUpdates: %s", exc)
                await asyncio.sleep(5)
                continue
            for update in data.get("result", []):
                offset = update["update_id"] + 1
                await self.handle(update)


async def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")
    # httpx пишет URL целиком — в них токен бота, поэтому глушим
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    bot = Telegram()
    try:
        await bot.run()
    finally:
        await bot.client.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
