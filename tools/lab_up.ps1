#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Поднимает всю лабораторию: внешний коммутатор, seed-диск, ВМ, ожидание сети,
    установка проекта.

.DESCRIPTION
    ВМ подключается к физическому адаптеру (bridged), поэтому адрес ей выдаёт
    домашний роутер по DHCP — как обычному устройству в сети.

    Идемпотентно: отсутствующие части создаёт, существующую ВМ не трогает
    (если не указан -Recreate). Требует прав администратора и включённого Hyper-V.

.EXAMPLE
    .\tools\lab_up.ps1
    .\tools\lab_up.ps1 -Recreate -NoProvision
#>
param(
    [string]$Name = "assistant-hub",
    [string]$ImageVhd = "C:\VMs\images\ubuntu-24.04-generic.vhdx",
    [string]$VmRoot = "C:\VMs",
    [string]$SwitchName = "lab-external",
    [string]$AdapterName = "",
    [int]$MemoryGB = 4,
    [int]$CpuCount = 2,
    [int]$DiskGB = 24,
    [int]$TimeoutMinutes = 8,
    [switch]$Recreate,
    [switch]$NoProvision,
    [switch]$IsoMode,
    [string]$IsoPath = ""
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8

if (-not (Get-Command New-VM -ErrorAction SilentlyContinue)) {
    throw "Hyper-V не готов. Запустите .\tools\enable_hyperv.ps1 и перезагрузитесь."
}

# ── зависшие предыдущие запуски (например, ожидание IP) ──
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*net-assistant-elevated-*" -and $_.ProcessId -ne $PID } |
    ForEach-Object {
        Write-Host "Останавливаю предыдущий elevated-запуск (PID $($_.ProcessId))…" -ForegroundColor Yellow
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

# ── внешний коммутатор: адрес выдаёт роутер ──
Write-Host "== Сеть ==" -ForegroundColor Cyan
$switch = Get-VMSwitch -Name $SwitchName -ErrorAction SilentlyContinue
if (-not $switch) {
    $adapter = $null
    if ($AdapterName) {
        $adapter = Get-NetAdapter -Name $AdapterName -ErrorAction Stop
    } else {
        $adapter = Get-NetAdapter | Where-Object {
            $_.Status -eq 'Up' -and
            $_.Name -notlike 'vEthernet*' -and
            $_.InterfaceDescription -notlike '*Hyper-V*' -and
            $_.InterfaceDescription -notlike '*Tailscale*'
        } | Sort-Object -Property @{ Expression = { if ($_.InterfaceDescription -like '*Ethernet*') { 0 } else { 1 } } } |
            Select-Object -First 1
    }
    if (-not $adapter) { throw "Не нашёл физический сетевой адаптер. Укажите -AdapterName." }

    Write-Host "Создаю внешний коммутатор «$SwitchName» на адаптере «$($adapter.Name)»…" -ForegroundColor Cyan
    Write-Host "(сеть на несколько секунд прервётся — это нормально)" -ForegroundColor DarkGray
    New-VMSwitch -Name $SwitchName -NetAdapterName $adapter.Name -AllowManagementOS $true | Out-Null
} else {
    Write-Host "Коммутатор «$SwitchName» уже есть ($($switch.SwitchType))."
}

$hostAdapter = Get-NetAdapter -Name "vEthernet ($SwitchName)" -ErrorAction SilentlyContinue
if ($hostAdapter) {
    $hostIp = Get-NetIPAddress -InterfaceIndex $hostAdapter.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike '169.254.*' } | Select-Object -First 1
    if ($hostIp) { Write-Host "Хост в этой сети: $($hostIp.IPAddress)" }
}

$vmDir = Join-Path $VmRoot $Name
$seedPath = Join-Path $vmDir "seed.vhd"
$keyPath = Join-Path $vmDir "id_ed25519"
$pubPath = "$keyPath.pub"

New-Item -ItemType Directory -Path $vmDir -Force | Out-Null

# ── пересоздание: сначала убираем ВМ, иначе диски заблокированы ──
$vm = Get-VM -Name $Name -ErrorAction SilentlyContinue
if ($Recreate) {
    foreach ($staleName in @($Name, "-Recreate")) {
        $stale = Get-VM -Name $staleName -ErrorAction SilentlyContinue
        if ($stale) {
            Write-Host "Удаляю ВМ «$staleName»…" -ForegroundColor Yellow
            Stop-VM -Name $staleName -TurnOff -Force -ErrorAction SilentlyContinue
            Remove-VM -Name $staleName -Force
        }
    }
    Remove-Item (Join-Path $VmRoot "-Recreate") -Recurse -Force -ErrorAction SilentlyContinue
    $vm = $null
}

# ── SSH-ключ ──
if (-not (Test-Path $keyPath)) {
    Write-Host "Генерирую SSH-ключ лаборатории…" -ForegroundColor Cyan
    cmd /c "ssh-keygen -t ed25519 -f `"$keyPath`" -N `"`" -q -C net-assistant-lab"
}

# ── seed-диск с cloud-init ──
if (-not (Test-Path $seedPath) -or -not (Test-Path "$seedPath.ok") -or $Recreate) {
    $publicKey = Get-Content $pubPath -Raw
    & (Join-Path $PSScriptRoot "make_seed_vhd.ps1") -Path $seedPath -SshPublicKey $publicKey
}

# ── ВМ ──
if (-not $vm -or $Recreate) {
    if ($IsoMode) {
        & (Join-Path $PSScriptRoot "create_lab_vm.ps1") -Name $Name -IsoPath $IsoPath `
            -SwitchName $SwitchName -MemoryGB $MemoryGB -CpuCount $CpuCount -VmRoot $VmRoot -Force
    } else {
        & (Join-Path $PSScriptRoot "create_lab_vm.ps1") -Name $Name -ImageVhd $ImageVhd `
            -SeedVhd $seedPath -SwitchName $SwitchName -MemoryGB $MemoryGB `
            -CpuCount $CpuCount -DiskGB $DiskGB -VmRoot $VmRoot -Force
    }
} elseif ($vm.State -ne "Running") {
    Write-Host "Запускаю существующую ВМ «$Name»…" -ForegroundColor Cyan
    Start-VM -Name $Name
}

# ── поиск адреса, выданного роутером ──
function Get-GuestAddress {
    param([string]$VmName, [string]$Mac, [int]$TimeoutSec)

    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $macNorm = ($Mac -replace '[^0-9A-Fa-f]', '').ToUpper()
    $attempt = 0

    while ((Get-Date) -lt $deadline) {
        $attempt++

        # 1. Hyper-V Integration Services (если гость отдаёт IP через KVP)
        $fromHyperV = (Get-VMNetworkAdapter -VMName $VmName -ErrorAction SilentlyContinue).IPAddresses |
            Where-Object { $_ -match '^\d{1,3}(\.\d{1,3}){3}$' } |
            Where-Object { $_ -notlike '169.254.*' } |
            Select-Object -First 1
        if ($fromHyperV) { return $fromHyperV }

        # 2. ARP: пингуем подсеть и ищем MAC гостя
        $nic = Get-NetAdapter -Name "vEthernet ($SwitchName)" -ErrorAction SilentlyContinue
        if ($nic) {
            $addr = Get-NetIPAddress -InterfaceIndex $nic.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
                Where-Object { $_.IPAddress -notlike '169.254.*' } | Select-Object -First 1
            if ($addr) {
                $prefix = ($addr.IPAddress -split '\.')[0..2] -join '.'
                if ($attempt -eq 1 -or ($attempt % 2) -eq 0) {
                    $pings = 1..254 | ForEach-Object {
                        $ping = New-Object Net.NetworkInformation.Ping
                        [pscustomobject]@{ Task = $ping.SendPingAsync("$prefix.$_", 350) }
                    }
                    foreach ($item in $pings) {
                        try { [void]$item.Task.Wait(600) } catch { }
                    }
                    $neighbour = Get-NetNeighbor -InterfaceIndex $nic.ifIndex -ErrorAction SilentlyContinue |
                        Where-Object { ($_.LinkLayerAddress -replace '[^0-9A-Fa-f]', '').ToUpper() -eq $macNorm } |
                        Select-Object -First 1
                    if ($neighbour) { return $neighbour.IPAddress }
                }
            }
        }

        Start-Sleep -Seconds 5
    }
    return $null
}

Write-Host ""
Write-Host "Жду адрес от роутера (до $TimeoutMinutes мин)…" -ForegroundColor Cyan
$mac = (Get-VMNetworkAdapter -VMName $Name).MacAddress
$ip = Get-GuestAddress -VmName $Name -Mac $mac -TimeoutSec ($TimeoutMinutes * 60)

if (-not $ip) {
    Write-Host "Адрес не появился. Консоль ВМ:" -ForegroundColor Red
    Write-Host "  vmconnect localhost $Name" -ForegroundColor DarkGray
    Write-Host "  .\tools\vm_console_shot.ps1 -Name $Name   (скриншот в PNG)" -ForegroundColor DarkGray
    exit 1
}

Write-Host "Адрес ВМ: $ip (выдан роутером)" -ForegroundColor Green

# ── ожидание SSH ──
Write-Host "Жду SSH на $ip…" -ForegroundColor Cyan
$sshDeadline = (Get-Date).AddMinutes(5)
$sshReady = $false
while ((Get-Date) -lt $sshDeadline) {
    $client = New-Object Net.Sockets.TcpClient
    try {
        $client.Connect($ip, 22)
        $sshReady = $client.Connected
    } catch {
        $sshReady = $false
    } finally {
        $client.Close()
    }
    if ($sshReady) { break }
    Start-Sleep -Seconds 5
}

if (-not $sshReady) {
    Write-Host "SSH не поднялся за 5 минут." -ForegroundColor Red
    Write-Host "  vmconnect localhost $Name" -ForegroundColor DarkGray
    exit 1
}

Write-Host "SSH доступен." -ForegroundColor Green

if ($NoProvision) {
    Write-Host ""
    Write-Host "ВМ готова. Установка проекта пропущена (-NoProvision)." -ForegroundColor Yellow
    exit 0
}

# ── проект внутри ВМ ──
& (Join-Path $PSScriptRoot "lab_provision.ps1") -Ip $ip

Write-Host ""
Write-Host "== Лаборатория готова ==" -ForegroundColor Green
Write-Host "Ядро и дашборд: http://${ip}:8787"
Write-Host "Проверка: .\tools\smoke_test.ps1 -Base http://${ip}:8787"
