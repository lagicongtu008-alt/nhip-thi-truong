# Đăng ký lịch chạy tự động trong Windows Task Scheduler.
# Dùng:  powershell -ExecutionPolicy Bypass -File cai-lich.ps1                 (mặc định 07:30 và 17:30 hằng ngày)
#        powershell -ExecutionPolicy Bypass -File cai-lich.ps1 -Gio "08:00","12:00","18:00"
#        powershell -ExecutionPolicy Bypass -File cai-lich.ps1 -Go              (gỡ lịch)
param(
    [string[]]$Gio = @("07:30", "17:30"),
    [switch]$Go
)

$TaskName = "BanTinKinhTeTaiChinh"

if ($Go) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Đã gỡ lịch '$TaskName'."
    return
}

$bat = Join-Path $PSScriptRoot "chay.bat"
$action = New-ScheduledTaskAction -Execute $bat -WorkingDirectory $PSScriptRoot
$triggers = $Gio | ForEach-Object { New-ScheduledTaskTrigger -Daily -At $_ }
# StartWhenAvailable: nếu máy tắt đúng giờ chạy, sẽ chạy bù khi bật lại
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Settings $settings `
    -Description "Thu thập và phân tích tin tức kinh tế - tài chính" -Force | Out-Null

Write-Host "Đã đặt lịch '$TaskName' chạy hằng ngày lúc: $($Gio -join ', ')"
Write-Host "Báo cáo mới nhất: $(Join-Path $PSScriptRoot 'reports\moi-nhat.html')"
