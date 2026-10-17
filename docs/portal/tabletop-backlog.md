# Tabletop backlog

Ideas for the Heroes of Chance Tabletop, researched 2026-10-16 (a web survey of Foundry VTT and its Daggerheart add-ons, Roll20 and
Demiplane, Owlbear Rodeo, Discord RPG bots, and campaign sites such as World Anvil and Kanka). Nothing here is built. The Discord pieces
reuse `portal/discord_admin.py` (a REST wrapper with the bot token, channel/invite/webhook "ensure" helpers, a cached message reader)
and `portal/notify.py` (DMs the Captain, pings nobody, swallows and logs errors, sends off the request thread). There is no
Discord OAuth, interactions endpoint, scheduled-event, thread or Cloud Scheduler code yet.

Discord's docs are at https://docs.discord.com/developers (the old discord.com/developers/docs address redirects).

## Remote play via Discord (a player who can't be at the table)

| # | Feature | Effort | How it would work here | Watch out for |
|---|---|---|---|---|
| 1 | **Session feed**: Fear changes, new pins, project-clock ticks (later: rolls) posted to a session channel | S | In the Tabletop handlers, POST to a webhook with [Execute Webhook](https://docs.discord.com/developers/resources/webhook). No bot needed to post; set `allowed_mentions: {"parse": []}`; fire-and-forget on a thread. Webhook URL kept next to the Tabletop state in the bucket. | Per-webhook rate limits (handle 429 `retry_after`, debounce Fear to about 3 s); a 404 means stop using the hook; **never post fogged cards or the GM quick reference**. |
| 2 | **Discord sign-in + "join the table"**: a button showing the session's voice-channel invite, and the portal knowing which Discord person is which player | M | OAuth2 authorization-code flow, `identify` scope only ([OAuth2](https://docs.discord.com/developers/topics/oauth2)); store `google email -> discord id` in the bucket; keep Google as the gate. The invite is the existing permanent one. | Client secret in Secret Manager; redirect URI must match exactly; store only the id and display name. |
| 3 | **Scheduled event + reminder** for each session | S | `POST /guilds/{id}/scheduled-events` ([docs](https://docs.discord.com/developers/resources/guild-scheduled-event)); type 2 (voice) with the table voice channel. Reminders via Cloud Scheduler hitting a protected endpoint (Cloud Run scales to zero). | Needs the bot's CREATE_EVENTS permission, so the bot is re-invited. |
| 4 | **Hope/Fear duality roller** everyone sees (and the Fear counter goes up on a Fear result, with undo) | M | Server-side `secrets` roll of two d12 plus a modifier; show on the TV; post to the feed (#1). Higher Hope: gain Hope; higher Fear: GM Fear +1; doubles are a critical (check against the core book). | Tie a roll to the hero the signed-in player owns, so nobody rolls for someone else. |
| 5 | **Session recap thread** | S-M | A thread (or recap embed) per session. Public thread type and its permission to be verified on the channel docs before building. | Recording consent applies: do **not** record voice via the bot. |
| 6 | **Slash commands** (`/fear`, `/roll`, `/pin`) | M-L | HTTP interactions endpoint, not the Gateway: verify the Ed25519 signature on every request, answer PING with `{"type":1}`, 401 on a bad signature ([overview](https://docs.discord.com/developers/interactions/overview)). Guild commands registered with the bot token. Limit `/fear` with `default_member_permissions`; identify the caller via #2. | Cold start can exceed the 3-second reply window: use min-instances 1 or defer the reply (timings to verify on "Receiving and Responding"). |

## At the table

- **A "table TV" view**: big Fear, the clocks and the newest pin, no controls (Foundry's countdown and Fear trackers are the model).
- **Spotlight tracker** (Foundry has a Daggerheart one): who has had the spotlight this session. The GM packet already has a paper one.
- **Roll/Fear log with stats** for the post-session comic and recap (Foundry's Daggerheart Statistics add-on is the model).

## Between sessions

- **"New pin" notifications** per player (a Discord DM or a mention in the feed), so a revealed handout isn't missed.
- **A clock/countdown library** beyond project clocks (encounter and narrative clocks).

## Not worth building (for now)

- A map, fog-of-war-on-a-map or token VTT: Foundry and Owlbear do it well and the table plays in person. (The Tabletop's own fog is per card, not a map.)
- A Discord Activity (the Tabletop inside Discord's voice screen): needs the Embedded App SDK, URL mappings and a sandbox CSP, and only helps the Discord-side player. Revisit after #1 to #4.
- A bot that reads messages (the privileged Message Content intent), voice recording or screen-share automation (consent and terms risk), or a wiki/world-building engine (World Anvil, Kanka and LegendKeeper exist; the cork board is enough).

## Not verified in the research

Feature lists for Avrae, Sidekick, Dice Maiden, Kanka, World Anvil, Obsidian Portal and LegendKeeper; webhook rate-limit numbers; thread
permissions; the 3-second and 15-minute interaction timings; Activity release approval; Discord's terms on recording. Check each
against the official docs before building it.
