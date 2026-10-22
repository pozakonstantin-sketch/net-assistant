#Requires -Version 5.1
<#
.SYNOPSIS
    Автозапуск агента ПК при входе в систему.

.DESCRIPTION
    Почему задача планировщика, а не служба Windows: служба работает в сессии 0
    и не видит рабочий стол, поэтому скриншоты, громкость, буфер обмена и
    блокировка экрана были бы недоступны. Задача при входе запускается в вашей
    сессии и сохраняет полный доступ, а безопасность обеспечивают белые списки
    самого агента (PC_SHELL_ALLOW, PC_FS_ROOTS) и каталог команд ядра.

.EXAMPLE
    .\tools\install_pc_agent.ps1
    .\tools\install_pc_agent.ps1 -RunNow
    .\tools\install_pc_agent.ps1 -Remove
#>
param(
    [string]$TaskName = "NetAssistantPcAgent",
    [string]$Python = "",
    [switch]$RunNow,
    [switch]$Remove,
    [switch]$Elevated,
    [string]$User = "",
    [string]$Password = ""
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$root = Split-Path -Parent $PSScriptRoot

if ($Remove) {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Задача «$TaskName» удалена." -ForegroundColor Green
    } else {
        Write-Host "Задачи «$TaskName» нет." -ForegroundColor Yellow
    }
    exit 0
}

if (-not $Python) {
    $candidate = Join-Path $root ".venv\Scripts\pythonw.exe"
    if (Test-Path $candidate) {
        $Python = $candidate
    } else {
        $candidate = Join-Path $root ".venv\Scripts\python.exe"
        if (Test-Path $candidate) { $Python = $candidate }
    }
}

if (-not $Python -or -not (Test-Path $Python)) {
    throw "Не найден Python агента. Создайте окружение (.venv) или укажите -Python <путь>."
}

$envPath = Join-Path $root ".env"
if (-not (Test-Path $envPath)) {
    throw "Нет файла .env — скопируйте .env.example и заполните токен."
}

Write-Host "Python агента: $Python" -ForegroundColor Cyan
Write-Host "Рабочий каталог: $root" -ForegroundColor Cyan

$action = New-ScheduledTaskAction -Execute $Python -Argument "-m agents.pc_agent" -WorkingDirectory $root
# Триггер строго на вход ТЕКУЩЕГО пользователя: без -UserId задача на AtLogOn
# по умолчанию срабатывает при входе любого, а чужой сессии агенту не нужно.
$trigger = New-ScheduledTaskTrigger -AtLogOn -UserId "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

$runLevel = if ($Elevated) { "Highest" } else { "Limited" }
if ($Elevated) {
    Write-Host "Режим: повышенные права (RunLevel Highest)." -ForegroundColor Yellow
}

if ($User) {
    if (-not $Password) {
        throw "Для запуска от другого пользователя нужен -Password (или создайте задачу вручную в Планировщике)."
    }
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -User $User -Password $Password -RunLevel $runLevel `
        -Description "Net Assistant: агент ПК (управление с телефона/роутера)" -Force | Out-Null
    Write-Host "Задача зарегистрирована от имени $User." -ForegroundColor Green
} else {
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
        -LogonType Interactive -RunLevel $runLevel
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal `
        -Description "Net Assistant: агент ПК (управление с телефона/роутера)" -Force | Out-Null
    Write-Host "Задача зарегистрирована для $env:USERNAME (только при входе в систему)." -ForegroundColor Green
}

if ($RunNow) {
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 2
}

$task = Get-ScheduledTask -TaskName $TaskName
$info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Host ""
Write-Host "Задача:  $($task.TaskName)" -ForegroundColor Cyan
Write-Host "Состояние: $($task.State)"
Write-Host "Последний запуск: $($info.LastRunTime)"
Write-Host ""
Write-Host "Проверить:  Get-Content (Join-Path '$root' 'data\agent-pc-desktop.log') -Tail 20" -ForegroundColor DarkGray
Write-Host "Удалить:    .\tools\install_pc_agent.ps1 -Remove" -ForegroundColor DarkGray
