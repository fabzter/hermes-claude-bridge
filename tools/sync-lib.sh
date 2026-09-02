#!/usr/bin/env bash
# tools/sync-lib.sh — vendor herdrbridge.py (+ test fakes/fixtures) from fabzter/herdrbridge at a pinned ref.
# Usage: tools/sync-lib.sh [REF]   (REF defaults to the pinned commit in herdrbridge.version, else main)
# Set HERDRBRIDGE_DIR=/path/to/local/clone to copy from a local checkout instead of GitHub.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
dest="$here/claude-bridge/scripts"
ref="${1:-$(cat "$dest/herdrbridge.version" 2>/dev/null || echo main)}"
src="${HERDRBRIDGE_DIR:-}"
fetch() { if [[ -n $src ]]; then cp "$src/$1" "$2"; else curl -fsSL "https://raw.githubusercontent.com/fabzter/herdrbridge/$ref/$1" -o "$2"; fi; }
mkdir -p "$dest" "$here/tests/fixtures"
fetch herdrbridge.py "$dest/herdrbridge.py"
fetch tests/fakes.py "$here/tests/fakes.py"
for f in claude_reply.txt hermes_reply.txt hermes_before.txt hermes_approval_menu.txt; do
  fetch "tests/fixtures/$f" "$here/tests/fixtures/$f" || echo "note: fixture $f not available at $ref"
done
# fakes.py in the library repo imports from "..": point it at the vendored location here.
sed -i '' 's#os.path.join(os.path.dirname(__file__), "..")#os.path.join(os.path.dirname(__file__), "..", "claude-bridge", "scripts")#' "$here/tests/fakes.py"
if [[ -n $src ]]; then ( cd "$src" && git rev-parse HEAD ) > "$dest/herdrbridge.version"
else curl -fsSL "https://api.github.com/repos/fabzter/herdrbridge/commits/$ref" | python3 -c 'import json,sys; print(json.load(sys.stdin)["sha"])' > "$dest/herdrbridge.version"; fi
echo "vendored herdrbridge @ $(cat "$dest/herdrbridge.version")"
