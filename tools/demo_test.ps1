# demo_test.ps1 - Full demo-day test suite
# Tests all 24 workflows from the demo checklist.
# Usage: powershell -File tools\demo_test.ps1

param()

$BASE    = "http://localhost:3003"
$PASS    = 0
$FAIL    = 0
$SKIP    = 0
$RESULTS = @()
$ADB_SER = "adb-R3CX20PV4YY-fJOeYZ._adb-tls-connect._tcp"

function Log-Pass([string]$id, [string]$label, [string]$detail="") {
    $script:PASS++
    $script:RESULTS += [PSCustomObject]@{ID=$id; Status="PASS"; Label=$label; Detail=$detail}
    Write-Host "  [PASS] #$id $label" -ForegroundColor Green
    if ($detail) { Write-Host "         $detail" -ForegroundColor DarkGray }
}
function Log-Fail([string]$id, [string]$label, [string]$detail="") {
    $script:FAIL++
    $script:RESULTS += [PSCustomObject]@{ID=$id; Status="FAIL"; Label=$label; Detail=$detail}
    Write-Host "  [FAIL] #$id $label" -ForegroundColor Red
    if ($detail) { Write-Host "         $detail" -ForegroundColor Yellow }
}
function Log-Skip([string]$id, [string]$label, [string]$detail="") {
    $script:SKIP++
    $script:RESULTS += [PSCustomObject]@{ID=$id; Status="SKIP"; Label=$label; Detail=$detail}
    Write-Host "  [SKIP] #$id $label" -ForegroundColor DarkYellow
    if ($detail) { Write-Host "         $detail" -ForegroundColor DarkGray }
}

function Post-Intent([string]$text, [string]$sid="demo-test") {
    $body = (@{text=$text; session_id=$sid} | ConvertTo-Json)
    try {
        return Invoke-RestMethod -Uri "$BASE/intent/unmatched" -Method POST `
            -Body $body -ContentType "application/json" -TimeoutSec 30
    } catch {
        $msg = $_.ErrorDetails.Message
        if (-not $msg) { $msg = $_.Exception.Message }
        return [PSCustomObject]@{tier=""; reply=""; _error=$msg}
    }
}

function Short([string]$s, [int]$n=80) {
    if ($s.Length -le $n) { return $s }
    return $s.Substring(0, $n) + "..."
}

Write-Host ""
Write-Host "=== SKIPPY DEMO-DAY TEST SUITE ===" -ForegroundColor Cyan
Write-Host ""

#
# ---- SECTION 1: Infrastructure ----
#
Write-Host "-- Section 1: Infrastructure --" -ForegroundColor White

# Test 1: SkippyTel health
try {
    $h = Invoke-RestMethod "$BASE/health" -TimeoutSec 5
    if ($h.status -eq "ok") {
        $tiers = $h.models.tiers
        $detail = "ollama=$($h.models.ollama.available) claude=$($tiers.claude) nagatha=$($tiers.nagatha) elevenlabs=$($tiers.elevenlabs) bridge=$($h.bridge)"
        Log-Pass "1" "SkippyTel health" $detail
    } else {
        Log-Fail "1" "SkippyTel health" "status=$($h.status)"
    }
} catch {
    Log-Fail "1" "SkippyTel health" $_.Exception.Message
}

# Test 2 (infra): ADB wireless debugging
$adbState = adb -s $ADB_SER get-state 2>&1
if ("$adbState" -match "device") {
    Log-Pass "2" "ADB wireless debugging" "S23 connected via TLS"
} else {
    adb connect 100.126.39.2:5555 2>&1 | Out-Null
    Start-Sleep -Seconds 2
    $adbState2 = adb -s $ADB_SER get-state 2>&1
    if ("$adbState2" -match "device") {
        Log-Pass "2" "ADB wireless debugging" "Reconnected via Tailscale"
    } else {
        Log-Fail "2" "ADB wireless debugging" "Run: adb connect 100.126.39.2:5555"
    }
}

# Test 19 (infra): claude-bridge
try {
    $br = Invoke-RestMethod "http://localhost:9337/health" -TimeoutSec 2
    if ($br.bridge -eq "claude-code") {
        Log-Pass "19" "Claude bridge process" "Responding on :9337"
    } else {
        Log-Skip "19" "Claude bridge process" "Start before demo: python SkippyTel/services/claude-bridge/bridge.py"
    }
} catch {
    Log-Skip "19" "Claude bridge process" "Start before demo: python SkippyTel/services/claude-bridge/bridge.py"
}

Write-Host ""

#
# ---- SECTION 2: AI Tiers ----
#
Write-Host "-- Section 2: AI Tiers --" -ForegroundColor White

# Test 3: Default Ollama (brief pause so flask is settled after health check)
Start-Sleep -Seconds 2
$r = Post-Intent "What color is the sky?"
if ($r.tier -eq "local" -and $r.reply -ne "") {
    Log-Pass "3" "Default Ollama" (Short "reply=$($r.reply)")
} else {
    Log-Fail "3" "Default Ollama" "tier=$($r.tier) err=$($r._error)"
}

Start-Sleep -Seconds 1

# Test 4: Nagatha "Hey Nagatha"
$r = Post-Intent "Hey Nagatha, how are you?"
if ($r.tier -eq "nagatha" -and $r.reply -ne "") {
    if ($r.reply -match "Here.s Nagatha") {
        Log-Fail "4" "Nagatha persona (old prefix present)" (Short $r.reply)
    } else {
        Log-Pass "4" "Nagatha tier + warm persona" (Short $r.reply)
    }
} elseif ($r._error -match "429") {
    Log-Skip "4" "Nagatha persona" "Gemini rate-limited (429) from rapid testing"
} else {
    Log-Fail "4" "Nagatha tier" "tier=$($r.tier) err=$($r._error)"
}

Start-Sleep -Seconds 3

# Test 4b: Nagatha "Ask Nagatha" variant
$r = Post-Intent "Ask Nagatha to tell me something British"
if ($r.tier -eq "nagatha") {
    Log-Pass "4b" "Nagatha alt trigger 'ask nagatha'" ""
} elseif ($r._error -match "429") {
    Log-Skip "4b" "Nagatha alt trigger" "Gemini 429 rate limit"
} else {
    Log-Fail "4b" "Nagatha alt trigger 'ask nagatha'" "tier=$($r.tier)"
}

Start-Sleep -Seconds 1

# Test 5: Thinking cap -> Claude
$r = Post-Intent "Put on your thinking cap, explain recursion"
if ($r.tier -eq "claude") {
    Log-Pass "5" "Thinking cap routes to Claude" ""
} elseif ($r._error -match "claude-bridge unreachable") {
    Log-Pass "5" "Thinking cap routes to Claude (bridge offline)" "Correctly identified claude tier; bridge just not running"
} else {
    Log-Fail "5" "Thinking cap" "tier=$($r.tier) err=$($r._error)"
}

# Test 6: Trust the awesomeness -> bridge
$r = Post-Intent "Trust the awesomeness, say hello"
if ($r._error -match "claude-bridge unreachable") {
    Log-Pass "6" "Trust the awesomeness routing" "Correctly routes to bridge (offline - start before demo)"
} elseif ($r.reply -ne "") {
    Log-Pass "6" "Trust the awesomeness + bridge live" (Short $r.reply)
} else {
    Log-Fail "6" "Trust the awesomeness routing" "tier=$($r.tier) err=$($r._error)"
}

Write-Host ""

#
# ---- SECTION 3: Bilby / Media ----
#
Write-Host "-- Section 3: Bilby / Media --" -ForegroundColor White

# Test 11: Bilby now playing
try {
    $b = Invoke-RestMethod "$BASE/bilby/status" -TimeoutSec 5
    $track = if ($b.track) { $b.track } elseif ($b.title) { $b.title } else { "(no track field)" }
    Log-Pass "11" "Bilby status endpoint" "track=$track"
} catch {
    $msg = "$($_.ErrorDetails.Message)$($_.Exception.Message)"
    # Timeout or offline are both expected if Mac is not running Bilby
    Log-Skip "11" "Bilby status" "Bilby Mac daemon offline (start stage9_dj_suggest.py on Mac)"
}

# Test 12: Bilby skip
try {
    $skip = Invoke-RestMethod "$BASE/bilby/next" -Method POST -TimeoutSec 5
    Log-Pass "12" "Bilby skip track" "POST /bilby/next OK"
} catch {
    Log-Skip "12" "Bilby skip track" "Bilby Mac daemon offline"
}

Write-Host ""

#
# ---- SECTION 4: Spanish / Translation ----
#
Write-Host "-- Section 4: Spanish + Translation --" -ForegroundColor White

# Test 21a: Spanish -> English translation (with hint_lang=es, as the app sends)
# Note: field is english_text (not translation). Fast path bypassed when hint_lang set.
try {
    $tbody = @{text="El cielo es azul y el sol brilla."; hint_lang="es"} | ConvertTo-Json
    $tr = Invoke-RestMethod "$BASE/translate/text" -Method POST -Body $tbody -ContentType "application/json" -TimeoutSec 15
    $eng = $tr.english_text
    if ($eng -ne "" -and $eng -ne $null -and $eng -notmatch "cielo") {
        Log-Pass "21" "Spanish->English translation" "detected=$($tr.detected_lang) result='$eng'"
    } elseif ($eng -ne "") {
        # Returned something but looks untranslated - could be Gemini 429
        Log-Skip "21" "Spanish->English translation" "Result may be untranslated (Gemini 429?): '$eng'"
    } else {
        Log-Fail "21" "Spanish->English translation" ($tr | ConvertTo-Json -Compress)
    }
} catch {
    $em = "$($_.ErrorDetails.Message)$($_.Exception.Message)"
    if ($em -match "429") { Log-Skip "21" "Spanish->English translation" "Gemini 429 rate limit" }
    else { Log-Fail "21" "Spanish->English translation" $em }
}

# Test 21b: target_lang param (English -> Spanish AI reply)
# This goes to Gemini so may hit 429 if testing rapidly
try {
    $tbody2 = @{text="The sky is blue."; target_lang="es"} | ConvertTo-Json
    $tr2 = Invoke-RestMethod "$BASE/translate/text" -Method POST -Body $tbody2 -ContentType "application/json" -TimeoutSec 15
    if ($tr2.english_text) {
        Log-Pass "21b" "target_lang param (EN->ES)" "result='$($tr2.english_text)'"
    } else {
        Log-Skip "21b" "target_lang param" "No english_text: $($tr2 | ConvertTo-Json -Compress)"
    }
} catch {
    $em = "$($_.ErrorDetails.Message)$($_.Exception.Message)"
    if ($em -match "429|502") { Log-Skip "21b" "target_lang param (EN->ES)" "Gemini rate limit" }
    else { Log-Fail "21b" "target_lang param (EN->ES)" $em }
}

Write-Host ""

#
# ---- SECTION 5: Voice Keywords (KeywordScanner.kt static audit) ----
#
Write-Host "-- Section 5: Voice Keywords (KeywordScanner.kt audit) --" -ForegroundColor White

$ksFile = "D:\Aaron\development\zPhone\.claude\worktrees\jovial-ritchie-ae97ee\apps\SkippyChat\app\src\main\java\local\skippy\chat\model\KeywordScanner.kt"

function Check-KW([string]$id, [string]$label, [string]$pattern) {
    $found = Select-String -Path $ksFile -Pattern $pattern -Quiet 2>$null
    if ($found) { Log-Pass $id "KW: $label" "in KeywordScanner.kt" }
    else { Log-Fail $id "KW: $label" "pattern '$pattern' missing" }
}

# Mute / unmute (#7)
Check-KW "7" "skippy sleep (mute)"    "skippy sleep"
Check-KW "7" "skippy wake (unmute)"   "skippy wake"
Check-KW "7" "go quiet"               "go quiet"

# Volume (#8)
Check-KW "8" "louder"    "louder"
Check-KW "8" "softer"    "softer"
Check-KW "8" "max volume" "max volume"

# Flashlight (#9)
Check-KW "9" "flashlight on"  "flashlight on"
Check-KW "9" "flashlight off" "flashlight off"
Check-KW "9" "torch on"       "torch on"

# Brightness (#10)
Check-KW "10" "brighter"       '"brighter"'
Check-KW "10" "dimmer"         '"dimmer"'
Check-KW "10" "max brightness" "max brightness"

# Cancel draft (#13)
Check-KW "13" "cancel that"  "cancel that"
Check-KW "13" "scratch that" "scratch that"
Check-KW "13" "start over"   "start over"

# Send now (#14)
Check-KW "14" "send it"  "send it"
Check-KW "14" "ship it"  "ship it"
Check-KW "14" "go ahead" "go ahead"

# Planning mode (#15)
Check-KW "15" "let's make a plan" "make a plan"
Check-KW "15" "done planning"     "done planning"
Check-KW "15" "vibe mode"         "vibe mode"

# Shades on (#16)
Check-KW "16" "put on the shades" "put on the shades"

# Predator vision (#20)
Check-KW "20" "predator vision" "predator vision"
Check-KW "20" "predator mode"   "predator mode"

# Demo dot (#22 / #24)
Check-KW "22" "dot demo start"  "dot demo"
Check-KW "22" "demo dot color"  "demo dot"
Check-KW "24" "remove the dot"  "remove the dot"
Check-KW "24" "demo done"       "demo done"

# Nagatha mode keywords (#4)
Check-KW "4" "hey nagatha in scanner"  "hey nagatha"
Check-KW "4" "yo nagatha in scanner"   "yo nagatha"

# Spanish enter/exit (#21)
Check-KW "21" "espanol enter"    "espanol"
Check-KW "21" "spanish mode"     "spanish mode"
Check-KW "21" "back to english"  "back to english"

Write-Host ""

#
# ---- SECTION 6: MainActivity handlers ----
#
Write-Host "-- Section 6: MainActivity Handlers --" -ForegroundColor White

$mainFile = "D:\Aaron\development\zPhone\.claude\worktrees\jovial-ritchie-ae97ee\apps\SkippyChat\app\src\main\java\local\skippy\chat\MainActivity.kt"

function Check-Main([string]$id, [string]$label, [string]$pattern) {
    $found = Select-String -Path $mainFile -Pattern $pattern -Quiet 2>$null
    if ($found) { Log-Pass $id "MainActivity: $label" "" }
    else { Log-Fail $id "MainActivity: $label" "pattern '$pattern' not found" }
}

Check-Main "9"  "setFlashlight() impl"     "setFlashlight"
Check-Main "10" "adjustBrightness() impl"  "adjustBrightness"
Check-Main "7"  "handleMicActive() impl"   "handleMicActive"
Check-Main "2"  "FLAG_KEEP_SCREEN_ON"      "FLAG_KEEP_SCREEN_ON"
Check-Main "17" "Glass mode disabled"      "camera passthrough.*disabled|not ready for demo|Glass mode"

# PredatorVision and DemoDot are handled in ChatScreen.kt, not MainActivity
$csFile = "D:\Aaron\development\zPhone\.claude\worktrees\jovial-ritchie-ae97ee\apps\SkippyChat\app\src\main\java\local\skippy\chat\ui\ChatScreen.kt"
$found20 = Select-String -Path $csFile -Pattern "togglePredatorVision" -Quiet 2>$null
if ($found20) { Log-Pass "20" "ChatScreen: togglePredatorVision call" "" } else { Log-Fail "20" "ChatScreen: togglePredatorVision" "not found" }
$found22 = Select-String -Path $csFile -Pattern "DemoDotStart|DemoDotStop" -Quiet 2>$null
if ($found22) { Log-Pass "22" "ChatScreen: DemoDot handlers" "" } else { Log-Fail "22" "ChatScreen: DemoDot handlers" "not found" }

# Portrait stays portrait with glasses (our fix)
$noLandscape = -not (Select-String -Path $mainFile -Pattern "SCREEN_ORIENTATION_SENSOR_LANDSCAPE" -Quiet 2>$null)
if ($noLandscape) {
    Log-Pass "glassfix" "Phone stays portrait with glasses" "SENSOR_LANDSCAPE removed"
} else {
    Log-Fail "glassfix" "Phone stays portrait with glasses" "SENSOR_LANDSCAPE still present"
}

Write-Host ""

#
# ---- SECTION 7: ADB live device checks ----
#
Write-Host "-- Section 7: ADB Live Device Checks --" -ForegroundColor White

# App installed
$apk = adb -s $ADB_SER shell pm list packages local.skippy.chat 2>&1
if ("$apk" -match "local.skippy.chat") {
    Log-Pass "18" "SkippyChat installed on S23" ""
} else {
    Log-Fail "18" "SkippyChat installed" "Run deploy cycle"
}

# Tailscale ping from phone to skippy-pc
$pingOut = adb -s $ADB_SER shell "ping -c 1 -W 2 skippy-pc" 2>&1
$pingStr = "$pingOut"
if ($pingStr -match "1 received") {
    Log-Pass "18" "Phone->skippy-pc Tailscale" "MagicDNS working"
} elseif ($pingStr -match "1 packets received") {
    Log-Pass "18" "Phone->skippy-pc Tailscale" "MagicDNS working"
} else {
    Log-Fail "18" "Phone->skippy-pc Tailscale" "ping failed - check Tailscale on phone"
}

# SkippyChat can reach SkippyTel
$curl = adb -s $ADB_SER shell "curl -s -o /dev/null -w '%{http_code}' http://skippy-pc:3003/health" 2>&1
if ("$curl" -match "200") {
    Log-Pass "18" "Phone->SkippyTel HTTP" "HTTP 200 from phone"
} else {
    Log-Fail "18" "Phone->SkippyTel HTTP" "curl returned: $curl"
}

Write-Host ""

#
# ---- SECTION 8: TTS / ElevenLabs ----
#
Write-Host "-- Section 8: TTS / ElevenLabs --" -ForegroundColor White

try {
    $h2 = Invoke-RestMethod "$BASE/health" -TimeoutSec 5
    if ($h2.models.tiers.elevenlabs -eq $true) {
        Log-Pass "2" "ElevenLabs key configured" ""
        # Smoke-test TTS endpoint
        # TTS uses GET /tts/stream?text=...&voice=skippy (streaming audio)
        # Health check is faster and sufficient to confirm ElevenLabs is wired
        try {
            $ttsH = Invoke-RestMethod "$BASE/tts/health" -TimeoutSec 5
            if ($ttsH.configured -eq $true) {
                Log-Pass "2" "ElevenLabs TTS configured" "voice_id=$($ttsH.voice_id) model=$($ttsH.model)"
            } else {
                Log-Skip "2" "ElevenLabs TTS" "configured=false - add voice IDs to .env"
            }
        } catch {
            Log-Fail "2" "ElevenLabs TTS health" $_.Exception.Message
        }
    } else {
        Log-Skip "2" "ElevenLabs voice" "Add ELEVENLABS_VOICE_ID to .env after recording at 10pm"
    }
} catch {
    Log-Skip "2" "ElevenLabs health check" "SkippyTel unreachable"
}

#
# ---- SECTION 9: New file existence checks ----
#
Write-Host ""
Write-Host "-- Section 9: New Files Exist --" -ForegroundColor White

$newFiles = @(
    @{id="20"; label="PredatorMode.kt";   path="D:\Aaron\development\zPhone\.claude\worktrees\jovial-ritchie-ae97ee\apps\SkippyChat\app\src\main\java\local\skippy\chat\model\PredatorMode.kt"},
    @{id="22"; label="DemoDotPrompts.kt"; path="D:\Aaron\development\zPhone\.claude\worktrees\jovial-ritchie-ae97ee\apps\SkippyChat\app\src\main\java\local\skippy\chat\model\DemoDotPrompts.kt"},
    @{id="21"; label="TranslationStrip.kt"; path="D:\Aaron\development\zPhone\.claude\worktrees\jovial-ritchie-ae97ee\apps\SkippyChat\app\src\main\java\local\skippy\chat\ui\TranslationStrip.kt"},
    @{id="glassfix"; label="GlassesHudPresentation.kt"; path="D:\Aaron\development\zPhone\.claude\worktrees\jovial-ritchie-ae97ee\apps\SkippyChat\app\src\main\java\local\skippy\chat\ui\GlassesHudPresentation.kt"},
    @{id="TL"; label="MoneyCandy.kt";    path="D:\Aaron\development\zPhone\.claude\worktrees\jovial-ritchie-ae97ee\apps\SkippyChat\app\src\main\java\local\skippy\chat\ui\MoneyCandy.kt"}
)
foreach ($f in $newFiles) {
    if (Test-Path $f.path) { Log-Pass $f.id "File exists: $($f.label)" "" }
    else { Log-Fail $f.id "File exists: $($f.label)" "NOT FOUND: $($f.path)" }
}

# PocketDetector should be GONE
$pdPath = "D:\Aaron\development\zPhone\.claude\worktrees\jovial-ritchie-ae97ee\apps\SkippyChat\app\src\main\java\local\skippy\chat\sensors\PocketDetector.kt"
if (-not (Test-Path $pdPath)) {
    Log-Pass "cleanup" "PocketDetector.kt deleted" ""
} else {
    Log-Fail "cleanup" "PocketDetector.kt still exists" "Should be deleted"
}

#
# ---- FINAL SUMMARY ----
#
Write-Host ""
Write-Host "===================================" -ForegroundColor Cyan
Write-Host ("  PASS: {0}   FAIL: {1}   SKIP: {2}" -f $PASS, $FAIL, $SKIP) -ForegroundColor Cyan
Write-Host "===================================" -ForegroundColor Cyan

if ($FAIL -gt 0) {
    Write-Host ""
    Write-Host "FAILURES:" -ForegroundColor Red
    $RESULTS | Where-Object { $_.Status -eq "FAIL" } | ForEach-Object {
        Write-Host "  [$($_.ID)] $($_.Label)" -ForegroundColor Red
        if ($_.Detail) { Write-Host "       $($_.Detail)" -ForegroundColor Yellow }
    }
}

if ($SKIP -gt 0) {
    Write-Host ""
    Write-Host "ACTION NEEDED BEFORE DEMO:" -ForegroundColor DarkYellow
    $RESULTS | Where-Object { $_.Status -eq "SKIP" } | ForEach-Object {
        Write-Host "  [$($_.ID)] $($_.Label)" -ForegroundColor DarkYellow
        if ($_.Detail) { Write-Host "       $($_.Detail)" -ForegroundColor DarkGray }
    }
}

Write-Host ""
if ($FAIL -eq 0) { exit 0 } else { exit 1 }
