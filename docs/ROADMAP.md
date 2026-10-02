# Skippy roadmap

**North star:** one AI, one memory, one bill, and personal infrastructure
you own instead of rent — changed by asking, deployed by pushing.
Last updated 2026-10-02.

## Tonight's victory condition
- [ ] **CI/CD:** tell Claude a change → it pushes → GitHub Actions tests and deploys. No typed commands. → [`docs/cicd.md`](cicd.md)
- [ ] **Portal reachable** behind Google sign-in → [`docs/portal/README.md`](portal/README.md)
- [ ] **RuneScape: Dragonwilds tile** with status, start/stop, how to join (world-name search; PC + Xbox crossplay), and its `#dragonwilds` / 🔊 Discord links
- [ ] **Admin page:** settings, Restart & update, Discord setup
- [ ] **Text chat with Skippy** (Claude + persona; skippy-tel-core/MCP bridging is the next phase)

**Built and pushed; waiting on one step from you:** the one-time bootstrap in Cloud Shell —
`cd ~/skippy-tel && git pull && bash infra/bootstrap/bootstrap.sh`.

## Done
| What | Where |
|---|---|
| AI bill audit (≈$240/mo → ≈$200: cancel SuperGrok, stop Claude auto-recharge, Google AI Plus → storage) | [`docs/ai-bills.md`](ai-bills.md) |
| Skippy Memory: AI chat exports → Markdown archive on Drive, local-LLM topics, Claude Project instructions | [`memory/`](../memory), [`docs/memory/`](memory/README.md) |
| Game servers as compose profiles: Valheim, Factorio, Satisfactory, RuneScape: Dragonwilds | [`games/`](../games) |
| GCP pop-up VM — **live** in project `skippy-tel`, `us-west1-b`, IP `35.233.145.176`: auto-sizing, idle auto-sleep, graceful shutdown, daily snapshots | [`infra/gcp-games/`](../infra/gcp-games) |
| Discord per-game channels, webhooks, self-updating pinned status | [`portal/discord_admin.py`](../portal/discord_admin.py), [`infra/gcp-games/discord-notify.py`](../infra/gcp-games/discord-notify.py) |
| Maker Portal: auth, dashboard, admin, chat | [`portal/`](../portal) |
| CI/CD: GitHub Actions + Workload Identity Federation → Terraform + Cloud Run | [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml), [`infra/bootstrap/`](../infra/bootstrap) |
| Remote play host setup (Sunshine + readiness script), PC side | [`docs/streaming.md`](streaming.md) |
| Claude Code history from every machine → Skippy Memory (hourly one-way copy + parser) | [`scripts/claude-history-sync.*`](../scripts), [`memory/parsers/claude_code.py`](../memory/parsers/claude_code.py) |
| Secrets convention (vault on Drive) | [`docs/SECRETS.md`](SECRETS.md) |

## Next
| Track | Item |
|---|---|
| Portal | Bridge chat to **skippy-tel-core** and its MCP tools (must filter tools through `portal/tool_policy.py`: friends get only `everyone` servers like physics) (needs a route from Cloud Run into the home mesh: Tailscale or a tunnel) |
| Portal | ~~Live services list (skippy-tel-core `/services`)~~ Done via the home reporter: the **Core · skippy-tel** widget on `/me` shows the health verdict, tiers and the whole service manifest (no mesh route needed; skippy-pc pushes out) |
| Portal | Background restarts with progress; per-game container restart without a full VM reboot |
| Games | **EverQuest (EQEmu)**: built 2026-09-29 (AkkStack on the VM, home DB relayed through the portal, own login server). Next: first-boot install test, import, player count for the game page |
| Games | **In-game chat → Discord → portal** (backlogged until a real play session): a VM log watcher posts chat lines to the game's channel webhook, so they show in Discord and on the game page. Factorio logs `[CHAT]`, so it's the first candidate. **Dragonwilds: tested 2026-09-29, its server doesn't log chat** (a marker typed in game never reached the container log), so no bridge there; Valheim/Satisfactory don't without mods |
| Games | First real session: Valheim / Dragonwilds (PC + Xbox crossplay); move Nitrado worlds, cancel Nitrado |
| Memory | Install the history sync on the PC and MacBook; first real Claude export; Grok + Gemini parsers from real exports |
| Secrets | Vault → Secret Manager sync in the deploy job (needs the Captain's OK for the IAM grant; manual `secrets.sh --from-env` until then) |
| Bills | Cancel SuperGrok after Claude voice in the car is proven |
| Streaming | MacBook side (Moonlight, pairing, hotspot test) |
| Later | **AR glasses view in the portal** (backburner, the Captain 2026-10-02): if it's picked up again, do it as a portal page (`/hud`), not an app. Findings: zPhone has no Viture-specific code (the glasses are a plain USB-C DisplayPort second screen; heading is the phone's own sensors), so a full-screen black page works mirrored or under Samsung DeX; camera only on demand (the glasses are see-through), no background services. The native app ran the phone hot and drained it. First step then: a 5-minute capability test page on a spare phone. skippy-pc can't drive the glasses through a USB hub (it needs DP Alt Mode or an HDMI/DP-to-USB-C adapter) |
| Portal | **Nagatha's Video Service**: browser video from skippy-pc's Jellyfin through a pass-guarded video gate (gate built 2026-09-30; `/video` live since 2026-10-01; open: the gate's collections came back empty on 2026-10-01 after Jellyfin's item ids changed, a TV library when there is one) |
| Later | SkippyTel v2 core ([`docs/v2/CHARTER.md`](v2/CHARTER.md)); ARK (deferred, too heavy) |

**Dropped:** face recognition (InsightFace, HeyBuddy) was dropped by the Captain's decision on 2026-09-29; do not re-add it.
