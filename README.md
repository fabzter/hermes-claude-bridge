# hermes-claude-bridge

A [Hermes Agent](https://github.com/NousResearch) skill that lets Hermes hold a **continuing
conversation with Claude Code** — not one-shot prompts.

It drives `claude -p` with `--session-id` on the first call and `--resume` afterwards, so
follow-up questions keep their context, and it is **read-only by design**: the allowed tool set
is `Read Grep Glob WebSearch WebFetch`, so Claude can read and reason but cannot write, edit, or
run shell commands on Hermes's behalf. Anything else is denied automatically, since a headless
session has no human to approve it.

## Install

```bash
hermes skills install fabzter/hermes-claude-bridge/claude-bridge --yes
```

## Use

```bash
bash ~/.hermes/skills/claude-bridge/scripts/claude-bridge ask "what changed in this repo today?"
bash ~/.hermes/skills/claude-bridge/scripts/claude-bridge ask-file long-context.md
bash ~/.hermes/skills/claude-bridge/scripts/claude-bridge session | reset | list
```

Options: `--session NAME` (default `bean`), `--timeout SECONDS` (300), `--cwd DIR` (`$HOME`),
`--model NAME`, `--tools "T1 T2"`. Exit codes: `0` ok, `1` error, `2` bad usage, `6` timeout,
`7` `claude` CLI not found.

Requires the `claude` CLI on `PATH` (or set `CLAUDE_BIN`). macOS/Linux, bash 3.2 compatible,
no dependencies beyond `jq`, `uuidgen`, and coreutils.

## The other direction

For Claude Code driving Hermes, see [fabzter/hermes-bridge](https://github.com/fabzter/hermes-bridge).
