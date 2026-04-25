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
from app.routes.heybuddy  import bp as heybuddy_bp
from app.routes.image     import bp as image_bp
from app.routes.bilby     import bp as bilby_bp
from app.routes.vault     import bp as vault_bp
from app.routes.ups       import bp as ups_bp
from app.routes.dropship  import bp as dropship_bp
from app.routes.services      import bp as services_bp
from app.routes.service_icons import bp as service_icons_bp


def create_app() -> Flask:
    app = Flask(__name__)
    app.register_blueprint(health_bp)
    app.register_blueprint(intent_bp)
    app.register_blueprint(battery_bp)
    app.register_blueprint(starmap_bp)
    app.register_blueprint(translate_bp)
    app.register_blueprint(heybuddy_bp)
    app.register_blueprint(image_bp)
    app.register_blueprint(bilby_bp)
    app.register_blueprint(vault_bp)
    app.register_blueprint(ups_bp)
    app.register_blueprint(dropship_bp)
    app.register_blueprint(services_bp)
    app.register_blueprint(service_icons_bp)
    return app


app = create_app()
