# Skippy roadmap

**North star:** one AI, one memory, one bill, and personal infrastructure
you own instead of rent. Last updated 2026-09-28.

## Track 1: One AI, one memory, one bill
| Phase | What | Status |
|---|---|---|
| 1.1 | AI bill audit → [`docs/ai-bills.md`](ai-bills.md) | ✅ done: cancel SuperGrok ($30), stop Claude auto-recharge, drop Google AI Plus to storage-only |
| 1.2 | Skippy Memory: Claude exports → Drive archive, local-LLM topics → [`docs/memory/`](memory/README.md) | ✅ built. **Next:** first real Claude export, "Skippy" Claude Project, Drive connector |
| 1.3 | Tesla: Claude voice on the S23 over Bluetooth replaces Grok | ⏳ try on the next drive, then cancel SuperGrok |
| 1.4 | Grok + Gemini (Takeout) parsers | ⏳ needs a real export of each |
| 1.5 | Home-hosted MCP server with semantic search over the archive | later: needs Track 4 auth |

## Track 2: Pop-up game servers (replace Nitrado)
| Phase | What | Status |
|---|---|---|
| 2.1 | Compose for Valheim, Factorio, Satisfactory, **RuneScape: Dragonwilds** → [`games/`](../games) | ✅ built |
| 2.2 | GCP pop-up VM: auto-sizing, idle auto-sleep, daily snapshots → [`infra/gcp-games/`](../infra/gcp-games), [`scripts/games.sh`](../scripts/games.sh) | ✅ built. **Next:** `tofu apply` in a real project |
| 2.3 | Discord: per-game text + voice channel, pinned self-updating status post, start/sleep pings → [`scripts/discord_setup.py`](../scripts/discord_setup.py) | ✅ built. **Next:** create the bot, run setup |
| 2.4 | First session: Dragonwilds with the work friend (PC + Xbox crossplay) | ⏳ |
| 2.5 | Move Nitrado worlds over, then cancel Nitrado | ⏳ |
| 2.6 | Start servers from Discord / by voice ("Skippy, spin up Valheim") | later |
| — | ARK | deferred: too resource-heavy |

Hosting comparison and runbook: [`docs/games/README.md`](games/README.md).

## Track 3: Remote play from anywhere (no Steam Link PIN)
| Phase | What | Status |
|---|---|---|
| 3.A | PC host: Sunshine + readiness script (power, updates, auto-start) → [`docs/streaming.md`](streaming.md), [`scripts/windows/streaming-host.ps1`](../scripts/windows/streaming-host.ps1) | ✅ built. **Next:** run it on the PC |
| 3.B | MacBook: Moonlight + Tailscale, pair via the Sunshine web UI, hotspot test, Steam Remote Play backup | later (MacBook phase) |
| 3.C | Wake-on-LAN from away, GPU "game mode" in SkippyTel | later |

## Track 4: SkippyTel v2 infrastructure (on hold)
The charter is in [`docs/v2/CHARTER.md`](v2/CHARTER.md): home Core + small edge,
Headscale + own DERP (the Tailscale lag fix), passkey auth, capability
modules. Paused until "personal assistant" is defined. Track 1 is shaping
that answer: **Skippy is the memory.**
