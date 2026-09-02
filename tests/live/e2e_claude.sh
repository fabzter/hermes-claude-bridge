#!/usr/bin/env bash
# tests/live/e2e_claude.sh — real herdr + real Claude Code + real Hermes webhook, in a throwaway herdr session.
set -euo pipefail
here="$(cd "$(dirname "$0")/../.." && pwd)"
export HERDR_BRIDGE_SESSION="bridge-test-$$"
export CLAUDE_BRIDGE_STATE_DIR="$(mktemp -d)"
B="python3 $here/claude-bridge/scripts/claude-bridge"
STATE="$CLAUDE_BRIDGE_STATE_DIR"
cleanup() { $B watch stop >/dev/null 2>&1 || true
            HERDR_SESSION="$HERDR_BRIDGE_SESSION" herdr session stop "$HERDR_BRIDGE_SESSION" --json >/dev/null 2>&1 || true
            herdr session delete "$HERDR_BRIDGE_SESSION" --json >/dev/null 2>&1 || true
            hermes webhook remove claude-bridge-e2e >/dev/null 2>&1 || true
            rm -rf "$CLAUDE_BRIDGE_STATE_DIR"; }
trap cleanup EXIT
echo "## webhook route (log delivery)"; $B setup-webhook --route claude-bridge-e2e --deliver log
echo "## watcher"; $B watch start; $B watch status
echo "## open read-only"; $B open e2e --cwd "$here" --read-only --fresh
echo "## ask"; reply=$($B ask e2e "Read README.md in the current directory and answer in one sentence: what is this repo? Reply with only that sentence."); echo "reply=<$reply>"
[[ -n $reply ]] || { echo "empty reply"; exit 1; }
echo "## session id"; $B session e2e
echo "## capture fixture"; $B read e2e -n 120 > /tmp/claude_live_capture.txt
echo "## approval (Bash is disallowed and --permission-mode manual is pinned, so this must prompt)"; set +e
$B ask e2e "Run the shell command: echo hello-from-e2e" > /tmp/e2e-claude-approval.out 2>&1; rc=$?; set -e; cat /tmp/e2e-claude-approval.out
if [[ $rc == 3 ]]; then echo "approval detected"; $B state e2e; echo "## deny via esc"; $B keys e2e esc; sleep 2; $B state e2e
else echo "FAIL: rc=$rc — expected exit 3 (approval); with --permission-mode manual, Bash must prompt rather than being silently skipped or auto-run"; exit 1
fi
echo "## watcher log (expect a posted line)"; sleep 3; cat "$STATE/watch.log" | tail -5
echo "## second session in parallel"; $B open e2e2 --cwd "$here" --read-only --fresh; $B list
echo "## close both"; $B close e2e; $B close e2e2; $B list; $B gc
echo "ALL LIVE CHECKS PASSED"
