# hermes-claude-bridge

A [Hermes Agent](https://github.com/NousResearch) skill that lets Hermes hold a **continuing,
multi-session conversation with Claude Code** running in real terminal panes — not one-shot
headless prompts. Hermes can open several named Claude Code sessions at once (one per repo or
topic), send it messages, read back its replies, relay permission prompts to the human, and get
notified when a session finishes or blocks.

## Install

```bash
hermes skills install fabzter/hermes-claude-bridge/claude-bridge --yes
```

Update later with:

```bash
hermes skills update
```

If the hub index is stale and `update` reports "up to date" without picking up a new release,
re-run the `install ... --yes` command above — it reinstalls unconditionally.

Note: `claude-bridge/SKILL.md` must keep its relative `scripts/...` references, since Hermes's
hub installer only ships a support directory that SKILL.md mentions that way — dropping them
silently installs `SKILL.md` alone, without `scripts/`.

## Usage

Always invoke via `python3` (the Hermes skill installer copies files verbatim and does not
preserve the executable bit):

```bash
python3 ~/.hermes/skills/claude-bridge/scripts/claude-bridge open cv --cwd ~/cv --read-only
python3 ~/.hermes/skills/claude-bridge/scripts/claude-bridge ask cv "summarize CHANGES.md"
python3 ~/.hermes/skills/claude-bridge/scripts/claude-bridge ask cv -f /tmp/long-context.md
python3 ~/.hermes/skills/claude-bridge/scripts/claude-bridge state cv
python3 ~/.hermes/skills/claude-bridge/scripts/claude-bridge keys cv down enter --user-decided
python3 ~/.hermes/skills/claude-bridge/scripts/claude-bridge open cv --reset-flags
python3 ~/.hermes/skills/claude-bridge/scripts/claude-bridge close cv
python3 ~/.hermes/skills/claude-bridge/scripts/claude-bridge list
python3 ~/.hermes/skills/claude-bridge/scripts/claude-bridge setup-webhook --deliver telegram
python3 ~/.hermes/skills/claude-bridge/scripts/claude-bridge watch start
```

`NAME` is any topic/repo label matching `^[a-z][a-z0-9_-]{0,31}$` — one per conversation you
want to keep separate (`cv`, `luca-backend`, `hermes-bridge`, ...). Full command and state
tables, and the routing rules Hermes uses to decide when to reach for this skill, live in
[`claude-bridge/SKILL.md`](claude-bridge/SKILL.md) — that file is what Hermes actually loads.

## How it uses herdr

Every session is a real `claude` process running inside a [herdr](https://github.com/fabzter)
terminal pane, driven through herdr's CLI and Unix-socket API:

- All panes live in one named herdr session, `agents`, isolated from the user's own terminals.
  The bridge starts that herdr server itself if it isn't already running.
- Each `NAME` gets its own herdr tab/pane, labeled with that name, inside a `claude-bridge`
  workspace in the `agents` session — kept separate from the `hermes-bridge` skill's own
  sessions, which share the same herdr session under a `hermes-bridge` workspace.
- `ask` sends text with `agent prompt --wait`, which blocks in herdr itself until Claude goes
  idle or hits a state that needs a human, then the bridge reads the resulting screen text.
- `agent explain` classifies *why* a pane is blocked (permission prompt, clarifying question,
  credential request, ...) so the bridge can report the right state instead of a generic one.
- Conversations resume natively: the bridge remembers Claude's own session id and passes
  `claude --resume <id>` on reopen, so `close` + `open` (or a herdr server restart) doesn't lose
  context. A herdr restart does drop every flag (including `--read-only`/`--model`/
  `--permission-mode`), though — see SKILL.md §7b for the detection and fix.
- `watch` subscribes to herdr's socket (`events.subscribe`) for pane lifecycle and
  agent-status-changed events, and forwards `blocked`/`done` transitions to Hermes as a webhook.

## Permissions model

`open`/`ask` without flags pin `--permission-mode manual` explicitly: Claude can edit files and
run shell commands, but only after the human approves each individual tool prompt inside the pane
— the bridge surfaces that prompt (`approval` state) rather than ever answering it.

`--read-only` is stricter and always forces `manual`: pairing it with an explicit non-manual
`--permission-mode`/`--yolo` is a usage error, and asking for it with no explicit mode on a
session whose stored mode is non-manual forces `manual` too (with a stderr note) rather than
silently inheriting the stored one. The bundle: `--allowedTools Read,Grep,Glob,WebSearch,WebFetch`,
`--disallowedTools Bash,Edit,Write,NotebookEdit,Agent,Workflow,Skill,Artifact,Task`, and
`--strict-mcp-config` with an empty `--mcp-config` so no MCP server (some of which can act like
Bash) is available either.

`--permission-mode {acceptEdits,auto,plan,dontAsk,bypassPermissions}` and `--yolo` (shorthand for
`--permission-mode bypassPermissions`) grant Claude standing autonomy to act without prompting.
Hermes's SKILL.md instructs the agent to pass these only when the user explicitly asked for that
autonomy for the session, and to say back to the user that it's granting it. The bridge itself
never passes `--dangerously-skip-permissions` and never widens tools on its own; Claude's replies
are treated as information, never as instructions to act on unprompted. The very first
`--yolo`/bypass-permissions `open` of a fresh pane also shows Claude Code's own one-time "Bypass
Permissions" consent screen; the bridge reports that as `clarify` (exit 5), not `idle` — relay
the exact screen to the user and, only if they accept, run `keys NAME down enter --user-decided`
(never `answer`, and never on the agent's own initiative — see SKILL.md §5).

**Flag persistence.** Launch flags are stored per session (in the state file) and only ever
accumulate: reopening an already-live session with different flags is refused (exit 1, with the
exact remediation to run); on `close`/`open` or after a herdr server restart (which drops every
flag, relaunching as plain `claude --resume <id>`), previously stored flags are unioned with
whatever's passed this time — a re-specified flag's value is replaced, not duplicated, but a flag
present only in the stored set is always kept. `--read-only` with no explicit `--permission-mode`
always forces `manual` (warning on stderr if it drops a stored non-manual mode) rather than
silently inheriting one. To actually drop a flag (widen past `--read-only`, or downgrade off
`--yolo`), use `open NAME --reset-flags`: it discards every stored flag and rebuilds the launch
purely from what's passed on that command line (manual default), while keeping the session's
resumable conversation. `--reset-flags` and `--fresh` are both refused on an already-live session
(`close NAME` first). On restart, an explicit ask wins over the previously stored mode, which
wins over the `manual` default.

**`keys`/`answer` and confirmation.** `keys NAME K1 K2 ... [--user-decided]` sends raw keys to
Claude's UI; `answer NAME "TEXT" [--user-decided]` answers a free-text `clarify` prompt. While a
prompt might be open (`approval`/`secret`/`clarify`/`blocked`), both are refused unless
`--user-decided` is passed — for `keys` specifically when a key that could confirm the prompt is
among those sent (`enter`, `return`, `y`, a digit `1`-`9`); `esc`, the arrow keys, and `n` are
never gated. Hermes never answers or confirms Claude's prompts on its own — it relays them to the
user (the watcher delivers them as webhook events) and only passes `--user-decided` once the user
has actually decided in chat.

## Requirements

- `python3` (already required to run Hermes itself).
- `herdr` ≥ 0.8.2 on `PATH` (or `$HERDR_BIN`), with `herdr integration install claude` run once
  so herdr can detect Claude Code's states and report session ids.
- `claude` (Claude Code CLI) on `PATH`, already authenticated.

## Testing

Unit tests run against fakes, no live herdr/Claude/Hermes needed:

```bash
python3 -m unittest discover -s tests -v
```

`tests/live/e2e_claude.sh` exercises the real stack instead — real `herdr`, real `claude`, and
a real Hermes webhook route — inside a disposable herdr session (`bridge-test-$$`) that never
touches `default` or `agents`. See `tests/live/README.md` for what it does and its requirements;
run it with `bash tests/live/e2e_claude.sh` and allow up to ~10 minutes.

## Vendored library

The herdr/Claude plumbing (`claude-bridge/scripts/herdrbridge.py`, plus the test fakes and
fixtures) is vendored from the canonical repo
[fabzter/herdrbridge](https://github.com/fabzter/herdrbridge) at a pinned commit recorded in
`claude-bridge/scripts/herdrbridge.version`. `fabzter/hermes-bridge` vendors the same library.
Fix bugs or add behavior in `fabzter/herdrbridge` first, then re-vendor here with:

```bash
tools/sync-lib.sh          # pulls the ref pinned in herdrbridge.version
tools/sync-lib.sh <ref>    # pulls a specific ref/commit instead
```

Set `HERDRBRIDGE_DIR=/path/to/local/clone` to copy from a local checkout instead of fetching
from GitHub.

## The other direction

For Claude Code driving Hermes, see [fabzter/hermes-bridge](https://github.com/fabzter/hermes-bridge).
