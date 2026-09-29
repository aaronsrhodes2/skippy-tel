# SkippyTel Watchdog
# Runs every 5 minutes via Task Scheduler.
# Checks /health — if unhealthy, diagnoses the failure mode and recovers:
#
#   Mode A — Docker Desktop not running at all
#            → clean stale sockets → start Docker Desktop → compose up
#
#   Mode B — Docker Desktop running but engine returning 500 / pipe missing
#            (stale AF-UNIX socket files blocking dockerd startup)
#            → kill Docker Desktop → clean stale sockets → start → compose up
#
#   Mode C — Docker Desktop + engine healthy but containers down
#            → docker compose up -d

$logFile      = "D:\Aaron\development\SkippyTel\watchdog.log"
$maxLines     = 500
$dockerExe    = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
$composeFile  = "D:\Aaron\development\SkippyTel\docker-compose.yml"

# Directories that Docker Desktop creates Unix sockets in.
# When Docker Desktop crashes these are left locked by the Windows kernel.
# Renaming them (not deleting — they're kernel-locked) lets Docker Desktop
# recreate them fresh on next launch.
$socketDirs = @(
    "C:\Users\aaron\AppData\Local\Docker\run",
    "C:\Users\aaron\AppData\Local\docker-secrets-engine"
)

function Write-Log {
    param([string]$msg)
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg"
    Write-Host $line
    Add-Content -Path $logFile -Value $line -Encoding utf8
    $lines = Get-Content $logFile -ErrorAction SilentlyContinue
    if ($lines -and $lines.Count -gt $maxLines) {
        $lines | Select-Object -Last $maxLines | Set-Content $logFile -Encoding utf8
    }
}

# Returns $true if `docker ps` succeeds (engine is responding).
function Test-DockerEngine {
    $r = docker ps 2>&1
    return ($LASTEXITCODE -eq 0)
}

# Renames stale socket directories so Docker Desktop can create fresh ones.
# Uses Rename-Item because del/takeown cannot touch AF-UNIX socket files —
# they are kernel-locked and only become deletable after Windows GC runs.
# The .bak dirs accumulate but are tiny (empty socket files); a future boot
# cleans them when Docker recreates the paths from scratch.
function Clear-StaleSocketDirs {
    foreach ($dir in $socketDirs) {
        if (Test-Path $dir) {
            $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
            $dest  = "${dir}.${stamp}.bak"
            try {
                Rename-Item $dir $dest -ErrorAction Stop
                Write-Log "INFO  renamed stale socket dir: $dir → $dest"
            } catch {
                Write-Log "WARN  could not rename $dir : $($_.Exception.Message)"
            }
        }
    }
}

# Kills Docker Desktop and all related processes cleanly.
function Stop-DockerDesktop {
    Write-Log "INFO  stopping Docker Desktop processes"
    @("Docker Desktop", "com.docker.backend", "dockerd") | ForEach-Object {
        Stop-Process -Name $_ -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 4
    # Shutdown WSL so docker-desktop distro releases any file handles
    wsl --shutdown 2>&1 | Out-Null
    Start-Sleep -Seconds 3
}

# Starts Docker Desktop and waits up to $timeoutSec for the engine to respond.
function Start-DockerDesktop {
    param([int]$timeoutSec = 90)
    if (-not (Test-Path $dockerExe)) {
        Write-Log "ERROR Docker Desktop exe not found at $dockerExe"
        return $false
    }
    Start-Process $dockerExe
    Write-Log "INFO  Docker Desktop launched — waiting up to ${timeoutSec}s for engine"
    $elapsed = 0
    while ($elapsed -lt $timeoutSec) {
        Start-Sleep -Seconds 10
        $elapsed += 10
        if (Test-DockerEngine) {
            Write-Log "INFO  engine ready after ${elapsed}s"
            return $true
        }
    }
    Write-Log "ERROR engine not ready after ${timeoutSec}s"
    return $false
}

# ── 1. Check health ───────────────────────────────────────────────────────────
try {
    $resp = Invoke-RestMethod -Uri "http://localhost:3003/health" -TimeoutSec 8 -ErrorAction Stop
    if ($resp.status -eq "ok") {
        exit 0   # healthy — silent exit, no log noise
    }
    Write-Log "WARN  health status=$($resp.status) — recovering"
} catch {
    Write-Log "WARN  health unreachable: $($_.Exception.Message) — recovering"
}

# ── 2. Diagnose Docker state ──────────────────────────────────────────────────
$ddRunning    = [bool](Get-Process "Docker Desktop" -ErrorAction SilentlyContinue)
$engineOk     = Test-DockerEngine

Write-Log "INFO  Docker Desktop running=$ddRunning  engine-ok=$engineOk"

if ($ddRunning -and $engineOk) {
    # Mode C — engine fine, just containers down
    Write-Log "INFO  Mode C: engine healthy, containers down — compose up only"
} elseif ($ddRunning -and -not $engineOk) {
    # Mode B — Docker Desktop process alive but engine broken (stale sockets)
    Write-Log "INFO  Mode B: engine unhealthy — kill → clean sockets → restart"
    Stop-DockerDesktop
    Clear-StaleSocketDirs
    $started = Start-DockerDesktop -timeoutSec 90
    if (-not $started) {
        Write-Log "ERROR could not recover Docker Desktop — giving up this cycle"
        exit 1
    }
} else {
    # Mode A — Docker Desktop not running at all
    Write-Log "INFO  Mode A: Docker Desktop not running — clean sockets → start"
    Clear-StaleSocketDirs
    $started = Start-DockerDesktop -timeoutSec 90
    if (-not $started) {
        Write-Log "ERROR Docker Desktop did not start — giving up this cycle"
        exit 1
    }
}

# ── 3. Bring services up ──────────────────────────────────────────────────────
Write-Log "INFO  docker compose up -d"
$result = & docker compose -f $composeFile up -d 2>&1
Write-Log "INFO  compose: $($result -join '; ')"

# ── 4. Verify recovery ────────────────────────────────────────────────────────
Start-Sleep -Seconds 15
try {
    $resp2 = Invoke-RestMethod -Uri "http://localhost:3003/health" -TimeoutSec 8 -ErrorAction Stop
    if ($resp2.status -eq "ok") {
        Write-Log "OK    SkippyTel recovered"
    } else {
        Write-Log "ERROR still unhealthy after recovery: status=$($resp2.status)"
    }
} catch {
    Write-Log "ERROR still unreachable after recovery: $($_.Exception.Message)"
}
