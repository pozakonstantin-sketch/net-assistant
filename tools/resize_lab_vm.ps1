#Requires -Version 5.1
<#
.SYNOPSIS
    Меняет память и число процессоров лабораторной ВМ.

.DESCRIPTION
    ВМ выключается (штатно, с отключением питания через минуту), получает новые
    параметры и включается снова. Нужны права администратора — запускайте через
    tools\run_elevated.ps1.

.EXAMPLE
    .\tools\run_elevated.ps1 -Script .\tools\resize_lab_vm.ps1 -ScriptArgs "-MemoryMB","8192","-Cpu","4"
#>
param(
    [string]$Name = "assistant-hub",
    [int]$MemoryMB = 8192,
    [int]$Cpu = 4,
    [int]$ShutdownTimeoutSec = 90
)

$ErrorActionPreference = "Stop"

$vm = Get-VM -Name $Name -ErrorAction Stop
Write-Host "ВМ: $Name, состояние: $($vm.State)"
Write-Host "Было: память $([math]::Round($vm.MemoryStartup/1MB)) МБ, процессоров $($vm.ProcessorCount)"

if ($vm.State -ne "Off") {
    Write-Host "Останавливаю ВМ..."
    Stop-VM -Name $Name -ErrorAction SilentlyContinue
    $deadline = (Get-Date).AddSeconds($ShutdownTimeoutSec)
    while ((Get-VM -Name $Name).State -ne "Off" -and (Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 3
    }
    if ((Get-VM -Name $Name).State -ne "Off") {
        Write-Host "Штатное выключение не успело — выключаю питание."
        Stop-VM -Name $Name -TurnOff -Force
        Start-Sleep -Seconds 3
    }
}

Set-VM -Name $Name -StaticMemory -MemoryStartupBytes ($MemoryMB * 1MB) -ProcessorCount $Cpu
Start-VM -Name $Name

$vm = Get-VM -Name $Name
Write-Host "Стало: память $([math]::Round($vm.MemoryStartup/1MB)) МБ, процессоров $($vm.ProcessorCount), состояние $($vm.State)"
