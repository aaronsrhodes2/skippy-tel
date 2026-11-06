# voice-gate

Skippy's ears and mouth for the portal's chats, the same kind of door as `music-gate` and `video-gate`: the portal signs short-lived passes
(`portal/voice.py`), this container shows the internet two routes (`POST /t/<pass>/stt`, `POST /t/<pass>/tts`) and `/health`. The code's top
docstring (`gate.py`) is the authority on routes and scopes. A pass names its voice (`stock`, `skippy`): the portal signs
`stock` for everyone who may chat and `skippy` for the Captain alone (`portal/voice.py` `voice_for`). Both are
rendered at home by `services/tts-studio` (Kokoro-82M, F5-TTS). The reply is sent once it is whole (the hub and the pages read it whole), and a line already said
comes from `state/voice-gate/tts-cache` (300 lines, least recently used out). That cache is bookkept off the reply's path: it used to stat() all 300 files on the
Windows bind mount after every render, 319 ms median before the first byte of a new line (2026-11-05); now a store is one write in a background thread. A file is
written whole or not there (its own temp file, then `os.replace`: a reader never meets half a line, two stores of the same words at once never share a temp file, a store that
dies takes its temp file with it). Where a file a reader has open cannot be unlinked (Windows: WinError 32), a line that left the cache but whose file would not go is
tried again at each of the next 3 stores (`DOOMED_TRIES`), then left for a restart with one warning in the log (a line said again meanwhile is left alone); a burst of stores ends at the cap, and a store that fails (Windows refuses a replace over a file another store is replacing) puts its name back on the list.
`"whole": true` in the /tts body (or `TTS_WHOLE=1` in the gate's environment for every request) asks the studio to render a reply that fits one F5 batch in ONE
run instead of one per sentence: 0.8 to 1.1 s faster for 2 to 3 sentences, and the hub then skips its ffmpeg re-encode of joined pieces. The service's own default is off; the house hub asks for it on every reply (`services/satellite/voicegate.py`, `Gate.tts` sends `"whole": true`; one line there turns it off), and the Captain's ear decides whether it stays.

```bash
cd /d/Aaron/development/SkippyTel
docker compose stop voice-gate; docker compose rm -f voice-gate; docker compose up --build -d voice-gate
curl -s http://127.0.0.1:3007/health          # {"ok":true,"ready":true,"tts":true,"whisper":true}
tailscale funnel --https=10000 --bg 3007       # the internet door (Funnel offers 443, 8443 and 10000)
tailscale funnel --https=10000 off             # take it off again
python -m pytest services/voice-gate/tests -q  # in its own session: every gate has a gate.py
```

