#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Контрольные точки лаборатории: создать / восстановить / показать.

.DESCRIPTION
    Главный инструмент «не сломать»: перед экспериментом делаем снимок,
    после неудачного — откатываемся за минуту.

.EXAMPLE
    .\tools\vm_checkpoint.ps1 -Name assistant-hub -Create -Snapshot before-router
    .\tools\vm_checkpoint.ps1 -Name assistant-hub -List
    .\tools\vm_checkpoint.ps1 -Name assistant-hub -Restore -Snapshot before-router
#>
param(
    [string]$Name = "assistant-hub",
    [switch]$Create,
    [switch]$Restore,
    [switch]$List,
    [string]$Snapshot = ""
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$vm = Get-VM -Name $Name -ErrorAction SilentlyContinue
if (-not $vm) { throw "ВМ «$Name» не найдена." }

if ($List -or (-not $Create -and -not $Restore)) {
    $snapshots = Get-VMSnapshot -VMName $Name
    if (-not $snapshots) {
        Write-Host "Снимков нет." -ForegroundColor Yellow
    } else {
        Write-Host "Снимки «$Name»:" -ForegroundColor Cyan
        $snapshots | Select-Object Name, CreationTime, ParentSnapshotName |
            Format-Table -AutoSize
    }
    exit 0
}

if ($Create) {
    if (-not $Snapshot) { throw "Укажите -Snapshot <имя>" }
    $wasRunning = $vm.State -ne "Off"
    if ($wasRunning) {
        Write-Host "Останавливаю ВМ для чистого снимка…" -ForegroundColor Yellow
        Stop-VM -Name $Name -Force
    }
    Checkpoint-VM -Name $Name -SnapshotName $Snapshot
    Write-Host "Снимок «$Snapshot» создан." -ForegroundColor Green
    if ($wasRunning) { Start-VM -Name $Name }
    exit 0
}

if ($Restore) {
    if (-not $Snapshot) {
        # по умолчанию — последний снимок
        $latest = Get-VMSnapshot -VMName $Name | Sort-Object CreationTime -Descending |
            Select-Object -First 1
        if (-not $latest) { throw "Снимков нет." }
        $Snapshot = $latest.Name
    }
    $target = Get-VMSnapshot -VMName $Name -Name $Snapshot -ErrorAction SilentlyContinue
    if (-not $target) { throw "Снимок «$Snapshot» не найден." }

    if ($vm.State -ne "Off") { Stop-VM -Name $Name -Force }
    Restore-VMSnapshot -VMName $Name -Name $Snapshot -Confirm:$false
    Start-VM -Name $Name
    Write-Host "ВМ восстановлена из снимка «$Snapshot» и запущена." -ForegroundColor Green
}
