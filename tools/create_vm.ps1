# Создание виртуальной машины для «умного дома» (Hyper-V, поколение 2/UEFI).
#
# Машина задумана как единственный «мозг» системы: ядро, панель, бот, агенты и
# рабочий стол. Параметры подобраны так, чтобы установщик Astra Linux работал
# без Secure Boot, а диск лежал на большом томе.
#
# Пример:
#   .\tools\create_vm.ps1 -Name assistant -Iso D:\VMs\images\astra-se-1.8.1.6-install.iso

[CmdletBinding()]
param(
    [string]$Name = "assistant",
    [Parameter(Mandatory = $true)][string]$Iso,
    [string]$Root = "D:\VMs",
    [string]$Switch = "lab-external",
    [int]$MemoryGB = 8,
    [int]$CpuCount = 4,
    [int]$DiskGB = 100
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $Iso)) { throw "Образ не найден: $Iso" }
if (Get-VM -Name $Name -ErrorAction SilentlyContinue) {
    throw "Виртуальная машина '$Name' уже существует"
}

$dir = Join-Path $Root $Name
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$disk = Join-Path $dir "disk.vhdx"

Write-Host "Создаю диск $DiskGB ГБ: $disk"
New-VHD -Path $disk -SizeBytes ($DiskGB * 1GB) -Dynamic | Out-Null

Write-Host "Создаю машину '$Name' (поколение 2, UEFI)"
New-VM -Name $Name -Generation 2 -MemoryStartupBytes ($MemoryGB * 1GB) `
    -VHDPath $disk -SwitchName $Switch -Path $dir | Out-Null

Set-VM -Name $Name -ProcessorCount $CpuCount -StaticMemory `
    -AutomaticStopAction ShutDown -AutomaticStartAction StartIfRunning | Out-Null

# Secure Boot для установщика выключаем: подписанный загрузчик Astra проверяется
# позже, а на этапе установки лишние ограничения только мешают.
Set-VMFirmware -VMName $Name -EnableSecureBoot Off | Out-Null

Add-VMDvdDrive -VMName $Name -Path $Iso | Out-Null
$dvd = Get-VMDvdDrive -VMName $Name
Set-VMFirmware -VMName $Name -FirstBootDevice $dvd | Out-Null

# Консоль Hyper-V по умолчанию 1024x768 — для графического установщика мало.
Set-VMVideo -VMName $Name -HorizontalResolution 1600 -VerticalResolution 1200 -ErrorAction SilentlyContinue | Out-Null

# Отключаем точки восстановления — для этой машины они не нужны.
Set-VM -Name $Name -CheckpointType Disabled | Out-Null

Write-Host "Готово. Машина '$Name':"
Get-VM -Name $Name | Format-Table Name, State, Generation, ProcessorCount,
    @{n='RAM_ГБ';e={[int]($_.MemoryStartup / 1GB)}} -AutoSize
Get-VMHardDiskDrive -VMName $Name | Format-Table ControllerType, Path -AutoSize
Get-VMDvdDrive -VMName $Name | Format-Table Path -AutoSize
