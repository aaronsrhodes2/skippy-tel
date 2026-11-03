"""Blocking work, off the gevent event loop.

gunicorn runs this app on gevent workers (the Dockerfile): one worker is one thread running one event loop, and a synchronous call
that does not yield (SQLite is a C library) freezes EVERY request on that worker until it returns. That is how flask-api went
"unhealthy" on 2026-11-02: the telemetry database sits on a Docker bind mount of the Windows D: drive, where the dashboard summary
took 14 s (0.1 s on the container's own disk), the home reporter asked for it every 15 s, and /health, /services and /status.json
queued behind it for minutes. Docker's 5 s health check failed 45 times in a row, the doctor (portal/doctor.py) recreated the container
three times an hour, and the Captain got a Discord DM each hour.

`off_loop(fn, *args)` runs `fn` in a real thread from gevent's thread pool. The calling greenlet waits, the loop keeps serving every
other request, and SQLite lets go of the GIL while it reads.

What may run there: plain blocking work that makes its own connection and touches no shared state. Not a gevent lock (the patched
`threading.Lock`), not a socket or object made in another greenlet: those belong to the thread that made them.
"""
from __future__ import annotations


def off_loop(fn, *args, **kwargs):
    """fn(*args, **kwargs), in a thread of gevent's pool when gevent is there (the greenlet waits; the loop serves others);
    called directly when it is not (a plain test run). Exceptions come back out as they went in."""
    try:
        from gevent import get_hub
    except ImportError:
        return fn(*args, **kwargs)
    return get_hub().threadpool.apply(fn, args, kwargs)
