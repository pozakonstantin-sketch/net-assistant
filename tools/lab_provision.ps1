#Requires -Version 5.1
<#
.SYNOPSIS
    Копирует проект в ВМ и настраивает внутри ядро + fake-агентов.

.DESCRIPTION
    Работает по SSH-ключу (сгенерирован для лаборатории). Проект упаковывается
    в tar без .venv и data, чтобы не тащить Windows-окружение в Linux.

.EXAMPLE
    .\tools\lab_provision.ps1 -Ip 172.20.10.5
#>
param(
    [Parameter(Mandatory = $true)][string]$Ip,
    [string]$UserName = "assistant",
    [string]$KeyPath = "C:\VMs\assistant-hub\id_ed25519",
    [string]$ProjectRoot = ""
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8

if (-not $ProjectRoot) { $ProjectRoot = Split-Path -Parent $PSScriptRoot }
if (-not (Test-Path $KeyPath)) { throw "Нет SSH-ключа: $KeyPath" }

# Windows OpenSSH требует, чтобы ключ читал только владелец
icacls $KeyPath /inheritance:r /grant:r "$($env:USERNAME):F" | Out-Null

$sshOptions = @(
    "-i", "`"$KeyPath`"",
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=NUL",
    "-o", "ConnectTimeout=10",
    "-o", "LogLevel=ERROR"
)

function Invoke-Ssh([string]$Command) {
    $target = "$UserName@$Ip"
    & ssh @sshOptions $target $Command
    if ($LASTEXITCODE -ne 0) { throw "ssh вернул код ${LASTEXITCODE}: $Command" }
}

Write-Host "== Проверка SSH ==" -ForegroundColor Cyan
$target = "$UserName@$Ip"
& ssh @sshOptions $target "echo connected"
if ($LASTEXITCODE -ne 0) { throw "SSH недоступен на $Ip" }

Write-Host "== Упаковка проекта ==" -ForegroundColor Cyan
$archive = Join-Path $env:TEMP "net-assistant-lab.tar.gz"
if (Test-Path $archive) { Remove-Item $archive -Force }
& tar -czf $archive --exclude=.venv --exclude=data --exclude=__pycache__ `
    --exclude=.git -C $ProjectRoot .
if ($LASTEXITCODE -ne 0) { throw "Не удалось упаковать проект" }
Write-Host ("размер архива: {0:N1} МБ" -f ((Get-Item $archive).Length / 1MB))

Write-Host "== Копирование в ВМ ==" -ForegroundColor Cyan
& scp @sshOptions $archive "${target}:/tmp/net-assistant.tar.gz"
if ($LASTEXITCODE -ne 0) { throw "scp не смог скопировать архив" }

Write-Host "== Установка внутри ВМ ==" -ForegroundColor Cyan
Invoke-Ssh "sudo rm -rf /opt/net-assistant && sudo mkdir -p /opt/net-assistant && sudo tar -xzf /tmp/net-assistant.tar.gz --no-same-permissions -C /opt/net-assistant && rm -f /tmp/net-assistant.tar.gz"

$provision = "sudo bash /opt/net-assistant/deploy/provision_vm.sh"
& ssh @sshOptions $target $provision
if ($LASTEXITCODE -ne 0) { throw "provision вернул код $LASTEXITCODE" }

Write-Host ""
Write-Host "== Проверка ядра ==" -ForegroundColor Cyan
Start-Sleep -Seconds 5
& ssh @sshOptions $target "curl -s --max-time 5 http://127.0.0.1:8787/health; echo; systemctl is-active net-assistant-core"

Write-Host ""
Write-Host "Готово. Ядро в ВМ: http://${Ip}:8787" -ForegroundColor Green
Write-Host "Дашборд: http://${Ip}:8787" -ForegroundColor Green
