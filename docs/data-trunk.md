# The data trunk: skippy-pc's D: drive on skippy-core (2026-10-25)

skippy-pc's D: drive (14 TB) is the house's storage and development disk; skippy-core's 2 TB is for running models, their containers'
volumes and the chat histories. The trunk joins them: two Windows shares on skippy-pc, mounted on skippy-core over the LAN (both
machines are wired to the same gigabit switch; addresses reserved in the router: skippy-pc 192.168.0.164, skippy-core 192.168.0.242).

| On skippy-pc | On skippy-core | Access | For |
|---|---|---|---|
| `D:\trunk` (share `trunk`) | `/mnt/pc/trunk` | read and write | anything either machine hands the other, in both directions |
| `D:\datasets` (share `datasets`) | `/mnt/pc/datasets` | read only | every dataset, used in place; copy one to `/srv/datasets` only when it must be local and fast |

**Measured (2 GB files):**
- reading from skippy-pc: 115-117 MB/s (the link's full speed);
- writing to it: 77 MB/s. Windows Defender scans every write; an exclusion for `D:\trunk` lifts it.

**Always open:**
- the mounts are `x-systemd.automount` in skippy-core's `/etc/fstab`: mounted on first use, remounted after a drop or either
  machine's reboot (tested: unmounted, then a plain `ls` brought it back);
- `nofail` and `_netdev` mean skippy-core never waits for skippy-pc to boot; with skippy-pc off, the folders are simply away until it's
  back.

**The login:**
- on skippy-pc, a local Windows account `skippy-core`, the only account either share admits (`trunk` Full, `datasets` Read). Its
  password is `SKIPPY_CORE_PWD` in the vault;
- on skippy-core, `/etc/skippy-core/pc-share.cred` (root only, 600, LF line endings: CRLF makes mount.cifs say "Invalid username").

**What lives where now:**
- Mentat's JPL data: a local copy at `/srv/datasets/jpl` on skippy-core (179 files, checked identical);
- everything else stays on `D:\datasets`, read through `/mnt/pc/datasets`.

To make a dataset local: `cp -a /mnt/pc/datasets/<name> /srv/datasets/` on skippy-core.

**Why not SSH or rsync** (tried first, same day):
- a tar stream over SSH dropped twice at the same point;
- rsync from WSL reads D: at only about 8 MB/s;
- a share is always there, needs no copy, and resumes by itself.
