"""Watch Claude panes over the herdr socket and forward blocked/done transitions to Hermes."""
from __future__ import annotations

import datetime
import os
import signal
import subprocess
import sys
import time

import herdrbridge as hb
import claude_bridge_webhook as wh


def should_forward(prev: str | None, new: str) -> bool:
    if new == "blocked":
        return prev != "blocked"
    if new in ("done", "idle"):
        return prev == "working"
    return False


class Watcher:
    def __init__(self, bridge: hb.Bridge, cfg: wh.WebhookConfig, poster=wh.post_webhook, log=None):
        self.b, self.cfg, self.poster = bridge, cfg, poster
        self.log = log or sys.stderr
        self.prev = {}
        self.resubscribe_requested = False

    def _log(self, msg: str) -> None:
        self.log.write("%s %s\n" % (datetime.datetime.now().strftime("%H:%M:%S"), msg))
        self.log.flush()

    def subscriptions(self) -> list:
        subs = [{"type": "pane.agent_status_changed", "pane_id": p["pane_id"]} for p in self.b.panes()]
        subs += [{"type": "pane.created"}, {"type": "pane.closed"}]
        return subs

    def _ws_id(self) -> str:
        return self.b.workspace()["workspace_id"]

    def handle(self, envelope: dict):
        kind = envelope.get("event") or envelope.get("type") or ""
        data = envelope.get("data") or envelope
        if data.get("workspace_id") not in (None, self._ws_id()):
            return None
        if kind in ("pane.created", "pane.closed"):
            self.resubscribe_requested = True
            return None
        if kind != "pane.agent_status_changed":
            return None
        pane_id, new = data.get("pane_id"), data.get("agent_status")
        prev = self.prev.get(pane_id)
        self.prev[pane_id] = new
        if not should_forward(prev, new):
            return None
        agent = None
        for a in self.b.agents():
            if a.get("pane_id") == pane_id:
                agent = a
        if not agent or not agent.get("name"):
            self._log("status %s on %s but no named agent; skipped" % (new, pane_id))
            return None
        name = agent["name"]
        rule = self.b.explain_rule(name) if new == "blocked" else None
        state = hb.classify(new, rule)
        excerpt = self.b.visible(name)
        payload = wh.build_payload(name, pane_id, state, excerpt)
        try:
            status = self.poster(self.cfg, payload)
            if status >= 300:
                self._log("WARN post FAILED for %s (%s) -> HTTP %s — re-run setup-webhook and restart the watcher"
                           % (name, state, status))
            else:
                self._log("posted %s for %s (%s) -> HTTP %s" % (payload["event_type"], name, state, status))
        except Exception as e:  # network errors must not kill the watcher
            self._log("post failed for %s: %s" % (name, e))
        return payload

    def run_once(self) -> None:
        self.resubscribe_requested = False
        subs = self.subscriptions()
        self._log("subscribing to %d panes" % (len(subs) - 2))
        for env in self.b.h.subscribe(subs):
            self.handle(env)
            if self.resubscribe_requested:
                return

    def run_forever(self, backoff_max: float = 30) -> None:
        delay = 1.0
        while True:
            try:
                self.b.h.ensure_server()
                self.run_once()
                delay = 1.0
            except (OSError, hb.BridgeError, ValueError) as e:
                self._log("subscription ended: %s; retrying in %.0fs" % (e, delay))
                time.sleep(delay)
                delay = min(delay * 2, backoff_max)


def _pidfile(state_dir: str) -> str:
    return os.path.join(state_dir, "watch.pid")


def _running_pid(state_dir: str) -> int | None:
    p = _pidfile(state_dir)
    try:
        with open(p) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def command(action: str, state_dir: str, bridge_factory, out, err) -> int:
    os.makedirs(state_dir, exist_ok=True)
    pid = _running_pid(state_dir)
    if action == "status":
        out.write("watcher running (pid %d)\n" % pid if pid else "watcher not running\n")
        return 0 if pid else 1
    if action == "stop":
        if pid:
            os.kill(pid, signal.SIGTERM)
            out.write("stopped watcher pid %d\n" % pid)
        else:
            out.write("watcher not running\n")
        try:
            os.remove(_pidfile(state_dir))
        except OSError:
            pass
        return 0
    if action == "start":
        if pid:
            out.write("watcher already running (pid %d)\n" % pid)
            return 0
        cfg = wh.load_config(state_dir)
        if not cfg:
            err.write("claude-bridge: no webhook configured; run `setup-webhook` first\n")
            return 1
        log = open(os.path.join(state_dir, "watch.log"), "ab")
        launcher = os.path.join(os.path.dirname(os.path.abspath(__file__)), "claude-bridge")
        proc = subprocess.Popen([sys.executable, launcher, "watch", "run"], stdin=subprocess.DEVNULL,
                                stdout=log, stderr=log, start_new_session=True, env=dict(os.environ))
        with open(_pidfile(state_dir), "w") as f:
            f.write(str(proc.pid))
        out.write("watcher started (pid %d), log: %s\n" % (proc.pid, os.path.join(state_dir, "watch.log")))
        return 0
    if action == "run":
        cfg = wh.load_config(state_dir)
        if not cfg:
            err.write("claude-bridge: no webhook configured; run `setup-webhook` first\n")
            return 1
        with open(_pidfile(state_dir), "w") as f:
            f.write(str(os.getpid()))
        b = bridge_factory()
        Watcher(b, cfg, log=sys.stderr).run_forever()
        return 0
    err.write("claude-bridge: unknown watch action %r\n" % action)
    return 2
