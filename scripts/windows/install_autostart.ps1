# Hushyor AI: Windows'ga kirganda dasturni avtomatik ishga tushirish (Task Scheduler).
#
# Ishlatish (loyiha papkasida, oddiy PowerShell oynasida):
#   powershell -ExecutionPolicy Bypass -File scripts\windows\install_autostart.ps1
#
# Nima qiladi:
#   * "HushyorAI" nomli vazifa yaratadi: Windows'ga kirganingizda oynasiz (pythonw.exe) ishga tushadi;
#   * run.py --supervise rejimida: dastur to'xtasa yoki qotsa, o'zi qayta ishga tushiradi;
#   * jurnal: data\logs\app.log, data\logs\supervisor.log, data\logs\console.log
# O'chirish: scripts\windows\uninstall_autostart.ps1

param(
    [string]$TaskName = "HushyorAI"
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$pyw = Join-Path $root ".venv\Scripts\pythonw.exe"
$runpy = Join-Path $root "run.py"

if (-not (Test-Path $pyw)) {
    Write-Host "[XATO] $pyw topilmadi. Avval start_windows.bat ni bir marta ishga tushiring." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $runpy)) {
    Write-Host "[XATO] $runpy topilmadi. Skriptni loyiha papkasidan ishga tushiring." -ForegroundColor Red
    exit 1
}

$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute $pyw -Argument "run.py --supervise --no-browser" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -RestartCount 99 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "Hushyor AI - do'kon kameralari uchun AI (avtomatik start)" -Force | Out-Null

# Hozir qo'lda ishlayotgan nusxa bo'lsa, ikkita dastur bitta portda ishlay olmaydi
$running = Get-CimInstance Win32_Process -Filter "Name like 'python%.exe'" |
    Where-Object { $_.CommandLine -like "*run.py*" -and $_.CommandLine -like "*$root*" }
if ($running) {
    Write-Host "Diqqat: Hushyor AI hozir qo'lda ishlab turibdi. Uni to'xtating (Ctrl+C), keyin" -ForegroundColor Yellow
    Write-Host "         Start-ScheduledTask -TaskName $TaskName  buyrug'i bilan avtomatik nusxani yoqing." -ForegroundColor Yellow
} else {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Ishga tushirildi." -ForegroundColor Green
}

Write-Host ""
Write-Host "Tayyor: '$TaskName' vazifasi yaratildi." -ForegroundColor Green
Write-Host "  * Har safar Windows'ga kirganingizda Hushyor AI o'zi ishga tushadi (oynasiz)."
Write-Host "  * Dastur to'xtasa yoki qotsa, nazoratchi uni qayta ishga tushiradi."
$port = "8000"
$envFile = Join-Path $root ".env"
if ($env:PORT -match '^\s*(\d+)\s*$') { $port = $Matches[1] }
elseif (Test-Path $envFile) {
    $l = Select-String -Path $envFile -Pattern '^\s*(export\s+)?PORT\s*=\s*["'']?\s*(\d+)' | Select-Object -First 1
    if ($l) { $port = $l.Matches[0].Groups[2].Value }
}
Write-Host "  * Web panel: http://localhost:$port  (30-60 soniyadan keyin ochiladi)"
Write-Host "  * Jurnal:   $root\data\logs\"
Write-Host "  * Holatni ko'rish:  Get-ScheduledTask -TaskName $TaskName"
Write-Host "  * O'chirish:        powershell -ExecutionPolicy Bypass -File scripts\windows\uninstall_autostart.ps1"
