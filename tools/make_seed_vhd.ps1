#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Создаёт VHD-диск с меткой CIDATA для cloud-init (NoCloud datasource).

.DESCRIPTION
    Ubuntu Cloud-образ при загрузке ищет диск с меткой CIDATA и берёт оттуда
    user-data / meta-data. Так гость настраивается полностью автоматически:
    пользователь, SSH-ключ, пароль, имя хоста — без ручной установки.

.EXAMPLE
    .\tools\make_seed_vhd.ps1 -Path C:\VMs\assistant-hub\seed.vhd `
        -SshPublicKey (Get-Content C:\VMs\assistant-hub\id_ed25519.pub -Raw)
#>
param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$SshPublicKey,
    [string]$UserName = "assistant",
    [string]$Password = "assistant",
    [string]$Hostname = "assistant-hub",
    [int]$SizeMB = 64
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8

if (-not (Get-Command New-VHD -ErrorAction SilentlyContinue)) {
    throw "Модуль Hyper-V недоступен — сначала включите Hyper-V и перезагрузитесь."
}

$Path = [IO.Path]::GetFullPath($Path)
New-Item -ItemType Directory -Path (Split-Path -Parent $Path) -Force | Out-Null
if (Test-Path $Path) { Remove-Item $Path -Force }

$key = $SshPublicKey.Trim()
if (-not $key) { throw "Пустой SSH-ключ." }

$userData = @"
#cloud-config
hostname: $Hostname
manage_etc_hosts: true
users:
  - name: $UserName
    gecos: Net Assistant
    groups: [adm, sudo]
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    lock_passwd: false
    ssh_authorized_keys:
      - $key
ssh_pwauth: true
chpasswd:
  list: |
    ${UserName}:${Password}
  expire: false
runcmd:
  - [ systemctl, enable, --now, ssh ]
  - [ bash, -c, "echo ready > /var/log/net-assistant-ready" ]
"@

$metaData = @"
instance-id: $Hostname-seed
local-hostname: $Hostname
"@

$utf8 = New-Object System.Text.UTF8Encoding($false)

$temp = Join-Path $env:TEMP ("na-seed-{0}" -f (Get-Random))
New-Item -ItemType Directory -Path $temp -Force | Out-Null
[IO.File]::WriteAllText((Join-Path $temp "user-data"), $userData, $utf8)
[IO.File]::WriteAllText((Join-Path $temp "meta-data"), $metaData, $utf8)

$success = $false
try {
    Write-Host "Создаю seed-диск $Path ($SizeMB МБ)…" -ForegroundColor Cyan
    New-VHD -Path $Path -SizeBytes ($SizeMB * 1MB) -Fixed | Out-Null
    Mount-VHD -Path $Path

    $vhd = Get-VHD -Path $Path
    $diskNumber = $vhd.DiskNumber
    if ($null -eq $diskNumber) {
        $diskNumber = (Get-Disk | Where-Object { $_.Location -like "*$Path*" } | Select-Object -First 1).Number
    }
    if ($null -eq $diskNumber) { throw "Не удалось определить номер диска seed." }

    Initialize-Disk -Number $diskNumber -PartitionStyle MBR -ErrorAction Stop | Out-Null
    $partition = New-Partition -DiskNumber $diskNumber -UseMaximumSize -AssignDriveLetter
    $letter = $partition.DriveLetter
    Format-Volume -DriveLetter $letter -FileSystem FAT32 -NewFileSystemLabel "CIDATA" `
        -Confirm:$false -Force | Out-Null

    Copy-Item -Path @(
        (Join-Path $temp "user-data"),
        (Join-Path $temp "meta-data")
    ) -Destination "${letter}:\"
    Write-Host "Файлы cloud-init записаны на ${letter}:\ (метка CIDATA)." -ForegroundColor Green
    Get-ChildItem "${letter}:\" | Select-Object Name, Length | Format-Table -AutoSize
    $success = $true
}
finally {
    Dismount-VHD -Path $Path -ErrorAction SilentlyContinue
    Remove-Item $temp -Recurse -Force -ErrorAction SilentlyContinue
    if (-not $success) {
        Remove-Item $Path -Force -ErrorAction SilentlyContinue
        Write-Host "Seed-диск удалён из-за ошибки — при следующем запуске создастся заново." -ForegroundColor Yellow
    }
}

if (-not $success) { throw "Не удалось подготовить seed-диск." }

Set-Content -Path "$Path.ok" -Value (Get-Date -Format "o") -Encoding ASCII
Write-Host "Seed-диск готов: $Path" -ForegroundColor Green
