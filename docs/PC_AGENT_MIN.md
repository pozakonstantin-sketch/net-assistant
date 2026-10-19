# Минимальный агент ПК (без полного проекта)

Хаб живёт на ВМ `astra-hub` (`/opt/net-assistant`), полный исходник — в этом
репозитории. На самом ПК держать весь проект не нужно: агенту достаточно
шести файлов, `.env` и своего окружения. Так ПК остаётся «тонким» клиентом.

## Что оставить на ПК

```
C:\lab\pc-agent\
  .env                     настройки агента (CORE_URL, ASSISTANT_TOKEN, ACCESS_MODE=full)
  agents\__init__.py
  agents\common.py
  agents\pc_agent.py
  core\__init__.py
  core\config.py
  core\protocol.py
  .venv\                   Python 3.15: websockets, pillow, python-dotenv
  data\                    логи (создаётся сам)
```

Больше ничего из проекта агенту не нужно: `pc_agent` импортирует только
`agents.common`, `core.config` и `core.protocol`, а те — лишь `websockets`.

## Как собрать

Из корня репозитория:

```powershell
$dst = "C:\lab\pc-agent"
New-Item -ItemType Directory -Force -Path "$dst\agents","$dst\core" | Out-Null
Copy-Item agents\__init__.py,agents\common.py,agents\pc_agent.py "$dst\agents\"
Copy-Item core\__init__.py,core\config.py,core\protocol.py   "$dst\core\"
Copy-Item .env "$dst\.env"
python -m venv "$dst\.venv"
& "$dst\.venv\Scripts\pip" install -q websockets pillow python-dotenv
```

## Задача планировщика

Агент работает в сессии пользователя (иначе нет рабочего стола, скриншотов,
громкости и буфера обмена), с повышенными правами:

```
C:\lab\pc-agent\.venv\Scripts\pythonw.exe -m agents.pc_agent
рабочий каталог: C:\lab\pc-agent
```

```powershell
$action = New-ScheduledTaskAction -Execute "C:\lab\pc-agent\.venv\Scripts\pythonw.exe" `
  -Argument "-m agents.pc_agent" -WorkingDirectory "C:\lab\pc-agent"
Register-ScheduledTask -TaskName NetAssistantPcAgent -Action $action `
  -Trigger (New-ScheduledTaskTrigger -AtLogOn) `
  -Settings (New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)) `
  -Principal (New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Highest) -Force
```

## Проверка

```powershell
Get-Content C:\lab\pc-agent\data\agent-pc-desktop.log -Tail 5
# INFO agent: подключено к ядру как «pc-desktop» (15 команд)
```

В хабе устройство видно в `/devices` (`pc-desktop`, `online = true`).

## Что ещё остаётся на ПК

- **Tailscale** — сеть до хаба и телефона, без проброса портов.
- Папка `C:\lab\tools` (`adb`, platform-tools) — только для настройки телефона;
  на работу агента не влияет.
