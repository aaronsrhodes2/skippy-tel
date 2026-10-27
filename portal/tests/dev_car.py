"""Look at the Car app in a real browser with a made-up car: `python portal/tests/dev_car.py`, then open http://127.0.0.1:8766/dev-login.

A real portal app on the tests' fake services, signed in as the Captain by /dev-login. `?car=none` shows the not-signed-in screen.
Nothing here touches the real car.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]

from test_app import ADMIN, SETTINGS, FakeSkippy, FakeVm  # noqa: E402
from test_car import SIGNED_IN  # noqa: E402

import app as portal  # noqa: E402


def main(port: int = 8766) -> None:
    app = portal.create_app(dict(SETTINGS), vm=FakeVm(), skippy=FakeSkippy())
    home = app.extensions["home"]

    @app.get("/dev-login")
    def dev_login():
        from flask import redirect, request, session
        session["user"] = {"email": ADMIN, "name": "Captain", "picture": None}
        car = {"ready": False, "mqtt": True, "cars": [], "sign_in": "http://skippy-core:4000"} if request.args.get("car") == "none" else SIGNED_IN
        home.accept({"capabilities": {"car": car}})
        return redirect("/car")
    print(f"Car app: http://127.0.0.1:{port}/dev-login   (stop with Ctrl-C)", flush=True)
    app.run(host="127.0.0.1", port=port)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8766)
