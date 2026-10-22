"""Агент роутера: SSH + шаблоны команд под конкретную прошивку.

    python -m agents.router_agent

Поддерживается OpenWrt (по умолчанию), а также «сырые» шаблоны для MikroTik,
Keenetic и Asus. Шаблоны можно править под свою модель: см. TEMPLATES.

Важно: команды, меняющие состояние (выключение WiFi, блокировка клиента,
перезагрузка), помечены в ядре как опасные и требуют подтверждения.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time

from agents.common import AgentClient
from core.config import settings

log = logging.getLogger("router-agent")
FULL_ACCESS = settings.access_mode == "full"

TEMPLATES: dict[str, dict[str, str]] = {
    "openwrt": {
        "uptime": "uptime",
        "board": "cat /tmp/sysinfo/model 2>/dev/null || ubus call system board",
        "wan": "ifstatus wan 2>/dev/null | head -30",
        "lan": "uci get network.lan.ipaddr 2>/dev/null",
        "clients": ("echo '--- leases'; cat /tmp/dhcp.leases 2>/dev/null; "
                    "echo '--- wifi'; iw dev {wifi_if} station dump 2>/dev/null | "
                    "grep -E 'Station|signal:|rx bitrate'"),
        "wifi_off": "uci set wireless.@wifi-device[0].disabled='1'; uci commit wireless; wifi down",
        "wifi_on": "uci set wireless.@wifi-device[0].disabled='0'; uci commit wireless; wifi up",
        "kick": "ubus call hostapd.{wifi_if} del_client "
                '\'{{"addr":"{mac}","deauth":true,"ban_time":60000}}\'',
        "block": ("nft add element inet fw4 blocked_clients '{{ {mac} }}' 2>/dev/null || "
                  "echo 'набор blocked_clients не найден: настройте nft-правило вручную'"),
        "reboot": "reboot",
    },
    "mikrotik": {
        "uptime": "/system resource print",
        "board": "/system identity print; /system routerboard print",
        "wan": "/ip address print",
        "lan": "/ip address print where interface=bridge",
        "clients": "/ip dhcp-server lease print; /interface wireless registration-table print",
        "wifi_off": "/interface wireless disable [find]",
        "wifi_on": "/interface wireless enable [find]",
        "kick": "/interface wireless registration-table remove [find mac-address={mac}]",
        "block": "/ip firewall address-list add list=blocked address={mac}",
        "reboot": "/system reboot",
    },
    # Keenetic и Asus имеют ограниченный CLI; шаблоны заведомо приблизительные.
    "keenetic": {
        "uptime": "show version",
        "board": "show version",
        "wan": "show interface",
        "lan": "show ip interface",
        "clients": "show ip hotspot",
        "wifi_off": "interface Wifi disable",
        "wifi_on": "interface Wifi enable",
        "kick": "no ip hotspot host {mac}",
        "block": "ip hotspot host {mac} deny",
        "reboot": "reboot",
    },
    "asus": {
        "uptime": "uptime",
        "board": "nvram get productid",
        "wan": "nvram get wan0_ipaddr",
        "lan": "nvram get lan_ipaddr",
        "clients": "cat /var/lib/misc/dnsmasq.leases",
        "wifi_off": "radio off",
        "wifi_on": "radio on",
        "kick": "wl -i {wifi_if} deauthenticate {mac}",
        "block": "echo 'блокировка на Asus зависит от прошивки — настройте iptables вручную'",
        "reboot": "reboot",
    },
}


class Router:
    """Одно SSH-соединение, переиспользуется между командами."""

    def __init__(self) -> None:
        self.host = os.getenv("ROUTER_HOST", "192.168.1.1")
        self.port = int(os.getenv("ROUTER_PORT", "22"))
        self.user = os.getenv("ROUTER_USER", "root")
        self.password = os.getenv("ROUTER_PASSWORD") or None
        self.key_path = os.getenv("ROUTER_KEY_PATH") or None
        self.kind = os.getenv("ROUTER_KIND", "openwrt").strip().lower()
        self.wifi_if = os.getenv("ROUTER_WIFI_IF", "wlan0")
        self.timeout = float(os.getenv("ROUTER_TIMEOUT", "20"))
        self._client = None
        self._lock = threading.Lock()

    # ─────────────────────────── SSH ───────────────────────────

    def _connect(self):
        try:
            import paramiko
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("нужен пакет paramiko: pip install paramiko") from exc

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=self.host, port=self.port, username=self.user,
            password=self.password, key_filename=self.key_path,
            timeout=self.timeout, banner_timeout=self.timeout, auth_timeout=self.timeout,
            look_for_keys=bool(self.key_path), allow_agent=False,
        )
        return client

    def close(self) -> None:
        """Закрыть SSH-соединение под локом — например, перед перезагрузкой."""
        with self._lock:
            if self._client is None:
                return
            try:
                self._client.close()
            finally:
                self._client = None

    def run_status(self, command: str) -> tuple[int, str]:
        """Выполнить команду и вернуть (код возврата, вывод) — честно об ошибках."""
        with self._lock:
            for attempt in (1, 2):
                try:
                    if self._client is None:
                        self._client = self._connect()
                    _, stdout, stderr = self._client.exec_command(command, timeout=self.timeout)
                    output = stdout.read().decode("utf-8", errors="replace")
                    error = stderr.read().decode("utf-8", errors="replace")
                    code = stdout.channel.recv_exit_status()
                    if code != 0 and error.strip():
                        # у части прошивок предупреждения уходят в stderr с кодом 0
                        return code, f"{output}\n[stderr] {error}".strip()
                    return code, output.strip()
                except Exception as exc:  # noqa: BLE001 - переподключаемся при обрыве
                    log.warning("SSH %s (попытка %d): %s", self.host, attempt, exc)
                    try:
                        if self._client:
                            self._client.close()
                    finally:
                        self._client = None
                    if attempt == 2:
                        raise RuntimeError(f"не удалось подключиться к {self.host}: {exc}") from exc
        return 255, ""

    def run(self, command: str) -> str:
        """Вывод шаблонной команды; конкретный код возврата важен только для exec.run."""
        return self.run_status(command)[1]

    def template(self, name: str) -> str:
        table = TEMPLATES.get(self.kind) or TEMPLATES["openwrt"]
        if name not in table:
            raise RuntimeError(f"для прошивки «{self.kind}» нет шаблона «{name}»")
        return table[name]


router = Router()


def _render(name: str, **values: str) -> str:
    return router.template(name).format(wifi_if=router.wifi_if, **values)


# ───────────────────────────── команды ─────────────────────────────


def net_status(_args: dict) -> dict:
    return {
        "host": router.host,
        "kind": router.kind,
        "uptime": router.run(router.template("uptime")),
        "board": router.run(router.template("board"))[:500],
        "wan": router.run(router.template("wan"))[:800],
    }


def router_clients(_args: dict) -> dict:
    output = router.run(_render("clients"))
    return {"host": router.host, "raw": output[:4000],
            "note": "формат вывода зависит от прошивки"}


def router_wifi(args: dict) -> dict:
    enabled = bool(args["enabled"])
    router.run(_render("wifi_on" if enabled else "wifi_off"))
    return {"wifi_enabled": enabled, "note": "если вы в WiFi — связь с роутером прервётся"}


MAC_RE = re.compile(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}|[0-9a-f]{12}\Z")


def router_kick(args: dict) -> dict:
    mac = str(args["mac"]).strip().lower()
    # Жёсткий формат: MAC попадает в команды отключения/блокировки —
    # строка вида «aa'; reboot; '» не должна пройти дальше.
    if not MAC_RE.fullmatch(mac):
        raise ValueError("MAC должен быть вида aa:bb:cc:dd:ee:ff")
    result = router.run(_render("kick", mac=mac))
    if args.get("block"):
        result += "\n" + router.run(_render("block", mac=mac))
    return {"mac": mac, "block": bool(args.get("block")), "output": result[:2000]}


def router_reboot(_args: dict) -> dict:
    try:
        router.run(_render("reboot"))
    except Exception as exc:  # noqa: BLE001 - роутер обрывает соединение при перезагрузке
        log.info("перезагрузка инициирована (%s)", exc)
    router.close()
    return {"rebooting": True, "at": time.strftime("%H:%M:%S")}


# ───────────────────────── отчёт о состоянии ─────────────────────────

REPORT_SCRIPT = r"""
echo '@@@src'
echo "${SSH_CLIENT:-}" | awk '{print $1}'
echo '@@@model'
cat /tmp/sysinfo/model 2>/dev/null || echo unknown
echo '@@@uptime'
cat /proc/uptime 2>/dev/null
echo '@@@load'
cat /proc/loadavg 2>/dev/null
echo '@@@meminfo'
grep -E '^(MemTotal|MemFree|MemAvailable|Buffers|Cached|SwapTotal|SwapFree):' /proc/meminfo 2>/dev/null
echo '@@@disk'
df -k / /mnt/sda /tmp 2>/dev/null
echo '@@@wan'
ubus call network.interface.wan status 2>/dev/null
echo '@@@ping'
ping -c 2 -W 2 1.1.1.1 2>&1
echo '@@@dns'
nslookup ya.ru 127.0.0.1 2>&1
echo '@@@proxy'
echo "singbox=$(pidof sing-box 2>/dev/null | awk '{print $1}')"
if nft list table inet TachyonTable >/dev/null 2>&1; then echo 'nft=ok'; else echo 'nft=missing'; fi
echo "watchdog=$(pgrep -f 'tachyon/service/watchdog' 2>/dev/null | head -n1)"
echo "fixloop=$(pgrep -f 'tachyon-tailnet-fix.sh' 2>/dev/null | head -n1)"
echo "dns1603=$(netstat -ln 2>/dev/null | grep -c ':1603')"
echo '@@@wifi'
for i in $(iw dev 2>/dev/null | awk '/Interface/{print $2}'); do echo "iface $i $(iw dev "$i" station dump 2>/dev/null | grep -c '^Station')"; done
echo '@@@leases'
wc -l < /tmp/dhcp.leases 2>/dev/null
echo '@@@tailscale'
tailscale ip -4 2>&1 | head -n1
echo '---'
tailscale status 2>&1 | head -n 10
echo '@@@logs'
logread 2>/dev/null | tail -n 400
"""

LOG_LINE_RE = re.compile(
    r"^\w{3}\s+\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\d{4}\s+"
    r"(\S+)\s+([^:]+):\s*(.*)$"
)

# Слова, по которым сообщение действительно считаем ошибкой. Нужны для
# tailscaled: Go пишет в syslog всё с уровнем err, включая обычную телеметрию.
ERROR_WORDS = (
    "error", "fail", "failed", "denied", "refused", "timeout", "unreachable",
    "panic", "fatal", "cannot", "can't", "no such", "not found", "corrupt",
    "out of memory", "oom", "killed", "segfault", "crash", "unable",
)

# Известные сообщения, которые выглядят как ошибки, но не ломают работу.
# Для них в отчёте будет человеческое пояснение, а не «13 ошибок» без деталей.
KNOWN_LOG_PATTERNS = (
    {
        "test": lambda source, message: (
            source.startswith("tailscaled")
            and ("network is unreachable" in message.lower()
                 or ("derp" in message.lower() and "dial tcp6" in message.lower()))
        ),
        "title": "Tailscale: недоступен IPv6-релей",
        "note": ("на роутере нет IPv6-маршрута, Tailscale не может подключиться "
                 "к резервному релею по IPv6. Обычно не мешает работе; "
                 "IPv6 в Tailscale можно отключить."),
        "error": True,
    },
    {
        "test": lambda source, message: (
            source.startswith("tailscaled")
            and "peerapi" in message.lower()
            and "dns" in message.lower()
        ),
        "title": "Tailscale: отменён DNS-запрос peerapi",
        "note": ("DNS-запрос от другого устройства через Tailscale был отменён. "
                 "Обычно безопасно."),
        "error": True,
    },
    {
        "test": lambda source, message: (
            source.startswith("tailscaled") and "logtail" in message.lower()
        ),
        "title": "Tailscale: телеметрия не отправлена",
        "note": ("сервис Tailscale ограничил приём служебных логов (429) — "
                 "на работу туннеля это не влияет."),
        "error": True,
    },
    {
        "test": lambda source, message: "DNS-rebind attack detected" in message,
        "title": "dnsmasq: сработала защита DNS-rebind",
        "note": ("устройство запросило домен, указывающий на локальный IP; "
                 "dnsmasq заблокировал ответ. Это защита, а не сбой роутера."),
        "error": False,
    },
)


def _known_pattern(source: str, message: str) -> dict | None:
    for pattern in KNOWN_LOG_PATTERNS:
        try:
            if pattern["test"](source, message):
                return pattern
        except Exception:  # noqa: BLE001 - пояснение не должно ломать отчёт
            continue
    return None


def _sections(raw: str) -> dict[str, str]:
    """Разобрать вывод REPORT_SCRIPT на секции по маркерам @@@name."""
    result: dict[str, list[str]] = {}
    current: str | None = None
    for line in raw.splitlines():
        match = re.fullmatch(r"@@@([a-z0-9_]+)", line.strip())
        if match:
            current = match.group(1)
            result[current] = []
        elif current is not None:
            result[current].append(line)
    return {name: "\n".join(lines).strip() for name, lines in result.items()}


def _uptime_text(seconds: float) -> str:
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


def _parse_meminfo(text: str) -> dict:
    values: dict[str, int] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, rest = line.partition(":")
        parts = rest.split()
        if not parts:
            continue
        try:
            values[key.strip()] = int(parts[0])
        except ValueError:
            continue
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable")
    if available is None:
        available = (values.get("MemFree", 0) + values.get("Buffers", 0)
                     + values.get("Cached", 0))
    used = max(0, total - available)
    return {
        "total_mb": round(total / 1024),
        "used_mb": round(used / 1024),
        "available_mb": round(available / 1024),
        "percent": round(used * 100 / total) if total else 0,
    }


def _parse_disks(text: str) -> list[dict]:
    items: list[dict] = []
    labels = {"/": "система", "/mnt/sda": "SSD", "/tmp": "tmpfs"}
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 6:
            continue
        try:
            total = int(parts[1])
            used = int(parts[2])
            percent = int(parts[4].rstrip("%"))
        except ValueError:
            continue
        mount = " ".join(parts[5:])
        items.append({
            "mount": mount,
            "label": labels.get(mount, mount),
            "total_kb": total,
            "used_kb": used,
            "percent": percent,
        })
    return items


def _parse_wan(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    addresses = data.get("ipv4-address") or []
    ip = ""
    if addresses and isinstance(addresses[0], dict):
        ip = str(addresses[0].get("address") or "")
    return {
        "up": bool(data.get("up")),
        "ip": ip,
        "device": str(data.get("l3_device") or data.get("device") or ""),
        "uptime_seconds": int(data.get("uptime") or 0),
        "dns": [str(item) for item in (data.get("dns-server") or [])][:6],
    }


def _parse_ping(text: str) -> dict:
    loss_match = re.search(r"(\d+)% packet loss", text)
    loss = int(loss_match.group(1)) if loss_match else 100
    avg_match = re.search(r"round-trip [^=]+=\s*[\d.]+/([\d.]+)/", text)
    avg = float(avg_match.group(1)) if avg_match else None
    return {"ok": loss < 100, "loss_percent": loss, "avg_ms": avg}


def _parse_dns(text: str) -> dict:
    addresses: list[str] = []
    for match in re.finditer(r"^Address:\s*([0-9a-fA-F:.]+)\s*$", text, re.MULTILINE):
        value = match.group(1)
        # BusyBox nslookup печатает сервер как 127.0.0.1:53 — порт отрезаем
        # только у IPv4, иначе пострадает IPv6 вида 2a02:6b8::2:242.
        with_port = re.fullmatch(r"(\d{1,3}(?:\.\d{1,3}){3}):\d+", value)
        if with_port:
            value = with_port.group(1)
        if value == "127.0.0.1" or value in addresses:
            continue
        addresses.append(value)
    return {"ok": bool(addresses), "addresses": addresses[:4]}


def _parse_proxy(text: str) -> dict:
    raw: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            raw[key.strip()] = value.strip()
    return {
        "singbox": raw.get("singbox", ""),
        "sing_box": bool(raw.get("singbox")),
        "nft_table": raw.get("nft") == "ok",
        "watchdog": bool(raw.get("watchdog")),
        "fix_loop": bool(raw.get("fixloop")),
        "dns_1603": raw.get("dns1603", "0") not in {"", "0"},
    }


def _parse_wifi(text: str) -> list[dict]:
    items: list[dict] = []
    for line in text.splitlines():
        match = re.match(r"iface\s+(\S+)\s+(\d+)\s*$", line.strip())
        if match:
            items.append({"interface": match.group(1),
                          "clients": int(match.group(2))})
    return items


def _parse_tailscale(text: str) -> dict:
    head, _, status = text.partition("---")
    ip = ""
    for line in head.splitlines():
        value = line.strip()
        if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", value):
            ip = value
            break
    return {
        "ip": ip,
        "exit_node": "offers exit node" in status,
        "status": status.strip()[:800],
    }


def _parse_logs(text: str, src_ip: str = "") -> dict:
    lines = [line for line in text.splitlines() if line.strip()]
    groups: dict[tuple[str, str], dict] = {}
    errors = warnings = notices = 0

    for line in lines:
        match = LOG_LINE_RE.match(line)
        if not match:
            continue
        token, source, message = match.groups()
        severity = token.rsplit(".", 1)[-1].lower()
        source = re.sub(r"\[\d+\]$", "", source.strip())
        message = message.strip()

        # Наши собственные SSH-подключения не считаем тревогой.
        if source.startswith("dropbear") and src_ip and src_ip in line:
            continue
        # BusyBox crond пишет факт запуска задачи с уровнем err — это не ошибка.
        if source.startswith("crond") and " cmd " in message:
            severity = "info"
        # tailscaled (Go) отправляет в syslog всё с daemon.err, включая
        # обычные сообщения magicsock/control. Ошибкой считаем только текст
        # с явными признаками проблемы.
        if source.startswith("tailscaled"):
            lowered = message.lower()
            if not any(word in lowered for word in ERROR_WORDS):
                severity = "info"

        if severity in {"err", "error", "crit", "emerg", "alert"}:
            errors += 1
            level = "error"
        elif severity in {"warn", "warning"}:
            warnings += 1
            level = "warn"
        else:
            notices += 1
            continue

        # Одинаковые сообщения с разными pid/временем/указателями группируем,
        # чтобы 10 строк об одном и том же не занимали весь отчёт.
        display = re.sub(r"^\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}\s*", "",
                         message).strip()
        key_text = re.sub(r"\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}", "", message)
        key_text = re.sub(r"0x[0-9a-fA-F]+", "0x…", key_text)
        key_text = re.sub(r"\[\w{4,}\]", "[…]", key_text)
        key_text = re.sub(r"\s+", " ", key_text).strip()[:110]
        key = (source, key_text)
        item = groups.get(key)
        if item is None:
            item = {"level": level, "source": source,
                    "message": display[:200], "count": 0}
            groups[key] = item
        item["count"] += 1

    ordered = sorted(groups.values(),
                     key=lambda item: (item["level"] != "error", -item["count"]))

    known: dict[str, dict] = {}
    unknown: list[dict] = []
    critical_errors = 0
    for item in ordered:
        pattern = _known_pattern(str(item["source"]), str(item["message"]))
        if pattern:
            entry = known.setdefault(pattern["title"], {
                "title": pattern["title"],
                "note": pattern["note"],
                "count": 0,
                "examples": [],
            })
            entry["count"] += item["count"]
            if len(entry["examples"]) < 3:
                entry["examples"].append(str(item["message"])[:120])
        else:
            if item["level"] == "error":
                critical_errors += item["count"]
            unknown.append(item)

    return {
        "checked": len(lines),
        "errors": errors,
        "warnings": warnings,
        "notices": notices,
        "critical_errors": critical_errors,
        "known": sorted(known.values(), key=lambda item: -item["count"]),
        "top": unknown[:8],
    }


def _report_summary(report: dict) -> str:
    wan = report.get("wan") or {}
    internet = report.get("internet") or {}
    dns = report.get("dns") or {}
    proxy = report.get("proxy") or {}
    tailscale = report.get("tailscale") or {}
    logs = report.get("logs") or {}
    proxy_ok = bool(proxy.get("sing_box")) and bool(proxy.get("nft_table"))
    known = logs.get("known") or []
    summary = (
        f"Роутер {report.get('model') or '—'}: "
        f"WAN {'ок' if wan.get('up') else 'НЕТ'}, "
        f"интернет {'ок' if internet.get('ok') else 'НЕТ'}, "
        f"DNS {'ок' if dns.get('ok') else 'НЕТ'}, "
        f"прокси {'ок' if proxy_ok else 'НЕТ'}, "
        f"Tailscale {'ок' if tailscale.get('ip') else 'НЕТ'}; "
        f"аптайм {report.get('uptime_text') or '—'}, "
        f"журнал: критичных ошибок {logs.get('critical_errors', 0)}, "
        f"предупреждений {logs.get('warnings', 0)}"
    )
    if known:
        summary += "; известные сообщения: " + ", ".join(
            f"{item.get('title')} ×{item.get('count')}" for item in known[:4]
        )
    return summary[:900]


def router_report(args: dict) -> dict:
    """Подробный отчёт о роутере: только чтение, без изменения настроек."""
    lines = int(args.get("log_lines") or 400)
    lines = max(50, min(lines, 2000))
    # В шаблоне жёстко 400 строк; для другого значения подменяем хвост команды.
    script = REPORT_SCRIPT.replace("tail -n 400", f"tail -n {lines}")
    raw = router.run(script)
    sections = _sections(raw)

    src_ip = (sections.get("src") or "").strip().split(" ")[0]
    uptime_seconds = 0.0
    if sections.get("uptime"):
        try:
            uptime_seconds = float(sections["uptime"].split()[0])
        except (ValueError, IndexError):
            pass

    load: list[float] = []
    for value in (sections.get("load") or "").split()[:3]:
        try:
            load.append(float(value))
        except ValueError:
            pass

    leases_text = (sections.get("leases") or "").strip()
    leases = int(leases_text) if leases_text.isdigit() else 0

    report = {
        "summary": "",
        "host": router.host,
        "kind": router.kind,
        "model": (sections.get("model") or "unknown").strip(),
        "uptime_seconds": int(uptime_seconds),
        "uptime_text": _uptime_text(uptime_seconds),
        "load": load,
        "memory": _parse_meminfo(sections.get("meminfo") or ""),
        "disks": _parse_disks(sections.get("disk") or ""),
        "wan": _parse_wan(sections.get("wan") or ""),
        "internet": _parse_ping(sections.get("ping") or ""),
        "dns": _parse_dns(sections.get("dns") or ""),
        "proxy": _parse_proxy(sections.get("proxy") or ""),
        "tailscale": _parse_tailscale(sections.get("tailscale") or ""),
        "clients": {
            "dhcp_leases": leases,
            "wifi": _parse_wifi(sections.get("wifi") or ""),
        },
        "logs": _parse_logs(sections.get("logs") or "", src_ip=src_ip),
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    report["summary"] = _report_summary(report)
    return report


def exec_run(args: dict) -> dict:
    """Произвольная команда на роутере по SSH (только полный режим)."""
    if not FULL_ACCESS:
        raise PermissionError("exec.run доступен только при ACCESS_MODE=full")
    command = str(args["command"]).strip()
    if not command:
        raise ValueError("пустая команда")
    # В отличие от шаблонных команд результат важен честно: неудачная
    # команда не должна выглядеть успешной.
    code, output = router.run_status(command)
    return {"command": command, "exit_code": code,
            "stdout": output[:64_000], "ok": code == 0,
            "truncated": len(output) > 64_000}


def fs_write(args: dict) -> dict:
    if not FULL_ACCESS:
        raise PermissionError("fs.write доступен только при ACCESS_MODE=full")
    import base64 as _b64
    import shlex as _shlex
    path = _shlex.quote(str(args["path"]))
    text = str(args["text"])
    op = ">>" if args.get("append") else ">"
    payload = _b64.b64encode(text.encode("utf-8")).decode("ascii")
    router.run(f"mkdir -p \"$(dirname {path})\" 2>/dev/null; "
               f"echo '{payload}' | base64 -d {op} {path}")
    return {"path": str(args["path"]), "bytes": len(text.encode("utf-8")),
            "mode": "append" if args.get("append") else "overwrite"}


def fs_delete(args: dict) -> dict:
    if not FULL_ACCESS:
        raise PermissionError("fs.delete доступен только при ACCESS_MODE=full")
    import shlex as _shlex
    path = str(args["path"])
    flag = "-rf" if args.get("recursive") else "-f"
    router.run(f"rm {flag} {_shlex.quote(path)}")
    return {"deleted": path}


def fs_mkdir(args: dict) -> dict:
    if not FULL_ACCESS:
        raise PermissionError("fs.mkdir доступен только при ACCESS_MODE=full")
    import shlex as _shlex
    path = str(args["path"])
    router.run(f"mkdir -p {_shlex.quote(path)}")
    return {"created": path}


HANDLERS = {
    "net.status": net_status,
    "router.report": router_report,
    "router.clients": router_clients,
    "router.wifi": router_wifi,
    "router.kick": router_kick,
    "router.reboot": router_reboot,
    "exec.run": exec_run,
    "fs.write": fs_write,
    "fs.delete": fs_delete,
    "fs.mkdir": fs_mkdir,
}


def main() -> None:
    client = AgentClient(
        device_id=os.getenv("ROUTER_DEVICE_ID", "router"),
        name=os.getenv("ROUTER_DEVICE_NAME", "Роутер"),
        platform="router",
        handlers=HANDLERS,
    )
    client.run()


if __name__ == "__main__":
    main()
