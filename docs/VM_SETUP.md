# Лаборатория в виртуальной машине

Цель: всё, включая опасные команды, отрабатывается в ВМ — реальный ПК, роутер
и телефон не участвуют, пока лаборатория не станет стабильной.

## Что где живёт

| Компонент | Где |
|---|---|
| Ядро + дашборд | ВМ `assistant-hub` (Ubuntu 24.04) |
| LLM-планировщик | там же: CLI `opencode` с бесплатными моделями |
| `fake_agent` ×3 | там же (systemd-юниты) |
| Tailscale | там же: телефон и ПК видят ядро по tailnet-адресу |
| Реальный ПК / роутер / телефон | только после стабильной лаборатории |

## Как устроена автоматика

ВМ создаётся **из cloud-образа Ubuntu**, а не установщиком: гость уже
установлен, cloud-init по seed-диску создаёт пользователя `assistant`,
кладет SSH-ключ и поднимает SSH. Дальше проект копируется по scp, а
`deploy/provision_vm.sh` ставит зависимости и systemd-юниты.

```
tools/enable_hyperv.ps1   включить Hyper-V (один UAC + перезагрузка)
tools/make_seed_vhd.ps1   VHD с меткой CIDATA: user-data, meta-data
tools/create_lab_vm.ps1   ВМ Gen1 из cloud-образа (+ seed-диск)
tools/lab_up.ps1          всё вместе: seed → ВМ → IP → SSH → проект
tools/lab_provision.ps1   копирование проекта и provision внутри ВМ
tools/vm_checkpoint.ps1   снимки и откат
tools/vm_diag.ps1         диагностика: диски ВМ, seed, сеть
tools/vm_console_shot.ps1 скриншот консоли ВМ в PNG
tools/resize_lab_vm.ps1   память/процессоры ВМ (нужны для планировщика)
tools/run_elevated.ps1    запуск скрипта с UAC и логом
```

## Шаг 1. Hyper-V (один раз)

```powershell
.\tools\run_elevated.ps1 -Script .\tools\enable_hyperv.ps1
```

Подтвердите UAC. Скрипт включит `Microsoft-Hyper-V-All` и попросит
перезагрузку. **Перезагрузитесь** и запустите его ещё раз — он проверит
службу `vmms` и модуль Hyper-V.

## Шаг 2. Образ и ключ (без прав администратора)

```powershell
New-Item -ItemType Directory -Force C:\VMs\images | Out-Null
curl.exe -L -o C:\VMs\images\ubuntu-24.04-generic.img `
  https://cloud-images.ubuntu.com/releases/24.04/release/ubuntu-24.04-server-cloudimg-amd64.img
curl.exe -L -o C:\VMs\images\SHA256SUMS `
  https://cloud-images.ubuntu.com/releases/24.04/release/SHA256SUMS
Get-FileHash C:\VMs\images\ubuntu-24.04-generic.img -Algorithm SHA256

cmd /c 'ssh-keygen -t ed25519 -f "C:\VMs\assistant-hub\id_ed25519" -N "" -q -C net-assistant-lab'
```

Образ скачивается в формате qcow2, Hyper-V его не читает — конвертируем в VHDX
через `qemu-img`:

```powershell
winget install --id SoftwareFreedomConservancy.QEMU -e --silent
& "C:\Program Files\qemu\qemu-img.exe" convert -f qcow2 -O vhdx `
  -o subformat=dynamic C:\VMs\images\ubuntu-24.04-generic.img `
  C:\VMs\images\ubuntu-24.04-generic.vhdx
```

> **Почему не Azure-образ.** `...-azure.vhd` выглядит удобнее (уже VHD), но его
> cloud-init настроен только на datasource Azure: seed-диск с меткой CIDATA он
> игнорирует, гость загружается как `ubuntu` без SSH-ключа. Обычный cloud-образ
> подхватывает NoCloud и настраивается сам.

## Шаг 3. Поднять лабораторию

```powershell
.\tools\run_elevated.ps1 -Script .\tools\lab_up.ps1
```

Скрипт по шагам: создаёт внешний коммутатор `lab-external` на физическом адаптере
(bridged) → создаёт seed-диск → копирует cloud-образ в диск ВМ и расширяет его
до 24 ГБ → создаёт ВМ (4 ГБ, 2 vCPU) → запускает → ждёт адрес, который выдал
**домашний роутер**, и SSH → копирует проект в `/opt/net-assistant` → запускает
`provision_vm.sh`.

Адрес ВМ — обычный DHCP-адрес в домашней сети (например `192.168.1.160`).
Скрипт находит его двумя способами: через Hyper-V Integration Services, а если
гость не отдаёт IP — пингует подсеть и ищет MAC-адрес ВМ в ARP-таблице.

Через несколько минут в консоли появится адрес, например:

```
Адрес ВМ: 172.20.10.5
Ядро и дашборд: http://172.20.10.5:8787
```

Если ВМ уже есть и нужно только обновить проект:

```powershell
.\tools\lab_provision.ps1 -Ip <адрес ВМ>
```

### Память и процессоры

Планировщику на бесплатных моделях OpenCode нужен запас памяти: CLI —
тяжёлый бинарь, при нехватке его убивает OOM-киллер. Дайте ВМ **8 ГБ
фиксированной** памяти и **4 vCPU**:

```powershell
.\tools\run_elevated.ps1 -Script .\tools\resize_lab_vm.ps1 `
  -ScriptArgs "-MemoryMB","8192","-Cpu","4"
```

Скрипт штатно гасит ВМ (при необходимости — питанием), выставляет память как
**статическую** (`-StaticMemory`, а не `-DynamicMemory:$false` — это ключ-переключатель
и так память не меняет) и снова включает ВМ. Динамическую память включать не
стоит: гость ужимается до ~0,7 ГБ, и OpenCode падает.

Проверка в ВМ: `free -m` должен показать ~7,9 ГБ, `nproc` — 4.

## Шаг 4. Проверка

```powershell
.\tools\smoke_test.ps1 -Base http://<адрес ВМ>:8787
```

13 зелёных проверок: ядро, агенты, скриншот, подтверждения, каталог, токен,
аудит, дашборд, голосовой эндпоинт. Дашборд — в браузере, токен из `.env`.

Внутри ВМ:

```bash
systemctl status net-assistant-core
journalctl -u net-assistant-core -f
systemctl status 'net-assistant-fake@*'
```

## Шаг 5. Роутер-заглушка (по желанию)

Второй ВМ с SSH: можно взять тот же cloud-образ и второй seed-диск,
либо обычную Ubuntu. В `/opt/net-assistant/.env` на ВМ №1:

```ini
ROUTER_HOST=<адрес второй ВМ>
ROUTER_USER=assistant
ROUTER_PASSWORD=<пароль>
ROUTER_KIND=openwrt
```

Запуск: `sudo -u assistant .venv/bin/python -m agents.router_agent`.

## Шаг 6. Реальные устройства (Tailscale)

1. В ВМ: `curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up`
   (вход по ссылке в браузере; у нас получился адрес `100.67.117.60`).
2. На телефоне и ПК: Tailscale, тот же аккаунт.
3. `tailscale ip -4` → адрес ВМ.
4. На агентах: `CORE_URL=ws://<tailscale-ip>:8787`.
5. Порты в интернет не пробрасываются.

> **Узел показывается `offline`.** Домашняя сеть рвёт простаивающие TCP-соединения
> примерно за 3 минуты, поэтому Tailscale периодически теряет long-poll к
> координатору (симптом в логе: `control: map response long-poll timed out!`), и
> узел ВМ пропадает из tailnet — телефон перестаёт видеть ядро.
>
> Перезапуск `sudo systemctl restart tailscaled` это лечит, но вручную делать
> ничего не нужно: в ВМ работает **сторож** `tailscale-watchdog.timer`. Раз в
> минуту он проверяет состояние (`tailscale status --json`) и перезапускает
> `tailscaled`, если узел offline или карта не обновляется (не чаще раза в 2
> минуты). Сторож ставится шагом `deploy/provision_vm.sh`; файлы —
> `deploy/tailscale_watchdog.py`, `.service`, `.timer`.
>
> ```bash
> systemctl status tailscale-watchdog.timer      # включён?
> tail -f /var/log/tailscale-watchdog.log        # когда перезапускал
> ```

Телефон настраивается по `docs/PHONE_SETUP.md` (Termux, Termux:API, дашборд как
приложение).

## Шаг 7. Бесплатные модели OpenCode (планировщик)

Ставится в ВМ рядом с ядром:

```bash
curl -fsSL https://opencode.ai/install | bash
export PATH="$HOME/.opencode/bin:$PATH"
opencode --version
opencode run -m opencode/deepseek-v4.1-flash-free "Say hi" < /dev/null
```

Затем в `/opt/net-assistant/.env` (см. `.env.example`):

```ini
LLM_BACKEND=auto
OPENCODE_BIN=/home/assistant/.opencode/bin/opencode
OPENCODE_MODEL=opencode/deepseek-v4.1-flash-free
OPENCODE_TIMEOUT=150
OPENCODE_AGENT=
```

```bash
sudo systemctl restart net-assistant-core
curl -s localhost:8787/health     # "llm_backend":"opencode", "llm_configured":true
```

Проверка end-to-end:

```bash
curl -s -X POST localhost:8787/chat \
  -H "Authorization: Bearer $ASSISTANT_TOKEN" -H "Content-Type: application/json" \
  -d '{"message":"Узнай сведения о системе на компьютере"}'
```

Грабли (свои агенты и `opencode.json` бесплатный тариф отклоняет, обязательный
`stdin=/dev/null`, требования к памяти) разобраны в `docs/OPENCODE_PLANNER.md`.

## Контрольные точки

```powershell
.\tools\vm_checkpoint.ps1 -Name assistant-hub -Create -Snapshot before-router
.\tools\vm_checkpoint.ps1 -Name assistant-hub -List
.\tools\vm_checkpoint.ps1 -Name assistant-hub -Restore -Snapshot before-router
```

## Установка вручную (альтернатива)

Если cloud-образ не подошёл — обычный установщик:

```powershell
.\tools\run_elevated.ps1 -Script .\tools\create_lab_vm.ps1 `
  -ScriptArgs "-IsoPath","C:\iso\ubuntu-24.04-live-server-amd64.iso"
```

Далее установка через `vmconnect localhost assistant-hub`, пользователь
`assistant`, OpenSSH server — и `.\tools\lab_provision.ps1 -Ip <адрес>`.

## Чек-лист «не сломать ПК»

- [ ] Всё выполняется в ВМ, реальный `pc_agent` не запущен.
- [ ] Домашний роутер не указан в `ROUTER_HOST`.
- [ ] Перед экспериментами — снимок.
- [ ] `REQUIRE_CONFIRM=true`.
- [ ] `.env` не в git.
- [ ] Проект лучше держать вне OneDrive (`C:\lab\net-assistant`).
