# Run this elevated (right-click → Run as Administrator) to install the watchdog task.
# Or: Start-Process powershell -Verb RunAs -ArgumentList "-File register_watchdog.ps1"

$action = New-ScheduledTaskAction `
    -Execute  "powershell.exe" `
    -Argument "-NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"D:\Aaron\development\SkippyTel\watchdog.ps1`""

# Repeat every 5 minutes, start now, run indefinitely
$trigger  = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 5) -Once -At (Get-Date)

$principal = New-ScheduledTaskPrincipal `
    -UserId   "SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances  IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 3) `
    -RestartCount       2 `
    -RestartInterval    (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable

Unregister-ScheduledTask -TaskName "SkippyTelWatchdog" -Confirm:$false -ErrorAction SilentlyContinue

Register-ScheduledTask `
    -TaskName    "SkippyTelWatchdog" `
    -Action      $action `
    -Trigger     $trigger `
    -Principal   $principal `
    -Settings    $settings `
    -Description "Checks SkippyTel /health every 5 min; runs docker compose up -d if unhealthy"

Write-Host "Watchdog task registered."
Get-ScheduledTask -TaskName "SkippyTelWatchdog" | Get-ScheduledTaskInfo | Select-Object TaskName, LastRunTime, NextRunTime

# Kick it off immediately
Start-ScheduledTask -TaskName "SkippyTelWatchdog"
Write-Host "Task started — check D:\Aaron\development\SkippyTel\watchdog.log"
