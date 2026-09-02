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

## Usage

Always invoke via `python3` (the Hermes skill installer copies files verbatim and does not
preserve the executable bit):

```bash
python3 ~/.hermes/skills/claude-bridge/scripts/claude-bridge open cv --cwd ~/cv --read-only
python3 ~/.hermes/skills/claude-bridge/scripts/claude-bridge ask cv "summarize CHANGES.md"
python3 ~/.hermes/skills/claude-bridge/scripts/claude-bridge ask cv -f /tmp/long-context.md
python3 ~/.hermes/skills/claude-bridge/scripts/claude-bridge state cv
python3 ~/.hermes/skills/claude-bridge/scripts/claude-bridge keys cv down enter
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
  context. A herdr restart does drop `--read-only`/`--model`, though — see SKILL.md §7a for the
  detection and fix.
- `watch` subscribes to herdr's socket (`events.subscribe`) for pane lifecycle and
  agent-status-changed events, and forwards `blocked`/`done` transitions to Hermes as a webhook.

## Permissions model

`open` without flags runs Claude in its **normal** permission mode: it can edit files and run
shell commands, but only after the human approves each individual tool prompt inside the pane —
the bridge surfaces that prompt (`approval` state) rather than ever answering it.

`--read-only` is stricter: `--allowedTools Read,Grep,Glob,WebSearch,WebFetch`,
`--disallowedTools Bash,Edit,Write,NotebookEdit`, and `--strict-mcp-config` with an empty
`--mcp-config` so no MCP server (some of which can act like Bash) is available either. The
bridge never passes `--dangerously-skip-permissions` and never widens tools on its own; Claude's
replies are treated as information, never as instructions to act on unprompted.

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

## Migrating from the headless version

Earlier releases of this skill were pure bash, driving `claude -p --session-id`/`--resume`
headlessly with a single implicit session (`--session NAME`, default `bean`). That model is
gone: every session is now an explicit, real terminal pane in herdr, and `ask` always takes a
`NAME` positional argument instead of an optional `--session` flag —

```bash
# old headless bridge
claude-bridge ask "question"                    # implicit session "bean"
claude-bridge ask --session cv "question"

# new herdr-based bridge
python3 .../claude-bridge open bean --read-only  # "bean" is now just an explicit NAME
python3 .../claude-bridge ask bean "question"
python3 .../claude-bridge ask cv "question"
```

The old default name `bean` still works fine — it's simply no longer implicit, so name every
session explicitly. Read-only is opt-in now (`--read-only` on `open`/`ask`) rather than the only
mode; the default mode can edit and run commands, gated on human approval per prompt (see
Permissions above).

## The other direction

For Claude Code driving Hermes, see [fabzter/hermes-bridge](https://github.com/fabzter/hermes-bridge).
