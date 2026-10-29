# voice-gate

Skippy's ears and mouth for the portal's chats, the same kind of door as `music-gate` and `video-gate`: the portal signs short-lived passes
(`portal/voice.py`), this container shows the internet two routes (`POST /t/<pass>/stt`, `POST /t/<pass>/tts`) and `/health`. The code's top
docstring (`gate.py`) is the authority on routes and scopes. A pass names its voice (`stock`, `skippy`): the portal signs
`stock` for everyone who may chat and `skippy` for the Captain alone (`portal/voice.py` `voice_for`). Both are
rendered at home by `services/tts-studio` (Kokoro-82M, F5-TTS) and streamed through a sentence at a time.

```bash
cd /d/Aaron/development/SkippyTel
docker compose stop voice-gate; docker compose rm -f voice-gate; docker compose up --build -d voice-gate
curl -s http://127.0.0.1:3007/health          # {"ok":true,"ready":true,"tts":true,"whisper":true}
tailscale funnel --https=10000 --bg 3007       # the internet door (Funnel offers 443, 8443 and 10000)
tailscale funnel --https=10000 off             # take it off again
python -m pytest services/voice-gate/tests -q  # in its own session: every gate has a gate.py
```

