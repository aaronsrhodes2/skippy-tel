# The Captain's Tesla

His Model 3, read at home and shown in the portal (2026-10-26, the Captain: "option 1 today, option 2 save it for the future").

## What runs (phase 1: read only)

```
Tesla's servers ──(his own tokens)── TeslaMate ─┬─ Postgres   every drive and charge, kept forever (${CORE_STATE}/tesla/db)
                                   (skippy-core) └─ MQTT ── car reader (core/car, :7351) ── skippy-pc's reporter ── portal
                                                                                                   (CAR_URL)       /car, car_status
```

- **TeslaMate** (`core/compose.tesla.yml`, pinned 4.4.0) talks to Tesla and logs everything. Its own page is on `http://skippy-core:4000`,
  tailnet only (its page has no password; it is never on the Funnel). It comes with Grafana dashboards, but we don't run Grafana.
  The portal is the interface.
- **The car reader** (`core/car/car.py`) keeps TeslaMate's latest MQTT values and serves them as JSON (`/car`) with the readiness
  contract (`/health`: ready once a car is signed in). Asking it never wakes the car and never costs a Tesla API call.
- **skippy-pc's reporter** carries it as the `car` capability (`{ready, mqtt, cars, sign_in}`) in every home report.
- **The portal**: the **Car** tile (`/car`, `portal/car_web.py`): battery and charge limit, range in miles, charging, locks and doors,
  inside/outside °F, the named place, sentry mode. **Skippy's `car_status`** tool, so "how's the car?" works in the chat and by voice.
  Both are the Captain's alone (tool_policy `car`; no grant opens the page), because they include where the car is.

Set up on skippy-core: `sudo bash core/install/tesla.sh --apply` (it makes `/etc/skippy-core/tesla.env` with the encryption key and the
database password, serves :4000 and :7351 to the tailnet, sets `CORE_TESLA=1` and repairs). Check it: `sudo bash core/install/tesla.sh --check`.
Never make a new `tesla.env`: a new `ENCRYPTION_KEY` makes the stored tokens unreadable.

### The one step only the Captain can do: sign in to Tesla

TeslaMate asks for an **access token and a refresh token** from his Tesla account (it never sees his password). On a device on the tailnet:

1. Get the two tokens from a token app (TeslaMate's docs list them under "Tokens", e.g. "Auth app for Tesla" on iOS/Android),
   signing in to Tesla there.
2. Open `http://skippy-core:4000` (or tap the Car tile, which links there until a car reports) and paste both.
3. Within a minute the Car tile shows the car. TeslaMate refreshes the tokens itself from then on.

## Phase 2, saved for later: control (Tesla's Fleet API)

Commands (climate, locks, charge limit, frunk, horn, **media next/previous**) are signed commands through the Fleet API:

- Register an app at developer.tesla.com under **makerportal.live** (we own it), and serve the public key at
  `/.well-known/appspecific/com.tesla.3p.public-key.pem` from the portal.
- OAuth once (his sign-in), tokens in Secret Manager. Add the **virtual key** to the car (`https://tesla.com/_ak/makerportal.live`, then
  approve it on the car's screen).
- Pay per call, with Tesla's monthly credit for personal use (about $10 a month as of 2025: check the current pricing first). Read state
  stays with TeslaMate (free); the Fleet API is only for sending commands.

**The steering wheel and the browser:** Bilby's player already sets the Media Session actions (next, previous, play, pause), which is
all a web page can do. The Tesla browser appears not to pass the wheel's buttons to web pages, and the Fleet API won't fix that
directly: its `media_next_track` steers the car's own media player, not the browser. Two things to try in phase 2: watch for a Tesla
software update that routes the wheel to Media Session, and look at whether Fleet Telemetry reports wheel presses. If it does, the
portal could catch a press and skip Bilby's track itself.
