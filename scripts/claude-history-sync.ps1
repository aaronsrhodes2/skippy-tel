# Copy this PC's Claude Code history into Skippy Memory on Google Drive.
# One-way (PC -> Drive), newer files only, so it never fights with Drive sync
# or with sessions running on other machines.
#
#   powershell -File scripts\claude-history-sync.ps1            # sync now
#   powershell -File scripts\claude-history-sync.ps1 -Install   # sync every hour (Task Scheduler)
#   powershell -File scripts\claude-history-sync.ps1 -Uninstall
#
# Lands in <Skippy Memory>\claude-code\<COMPUTERNAME>\, which
# `python -m memory inbox` turns into searchable Markdown (docs/memory/README.md).
param([switch]$Install, [switch]$Uninstall)

$TaskName = "Skippy Claude history sync"
$Memory   = if ($env:SKIPPY_MEMORY_DIR) { $env:SKIPPY_MEMORY_DIR } else { "G:\My Drive\Skippy Memory" }
$Source   = Join-Path $env:USERPROFILE ".claude\projects"
$Dest     = Join-Path $Memory "claude-code\$env:COMPUTERNAME"

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed '$TaskName'."
    exit 0
}

if ($Install) {
    $action  = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours 1)
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Description "Copies ~/.claude/projects into Skippy Memory on Google Drive" -Force | Out-Null
    Write-Host "Installed '$TaskName' (hourly). Running it once now..."
}

if (-not (Test-Path $Source)) { Write-Host "No Claude Code history at $Source yet."; exit 0 }
if (-not (Test-Path $Memory)) { Write-Error "Skippy Memory folder not found: $Memory (is Google Drive running?)"; exit 1 }

# /E subfolders, /XO skip files older than the copy, /R:1 /W:1 don't hang on a locked live session.
robocopy $Source $Dest *.jsonl /E /XO /R:1 /W:1 /NP /NFL /NDL /NJH /NJS | Out-Null
if ($LASTEXITCODE -ge 8) { Write-Error "robocopy failed ($LASTEXITCODE)"; exit 1 }
Write-Host "Synced $Source -> $Dest"
exit 0
