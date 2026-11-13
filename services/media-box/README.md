# The bedroom media box (X88 Pro 13), a deployment target like the room pods

An RK3528 Android 13 TV box on the bedroom TV (192.168.0.69). Moonlight streams Steam Big Picture from skippy-pc's Sunshine to it (a DualSense is paired
to the box over Bluetooth); Plex plays skippy-pc's library directly. It replaces skippy-core's Moonlight kiosk (`core/install/couch.sh`).

Its software stack is made here, from skippy-pc, in layers, each one pinned in `manifest.json` and applied by `tools/media_box.py`:

| layer | what | how | state |
|---|---|---|---|
| apps | Moonlight, Plex (SHA-256 pinned; Plex's signer too) | `python tools/media_box.py deploy` | done |
| stock apps | TikTok, two app stores, Scooper TV ... taken off | the manifest's `remove` list, same command | done |
| firmware | the Android image itself | Rockchip maskrom + `rkdeveloptool` (below) | not yet: needs the Captain's USB A-to-A cable and a chosen image |

`python tools/media_box.py status` is read only and exits 2 when the box differs from the manifest. A pin is the only way an app changes: put the new file
in `state/media-box/cache/<sha256>.apk|apkm` (or let `deploy` download it from the manifest's source) and change the hash.

## Reaching the box
The box's own "Internet Adb" switch (Developer options > Debugging, on) keeps adb on the fixed port 5555 and survives reboots; our key is already authorised. Wireless
debugging does not: Android drops it at every reboot and its port is random, so it is only the last resort. `media_box.py` tries `<manifest host>:5555`, then
the address mDNS gives, then scans the subnet for 5555, and checks `ro.serialno` so it never talks to another Android. The box's Wi-Fi MAC is random per boot, so its
DHCP address moves (it was .69, then .237): a router reservation cannot pin it on Wi-Fi, only the wired Ethernet MAC (30:a6:12:15:45:4c) once a cable is in.
Never forward port 5555 on the router: "Internet Adb" is only for the LAN.

## Firmware flashes (the plan; nothing here has been flashed)
1. Back up the stock firmware first (`rkdeveloptool rl` in maskrom, or the vendor's tool): the stock image is the only way back.
2. Get the box into maskrom: USB A-to-A cable to skippy-pc, hold the reset button (the AV jack's pinhole) while powering on; `rkdeveloptool ld` shows "Maskrom".
3. Flash a pinned image. Candidates: a community AOSP Android TV ROM for this exact box (gitee `huotanzai/X88_Pro_13_AOSP_AndroidTV_ROM`, flashed with Rockchip
   FactoryTool), or Armbian for RK3528 (experimental; eMMC installs have bricked boxes: SD-card boot first).
4. Redeploy the apps with `media_box.py deploy`.
