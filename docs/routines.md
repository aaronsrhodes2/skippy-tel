# Skippy's daily routines (morning, coming home, bedtime, the night shift)

Code: `services/satellite/daily.py` (the routines, inside the hub), `services/satellite/stereo.py` (Bilby on the house stereo),
`services/satellite/google_home.py` (his calendar, mail, contacts, to-dos: read only), `tools/nightly.py` (the night shift).
Tests: `services/satellite/tests/test_sat_daily.py`, `test_sat_google_home.py`, `core/tests/test_routes.py`. State (not in git): `state/daily/`, `state/ledger/`.

## What he hears

**Morning**, every day at `SKIPPY_MORNING_AT` (07:10), only when he is home (the car is parked at home, or the house heard him since
last evening). An early first event tomorrow moves it earlier (90 min before it, never before 04:30). "Skippy, good morning" runs it by hand.
1. The lights come up (`SKIPPY_MORNING_LIGHTS`, default the Bedroom; a dimmable light ramps 5 → 50% over a minute).
2. Good morning, the weather and what to wear (Open-Meteo for home).
3. The news: major world news, new science (space, physics), AI, AR, software, each with its source (the house's SearXNG).
4. Today's calendar, then **when to leave** for the first off-site event, the mail that matters (bills, people in his contacts), birthdays,
   packages due today (once Google is connected).
5. The car: charge, range, plugged in, and today's miles against its range (a plain warning when the day needs more than it has).
6. A joke, a word of the day (its meaning read from the web), a science fact from the checked quip bank, the overnight report.
7. Bilby's library shuffled on the stereo (`SKIPPY_STEREO_OUT`, default `hdmi`: the downstairs projector/Sony output; `hdmi+bt` later).

**Coming home**: the car gets home after `SKIPPY_HOMECOMING_AWAY_H` (3) hours away, between 11:00 and 23:00. The kitchen lights
(`SKIPPY_HOMECOMING_LIGHTS`) come on at once, the outdoor ones (`SKIPPY_OUTDOOR_LIGHTS`) only after dark. When he is heard in the room
(or says "Skippy, I'm home"): the day's news, Bilby's shuffle, then Skippy asks about his day (the conversation stays open).

**Bedtime**, on "Skippy, goodnight" ("goodnight at 11" asks at 11 first): the stereo stops, every light off, the car charging if it is
plugged in and below its limit (Bluetooth, `core/car-ble`), Sentry armed (`core/car-fleet`, `SKIPPY_BEDTIME_SENTRY=arm|off`), his day and
tomorrow's first event and to-dos, then "Anything on your mind before you sleep?".

**While one runs**: each part ends with "Next: …". In the pause after it, without the name: **"skip"** drops that next part, **"later"** /
**"snooze"** puts the rest off ten minutes, **"stop"** ends it. Anything else is answered as usual and the routine waits until he is done.
When Core is down, each part falls back to the plain sourced facts instead of going silent.

**Night shift** (`tools/nightly.py`, started by the hub at `SKIPPY_NIGHTLY_AT` 01:30 once the house has been quiet 20 minutes): main vs CI vs
live (a commit CI passed that still isn't live an hour later is deployed again; nothing is ever reverted here, because skippy-core's deployer
already sends a failed deploy's traffic back), the night's scheduled tasks, disks, Docker's dangling images and week-old build cache, the hub
restarted onto new code (only after 30 quiet minutes), the vault. It writes `state/ledger/<date>.md` and the one line the morning reads out.

By hand, from skippy-pc:

```bash
services/satellite/.venv/Scripts/python services/satellite/daily.py run morning
```

```bash
services/satellite/.venv/Scripts/python tools/nightly.py --dry-run
```

## Leave-by and today's miles (no cloud: roads on skippy-core)

The morning works out today's driving from the calendar, on skippy-core's own map (`core/routes`: Valhalla and Nominatim, OpenStreetMap's
California extract; `SKIPPY_ROUTES_URL`, default `http://skippy-core:7354`). Each timed event's location is looked up: online meetings
(a link, Zoom, Teams, "phone"...), places the map doesn't know (a room name) and places within 1 km of home are skipped; a location with
no comma is only looked for within ~100 km of home. The rest, in order, make the day's trip: home → each stop → home, one route.

- **Leave by**: the first off-site event's start, less the router's drive time, less `SKIPPY_LEAVE_BUFFER_MIN` (10), down to five minutes.
  "Dentist at 10 AM is at Smile Dental, about 32 minutes and 15 miles by road, before traffic. Leave by 9:15 AM." Times have no live
  traffic. When only the town could be found, it says so.
- **Today's miles** (`extras["miles_today"]`): the whole trip's km in miles; the car part compares it with TeslaMate's range (more than
  the range: a plain warning; over 80% of it: "close").
- The router down, or no Google: nothing is said about either (never a guess). Worked out once a day.

Switching the roads on (once, on skippy-core): `sudo bash core/install/routes.sh --apply`, then `--check` (the first map import takes
about an hour; until then `/health` says what it is waiting on).

## Connecting his Google account (once, read only)

Nothing reads his password: Google's own sign-in page does that, and this PC keeps only a refresh token in the vault folder
(`G:\My Drive\vault\google-home-token.json`). Scopes: calendar, Gmail, contacts and tasks, all read-only. Mail is summed up by Core only.

1. In the Google Cloud console, project **skippy-tel**, open APIs & Services → Library and enable: **Google Calendar API**, **Gmail API**,
   **People API**, **Google Tasks API**.
2. APIs & Services → Credentials → Create credentials → OAuth client ID → Application type **Desktop app**, name "Skippy home" →
   Create → **Download JSON**, and save it as `G:\My Drive\vault\google-home-client.json`.
3. Consent screen: it must be **In production**, not Testing; in Testing Google expires the grant after 7 days. Because the app isn't
   verified, the sign-in shows "Google hasn't verified this app": choose Advanced → Go to Skippy.
4. Then (Skippy runs this; a browser tab opens, and he signs in with his personal account and allows):

```bash
services/satellite/.venv/Scripts/python services/satellite/google_home.py connect
```

```bash
services/satellite/.venv/Scripts/python services/satellite/google_home.py check
```

`check` lists his calendars and says whether the work calendar is shared in. Set `GOOGLE_WORK_CALENDAR=<work address>` in the vault .env
so it is recognised for certain.

### If the work calendar isn't shared into his personal one

Signed in to the **work** Google account, at calendar.google.com:
1. Settings (gear) → **Settings for my calendars** → pick the work calendar → **Share with specific people or groups** → **Add people and
   groups** → his personal Gmail address → permission **See all event details** → Send.
   (If only "See only free/busy" is offered, the company's admin limits outside sharing; Skippy then sees busy blocks without titles.)
2. In the **personal** account, open the email "… has shared a calendar with you" and click **Add this calendar**.
3. Skippy's `check` then shows it as shared in, and the morning reads work meetings too.

## Not done yet

- Live traffic in the leave-by time (the router has none), and the work office as a stop for work meetings in a room with no address.
- Doors, plugs, the Nest and pre-conditioning the car at night: the house shows no door sensors or thermostat right now (Matter's Nest
  isn't listed), and car-ble has no climate command.
- The bridge front-door switch-over, and wake-word retraining from the day's recordings: each is its own piece of work.

(Chat archiving is done, on the portal's side: once a night, nudged by the home report, every chat untouched for 14 days is zipped to
`chat-archive/<owner>/<id>.zip` in the Tabletop bucket and leaves a stub in the chats list; opening it restores it. `portal/chat_history.py`.)
