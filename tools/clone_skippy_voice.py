#!/usr/bin/env python3
"""
clone_skippy_voice.py — Upload a voice sample to ElevenLabs Instant Voice Clone.

Usage:
    python tools/clone_skippy_voice.py --key <YOUR_API_KEY> [--file state/skippy-voice-training.m4a]

What it does:
    1. Uploads the audio file to ElevenLabs as a new cloned voice
    2. Prints the new voice_id
    3. Tells you exactly what to add to .env

Attribution:
    Character: Skippy the Magnificent © Craig Alanson (Expeditionary Force)
    Narration style: R.C. Brey (audiobook narrator)
    This cloned voice is for PERSONAL USE ONLY. Obtain permission from both
    Craig Alanson and R.C. Brey before any distribution or public release.

ElevenLabs voice quality tips:
    - 1-5 minutes of clean speech is ideal for Instant Voice Clone
    - No background music, sound effects, or room echo
    - The sample should include a range of emotions/tones
    - More varied expressiveness = better clone of Skippy's dramatic delivery
"""
import argparse
import os
import sys
import requests

DEFAULT_AUDIO = os.path.join(
    os.path.dirname(__file__), "..", "state", "skippy-voice-training.m4a"
)

ELEVENLABS_VOICES_URL = "https://api.elevenlabs.io/v1/voices/add"


def clone_voice(api_key: str, audio_path: str, name: str = "Skippy the Magnificent") -> str:
    """Upload audio to ElevenLabs IVC. Returns voice_id."""
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
    print(f"Uploading: {audio_path} ({file_size_mb:.1f} MB)")

    # Detect MIME type from extension
    ext = os.path.splitext(audio_path)[1].lower()
    mime = {
        ".m4a":  "audio/mp4",
        ".mp3":  "audio/mpeg",
        ".wav":  "audio/wav",
        ".flac": "audio/flac",
        ".ogg":  "audio/ogg",
        ".aac":  "audio/aac",
    }.get(ext, "audio/mpeg")

    with open(audio_path, "rb") as f:
        response = requests.post(
            ELEVENLABS_VOICES_URL,
            headers={"xi-api-key": api_key},
            files={"files": (os.path.basename(audio_path), f, mime)},
            data={
                "name": name,
                "description": (
                    "Skippy the Magnificent — R.C. Brey narration style. "
                    "Personal use only. © Craig Alanson / R.C. Brey."
                ),
                "labels": '{"use":"personal","character":"skippy","narrator":"rc_brey"}',
            },
            timeout=120,
        )

    if response.status_code not in (200, 201):
        print(f"\nERROR {response.status_code}: {response.text}")
        sys.exit(1)

    data = response.json()
    return data["voice_id"]


def main():
    parser = argparse.ArgumentParser(description="Clone Skippy's voice on ElevenLabs")
    parser.add_argument("--key",  required=True, help="ElevenLabs API key")
    parser.add_argument("--file", default=DEFAULT_AUDIO, help="Path to audio file")
    parser.add_argument("--name", default="Skippy the Magnificent", help="Voice name on ElevenLabs")
    args = parser.parse_args()

    print("=" * 60)
    print("  ElevenLabs Instant Voice Clone — Skippy the Magnificent")
    print("=" * 60)
    print(f"\n  Audio: {os.path.abspath(args.file)}")
    print(f"  Voice: {args.name}")
    print()

    voice_id = clone_voice(args.key, args.file, args.name)

    print(f"\n✓ Voice cloned successfully!")
    print(f"\n  Voice ID: {voice_id}")
    print()
    print("  Add these two lines to SkippyTel/.env:")
    print()
    print(f"    ELEVENLABS_API_KEY={args.key}")
    print(f"    ELEVENLABS_VOICE_ID={voice_id}")
    print()
    print("  Then redeploy SkippyTel:")
    print("    docker compose up -d flask-api")
    print()
    print("  Verify:")
    print("    curl 'http://localhost:3003/tts/health'")
    print()
    print("  Test a sample:")
    print("    curl -o /tmp/test.mp3 'http://localhost:3003/tts/stream?text=Oh+for...+fine.'")
    print("    # (open test.mp3 to hear Skippy)")
    print()


if __name__ == "__main__":
    main()
