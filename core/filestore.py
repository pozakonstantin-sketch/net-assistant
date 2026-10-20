"""Хранилище присланных файлов на SSD роутера (SFTP).

Оригиналы и извлечённый текст складываются в каталог ``ROUTER_FILES_DIR``
(по умолчанию ``/mnt/sda/net-assistant/files``) на роутере OpenWrt.
Если роутер недоступен, ядро оставляет копию локально и честно сообщает
об этом пользователю.
"""

from __future__ import annotations

import logging
import posixpath
from pathlib import Path

from .config import settings

log = logging.getLogger("filestore")


class StorageError(RuntimeError):
    """Не удалось сохранить файл на SSD роутера."""


def enabled() -> bool:
    """Настроено ли хранилище (есть хост и пользователь роутера)."""
    return bool(settings.router_host and settings.router_user)


def _connect():
    import paramiko

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    params: dict = {
        "hostname": settings.router_host,
        "port": settings.router_port,
        "username": settings.router_user,
        "timeout": 8,
        "banner_timeout": 10,
        "auth_timeout": 10,
        "look_for_keys": False,
        "allow_agent": False,
    }
    if settings.router_key_path:
        params["key_filename"] = settings.router_key_path
    else:
        params["password"] = settings.router_password
    try:
        client.connect(**params)
    except Exception as exc:  # noqa: BLE001 - сеть/авторизация
        raise StorageError(f"роутер {settings.router_host}: {exc}") from exc
    return client


def _mkdirs(sftp, path: str) -> None:
    current = "/"
    for part in [p for p in path.split("/") if p]:
        current = posixpath.join(current, part)
        try:
            sftp.stat(current)
        except OSError:
            sftp.mkdir(current)


def upload_many(files: list[tuple[Path, str]], subdir: str = "") -> list[str]:
    """Загрузить файлы ``(локальный путь, имя на роутере)`` в один каталог."""
    if not enabled():
        raise StorageError("хранилище не настроено: задайте ROUTER_HOST/ROUTER_USER")

    remote_dir = settings.router_files_dir
    if subdir:
        remote_dir = posixpath.join(remote_dir, subdir)

    client = _connect()
    paths: list[str] = []
    try:
        sftp = client.open_sftp()
        try:
            _mkdirs(sftp, remote_dir)
            for local, name in files:
                remote = posixpath.join(remote_dir, name)
                sftp.put(str(local), remote)
                paths.append(remote)
        finally:
            sftp.close()
    except OSError as exc:
        raise StorageError(f"ошибка SFTP: {exc}") from exc
    finally:
        client.close()

    log.info("на SSD роутера сохранено: %s", ", ".join(paths))
    return paths


def target_dir(subdir: str = "") -> str:
    """Путь каталога на роутере (для подсказок модели и человека)."""
    base = settings.router_files_dir
    return posixpath.join(base, subdir) if subdir else base
