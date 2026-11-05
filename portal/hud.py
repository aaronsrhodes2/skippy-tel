"""The glasses view (the Captain, 2026-11-02: "I mostly want the new skippy portal as the interface, but I also want the glasses to
work"). The Viture glasses are a USB-C DisplayPort screen on the S23, and black is see-through on them, so the glasses view is a
portal page: `/hud`, Drive's page in its hud mode: the same keys, purple Skippy (the agent on skippy-pc, the full power) behind the
voice key instead of the house brain, a hub mode that hides every key and is hands-off like the pods, and the apps one key away. OpenViture (D:\\Aaron\\development\\viture-android) will host it on the
glasses and feed it the glasses' camera and head pose, the parts a page can't reach.

First, `/hud/test`: one screen, opened on the S23 with the glasses plugged in, that says what the browser there can do (the screen it
sees, a second screen under DeX, full screen, keep-awake, the camera, motion, battery). The Captain's only; nothing is sent anywhere,
the report is copied by hand. docs/ROADMAP.md: "First step then: a 5-minute capability test page"."""
from __future__ import annotations

from flask import Blueprint, render_template

import auth
import drive as drive_mod


def init_app(app) -> None:
    bp = Blueprint("hud", __name__)

    @bp.get("/hud")
    @auth.admin_required
    def page():
        """The glasses view (and the phone's): Drive's keys, Skippy's face, purple Skippy as the brain behind the voice, a hands-off
        hub mode like the pods, and the apps one key away. Drive's own page and script, in its hud mode (templates/drive.html)."""
        return render_template("hud.html", lamps=drive_mod.LAMPS, hud=True, spotify_in_car=drive_mod.SPOTIFY_IN_CAR)

    @bp.get("/hud/test")
    @auth.admin_required
    def test():
        return render_template("hud_test.html")

    app.register_blueprint(bp)
