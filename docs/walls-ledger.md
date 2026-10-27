# The walls ledger (draft, 2026-10-27)

The Captain's goal: remove **paywalls, authentication walls, vendor walls and permission friction** from the home AI. The one login he does
is makerportal.live's Google sign-in; everything else is the house's job. One interface, no app-switching, and purple Skippy holds the
control he holds. This is the list of every wall we know of, what it costs him, and the fix. **The order is a first guess from the repo and
the bill audit (docs/ai-bills.md): the Captain corrects it**, and the top of the list gets built first.

Status: `done` · `building` · `designed` · `open` (nobody has designed it yet) · `his` (only he can act, listed so it stays visible).

## Ranked (hit most often or hurts most first; guessed)

| # | Wall | Kind | What it costs him | Fix | Status |
|---|---|---|---|---|---|
| 1 | **Claude Code login on skippy-pc expires** (the bridge health says `login: expired`; orange Skippy is down until `claude auth login`) | auth | A manual sign-in on the PC whenever it lapses; orange is the fallback and the hybrid reviewer | Purple (local) is the default so nothing needs it; for orange, a re-login that is one tap (portal key opens the sign-in on the PC) or an unattended refresh if Anthropic allows it | open |
| 2 | **Windows admin tasks** (Defender exclusion for `D:\trunk`, shares, firewall, services) | permission | He can't do them from the phone, so they wait or he runs them at the PC | A SYSTEM scheduled task that runs named, allow-listed operations; security ones show an Approve card on his phone | designed (not built) |
| 3 | **Cloud Shell** (new buckets, permissions) | auth + permission | A copy-paste session each time | `skippy-bootstrap` + `cd.py bootstrap`: Terraform runs from skippy-core; the last Cloud Shell run is done | **done** 2026-10-27 |
| 4 | **Copying between apps** (a screen, another app's text, files) | vendor (Apple) | Screenshots and pastes by hand | The camera key takes screens and every camera into the chat; an iOS Shortcut posts a screenshot of any app to the portal (Back Tap) | building (key done; Shortcut route not built) |
| 5 | **Session permission prompts** (a Claude Code session asks to approve a command; the auto-mode classifier denies one) | permission | Taps on the phone, or a stalled run | Named safe operations pre-approved for purple; denials reported, never worked around; approval cards in the portal instead of terminal prompts | partly |
| 6 | **Paid AI bills**: Claude Max 20x $200/mo, SuperGrok $30, Google AI Plus $9.99 (docs/ai-bills.md, 2026-09-28) | paywall | ~$240/mo | Purple does the routine coding for free; Claude stays only as reviewer and fallback; SuperGrok and the Google AI tier are his to cancel | his / building |
| 7 | **ElevenLabs** (Skippy's voice) | paywall + vendor | A key and a plan; the voice lives at a vendor | Local F5 voice is live; make ElevenLabs a switch that is off, then remove it; render the pre-recorded lines locally | designed |
| 8 | **Anthropic key in the portal** (`CHAT_MODEL`, the thinking-cap tier in flask-api) | paywall | Pay-per-use if anything calls it | Core's local model answers everything by default; the key is used only for an explicit ask. Audit what still calls it | open |
| 9 | **Leviton cloud login** (email and password in the vault, a cloud session) | auth + vendor | The house lights work only while their cloud does | Matter pairing, local, for every switch (codes from the Leviton app), then the cloud is a fallback | building (Matter set up, bulk pairing waits on codes) |
| 10 | **Logins stored as files** (Discord bot token, Jellyfin key, Google Drive token, Home token) | auth | He pastes them once and they can lapse | A "secrets health" row in Status: each one tested on a timer; one that lapses is flagged with the exact one-tap fix | open |
| 11 | **Tailscale sign-in on each device** (phone, Mac, new devices) | auth | A sign-in per device | Keep: one-time per device; note it as a known wall (an auth key made by the house can pre-approve devices) | his |
| 12 | **Docker Hub and registry images** (llama-swap, Terraform, Matter server) | vendor | A pull that can fail or change | A local registry on skippy-core mirrors every third-party image the house runs | designed (docs/no-github.md) |
| 13 | **GitHub** | vendor + paywall (Actions billing) | Private repos' CI stopped over billing | Removed from the architecture: house git, CI/CD on skippy-core, nightly bundles off-site; archive the repos at the end | mostly done |
| 14 | **Google Cloud as the portal's host** (Cloud Run, buckets, DNS) | vendor | The portal is only as available as one vendor, and it bills | Keep (it has the best uptime, and the deploy is keyless); an exit note and a backup of every bucket in the house | open |
| 15 | **Squarespace registrar** (holds the nameservers) | vendor + auth | A web login for domain changes | Keep for now; move the domain to a registrar with an API if it is ever changed | his |
| 16 | **BIOS and power** (power-on after AC loss, Wake-on-LAN on skippy-core) | permission (physical) | A reboot and a keyboard | Done at a quiet window, once (deferred by the Captain 2026-10-26) | his |
| 17 | **Apple ID and the iPad itself** (camera busy when another app holds it, no cross-app screen) | vendor | A tap per limit | Nothing to remove; named in the camera key's own messages | known wall |
| 18 | **Free-tier API limits** (Wolfram Alpha ~2,000 calls a month, Gemini free tier) | paywall | A limit that hits without warning | Mentat answers physics, so Wolfram is replaced; Nagatha (Gemini) is flask-api only: replace with Core or drop | open |

## What "purple has my control" means here

Capabilities in three tiers, so it is fast and still safe (PLATINUM §19, the agents-that-ship rule):
1. **Free and reversible, no question:** read anything, run tests, commit to its own branch, plan a bootstrap, restart a container that the
   doctor already heals, take a screenshot.
2. **Named and allow-listed, runs and tells him:** deploy a green commit, apply a bootstrap, a Windows operation on the list that does not
   touch security, pop-up jobs.
3. **Approval card on his phone, one tap:** anything irreversible or security-related: a Defender or firewall change, deleting data, a new
   permission, a payment, spending money, a reboot of skippy-core.
Nothing outside those three, and no operation is ever added to tier 2 by the agent itself.

## How this list is kept

One row per wall, edited here and nowhere else. When a row's status changes it is changed in the same commit as the work. The Status
page will link to it; a "secrets health" row (row 10) is its first live part.
