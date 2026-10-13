# Hushyor AI: avtomatik startni o'chirish va ishlab turgan nusxani to'xtatish.
#
# Ishlatish (loyiha papkasida):
#   powershell -ExecutionPolicy Bypass -File scripts\windows\uninstall_autostart.ps1

param(
    [string]$TaskName = "HushyorAI"
)

$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "'$TaskName' vazifasi o'chirildi." -ForegroundColor Green
} else {
    Write-Host "'$TaskName' vazifasi topilmadi." -ForegroundColor Yellow
}

# Shu papkadan ishga tushgan Hushyor AI jarayonlarini to'xtatish (nazoratchi va dastur)
$procs = Get-CimInstance Win32_Process -Filter "Name like 'python%.exe'" |
    Where-Object { $_.CommandLine -like "*run.py*" -and $_.CommandLine -like "*$root*" }
foreach ($p in $procs) {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Host "To'xtatildi: jarayon $($p.ProcessId)"
}
if (-not $procs) {
    Write-Host "Ishlab turgan Hushyor AI jarayoni topilmadi."
}
