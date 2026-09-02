# Live end-to-end check

`e2e_claude.sh` exercises the real stack, not fakes: real `herdr` (>= 0.8.2, with the
Claude Code agent-detection manifest installed), real `claude` (Claude Code CLI) on
`PATH`, and the real Hermes gateway webhook adapter listening on `127.0.0.1:8644`.

It does **not** touch the herdr `default` or `agents` sessions, nor the project's own
`claude-bridge/state/` directory. It creates its own throwaway herdr session named
`bridge-test-$$` (the script's own PID, so concurrent runs don't collide) and its own
throwaway state directory (`CLAUDE_BRIDGE_STATE_DIR`, a fresh `mktemp -d`, so it never reads
or writes the real `claude-bridge/state/webhook.json` / `watch.pid` / `watch.log`), drives
two named Claude panes (`e2e`, `e2e2`) inside it, and tears everything down in an `EXIT`
trap:

- stops the `claude-bridge` watcher it started (`watch stop`)
- stops and deletes the throwaway herdr session
- removes the `claude-bridge-e2e` Hermes webhook route (`hermes webhook remove`)
- copies `$CLAUDE_BRIDGE_STATE_DIR/watch.log` to `/tmp/claude-e2e-watch.log` (if it exists) so
  the watcher's log survives for inspection
- deletes the throwaway `CLAUDE_BRIDGE_STATE_DIR` directory entirely (`rm -rf`)

## What it does

1. Registers a Hermes webhook route (`claude-bridge-e2e`, `--deliver log` so the
   rendered prompt lands in Hermes's own log instead of actually notifying anyone).
2. Starts the `claude-bridge` watcher, which subscribes to herdr pane events and posts
   `claude_done` / `claude_blocked` to that route when a pane finishes or blocks.
3. Opens a read-only Claude session (`e2e`) in the repo root and asks it to read
   `README.md` and answer in one sentence — a real turn through real Claude Code.
4. Prints the Claude session id and captures a transcript (`read e2e -n 120`) to
   `/tmp/claude_live_capture.txt` — this becomes the new `tests/fixtures/claude_reply.txt`.
5. Asks `e2e` (read-only) to run a shell command. `Bash` is in `--disallowedTools`, so
   Claude Code must deny it outright without ever prompting — the script asserts exit code
   `0` and checks the transcript (`read e2e -n 80`) for actual evidence of execution (a line
   matching `^\s*⏺ Bash\(` — a Bash tool-use — or `^\s*⎿\s+hello-from-e2e` — its output
   block), not a plain substring match, since Claude's own refusal prose quotes the command
   text back and would otherwise false-positive.
6. Opens a second session (`e2e2`) in **default** mode — no `--read-only` — to confirm the
   *other* half of the permission story: here `Bash` is not denied, so `--permission-mode
   manual` must make Claude Code prompt for it. Asks it to run the same shell command; the
   script asserts exit code `3` (`approval`) and that `state e2e2` reads `approval`, **fails
   the run if either is anything else**, then dismisses the prompt with `esc` (never
   approves it) and asserts `state e2e2` has left `approval`.
7. Tails the throwaway `$CLAUDE_BRIDGE_STATE_DIR/watch.log` to show the watcher's forwarded
   event(s), lists both sessions to confirm they coexist, then closes both and runs `gc`.

## Requirements

- `herdr` >= 0.8.2 running, with the Claude Code integration (agent-detection rules for
  `claude`) installed.
- `claude` (Claude Code CLI) on `PATH`, already authenticated.
- The Hermes gateway running locally with its webhook adapter listening on
  `127.0.0.1:8644` (`hermes webhook subscribe/remove` must work).
- Run from the repo root: `bash tests/live/e2e_claude.sh`. Allow up to 10 minutes —
  Claude Code startup can take up to 60s and each turn 20-90s.

## Safety

The script only ever presses `esc` to dismiss a blocking prompt; it never approves a
tool call, never passes `--dangerously-skip-permissions`, and never runs against the
`default` or `agents` herdr sessions.
