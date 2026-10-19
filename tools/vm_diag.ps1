#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Диагностика ВМ: диски, сеть, содержимое seed-диска.
#>
param([string]$Name = "assistant-hub")

$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "== ВМ ==" -ForegroundColor Cyan
Get-VM -Name $Name | Select-Object Name, State, Generation, Uptime |
    Format-List

Write-Host "== Диски ВМ ==" -ForegroundColor Cyan
Get-VMHardDiskDrive -VMName $Name |
    Select-Object ControllerType, ControllerNumber, ControllerLocation, Path |
    Format-Table -AutoSize

Write-Host "== Сеть ==" -ForegroundColor Cyan
Get-VMNetworkAdapter -VMName $Name |
    Select-Object Name, SwitchName, MacAddress, @{n = 'IP'; e = { $_.IPAddresses -join ',' } } |
    Format-Table -AutoSize

$seed = Join-Path "C:\VMs\$Name" "seed.vhd"
if (Test-Path $seed) {
    Write-Host "== Seed-диск ==" -ForegroundColor Cyan
    # диск прикреплён к работающей ВМ — проверяем копию
    $copy = Join-Path $env:TEMP "seed-check.vhd"
    Copy-Item $seed $copy -Force
    Mount-VHD -Path $copy -ReadOnly
    try {
        $diskNumber = (Get-VHD -Path $copy).DiskNumber
        $disk = Get-Disk -Number $diskNumber
        $part = Get-Partition -DiskNumber $disk.Number | Where-Object { $_.DriveLetter } | Select-Object -First 1
        if ($part) {
            Write-Host "Буква: $($part.DriveLetter):"
            Get-Volume -DriveLetter $part.DriveLetter |
                Select-Object FileSystemLabel, FileSystem, SizeRemaining, Size | Format-List
            Get-ChildItem "$($part.DriveLetter):\" |
                Select-Object Name, Length | Format-Table -AutoSize
            $ud = Join-Path "$($part.DriveLetter):\" "user-data"
            if (Test-Path $ud) {
                Write-Host "--- user-data (первые 20 строк) ---"
                Get-Content $ud -TotalCount 20
            }
        } else {
            Write-Host "Раздел с буквой не найден — диск не отформатирован?" -ForegroundColor Yellow
            Get-Partition -DiskNumber $disk.Number | Format-Table -AutoSize
        }
    }
    finally {
        Dismount-VHD -Path $copy -ErrorAction SilentlyContinue
        Remove-Item $copy -Force -ErrorAction SilentlyContinue
    }
} else {
    Write-Host "Seed-диск отсутствует: $seed" -ForegroundColor Yellow
}
