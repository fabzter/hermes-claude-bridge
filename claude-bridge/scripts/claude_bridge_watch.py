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

# Indirected through a module-level name (rather than calling subprocess.Popen directly) so tests
# can inject a fake via `w._popen = ...` and exercise `command("start")` without spawning a real
# process.
_popen = subprocess.Popen

# Indirected the same way as `_popen`: tests replace `w._dup2` with a recorder so the fd-1/2
# redirect after log rotation (see `_maybe_rotate_log`) doesn't clobber the test runner's own
# stdout/stderr.
_dup2 = os.dup2


def should_forward(prev: str | None, new: str) -> bool:
    if new == "blocked":
        return prev != "blocked"
    if new in ("done", "idle"):
        return prev == "working"
    return False


class Watcher:
    def __init__(self, bridge: hb.Bridge, cfg: wh.WebhookConfig, poster=wh.post_webhook, log=None,
                 log_path: str | None = None, rotator=None):
        self.b, self.cfg, self.poster = bridge, cfg, poster
        self.log_path = log_path
        # Own the file handle only when the caller didn't hand us one explicitly (e.g. sys.stderr,
        # or a StringIO in tests) -- only a handle we opened ourselves is safe to close and reopen
        # on rotation.
        self._owns_log = log is None and log_path is not None
        self.log = log if log is not None else (open(log_path, "a") if log_path else sys.stderr)
        self.rotator = rotator
        self.prev = {}
        self.resubscribe_requested = False
        self._handled = 0

    def _log(self, msg: str) -> None:
        self.log.write("%s %s\n" % (datetime.datetime.now().strftime("%H:%M:%S"), msg))
        self.log.flush()

    def _maybe_rotate_log(self) -> None:
        """Called once per handled event; every 500th event, rotate `self.log_path` if it has
        grown past the size threshold and, when we own the file handle (see __init__), reopen it
        so subsequent writes land in the fresh post-rotation file rather than the renamed one."""
        if not self.log_path:
            return
        self._handled += 1
        if self._handled % 500 != 0:
            return
        rotate = self.rotator or hb.rotate_log
        if rotate(self.log_path) and self._owns_log:
            try:
                self.log.close()
            except Exception:
                pass
            self.log = open(self.log_path, "a")
            # The watcher process's own stdout/stderr (fds 1 and 2) are still pointed at the
            # pre-rotation file (they were inherited from `command("start")`'s spawn, or from the
            # shell in `command("run")`). Redirect them to the freshly reopened log too, so an
            # uncaught traceback printed after rotation lands in the fresh `watch.log` instead of
            # the renamed `watch.log.1`. Best-effort: never let this break the watcher.
            try:
                _dup2(self.log.fileno(), 1)
                _dup2(self.log.fileno(), 2)
            except Exception:
                pass

    def subscriptions(self) -> list:
        subs = [{"type": "pane.agent_status_changed", "pane_id": p["pane_id"]} for p in self.b.panes()]
        subs += [{"type": "pane.created"}, {"type": "pane.closed"}]
        return subs

    def _ws_id(self) -> str:
        return self.b.workspace()["workspace_id"]

    def handle(self, envelope: dict):
        self._maybe_rotate_log()
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
        log_path = os.path.join(state_dir, "watch.log")
        hb.rotate_log(log_path)
        log = open(log_path, "ab")
        launcher = os.path.join(os.path.dirname(os.path.abspath(__file__)), "claude-bridge")
        proc = _popen([sys.executable, launcher, "watch", "run"], stdin=subprocess.DEVNULL,
                      stdout=log, stderr=log, start_new_session=True, env=dict(os.environ))
        with open(_pidfile(state_dir), "w") as f:
            f.write(str(proc.pid))
        out.write("watcher started (pid %d), log: %s\n" % (proc.pid, log_path))
        return 0
    if action == "run":
        if pid and pid != os.getpid():
            err.write("claude-bridge: watcher already running (pid %d); refusing to start a "
                      "second `watch run` (use `watch stop` first)\n" % pid)
            return 1
        cfg = wh.load_config(state_dir)
        if not cfg:
            err.write("claude-bridge: no webhook configured; run `setup-webhook` first\n")
            return 1
        with open(_pidfile(state_dir), "w") as f:
            f.write(str(os.getpid()))
        log_path = os.path.join(state_dir, "watch.log")
        hb.rotate_log(log_path)
        b = bridge_factory()
        Watcher(b, cfg, log_path=log_path).run_forever()
        return 0
    err.write("claude-bridge: unknown watch action %r\n" % action)
    return 2
