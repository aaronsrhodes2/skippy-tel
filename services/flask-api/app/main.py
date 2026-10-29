"""SkippyTel Flask API — entry point.

PC-side AI inference service. Phone (SkippyDroid) calls these endpoints
over Tailscale. See misc/SKIPPYTEL_BRIEF.md for the full contract.

Quarantine: this code does not import from SkippyDroid / SkippyAR /
SkippyGlassesMac / SkippyStarMap. Protocol details copied as comments
where load-bearing.
"""
from flask import Flask

from app.routes.health    import bp as health_bp
from app.routes.intent    import bp as intent_bp
from app.routes.battery   import bp as battery_bp
from app.routes.starmap   import bp as starmap_bp
from app.routes.translate import bp as translate_bp
from app.routes.image     import bp as image_bp
from app.routes.bilby         import bp as bilby_bp
from app.routes.bilby_proxy   import bp as bilby_proxy_bp
from app.routes.vault     import bp as vault_bp
from app.routes.ups       import bp as ups_bp
from app.routes.dropship  import bp as dropship_bp
from app.routes.services             import bp as services_bp
from app.routes.service_icons        import bp as service_icons_bp
from app.routes.service_registration import bp as service_registration_bp
from app.routes.music                import bp as music_bp
from app.routes.lyric_summaries      import bp as lyric_summaries_bp
from app.routes.telemetry            import bp as telemetry_bp, init_db as telemetry_init_db
from app.routes.status               import bp as status_bp
from app.routes.explain              import bp as explain_bp
from app.routes.keywords             import bp as keywords_bp
from app.routes.portal               import bp as portal_bp
from app.routes.geo                  import bp as geo_bp


def _start_ollama_warmup() -> None:
    """Fire a cheap Ollama request 8s after startup to pre-load the model.

    Runs in a daemon thread so it doesn't block the server from accepting
    requests. If Ollama isn't ready yet it will retry once after another 15s.
    """
    import logging, threading, time
    _log = logging.getLogger(__name__)

    def _warmup() -> None:
        for delay in (8, 15):
            time.sleep(delay)
            try:
                from app.clients import ollama  # noqa: PLC0415
                ollama.chat([{"role": "user", "content": "ping"}])
                _log.info("ollama warm-up complete")
                return
            except Exception as exc:
                _log.warning("ollama warm-up attempt failed: %s — will retry", exc)
        _log.warning("ollama warm-up gave up after 2 attempts")

    threading.Thread(target=_warmup, daemon=True, name="ollama-warmup").start()


def create_app() -> Flask:
    app = Flask(__name__)

    # 128 KB max body — large enough for any reasonable voice prompt, small
    # enough that a runaway client can't OOM the worker with a giant payload.
    app.config["MAX_CONTENT_LENGTH"] = 128 * 1024

    # Init SQLite DBs
    from app import photo_tags as _pt
    _pt.init_db()
    telemetry_init_db()
    from app import keyword_memory as _km
    _km.init_db()
    from app import conversation_store as _cs
    _cs.init_db()
    # DropShip is parked (the Captain, 2026-10-13: a failed project, to restart later with more planning):
    # its Drive scanner and the LLaVA tagger it fed are gone, so nothing polls Drive or loads a vision model.
    # The /dropship routes and the photo_tags store stay, dormant, for the restart.
    _start_ollama_warmup()

    from flask import g, request as _request
    from app.routes.health import READY as _READY

    @app.before_request
    def _job_begins():
        if _request.method == "POST":                      # a call that does work is a job while it runs (/health says how many)
            g._job = _READY.begin(_request.path)

    @app.teardown_request
    def _job_ends(_exc):
        token = g.pop("_job", None)
        if token:
            _READY.end(token)

    app.register_blueprint(health_bp)
    app.register_blueprint(intent_bp)
    app.register_blueprint(battery_bp)
    app.register_blueprint(starmap_bp)
    app.register_blueprint(translate_bp)
    app.register_blueprint(image_bp)
    app.register_blueprint(bilby_bp)
    app.register_blueprint(bilby_proxy_bp)
    app.register_blueprint(vault_bp)
    app.register_blueprint(ups_bp)
    app.register_blueprint(dropship_bp)
    app.register_blueprint(services_bp)
    app.register_blueprint(service_icons_bp)
    app.register_blueprint(service_registration_bp)
    app.register_blueprint(music_bp)
    app.register_blueprint(lyric_summaries_bp)
    app.register_blueprint(telemetry_bp)
    app.register_blueprint(status_bp)
    app.register_blueprint(explain_bp)
    app.register_blueprint(keywords_bp)
    app.register_blueprint(portal_bp)
    app.register_blueprint(geo_bp)
    return app


app = create_app()
