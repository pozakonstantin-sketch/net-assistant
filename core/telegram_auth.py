"""Проверка подписи Telegram Web App (initData).

Telegram подписывает данные мини-приложения ключом, производным от токена бота.
Проверив подпись, ядро может доверять `user.id` и выдать доступ без ручного
ввода токена — то есть дашборд внутри Telegram открывается сразу.

Алгоритм (см. https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app):

    secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token)
    hash       = HMAC_SHA256(key=secret_key, msg=data_check_string)

где `data_check_string` — все поля кроме `hash`, отсортированные по имени и
склеенные через перевод строки в виде ``ключ=значение``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from urllib.parse import parse_qsl

from .config import settings

log = logging.getLogger("telegram-auth")

MAX_AGE = 24 * 3600  # подпись старше суток не принимаем


class AuthError(ValueError):
    """Подпись неверна, данные устарели или пользователь не в списке."""


def _secret(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()


def validate_init_data(init_data: str, max_age: int = MAX_AGE) -> dict:
    """Проверить initData и вернуть разобранные поля (включая user)."""
    if not init_data:
        raise AuthError("пустые данные мини-приложения")
    if not settings.telegram_token:
        raise AuthError("не задан TELEGRAM_BOT_TOKEN — проверить подпись нельзя")

    pairs = dict(parse_qsl(init_data, strict_parsing=False, keep_blank_values=True))
    received = pairs.pop("hash", "")
    if not received:
        raise AuthError("в данных нет hash")

    check = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    expected = hmac.new(_secret(settings.telegram_token), check.encode(),
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise AuthError("подпись не совпадает")

    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError as exc:
        raise AuthError("некорректный auth_date") from exc
    if auth_date and time.time() - auth_date > max_age:
        raise AuthError("данные устарели, откройте приложение заново")

    user: dict = {}
    if pairs.get("user"):
        try:
            user = json.loads(pairs["user"])
        except json.JSONDecodeError as exc:
            raise AuthError("не удалось разобрать user") from exc

    allowed = {item.strip() for item in settings.telegram_chats if item.strip()}
    user_id = str(user.get("id", ""))
    if allowed and user_id not in allowed:
        raise AuthError(f"пользователь {user_id} не в списке разрешённых")

    log.info("мини-приложение: вошёл %s %s (id %s)",
             user.get("first_name", ""), user.get("last_name", ""), user_id)
    return {"user": user, "auth_date": auth_date, "query_id": pairs.get("query_id", "")}
