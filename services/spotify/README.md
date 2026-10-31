# The Captain's Spotify at home

skippy-pc is a Spotify Connect speaker called **Skippy**, and the house plays his Spotify (Premium) and his own library (Bilby) through the same audio
path: the hub's router (`services/satellite/router.py`) out to the Sony over HDMI (and any other PC output `SKIPPY_STEREO_OUT` names), on the
`media` layer, ducked under Skippy's voice.

```
Spotify ── go-librespot (pipe, f32le 44.1 kHz) ── FIFO ── relay :43872 ──TCP── hub: ffmpeg → 48 kHz → router.PipeSource → Sony
            ▲ its API (localhost:43871)                                         ▲
            └──── service.py :43873 (his Web API sign-in, search, playlists) ───┴── hub voice tools, purple's `spotify` tool
```

- `service.py` (this container): starts and keeps up go-librespot (github.com/devgianlu/go-librespot, unofficial, Premium only; image pinned in
  the Dockerfile), relays its FIFO to one TCP client unaltered, and answers the API (endpoints in its docstring). `/health` is the readiness
  contract (`ready.py`): ready only when both the Web API sign-in and the speaker's login work; while the speaker waits it carries `pair`.
- `signin.py` (on skippy-pc, standard library): the one-time sign-in. Claude runs it; the Captain clicks.
- `services/satellite/spotify.py`: the hub's client and the ffmpeg resample. `stereo.py` plays a mixed queue (Bilby paths and `spotify:track:`
  URIs in one `music.BoxMusic`), one Spotify stream across consecutive Spotify tracks and a plain cut where Bilby takes over: no crossfade on
  Spotify's audio. A play started from his phone (picking the Skippy speaker) is followed onto the stereo the same way.

## Voice (at a pod)

"Skippy, play Daft Punk on Spotify" · "shuffle my Spotify Gym playlist" · "play my liked songs on Spotify" · "queue some Pink Floyd after this"
(Bilby's tracks after the Spotify ones, in the stereo's one queue) · "add this song to my Spotify playlist Gym" (a Bilby song is looked up on
Spotify) · "save this on Spotify" · "add this song to my favorites" (a Spotify track is kept by its URI; the Traktor `.m3u` shows it as a comment
line). "Next", "pause", "stop the music" reach the stereo when it is what plays. Spotify always plays on the house stereo, never a pod's own speaker
(a pod's queue skips Spotify items).

## The one-time sign-in

```bash
python services/spotify/signin.py
```

It opens `http://127.0.0.1:8888/` in skippy-pc's browser:

1. **The Web API**: his own Spotify app. If the vault has no `SPOTIFY_CLIENT_ID`, the page says how to make one at developer.spotify.com (the music
   organizer's app on the Mac is fine if it is his) and to add the Redirect URI `http://127.0.0.1:8888/callback`; he pastes its Client ID (not a
   secret). Then Spotify's own consent page (authorization code + PKCE; his password never comes here). `SPOTIFY_CLIENT_ID` and
   `SPOTIFY_REFRESH_TOKEN` go into the vault `.env`, and straight to the container. Spotify hands a new refresh token on refresh: the container
   keeps the current one in `state/spotify/token.json` (a new one in the vault, from a fresh sign-in, wins).
2. **The speaker**: the page shows go-librespot's pairing code and the `spotify.com/pair` link; its login is kept in `state/spotify/librespot/`.

Development Mode (February 2026 rules): the app owner needs Premium; 5 users; search pages of at most 10 (the service pages with `offset`); no
artist top tracks (an artist's songs come from a search); playlist items only for his own playlists (`/playlists/{id}/items`); saving is
`PUT /me/library`.

## Run it

```bash
docker compose up --build -d spotify
curl -s 127.0.0.1:43873/health
```

The hub needs no setting (`SPOTIFY_URL`, `SPOTIFY_PCM` default to the ports above; `SKIPPY_SPOTIFY=off` turns it off). Tests:
`cd services/spotify && python -m pytest tests -q` (the relay's test needs Linux: CI runs it), and `services/satellite/tests/test_sat_spotify.py`.
