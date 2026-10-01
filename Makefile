.PHONY: run stop logs build test pull-model health intent apks phone-check adb-connect adb-devices ups-monitor ups-status push-all memory-init memory-ingest memory-inbox memory-distill games-up games-down games-status games-env

# ── Two-machine sync ─────────────────────────────────────────────────────────
# Single developer, two stations (PC + Mac).
# Doctrine: push after every session so the other machine is always current.
#   make push-all   → push SkippyTel, zPhone, and bilby to GitHub in one shot
#   On the other machine, start each session with:
#     git -C <repo> pull --rebase
# Mac owns bilby/ (stage9_dj_suggest.py). PC owns SkippyTel/ + zPhone/.
# Neither machine force-pushes main. That's the entire policy.
ZPHONE_REPO   ?= D:/Aaron/development/zPhone
BILBY_REPO    ?= D:/Aaron/development/music-collection
push-all:
	git push origin main
	git -C "$(ZPHONE_REPO)" push origin main
	git -C "$(BILBY_REPO)"  push origin main 2>/dev/null || \
		echo "(bilby: nothing to push or not reachable)"
	@echo "All repos pushed."

run:
	docker compose up -d --build

# Primary deploy target — rebuild and restart only flask-api (fast path).
# Claude Code calls this after any Python code change.
deploy-api:
	docker compose up --build -d flask-api
	@echo "Verifying..."
	@sleep 3
	@curl -s http://localhost:3003/health || echo "WARNING: health check failed"

stop:
	docker compose down

logs:
	docker compose logs -f flask-api

build:
	docker compose build

test:
	python -m pytest memory/tests -q

# Pull the default Ollama model into the running ollama container.
# First boot only; OLLAMA_KEEP_ALIVE=-1 keeps it warm afterwards.
pull-model:
	docker compose exec ollama ollama pull qwen2.5:7b

# Smoke tests — run after `make run`
health:
	curl -s http://localhost:3003/health | jq .

intent:
	curl -s -X POST http://localhost:3003/intent/unmatched \
		-H "Content-Type: application/json" \
		-d '{"text":"what time is it on Mars right now"}' | jq .

intent-stream:
	curl -N -X POST http://localhost:3003/intent/unmatched \
		-H "Content-Type: application/json" \
		-H "Accept: text/event-stream" \
		-d '{"text":"put on your thinking cap and explain dark matter in two sentences"}'

# Translate test — generate 3s of synthesized speech with espeak via ffmpeg
# inside the whisper container, pipe back, then POST to /translate/audio.
# Requires the stack to be running.
translate:
	@docker compose exec -T whisper sh -c "apt-get install -y espeak-ng >/dev/null 2>&1; \
		espeak-ng -v fr 'Bonjour, comment allez-vous aujourd hui' --stdout > /tmp/test.wav 2>/dev/null; \
		cat /tmp/test.wav" > /tmp/skippy-test.wav 2>/dev/null && \
	curl -s -F "audio=@/tmp/skippy-test.wav" -F "hint_lang=fr" \
		http://localhost:3003/translate/audio | jq .

# Phone recovery — copy latest debug APKs from Android builds to apks/ staging.
# Run after `gradlew assembleDebug` in the zPhone repo.
# Container picks up new files immediately (bind mount, no restart needed).
ZPHONE_DIR ?= D:/Aaron/development/zPhone/apps
apks:
	@mkdir -p apks
	cp "$(ZPHONE_DIR)/SkippyChat/app/build/outputs/apk/debug/app-debug.apk"  apks/skippy-chat.apk
	cp "$(ZPHONE_DIR)/SkippyDroid/app/build/outputs/apk/debug/app-debug.apk" apks/skippy-droid.apk
	@echo "APKs staged:"
	@ls -lh apks/

# ADB over Tailscale — reconnect to the physical S23.
# One-time MANUAL pairing (do this once when you first set up a phone):
#   1. On S23: Settings → Developer options → Wireless debugging → ON
#   2. Tap "Pair device with pairing code"
#   3. adb pair <ip>:<pairing-port>   ← enter the 6-digit code shown on screen
#   4. Fill in PHONE_TAILSCALE_HOST in .env
# After that, reconnecting is fully automatic:
PHONE_HOST ?= $(shell grep '^PHONE_TAILSCALE_HOST=' .env | cut -d= -f2)
adb-connect:
	@if [ -z "$(PHONE_HOST)" ]; then \
		echo "ERROR: Set PHONE_TAILSCALE_HOST in .env first"; exit 1; fi
	adb connect $(PHONE_HOST):5555
	@echo "Connected devices:"; adb devices

adb-devices:
	@adb devices

# Smoke-test the phone bootstrap endpoint (skippy core serves data only; the old /phone page is gone)
phone-check:
	@echo "=== /vault/bootstrap ==="; curl -s http://localhost:3003/vault/bootstrap | jq .

# solid-gold bridge — runs on the Windows HOST, not in docker. Shells out
# to your local `claude` CLI so SkippyTel can use your Claude Code
# subscription instead of the metered API key.
#
# Set CLAUDE_PATH if `claude` isn't in PATH:
#   make bridge CLAUDE_PATH=C:/Users/aaron/AppData/Roaming/npm/claude.cmd
bridge:
	cd services/claude-bridge && \
	CLAUDE_BRIDGE_CWD="D:/Aaron/development" \
	$(if $(CLAUDE_PATH),CLAUDE_PATH="$(CLAUDE_PATH)") \
	python bridge.py

# UPS monitor — runs on the Windows HOST (not in Docker).
# Reads CyberPower ABMT1500 via WMI, serves /status at port 9338,
# and pushes power-state alerts to SkippyTel.
# Requires: pip install flask requests
ups-monitor:
	python services/ups-monitor/monitor.py

ups-status:
	@curl -s http://localhost:9338/status | jq . 2>/dev/null || \
		powershell -Command "Invoke-RestMethod http://localhost:9338/status | ConvertTo-Json"

bridge-health:
	curl -s http://localhost:9337/health | jq .

# Smoke tests — require `make bridge` running in another terminal
solid-gold:
	curl -s -X POST http://localhost:3003/intent/unmatched \
		-H "Content-Type: application/json" \
		-d '{"text":"solid gold list the top-level files in the SkippyTel repo"}' | jq .

trust-awesomeness:
	curl -s -X POST http://localhost:3003/intent/unmatched \
		-H "Content-Type: application/json" \
		-d '{"text":"trust the awesomeness echo hello from awesomeness"}' | jq .

# Skippy Memory — every AI conversation in one Drive folder. See docs/memory/README.md.
#   make memory-inbox              import every export dropped in <MEMORY_DIR>/inbox/
#   make memory-inbox DISTILL=1    ...and tag topics + summaries with local Ollama
#   make memory-ingest EXPORT=C:/Users/aaron/Downloads/data-2026-09-28.zip
MEMORY_DIR ?= G:/My Drive/Skippy Memory
memory-init:
	python -m memory init --out "$(MEMORY_DIR)"

memory-ingest:
	@if [ -z "$(EXPORT)" ]; then echo "usage: make memory-ingest EXPORT=<zip|folder|json>"; exit 2; fi
	python -m memory ingest "$(EXPORT)" --out "$(MEMORY_DIR)" $(if $(DISTILL),--distill)

memory-inbox:
	python -m memory inbox --out "$(MEMORY_DIR)" $(if $(DISTILL),--distill)

memory-distill:
	python -m memory distill --out "$(MEMORY_DIR)"

# Pop-up game servers on GCP. See docs/games/README.md.
#   make games-up GAMES="valheim dragonwilds"   (IDLE=90 for a longer auto-sleep grace)
games-up:
	@if [ -z "$(GAMES)" ]; then echo 'usage: make games-up GAMES="valheim dragonwilds"'; exit 2; fi
	bash scripts/games.sh up $(GAMES) $(if $(IDLE),--idle $(IDLE))

games-down:
	bash scripts/games.sh down

games-status:
	bash scripts/games.sh status

games-env:
	bash scripts/games.sh env
