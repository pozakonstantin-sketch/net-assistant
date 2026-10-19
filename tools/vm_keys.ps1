# Ввод текста и нажатий в консоль виртуальной машины Hyper-V.
#
# Три способа ввода:
#   -ScanText  — раскладка US собирается вручную из скан-кодов: работает всегда,
#                даже когда штатный TypeText искажает символы (так ведёт себя
#                консоль Hyper-V в гостях под X11).
#   -Text      — штатный TypeText (быстро, но в X11 бывает ненадёжен).
#   -Key/-Combo — одиночные клавиши и комбинации.
#
# Примеры:
#   .\tools\vm_keys.ps1 -Name assistant -ScanText "echo HI{ENTER}"
#   .\tools\vm_keys.ps1 -Name assistant -Key ENTER
#   .\tools\vm_keys.ps1 -Name assistant -Combo CTRL+ALT+T

[CmdletBinding(DefaultParameterSetName = 'Scan')]
param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true, ParameterSetName = 'Scan')][string]$ScanText,
    [Parameter(Mandatory = $true, ParameterSetName = 'Text')][string]$Text,
    [Parameter(Mandatory = $true, ParameterSetName = 'Key')][string]$Key,
    [Parameter(Mandatory = $true, ParameterSetName = 'Combo')][string]$Combo,
    [int]$DelayMs = 25
)

$ErrorActionPreference = 'Stop'
$ns = 'root\virtualization\v2'

$vm = Get-CimInstance -Namespace $ns -ClassName Msvm_ComputerSystem -Filter "ElementName='$Name'"
if (-not $vm) { throw "Виртуальная машина '$Name' не найдена" }

$keyboard = Get-CimAssociatedInstance -ResultClassName Msvm_Keyboard -InputObject $vm |
    Select-Object -First 1
if (-not $keyboard) { throw "У '$Name' нет клавиатуры" }

$BREAK = 0x80
$scancodes = @{
    'ENTER'=0x1C; 'TAB'=0x0F; 'ESC'=0x01; 'SPACE'=0x39; 'BACKSPACE'=0x0E; 'DELETE'=0x53
    'UP'=0x48; 'DOWN'=0x50; 'LEFT'=0x4B; 'RIGHT'=0x4D
    'HOME'=0x47; 'END'=0x4F; 'PGUP'=0x49; 'PGDN'=0x51
    'F1'=0x3B; 'F2'=0x3C; 'F3'=0x3D; 'F4'=0x3E; 'F5'=0x3F; 'F6'=0x40
    'F7'=0x41; 'F8'=0x42; 'F9'=0x43; 'F10'=0x44; 'F11'=0x57; 'F12'=0x58
    'CTRL'=0x1D; 'ALT'=0x38; 'SHIFT'=0x2A
    # буквы и цифры — нужны для комбинаций вида CTRL+ALT+T
    'Q'=0x10; 'W'=0x11; 'E'=0x12; 'R'=0x13; 'T'=0x14; 'Y'=0x15; 'U'=0x16; 'I'=0x17
    'O'=0x18; 'P'=0x19; 'A'=0x1E; 'S'=0x1F; 'D'=0x20; 'F'=0x21; 'G'=0x22; 'H'=0x23
    'J'=0x24; 'K'=0x25; 'L'=0x26; 'Z'=0x2C; 'X'=0x2D; 'C'=0x2E; 'V'=0x2F; 'B'=0x30
    'N'=0x31; 'M'=0x32
    '1'=0x02; '2'=0x03; '3'=0x04; '4'=0x05; '5'=0x06; '6'=0x07; '7'=0x08; '8'=0x09
    '9'=0x0A; '0'=0x0B; 'MINUS'=0x0C; 'EQUAL'=0x0D; 'SLASH'=0x35; 'DOT'=0x34; 'COMMA'=0x33
}

function Send-Codes {
    param([byte[]]$Codes)
    Invoke-CimMethod -InputObject $keyboard -MethodName TypeScancodes -Arguments @{
        Scancodes = [byte[]]$Codes
    } | Out-Null
    Start-Sleep -Milliseconds $DelayMs
}

function Send-KeyName {
    param([string]$KeyName)
    $upper = $KeyName.ToUpper()
    if (-not $scancodes.ContainsKey($upper)) { throw "Неизвестная клавиша '$KeyName'" }
    $code = [byte]$scancodes[$upper]
    Send-Codes @($code, ($code -bor $BREAK))
}

# ─────────── раскладка US: символ → скан-код (со Shift, где нужно) ───────────
$plain = @{}
$letters = 'qwertyuiopasdfghjklzxcvbnm'
$letterCodes = @(0x10,0x11,0x12,0x13,0x14,0x15,0x16,0x17,0x18,0x19,
                 0x1E,0x1F,0x20,0x21,0x22,0x23,0x24,0x25,0x26,
                 0x2C,0x2D,0x2E,0x2F,0x30,0x31,0x32)
for ($i = 0; $i -lt $letters.Length; $i++) {
    $plain[$letters[$i]] = [byte]$letterCodes[$i]
    $plain[$letters[$i].ToString().ToUpper()] = [byte]$letterCodes[$i]
}
$plain['1'] = 0x02; $plain['2'] = 0x03; $plain['3'] = 0x04; $plain['4'] = 0x05
$plain['5'] = 0x06; $plain['6'] = 0x07; $plain['7'] = 0x08; $plain['8'] = 0x09
$plain['9'] = 0x0A; $plain['0'] = 0x0B
$plain['-'] = 0x0C; $plain['='] = 0x0D
$plain['['] = 0x1A; $plain[']'] = 0x1B; $plain['\'] = 0x2B
$plain[';'] = 0x27; $plain["'"] = 0x28; $plain['`'] = 0x29
$plain[','] = 0x33; $plain['.'] = 0x34; $plain['/'] = 0x35
$plain[' '] = 0x39

# символы, требующие Shift
$shifted = @{
    '!'=0x02; '@'=0x03; '#'=0x04; '$'=0x05; '%'=0x06; '^'=0x07; '&'=0x08; '*'=0x09
    '('=0x0A; ')'=0x0B; '_'=0x0C; '+'=0x0D; '{'=0x1A; '}'=0x1B; '|'=0x2B
    ':'=0x27; '"'=0x28; '~'=0x29; '<'=0x33; '>'=0x34; '?'=0x35
}
# заглавные буквы — через Shift
$upperLetters = @{}
for ($i = 0; $i -lt $letters.Length; $i++) {
    $upperLetters[$letters[$i].ToString().ToUpper()] = [byte]$letterCodes[$i]
}

function Send-Char {
    param([char]$Char)
    $lower = $Char.ToString().ToLower()
    $needsShift = $false
    $code = $null

    if ($shifted.ContainsKey($Char.ToString())) {
        $code = [byte]$shifted[$Char.ToString()]
        $needsShift = $true
    } elseif ($upperLetters.ContainsKey($Char.ToString()) -and $Char.ToString() -cne $lower) {
        $code = [byte]$upperLetters[$Char.ToString()]
        $needsShift = $true
    } elseif ($plain.ContainsKey($Char.ToString())) {
        $code = [byte]$plain[$Char.ToString()]
    } else {
        throw "Символ '$Char' не поддерживается (раскладка US)"
    }

    if ($needsShift) {
        Send-Codes @(0x2A, $code, ($code -bor $BREAK), (0x2A -bor $BREAK))
    } else {
        Send-Codes @($code, ($code -bor $BREAK))
    }
}

if ($PSCmdlet.ParameterSetName -eq 'Key') {
    Send-KeyName $Key
    Write-Output "нажато: $Key"
    return
}

if ($PSCmdlet.ParameterSetName -eq 'Combo') {
    $parts = $Combo.ToUpper() -split '\+'
    $codes = @()
    foreach ($part in $parts) {
        if (-not $scancodes.ContainsKey($part)) { throw "Неизвестная часть комбинации '$part'" }
        $codes += [byte]$scancodes[$part]
    }
    $press = $codes
    $release = @($codes[($codes.Count - 1)..0] | ForEach-Object { [byte]($_ -bor $BREAK) })
    Send-Codes ([byte[]]($press + $release))
    Write-Output "нажато: $Combo"
    return
}

if ($PSCmdlet.ParameterSetName -eq 'Text') {
    $pattern = '\{[A-Za-z0-9]+\}'
    $parts = [regex]::Split($Text, "($pattern)")
    foreach ($part in $parts) {
        if (-not $part) { continue }
        if ($part -match '^\{([A-Za-z0-9]+)\}$') {
            Send-KeyName $Matches[1]
            continue
        }
        Invoke-CimMethod -InputObject $keyboard -MethodName TypeText -Arguments @{
            AsciiText = $part
        } | Out-Null
        Start-Sleep -Milliseconds $DelayMs
    }
    Write-Output "набрано (TypeText): $Text"
    return
}

# ─────────── ScanText: посимвольно скан-кодами ───────────
$pattern = '\{[A-Za-z0-9]+\}'
$parts = [regex]::Split($ScanText, "($pattern)")
foreach ($part in $parts) {
    if (-not $part) { continue }
    if ($part -match '^\{([A-Za-z0-9]+)\}$') {
        Send-KeyName $Matches[1]
        continue
    }
    foreach ($char in $part.ToCharArray()) {
        Send-Char $char
    }
}
Write-Output "набрано (скан-кодами): $ScanText"
