#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Создаёт ВМ лаборатории: из cloud-образа Ubuntu (быстро, автоматически)
    или из установочного ISO (ручная установка).

.DESCRIPTION
    Режим A (рекомендуемый): -ImageVhd <VHD из ubuntu-cloudimg> -SeedVhd <seed.vhd>
      ВМ Generation 1, гость уже установлен, cloud-init настраивает пользователя
      и SSH по seed-диску. Загрузка до готовности — около минуты.

    Режим B: -IsoPath <ubuntu-server.iso>
      ВМ Generation 2 с DVD, установка вручную через vmconnect.

.EXAMPLE
    .\tools\create_lab_vm.ps1 `
        -ImageVhd C:\VMs\images\ubuntu-24.04-azure.vhd `
        -SeedVhd C:\VMs\assistant-hub\seed.vhd
#>
param(
    [string]$Name = "assistant-hub",
    [string]$ImageVhd = "",
    [string]$SeedVhd = "",
    [string]$IsoPath = "",
    [string]$SwitchName = "Default Switch",
    [int]$MemoryGB = 4,
    [int]$CpuCount = 2,
    [int]$DiskGB = 24,
    [string]$VmRoot = "C:\VMs",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8

if (-not (Get-Command New-VM -ErrorAction SilentlyContinue)) {
    throw "Модуль Hyper-V недоступен. Запустите .\tools\enable_hyperv.ps1 и перезагрузитесь."
}

$existing = Get-VM -Name $Name -ErrorAction SilentlyContinue
if ($existing -and -not $Force) {
    throw "ВМ «$Name» уже существует. Используйте -Force, чтобы пересоздать."
}
if ($existing) {
    Write-Host "Удаляю существующую ВМ «$Name»…" -ForegroundColor Yellow
    Stop-VM -Name $Name -TurnOff -Force -ErrorAction SilentlyContinue
    Remove-VM -Name $Name -Force
}

$vmDir = Join-Path $VmRoot $Name
New-Item -ItemType Directory -Path $vmDir -Force | Out-Null
$diskPath = Join-Path $vmDir "disk.vhdx"

if ($ImageVhd) {
    # ───── режим A: готовый cloud-образ ─────
    if (-not (Test-Path $ImageVhd)) { throw "Образ не найден: $ImageVhd" }
    if (-not $SeedVhd -or -not (Test-Path $SeedVhd)) {
        throw "Нужен seed-диск: создайте его через .\tools\make_seed_vhd.ps1"
    }

    # Hyper-V не читает разреженные и сжатые VHD (0xC03A001A)
    $item = Get-Item $ImageVhd
    if (($item.Attributes -band [IO.FileAttributes]::SparseFile) -or
        ($item.Attributes -band [IO.FileAttributes]::Compressed)) {
        Write-Host "Образ разреженный/сжатый — исправляю атрибуты…" -ForegroundColor Yellow
        & fsutil.exe sparse setflag $ImageVhd 0 | Out-Null
        & compact.exe /u /f $ImageVhd | Out-Null
        $item.Refresh()
        if ($item.Attributes -band [IO.FileAttributes]::SparseFile) {
            Write-Host "Флаг sparse не снялся — копирую образ (это займёт пару минут)…" -ForegroundColor Yellow
            $tempCopy = "$ImageVhd.tmp"
            Copy-Item $ImageVhd $tempCopy -Force
            Remove-Item $ImageVhd -Force
            Move-Item $tempCopy $ImageVhd
            (Get-Item $ImageVhd).Refresh()
            if ((Get-Item $ImageVhd).Attributes -band [IO.FileAttributes]::SparseFile) {
                throw "Не удалось подготовить образ: файл остался разреженным."
            }
        }
    }

    Write-Host "Готовлю диск ВМ из образа…" -ForegroundColor Cyan
    if (Test-Path $diskPath) { Remove-Item $diskPath -Force }

    if ([IO.Path]::GetExtension($ImageVhd) -eq ".vhdx") {
        Copy-Item $ImageVhd $diskPath -Force
    } else {
        Convert-VHD -Path $ImageVhd -DestinationPath $diskPath -VHDType Dynamic
    }

    $currentSize = (Get-VHD -Path $diskPath).Size
    if (($DiskGB * 1GB) -gt $currentSize) {
        Write-Host "Расширяю диск до $DiskGB ГБ…" -ForegroundColor Cyan
        Resize-VHD -Path $diskPath -SizeBytes ($DiskGB * 1GB)
    }

    New-VM -Name $Name -Generation 1 -MemoryStartupBytes ($MemoryGB * 1GB) `
        -VHDPath $diskPath -SwitchName $SwitchName | Out-Null
    Set-VMProcessor -VMName $Name -Count $CpuCount
    Set-VM -Name $Name -AutomaticCheckpointsEnabled $false -CheckpointType Standard

    # второй диск — seed с cloud-init
    Add-VMHardDiskDrive -VMName $Name -Path $SeedVhd `
        -ControllerType IDE -ControllerNumber 0 -ControllerLocation 1

    Write-Host "ВМ создана из cloud-образа." -ForegroundColor Green
    Write-Host "Диск:      $diskPath"
    Write-Host "Seed:      $SeedVhd"
    Write-Host "Сеть:      $SwitchName (DHCP)"
}
elseif ($IsoPath) {
    # ───── режим B: установщик ─────
    if (-not (Test-Path $IsoPath)) { throw "ISO не найден: $IsoPath" }

    New-VM -Name $Name -Generation 2 -MemoryStartupBytes ($MemoryGB * 1GB) `
        -NewVHDPath $diskPath -NewVHDSizeBytes 40GB -SwitchName $SwitchName | Out-Null
    Set-VMProcessor -VMName $Name -Count $CpuCount
    Set-VM -Name $Name -AutomaticCheckpointsEnabled $false -CheckpointType Standard
    Add-VMDvdDrive -VMName $Name -Path $IsoPath | Out-Null
    Set-VMFirmware -VMName $Name -FirstBootDevice (Get-VMDvdDrive -VMName $Name) `
        -EnableSecureBoot Off

    Write-Host "ВМ создана для установки с ISO." -ForegroundColor Green
    Write-Host "Диск: $diskPath"
}
else {
    throw "Укажите -ImageVhd (готовый образ) или -IsoPath (установщик)."
}

Start-VM -Name $Name
Write-Host ""
Write-Host "ВМ «$Name» запущена." -ForegroundColor Green
Write-Host "Подключиться к консоли: vmconnect localhost $Name" -ForegroundColor DarkGray
