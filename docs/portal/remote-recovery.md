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
| Portal (the page, the chat's memory) | Cloud Run, Google's side | a good deploy | Google; a bad deploy is undone by `rollback` (below) |
| Home reporter | Docker on skippy-pc, container `skippytel-home-reporter` | Docker Desktop running, the internet | Docker (`restart: unless-stopped`); it also restarts itself after 15 minutes without reaching the portal |
| Bridge | the Windows host, port 9337 | you signed in, `tools/bridge_supervisor.sh` | the supervisor (restarts it within seconds; restarts one that is up but mute after a few minutes) |
| Claude Code | the host, signed in as you | its login not expired | you: `claude auth login` on the PC |

The reporter calls **out** to the portal every ~15 s. Nothing connects in to your house, so if the house has no internet, or the PC is
asleep or off, nothing below the portal can answer, and no setting on the portal can fix that.

Every portal deploy (and every rollback) restarts the portal, which forgets its in-memory chat records. That is normal: skippy-pc
holds the truth and re-sends it. **Within about 30 seconds of all three links being up, the chat shows everything again**, including
a run that finished while the portal was away.

## First: the Admin page's Recovery cards (if the portal opens at all)

Portal > **Admin** > **Recovery**. Three cards, admin only (a friend never sees them), every button asks "are you sure?" first,
and each press is logged (the last 20 show at the bottom; Cloud Logging keeps `"event": "recovery"` lines).

| Card | Shows | Buttons |
|---|---|---|
| **Live version** | the commit and Cloud Run revision serving now; "main is N pushes ahead of what's live"; "a newer deploy passed but isn't serving: a rollback is holding traffic back" | none |
| **Deploy pipeline** | the newest deploy runs of `main`: passed / failed / running, how long ago, a link to each log | **Roll back the portal** (box blank = the previous good revision; or a revision name, or `latest`), **Undo a rollback**, **Re-run the failed jobs** (only when the newest deploy failed), **Cancel the stuck deploy** (only when the newest deploy has been running for over 20 minutes) |
| **Private Skippy** | the current task and its state (queued, running, offline, stalled...) | **Cancel the current run**, **Clear a stuck chat record**, **Pause the private Skippy** / turn it back on |

- **Cancelled a deploy? It now rolls back by itself.** The deploy's last step runs when a run is cancelled too (not only when it
  fails), and puts back the revision it replaced if it had already switched traffic. So **Cancel the stuck deploy** only cancels;
  if the portal is still wrong a few minutes later, press **Roll back the portal**.
- **A rollback holds only until the next push to `main`**, which deploys normally (whatever that push contains). Fix forward.
- **Cancel the current run** also withdraws a message that is still waiting for skippy-pc, so it is never delivered hours later.
- **Pause** refuses new messages and cancels the current run. It lives in the portal's memory: a deploy or restart of the portal
  turns it back on.
- The deploy buttons need the portal's GitHub token (`GITHUB_ACTIONS_TOKEN`). Without it the Deploy pipeline card says "Not
  configured" and shows no buttons. To set it up: on github.com, Settings > Developer settings > Fine-grained tokens > a token for
  this repo only with **Actions: Read and write** and nothing else; add it to this repo's Actions secrets as `PORTAL_ACTIONS_TOKEN`;
  deploy (Actions > **deploy** > Run workflow). The deploy copies it into Secret Manager and hands it to the portal.
- **If the portal itself is broken** (the Admin page won't open), none of this is reachable: use GitHub mobile > this repo >
  Actions > **rollback** > Run workflow, box left blank (step 3). To cancel a hung deploy there: Actions > **deploy** > the running
  run > Cancel; it rolls itself back as above.

## 1. Read the card on `/me`

The pill on **Skippy · private** and the **Home · skippy-pc** card tell you which link broke. (Wording on screen may differ a little.)

| You see | It means | Do |
|---|---|---|
| Pill `Opus`, green; Home card **Online** | everything is up | nothing; if a chat looks stuck, tap **Check again** (it asks skippy-pc right now) |
| Chat note "No word from skippy-pc for N s. It reconnects by itself" | the link is quiet: a deploy, a restarted reporter or bridge, or a brief network drop | wait a minute, tap **Check again**. **Send** comes back by itself after 30 s of quiet |
| **Send** says "Skippy is still working on the last one." | the bridge really is on your previous message | wait, or press **Stop** |
| Pill `Opus · offline` (amber) | the portal sees skippy-pc, but the workshop is not ready: bridge down, its token missing, or Claude's login expired | step 2, then step 4 |
| Home card **Silent**, or "skippy-pc has been silent for N min" | the reporter is not reaching the portal: PC off or asleep, Docker not running, no internet | step 4 |
| A run says "skippy-pc hasn't picked this up. Is the bridge running?" | the reporter is up but the bridge is not | step 2, then step 4 |
| `/me` will not load, or errors | the portal itself | step 3 |
| Core · skippy-tel **Down** or **Degraded** | flask-api or Ollama at home; not the private Skippy's chain | the card lists the problem; step 4 for Docker |

## 2. What does the portal see of skippy-pc? (GitHub mobile)

GitHub app > this repo > **Actions** > **ops** > **Run workflow** > `command` = **home** > Run. Open the run, then its job log.
(No app? The same screens work at github.com in the phone's browser.)

| In the output | Meaning |
|---|---|
| `"online": false`, or `last_report_s` over 60 | the reporter is silent: step 4 (PC, Docker, internet) |
| `"online": true`, `"capabilities"` has `"workshop": false` | the reporter is fine, the bridge is not ready: step 4 (bridge, token, Claude login) |
| `"online": true`, `"workshop": true`, `jobs_pending` above 0 for minutes | the reporter is up and the bridge ready but a job is not being taken: step 4, restart the reporter |
| `"online": true`, `"workshop": true`, and the chat still stuck | give it 30 s and tap **Check again**; then step 3 if it began right after a deploy |

## 3. Undo a bad deploy: the `rollback` workflow

GitHub app > **Actions** > **rollback** > **Run workflow** > leave the box **blank** > **Run workflow**.

- Blank goes back **one revision**: the newest healthy revision older than the one serving now. The run summary lists the last 5
  revisions with the commit each came from, what it served before, what it serves now, and whether `/health` answers.
- To go to a particular one, type its name from that list. To undo a rollback, type `latest`.
- The next push to `main` deploys normally and sends traffic forward again.
- A deploy that fails its own smoke test already does this by itself (the job ends red, with an error saying it was **rolled back**),
  and so does a deploy you **cancel** (a hung one: every deploy job now gives up by itself after at most 20 minutes, and every
  smoke-test request after 20 seconds). This workflow is for a deploy that looked fine and is not.
- The same buttons are on the Admin page (Recovery > Deploy pipeline) while the portal still opens.
- It moves only the portal. It does not touch skippy-pc, the games VM or any data.

To redeploy `main` unchanged (for example after a secret changed): Actions > **deploy** > Run workflow.

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
| **New bridge code** (after a `git pull` changed `services/claude-bridge/`) | It only takes effect when the bridge restarts. Under the startup task: `MSYS_NO_PATHCONV=1 schtasks.exe /End /TN SkippyBridge`, then `MSYS_NO_PATHCONV=1 schtasks.exe /Run /TN SkippyBridge`. By hand: Ctrl-C in the supervisor's window and start it again. |
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

## If none of this helps

Open the `ops` workflow with `command` = **status** (the games VM) and **home**, read both logs, and tell Skippy (or Claude) what
they say. The supervisor's own record is `state/bridge-supervisor.log`; the reporter's is `docker logs skippytel-home-reporter`.
More about how the pieces recover: [docs/portal/README.md](README.md) (Private Skippy), and about deploys: [docs/cicd.md](../cicd.md).
