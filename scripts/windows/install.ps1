# Hushyor AI - Windows o'rnatuvchisi (do'kon kompyuteri). Bir marta ishga tushiriladi; qayta yurgizish xavfsiz.
#
#   install_windows.bat                       (ikki marta bosing)  yoki:
#   powershell -ExecutionPolicy Bypass -File scripts\windows\install.ps1 [-WithCloud] [-NoAutostart] [-Quiet]
#
# Nima qiladi:
#   1. Python 3.11/3.12 ni topadi (yo'q bo'lsa winget bilan o'rnatishni taklif qiladi)
#   2. .venv va kutubxonalar (requirements.txt o'zgarsa - yangilanadi)
#   3. .env: panel paroli (standart admin123 qoldirilmaydi), Telegram bot tokeni, do'kon nomi, bulut (ixtiyoriy)
#   4. O'zini tekshirish: kutubxonalar, modellar, port
#   5. Windows'ga kirganda avtomatik start (HushyorAI vazifasi, nazoratchi bilan)
#   6. -WithCloud: shu kompyuterda bulut serveri ham (HushyorCloud vazifasi) va birinchi egasi
# Yangilash (yangi versiya ZIP'i): scripts\windows\update.ps1 -Zip <fayl>

param(
    [switch]$WithCloud,
    [switch]$NoAutostart,
    [switch]$Quiet
)

# "Continue": Windows PowerShell 5.1 da tashqi dastur (python, pip) stderr'ga yozsa ham skript to'xtamasin -
# xatolar $LASTEXITCODE bo'yicha tekshiriladi
$ErrorActionPreference = "Continue"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $root
$envFile = Join-Path $root ".env"
$venvPy = Join-Path $root ".venv\Scripts\python.exe"

function Step($n, $text) { Write-Host ""; Write-Host "[$n/6] $text" -ForegroundColor Cyan }
function Ok($text) { Write-Host "   OK  $text" -ForegroundColor Green }
function Warn($text) { Write-Host "   !   $text" -ForegroundColor Yellow }
function Fail($text) { Write-Host "   XATO: $text" -ForegroundColor Red; exit 1 }

function Ask($question, $default) {
    if ($Quiet) { return $default }
    $suffix = ""
    if ($default) { $suffix = " [$default]" }
    $a = Read-Host "   $question$suffix"
    if ([string]::IsNullOrWhiteSpace($a)) { return $default }
    return $a.Trim()
}

function AskYes($question, [bool]$default) {
    if ($Quiet) { return $default }
    $d = "y/N"
    if ($default) { $d = "Y/n" }
    $a = Read-Host "   $question ($d)"
    if ([string]::IsNullOrWhiteSpace($a)) { return $default }
    return $a.Trim().ToLower().StartsWith("y") -or $a.Trim().ToLower().StartsWith("h")
}

function Read-EnvValue($key) {
    if (-not (Test-Path $envFile)) { return "" }
    foreach ($line in [IO.File]::ReadAllLines($envFile)) {
        $l = $line.Trim().TrimStart([char]0xFEFF)
        if ($l.StartsWith("$key=")) { return $l.Substring($key.Length + 1).Trim().Trim('"').Trim("'") }
    }
    return ""
}

function Set-EnvValue($key, $value) {
    # Faqat shu kalit qatori almashadi; qolgan qatorlar va izohlar saqlanadi. UTF-8, BOM'siz.
    $lines = New-Object System.Collections.Generic.List[string]
    if (Test-Path $envFile) { $lines.AddRange([string[]][IO.File]::ReadAllLines($envFile)) }
    $found = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i].Trim().TrimStart([char]0xFEFF).StartsWith("$key=")) { $lines[$i] = "$key=$value"; $found = $true }
    }
    if (-not $found) { $lines.Add("$key=$value") }
    [IO.File]::WriteAllLines($envFile, $lines, (New-Object System.Text.UTF8Encoding($false)))
}

function Read-Secret($prompt) {
    $s = Read-Host "   $prompt" -AsSecureString
    $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
}

function Find-Python {
    $cands = @(@("py", "-3.12"), @("py", "-3.11"), @("python", $null), @("py", "-3"))
    foreach ($c in $cands) {
        $exe = $c[0]; $arg = $c[1]
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        try {
            if ($arg) { $v = & $exe $arg -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null }
            else { $v = & $exe -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null }
        } catch { continue }
        if ($v -match '^3\.(1[0-3])$') { return @{ Exe = $exe; Arg = $arg; Version = $v } }
    }
    return $null
}

Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "  Hushyor AI - o'rnatish ($root)" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan

# ------------------------------------------------------------------ 1. Python
Step 1 "Python"
$py = Find-Python
if (-not $py) {
    Warn "Python 3.10-3.13 topilmadi."
    if ((Get-Command winget -ErrorAction SilentlyContinue) -and (AskYes "Python 3.12 ni winget bilan o'rnataymi?" $true)) {
        winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
        $py = Find-Python
    }
    if (-not $py) { Fail "Python o'rnatilmagan. https://www.python.org/downloads/ dan 3.12 ni o'rnating ('Add python.exe to PATH' belgisi bilan), keyin qayta ishga tushiring." }
}
Ok "Python $($py.Version)"

# ------------------------------------------------------------------ 2. .venv va kutubxonalar
Step 2 "Virtual muhit va kutubxonalar"
if (-not (Test-Path $venvPy)) {
    if ($py.Arg) { & $py.Exe $py.Arg -m venv .venv } else { & $py.Exe -m venv .venv }
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPy)) { Fail "Virtual muhit yaratilmadi" }
    Ok ".venv yaratildi"
}
$reqHash = (Get-FileHash -Algorithm SHA256 (Join-Path $root "requirements.txt")).Hash.ToLower()
$marker = Join-Path $root ".venv\.installed"
$old = ""
if (Test-Path $marker) { $old = ([IO.File]::ReadAllText($marker)).Trim().ToLower() }
if ($old -ne $reqHash) {
    Write-Host "   Kutubxonalar o'rnatilmoqda (birinchi marta 3-10 daqiqa)..."
    & $venvPy -m pip install --upgrade pip --quiet
    $okPip = $false
    for ($try = 1; $try -le 3 -and -not $okPip; $try++) {
        & $venvPy -m pip install -r requirements.txt --quiet
        if ($LASTEXITCODE -eq 0) { $okPip = $true } else { Warn "pip xatosi (urinish $try/3) - internetni tekshiring"; Start-Sleep 5 }
    }
    if (-not $okPip) { Fail "Kutubxonalarni o'rnatib bo'lmadi" }
    [IO.File]::WriteAllText($marker, $reqHash)
    Ok "kutubxonalar o'rnatildi"
} else {
    Ok "kutubxonalar joyida (requirements.txt o'zgarmagan)"
}

# ------------------------------------------------------------------ 3. .env
Step 3 "Sozlama (.env)"
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $root ".env.example") $envFile
    Ok ".env yaratildi (.env.example dan)"
}
$pw = Read-EnvValue "ADMIN_PASSWORD"
if (-not $Quiet -and ($pw -eq "" -or $pw -eq "admin123" -or $pw.Length -lt 8)) {
    Write-Host "   Panel paroli standart (admin123) - uni o'zgartiramiz (kamida 8 belgi)."
    while ($true) {
        $p1 = Read-Secret "Yangi panel paroli"
        if ($p1.Length -lt 8 -or $p1 -eq "admin123") { Warn "kamida 8 belgi va admin123 emas"; continue }
        # .env o'quvchi qiymat chetidagi bo'shliq va qo'shtirnoqlarni olib tashlaydi - bunday parol bilan kirib bo'lmasdi
        if ($p1 -ne $p1.Trim() -or $p1 -match "^['`"]" -or $p1 -match "['`"]$") { Warn "parol bo'shliq yoki qo'shtirnoq bilan boshlanmasin/tugamasin"; continue }
        $p2 = Read-Secret "Yana bir marta"
        if ($p1 -ne $p2) { Warn "parollar mos emas"; continue }
        Set-EnvValue "ADMIN_PASSWORD" $p1
        Ok "panel paroli saqlandi"
        break
    }
} elseif ($pw -eq "admin123") {
    Warn "panel paroli standart (admin123): tarmoqdan kirib bo'lmaydi, faqat shu kompyuterdan"
} else {
    Ok "panel paroli o'rnatilgan"
}
$tok = Read-EnvValue "TELEGRAM_BOT_TOKEN"
if (-not $tok -and -not $Quiet) {
    $t = Ask "Telegram bot tokeni (@BotFather; o'tkazib yuborish - Enter)" ""
    if ($t) {
        if ($t -notmatch '^\d{6,12}:[A-Za-z0-9_-]{30,}$') { Warn "token ko'rinishi odatiy emas - keyin .env da tekshiring" }
        Set-EnvValue "TELEGRAM_BOT_TOKEN" $t
        Ok "Telegram tokeni saqlandi"
    }
} elseif ($tok) { Ok "Telegram tokeni bor" }
if (-not (Read-EnvValue "CLOUD_URL") -and -not $Quiet -and (AskYes "Bulutga (markaziy serverga) ulaysizmi?" $false)) {
    Write-Host "   Bulut panelida: Filiallar -> Filial qo'shish. Qurilma identifikatori dastur ishga tushgach"
    Write-Host "   'Tizim holati -> Bulut' da ko'rinadi; filial yaratilgach bergan tokenni shu yerga kiriting."
    $u = Ask "Bulut manzili (masalan https://panel.dokon.uz yoki http://127.0.0.1:9000)" ""
    $e = Ask "Qurilma tokeni (EDGE_TOKEN)" ""
    if ($u -and $e) {
        Set-EnvValue "CLOUD_URL" $u.TrimEnd("/")
        Set-EnvValue "EDGE_TOKEN" $e
        Ok "bulut sozlandi"
    }
}

# ------------------------------------------------------------------ 4. tekshiruv
Step 4 "O'zini tekshirish"
$check = & $venvPy -c "import cv2, onnxruntime, fastapi, uvicorn, sklearn, httpx, numpy; print('ok', cv2.__version__, onnxruntime.__version__)" 2>&1
if ($LASTEXITCODE -ne 0) { Fail "kutubxona yuklanmadi: $check" }
Ok "kutubxonalar: $check"
foreach ($m in @("models\yolo11n-pose.onnx", "models\yolo11n.onnx")) {
    if (Test-Path (Join-Path $root $m)) { Ok $m } else { Warn "$m yo'q (GitHub'dan qayta yuklang)" }
}
$port = Read-EnvValue "PORT"
if (-not $port) { $port = "8000" }
$busy = Get-NetTCPConnection -LocalPort ([int]$port) -State Listen -ErrorAction SilentlyContinue
if ($busy) { Warn "$port-port band (Hushyor AI allaqachon ishlayotgan bo'lishi mumkin)" } else { Ok "$port-port bo'sh" }

# ------------------------------------------------------------------ 5. avtomatik start
Step 5 "Avtomatik start"
if ($NoAutostart) {
    Warn "o'tkazib yuborildi (-NoAutostart). Qo'lda: .venv\Scripts\python.exe run.py"
} else {
    if ($busy) {
        Stop-ScheduledTask -TaskName HushyorAI -ErrorAction SilentlyContinue
        Start-Sleep 3
    }
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "install_autostart.ps1")
}

# ------------------------------------------------------------------ 6. bulut serveri (ixtiyoriy)
Step 6 "Bulut serveri shu kompyuterda"
if ($WithCloud) {
    $hasOwner = & $venvPy -c "from cloud import db; db.init_db(); print(1 if db.row('SELECT id FROM users LIMIT 1') else 0)" 2>$null
    if ($hasOwner -ne "1") {
        Write-Host "   Birinchi egasi (bulut paneliga kirish):"
        $em = Ask "Email" ""
        $co = Ask "Kompaniya nomi" "Mening kompaniyam"
        if ($em) { & $venvPy -m cloud.server --init-owner $em - $co }
    } else { Ok "bulutda egasi bor" }
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "install_cloud_autostart.ps1")
} else {
    Write-Host "   o'tkazib yuborildi (kerak bo'lsa: install.ps1 -WithCloud)"
}

Write-Host ""
Write-Host "Tayyor." -ForegroundColor Green
Write-Host "  Panel:  http://localhost:$port   (login: admin)"
Write-Host "  Jurnal: $root\data\logs\"
Write-Host "  Yangilash: powershell -ExecutionPolicy Bypass -File scripts\windows\update.ps1 -Zip <yangi_versiya.zip>"
