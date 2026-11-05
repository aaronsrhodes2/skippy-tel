# The direct front door (2026-11-05)

The Captain: "Funnels and bridges seem like common failure points for us. Can we give direct HTTP to all of my devices?"

Until now every device that is not on the tailnet (the Tesla on cellular, a friend's browser, his phone off Tailscale) reached the media gates on
skippy-pc through a Tailscale Funnel, a relay that stalls on a poor link. The direct front door takes the relay out of the middle: a Caddy reverse
proxy on skippy-pc, reachable straight on the home's public address as **https://home.makerportal.live**. The Funnel stays, as the fallback the pages
use by themselves whenever the door is shut.

```
browser / Tesla ──https──▶ home.makerportal.live          (DNS: the home's public address)
                ──▶ router: external TCP 443 ──▶ skippy-pc 192.168.0.164:47443     (UPnP: tools/upnp_port.py, kept by the starter)
                ──▶ container skippytel-edge (Caddy) :443
                ──▶ the gate, by its compose name, :8080          (the prefix is cut off on the way)
```

## The layout (the contract all four pieces share)

| On the edge | Reaches | Notes |
|---|---|---|
| `/ping` | Caddy itself | `200 ok`, not cacheable: is the door open end to end (DNS, router, TLS, Caddy)? Not a health check of any gate |
| `/music/...` | `music-gate:8080` | Bilby's Audio Service |
| `/video/...` | `video-gate:8080` | Nagatha's Video Service |
| `/books/...` | `audiobook-gate:8080` | Audiobooks |
| `/voice/...` | `voice-gate:8080` | Skippy's ears and mouth |
| `/in/...` | `popup-gate:8080` | big files for pop-ups (PUT, no size limit at the edge: the gate keeps its 64 MB) |
| anything else, any other Host name | 404 | plain text, no `Server` header |

`/music/t/<pass>/x?y=1` reaches the gate as `/t/<pass>/x?y=1`, and `/music/health` as `/health`. Every gate serves `/health` and answers CORS `*` itself (the
signed pass in the URL is the credential, never a cookie). The portal adds `"direct": "https://home.makerportal.live/<prefix>"` to every gate-pass JSON when
`EDGE_HOST` is set and its own probe of `https://EDGE_HOST/ping` from Cloud Run says the door is open from outside (cached, never blocking a request);
`url` stays the Funnel address exactly as before. A page's `SkippyDirect.pick(g)` (`portal/static/direct.js`) swaps `g.url` for `g.direct` only when
`GET <direct>/health` answers JSON within 2.5 s, remembers the verdict per prefix for 120 s (worked) or 300 s (failed), and never throws.

## What is where

| Piece | File |
|---|---|
| the proxy | `edge/Caddyfile` (the whole configuration, commented), `edge/README.md` |
| its proof | `edge/selftest.sh` + `edge/selftest_upstream.py`: this very Caddyfile in throwaway containers (below) |
| the container | the `edge` service in `docker-compose.override.yml` (profile `edge`: **off until `docker compose up -d edge`**; pinned by tag and digest; its own network `edge-net`, shared only with the five gates), volumes `edge-data` (certificates, the ACME account, `access.log`) and `edge-config` |
| the DNS name | `home_dns.hosts` in `core/cd/deploy.json`: skippy-core's deployer (`cd.py home_dns`, every 5 minutes; `cd.py home-dns` to do it now) keeps `home.makerportal.live` on the home's public address, like `telemetry.makerportal.live` |
| the router forward | `tools/upnp_port.py` (`discover`, `list`, `ip`, `ensure`, `remove`), kept in place by the starter's keeper (`Starter.watch_edge`, `tools/skippy_starter.py`) |
| the status card | `edge` in `portal/status_nodes.json` |
| the tests | `tools/tests/test_upnp_port.py`, `test_starter_edge_keeper.py`, `test_edge_caddyfile_contract.py` (`tools/tests/fake_igd.py` is the stand-in router) |
| the portal side | `EDGE_HOST` (set in `core/cd/deploy.json` → `portal.env`), `portal/edge.py` (the probe, the `direct` field, the doctor's "Direct door" row) and `portal/static/direct.js` (the page's half; Drive's page included) |

The edge holds **no secret**: no `env_file` (not the vault), no token, no `docker.sock`. It cannot sign a pass; it only forwards requests that carry one. Its
container is read-only, has no capabilities but `NET_BIND_SERVICE`, and publishes one port, `0.0.0.0:47443` (the only service in the file not on 127.0.0.1). It is on
a network of its own (`edge-net`) that only the five gates also join, so even a flaw in Caddy would not reach flask-api, Whisper or the home reporter.

## Turning it on (in this order)

1. **DNS** (automatic). `home.makerportal.live` is an A record for the home's public address; skippy-core's deployer creates it from `core/cd/deploy.json` and moves it
   when the provider changes the address (it is what keeps `telemetry.makerportal.live` there). To do it at once: `ssh skippy-core 'sudo python3 /opt/skippy-core/cd/cd.py home-dns'`
   (one line per name: `created`, `updated`, `already`). `python tools/upnp_port.py ip` prints the router's own idea of the address.
2. **The router.** Look first, then do it:
   ```bash
   python tools/upnp_port.py discover
   python tools/upnp_port.py ip                    # must be a public address; it says when it is carrier-grade NAT or behind another router
   python tools/upnp_port.py ensure --dry-run      # says what it would add or replace, changes nothing
   python tools/upnp_port.py ensure                # 443/TCP -> 192.168.0.164:47443; exit 0 only when the router lists it afterwards
   ```
   Then put `SKIPPY_EDGE_UPNP=1` in the vault `.env` (or the starter's environment). The starter (restarted once to load the keeper) re-checks every 5 minutes
   and puts the forward back after a router reboot or a changed LAN address. It logs one line to `state/starter.log` when it changes the router and one when
   a failure begins and ends, never one per round (`grep "edge upnp" state/starter.log`). Off is the default: nothing touches the router until that line exists.
3. **The container** (the only new service; nothing existing is touched):
   ```bash
   bash edge/selftest.sh                           # about two minutes: proves the Caddyfile (no running service involved)
   docker compose up -d edge
   docker compose ps edge                          # "healthy" within about 30 s; the certificate arrives a few seconds after DNS and the forward are right
   docker logs skippytel-edge                      # "certificate obtained successfully" for home.makerportal.live
   ```
   Mind the order: a container that starts before the DNS record and the forward exist asks Let's Encrypt for a certificate it cannot be given. Caddy retries on its own, but
   Let's Encrypt allows only 5 failed validations an hour per name and account, and that limit is Let's Encrypt's, not Caddy's: a restart does NOT lift it, so the later
   tries wait out the hour. That is why the service has the compose profile `edge`: a bare `docker compose up -d` (the starter's start and repair run one) leaves it alone,
   and only `docker compose up -d edge` (named, so the profile is on for that command) creates it, and then it keeps itself up (`restart: unless-stopped`; the
   starter does not recreate it, the doctor's "Direct door" row tells the Captain when it has stayed down 10 minutes).
   A first issuance can be rehearsed against Let's Encrypt's staging CA without touching the real rate limits (5 failed validations an hour per name):
   `EDGE_ACME_DIR=https://acme-staging-v02.api.letsencrypt.org/directory docker compose up -d edge`, check with `curl -k`, then `docker compose up -d edge` again
   without the variable (the production certificate is a separate one in the same volume).
4. **The portal** (automatic with the deploy of this change). `EDGE_HOST=home.makerportal.live` is in `core/cd/deploy.json` → `portal.env` (empty = the door is off
   everywhere). The portal probes `https://home.makerportal.live/ping` from Cloud Run (it starts at once and looks again every 30 to 60 s) and adds `direct` to every gate
   pass while the answer is `ok`; until step 2 and 3 are done the probe fails and every pass says `"direct": null`, so nothing changes for anyone. When the door comes up the
   pages find it by themselves: a pass that came without it is asked for again within about five minutes in a browser that has seen the door before.

## How to check it

| Question | Command |
|---|---|
| Is the door open from the internet? | from a phone on cellular (Wi-Fi off): `curl -s https://home.makerportal.live/ping` prints `ok`. From the PC itself this may time out (see hairpin NAT below) |
| Is the router forwarding? | `python tools/upnp_port.py list` (a row `TCP 443 -> 192.168.0.164:47443 'skippy-edge'`) |
| Does the PC answer on the forwarded port? | from another machine on the LAN: `curl -k https://192.168.0.164:47443/ping` is refused with a handshake error by design (no server name); use `curl --resolve home.makerportal.live:47443:192.168.0.164 https://home.makerportal.live:47443/ping` |
| Is a gate reachable through it? | `curl -s https://home.makerportal.live/music/health` (the gate's own JSON) |
| What is Caddy saying? | `docker logs --since 10m skippytel-edge` |
| What is arriving? | `docker exec skippytel-edge tail -n 20 /data/access.log` (one JSON line per request; the pass is written as `/music/t/-/...`) |
| When does the certificate end? | `echo \| openssl s_client -connect home.makerportal.live:443 -servername home.makerportal.live 2>/dev/null \| openssl x509 -noout -enddate` |
| Is the keeper alive? | `grep "edge upnp" state/starter.log \| tail` |

## How to turn it off

Any one of these, from the least to the most:

- the page-side only: empty `EDGE_HOST` in `core/cd/deploy.json` (`portal.env`) and push. Pages use the Funnel again within the cache time (a few minutes) and nothing else changes;
- stop the proxy: `docker compose stop edge` (pages fall back by themselves: the 2.5 s `/health` test fails, the verdict is remembered for 5 minutes);
- stop the keeper: remove the `SKIPPY_EDGE_UPNP` line (or set it to 0). It never removes the forward by itself;
- take the forward away: `python tools/upnp_port.py remove --dry-run`, then `remove`. It only deletes a forward that is this PC's own.

## What the router and the provider must allow

- **UPnP switched on** in the router (TP-Link: Advanced, NAT Forwarding, UPnP). The router here is a TP-Link running MiniUPnPd; `discover` finds it in about 3 s.
- **UPnP may be restricted.** Some routers only open ports above 1023, or only for clients on a list: the add then fails with `606` (or `402` / `501`) and
  `ensure` prints it plainly. The forward can then be made by hand in the router's own page (Virtual Servers / Port Forwarding): TCP, external 443, internal
  192.168.0.164 port 47443. skippy-pc's address is reserved in the router, so it does not move.
- **A public address.** `upnp_port.py ip` must not be a private or 100.64.x.x one: behind carrier-grade NAT, or a second router, no forward can be reached from outside.
- **The provider must let inbound TCP 443 through.** Most residential plans do; a few block 80 and 443. Port 80 is not used and not forwarded: the certificate
  is proved by TLS-ALPN-01 on 443.
- **Windows Firewall** has to let TCP 47443 in (Docker Desktop normally adds the rule when the port is first published). Test from another LAN machine as above.
- **Hairpin NAT.** A browser on the home Wi-Fi asking for `home.makerportal.live` goes out to the public address and back in, which many routers do not do. The page's
  2.5 s test then fails and it uses the tailnet or the Funnel as before. (A DNS answer that gives home devices the LAN address would make this instant; not done.)

## What breaks, and what happens

| Failure | What the visitor sees | What brings it back |
|---|---|---|
| the edge container is down or unhealthy | nothing: the pages' `/health` test fails and they use the Funnel | Docker (`restart: unless-stopped`); the Status page shows the `edge` card, with its health |
| the router forgot the forward (reboot, a lease that ended) | the same: Funnel | the keeper, within 5 minutes |
| the home address changed and DNS did not follow | the same | the DNS job; a renewal needs the name to point here, and the 90-day certificate is renewed 30 days early, so there is room |
| a gate is down or restarting | that prefix answers `502 Bad Gateway` (plain text, about 3 s: Docker's DNS has to give up on the name); the rest carry on; the page falls back to the Funnel for it | the gate's own heal |
| Let's Encrypt cannot be reached | the current certificate keeps being served until it ends | Caddy retries on its own; `docker logs skippytel-edge` shows why |
| a scanner finds the name (certificate transparency publishes it) | 404 to everything, and a TLS handshake error to anyone who connects by IP | nothing to do: scanners' 404s go to `access.log`, never to the logs the starter ships as errors |
| the provider blocks 443 | the portal's probe fails from the first day: Funnel | the forward can be moved to another external port only by changing the contract (`direct` has no port); ask the provider |

Behind Docker Desktop the container sees Docker's own gateway as every visitor's address, so `access.log` cannot tell visitors apart.

## Changing it

- **The Caddyfile.** Edit, `bash edge/selftest.sh` (add `--slow` to also hold two streams unread for 70 s), then `docker compose restart edge`. The admin API is off on
  purpose, so a change is a restart (a second or two; in-flight streams end and the players ask again).
- **Why 2.11.8 and why it must not go lower.** The long-stream prefixes (`/music`, `/video`, `/books`) get `write_timeout 15m` per route so a paused player's stream is
  not cut after Caddy's default minute of not being read (the `--slow` test shows the default cutting `/voice` and the setting keeping `/music`). A per-route timeout
  only beats the server-wide default from 2.11.8; 2.11.6 and 2.11.7 also had HTTP/2 proxy bugs that touch the 20 MB PUT to `/in`. Bump the tag AND the digest in
  `docker-compose.override.yml` (`docker pull caddy:<tag>` then `docker image inspect caddy:<tag> --format '{{index .RepoDigests 0}}'`), run the selftest, then `docker compose up -d edge`.
- **A scanner's junk in the logs.** A client that offers the ACME ALPN `acme-tls/1` makes Caddy print an `error` line ("tls-alpn challenge", no active challenge); the log
  shipper (`core/shipper.py`, `NOT_AN_ERROR`) leaves exactly that line out, so it cannot fill the portal's log ring. Real Caddy errors are still shipped.
- **The log.** `docker logs` holds what Caddy says about itself (JSON, with the pass cut out of any request it prints, at debug level too: the selftest checks); the
  traffic goes to `/data/access.log` in `edge-data` (10 MiB, 3 files, a week), so 4xx lines are never mistaken for errors by the log shipper.
- **Keep `edge-data`.** It holds the certificate and the ACME account; losing it means a fresh issuance (fine, but it counts toward Let's Encrypt's limits).
- **Later, once the first certificate exists: a CAA record** for `makerportal.live` that lets only Let's Encrypt, and only this ACME account, issue for the name
  (`0 issue "letsencrypt.org; accounturi=<the account URL in edge-data>; validationmethods=tls-alpn-01"`, RFC 8657). Without it the five minutes after a change of the home's address
  (the DNS record follows within 5 minutes plus its 60 s TTL) are a window in which whoever is handed the old address could answer a validation for the name. Check the zone for an
  existing CAA first (a stricter one must allow Let's Encrypt). Not done yet: it needs the account URL, which exists only after the first issuance.
