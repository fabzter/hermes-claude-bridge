# claude-bridge follow-up — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Hermes the full permission range for the Claude sessions it drives (`--permission-mode`, `--yolo`) with explicit-user-request policy, add the `keys --user-decided` backstop, make flag comparison pair-aware everywhere, finish the deferred polish, document the flag-persistence rules for Hermes, rotate the watcher log, re-vendor the follow-up library, and republish into Hermes.

**Architecture:** Additive CLI options and helpers in `claude-bridge/scripts/claude_bridge_cli.py`; watcher uses the library's `rotate_log`; docs in `claude-bridge/SKILL.md`/`README.md`; vendored library refresh via `tools/sync-lib.sh`; publish with `hermes skills install … --force`.

**Tech Stack:** Python 3.9+ stdlib; herdr 0.8.2; Claude Code 2.1.236 (`--permission-mode` choices: `acceptEdits, auto, bypassPermissions, manual, dontAsk, plan`).

**Spec:** `docs/superpowers/specs/2026-09-01-herdr-bridges-design.md` §5 (amend §3.6/§5: permission modes beyond `manual` only on explicit user request).

## Global Constraints

- Repo root `/Users/fabzter/src/hermes-claude-bridge`; skill dir `claude-bridge/`; never edit the vendored `claude-bridge/scripts/herdrbridge.py`.
- Python 3 stdlib only; Python 3.9 compatible. `python3 -m unittest discover -s tests -v` pristine under `python3` and `/Users/fabzter/.hermes/hermes-agent/venv/bin/python` before every commit.
- SKILL.md: frontmatter `name: claude-bridge`, `platforms: [macos, linux]`; `description` ≤ 50 chars (leave headroom under Hermes's 60-char index budget); keep relative `scripts/…` references (the installer ships `scripts/` only because of them); ≤ 170 lines.
- Safety policy: default launch is `--permission-mode manual`; any other mode or `--yolo` requires the user to have asked for it in chat for that session; `--read-only` cannot be combined with a non-manual mode (usage error). Hermes never answers Claude's prompts on its own; `keys` presses that could confirm a prompt require `--user-decided`.
- No AI-authorship text; no attribution footers; push after each task.

---

### Task 1: `--permission-mode` / `--yolo` on `open` and `ask`

**Files:** Modify `claude-bridge/scripts/claude_bridge_cli.py`, `tests/test_claude_cli.py`.

**Interfaces — Produces:** `PERMISSION_MODES = ("manual", "acceptEdits", "auto", "plan", "dontAsk", "bypassPermissions")`; `build_launch_args(read_only: bool, model: str | None, permission_mode: str = "manual") -> list` emitting `["--permission-mode", permission_mode]` first; `open`/`ask` gain `--permission-mode MODE` (choices above, default `manual`) and `--yolo` (alias for `bypassPermissions`; conflicts with `--permission-mode` other than manual → `UsageError`); `--read-only` with any mode other than `manual` → `UsageError("--read-only requires --permission-mode manual")`. `ensure_open(bridge, name, cwd, read_only, model, fresh, permission_mode="manual")`. The live-path pairwise check and the restart merge already handle the value change (a live `manual` session asked for `--yolo` is refused with the close/open remediation).

- [ ] **Step 1: Failing tests:** `build_launch_args(False, None, "bypassPermissions")` → `["--permission-mode", "bypassPermissions"]`; `open y --yolo` → argv contains `--permission-mode bypassPermissions`; `open y --permission-mode plan`; `open ro --read-only --yolo` → rc 2; `open y --yolo --permission-mode plan` → rc 2; live `manual` session + `ask y --yolo hi` → rc 1 with "already running" and `--permission-mode bypassPermissions` in the message, no prompt sent; stored yolo + restart `open y` → merge keeps `bypassPermissions`.
- [ ] **Step 2: Run → fail.**  - [ ] **Step 3: Implement.**  - [ ] **Step 4: Run → pass.**
- [ ] **Step 5: Commit** `claude-bridge: --permission-mode and --yolo (explicit opt-in) on open/ask`; push.

---

### Task 2: `keys --user-decided` backstop; pair-aware `flags_match`; complete remediation hints

**Files:** Modify `claude-bridge/scripts/claude_bridge_cli.py`, `tests/test_claude_cli.py`.

**Interfaces — Produces:** `keys NAME KEY... [--user-decided]`; `CONFIRMING_KEYS = {"enter", "return", "y", "1", "2", "3", "4", "5", "6", "7", "8", "9"}`; when the session state is `approval`, `secret`, `clarify` or `blocked` and any key (case-insensitive) is in `CONFIRMING_KEYS`, refuse with `BridgeError("keys that could confirm a prompt require --user-decided (the user must have decided in chat)", EXIT_ERROR)` unless `--user-decided`; `esc`/arrows always allowed. `flags_match(stored_flags, argv) -> bool` becomes pair-aware: parse the argv tail after the `claude` executable with `_parse_launch_pairs` and require every stored (flag, value) pair to be present with the same value (bare flags by presence). `check_flags` and the live-path refusal list ALL missing `flag value` pairs in the remediation (e.g. `open cv --read-only --model sonnet`), including `--permission-mode X` when X is not `manual`.

- [ ] **Step 1: Failing tests:** `keys cv enter` on approval → rc 1 with "--user-decided"; `keys cv esc` on approval → rc 0; `keys cv enter --user-decided` on approval → rc 0 and `agent send-keys cv enter` recorded; `keys cv enter` on idle → rc 0 (no prompt open); `flags_match([..., "--model", "opus"], argv_with_model_sonnet)` → False; `flags_match` True when values match regardless of order; remediation message for a live session missing both read-only and model lists both.
- [ ] **Step 2: Run → fail.**  - [ ] **Step 3: Implement.**  - [ ] **Step 4: Run → pass.**
- [ ] **Step 5: Commit** `claude-bridge: keys --user-decided backstop; pair-aware flags_match; complete remediation hints`; push.

---

### Task 3: Watcher log rotation and library re-vendor

**Files:** `tools/sync-lib.sh <SHA>` (given at dispatch), `claude-bridge/scripts/claude_bridge_watch.py`, `tests/test_watch.py`, `tests/test_claude_cli.py`.

- [ ] `tools/sync-lib.sh <SHA>`; suite passes (fix scripting only). Confirm `tests/fixtures/claude_reply.txt` no longer carries the `MultiEdit` warning lines.
- [ ] Watcher: `command("start")` and `command("run")` call `hb.rotate_log(<state_dir>/watch.log)` before opening it; `Watcher.run_forever` calls `hb.rotate_log` on the log path every 500 handled events (pass `log_path` into `Watcher` as an optional argument; when the log is rotated, reopen the file handle). Tests: a 6 MB `watch.log` in a temp state dir is rotated to `.1` by `command("status")`? No — by `start`'s pre-open step (test `start` with a fake `subprocess.Popen` injected via a module-level `_popen` so no process is spawned).
- [ ] `wait`-style operations: none in this CLI; but `ask` should use the library's polling `answer` unchanged. `list` unchanged.
- [ ] Commit `Re-vendor herdrbridge at <SHA>; rotate the watcher log`; push.

---

### Task 4: Docs for Hermes and republish

**Files:** `claude-bridge/SKILL.md`, `README.md`, `docs/superpowers/specs/2026-09-01-herdr-bridges-design.md`.

- [ ] SKILL.md (description ≤ 50 chars, e.g. `"Ask Claude Code via herdr: coding expert"`): a "Permissions" section that Hermes can act on: default `manual` (Claude prompts; relay prompts to the user); `--read-only` (list the allow/deny sets and the MCP disable; incompatible with other modes); `--permission-mode acceptEdits|plan|dontAsk|auto|bypassPermissions` and `--yolo` — only when the user explicitly asked for that autonomy for this session, say it back to the user when you use it; a "Flag persistence" section in plain words: flags are stored per session; asking for different flags on a live session is refused → `close NAME` then `open NAME <flags>`; after a herdr restart herdr relaunches Claude without any flags, the bridge notices and `ask` exits 1 → `close` + `open` with the same flags; `keys` rules incl. `--user-decided`; argument order rule (options after TEXT or `-f FILE`); watcher/webhook section unchanged; keep the relative `scripts/…` file list.
- [ ] README: same permission/flag-persistence content for humans; installer note retained; remove any headless-era leftovers.
- [ ] Spec §3.6/§5 amended (permission modes on explicit request).
- [ ] Verify: frontmatter, description length, `grep -n -i -E "co-authored|wrote it|generated"` empty, suite passes. Commit `docs: permission modes, flag persistence, keys policy for Hermes`; push.
- [ ] Publish: `hermes skills install fabzter/hermes-claude-bridge/claude-bridge --yes --force`; `diff -rq claude-bridge ~/.hermes/skills/claude-bridge --exclude=state --exclude=__pycache__` must be empty; `python3 ~/.hermes/skills/claude-bridge/scripts/claude-bridge --help`.
- [ ] Live smoke (bounded, throwaway herdr session + `CLAUDE_BRIDGE_STATE_DIR=$(mktemp -d)`): `open y --yolo --cwd /tmp/claude-bridge-yolo-$$ --fresh` (create the dir first), `ask y "Create a file named marker.txt containing the word ok in the current directory."` → rc 0 and the file exists (no prompt); `close y`; cleanup. Record in the report.
