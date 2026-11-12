# edge: the direct front door

Caddy in front of the five gates, answering on `https://home.makerportal.live` (the router forwards external TCP 443 to this PC's 47443, which is the
container's :443). Everything about what it is for, how to turn it on and off, how to check it and what can go wrong is in
[docs/direct-door.md](../docs/direct-door.md). This folder is the proxy itself:

| File | What |
|---|---|
| `Caddyfile` | the whole configuration, commented: `/ping`, `/music` `/video` `/books` `/voice` `/in` to their gates with the prefix cut off, 404 for the rest |
| `selftest.sh` | proves that very file in throwaway containers (same image, same hardening flags, five stand-in gates, `localhost` as the name); touches no running service |
| `selftest_upstream.py` | the stand-in gate the selftest runs (a big body, a slow trickle, a range, a body cut short) |

```bash
bash edge/selftest.sh              # ~2 minutes, exit 0 only if every check passes
bash edge/selftest.sh --slow       # also holds two streams unread for 70 s (the paused-player case)
docker compose up -d edge          # the service is in docker-compose.override.yml under the profile "edge": a bare `up -d` never starts it, naming it does (DNS and the router's forward first: docs/direct-door.md)
docker compose restart edge        # after a change to the Caddyfile (the admin API is off on purpose)
docker logs skippytel-edge         # what Caddy says; the traffic is in /data/access.log (docker exec skippytel-edge tail -f /data/access.log)
```

Rules the Caddyfile keeps: no secret in it or in the container; the signed pass in a URL path (`/t/<pass>/...`) never reaches a log; HTTP/1.1 and HTTP/2 only
(the router forwards TCP); the Caddy version is pinned in the compose file and must stay at 2.11.8 or later (docs/direct-door.md says why).
