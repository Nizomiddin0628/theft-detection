# Hushyor AI BULUT serverini shu kompyuterda Windows'ga kirganda avtomatik ishga tushirish (Task Scheduler).
# (VPS bo'lmasa - bulut egasining kompyuterida, SQLite bilan. VPS uchun: deploy/install_vps.sh, docs/DEPLOY.md)
#
# Ishlatish (loyiha papkasida):
#   powershell -ExecutionPolicy Bypass -File scripts\windows\install_cloud_autostart.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\windows\install_cloud_autostart.ps1 -BindHost 127.0.0.1
#
# -BindHost 0.0.0.0 (sukut) - panel shu tarmoqdagi telefon/kompyuterlardan ham ochiladi (http://<kompyuter-IP>:9000);
#           127.0.0.1 - faqat shu kompyuterdan. Jurnal: cloud\data\logs\cloud.log
# O'chirish: scripts\windows\install_cloud_autostart.ps1 -Remove

param(
    [string]$TaskName = "HushyorCloud",
    [string]$BindHost = "0.0.0.0",
    [int]$Port = 9000,
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "'$TaskName' vazifasi o'chirildi." -ForegroundColor Green
    }
    Get-CimInstance Win32_Process -Filter "Name like 'python%.exe'" |
        Where-Object { $_.CommandLine -like "*cloud.server*" -and $_.CommandLine -like "*--port $Port*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    exit 0
}

$pyw = Join-Path $root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pyw)) {
    Write-Host "[XATO] $pyw topilmadi. Avval install_windows.bat (yoki start_windows.bat) ni ishga tushiring." -ForegroundColor Red
    exit 1
}

$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute $pyw -Argument "-m cloud.server --host $BindHost --port $Port" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -RestartCount 99 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "Hushyor AI bulut serveri (panel, Sync API, bildirishnomalar)" -Force | Out-Null

$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Write-Host "Diqqat: $Port-port band (bulut qo'lda ishlab turgan bo'lishi mumkin). Uni yoping, keyin:" -ForegroundColor Yellow
    Write-Host "         Start-ScheduledTask -TaskName $TaskName" -ForegroundColor Yellow
} else {
    Start-ScheduledTask -TaskName $TaskName
}
if ($BindHost -ne "127.0.0.1") {
    # Windows brauzer ruxsati (Defender Firewall): faqat lokal tarmoq (Private) profilida
    $rule = "Hushyor AI bulut $Port"
    if (-not (Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue)) {
        try {
            New-NetFirewallRule -DisplayName $rule -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow `
                -Profile Private | Out-Null
        } catch {
            Write-Host "Eslatma: tarmoq ruxsatini qo'shish uchun PowerShell'ni administrator sifatida oching (ixtiyoriy)." -ForegroundColor Yellow
        }
    }
}
Write-Host ""
Write-Host "Tayyor: '$TaskName' - bulut serveri Windows'ga kirganda o'zi ishga tushadi." -ForegroundColor Green
Write-Host "  * Panel: http://127.0.0.1:$Port/"
Write-Host "  * Jurnal: $root\cloud\data\logs\cloud.log"
Write-Host "  * Zaxira nusxa: har kuni $root\cloud\data\backups\ (oxirgi 7 tasi)"
Write-Host "  * O'chirish: powershell -ExecutionPolicy Bypass -File scripts\windows\install_cloud_autostart.ps1 -Remove"
