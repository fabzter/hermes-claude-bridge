"""claude-bridge — Hermes Agent drives named Claude Code sessions inside herdr panes."""
from __future__ import annotations

import argparse
import os
import shutil
import sys

import herdrbridge as hb

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(SKILL_DIR, "state")
READ_ONLY_ALLOWED = "Read,Grep,Glob,WebSearch,WebFetch"
READ_ONLY_DENIED = "Bash,Edit,Write,MultiEdit,NotebookEdit"
CLAUDE_CFG = hb.BridgeConfig(workspace_label="claude-bridge", kind="claude",
                             default_cwd=os.path.expanduser("~"), exit_command="/exit")


def herdr_bin() -> str:
    return os.environ.get("HERDR_BIN") or shutil.which("herdr") or "/opt/homebrew/bin/herdr"


def default_bridge_factory():
    return hb.Bridge(hb.Herdr(hb.session_name(), bin=herdr_bin()), CLAUDE_CFG, hb.StateStore(STATE_DIR))


def build_launch_args(read_only: bool, model: str | None) -> list:
    args = []
    if read_only:
        args += ["--allowedTools", READ_ONLY_ALLOWED, "--disallowedTools", READ_ONLY_DENIED]
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


def check_flags(bridge: hb.Bridge, name: str, agent: dict) -> None:
    stored = bridge.store.load(name).get("launch_flags") or []
    if stored and not flags_match(stored, live_argv(bridge, agent["pane_id"])):
        raise hb.BridgeError(
            "session %r is running without its requested flags (%s) — probably relaunched by herdr's "
            "restore as plain `claude --resume`, so read-only limits are NOT in effect. Run `close %s` "
            "then `open %s --read-only` to restore them." % (name, " ".join(stored), name, name), hb.EXIT_ERROR)


def ensure_open(bridge: hb.Bridge, name: str, cwd: str | None, read_only: bool, model: str | None, fresh: bool) -> dict:
    kind, _ = bridge.resolve(name)
    if kind == "live":
        requested = build_launch_args(read_only, model)
        stored = bridge.store.load(name).get("launch_flags") or []
        if any(tok not in stored for tok in requested):
            raise hb.BridgeError(
                "session %r is already running without the requested flags (%s); run `close %s` then "
                "`open %s %s` to apply them"
                % (name, " ".join(requested), name, name, "--read-only" if read_only else "--model " + model),
                hb.EXIT_ERROR)
        agent = bridge.start(name, [], fresh=False, cwd=cwd)
    else:
        flags = build_launch_args(read_only, model)
        if not flags:
            st = bridge.store.load(name)
            if st.get("launch_flags"):
                flags = list(st["launch_flags"])
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

    sp = named("open", "open (or resume) a Claude session in a herdr pane")
    sp.add_argument("--cwd", help="directory Claude works in (default: $HOME or the stored one)")
    sp.add_argument("--read-only", action="store_true", help="allow only Read/Grep/Glob/WebSearch/WebFetch")
    sp.add_argument("--model", help="Claude model name")
    sp.add_argument("--fresh", action="store_true", help="start a new conversation instead of resuming")
    sp = named("ask", "send a message (auto-opens), wait, print Claude's reply")
    sp.add_argument("text", nargs="?", help="message; '-' reads stdin")
    sp.add_argument("-f", "--file", help="read the message from FILE")
    sp.add_argument("--timeout", type=int, default=600)
    sp.add_argument("--cwd"); sp.add_argument("--read-only", action="store_true"); sp.add_argument("--model")
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


def main(argv=None, bridge_factory=None, stdout=None, stderr=None) -> int:
    out, err = stdout or sys.stdout, stderr or sys.stderr
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    try:
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
            a = ensure_open(b, name, args.cwd, args.read_only, args.model, args.fresh)
            st = b.state(name)[0]
            out.write("%s %s %s\n" % (name, a.get("pane_id"), st))
            return 0 if st in ("idle", "busy") else hb.state_exit(st)
        if args.cmd == "ask":
            text = _text(args)
            a = ensure_open(b, name, args.cwd, args.read_only, args.model, False)
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
