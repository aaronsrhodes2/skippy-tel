<#
.SYNOPSIS
    Make the gaming PC a remote-play host you can reach from anywhere
    (Sunshine for Moonlight, Steam Remote Play as a backup), with no trip
    home to type a PIN.

.DESCRIPTION
    Default is REPORT ONLY: it prints what is and isn't ready.
    Run with -Apply (from an elevated PowerShell) to fix what it can:
      - AC sleep + hibernate → never
      - Windows Update active hours → 08:00–02:00, so updates don't reboot you mid-session
      - install Sunshine (winget), set its service to start automatically
    Things it can only report (you do them once by hand; see docs/streaming.md):
      Tailscale "Run unattended", auto-login, Steam on startup, BIOS power-on after
      AC loss, Remote Desktop as a rescue path.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\windows\streaming-host.ps1
    powershell -ExecutionPolicy Bypass -File scripts\windows\streaming-host.ps1 -Apply
#>
param([switch]$Apply)

$ErrorActionPreference = 'Stop'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($Apply -and -not $isAdmin) { throw 'Run -Apply from an elevated (Administrator) PowerShell.' }

$script:problems = 0
function Report([string]$name, [bool]$ok, [string]$hint = '') {
    if ($ok) { Write-Host ("  [ok]   {0}" -f $name) -ForegroundColor Green }
    else {
        $script:problems++
        Write-Host ("  [todo] {0}" -f $name) -ForegroundColor Yellow
        if ($hint) { Write-Host ("         {0}" -f $hint) }
    }
}

function Get-PowerIndex([string]$sub, [string]$setting) {
    $out = powercfg /query SCHEME_CURRENT $sub $setting | Out-String
    if ($out -match 'Current AC Power Setting Index:\s+0x([0-9a-fA-F]+)') { return [Convert]::ToInt32($Matches[1], 16) }
    return -1
}

Write-Host "`nPower" -ForegroundColor Cyan
if ($Apply) {
    powercfg /change standby-timeout-ac 0 | Out-Null
    powercfg /change hibernate-timeout-ac 0 | Out-Null
}
Report 'Never sleep on AC power' ((Get-PowerIndex SUB_SLEEP STANDBYIDLE) -eq 0) 'run with -Apply'
Report 'Never hibernate on AC power' ((Get-PowerIndex SUB_SLEEP HIBERNATEIDLE) -eq 0) 'run with -Apply'
Write-Host '         (also set BIOS: "Restore on AC power loss" = Power On. The script cannot check that.)'

Write-Host "`nWindows Update" -ForegroundColor Cyan
$wu = 'HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings'
if ($Apply) {
    New-Item -Path $wu -Force | Out-Null
    Set-ItemProperty -Path $wu -Name ActiveHoursStart -Value 8 -Type DWord
    Set-ItemProperty -Path $wu -Name ActiveHoursEnd -Value 2 -Type DWord
    Set-ItemProperty -Path $wu -Name SmartActiveHoursState -Value 0 -Type DWord
}
$hours = Get-ItemProperty -Path $wu -ErrorAction SilentlyContinue
Report 'Active hours 08:00-02:00 (no surprise reboots while you play)' `
    ($hours -and $hours.ActiveHoursStart -eq 8 -and $hours.ActiveHoursEnd -eq 2) 'run with -Apply'

Write-Host "`nSunshine (stream server for Moonlight)" -ForegroundColor Cyan
$sunshine = Get-Service -ErrorAction SilentlyContinue | Where-Object { $_.Name -like 'Sunshine*' } | Select-Object -First 1
if (-not $sunshine -and $Apply) {
    winget install --id LizardByte.Sunshine -e --accept-source-agreements --accept-package-agreements
    $sunshine = Get-Service -ErrorAction SilentlyContinue | Where-Object { $_.Name -like 'Sunshine*' } | Select-Object -First 1
}
Report 'Sunshine installed' ([bool]$sunshine) 'run with -Apply (installs LizardByte.Sunshine via winget)'
if ($sunshine) {
    if ($Apply) {
        Set-Service -Name $sunshine.Name -StartupType Automatic
        if ($sunshine.Status -ne 'Running') { Start-Service -Name $sunshine.Name }
        $sunshine.Refresh()
    }
    Report 'Sunshine service starts automatically' ($sunshine.StartType -eq 'Automatic') 'run with -Apply'
    Report 'Sunshine service running' ($sunshine.Status -eq 'Running') 'run with -Apply'
    Write-Host '         Web UI: https://localhost:47990 (set a username/password on first visit)'
}

Write-Host "`nTailscale (how the MacBook reaches this PC from anywhere)" -ForegroundColor Cyan
$ts = Get-Service -Name Tailscale -ErrorAction SilentlyContinue
Report 'Tailscale service running' ($ts -and $ts.Status -eq 'Running') 'install Tailscale and sign in'
Write-Host '         Tray icon > Preferences > "Run unattended" must be ON, so it stays up when logged out.'
$tsExe = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
if (Test-Path $tsExe) {
    $ip = (& $tsExe ip -4 2>$null | Select-Object -First 1)
    Report "Tailscale IP: $ip" ([bool]$ip) 'tailscale up'
}

Write-Host "`nSteam (backup path: Steam Remote Play, same account, no PIN)" -ForegroundColor Cyan
$run = Get-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -ErrorAction SilentlyContinue
Report 'Steam starts with Windows' ($run -and $run.PSObject.Properties.Name -contains 'Steam') `
    'Steam > Settings > Interface > "Run Steam when my computer starts"'

$winlogon = Get-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon' -ErrorAction SilentlyContinue
Report 'Auto-login after reboot (Steam needs a logged-in session)' ($winlogon -and $winlogon.AutoAdminLogon -eq '1') `
    'use Sysinternals Autologon (stores the password encrypted): https://learn.microsoft.com/sysinternals/downloads/autologon'

Write-Host "`nRescue path" -ForegroundColor Cyan
$rdp = Get-ItemProperty -Path 'HKLM:\System\CurrentControlSet\Control\Terminal Server' -ErrorAction SilentlyContinue
Report 'Remote Desktop enabled (reach it over Tailscale to fix a stuck PC)' ($rdp -and $rdp.fDenyTSConnections -eq 0) `
    'Settings > System > Remote Desktop > On (Windows Pro). Never port-forward 3389. Use Tailscale only.'

Write-Host ''
if ($script:problems -eq 0) { Write-Host 'Ready to stream from anywhere.' -ForegroundColor Green }
else { Write-Host ("{0} item(s) to do. See docs/streaming.md." -f $script:problems) -ForegroundColor Yellow }
