# SkippyCore install: from the USB stick to a box you can reach remotely

Gets the Bosgame M5 from factory Windows to Ubuntu 26.04 server with SSH, Tailscale, Docker, the repo and a models
check. After that, carry on with `core/README.md` → "Day one on the box" from step 3 (kernel options, models, deployer).

## The stick

A FAT32 USB stick with the contents of `ubuntu-26.04.1-live-server-amd64.iso` copied onto it (the ISO's SHA256 checked
against releases.ubuntu.com first). UEFI boots it directly, so no Rufus and no reformat. Next to Ubuntu it carries:

- `skippycore/firstboot.sh` and this README
- `skippycore/skippy-tel.bundle`, the whole repo as one git file (the repo is private, so the box can't clone it yet)

## Before wiping Windows (inside the return window)

1. In Windows: Settings → System → About shows **128 GB** RAM; Disk Management shows the **2 TB** disk.
2. Memory test: `mdsched.exe` → "Restart now and check for problems". The result appears after the reboot.
3. Decide: **replace Windows** (simplest, the plan) or **dual-boot** (suspend BitLocker and turn off Fast Startup first).

## Booting the stick

You don't need to change the boot order. Use the one-time boot menu:

1. Plug in the stick and **Ethernet** (the installer and `firstboot.sh` download packages).
2. Power on and tap **F7** for the boot menu (if that doesn't work: **Del** for setup → Boot → Boot Override).
   Pick the **UEFI** entry for the USB stick.
3. While in setup (**Del**), set the iGPU memory (UMA frame buffer) to its smallest value and note the BIOS version.
   Secure Boot can stay on: Ubuntu's loader is signed.

## In the installer

- **Ubuntu Server** (not minimized). Network: the Ethernet port with DHCP.
- Disk: "Use an entire disk" on the 2 TB NVMe **erases Windows**. That is the Captain's call, at that screen.
  For dual-boot, use "Custom" and the free space you shrank in Windows.
- Profile: hostname `skippy-core`, user `aaron`.
- **Tick "Install OpenSSH server".** No featured snaps (Docker comes from apt, not the snap).
- Reboot and remove the stick when asked.

## First boot

The repo is private, so the box can't fetch anything from GitHub yet: everything comes off the stick. Leave the stick in
(or put it back), sign in on the box's own screen, find the stick (the ~31 GB vfat one) and copy its `skippycore` folder:

```bash
lsblk -o NAME,SIZE,LABEL,FSTYPE
```

```bash
sudo mount /dev/sda1 /mnt && cp -r /mnt/skippycore ~ && sudo umount /mnt
```

Then a dry run, then the real thing:

```bash
sudo bash ~/skippycore/firstboot.sh
```

```bash
sudo bash ~/skippycore/firstboot.sh --apply
```

It installs and starts SSH, installs Tailscale and runs `tailscale up` (open the link it prints on your phone),
installs Docker, git and python3, clones the repo to `/opt/skippy-core/repo` from the bundle (origin then points at GitHub)
and runs `core/llm/models.py check`. Missing models are expected at this point. It never touches disks or the bootloader.
Re-running it is safe.

From then on the box is reachable as `ssh aaron@skippy-core` over the tailnet, and the rest can be done remotely.

## Hand it to skippy-pc (once, from skippy-pc)

The Captain never types keys or codes after this; skippy-pc (Claude, the box's admin) does the rest over SSH.

- skippy-pc's key: `cat ~/.ssh/id_ed25519.pub | ssh aaron@skippy-core 'mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys'` (the one password prompt), then on the box
  `echo 'aaron ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/90-aaron && sudo chmod 440 /etc/sudoers.d/90-aaron && sudo visudo -c`.
- Secrets go from the vault straight into root-only files, never through a terminal or chat:
  `tr -d '\r' < "/g/My Drive/vault/.env" | ssh aaron@skippy-core 'sudo install -m 0600 /dev/stdin /etc/skippy-core/vault.env'`.
  The deployer's `GITHUB_TOKEN` in `/etc/skippy-core/core.env` is the vault's `GITHUB_DEPLOYER_TOKEN` (the repo is private:
  it needs Contents and Actions read; it is also what lets `git fetch` through).
- Ubuntu's installer gives `/` only 100 GB of the disk: `sudo lvextend -r -l +100%FREE /dev/ubuntu-vg/ubuntu-lv` (grows, erases nothing).

## Headless and remote-only (`harden.sh`)

```bash
sudo bash harden.sh
```

```bash
sudo bash harden.sh --apply
```

```bash
sudo bash harden.sh --check
```

It makes the box recover by itself and keeps two ways in:

- **Recovers by itself:** boots to text; the AMD chip watchdog (`sp5100_tco`, which Ubuntu deny-lists, so a small
  service loads it) reboots a box that stops for 60 s; a kernel panic reboots after 10 s; a failed boot doesn't wait
  at a menu; fsck repairs instead of asking; Wake-on-LAN on `eno1`; security updates install by themselves and a
  reboot they need happens at 04:30 Pacific.
- **Two doors:** SSH over the tailnet and from the home LAN (192.168.0.0/24), keys only, `aaron` only. Tailscale
  updates itself. The firewall denies everything else coming in; enabling it arms a 5-minute dead-man switch that
  turns it off again unless both doors were checked and the timer stopped.
- **Lean:** VM tools, modem manager, iSCSI, multipath, fan networking, cloud-init, apport and snapd are removed
  (their dependencies are marked kept first, so autoremove can't take SSH or Docker with them). Disk health
  (`smartd`) and sensors are added.

`--check` is read-only and exits 1 when any piece is missing: the health checks run it.

Things a script can't do, and need the Captain once:
- **Tailscale key expiry:** in the Tailscale admin console → Machines → skippy-core → ⋯ → *Disable key expiry*.
  Otherwise the box drops off the tailnet when its login expires (the LAN door still works).
- **BIOS:** *Restore on AC Power Loss = Power On*, so a power cut doesn't leave it off. Only a visit (or a KVM) changes it.
