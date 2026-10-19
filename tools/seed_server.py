"""Сервер раздачи для установки ВМ: статика + канал управления гостем.

Зачем: в live-образе Astra нет SSH, а вводить команды в консоль по одной —
долго. Поэтому гость получает короткий скрипт-агент, который сам опрашивает
этот сервер и возвращает вывод команд.

Эндпоинты:
    GET  /<файл>   — обычная раздача файлов из каталога (preseed.cfg, скрипты).
    GET  /task     — содержимое task.sh (файл при этом переименовывается в
                     task.run-<время>, чтобы задание не выполнилось дважды).
                     Если задания нет — строка EMPTY.
    POST /result   — тело запроса дописывается в results.txt с отметкой времени.

Запуск:
    python tools/seed_server.py --dir D:\\VMs\\seed --port 8080
"""

from __future__ import annotations

import argparse
import datetime as dt
import http.server
import os
import socketserver
import threading
from pathlib import Path

TASK_NAME = "task.sh"
RESULT_NAME = "results.txt"


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, directory: str | None = None, **kwargs):
        super().__init__(*args, directory=directory, **kwargs)

    # ── канал управления ──

    def do_GET(self) -> None:  # noqa: N802 - имя задано стандартной библиотекой
        if self.path.split("?")[0] == "/task":
            self._send_task()
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?")[0] != "/result":
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8", "replace")
        root = Path(self.directory)
        stamp = dt.datetime.now().strftime("%H:%M:%S")
        with (root / RESULT_NAME).open("a", encoding="utf-8") as handle:
            handle.write(f"\n===== {stamp} =====\n{body}\n")
        print(f"[{stamp}] получен результат, {len(body)} символов", flush=True)

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"ok")

    def _send_task(self) -> None:
        root = Path(self.directory)
        path = root / TASK_NAME
        if not path.is_file():
            payload = b"EMPTY"
        else:
            payload = path.read_bytes()
            done = root / f"task.run-{dt.datetime.now().strftime('%H%M%S')}"
            try:
                path.rename(done)
            except OSError:
                path.unlink(missing_ok=True)
            print(f"[{dt.datetime.now():%H:%M:%S}] задание выдано, "
                  f"{len(payload)} байт", flush=True)

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args) -> None:  # тише в консоли
        if "/task" in (args[0] if args else ""):
            return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default=r"D:\VMs\seed")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    directory = os.path.abspath(args.dir)
    if not os.path.isdir(directory):
        raise SystemExit(f"нет каталога: {directory}")

    handler = lambda *a, **kw: Handler(*a, directory=directory, **kw)  # noqa: E731
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("0.0.0.0", args.port), handler) as server:
        print(f"раздаю {directory} на порту {args.port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
