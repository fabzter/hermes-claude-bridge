import functools, io, os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "claude-bridge", "scripts"))
import herdrbridge as hb
import claude_bridge_cli as cli
import claude_bridge_watch as w
import claude_bridge_webhook as wh
from fakes import FakeHerdr, agent, ok

WS = {"workspace_id": "w2", "label": "claude-bridge"}
def cagent(name="cv", pane="w2:p1", status="idle"):
    return agent(name, pane=pane, tab="w2:t1", ws="w2", status=status, kind="claude")


class ShouldForwardTests(unittest.TestCase):
    def test_transitions(self):
        self.assertTrue(w.should_forward("working", "blocked"))
        self.assertTrue(w.should_forward(None, "blocked"))
        self.assertTrue(w.should_forward("working", "done"))
        self.assertTrue(w.should_forward("working", "idle"))
        self.assertFalse(w.should_forward("blocked", "blocked"))
        self.assertFalse(w.should_forward("idle", "done"))
        self.assertFalse(w.should_forward(None, "idle"))
        self.assertFalse(w.should_forward("idle", "working"))


class WatcherTests(unittest.TestCase):
    def make(self, agents, text="Do you want to proceed?\n❯ 1. Yes\n"):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "pane list": [ok("pane_list", panes=[{"pane_id": a["pane_id"], "tab_id": "w2:t1"} for a in agents])],
                       "agent list": [ok("agent_list", agents=agents)],
                       "agent explain": [{"matched_rule": {"id": "bash_permission_prompt"}}]},
                      {"agent read": [text]})
        b = hb.Bridge(h, cli.CLAUDE_CFG, hb.StateStore(tempfile.mkdtemp()))
        posted = []
        cfg = wh.WebhookConfig("claude-bridge", "k", "http://127.0.0.1:8644/webhooks/claude-bridge")
        watcher = w.Watcher(b, cfg, poster=lambda c, p: posted.append(p) or 200, log=io.StringIO())
        return watcher, posted, h

    def test_subscriptions_cover_panes_and_lifecycle(self):
        watcher, _, _ = self.make([cagent()])
        subs = watcher.subscriptions()
        self.assertIn({"type": "pane.agent_status_changed", "pane_id": "w2:p1"}, subs)
        self.assertIn({"type": "pane.created"}, subs); self.assertIn({"type": "pane.closed"}, subs)

    def test_blocked_event_posts_payload_with_session_name(self):
        watcher, posted, _ = self.make([cagent(status="blocked")])
        env = {"event": "pane.agent_status_changed", "data": {"pane_id": "w2:p1", "workspace_id": "w2", "agent_status": "working"}}
        self.assertIsNone(watcher.handle(env))
        env["data"]["agent_status"] = "blocked"
        payload = watcher.handle(env)
        self.assertEqual(payload["event_type"], "claude_blocked"); self.assertEqual(payload["session"], "cv")
        self.assertEqual(payload["state"], "approval"); self.assertIn("proceed", payload["excerpt"])
        self.assertEqual(posted, [payload])

    def test_repeated_blocked_is_debounced(self):
        watcher, posted, _ = self.make([cagent(status="blocked")])
        env = {"event": "pane.agent_status_changed", "data": {"pane_id": "w2:p1", "workspace_id": "w2", "agent_status": "blocked"}}
        watcher.handle(env); watcher.handle(env)
        self.assertEqual(len(posted), 1)

    def test_done_after_working_posts_claude_done(self):
        watcher, posted, _ = self.make([cagent(status="idle")], text="> q\n⏺ answer\n")
        for st in ("working", "done"):
            watcher.handle({"event": "pane.agent_status_changed", "data": {"pane_id": "w2:p1", "workspace_id": "w2", "agent_status": st}})
        self.assertEqual(posted[0]["event_type"], "claude_done"); self.assertEqual(posted[0]["state"], "idle")

    def test_pane_lifecycle_event_requests_resubscribe(self):
        watcher, _, _ = self.make([cagent()])
        self.assertIsNone(watcher.handle({"event": "pane.created", "data": {"pane_id": "w2:p9", "workspace_id": "w2"}}))
        self.assertTrue(watcher.resubscribe_requested)

    def test_events_outside_workspace_ignored(self):
        watcher, posted, _ = self.make([cagent(status="blocked")])
        watcher.handle({"event": "pane.agent_status_changed", "data": {"pane_id": "w1:p1", "workspace_id": "w1", "agent_status": "blocked"}})
        self.assertEqual(posted, [])

    def test_poster_error_status_logged_as_failed(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "pane list": [ok("pane_list", panes=[{"pane_id": "w2:p1", "tab_id": "w2:t1"}])],
                       "agent list": [ok("agent_list", agents=[cagent(status="blocked")])],
                       "agent explain": [{"matched_rule": {"id": "bash_permission_prompt"}}]},
                      {"agent read": ["Do you want to proceed?\n❯ 1. Yes\n"]})
        b = hb.Bridge(h, cli.CLAUDE_CFG, hb.StateStore(tempfile.mkdtemp()))
        cfg = wh.WebhookConfig("claude-bridge", "k", "http://127.0.0.1:8644/webhooks/claude-bridge")
        log = io.StringIO()
        watcher = w.Watcher(b, cfg, poster=lambda c, p: 401, log=log)
        env = {"event": "pane.agent_status_changed", "data": {"pane_id": "w2:p1", "workspace_id": "w2", "agent_status": "blocked"}}
        watcher.handle(env)
        self.assertIn("FAILED", log.getvalue())
        self.assertIn("401", log.getvalue())


class PidfileTests(unittest.TestCase):
    def test_status_without_pidfile(self):
        out, err = io.StringIO(), io.StringIO()
        rc = w.command("status", tempfile.mkdtemp(), lambda: None, out, err)
        self.assertEqual(rc, 1); self.assertIn("not running", out.getvalue())

    def test_stop_without_pidfile(self):
        out, err = io.StringIO(), io.StringIO()
        self.assertEqual(w.command("stop", tempfile.mkdtemp(), lambda: None, out, err), 0)

    def test_running_pid_none_for_dead_pid(self):
        d = tempfile.mkdtemp()
        with open(w._pidfile(d), "w") as f:
            f.write("999999")
        self.assertIsNone(w._running_pid(d))

    def test_running_pid_none_for_garbage_content(self):
        d = tempfile.mkdtemp()
        with open(w._pidfile(d), "w") as f:
            f.write("not-a-pid")
        self.assertIsNone(w._running_pid(d))


class FakeProc:
    def __init__(self, pid=4242):
        self.pid = pid


class StartLogRotationTests(unittest.TestCase):
    def test_start_rotates_an_oversized_watch_log_before_reopening_it(self):
        d = tempfile.mkdtemp()
        wh.save_config(d, wh.WebhookConfig("claude-bridge", "k", "http://127.0.0.1:8644/webhooks/claude-bridge"))
        log_path = os.path.join(d, "watch.log")
        with open(log_path, "wb") as f:
            f.write(b"x" * (6 * 1024 * 1024))
        rotated_path = log_path + ".1"
        self.assertFalse(os.path.exists(rotated_path))

        calls = []

        def fake_popen(*args, **kwargs):
            calls.append((args, kwargs))
            return FakeProc(pid=4242)

        orig_popen = w._popen
        w._popen = fake_popen
        try:
            out, err = io.StringIO(), io.StringIO()
            rc = w.command("start", d, lambda: None, out, err)
        finally:
            w._popen = orig_popen

        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 1)  # no real process was spawned
        self.assertTrue(os.path.exists(rotated_path))
        self.assertEqual(os.path.getsize(rotated_path), 6 * 1024 * 1024)
        # the log path itself must be a fresh (small) file after rotation, ready for the new run
        self.assertLess(os.path.getsize(log_path), 6 * 1024 * 1024)
        self.assertIn("4242", out.getvalue())


class WatcherLogRotationTests(unittest.TestCase):
    def make(self, agents, log_path, rotator=None):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "pane list": [ok("pane_list", panes=[{"pane_id": a["pane_id"], "tab_id": "w2:t1"} for a in agents])],
                       "agent list": [ok("agent_list", agents=agents)],
                       "agent explain": [{"matched_rule": {"id": "bash_permission_prompt"}}]},
                      {"agent read": ["Do you want to proceed?\n❯ 1. Yes\n"]})
        b = hb.Bridge(h, cli.CLAUDE_CFG, hb.StateStore(tempfile.mkdtemp()))
        cfg = wh.WebhookConfig("claude-bridge", "k", "http://127.0.0.1:8644/webhooks/claude-bridge")
        watcher = w.Watcher(b, cfg, poster=lambda c, p: 200, log=io.StringIO(), log_path=log_path, rotator=rotator)
        return watcher

    def test_rotate_log_invoked_every_500_handled_events(self):
        d = tempfile.mkdtemp()
        log_path = os.path.join(d, "watch.log")
        rotate_calls = []

        def fake_rotator(path):
            rotate_calls.append(path)
            return False

        watcher = self.make([cagent()], log_path, rotator=fake_rotator)
        env = {"event": "pane.created", "data": {"pane_id": "w2:p9", "workspace_id": "w2"}}
        for _ in range(500):
            watcher.handle(env)
        self.assertEqual(rotate_calls, [log_path])

        for _ in range(499):
            watcher.handle(env)
        self.assertEqual(rotate_calls, [log_path])  # not yet at the next 500 boundary
        watcher.handle(env)
        self.assertEqual(rotate_calls, [log_path, log_path])

    def test_rotate_log_not_invoked_without_log_path(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "pane list": [ok("pane_list", panes=[{"pane_id": "w2:p1", "tab_id": "w2:t1"}])],
                       "agent list": [ok("agent_list", agents=[cagent()])]}, {})
        b = hb.Bridge(h, cli.CLAUDE_CFG, hb.StateStore(tempfile.mkdtemp()))
        cfg = wh.WebhookConfig("claude-bridge", "k", "http://127.0.0.1:8644/webhooks/claude-bridge")
        rotate_calls = []
        watcher = w.Watcher(b, cfg, poster=lambda c, p: 200, log=io.StringIO(), rotator=lambda p: rotate_calls.append(p))
        env = {"event": "pane.created", "data": {"pane_id": "w2:p9", "workspace_id": "w2"}}
        for _ in range(500):
            watcher.handle(env)
        self.assertEqual(rotate_calls, [])

    def test_rotate_log_uses_module_level_hb_rotate_log_by_default(self):
        d = tempfile.mkdtemp()
        log_path = os.path.join(d, "watch.log")
        watcher = self.make([cagent()], log_path)  # no rotator injected
        env = {"event": "pane.created", "data": {"pane_id": "w2:p9", "workspace_id": "w2"}}
        orig = hb.rotate_log
        calls = []

        def fake(path, *a, **kw):
            calls.append(path)
            return False

        hb.rotate_log = fake
        try:
            for _ in range(500):
                watcher.handle(env)
        finally:
            hb.rotate_log = orig
        self.assertEqual(calls, [log_path])


class WatcherOwnedLogReopenTests(unittest.TestCase):
    def test_watcher_reopens_owned_log_after_rotation_and_redirects_fds(self):
        d = tempfile.mkdtemp()
        log_path = os.path.join(d, "watch.log")
        # Pre-fill the log above the (patched-small) rotation threshold, so the 500th handle()
        # call triggers a real rotation.
        with open(log_path, "wb") as f:
            f.write(b"x" * 5000)

        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "pane list": [ok("pane_list", panes=[{"pane_id": "w2:p1", "tab_id": "w2:t1"}])],
                       "agent list": [ok("agent_list", agents=[cagent(status="blocked")])],
                       "agent explain": [{"matched_rule": {"id": "bash_permission_prompt"}}]},
                      {"agent read": ["Do you want to proceed?\n❯ 1. Yes\n"]})
        b = hb.Bridge(h, cli.CLAUDE_CFG, hb.StateStore(tempfile.mkdtemp()))
        cfg = wh.WebhookConfig("claude-bridge", "k", "http://127.0.0.1:8644/webhooks/claude-bridge")

        # No `log=` override: the watcher opens and owns `log_path` itself, exactly like the
        # `watch run` path (`command("run")`) does.
        watcher = w.Watcher(b, cfg, poster=lambda c, p: 200, log_path=log_path)

        dup2_calls = []
        orig_dup2 = w._dup2
        w._dup2 = lambda fd, target: dup2_calls.append((fd, target))

        orig_rotate = hb.rotate_log
        hb.rotate_log = functools.partial(hb.rotate_log, max_bytes=10)

        created = {"event": "pane.created", "data": {"pane_id": "w2:p9", "workspace_id": "w2"}}
        blocked = {"event": "pane.agent_status_changed",
                   "data": {"pane_id": "w2:p1", "workspace_id": "w2", "agent_status": "blocked"}}
        try:
            for _ in range(499):
                watcher.handle(created)  # no-op events: bump the counter without logging anything
            payload = watcher.handle(blocked)  # 500th call: rotates+reopens, THEN logs this event
        finally:
            hb.rotate_log = orig_rotate
            w._dup2 = orig_dup2

        rotated_path = log_path + ".1"
        self.assertTrue(os.path.exists(rotated_path))
        self.assertEqual(os.path.getsize(rotated_path), 5000)
        self.assertIsNotNone(payload)

        watcher.log.flush()
        with open(log_path) as f:
            fresh_content = f.read()
        self.assertIn("posted", fresh_content)
        self.assertIn("cv", fresh_content)

        with open(rotated_path, "rb") as f:
            rotated_content = f.read()
        self.assertNotIn(b"posted", rotated_content)

        # fds 1 and 2 were redirected to the freshly reopened handle (recorded, never actually
        # dup2'd, so the test runner's own stdout/stderr are untouched).
        self.assertEqual(len(dup2_calls), 2)
        self.assertEqual({target for _, target in dup2_calls}, {1, 2})
        self.assertEqual({fd for fd, _ in dup2_calls}, {watcher.log.fileno()})


if __name__ == "__main__":
    unittest.main()
