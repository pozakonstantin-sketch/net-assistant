# Снимок экрана (консоли) виртуальной машины Hyper-V.
#
# Работает без гостевых служб: хост отдаёт миниатюру видеоадаптера, поэтому
# видно даже загрузчик UEFI/BIOS и окно установщика ОС. Данные приходят сырым
# кадром RGB565 с четырьмя служебными байтами впереди — превращаем их в PNG.
#
# Пример:
#   .\tools\vm_console.ps1 -Name assistant -Out D:\VMs\shots\screen.png

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Name,
    [string]$Out = "",
    [int]$Width = 0,
    [int]$Height = 0
)

$ErrorActionPreference = 'Stop'
$ns = 'root\virtualization\v2'

$vm = Get-CimInstance -Namespace $ns -ClassName Msvm_ComputerSystem -Filter "ElementName='$Name'"
if (-not $vm) { throw "Виртуальная машина '$Name' не найдена" }

# Размер кадра нельзя запрашивать больше текущего разрешения гостя — Hyper-V
# отвечает «недопустимое состояние». Берём текущее разрешение как предел.
$head = Get-CimAssociatedInstance -ResultClassName Msvm_VideoHead -InputObject $vm |
    Where-Object { $_.CurrentHorizontalResolution -gt 0 } | Select-Object -First 1
$currentWidth = if ($head) { [int]$head.CurrentHorizontalResolution } else { 1024 }
$currentHeight = if ($head) { [int]$head.CurrentVerticalResolution } else { 768 }

if ($Width -le 0 -or $Width -gt $currentWidth) { $Width = $currentWidth }
if ($Height -le 0 -or $Height -gt $currentHeight) { $Height = $currentHeight }

$vmms = Get-CimInstance -Namespace $ns -ClassName Msvm_VirtualSystemManagementService
$result = Invoke-CimMethod -InputObject $vmms -MethodName GetVirtualSystemThumbnailImage -Arguments @{
    TargetSystem = $vm
    WidthPixels  = [uint16]$Width
    HeightPixels = [uint16]$Height
}
if ($result.ReturnValue -ne 0) {
    throw "Не удалось снять экран '$Name' (код $($result.ReturnValue))"
}

$raw = [byte[]]$result.ImageData
$needed = $Width * $Height * 2
$skip = $raw.Count - $needed
if ($skip -lt 0) {
    throw "Кадр короче ожидаемого: получено $($raw.Count) байт, нужно $needed"
}

if (-not $Out) {
    $Out = Join-Path (Split-Path -Parent $PSScriptRoot) "data\console-$Name.png"
}
$dir = Split-Path -Parent $Out
if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }

Add-Type -AssemblyName System.Drawing
$bitmap = New-Object System.Drawing.Bitmap($Width, $Height, [System.Drawing.Imaging.PixelFormat]::Format16bppRgb565)
$rect = New-Object System.Drawing.Rectangle(0, 0, $Width, $Height)
$bits = $bitmap.LockBits($rect, [System.Drawing.Imaging.ImageLockMode]::WriteOnly,
                         [System.Drawing.Imaging.PixelFormat]::Format16bppRgb565)
try {
    [System.Runtime.InteropServices.Marshal]::Copy($raw, $skip, $bits.Scan0, $needed)
} finally {
    $bitmap.UnlockBits($bits)
}
$bitmap.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$bitmap.Dispose()

Write-Output $Out
