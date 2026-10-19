#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Включает Hyper-V и проверяет, что виртуализация готова к работе.

.DESCRIPTION
    Скрипт идемпотентен: повторный запуск ничего не ломает.
    После установки компонента нужна перезагрузка — скрипт скажет об этом
    и завершится; запустите его ещё раз после перезагрузки.

.EXAMPLE
    .\tools\enable_hyperv.ps1
#>
param()

$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "== Проверка виртуализации ==" -ForegroundColor Cyan

$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
Write-Host "Процессор: $($cpu.Name)"
Write-Host "Виртуализация в прошивке: $($cpu.VirtualizationFirmwareEnabled)"

$feature = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All
Write-Host "Компонент Hyper-V: $($feature.State)"

$rebootNeeded = $false
if ($feature.State -ne "Enabled") {
    Write-Host "Включаю Microsoft-Hyper-V-All (без немедленной перезагрузки)…" -ForegroundColor Yellow
    $result = Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All -All -NoRestart
    $rebootNeeded = [bool]$result.RestartNeeded
}

$vmms = Get-Service vmms -ErrorAction SilentlyContinue
if (-not $vmms -or $vmms.Status -ne "Running") {
    $rebootNeeded = $true
}

if ($rebootNeeded -or -not (Get-Command New-VM -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "Нужна перезагрузка. После неё запустите этот скрипт снова:" -ForegroundColor Yellow
    Write-Host "    Restart-Computer" -ForegroundColor DarkGray
    Write-Host "    .\tools\enable_hyperv.ps1" -ForegroundColor DarkGray
    exit 0
}

Write-Host ""
Write-Host "== Готовность ==" -ForegroundColor Cyan
Write-Host "Служба vmms:      $((Get-Service vmms).Status)"
Write-Host "Модуль Hyper-V:   есть"

$default = Get-VMSwitch -Name "Default Switch" -ErrorAction SilentlyContinue
if ($default) {
    Write-Host "Default Switch:   есть (NAT + DHCP, изолирует гостя от входящих)"
} else {
    Write-Host "Default Switch:   НЕТ — создайте ВМ с -SwitchName lab-internal и статическим IP" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Готово. Дальше — создание ВМ лаборатории:" -ForegroundColor Green
Write-Host "  .\tools\make_seed_vhd.ps1 -Path C:\VMs\assistant-hub\seed.vhd ``" -ForegroundColor DarkGray
Write-Host "      -SshPublicKey (Get-Content C:\VMs\assistant-hub\id_ed25519.pub -Raw)" -ForegroundColor DarkGray
Write-Host "  .\tools\create_lab_vm.ps1 -ImageVhd C:\VMs\images\livecd.ubuntu-cpc.azure.vhd ``" -ForegroundColor DarkGray
Write-Host "      -SeedVhd C:\VMs\assistant-hub\seed.vhd" -ForegroundColor DarkGray
