#Requires -Version 5.1
<#
.SYNOPSIS
    Запускает указанный скрипт с правами администратора (через UAC) и пишет лог.

.DESCRIPTION
    Повышение прав происходит только на время выполнения одного скрипта:
    остальная сессия остаётся обычной. Windows покажет запрос UAC — без вашего
    подтверждения ничего не запустится.

    Вывод elevated-процесса нельзя перенаправить напрямую, поэтому он пишется
    в транскрипт, который этот скрипт затем печатает.

.PARAMETER Script
    Путь к .ps1, который нужно выполнить с правами администратора.

.PARAMETER ScriptArgs
    Аргументы для этого скрипта.

.EXAMPLE
    .\tools\run_elevated.ps1 -Script .\tools\enable_hyperv.ps1

.EXAMPLE
    .\tools\run_elevated.ps1 -Script .\tools\create_lab_vm.ps1 `
        -ScriptArgs "-IsoPath","C:\iso\ubuntu-24.04-live-server-amd64.iso"
#>
param(
    [Parameter(Mandatory = $true)][string]$Script,
    [string[]]$ScriptArgs = @(),
    [string]$LogPath = ""
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path $Script)) {
    throw "Скрипт не найден: $Script"
}
$Script = (Resolve-Path $Script).Path

if (-not $LogPath) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $LogPath = Join-Path (Join-Path $root "data") "elevated-$stamp.log"
}
New-Item -ItemType Directory -Path (Split-Path -Parent $LogPath) -Force | Out-Null

# Собираем вызов целевого скрипта с корректным экранированием аргументов.
# Аргументы, начинающиеся с дефиса, — это параметры/ключи, их нельзя брать в кавычки,
# иначе PowerShell примет их за строковое значение (например, имя ВМ).
$quotedScript = "'" + $Script.Replace("'", "''") + "'"
$quotedArgs = ($ScriptArgs | ForEach-Object {
        if ($_ -match '^-[A-Za-z]') { $_ }
        else { "'" + ($_ -replace "'", "''") + "'" }
    }) -join " "
$invocation = "& $quotedScript $quotedArgs".TrimEnd()

$wrapper = Join-Path $env:TEMP ("net-assistant-elevated-{0}.ps1" -f (Get-Random))
$wrapperBody = @"
param([string]`$Log)
`$ErrorActionPreference = 'Continue'
Start-Transcript -Path `$Log -Force | Out-Null
`$code = 0
try {
    $invocation
    if (`$null -ne `$LASTEXITCODE) { `$code = `$LASTEXITCODE }
} catch {
    Write-Host ('ОШИБКА: ' + `$_) -ForegroundColor Red
    `$code = 1
} finally {
    Stop-Transcript | Out-Null
}
exit `$code
"@
[IO.File]::WriteAllText($wrapper, $wrapperBody, (New-Object System.Text.UTF8Encoding($true)))

Write-Host "Запускаю с правами администратора:" -ForegroundColor Cyan
Write-Host "  $Script $($ScriptArgs -join ' ')"
Write-Host "Windows покажет запрос UAC — подтвердите его." -ForegroundColor Yellow
Write-Host ""

$args = @(
    "-NoProfile", "-ExecutionPolicy", "Bypass",
    "-File", "`"$wrapper`"",
    "-Log", "`"$LogPath`""
)

try {
    $process = Start-Process powershell.exe -Verb RunAs -ArgumentList $args -Wait -PassThru
} catch {
    Remove-Item $wrapper -Force -ErrorAction SilentlyContinue
    Write-Host "Повышение прав отменено (UAC отклонён или окно закрыто)." -ForegroundColor Red
    exit 1
}

$code = $process.ExitCode
Remove-Item $wrapper -Force -ErrorAction SilentlyContinue

Write-Host "== Вывод elevated-процесса ==" -ForegroundColor Cyan
if (Test-Path $LogPath) {
    Get-Content $LogPath -Encoding UTF8 | ForEach-Object { Write-Host $_ }
} else {
    Write-Host "(лог не создан)" -ForegroundColor Yellow
}
Write-Host ""
Write-Host "Код выхода: $code. Лог: $LogPath" -ForegroundColor $(if ($code -eq 0) { "Green" } else { "Red" })
exit $code
