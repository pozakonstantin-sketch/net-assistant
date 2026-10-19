#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Снимает скриншот консоли ВМ через WMI Hyper-V (без открытия vmconnect).

.DESCRIPTION
    GetVirtualSystemThumbnailImage возвращает кадр консоли в RGB565.
    Скрипт конвертирует его в PNG — удобно смотреть, что происходит
    с загрузкой гостя, не трогая мышь и клавиатуру.

.EXAMPLE
    .\tools\vm_console_shot.ps1 -Name assistant-hub
#>
param(
    [string]$Name = "assistant-hub",
    [string]$Out = "",
    [int]$Width = 1024,
    [int]$Height = 768
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8

if (-not $Out) { $Out = "C:\VMs\$Name\console.png" }
$ns = "root\virtualization\v2"

$vm = Get-CimInstance -Namespace $ns -ClassName Msvm_ComputerSystem -Filter "ElementName='$Name'"
if (-not $vm) { throw "ВМ «$Name» не найдена" }
Write-Host "Состояние ВМ: $($vm.EnabledState) ($($vm.ElementName))"

$sd = Get-CimAssociatedInstance -InputObject $vm -ResultClassName Msvm_VirtualSystemSettingData |
    Select-Object -First 1
$vmms = Get-CimInstance -Namespace $ns -ClassName Msvm_VirtualSystemManagementService

$result = Invoke-CimMethod -InputObject $vmms -MethodName GetVirtualSystemThumbnailImage -Arguments @{
    TargetSystem = $sd
    WidthPixels  = [uint16]$Width
    HeightPixels = [uint16]$Height
}
if ($result.ReturnValue -ne 0) { throw "GetVirtualSystemThumbnailImage вернул $($result.ReturnValue)" }

$data = $result.ImageData
if (-not $data) { throw "Кадр консоли пуст (ВМ выключена?)" }
Write-Host "Кадр: $($data.Length) байт ($Width x $Height)"

Add-Type -AssemblyName System.Drawing
if (-not ("Rgb565ToPng" -as [type])) {
    Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @"
using System;
using System.Drawing;
using System.Drawing.Imaging;

public static class Rgb565ToPng
{
    public static void Convert(byte[] data, int w, int h, string path)
    {
        using (var bmp = new Bitmap(w, h, PixelFormat.Format24bppRgb))
        {
            for (int y = 0; y < h; y++)
            {
                for (int x = 0; x < w; x++)
                {
                    int i = (y * w + x) * 2;
                    int v = data[i] | (data[i + 1] << 8);
                    int r = ((v >> 11) & 0x1F) * 255 / 31;
                    int g = ((v >> 5) & 0x3F) * 255 / 63;
                    int b = (v & 0x1F) * 255 / 31;
                    bmp.SetPixel(x, y, Color.FromArgb(r, g, b));
                }
            }
            bmp.Save(path, ImageFormat.Png);
        }
    }
}
"@
}

$dir = Split-Path -Parent $Out
if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
[Rgb565ToPng]::Convert($data, $Width, $Height, $Out)
Write-Host "Скриншот: $Out" -ForegroundColor Green
