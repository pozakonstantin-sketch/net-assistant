# Smoke-тест: проверяет ядро, агентов, защиту каталога и подтверждения.
# Запуск (ядро и fake-агенты должны быть уже запущены):
#     .\tools\smoke_test.ps1
param([string]$Base = "http://127.0.0.1:8787")

$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$root = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $root ".env"
if (-not (Test-Path $envPath)) { throw "нет файла .env — скопируйте .env.example" }

$token = (Select-String -Path $envPath -Pattern '^ASSISTANT_TOKEN=(.*)$').Matches[0].Groups[1].Value.Trim()
$Headers = @{ Authorization = "Bearer $token" }
$script:passed = 0
$script:failed = 0

function Check([string]$Name, [scriptblock]$Test) {
    try {
        & $Test
        $script:passed++
        Write-Host ("  [OK]   " + $Name) -ForegroundColor Green
    } catch {
        $script:failed++
        Write-Host ("  [FAIL] " + $Name + " — " + $_.Exception.Message) -ForegroundColor Red
    }
}

function Api([string]$Method, [string]$Path, [string]$Body) {
    $params = @{ Uri = "$Base$Path"; Method = $Method; Headers = $script:Headers }
    if ($Body) {
        $params.ContentType = "application/json; charset=utf-8"
        $params.Body = [System.Text.Encoding]::UTF8.GetBytes($Body)
    }
    return Invoke-RestMethod @params
}

function ExpectStatus([string]$Method, [string]$Path, [string]$Body, [int]$Code) {
    try {
        Api $Method $Path $Body | Out-Null
        throw "ожидался HTTP $Code, а запрос прошёл"
    } catch {
        $actual = $_.Exception.Response.StatusCode.value__
        if ($actual -ne $Code) { throw "ожидался HTTP $Code, получен $actual" }
    }
}

Write-Host "`nЯдро: $Base`n" -ForegroundColor Cyan

Check "ядро отвечает" {
    $health = Api "GET" "/health" $null
    if ($health.status -ne "ok") { throw "status=$($health.status)" }
}

Check "подключены три тестовых устройства" {
    $devices = (Api "GET" "/devices" $null).devices
    if ($devices.Count -ne 3) { throw "подключено $($devices.Count)" }
}

Check "команда sys.info выполняется" {
    $result = (Api "POST" "/cmd" '{"device":"fake-pc","action":"sys.info"}')
    if ($result.status -ne "ok") { throw "status=$($result.status)" }
    if (-not $result.result.host) { throw "нет поля host" }
}

Check "скриншот сохраняется и отдаётся по ссылке" {
    $result = (Api "POST" "/cmd" '{"device":"fake-pc","action":"sys.screenshot"}')
    $url = $result.result.url
    if (-not $url) { throw "нет url" }
    $static = Invoke-WebRequest ("$Base" + $url) -Headers $Headers
    if ($static.StatusCode -ne 200) { throw "статика вернула $($static.StatusCode)" }
}

Check "опасная команда ждёт подтверждения" {
    $pending = (Api "POST" "/cmd" '{"device":"fake-router","action":"router.reboot"}')
    if ($pending.status -ne "awaiting_confirmation") { throw "status=$($pending.status)" }
    $script:token = $pending.token
}

Check "подтверждение выполняет команду" {
    if (-not $script:token) { throw "нет токена из предыдущей проверки" }
    $done = (Api "POST" "/confirm/$($script:token)" $null)
    if ($done.status -ne "ok") { throw "status=$($done.status)" }
}

Check "команда вне каталога запрещена (400)" {
    ExpectStatus "POST" "/cmd" '{"device":"fake-pc","action":"sys.format_disk"}' 400
}

Check "неизвестное устройство (503)" {
    ExpectStatus "POST" "/cmd" '{"device":"nope","action":"sys.info"}' 503
}

Check "запрос без токена отклоняется (401)" {
    try {
        Invoke-RestMethod "$Base/devices" | Out-Null
        throw "запрос без токена прошёл"
    } catch {
        $actual = $_.Exception.Response.StatusCode.value__
        if ($actual -ne 401) { throw "ожидался 401, получен $actual" }
    }
}

Check "аудит пишется" {
    $records = (Api "GET" "/audit?limit=10" $null).records
    if ($records.Count -eq 0) { throw "журнал пуст" }
}

Check "дашборд отдаётся" {
    $page = Invoke-WebRequest "$Base/" -UseBasicParsing
    if ($page.StatusCode -ne 200) { throw "статус $($page.StatusCode)" }
    if ($page.Content -notmatch 'Net') { throw "страница без заголовка" }
}

Check "health сообщает о голосе и LLM" {
    $health = Api "GET" "/health" $null
    if (-not $health.PSObject.Properties.Name.Contains('stt_engine')) { throw "нет поля stt_engine" }
    if (-not $health.PSObject.Properties.Name.Contains('llm_configured')) { throw "нет поля llm_configured" }
}

Check "голосовой эндпоинт существует (без файла — 422)" {
    ExpectStatus "POST" "/voice" "{}" 422
}

Write-Host ""
if ($script:failed -eq 0) {
    Write-Host "Итог: всё зелёное, $($script:passed) проверок" -ForegroundColor Green
    exit 0
}
Write-Host "Итог: провалено $($script:failed) из $($script:passed + $script:failed)" -ForegroundColor Red
exit 1
