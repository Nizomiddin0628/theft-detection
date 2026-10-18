# Hushyor AI - yangi versiyaga yangilash (do'kon kompyuterida). Ma'lumot (data\, cloud\data\, .env) saqlanadi.
#
#   powershell -ExecutionPolicy Bypass -File scripts\windows\update.ps1 -Zip "$HOME\Downloads\hushyor_ai_2.0.0_yangilanish.zip"
#   powershell -ExecutionPolicy Bypass -File scripts\windows\update.ps1            # git pull bilan (repo bo'lsa)
#
# Tartib: ZIP vaqtinchalik papkada ochilib tekshiriladi -> vazifalar to'xtatiladi -> zaxira (.env, bazalar WAL bilan,
# almashadigan kod fayllari) -> yangi fayllar -> kutubxonalar (requirements.txt o'zgargan bo'lsa) -> vazifalar qayta
# ishga tushadi -> /healthz. Kutubxona o'rnatilmasa yoki panel 2 daqiqada javob bermasa - kod avtomatik eski holatga
# qaytariladi va eski versiya ishga tushiriladi. Zaxira: data\backups\update_<vaqt>
# Faqat ASCII (Windows PowerShell 5.1 BOM'siz faylni ANSI kodlashda o'qiydi).

param(
    [string]$Zip = "",
    [switch]$NoStart
)

$ErrorActionPreference = "Continue"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $root
$venvPy = Join-Path $root ".venv\Scripts\python.exe"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$bak = Join-Path $root "data\backups\update_$stamp"
$bakCode = Join-Path $bak "code"

function Ok($t) { Write-Host "   OK  $t" -ForegroundColor Green }
function Warn($t) { Write-Host "   !   $t" -ForegroundColor Yellow }
function Fail($t) { Write-Host "   XATO  $t" -ForegroundColor Red }

function Get-Port {
    $port = 8000
    $envp = Join-Path $root ".env"
    if (Test-Path $envp) {
        $l = Select-String -Path $envp -Pattern '^\s*PORT\s*=\s*(\d+)' | Select-Object -First 1
        if ($l) { $port = [int]$l.Matches[0].Groups[1].Value }
    }
    return $port
}

function Test-Health([int]$seconds) {
    $port = Get-Port
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $req = [System.Net.WebRequest]::Create("http://127.0.0.1:$port/healthz")
            $req.Proxy = $null          # tizim proksisi chetlab o'tiladi
            $req.Timeout = 3000
            $resp = $req.GetResponse()
            $body = (New-Object System.IO.StreamReader($resp.GetResponseStream())).ReadToEnd()
            $resp.Close()
            if ($body -match '"version"\s*:\s*"([^"]+)"') { return $Matches[1] }
            return "?"
        } catch { Start-Sleep 2 }
    }
    return $null
}

function Stop-Hushyor($tasks) {
    foreach ($t in $tasks) { Stop-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue }
    # Nazoratchi o'ldirilsa bola jarayon o'zi toza to'xtaydi (20 s gacha) - kutamiz
    $left = $null
    for ($i = 0; $i -lt 30; $i++) {
        $left = Get-CimInstance Win32_Process -Filter "Name like 'python%.exe'" |
            Where-Object { $_.ExecutablePath -like "$root*" -and ($_.CommandLine -like "*run.py*" -or $_.CommandLine -like "*cloud.server*") }
        if (-not $left) { break }
        Start-Sleep 1
    }
    if ($left) { $left | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Warn "qolgan jarayonlar majburan to'xtatildi" }
}

if ($Zip -and -not (Test-Path $Zip)) { Fail "ZIP topilmadi: $Zip"; exit 1 }

# ---------------------------------------------------------------- [1/7] ZIP tekshiruvi (hali hech narsa o'zgarmaydi)
Write-Host "[1/7] Yangilanishni tekshirish" -ForegroundColor Cyan
$stage = $null
if ($Zip) {
    $stage = Join-Path $env:TEMP "hushyor_update_$stamp"
    try {
        Expand-Archive -Path $Zip -DestinationPath $stage -Force -ErrorAction Stop
    } catch {
        Fail "ZIP ochilmadi (buzilgan yoki to'liq yuklanmagan): $($_.Exception.Message)"; exit 1
    }
    $markers = @("app", "cloud", "run.py", "README.md", "requirements.txt")
    $has = { param($d) foreach ($m in $markers) { if (Test-Path (Join-Path $d $m)) { return $true } }; return $false }
    if (-not (& $has $stage)) {
        # ZIP ichida bitta papka bo'lsa (masalan, GitHub'dan yuklangan) - fayllar shu papkadan olinadi
        $sub = @(Get-ChildItem -Path $stage -Directory)
        if ($sub.Count -eq 1 -and (& $has $sub[0].FullName)) { $stage = $sub[0].FullName }
    }
    if (-not (& $has $stage)) { Fail "ZIP ichida Hushyor AI fayllari topilmadi - hech narsa o'zgarmadi"; exit 1 }
    # Shaxsiy ma'lumot va sozlama hech qachon ZIP'dan ustiga yozilmaydi
    foreach ($skip in @(".env", "data", "cloud\data", ".venv")) {
        $p = Join-Path $stage $skip
        if (Test-Path $p) { Remove-Item -Recurse -Force $p; Warn "ZIP ichidagi $skip o'tkazib yuborildi" }
    }
    $files = @(Get-ChildItem -Path $stage -Recurse -File | ForEach-Object { $_.FullName.Substring($stage.Length).TrimStart('\') })
    Ok "ZIP to'g'ri: $($files.Count) ta fayl"
} elseif (Test-Path ".git") {
    Ok "git repo: git pull bilan yangilanadi"
} else {
    Fail "ZIP berilmadi va bu papka git repo emas - yangilash uchun -Zip <fayl> bering"; exit 1
}

# ---------------------------------------------------------------- [2/7] to'xtatish
Write-Host "[2/7] To'xtatish" -ForegroundColor Cyan
$tasks = @(@("HushyorAI", "HushyorCloud") | Where-Object { Get-ScheduledTask -TaskName $_ -ErrorAction SilentlyContinue })
Stop-Hushyor $tasks
Ok "to'xtatildi ($($tasks -join ', '))"

# ---------------------------------------------------------------- [3/7] zaxira
Write-Host "[3/7] Zaxira nusxa" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $bak | Out-Null
$keep = @(".env", "cloud\data\vapid_private.pem")
foreach ($db in @("data\hushyor.db", "cloud\data\cloud.db")) { $keep += @($db, "$db-wal", "$db-shm") }   # WAL ham: oxirgi yozuvlar shunda
foreach ($f in $keep) {
    $p = Join-Path $root $f
    if (Test-Path $p) {
        $dst = Join-Path $bak $f
        New-Item -ItemType Directory -Force -Path (Split-Path $dst) | Out-Null
        Copy-Item $p $dst -Force
    }
}
$newFiles = @()
$oldHead = ""
if ($stage) {
    foreach ($rel in $files) {
        $cur = Join-Path $root $rel
        if (Test-Path $cur) {
            $dst = Join-Path $bakCode $rel
            New-Item -ItemType Directory -Force -Path (Split-Path $dst) | Out-Null
            Copy-Item $cur $dst -Force
        } else { $newFiles += $rel }
    }
    if (Test-Path "cloud\panel_dist") {      # panel build'i to'liq (xesh nomli fayllar)
        Copy-Item "cloud\panel_dist" (Join-Path $bakCode "cloud\panel_dist") -Recurse -Force
    }
} else {
    $oldHead = (git rev-parse HEAD 2>$null)
}
Ok "data\backups\update_$stamp"

function Restore-Code {
    if ($stage) {
        if (Test-Path (Join-Path $bakCode "cloud\panel_dist")) {
            if (Test-Path "cloud\panel_dist") { Remove-Item -Recurse -Force "cloud\panel_dist" }
        }
        if (Test-Path $bakCode) { Copy-Item (Join-Path $bakCode "*") $root -Recurse -Force }
        foreach ($rel in $newFiles) { Remove-Item (Join-Path $root $rel) -Force -ErrorAction SilentlyContinue }
    } elseif ($oldHead) {
        git reset --keep $oldHead | Out-Null      # --keep: mahalliy o'zgarishlaringiz saqlanadi
    }
}

# ---------------------------------------------------------------- [4/7] yangi fayllar
Write-Host "[4/7] Yangi fayllar" -ForegroundColor Cyan
$reqBefore = (Get-FileHash -Algorithm SHA256 requirements.txt).Hash.ToLower()
if ($stage) {
    if (Test-Path (Join-Path $stage "cloud\panel_dist")) {
        if (Test-Path "cloud\panel_dist") { Remove-Item -Recurse -Force "cloud\panel_dist" }   # eski xesh nomli fayllar qolmasin
    }
    $copyOk = $true
    try { Copy-Item (Join-Path $stage "*") $root -Recurse -Force -ErrorAction Stop } catch { $copyOk = $false; Fail "nusxalashda xato: $($_.Exception.Message)" }
    if (-not $copyOk) {
        Restore-Code; Warn "eski fayllar qaytarildi"
        foreach ($t in $tasks) { Start-ScheduledTask -TaskName $t }
        exit 1
    }
    Ok "fayllar yangilandi: $(Split-Path $Zip -Leaf)"
} else {
    git pull --ff-only
    if ($LASTEXITCODE -ne 0) {
        Fail "git pull bajarilmadi - o'zgarishlaringizni tekshiring (git status). Hech narsa o'zgarmadi."
        foreach ($t in $tasks) { Start-ScheduledTask -TaskName $t }
        exit 1
    }
    Ok "git pull"
}

# ---------------------------------------------------------------- [5/7] kutubxonalar
Write-Host "[5/7] Kutubxonalar" -ForegroundColor Cyan
$reqHash = (Get-FileHash -Algorithm SHA256 requirements.txt).Hash.ToLower()
$marker = Join-Path $root ".venv\.installed"
$old = ""
if (Test-Path $marker) { $old = ([IO.File]::ReadAllText($marker)).Trim().ToLower() }
if ($old -ne $reqHash -or $reqBefore -ne $reqHash) {
    & $venvPy -m pip install -r requirements.txt --quiet
    if ($LASTEXITCODE -eq 0) {
        [IO.File]::WriteAllText($marker, $reqHash); Ok "yangilandi"
    } else {
        Fail "kutubxonalar o'rnatilmadi (internet?) - eski versiya qaytarilmoqda"
        Restore-Code
        foreach ($t in $tasks) { Start-ScheduledTask -TaskName $t }
        Warn "eski versiya ishga tushirildi. Internetni tekshirib, yangilashni qayta yurgizing."
        exit 1
    }
} else { Ok "o'zgarmagan" }

# 1.9.0: odam sanashni sizdagi asl app\ai\worker.py ga ulash (fayl almashtirilmaydi; qayta ishga tushirish zararsiz)
$hook = Join-Path $root "scripts\apply_counting_hook.py"
if (Test-Path $hook) {
    $wk = "app\ai\worker.py"
    $wkBak = Join-Path $bakCode $wk
    if ((Test-Path $wk) -and -not (Test-Path $wkBak)) {      # orqaga qaytishda asl worker.py ham tiklansin
        New-Item -ItemType Directory -Force -Path (Split-Path $wkBak) | Out-Null
        Copy-Item $wk $wkBak -Force
    }
    & $venvPy $hook
    if ($LASTEXITCODE -eq 0) { Ok "sanash ulangan" }
    else { Warn "sanash ulanmadi - dastur ishlaydi, lekin tashriflar sanalmaydi. app\ai\worker.py ni Claude'ga yuboring." }
}

# ---------------------------------------------------------------- [6/7] tekshiruv
Write-Host "[6/7] Tekshiruv" -ForegroundColor Cyan
$v = & $venvPy -c "from app.config import APP_VERSION; print(APP_VERSION)" 2>$null
if ($LASTEXITCODE -ne 0 -or -not $v) {
    Fail "yangi kod ishga tushmaydi (import xatosi) - eski versiya qaytarilmoqda"
    Restore-Code
    foreach ($t in $tasks) { Start-ScheduledTask -TaskName $t }
    exit 1
}
Ok "versiya: $v"
# 2.7.0: ochiq to'plam (YOLOX + MoveNet) fayli - MoveNet Thunder (25 MB, GitHub, SHA-256). Ixtiyoriy: yuklanmasa
# AI model sahifasida "Yuklab olish" qoladi. Dastur ishga tushgandan keyin yuklanadi (kameralar kutib turmasin;
# fayl atomar yoziladi - .part, keyin almashtirish)
function Invoke-ModelPrefetch {
    $pf = @(& $venvPy -c "from app.ai import model_zoo as m; f = getattr(m, 'prefetch', None); print(', '.join(f()) or 'hammasi bor') if f else print('-')" 2>&1 | ForEach-Object { "$_" })
    if ($LASTEXITCODE -eq 0) { Ok "model fayllari: $($pf | Select-Object -Last 1)" }
    else { Warn "MoveNet Thunder yuklanmadi (internet?) - kerak bo'lsa AI model sahifasida 'Yuklab olish'" }
}

# ---------------------------------------------------------------- [7/7] ishga tushirish
Write-Host "[7/7] Ishga tushirish" -ForegroundColor Cyan
if ($NoStart) { Warn "o'tkazib yuborildi (-NoStart)"; Invoke-ModelPrefetch; exit 0 }
if (-not $tasks) { Warn "avtomatik start vazifasi yo'q - install_windows.bat ni ishga tushiring"; Invoke-ModelPrefetch; exit 0 }
foreach ($t in $tasks) { Start-ScheduledTask -TaskName $t; Ok "$t ishga tushdi" }
if ($tasks -contains "HushyorAI") {
    $hv = Test-Health 120
    if (-not $hv) {
        Fail "panel 2 daqiqada javob bermadi - eski versiya qaytarilmoqda (jurnal: data\logs\app.log, console.log)"
        Stop-Hushyor $tasks
        Restore-Code
        foreach ($t in $tasks) { Start-ScheduledTask -TaskName $t }
        $hv2 = Test-Health 120
        if ($hv2) { Warn "eski versiya ($hv2) ishlayapti. Xato jurnalini Claude'ga yuboring." }
        else { Fail "eski versiya ham javob bermadi. Zaxira: data\backups\update_$stamp" }
        exit 1
    }
    Ok "panel javob beryapti (versiya $hv)"
}
Invoke-ModelPrefetch
if ($stage) { Remove-Item -Recurse -Force (Join-Path $env:TEMP "hushyor_update_$stamp") -ErrorAction SilentlyContinue }
Write-Host "Tayyor. Zaxira: data\backups\update_$stamp" -ForegroundColor Green
