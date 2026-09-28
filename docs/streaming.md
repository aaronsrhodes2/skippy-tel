# Remote play from anywhere (no PIN from home)

**Goal:** play the Windows PC's games on the MacBook Pro from anywhere (hotel,
vacation), without ever needing to be at the PC to type a Steam Link PIN.

**Why Steam Link keeps failing you:** Steam Link's pairing PIN is typed
*on the PC*. When it decides to re-pair, you're locked out until you get home.

**The fix, two layers:**

| | How | PIN? |
|---|---|---|
| **Primary: Sunshine + Moonlight** | Sunshine runs on the PC as a Windows service. Moonlight is the client app on the Mac. Traffic goes over Tailscale. | Pair **once**. Pairing is certificate-based and doesn't expire. Even pairing is done in Sunshine's **web page**, which you can open from the Mac over Tailscale. You never need to be home. |
| **Backup: Steam Remote Play** | The regular Steam client on the Mac (not the Steam Link app), logged into the same account. Windows games show a **Stream** button. | None: same account = trusted. |

Sunshine usually has lower latency and better image quality than Steam's
streaming, handles keyboard + mouse well, and streams the Windows login
screen if the PC rebooted.

## Phase A: PC side (doable tonight, from the PC)

1. **Run the readiness script** (report only):
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\windows\streaming-host.ps1
   ```
   Then fix what it can, from an **Administrator** PowerShell:
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\windows\streaming-host.ps1 -Apply
   ```
   This sets AC sleep/hibernate to never, sets Windows Update active hours to 08:00–02:00, and
   installs Sunshine and sets its service to start automatically.

2. **Sunshine first-run:** open `https://localhost:47990`, create the web UI username/password,
   and leave the defaults. It detects the NVIDIA GPU (NVENC). The default app list already has
   *Desktop* and *Steam Big Picture*.

3. **By hand, once** (the script reports these but can't set them):
   - **Tailscale → Run unattended**: tray icon → Preferences. It keeps the PC on the tailnet
     when nobody is logged in.
   - **Auto-login** with [Sysinternals Autologon](https://learn.microsoft.com/sysinternals/downloads/autologon),
     so Steam comes back after a Windows Update reboot. Sunshine doesn't need it; Steam does.
   - **Steam → Settings → Interface → Run Steam when my computer starts.**
   - **BIOS:** "Restore on AC power loss" → **Power On**. Otherwise a power blip on day 2
     ends your vacation gaming.
   - **Remote Desktop** on (Windows Pro) as the rescue path: reach it at the PC's Tailscale IP.
     **Never** port-forward 3389.

4. **Check the path isn't relayed:** from another device on a **phone hotspot** (not home
   Wi-Fi), run `tailscale ping skippy-pc`. You want `via <ip>:<port>` (direct). If it says
   `via DERP(...)`, traffic is detouring through a relay, and streaming will stutter.
   Common fix: on the home router, enable UPnP/NAT-PMP, or forward UDP 41641 to the PC.
   This is the same fix that addresses the SkippyTel lag.

5. **If the Sunshine web UI refuses connections over Tailscale:** Sunshine only allows its
   web UI from LAN addresses by default, and Tailscale's 100.x addresses may not count. Set
   `origin_web_ui_allowed = wan` in `C:\Program Files\Sunshine\config\sunshine.conf` and restart
   the service. That is safe as long as the router does **not** forward port 47990; the UI is
   still password-protected.

## Phase B: MacBook side (later)

1. Install **Moonlight** (`brew install --cask moonlight` or moonlight-stream.org) and **Tailscale**.
2. At home or away, Moonlight → **+** → add the PC's Tailscale name (`skippy-pc`) or its 100.x IP.
3. Moonlight shows a PIN. Open `https://skippy-pc:47990` on the Mac → **PIN** tab → enter it.
   You're paired for good.
4. Settings to start with: 1080p or the MacBook's native resolution, 60 fps, bitrate 20–40
   Mbps on hotel Wi-Fi, **HEVC** or **AV1** if the PC's GPU supports it, "Optimize mouse for
   remote desktop" off for games.
5. **Hotspot test before you travel:** tether the Mac to your phone, connect, and play for
   10 minutes.
6. **Backup path:** install Steam on the Mac, log in with the same account, confirm a Windows
   game shows **Stream**.

## Phase C: later ideas
- Wake-on-LAN from away (needs an always-on device at home, e.g. the router or a Pi), so the
  PC can sleep between sessions.
- A "game mode" in SkippyTel that unloads Ollama/Whisper from VRAM while Sunshine is streaming.
- Voice: "Skippy, get the PC ready to stream" → the readiness check runs through SkippyTel.
