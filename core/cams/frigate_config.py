#!/usr/bin/env python3
"""Frigate's config.yml for the Captain's cameras (2026-11-01: Reolink PoE, no vendor account, no cloud; Ring is off the list).

    python3 core/cams/frigate_config.py /etc/skippy-core/cams.json > ${CORE_STATE}/frigate/config/config.yml

cams.json (root, 600, written by core/install/cams.sh): {"cameras": {"front_door": {"host": "192.168.1.60", "doorbell": true}, ...}}.
Camera addresses only: the cameras' one password is FRIGATE_REOLINK_PASSWORD in the box's vault.env, which Frigate and its go2rtc read from
the container's environment ({FRIGATE_REOLINK_PASSWORD}), so it never lands in the config file. User: REOLINK_USER in cams.json, default admin.

What it sets, per Frigate 0.18's own docs (configuration/camera_specific.md → Reolink, object_detectors.md → OpenVINO):
  * each camera through go2rtc as http-flv (Reolink's steadiest stream): the main stream records, the sub stream is detected on;
  * detection on the CPU with OpenVINO and the model inside the image (ssdlite MobileNet v2): skippy-core's GPU stays the models';
    decoding on the Radeon through VAAPI (LIBVA_DRIVER_NAME=radeonsi in compose.cams.yml);
  * people and cars tracked; snapshots kept (the hub DMs one per announcement); recording only around what was seen, 7 days;
  * no MQTT and no Frigate login: the hub reads Frigate's own API on 5000, which compose publishes on 127.0.0.1 and install/cams.sh
    serves to the tailnet only (:7355), never the Funnel.
"""
from __future__ import annotations

import json
import re
import sys

NAME = re.compile(r"[a-z][a-z0-9_]{0,30}")
HOST = re.compile(r"[A-Za-z0-9.\-]{1,253}")
USER = re.compile(r"[A-Za-z0-9_.\-]{1,32}")


def check(cams: dict) -> dict[str, dict]:
    """The cameras, checked: a bad name or address would land in a URL inside Frigate's config."""
    cameras = cams.get("cameras") if isinstance(cams, dict) else None
    if not isinstance(cameras, dict) or not cameras:
        raise ValueError("cams.json has no cameras")
    out = {}
    for name, c in cameras.items():
        if not NAME.fullmatch(str(name)):
            raise ValueError(f"camera name {name!r}: lower case letters, digits and _ only")
        if not isinstance(c, dict) or not HOST.fullmatch(str(c.get("host", ""))):
            raise ValueError(f"camera {name}: a host (an address or a name) is needed")
        out[name] = {"host": c["host"], "doorbell": bool(c.get("doorbell"))}
    return out


def stream(host: str, user: str, which: str) -> str:
    url = f"http://{host}/flv?port=1935&app=bcs&stream=channel0_{which}.bcs&user={user}&password={{FRIGATE_REOLINK_PASSWORD}}"
    return f"ffmpeg:{url}#video=copy#audio=copy#audio=opus" if which == "main" else f"ffmpeg:{url}"


def render(cams: dict) -> str:
    cameras = check(cams)
    user = str(cams.get("REOLINK_USER") or "admin")
    if not USER.fullmatch(user):
        raise ValueError("REOLINK_USER: letters, digits, _ . - only")
    lines = [
        "# Made by core/cams/frigate_config.py from /etc/skippy-core/cams.json (core/install/cams.sh): change the cameras there, not here.",
        "mqtt:",
        "  enabled: false",
        "auth:",
        "  enabled: false            # the API is tailnet-only (tailscale serve :7355); no login between his own devices",
        "detectors:",
        "  ov:",
        "    type: openvino",
        "    device: CPU",
        "model:",
        "  width: 300",
        "  height: 300",
        "  input_tensor: nhwc",
        "  input_pixel_format: bgr",
        "  path: /openvino-model/ssdlite_mobilenet_v2.xml",
        "  labelmap_path: /openvino-model/coco_91cl_bkgr.txt",
        "ffmpeg:",
        "  hwaccel_args: preset-vaapi",
        "objects:",
        "  track: [person, car]",
        "snapshots:",
        "  enabled: true",
        "  bounding_box: false",
        "  retain:",
        "    default: 14",
        "record:",
        "  enabled: true",
        "  continuous:",
        "    days: 0",
        "  motion:",
        "    days: 2",
        "  alerts:",
        "    retain:",
        "      days: 7",
        "      mode: motion",
        "  detections:",
        "    retain:",
        "      days: 7",
        "      mode: motion",
        "go2rtc:",
        "  streams:",
    ]
    for name, c in cameras.items():
        lines += [f"    {name}:", f'      - "{stream(c["host"], user, "main")}"',
                  f"    {name}_sub:", f'      - "{stream(c["host"], user, "ext")}"']
    lines.append("cameras:")
    for name in cameras:
        lines += [f"  {name}:",
                  "    ffmpeg:",
                  "      output_args:",
                  "        record: preset-record-generic-audio-copy",
                  "      inputs:",
                  f"        - path: rtsp://127.0.0.1:8554/{name}",
                  "          input_args: preset-rtsp-restream",
                  "          roles: [record]",
                  f"        - path: rtsp://127.0.0.1:8554/{name}_sub",
                  "          input_args: preset-rtsp-restream",
                  "          roles: [detect]",
                  "    detect:",
                  "      enabled: true",
                  "      width: 640",
                  "      height: 480",
                  "      fps: 5"]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: frigate_config.py /etc/skippy-core/cams.json", file=sys.stderr)
        return 2
    try:
        with open(argv[0], encoding="utf-8") as f:
            sys.stdout.write(render(json.load(f)))
    except (OSError, ValueError) as e:
        print(f"frigate_config: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
