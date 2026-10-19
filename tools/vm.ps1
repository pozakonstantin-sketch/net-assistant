#Requires -Version 5.1
<#
.SYNOPSIS
    Управление ВМ лаборатории: включить, выключить, перезагрузить, автозапуск.

.DESCRIPTION
    Обёртка над Hyper-V, чтобы не помнить команды. Сама просит UAC (Start-VM /
    Stop-VM / Set-VM требуют прав администратора), ждёт результата и печатает
    адрес ВМ.

    Команды:
      status        состояние ВМ, адрес, автозапуск (без UAC)
      start         запустить ВМ
      stop          корректно выключить гостя (shutdown через SSH, затем Stop-VM)
      off           выключить жёстко (Stop-VM -TurnOff)
      restart       перезагрузить гостя
      autostart on  включать ВМ при старте ПК (AutomaticStartAction=Start)
      autostart off выключать автозапуск (=Nothing)

.EXAMPLE
    .\tools\vm.ps1 status

.EXAMPLE
    .\tools\vm.ps1 start

.EXAMPLE
    .\tools\vm.ps1 autostart on
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet("status", "start", "stop", "off", "restart", "autostart")]
    [string]$Action = "status",

    [Parameter(Position = 1)]
    [ValidateSet("on", "off")]
    [string]$Mode = "on",

    [string]$Name = "assistant-hub",
    [string]$VmIp = "192.168.1.160",
    [string]$SshKey = "C:\VMs\assistant-hub\id_ed25519",
    [string]$SshUser = "assistant"
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Test-Elevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-ElevatedSelf {
    # Перезапускает этот же скрипт с теми же аргументами через UAC и ждёт конца.
    $self = $PSCommandPath
    $argList = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$self`"",
        $Action
    )
    if ($Action -eq "autostart") { $argList += $Mode }
    $argList += @("-Name", "`"$Name`"", "-VmIp", "`"$VmIp`"")

    Write-Host "Нужны права администратора — подтвердите запрос UAC." -ForegroundColor Yellow
    $proc = Start-Process powershell.exe -Verb RunAs -ArgumentList $argList -Wait -PassThru
    exit $proc.ExitCode
}

function Get-VmAddress {
    param([string]$VmName)

    # 1. Hyper-V (KVP) — если гость отдаёт адрес
    $adapter = Get-VMNetworkAdapter -VMName $VmName -ErrorAction SilentlyContinue
    $ip = $adapter.IPAddresses | Where-Object { $_ -match '^\d{1,3}(\.\d{1,3}){3}$' -and $_ -notlike '169.254.*' } |
        Select-Object -First 1
    if ($ip) { return $ip }

    # 2. Если ВМ запущена и адрес из настроек отвечает по SSH — он и есть
    $vm = Get-VM -Name $VmName -ErrorAction SilentlyContinue
    if ($vm -and $vm.State -eq "Running" -and $VmIp) {
        $client = New-Object Net.Sockets.TcpClient
        try {
            $client.Connect($VmIp, 22)
            if ($client.Connected) { return $VmIp }
        } catch { } finally { $client.Dispose() }
    }

    return $null
}

function Show-Status {
    $vm = Get-VM -Name $Name -ErrorAction SilentlyContinue
    if (-not $vm) {
        Write-Host "ВМ «$Name» не найдена." -ForegroundColor Red
        return
    }
    $addr = Get-VmAddress -VmName $Name
    $addrNote = ""
    if (-not $addr) {
        $addr = $VmIp
        $addrNote = " (не проверен: ВМ выключена или SSH не отвечает)"
    }

    $autostart = switch ($vm.AutomaticStartAction) {
        "Start" { "включён (Start)" }
        "StartIfRunning" { "включён (StartIfRunning)" }
        default { "выключен ($($vm.AutomaticStartAction))" }
    }

    Write-Host ""
    Write-Host "ВМ:        $($vm.Name)" -ForegroundColor Cyan
    Write-Host "Состояние: $($vm.State)"
    Write-Host "Адрес:     $addr$addrNote"
    Write-Host "Автозапуск: $autostart"
    if ($vm.State -eq "Running") { Write-Host ("Аптайм:    {0:hh\:mm}" -f $vm.Uptime) }
    Write-Host ""
    Write-Host "Дашборд:   http://${addr}:8787" -ForegroundColor Green
}

function Wait-VmState {
    param([string]$VmName, [string]$State, [int]$TimeoutSec = 90)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        $vm = Get-VM -Name $VmName -ErrorAction SilentlyContinue
        if ($vm -and $vm.State -eq $State) { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Stop-GuestGracefully {
    # Сначала просим гостя выключиться по SSH — так не теряются данные.
    if (Test-Path $SshKey) {
        Write-Host "Прошу гостя выключиться по SSH…" -ForegroundColor Cyan
        $sshArgs = @(
            "-i", $SshKey, "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=NUL",
            "-o", "ConnectTimeout=8", "-o", "LogLevel=ERROR",
            "$SshUser@$VmIp", "sudo systemctl poweroff"
        )
        try { & ssh @sshArgs 2>$null } catch { }
    }

    if (Wait-VmState -VmName $Name -State "Off" -TimeoutSec 60) { return $true }

    Write-Host "Гость не выключился сам — останавливаю через Hyper-V (Save)." -ForegroundColor Yellow
    Stop-VM -Name $Name -Save -Force -ErrorAction SilentlyContinue
    return (Wait-VmState -VmName $Name -State "Off" -TimeoutSec 60)
}

# ── действия, требующие прав ──
$needsAdmin = $Action -in @("start", "stop", "off", "restart", "autostart")
if ($needsAdmin -and -not (Test-Elevated)) { Invoke-ElevatedSelf }

switch ($Action) {
    "status" { Show-Status }

    "start" {
        $vm = Get-VM -Name $Name -ErrorAction Stop
        if ($vm.State -eq "Running") {
            Write-Host "ВМ «$Name» уже запущена." -ForegroundColor Yellow
        } else {
            Write-Host "Запускаю «$Name»…" -ForegroundColor Cyan
            Start-VM -Name $Name
            if (-not (Wait-VmState -VmName $Name -State "Running" -TimeoutSec 60)) {
                throw "ВМ не перешла в состояние Running."
            }
        }
        Write-Host "Жду SSH…" -ForegroundColor Cyan
        $deadline = (Get-Date).AddMinutes(3)
        while ((Get-Date) -lt $deadline) {
            $client = New-Object Net.Sockets.TcpClient
            try { $client.Connect($VmIp, 22); if ($client.Connected) { $client.Close(); break } }
            catch { } finally { $client.Dispose() }
            Start-Sleep -Seconds 3
        }
        Show-Status
    }

    "stop" {
        Write-Host "Выключаю «$Name»…" -ForegroundColor Cyan
        if (Stop-GuestGracefully) {
            Write-Host "ВМ выключена." -ForegroundColor Green
        } else {
            Write-Host "Не удалось выключить корректно." -ForegroundColor Red
        }
        Show-Status
    }

    "off" {
        Write-Host "Жёсткое выключение «$Name» (как выдернуть питание)…" -ForegroundColor Yellow
        Stop-VM -Name $Name -TurnOff -Force
        Wait-VmState -VmName $Name -State "Off" -TimeoutSec 60 | Out-Null
        Show-Status
    }

    "restart" {
        Write-Host "Перезапускаю «$Name»…" -ForegroundColor Cyan
        Stop-GuestGracefully | Out-Null
        Start-VM -Name $Name
        Wait-VmState -VmName $Name -State "Running" -TimeoutSec 60 | Out-Null
        Start-Sleep -Seconds 5
        Show-Status
    }

    "autostart" {
        if ($Mode -eq "on") {
            Set-VM -Name $Name -AutomaticStartAction Start -AutomaticStartDelay 30
            Write-Host "Автозапуск включён: ВМ стартует через 30 с после загрузки ПК." -ForegroundColor Green
        } else {
            Set-VM -Name $Name -AutomaticStartAction Nothing
            Write-Host "Автозапуск выключен: ВМ стартует только вручную." -ForegroundColor Green
        }
        Show-Status
    }
}
