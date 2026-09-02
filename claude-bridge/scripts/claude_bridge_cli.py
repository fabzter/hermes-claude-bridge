"""claude-bridge — Hermes Agent drives named Claude Code sessions inside herdr panes."""
from __future__ import annotations

import argparse
import os
import shutil
import sys

import herdrbridge as hb

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.environ.get("CLAUDE_BRIDGE_STATE_DIR") or os.path.join(SKILL_DIR, "state")
# Claude Code 2.1.236's --permission-mode choices (verified via `claude --help`).
PERMISSION_MODES = ("manual", "acceptEdits", "auto", "plan", "dontAsk", "bypassPermissions")
READ_ONLY_ALLOWED = "Read,Grep,Glob,WebSearch,WebFetch"
# Bash/Edit/Write/NotebookEdit are the obvious file/shell escapes; Agent/Workflow/Skill/Artifact
# are built-in tools that can themselves invoke further tools (including Bash-equivalents), so
# read-only must deny them too. Task is claude 2.1.236's alias for Agent — deny both names.
READ_ONLY_DENIED = "Bash,Edit,Write,NotebookEdit,Agent,Workflow,Skill,Artifact,Task"
# MCP servers configured in the user's Claude settings load regardless of --allowedTools/
# --disallowedTools (observed live: a read-only session still had a Bash-capable MCP tool).
# --strict-mcp-config + an empty --mcp-config JSON blob disables all of them for read-only sessions.
READ_ONLY_MCP = ["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
CLAUDE_CFG = hb.BridgeConfig(workspace_label="claude-bridge", kind="claude",
                             default_cwd=os.path.expanduser("~"), exit_command="/exit")


def herdr_bin() -> str:
    return os.environ.get("HERDR_BIN") or shutil.which("herdr") or "/opt/homebrew/bin/herdr"


def default_bridge_factory():
    return hb.Bridge(hb.Herdr(hb.session_name(), bin=herdr_bin()), CLAUDE_CFG, hb.StateStore(STATE_DIR))


def build_launch_args(read_only: bool, model: str | None, permission_mode: str = "manual") -> list:
    # Always pin the permission mode explicitly (rather than relying on Claude's default) so it is
    # recorded in launch_flags and check_flags can notice its loss after a herdr restore (herdr
    # relaunches with plain `claude --resume <id>`, dropping every flag we passed).
    args = ["--permission-mode", permission_mode]
    if read_only:
        args += ["--allowedTools", READ_ONLY_ALLOWED, "--disallowedTools", READ_ONLY_DENIED] + READ_ONLY_MCP
    if model:
        args += ["--model", model]
    return args


def flags_match(stored_flags: list, argv: list) -> bool:
    return all(tok in argv for tok in stored_flags)


def live_argv(bridge: hb.Bridge, pane_id: str) -> list:
    info = bridge.h.cli("pane", "process-info", "--pane", pane_id)["result"].get("process_info", {})
    argv = []
    for p in info.get("foreground_processes") or []:
        argv += [str(a) for a in (p.get("argv") or [])]
    return argv


# Flags build_launch_args can ever emit, in the canonical order it emits them in, and whether
# each one takes a following value token (False == a bare switch).
_LAUNCH_FLAG_ORDER = (
    ("--permission-mode", True), ("--allowedTools", True), ("--disallowedTools", True),
    ("--strict-mcp-config", False), ("--mcp-config", True), ("--model", True),
)
_LAUNCH_FLAG_TAKES_VALUE = dict(_LAUNCH_FLAG_ORDER)
_MISSING = object()  # sentinel: distinct from any real flag value, including None (a bare switch)


def _parse_launch_pairs(tokens: list) -> dict:
    """Flat launch-arg token list -> {flag: value_or_None}, respecting which flags take a value."""
    pairs, i = {}, 0
    while i < len(tokens):
        tok = tokens[i]
        if _LAUNCH_FLAG_TAKES_VALUE.get(tok, False):
            pairs[tok] = tokens[i + 1] if i + 1 < len(tokens) else None
            i += 2
        else:
            pairs[tok] = None
            i += 1
    return pairs


def merge_launch_args(stored: list, requested: list) -> list:
    """UNION `requested` onto `stored`, flag/value-aware: a flag already in `stored` has its
    value REPLACED if `requested` re-specifies it (e.g. --model opus -> --model sonnet) rather
    than appearing twice; a flag only in `requested` is appended; a flag only in `stored` (e.g.
    read-only limits the caller didn't ask to change) is kept. Tokens come back in the same
    canonical order build_launch_args itself uses."""
    merged = _parse_launch_pairs(stored)
    merged.update(_parse_launch_pairs(requested))
    out = []
    for flag, _ in _LAUNCH_FLAG_ORDER:
        if flag in merged:
            out.append(flag)
            if merged[flag] is not None:
                out.append(merged[flag])
    return out


def _is_read_only_flags(flags: list) -> bool:
    return "--allowedTools" in flags


def check_flags(bridge: hb.Bridge, name: str, agent: dict) -> None:
    stored = bridge.store.load(name).get("launch_flags") or []
    if stored and not flags_match(stored, live_argv(bridge, agent["pane_id"])):
        suffix = " --read-only" if _is_read_only_flags(stored) else ""
        raise hb.BridgeError(
            "session %r is running without its requested flags (%s) — herdr's restore likely "
            "relaunched it as plain `claude --resume`, dropping every flag we set (including the "
            "pinned permission mode). Run `close %s` then `open %s%s` to restore them."
            % (name, " ".join(stored), name, name, suffix), hb.EXIT_ERROR)


def ensure_open(bridge: hb.Bridge, name: str, cwd: str | None, read_only: bool, model: str | None, fresh: bool,
                 permission_mode: str = "manual") -> dict:
    kind, _ = bridge.resolve(name)
    if kind == "live":
        requested = build_launch_args(read_only, model, permission_mode)
        stored = bridge.store.load(name).get("launch_flags") or []
        # Compare flag-by-flag (value included), not token-by-token: a stored --model with a
        # different value must count as a mismatch even though the bare token "--model" is
        # present in both lists.
        requested_pairs = _parse_launch_pairs(requested)
        stored_pairs = _parse_launch_pairs(stored)
        missing_flags = [flag for flag, _ in _LAUNCH_FLAG_ORDER
                          if flag in requested_pairs
                          and stored_pairs.get(flag, _MISSING) != requested_pairs[flag]]
        if missing_flags:
            missing_tokens = []
            for flag in missing_flags:
                missing_tokens.append(flag)
                if requested_pairs[flag] is not None:
                    missing_tokens.append(requested_pairs[flag])
            if set(missing_flags) <= {"--permission-mode"}:
                # Nothing the caller actually asked for is missing — only the always-pinned
                # permission-mode pair (e.g. a session predating this pin, or one that lost it
                # to a herdr restore). Don't imply --read-only/--model were requested when
                # they weren't: that path can't build a remediation flag anyway (`model` may be
                # None) and it isn't what the caller is missing.
                suffix = ""
            elif read_only:
                suffix = " --read-only"
            else:
                suffix = " --model " + model
            raise hb.BridgeError(
                "session %r is already running without the requested flags (%s); run `close %s` then "
                "`open %s%s` to apply them" % (name, " ".join(missing_tokens), name, name, suffix), hb.EXIT_ERROR)
        agent = bridge.start(name, [], fresh=False, cwd=cwd)
    else:
        # The session isn't live (e.g. the agent process died, or herdr restored it as plain
        # `claude --resume` without our flags). UNION the newly requested tokens with whatever
        # was stored before, flag/value-aware, so a caller who only passes --model doesn't
        # silently drop previously-requested --read-only limits, and re-requesting a flag with a
        # new value (e.g. a different --model) replaces it instead of appending a duplicate.
        requested = build_launch_args(read_only, model, permission_mode)
        stored = bridge.store.load(name).get("launch_flags") or []
        if permission_mode == "manual" and "--permission-mode" in _parse_launch_pairs(stored):
            # `permission_mode` here is ensure_open's own ambient default, not a real ask — main()
            # only passes a non-"manual" value when the caller actually typed --yolo/
            # --permission-mode. Don't let that ambient default silently downgrade a mode already
            # granted and stored on a previous `open`/`ask --yolo`; strip the pinned pair from
            # `requested` so merge_launch_args falls through to the stored value below, exactly
            # like an unspecified --model or --read-only already does. An explicit non-manual ask
            # (a different value) is left in place and overrides stored, same as --model.
            requested = requested[2:]  # build_launch_args always emits the pair first
        flags = merge_launch_args(stored, requested)
        agent = bridge.start(name, flags, fresh=fresh, cwd=cwd)
        bridge.store.save(name, launch_flags=flags)
    check_flags(bridge, name, agent)
    return agent


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="claude-bridge", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def named(n, help_):
        sp = sub.add_parser(n, help=help_)
        sp.add_argument("name", help="session NAME ([a-z][a-z0-9_-]{0,31}); one per topic/repo")
        return sp

    def add_permission_args(sp):
        sp.add_argument("--permission-mode", choices=PERMISSION_MODES, default="manual",
                         help="Claude's --permission-mode; only change this when the user "
                              "explicitly asked for that autonomy for this session")
        sp.add_argument("--yolo", action="store_true",
                         help="alias for --permission-mode bypassPermissions; only when the user "
                              "explicitly asked for that autonomy for this session")

    sp = named("open", "open (or resume) a Claude session in a herdr pane")
    sp.add_argument("--cwd", help="directory Claude works in (default: $HOME or the stored one)")
    sp.add_argument("--read-only", action="store_true", help="allow only Read/Grep/Glob/WebSearch/WebFetch")
    sp.add_argument("--model", help="Claude model name")
    add_permission_args(sp)
    sp.add_argument("--fresh", action="store_true", help="start a new conversation instead of resuming")
    sp = named("ask", "send a message (auto-opens), wait, print Claude's reply")
    sp.add_argument("text", nargs="?", help="message; '-' reads stdin")
    sp.add_argument("-f", "--file", help="read the message from FILE")
    sp.add_argument("--timeout", type=int, default=600)
    sp.add_argument("--cwd"); sp.add_argument("--read-only", action="store_true"); sp.add_argument("--model")
    add_permission_args(sp)
    named("state", "print idle|busy|approval|secret|clarify|blocked|unknown|dead|missing")
    sp = named("read", "print recent transcript text")
    sp.add_argument("-n", "--lines", type=int, default=120)
    sp = named("answer", "answer a free-text question Claude asked (clarify state)")
    sp.add_argument("text")
    sp = named("keys", "send raw keys to Claude's UI (only when the user explicitly decided)")
    sp.add_argument("keys", nargs="+")
    named("session", "print the Claude session id")
    named("close", "send /exit and close the tab (conversation stays resumable)")
    named("forget", "delete the stored session record")
    sub.add_parser("list", help="list Claude sessions")
    sub.add_parser("gc", help="close tabs whose Claude process is gone")
    sp = sub.add_parser("watch", help="event watcher that forwards blocked/done into Hermes")
    sp.add_argument("action", choices=["start", "stop", "status", "run"])
    sp = sub.add_parser("setup-webhook", help="create the Hermes webhook route the watcher posts to")
    sp.add_argument("--route", default="claude-bridge"); sp.add_argument("--deliver", default="telegram")
    sp.add_argument("--secret", help="use this HMAC secret instead of the one Hermes generates")
    return p


def _text(args) -> str:
    if args.file:
        try:
            with open(args.file, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError as e:
            raise hb.UsageError("cannot read %s: %s" % (args.file, e))
    elif args.text in (None, "-"):
        text = sys.stdin.read()
    else:
        text = args.text
    if not text.strip():
        raise hb.UsageError("empty message")
    return text


def _resolve_permission_mode(args) -> str:
    """Combine open/ask's --permission-mode and --yolo into the single mode ensure_open needs,
    enforcing the documented conflicts up front (before any herdr call is made)."""
    mode = args.permission_mode
    if args.yolo:
        if mode not in (None, "manual"):
            raise hb.UsageError(
                "--yolo conflicts with --permission-mode %s (use one or the other)" % mode)
        mode = "bypassPermissions"
    mode = mode or "manual"
    if args.read_only and mode != "manual":
        raise hb.UsageError("--read-only requires --permission-mode manual")
    return mode


def main(argv=None, bridge_factory=None, stdout=None, stderr=None) -> int:
    out, err = stdout or sys.stdout, stderr or sys.stderr
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    try:
        if args.cmd in ("open", "ask"):
            permission_mode = _resolve_permission_mode(args)
        if args.cmd == "watch":
            import claude_bridge_watch as w
            return w.command(args.action, STATE_DIR, bridge_factory or default_bridge_factory, out, err)
        if args.cmd == "setup-webhook":
            import claude_bridge_webhook as wh
            cfg = wh.setup_route(STATE_DIR, args.route, args.deliver, args.secret)
            out.write("webhook route %r ready; posting to %s\n" % (cfg.route, cfg.url))
            return 0
        b = (bridge_factory or default_bridge_factory)()
        b.h.ensure_server()
        if args.cmd == "list":
            for r in b.list_sessions():
                out.write("%-32s %-8s %-10s %s\n" % (r["name"], r["pane_id"] or "-", r["state"], r["session_id"] or "-"))
            return 0
        if args.cmd == "gc":
            for t in b.gc():
                out.write("closed %s\n" % t)
            return 0
        name = hb.validate_name(args.name)
        if args.cmd == "open":
            a = ensure_open(b, name, args.cwd, args.read_only, args.model, args.fresh, permission_mode)
            st = b.state(name)[0]
            out.write("%s %s %s\n" % (name, a.get("pane_id"), st))
            return 0 if st in ("idle", "busy") else hb.state_exit(st)
        if args.cmd == "ask":
            text = _text(args)
            a = ensure_open(b, name, args.cwd, args.read_only, args.model, False, permission_mode)
            state, reply, truncated, dialog = b.send(name, text, args.timeout * 1000)
            out.write(reply + ("\n" if reply and not reply.endswith("\n") else ""))
            if truncated:
                err.write("claude-bridge: reply anchor not found; printed best-effort tail. If it looks cut, run "
                          "`read %s -n 300` or ask Claude to write the answer to a file.\n" % name)
            if dialog:
                out.write("\n[claude-bridge] Claude is now %s and needs a human decision; dialog:\n%s\n" % (state, dialog.rstrip()))
            return hb.state_exit(state)
        if args.cmd == "state":
            out.write(b.state(name)[0] + "\n"); return 0
        if args.cmd == "read":
            out.write(b.read(name, args.lines)); return 0
        if args.cmd == "answer":
            st = b.answer(name, args.text); out.write(st + "\n"); return 0 if st == "busy" else hb.state_exit(st)
        if args.cmd == "keys":
            a = b.find_agent(name)
            if not a:
                raise hb.BridgeError("no live Claude session %r" % name, hb.EXIT_MISSING)
            b.h.cli("agent", "send-keys", name, *args.keys)
            out.write("sent %s\n" % " ".join(args.keys)); return 0
        if args.cmd == "session":
            a = b.find_agent(name)
            sid = ((a or {}).get("agent_session") or {}).get("value") or b.store.load(name).get("agent_session_id")
            if not sid:
                err.write("claude-bridge: no session id known for %r\n" % name); return hb.EXIT_ERROR
            out.write(sid + "\n"); return 0
        if args.cmd == "close":
            out.write("closed\n" if b.stop(name) else "nothing to close\n"); return 0
        if args.cmd == "forget":
            out.write("forgotten\n" if b.store.delete(name) else "nothing stored\n"); return 0
        raise hb.UsageError("unknown command %r" % args.cmd)
    except hb.BridgeError as e:
        err.write("claude-bridge: %s\n" % e)
        return e.code
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
