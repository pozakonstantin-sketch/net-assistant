#Requires -Version 5.1
<#
.SYNOPSIS
    Автонастройка телефона Honor по USB: установка Termux, Termux:API, Termux:Boot
    и запуск установщика агента.

.DESCRIPTION
    Требует включённой отладки по USB. Скрипт:
      1. ждёт, пока adb увидит авторизованное устройство;
      2. ставит APK из C:\lab\tools\apk (F-Droid версии);
      3. выдаёт Termux разрешения, снимает ограничения батареи;
      4. открывает Termux и копирует туда команду установки агента.

    Установку агента внутри Termux нужно запустить вручную (Android не даёт
    выполнять команды в чужом терминале без root) — скрипт печатает готовую
    строку и кладёт её в буфер обмена ПК.

.EXAMPLE
    .\tools\phone_setup.ps1
#>
param(
    [string]$Adb = "C:\lab\tools\platform-tools\adb.exe",
    [string]$ApkDir = "C:\lab\tools\apk",
    [string]$CoreUrl = "http://100.67.117.60:8787",
    # Токен не хранится в репозитории: по умолчанию читается из .env проекта.
    [string]$Token = "",
    [int]$TimeoutMinutes = 5,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Continue"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Токен берём из .env (или из параметра -Token), чтобы не держать секрет в коде.
if (-not $Token) {
    $envFile = Join-Path (Split-Path -Parent $PSScriptRoot) ".env"
    if (Test-Path $envFile) {
        $line = Get-Content $envFile -Encoding UTF8 | Where-Object { $_ -match '^\s*ASSISTANT_TOKEN\s*=' } | Select-Object -First 1
        if ($line) { $Token = ($line -split '=', 2)[1].Trim() }
    }
}
if (-not $Token) {
    Write-Host "Не найден ASSISTANT_TOKEN: укажите -Token или создайте .env (см. .env.example)." -ForegroundColor Red
    exit 1
}

function Write-Step($text) { Write-Host "`n== $text ==" -ForegroundColor Cyan }
function Write-Ok($text) { Write-Host "   OK  $text" -ForegroundColor Green }
function Write-Warn($text) { Write-Host "   !!  $text" -ForegroundColor Yellow }

if (-not (Test-Path $Adb)) { throw "adb не найден: $Adb" }

Write-Step "Ожидание телефона (до $TimeoutMinutes мин)"
Write-Host "   Включите на телефоне: Настройки → О телефоне → 7 раз «Номер сборки»" -ForegroundColor DarkGray
Write-Host "   затем: Настройки → Система → Для разработчиков → Отладка по USB" -ForegroundColor DarkGray
Write-Host "   и подтвердите «Разрешить отладку» на экране телефона." -ForegroundColor DarkGray

& $Adb start-server 2>$null | Out-Null
$deadline = (Get-Date).AddMinutes($TimeoutMinutes)
$serial = $null
while ((Get-Date) -lt $deadline) {
    $lines = @(& $Adb devices 2>$null)
    $ready = @($lines | Where-Object { $_ -match '^\S+\s+device$' })
    $unauth = @($lines | Where-Object { $_ -match '^\S+\s+unauthorized$' })
    if ($ready.Count -gt 0) { $serial = ($ready[0] -split '\s+')[0]; break }
    if ($unauth) {
        Write-Host "   Телефон виден, но не авторизован — подтвердите запрос на экране." -ForegroundColor Yellow
    }
    Start-Sleep -Seconds 3
}

if (-not $serial) {
    Write-Host ""
    Write-Host "Телефон не появился. Проверьте:" -ForegroundColor Red
    Write-Host "  • Отладка по USB включена?" -ForegroundColor DarkGray
    Write-Host "  • При подключении выбран режим «Передача файлов» (не «Только зарядка»)?" -ForegroundColor DarkGray
    Write-Host "  • На экране телефона подтверждён запрос «Разрешить отладку по USB»?" -ForegroundColor DarkGray
    Write-Host "  • Кабель поддерживает передачу данных?" -ForegroundColor DarkGray
    exit 1
}
Write-Ok "устройство: $serial"
$env:ANDROID_SERIAL = $serial

$model = (& $Adb shell getprop ro.product.model 2>$null | Out-String).Trim()
$android = (& $Adb shell getprop ro.build.version.release 2>$null | Out-String).Trim()
Write-Ok "модель: $model, Android $android"

if (-not $SkipInstall) {
    Write-Step "Установка приложений"
    foreach ($apk in Get-ChildItem (Join-Path $ApkDir "*.apk") | Sort-Object Name) {
        Write-Host "   ставим $($apk.Name)…" -NoNewline
        $out = & $Adb install -r -g $apk.FullName 2>&1
        if ($out -match "Success") { Write-Host " готово" -ForegroundColor Green }
        elseif ($out -match "INSTALL_FAILED_ALREADY_EXISTS|already exists") { Write-Host " уже установлено" -ForegroundColor Yellow }
        else { Write-Host "`n      $out" -ForegroundColor Red }
    }
}

Write-Step "Разрешения и батарея"
$packages = @("com.termux", "com.termux.api", "com.termux.boot")
foreach ($pkg in $packages) {
    $present = (& $Adb shell pm list packages $pkg 2>$null) -match [regex]::Escape($pkg)
    if (-not $present) { Write-Warn "$pkg не установлен"; continue }

    # Разрешения Termux:API (SMS, уведомления, локация, камера, контакты)
    foreach ($perm in @(
            "android.permission.SEND_SMS", "android.permission.READ_SMS", "android.permission.RECEIVE_SMS",
            "android.permission.READ_CONTACTS", "android.permission.ACCESS_FINE_LOCATION",
            "android.permission.ACCESS_COARSE_LOCATION", "android.permission.CAMERA",
            "android.permission.RECORD_AUDIO", "android.permission.POST_NOTIFICATIONS",
            "android.permission.READ_CALL_LOG", "android.permission.WRITE_SETTINGS",
            "android.permission.SYSTEM_ALERT_WINDOW")) {
        & $Adb shell pm grant $pkg $perm 2>$null | Out-Null
    }

    # Отключить оптимизацию батареи (иначе Honor усыпит агент)
    & $Adb shell cmd deviceidle whitelist +$pkg 2>$null | Out-Null
    & $Adb shell dumpsys deviceidle whitelist 2>$null | Out-Null
    Write-Ok "${pkg}: разрешения выданы, батарея без ограничений"
}

Write-Step "Фоновая работа"
# Разрешить автозапуск в Honor: adb-команды для этого не стандартизированы,
# поэтому просто напоминаем — это единственный шаг, который нельзя автоматизировать.
Write-Warn "Вручную на телефоне: Настройки → Батарея → Запуск приложений →"
Write-Host "        Termux, Termux:API, Termux:Boot → «Управлять вручную»," -ForegroundColor DarkGray
Write-Host "        включить все три пункта (автозапуск, косвенный запуск, работа в фоне)." -ForegroundColor DarkGray
Write-Host "        Это важно: без этого Honor выгружает агент." -ForegroundColor DarkGray

Write-Step "Проверка сети с телефона"
$tailnet = & $Adb shell "curl -s -m 8 -o /dev/null -w '%{http_code}' $CoreUrl/health" 2>$null
if ($tailnet -match "200") {
    Write-Ok "телефон достаёт ядро по tailnet ($CoreUrl)"
} else {
    Write-Warn "телефон не достал ядро по tailnet (код: $tailnet)"
    Write-Host "        Откройте на телефоне Tailscale и включите его." -ForegroundColor DarkGray
}

Write-Step "Команда установки агента"
$cmd = "curl -fsSL `"$CoreUrl/android/install.sh?token=$Token`" | bash"
Set-Clipboard -Value $cmd
Write-Host "   Команда скопирована в буфер обмена ПК." -ForegroundColor Green
Write-Host "   Открываю Termux на телефоне — вставьте её длинным нажатием → Вставить." -ForegroundColor Green
Write-Host ""
Write-Host "   $cmd" -ForegroundColor White

& $Adb shell monkey -p com.termux -c android.intent.category.LAUNCHER 1 2>$null | Out-Null
Write-Ok "Termux запущен на телефоне"

Write-Host ""
Write-Host "Готово. После установки проверьте в Termux: tail -f ~/agent.log" -ForegroundColor Cyan
