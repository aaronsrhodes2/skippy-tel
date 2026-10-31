# Remote recovery: when the portal or the private Skippy stops answering

For the Captain, on a phone, away from the PC. Read top to bottom; stop at the first step that fixes it.
Nothing here needs a secret typed anywhere. (Why this exists: on 2026-09-30 the private Skippy deployed its own changes once,
then the chat on `/me` froze with "The link to skippy-pc went quiet" and the only way out was to sit at the PC.)

## The chain

```
phone ──> portal (Cloud Run) <── skippy-pc's home reporter (Docker) ──> bridge (a program on Windows) ──> Claude Code
```

| Link | Lives on | What it needs to stay up | Who restarts it |
|---|---|---|---|
| Portal (the page, the chat's memory) | Cloud Run, Google's side | a good deploy | Google; a bad deploy is undone by skippy-core (step 3) or from Google's own console |
| Home reporter | Docker on skippy-pc, container `skippytel-home-reporter` | Docker Desktop running, the internet | Docker (`restart: unless-stopped`); it also restarts itself after 15 minutes without reaching the portal |
| Bridge | the Windows host, port 9337 | you signed in, `tools/bridge_supervisor.sh` | the supervisor (restarts it within seconds; restarts one that is up but mute after a few minutes) |
| OpenAudible (the Audible library, its web server on `127.0.0.1:5758`) | the Windows host, a desktop app | the web server switched on in its Preferences > Web Server | the starter probes it (any HTTP answer is up) and the doctor starts the app when it is not running; a running app with the web server off is never touched, the Captain is DMed |
| Claude Code | the host, signed in as you | its login not expired | you: `claude auth login` on the PC |

The reporter calls **out** to the portal every ~15 s. Nothing connects in to your house, so if the house has no internet, or the PC is
asleep or off, nothing below the portal can answer, and no setting on the portal can fix that.

Every portal deploy (and every rollback) restarts the portal, which forgets its in-memory chat records. That is normal: skippy-pc
holds the truth and re-sends it. **Within about 30 seconds of all three links being up, the chat shows everything again**, including
a run that finished while the portal was away.

## First: Admin > Recovery > **Put everything back**

Recovery is one operation (2026-10-23). Portal > **Admin** > **Recovery** shows one checklist: every part, green or red, and why
(the portal itself, skippy-pc's starter, Docker, the home reporter, each service on skippy-pc, the private Skippy's bridge, Skippy
Core), with what the doctor did lately. Under it, one button: **Put everything back**. It acts at once (no confirm box), at most
twice in five minutes, and is logged. It does all of this:

| Part | What it does |
|---|---|
| The portal | redeploys main (the deploy workflow, tests first) when the newest deploy failed, a deploy is stuck, or main is ahead and nothing is deploying |
| The private Skippy | clears stuck chat records, lifts a pause |
| skippy-pc (through the starter) | Docker answering (restarted clean if hung); main pulled into the checkout when that is a fast-forward (local changes are never touched); every container up from it (`up -d --build --remove-orphans`); whatever is failing recreated (stop, rm, up --build); the bridge restarted when it isn't mid-run |
| Skippy Core (over SSH, from the starter) | `deployer.py repair` on the box: the last good deploy, every container recreated, smoke-tested |

It never reverts code: undoing a change is the private Skippy's job, from a chat that works.

**The doctor keeps it that way by itself** (`portal/doctor.py`, the brain, on Cloud Run where it can't go down):
- skippy-pc's tests run with every home report (`services/home-reporter/doctor_checks.py`): each service must answer, in time, with
  the right thing, so a service that is up but hung counts as down. Skippy Core gets a real five-token reply every five minutes.
- A test failing for five minutes in a row gets healed (that one thing recreated, the bridge restarted, Docker restarted clean,
  or Core repaired), at most three times an hour per thing; then it DMs the Captain on Discord and waits for the next hour.
- It never heals while the stack was stopped on purpose (the starter's "back on at" time), never the bridge mid-run, and the
  starter leaves a service alone while someone is building on the PC (a session's own rebuild).
- skippy-pc and its starter both silent for 10 minutes: a DM (the PC is off, asleep, at sign-in, or without internet).
- On its own, with the portal out of reach: the starter gives a Docker that is hung or down for two minutes the clean restart,
  and skippy-core's doctor (`skippy-core-doctor.timer`, every 3 minutes) restarts llama-swap after two failed replies and repairs
  the stack when restarts don't help.

**Needs, once:** the starter installed on skippy-pc (`bash tools/install_starter_startup.sh --apply`), able to `ssh aaron@skippy-core`
with its key, and the doctor's timer on skippy-core (`sudo bash core/deployer/install.sh --apply` on the box). The old cards (roll
back, re-run, cancel, restart service, reset a copy, Funnel, start/stop) are gone; a rollback is `cd.py rollback` on skippy-core (step 3).

## 1. Read the card on `/me`

The pill on **Skippy · private** and the **Home · skippy-pc** card tell you which link broke. (Wording on screen may differ a little.)

| You see | It means | Do |
|---|---|---|
| Pill `claude-sonnet-5-5`, green; Home card **Online** | everything is up | nothing; if a chat looks stuck, tap **Check again** (it asks skippy-pc right now) |
| Chat note "No word from skippy-pc for N s. It reconnects by itself" | the link is quiet: a deploy, a restarted reporter or bridge, or a brief network drop | wait a minute, tap **Check again**. **Send** comes back by itself after 30 s of quiet |
| **Send** says "Skippy is still working on the last one." | the bridge really is on your previous message | wait, or press **Stop** |
| Pill `claude-sonnet-5-5 · offline` (amber) | the portal sees skippy-pc, but the workshop is not ready: bridge down, its token missing, or Claude's login expired | step 2, then step 4 |
| Home card **Silent**, or "skippy-pc has been silent for N min" | the reporter is not reaching the portal: PC off or asleep, Docker not running, no internet | step 4 |
| A run says "skippy-pc hasn't picked this up. Is the bridge running?" | the reporter is up but the bridge is not | step 2, then step 4 |
| `/me` will not load, or errors | the portal itself | step 3 |
| Core · skippy-tel **Down** or **Degraded** | flask-api or Ollama at home; not the private Skippy's chain | the card lists the problem; step 4 for Docker |

## 2. What does the portal see of skippy-pc?

On skippy-core (over Tailscale: `ssh aaron@skippy-core`): `sudo python3 /opt/skippy-core/cd/cd.py ops home`. The same box answers `status`, `logs
<game>`, `restart-game <game>` and the rest of what the games VM offers (`cd.py ops` lists them). From a phone: any SSH app on the tailnet.

| In the output | Meaning |
|---|---|
| `"online": false`, or `last_report_s` over 60 | the reporter is silent: step 4 (PC, Docker, internet) |
| `"online": true`, `"capabilities"` has `"workshop": false` | the reporter is fine, the bridge is not ready: step 4 (bridge, token, Claude login) |
| `"online": true`, `"workshop": true`, `jobs_pending` above 0 for minutes | the reporter is up and the bridge ready but a job is not being taken: step 4, restart the reporter |
| `"online": true`, `"workshop": true`, and the chat still stuck | give it 30 s and tap **Check again**; then step 3 if it began right after a deploy |

## 3. Undo a bad deploy

A deploy that fails its own smoke test is undone by skippy-core by itself (its log says **ROLLED BACK**, and the old revision is serving
again). For a deploy that looked fine and is not, any one of these, in the order of how much of the house must still be working:

1. **Admin > Recovery** on the portal (if the portal still opens): **Put everything back** redeploys main; the deploy card's rollback
   asks skippy-core (`cd.py rollback`) through the portal.
2. **On skippy-core** (over Tailscale: `ssh aaron@skippy-core`): `sudo python3 /opt/skippy-core/cd/cd.py rollback` goes back **one
   revision**: the newest healthy revision older than the one serving now (never onto a broken newest). `cd.py rollback <name>` goes to
   that revision, `cd.py rollback latest` undoes a rollback. It checks `/health` afterwards and fails loudly if it doesn't answer.
3. **Google's console, on the phone's browser, with nothing of the house running:** console.cloud.google.com > Cloud Run >
   **skippy-portal** > Revisions > **Manage traffic** > 100% to the previous revision > Save.

- The next deploy sends traffic forward again.
- It moves only the portal. It does not touch skippy-pc, the games VM or any data.

To redeploy `main` unchanged (for example after a secret changed): `sudo python3 /opt/skippy-core/cd/cd.py deploy` on skippy-core, or
**Put everything back** on the Admin page.

## 4. What only the PC can fix

Everything here is bash, from `/d/Aaron/development/SkippyTel`. If you cannot be at the PC, the PC has to fix itself (see "Set once", below).

```bash
# Is the bridge up, and is the supervisor watching it?
bash tools/bridge_supervisor.sh --status         # (or: make bridge-status)
curl -s http://localhost:9337/health             # "workshop": {"ready": ..., "login": ..., "reason": ...} says why not
tail -n 40 state/bridge-supervisor.log
```

| Problem | Fix |
|---|---|
| **The bridge is not running** | `bash tools/bridge_supervisor.sh` (or `make bridge-supervised`). Leave it running; it restarts the bridge by itself. A bridge already running by hand is left alone and watched. |
| **New bridge code** (after a `git pull` changed `services/claude-bridge/`) | It only takes effect when the bridge restarts: Admin > **skippy-pc** > **Restart the bridge**. At the PC, under the startup task: `MSYS_NO_PATHCONV=1 schtasks.exe /End /TN SkippyBridge`, then `MSYS_NO_PATHCONV=1 schtasks.exe /Run /TN SkippyBridge`. By hand: Ctrl-C in the supervisor's window and start it again. |
| **Claude's login expired** (`login: "expired"` in the bridge's `/health`) | `claude auth login` on the PC (or open `claude` and type `/login`). The workshop is ready again within about two minutes. |
| **The token is missing** (`reason` says SKIPPY_HOME_TOKEN) | It lives in the vault `.env`; the bridge re-reads it every 30 s. Re-pair from the portal (**Home** card > Re-pair) and put the new line in the vault `.env`. |
| **The reporter is not running or stuck** | `docker ps --filter name=skippytel-home-reporter`, then `docker logs --tail 50 skippytel-home-reporter`. To restart it: `docker compose stop home-reporter && docker compose rm -f home-reporter && docker compose up -d home-reporter` |
| **Docker Desktop is not running** (`docker ps` errors) | Start it. If it will not start: `powershell.exe -NoProfile -File 'D:\Aaron\development\start-docker-clean.ps1'`, wait about 30 s, then `docker ps`. (The `SkippyTelWatchdog` task, if it is registered, does this on its own every 5 minutes.) |
| **The PC is asleep, off, or at the sign-in screen** | Nothing remote can wake it. Set it up so it cannot happen (below). After a power cut the PC sits at the sign-in screen until someone signs in, because Docker Desktop and the bridge both need your session. |
| **No internet at home** | Router, then the ISP. The reporter reconnects by itself within a minute of the link coming back. |

### Set once, by you, on the PC (the three things nothing else can do for you)

1. **Docker Desktop starts when you sign in:** Docker Desktop > Settings > General > "Start Docker Desktop when you sign in to Windows".
2. **The PC never sleeps while plugged in** (the `MSYS_NO_PATHCONV=1` is Git Bash's quirk: without it, `/change` is rewritten into a
   file path and Windows refuses it):
   ```bash
   MSYS_NO_PATHCONV=1 powercfg /change standby-timeout-ac 0
   MSYS_NO_PATHCONV=1 powercfg /change hibernate-timeout-ac 0
   ```
3. **The bridge starts at every sign-in, and again if it ever stops:**
   ```bash
   bash tools/install_bridge_startup.sh            # a dry run: prints the task and the exact schtasks command, changes nothing
   bash tools/install_bridge_startup.sh --apply    # registers the "SkippyBridge" task
   bash tools/install_bridge_startup.sh --status   # asks Windows about it (read-only)
   ```
   If Windows answers "Access is denied", run the `--apply` line again from a terminal opened as Administrator. To remove it:
   `bash tools/install_bridge_startup.sh --remove --apply`. A console window stays open for the supervisor: minimize it; closing it
   stops the supervisor until the next sign-in (or `MSYS_NO_PATHCONV=1 schtasks.exe /Run /TN SkippyBridge`).

Your call, not set by anything here: Windows can sign in by itself after a restart (`netplwiz`), which is what makes steps 1 and 3 work
after a power cut with nobody at the PC. It trades away the sign-in screen's protection of a machine in your own house; decide for yourself.

## Start or stop the whole stack from the portal (the starter)

Two situations, two answers:

* **A power cut (or any reboot) must need nobody.** The PC powers itself back on (BIOS: "Restore on AC power loss" = Power On: set once, in the firmware, nothing in Windows
  can do it), Windows signs in by itself, Docker Desktop starts when you sign in, the containers come back (`restart: unless-stopped`), and the SkippyBridge task starts the
  bridge. Checked on this PC (2026-10-12): Docker Desktop AutoStart on, Windows auto sign-in on, SkippyBridge registered. Not checkable from Windows: the BIOS setting.
* **You took it down on purpose, for a heavy task, and forgot to bring it back.** That is what the **starter** is for: `tools/skippy_starter.py`, an always-on program on the
  Windows host (not in Docker, so it works when Docker is down). Like the home reporter it only makes outbound calls: every ~10 s it tells the portal how things are
  (Docker, containers, bridge) and receives the requests waiting for it with the home token (`POST /home/starter`, `portal/starter.py`). Admin > Recovery > **Start or stop SkippyTel**:

  | Button | What the starter does |
  |---|---|
  | **Start SkippyTel** | Docker Desktop if it is not running (then the clean restart, `start-docker-clean.ps1`, if it will not come up), `docker compose up -d`, then the bridge supervisor |
  | **Stop SkippyTel** | the bridge (refused, touching nothing, while Skippy Desktop is in a run) and every container; Docker Desktop and the starter stay up. It says when it comes back: **tomorrow at 7 AM** (the default), **in 4 hours**, or **only when I start it** |
  | **Restart SkippyTel** | a stop (no automatic return) and a start |

  The "back on" time is kept by the starter itself in `state/starter.json`, so it holds with the portal or the internet down, and across a reboot. **Start SkippyTel** (or any successful
  start) cancels it. If the automatic start fails it tries again ten minutes later.
  Nothing else can be asked of it: the three names above are all it knows, no text from the portal is ever run, and it listens on no port (a loopback lock keeps a second copy from starting).

  Set up once, at the PC (needs your go, it registers a scheduled task):
  ```bash
  bash tools/install_starter_startup.sh            # a dry run: prints the task and the exact schtasks command, changes nothing
  bash tools/install_starter_startup.sh --apply    # registers the "SkippyStarter" task (at sign-in; restarts on failure)
  bash tools/install_starter_startup.sh --status   # asks Windows about it (read-only)
  ```
  What it cannot do: wake a PC that is off, asleep or at the sign-in screen. With the setup above that is a power cut with a PC that failed to come back: look at it in person.
  The card says "The starter isn't answering" in that case, and shows when it was last heard.

## If none of this helps

Open the `ops` workflow with `command` = **status** (the games VM) and **home**, read both logs, and tell Skippy (or Claude) what
they say. The supervisor's own record is `state/bridge-supervisor.log`; the reporter's is `docker logs skippytel-home-reporter`.
More about how the pieces recover: [docs/portal/README.md](README.md) (Private Skippy), and about deploys: [docs/cicd.md](../cicd.md).
