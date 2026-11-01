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

**Signed out by Tesla** (2026-10-30: "user session flushed", and a day of drives went unlogged while the Car tile showed the last values):
the car reader asks TeslaMate's page every minute, and a `/` that redirects to `/sign_in` means signed out. Then the reader's `/health` is
not ready (`signed_in: false` in `/car`), the reporter's `car` capability is not ready, the Car tile says so with the sign-in link,
`car_status` tells Skippy the values are old, and the portal DMs the Captain once per episode (`car_web.sign_in_news`). The fix is
his alone: fresh tokens pasted at `http://skippy-core:4000`.

### The one step only the Captain can do: sign in to Tesla

TeslaMate asks for an **access token and a refresh token** from his Tesla account (it never sees his password). On a device on the tailnet:

1. Get the two tokens from a token app (TeslaMate's docs list them under "Tokens", e.g. "Auth app for Tesla" on iOS/Android),
   signing in to Tesla there.
2. Open `http://skippy-core:4000` (or tap the Car tile, which links there until a car reports) and paste both.
3. Within a minute the Car tile shows the car. TeslaMate refreshes the tokens itself from then on.

## Charging on and off: Bluetooth, from skippy-core (2026-10-30)

skippy-core is parked close enough to hear the car, so charging is switched over Bluetooth: no Tesla server, no fee.

```
purple Skippy ── car_charge (services/purple-tools) ── :7352 car-ble (core/car-ble, tailnet only) ── tesla-control ── Bluetooth ── car
```

- **car-ble** (`core/car-ble/ble.py`, in `compose.tesla.yml`): Tesla's own `tesla-control` (teslamotors/vehicle-command, pinned v0.4.1, built
  into the image) behind `POST /charge {"action": "start"|"stop"|"limit", "percent"}`. Host networking + NET_ADMIN/NET_RAW (the radio is the
  host's); listens on 127.0.0.1 only. One command at a time; a sleeping car is woken once and asked again. It shares the radio with
  bluetoothd (Matter's device setup): tested, both work.
- **The key**: `/etc/skippy-core/tesla-ble/private.pem` (made by `tesla.sh --apply`, never leaves the box), enrolled as
  **charging_manager**, so the car takes nothing from it but charging. `vin.env` holds the VIN (read from TeslaMate).
- **Pairing, once (the Captain, at the car):** `sudo bash core/install/tesla.sh --pair`, then tap the key card on the centre console and
  approve it on the screen.
- **purple Skippy's `car_charge`**: start, stop, limit, status; after a command it reads the state back from the car reader.
- Out of Bluetooth range (away from home) it can't reach the car: that is what the Fleet API below would add.

## His normal routes trim the traffic alerts (2026-10-31)

The car reader's `GET /routes` (core/car) reads TeslaMate's drive log: every ~100 m square the car drove through on at least two separate drives
in the last 180 days. skippy-pc's traffic check (`services/home-reporter/localinfo.py`, `CAR_URL`) keeps only CHP incidents within ~300 m of one,
on any road, so the Discord traffic DM and "how's the commute" speak only of his roads. With no drive log (the reader down, or nothing logged
yet) it falls back to the old box and highway list. The squares stay on the tailnet and skippy-pc; they never go to the portal.

## The car as a guard: Sentry, live (2026-10-31)

The Captain: "operate my car as an external security device". When someone comes near the parked car, or touches it, he hears about it at
once, and the car answers the alarm itself.

```
car ──(its own mutual TLS, from anywhere)── telemetry.makerportal.live:7443 ── home router ── fleet-telemetry (:7443) ── MQTT ── car-fleet (:7353) ── reporter ── portal
                                                                                                    │                          (DM, Car tile)
                                                                         tesla-http-proxy ◄─────────┘ on the alarm: flash the lights, lock
```

- **What the car streams:** only `SentryMode` (Tesla's states: off, idle, armed, **aware** = someone came close and the cameras are
  recording, **panic** = a threat, touched or leaned on, the car's own alarm, quiet). Every streamed signal is billed; this one changes rarely.
- **fleet-telemetry** (Tesla's server, pinned v0.9.5, `core/car-fleet/telemetry.json`) on :7443, so the car's mutual TLS ends at
  Tesla's own server. **Not the Funnel:** Tesla takes only a streaming name under the app's domain ("hostname domain does not match with
  partner account"), and the Funnel routes by its own ts.net names. So the home router forwards TCP 7443 to skippy-core (192.168.0.242),
  and skippy-core's deployer keeps `telemetry.makerportal.live`'s A record on home's public IP (`cd.py home_dns`: checked every 5 minutes,
  two IP services must agree, Cloud DNS touched only when it moved; `deploy.json` → `home_dns`; by hand: `cd.py home-dns`). Its
  certificate is ours (`/etc/skippy-core/tesla-fleet/telemetry/`: a CA made once by `tesla.sh --apply` and kept, the CA goes to the car
  in the signed config; the server certificate names telemetry.makerportal.live).
- **car-fleet** (`core/car-fleet/fleet.py`): keeps the state and the last 20 AWARE / PANIC events; on PANIC it flashes the lights and locks the
  doors, at most once a minute (**never the horn**: the Captain). Commands are signed by Tesla's tesla-http-proxy (built pinned into the image)
  with the app's key; his tokens (`tokens.json`, from `tools/tesla_fleet.py exchange`) are refreshed in place.
  `POST /sentry arm|disarm` (wakes the car once), `POST /command flash|lock` (to test), `POST /telemetry push|status`.
- **The Captain hears it:** the reporter carries the events (`CAR_FLEET_URL`), the portal DMs him on Discord once per event
  (`car_web.sentry_news`; "ALARM:" for panic), the Car tile's Sentry control says "someone near" / "alarm", `car_status` includes them, and
  purple Skippy's `car_sentry` arms, disarms and reports.
- **What Tesla doesn't allow any app:** the cameras' pictures (Sentry's clips stay on the car's USB drive) and the cabin lights. The alarm's
  own flashing, sound and screen warning are the car's.
- **Set up, once:** the virtual key on the car (`https://tesla.com/_ak/makerportal.live`, then approve on the car's screen), then
  `curl -s -X POST http://skippy-core:7353/telemetry -d '{"action":"push"}'` and wait for `status` to say `synced`. car-fleet pushes it again
  before it runs out (Tesla caps a config at a year).

## Phase 2, saved for later: control from anywhere (Tesla's Fleet API)

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
