# Хаб на Astra Linux SE 1.8.1.6 (новый «боевой» узел)

Документ описывает перевод ядра ассистента на **Astra Linux Special Edition
1.8.1.6** (уровень защищённости «Воронеж», `mode: 1`) в Hyper-V. Прежняя ВМ
`assistant-hub` (Ubuntu 24.04) страдала от постоянных обрывов long-poll
`tailscaled`; новая ВМ `astra-hub` нужна в том числе чтобы проверить, повторится
ли это на другом дистрибутиве.

## Что где живёт

| Компонент | Где |
|---|---|
| Ядро + дашборд | ВМ `astra-hub` (Astra Linux SE 1.8.1.6), `/opt/net-assistant` |
| LLM-планировщик | там же: CLI `opencode`, бесплатные модели |
| Агент роутера | там же: `router_agent` (SSH на роутер) |
| Агент ПК | Windows-ПК, задача планировщика `NetAssistantPcAgent` |
| Панель (Telegram Mini App) | `tailscale serve` на ВМ → `https://astra-hub.<тейлнет>.ts.net` |

## 1. Параметры ВМ

Gen 2 / UEFI, SecureBoot выключен (иначе чужие подписи Astra не проходят),
4 vCPU, **8 ГБ фиксированной** памяти, 100 ГБ динамический диск,
коммутатор `lab-external` (bridged), DVD первым в загрузке.

```powershell
.\tools\create_vm.ps1 -Name assistant -Iso D:\VMs\images\astra-se-1.8.1.6-install.iso `
  -MemoryGB 8 -CpuCount 4 -DiskGB 100 -Switch lab-external
```

## 2. Автоустановка (preseed)

Установщик Astra понимает **свой** YAML-preseed; интерактивный Qt-мастер не нужен.

```bash
# в живом окружении (загрузка с ISO):
astra-installer-console --preseed /tmp/preseed.yaml --locale ru_RU
```

Ключевые моменты (полный файл — `D:\VMs\seed\preseed.yaml`):

```yaml
license: true
mode: 1            # 0 — Орёл, 1 — Воронеж, 2 — Смоленск
host: assistant
timezone: Europe/Moscow
user:
  username: assistant
  password: <пароль>
tasks: [Base, Fly, Fly-ssh, Internet]
storage:
  template: default
  device: /dev/sda
  gpt: true
late_command: |    # выполняется в живом окружении, пути с /target
  ...
```

Грабли:

- `--locale ru_RU.UTF-8` падает (`main_help_ru_RU.UTF-8.txt`); нужен `ru_RU`.
- `late_command_intarget` оборачивается в `chroot … /bin/bash -c '…'` —
  **одинарные кавычки внутри запрещены**.
- `pkill -f <шаблон>` внутри команды убивает сам `bash -c`, если шаблон совпал
  с его командной строкой — используйте `astra-installer[-]qt`.
- Ключ `host` может не примениться — hostname проверьте и поправьте вручную.

## 3. Что поправить сразу после установки

`late_command` кладёт SSH-ключ, но два пункта на Astra приходится править руками:

1. **sudo без пароля.** `/etc/sudoers` содержит строку **после** `includedir`:

   ```
   %astra-admin ALL=(ALL:ALL) ALL
   ```

   Она перекрывает любой drop-in в `/etc/sudoers.d/` (последнее совпадение
   побеждает). Замените на:

   ```
   %astra-admin ALL=(ALL:ALL) NOPASSWD:ALL
   ```

   Затем `visudo -c` и `chmod 440 /etc/sudoers`.

2. **hostname и /etc/hosts:**

   ```bash
   sudo hostnamectl set-hostname assistant
   printf 'assistant\n' | sudo tee /etc/hostname
   sudo sed -i '/^127\.0\.1\.1/d' /etc/hosts
   printf '127.0.1.1\tassistant\n' | sudo tee -a /etc/hosts
   ```

## 4. Разворачивание проекта

Репозиторий приватный, поэтому копируем архивом (не `git clone`):

```powershell
tar -czf net-assistant.tar.gz --exclude=.venv --exclude=.git --exclude=data `
  --exclude=__pycache__ --exclude=.env .
scp -i C:\VMs\assistant-hub\id_ed25519 net-assistant.tar.gz assistant@<ip>:/home/assistant/
```

```bash
sudo mkdir -p /opt/net-assistant
sudo tar -xzf ~/net-assistant.tar.gz -C /opt/net-assistant
sudo chmod -R u+rwX /opt/net-assistant        # bsdtar из Windows даёт каталоги 0555
sudo chown -R assistant:assistant /opt/net-assistant
sudo INSTALL_DIR=/opt/net-assistant FAKE_AGENTS="" bash /opt/net-assistant/deploy/provision_vm.sh
```

`.env` переносится отдельно (в архив не входит): `ASSISTANT_TOKEN`,
`TELEGRAM_*`, `ROUTER_*`, `OPENCODE_*`, `ACCESS_MODE=full`,
`REQUIRE_CONFIRM=true`.

`FAKE_AGENTS=""` — на боевом хабе фальшивые агенты не нужны.

## 5. Python и pip (apt на Astra почти пуст)

В установленном Astra подключён только DVD-репозиторий; `python3-venv` и
`python3-pip` в нём нет, а `ensurepip` отсутствует. Поэтому:

```bash
python3 -c "import ensurepip" || {
  curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
  sudo python3 /tmp/get-pip.py --break-system-packages
  sudo python3 -m pip install --break-system-packages virtualenv
}
sudo -u assistant python3 -m virtualenv -q /opt/net-assistant/.venv
sudo -u assistant /opt/net-assistant/.venv/bin/pip install -r /opt/net-assistant/requirements.txt
```

`provision_vm.sh` умеет и то и другое сам (apt — «лучшее усилие»).

## 6. Tailscale

Ставится **статическими бинарниками** (в apt Astra его нет):

```bash
sudo bash /opt/net-assistant/deploy/install_tailscale_static.sh
sudo tailscale up --auth-key=tskey-auth-... --hostname=astra-hub \
    --accept-dns=false --operator=assistant
sudo tailscale serve --bg --https=443 http://127.0.0.1:8787
```

> **Главная грабля — `--accept-routes`.**
> Домашний роутер (OpenWrt `192.168.1.1`) анонсирует подсеть `192.168.1.0/24`
> и работает exit-node. Если узел принимает маршруты, ВМ «перекрывает» свою
> LAN-маршрутизацию: SSH и ping пропадают сразу после `tailscale up`, узел
> уходит в `offline`. Чинить из консоли Hyper-V:
>
> ```bash
> sudo tailscale set --accept-routes=false --exit-node=
> ```
>
> `tailscale serve` при этом не нужен для работы — панель отдаётся по HTTPS
> через сам демон.

Вход по ссылке в браузере на этом узле ненадёжен (auth-путь «сгорает» при
пересоздании ключа узла); проще использовать **auth-key** из админ-консоли
(Settings → Keys).

Графическая часть: у Tailscale есть трей `tailscale systray`; он прописан в
`/etc/xdg/autostart/tailscale-systray.desktop`. OpenCode — консольная
программа, ей соответствует пункт меню `opencode.desktop`
(`konsole -e ~/.opencode/bin/opencode`).

## 7. Панель и Telegram

```bash
sudo sed -i 's|^TELEGRAM_WEBAPP_URL=.*|TELEGRAM_WEBAPP_URL=https://astra-hub.<тейлнет>.ts.net|' \
    /opt/net-assistant/.env
sudo systemctl restart net-assistant-telegram
```

Проверка: `curl -s https://astra-hub.<тейлнет>.ts.net/health` с ПК.

На старой ВМ **обязательно** остановить `net-assistant-telegram` — два
поллера на один токен дают `409 Conflict` в `getUpdates`.

## 8. Агент ПК на новый хаб

В `C:\lab\net-assistant\.env`:

```ini
CORE_URL=ws://100.67.45.90:8787      # tailnet-адрес astra-hub
```

```powershell
Stop-ScheduledTask NetAssistantPcAgent; Start-ScheduledTask NetAssistantPcAgent
Get-Content .\data\agent-pc-desktop.log -Tail 5   # «подключено к ядру как pc-desktop»
```

## 9. Мониторинг стабильности `tailscaled`

Ради этого всё и затевалось. Временная служба пишет состояние раз в 30 с:

```bash
systemctl status ts-monitor          # /usr/local/bin/ts-monitor.sh
tail -f /var/log/ts-monitor.log      # state=Running longpoll_to=0 ping=pong …
```

Признак прежней проблемы — `longpoll_to` больше нуля и `state` не `Running`.
Дополнительно работает `tailscale-watchdog.timer` (перезапускает демон при
устойчивом отказе ≥90 с и никогда во время загрузки TLS-сертификата).

## Чек-лист отката

Пока новая ВМ не подтверждена, старую не удаляем: на ПК остаётся агент и
`tailscale serve` как запасной путь к панели; на `assistant-hub` можно вернуть
`systemctl start net-assistant-telegram`.
