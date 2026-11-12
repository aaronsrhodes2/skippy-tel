#!/usr/bin/env bash
# Refresh the vendored copy of Orion Trail (portal/static/orion-trail/) from github.com/aaronsrhodes2/orion-trail main.
#   bash tools/vendor_orion_trail.sh [ref]
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DEST=$ROOT/portal/static/orion-trail
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
git clone -q https://github.com/aaronsrhodes2/orion-trail.git "$TMP/src"
git -C "$TMP/src" checkout -q "${1:-main}"
SHA=$(git -C "$TMP/src" rev-parse HEAD)
rm -rf "$DEST/scenarios" && mkdir -p "$DEST/scenarios"
cp "$TMP/src/orion-trail.html" "$DEST/"
cp "$TMP/src"/scenarios/*.js "$DEST/scenarios/"
sed -i -E "s/commit \`[0-9a-f]{40}\`/commit \`$SHA\`/" "$DEST/VENDORED.md"
echo "Orion Trail vendored at $SHA. Run the portal tests, then commit."
