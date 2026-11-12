#!/usr/bin/env bash
# Proves the REAL edge/Caddyfile, byte for byte, in throwaway containers: the same image and the same hardening flags as the `edge` service in
# docker-compose.override.yml, five stand-in gates (edge/selftest_upstream.py) under the five compose service names, and `localhost` as the
# site name (Caddy's own local CA signs it; the real name needs the router and Let's Encrypt, which this does not touch).
#
#   bash edge/selftest.sh            about two minutes: validate, routing, headers, big bodies, streaming, a dead gate, nothing secret in a log
#   bash edge/selftest.sh --slow     also the paused-player test (two streams held 70 s: ~85 s more)
#   bash edge/selftest.sh --keep     leave the containers up afterwards (docker exec skippy-edge-selftest-edge sh)
#
# Nothing that runs is touched: no compose command, no published port (the checks run inside the test network), names all start with
# skippy-edge-selftest. Exit status 0 only when every check passed.
set -u
export MSYS_NO_PATHCONV=1                       # Git Bash on Windows would turn /dev/null and /etc/caddy into C:/Program Files/Git/...

slow=0; keep=0
for a in "$@"; do
  case "$a" in --slow) slow=1 ;; --keep) keep=1 ;; *) echo "usage: bash edge/selftest.sh [--slow] [--keep]" >&2; exit 2 ;; esac
done

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && (pwd -W 2>/dev/null || pwd))"      # a path Docker can mount (D:/... on Windows)
repo="${here%/*}"
compose="$repo/docker-compose.override.yml"
image="$(sed -n 's/^[[:space:]]*image:[[:space:]]*\(caddy:[^[:space:]]*\).*$/\1/p' "$compose" | head -1)"
[ -n "$image" ] || { echo "no 'image: caddy:<tag>' in $compose" >&2; exit 2; }
command -v docker >/dev/null || { echo "docker is not on PATH" >&2; exit 2; }

net=skippy-edge-selftest
edge=$net-edge
gates="music-gate video-gate audiobook-gate voice-gate popup-gate"
vols="$net-data $net-config $net-data-debug $net-config-debug"
# one place for the flags the compose service sets, so the test runs the container the way compose does
harden=(--cap-drop ALL --cap-add NET_BIND_SERVICE --read-only --tmpfs /tmp --security-opt no-new-privileges:true)

pass=0; fail=0
ok()  { pass=$((pass + 1)); printf '  ok    %s\n' "$1"; }
bad() { fail=$((fail + 1)); printf '  FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '          %s\n' "$2"; return 0; }
info() { printf '  info  %s\n' "$1"; }
section() { printf '\n%s\n' "$1"; }

cleanup() {
  docker rm -f "$edge" "$edge-debug" >/dev/null 2>&1
  for g in $gates; do docker rm -f "$net-$g" >/dev/null 2>&1; done
  docker network rm "$net" >/dev/null 2>&1
  docker volume rm $vols >/dev/null 2>&1
  rm -f "${tmpdir:-/nonexistent}"/* 2>/dev/null; rmdir "${tmpdir:-/nonexistent}" 2>/dev/null
}
cleanup                                          # leftovers of an aborted earlier run
[ "$keep" = 1 ] || trap cleanup EXIT
tmpdir="$(mktemp -d)"; tmpwin="$(cygpath -m "$tmpdir" 2>/dev/null || echo "$tmpdir")"       # a path Docker can mount

cu() { docker exec "$edge" curl -sk --max-time 25 "$@"; }          # curl inside the test network, as a client of the edge
code() { cu -o /dev/null -w '%{http_code}' "$@"; }
has() { grep -qi -- "$1"; }
sha_of() { grep -o '"sha256": "[0-9a-f]*"' | cut -d'"' -f4; }

section "the Caddyfile"
out="$(docker run --rm -e EDGE_HOST=home.makerportal.live -v "$here/Caddyfile:/etc/caddy/Caddyfile:ro" "$image" caddy validate --config /etc/caddy/Caddyfile 2>&1)"
echo "$out" | has "Valid configuration" && ok "validates with the real name (home.makerportal.live), $image" || bad "caddy validate" "$(echo "$out" | tail -2)"
actual="$(tr -d '\r' < "$repo/edge/Caddyfile")"                                                 # (a CRLF checkout is not a format problem)
formatted="$(printf '%s\n' "$actual" | docker run --rm -i "$image" caddy fmt - 2>&1)"
[ "$formatted" = "$actual" ] && ok "canonical format (caddy fmt changes nothing)" || bad "caddy fmt would change the file (run: caddy fmt --overwrite edge/Caddyfile in the image)" "$(diff <(echo "$actual") <(echo "$formatted") | head -4)"

section "starting the stand-ins and the edge"
docker network create "$net" >/dev/null || { echo "could not create the test network" >&2; exit 2; }
for g in $gates; do
  docker run -d --rm --name "$net-$g" --network "$net" --network-alias "$g" -v "$here/selftest_upstream.py:/srv/selftest_upstream.py:ro" \
    python:3.12-slim python /srv/selftest_upstream.py "$g" >/dev/null || { echo "could not start the stand-in for $g" >&2; exit 2; }
done
docker run -d --name "$edge" --network "$net" -e EDGE_HOST=localhost "${harden[@]}" \
  -v "$here/Caddyfile:/etc/caddy/Caddyfile:ro" -v "$net-data:/data" -v "$net-config:/config" "$image" >/dev/null || { echo "could not start the edge" >&2; exit 2; }
up=0
for _ in $(seq 1 40); do
  if [ "$(cu https://localhost/music/health -o /dev/null -w '%{http_code}')" = 200 ]; then up=1; break; fi
  sleep 0.5
done
[ "$up" = 1 ] && ok "edge answers through to a stand-in (certificate made, listeners up)" || { bad "edge did not come up" "$(docker logs "$edge" 2>&1 | tail -5 | cut -c1-300)"; exit 1; }
[ "$(docker exec "$edge" curl -s --max-time 3 http://127.0.0.1:8081/ping)" = ok ] && ok "health listener (127.0.0.1:8081/ping) says ok inside the container" || bad "health listener"
if docker run --rm --network "$net" "$image" curl -s -m 3 -o /dev/null "http://$edge:8081/ping"; then bad "the health listener is reachable from another container"; else ok "health listener is not reachable from outside the container"; fi
ports="$(docker exec "$edge" netstat -ltnp | awk '$NF ~ /caddy/ {print $4}' | sed 's/.*://' | sort -un | tr '\n' ' ')"      # (Docker's own resolver listens on 127.0.0.11 here)
[ "$ports" = "443 8081 " ] && ok "Caddy listens on TCP 443 and 8081 only (no :80, no admin :2019)" || bad "Caddy's listening TCP ports" "got: $ports"
udp="$(docker exec "$edge" netstat -lunp | awk '$NF ~ /caddy/' | wc -l | tr -d ' ')"
[ "$udp" = 0 ] && ok "Caddy has no UDP listener (HTTP/3 is off)" || bad "Caddy's UDP listeners: $udp"

section "routing"
for h in "--http2" "--http1.1"; do
  r="$(cu -i $h https://localhost/ping)"
  echo "$r" | head -1 | has "200" && [ "$(echo "$r" | tail -1 | tr -d '\r')" = ok ] && ok "/ping answers 200 ok ($h)" || bad "/ping ($h)" "$r"
done
r="$(cu -i https://localhost/ping)"
echo "$r" | has "cache-control: no-store" && echo "$r" | has "access-control-allow-origin: \*" && ok "/ping is not cacheable and is readable cross-origin" || bad "/ping headers" "$r"
for pair in music:music-gate video:video-gate books:audiobook-gate voice:voice-gate in:popup-gate; do
  p="${pair%%:*}"; svc="${pair##*:}"
  r="$(cu https://localhost/$p/health)"
  echo "$r" | has "\"service\": \"$svc\"" && echo "$r" | has '"path": "/health"' && ok "/$p/health reaches $svc as /health" || bad "/$p/health -> $svc" "$r"
done
r="$(cu "https://localhost/music/t/1800003600.d8383a1c/a%20song.mp3?x=1&y=2")"
echo "$r" | has '"path": "/t/1800003600.d8383a1c/a%20song.mp3?x=1&y=2"' && ok "a pass path and its query string arrive untouched, the prefix cut off" || bad "path and query" "$r"
for u in / /nope /music /video /ping/x /inx/health /musicians/t/x /health /t/x; do
  c="$(code "https://localhost$u")"
  [ "$c" = 404 ] && ok "404 for $u" || bad "404 for $u" "got $c"
done
c="$(code -H 'Host: evil.example' https://localhost/music/health)"
[ "$c" = 404 ] && ok "a wrong Host header behind the right SNI is a 404, not Caddy's empty 200" || bad "wrong Host header" "got $c"
r="$(cu -i -X OPTIONS -H 'Origin: https://makerportal.live' https://localhost/in/t/x/file)"
echo "$r" | has '"method": "OPTIONS"' && echo "$r" | has "access-control-allow-origin: \*" && ok "a CORS preflight reaches the gate and its CORS headers come back" || bad "OPTIONS preflight" "$r"
r="$(cu -i -H 'Range: bytes=5-9' https://localhost/books/t/p/range)"
echo "$r" | head -1 | has 206 && echo "$r" | has "content-range: bytes 5-9/100" && [ "$(echo "$r" | tail -1)" = 56789 ] && ok "a Range request comes back 206 with the right bytes" || bad "Range" "$r"

section "headers"
for u in /music/health /ping /nope; do
  r="$(cu -i https://localhost$u)"
  if echo "$r" | grep -qiE '^(server|via|alt-svc):'; then bad "$u leaks a Server/Via/Alt-Svc header" "$(echo "$r" | grep -iE '^(server|via|alt-svc):')"; else ok "$u: no Server, Via or Alt-Svc header"; fi
done
r="$(cu -i https://localhost/music/health)"
echo "$r" | has "access-control-allow-origin: \*" && ok "the gate's own CORS header is passed on" || bad "CORS header lost" "$r"

section "TLS"
if cu -o /dev/null https://127.0.0.1/ping; then bad "a client with no server name (by IP address) got an answer"; else ok "by IP address (no SNI) the handshake is refused: the door only answers to its name"; fi
if cu -o /dev/null --resolve evil.example:443:127.0.0.1 https://evil.example/ping; then bad "an unknown server name got an answer"; else ok "an unknown server name is refused"; fi
r="$(cu -o /dev/null -w '%{http_version}' --http2 https://localhost/ping)"
[ "$r" = 2 ] && ok "HTTP/2 is negotiated" || bad "HTTP/2" "got $r"

section "big bodies and streaming"
sums="$(docker exec "$edge" sh -c 'head -c 20971520 /dev/urandom > /tmp/up.bin && sha256sum /tmp/up.bin' | cut -d' ' -f1)"
for h in "--http2" "--http1.1"; do
  r="$(cu $h -X PUT -T /tmp/up.bin --max-time 60 https://localhost/in/t/1800003600.abc/file)"
  echo "$r" | has '"got": 20971520' && [ "$(echo "$r" | sha_of)" = "$sums" ] && ok "a 20 MB PUT to /in arrives whole ($h)" || bad "20 MB PUT ($h)" "$r"
done
want="$(cu https://localhost/video/t/p/sha?mb=40 | sha_of)"
for h in "--http2" "--http1.1"; do
  got="$(cu $h --max-time 60 https://localhost/video/t/p/big?mb=40 | sha256sum | cut -d' ' -f1)"
  [ -n "$want" ] && [ "$got" = "$want" ] && ok "a 40 MB body streams through intact ($h)" || bad "40 MB GET ($h)" "want $want got $got"
done
start=$(date +%s%N); first=""; last=0; n=0
while IFS= read -r line; do
  now=$(( ($(date +%s%N) - start) / 1000000 )); n=$((n + 1)); [ -z "$first" ] && first=$now; last=$now
done < <(cu -N --http2 https://localhost/voice/t/p/trickle)
[ "$n" = 4 ] && [ $((last - first)) -ge 1000 ] && ok "a slow stream is not buffered: the lines arrive as they are sent (${first} ms to ${last} ms, sent over 1.5 s)" || bad "streaming" "lines=$n first=${first}ms last=${last}ms"

section "a gate that is down"
docker rm -f "$net-video-gate" >/dev/null 2>&1
c="$(code https://localhost/video/health)"
r="$(cu -i https://localhost/video/health)"
[ "$c" = 502 ] && echo "$r" | has "502 Bad Gateway" && ! echo "$r" | grep -qi '^server:' && ok "its prefix answers 502 in plain words, with no Server header" || bad "dead gate" "$c: $r"
[ "$(code https://localhost/music/health)" = 200 ] && [ "$(code https://localhost/ping)" = 200 ] && ok "the other gates and /ping carry on" || bad "other prefixes after a gate died"

section "nothing secret in a log"
before="$(docker logs "$edge" 2>&1 | wc -l | tr -d ' ')"
for u in /wp-login.php /.env /admin /cgi-bin/x /music; do code "https://localhost$u" >/dev/null; done
after="$(docker logs "$edge" 2>&1 | wc -l | tr -d ' ')"
[ "$before" = "$after" ] && ok "five scanner 404s add nothing to docker logs (they go to the access file)" || bad "404s reached docker logs" "$(docker logs "$edge" 2>&1 | tail -3 | cut -c1-250)"
cu -o /dev/null https://localhost/music/t/SECRETPASS.abc/x?y=1
cu -o /dev/null https://localhost/music/t/SECRETPASS.abc/abort
cu -o /dev/null https://localhost/video/t/SECRETPASS.abc/x
sleep 1.5
n1="$(docker logs "$edge" 2>&1 | grep -c SECRETPASS)"; n2="$(docker exec "$edge" grep -c SECRETPASS /data/access.log)"
[ "$n1" = 0 ] && [ "$n2" = 0 ] && ok "the pass appears in neither docker logs nor the access file" || bad "the pass leaked" "docker logs: $n1 lines, access file: $n2 lines"
docker exec "$edge" grep -q '"uri":"/music/t/-/x?y=1"' /data/access.log && ok "the access file still says which prefix was used: /music/t/-/x?y=1" || bad "access file lost the path" "$(docker exec "$edge" tail -2 /data/access.log | cut -c1-300)"
docker exec "$edge" grep -q '"status":502' /data/access.log && docker exec "$edge" grep -q '"error":"dial tcp' /data/access.log && ok "a dead gate leaves a 502 line with the reason in the access file" || bad "no 502 line with a reason in the access file"
docker logs "$edge" 2>&1 | grep -q '"level":"error"' && info "docker logs holds an error line (the cut-short body above); it was checked for the pass too" || info "the cut-short body left no error line in docker logs"

section "the same at debug level (what someone sees after turning debug on to hunt a problem)"
sed 's/^\t\tlevel INFO$/\t\tlevel DEBUG/' "$here/Caddyfile" > "$tmpdir/Caddyfile.debug"
if ! grep -q 'level DEBUG' "$tmpdir/Caddyfile.debug"; then
  bad "could not make the debug copy of the Caddyfile (is the global 'level INFO' line gone?)"
else
  docker run -d --name "$edge-debug" --network "$net" -e EDGE_HOST=localhost "${harden[@]}" -v "$tmpwin/Caddyfile.debug:/etc/caddy/Caddyfile:ro" \
    -v "$net-data-debug:/data" -v "$net-config-debug:/config" "$image" >/dev/null
  for _ in $(seq 1 40); do
    [ "$(docker exec "$edge-debug" curl -sk --max-time 5 -o /dev/null -w '%{http_code}' https://localhost/music/health)" = 200 ] && break
    sleep 0.5
  done
  for u in /music/t/SECRETPASS.abc/x?y=1 /music/t/SECRETPASS.abc/abort /video/t/SECRETPASS.abc/x /in/t/SECRETPASS.abc/file; do
    docker exec "$edge-debug" curl -sk --max-time 25 -o /dev/null "https://localhost$u"
  done
  sleep 1.5
  leaked="$(docker logs "$edge-debug" 2>&1 | grep -c SECRETPASS)"; seen="$(docker logs "$edge-debug" 2>&1 | grep -c '/t/-/')"
  [ "$seen" -gt 0 ] && ok "at debug level docker logs does print requests ($seen lines carry the path)" || info "debug level printed no request line to check"
  [ "$leaked" = 0 ] && ok "...and the pass is in none of them" || bad "the pass leaked into the debug log" "$(docker logs "$edge-debug" 2>&1 | grep SECRETPASS | head -2 | cut -c1-300)"
fi

if [ "$slow" = 1 ]; then
  section "a paused player (70 s without reading): /video is meant to survive it, /voice keeps Caddy's default"
  ( cu --http2 --max-time 200 https://localhost/music/t/p/big?mb=300 | { head -c 1048576 >/dev/null; sleep 70; cat >/dev/null; }; echo "${PIPESTATUS[0]}" > "$tmpdir/media.rc" ) &
  ( cu --http2 --max-time 200 https://localhost/voice/t/p/big?mb=300 | { head -c 1048576 >/dev/null; sleep 70; cat >/dev/null; }; echo "${PIPESTATUS[0]}" > "$tmpdir/default.rc" ) &
  wait
  m="$(cat "$tmpdir/media.rc" 2>/dev/null)"; d="$(cat "$tmpdir/default.rc" 2>/dev/null)"
  [ "$m" = 0 ] && ok "a media stream (/music) survived 70 s unread and finished (curl exit 0)" || bad "paused media stream was cut" "curl exit $m"
  [ "$d" = 0 ] && info "Caddy's default did not cut the unread /voice stream either (curl exit 0)" || info "Caddy's default cut the unread /voice stream after about a minute (curl exit $d), which is why /music, /video and /books are set apart"
fi

section "result"
printf '  %d passed, %d failed\n' "$pass" "$fail"
[ "$keep" = 1 ] && echo "  containers kept: docker exec $edge sh   (everything is named $net-*; remove with docker rm -f, docker network rm $net and docker volume rm $vols)"
[ "$fail" = 0 ]
