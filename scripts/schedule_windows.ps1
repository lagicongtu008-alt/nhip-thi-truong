# Tạo 2 tác vụ Task Scheduler chạy bản tin lúc 6:45 và 16:45 (theo giờ máy – hãy đặt múi giờ UTC+7).
# Chạy trong PowerShell tại thư mục gốc dự án:  powershell -ExecutionPolicy Bypass -File scripts\schedule_windows.ps1
$root = (Resolve-Path "$PSScriptRoot\..").Path
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
$action = New-ScheduledTaskAction -Execute $py -Argument "scripts\collect.py" -WorkingDirectory $root
foreach ($t in @(@{n="NhipThiTruong-Sang"; h="06:45"}, @{n="NhipThiTruong-Chieu"; h="16:45"})) {
  $trigger = New-ScheduledTaskTrigger -Daily -At $t.h
  $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)
  Register-ScheduledTask -TaskName $t.n -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
  Write-Host "Da tao tac vu $($t.n) luc $($t.h)"
}
