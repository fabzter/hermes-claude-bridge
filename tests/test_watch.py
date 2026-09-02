import io, os, sys, tempfile, unittest
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


class PidfileTests(unittest.TestCase):
    def test_status_without_pidfile(self):
        out, err = io.StringIO(), io.StringIO()
        rc = w.command("status", tempfile.mkdtemp(), lambda: None, out, err)
        self.assertEqual(rc, 1); self.assertIn("not running", out.getvalue())

    def test_stop_without_pidfile(self):
        out, err = io.StringIO(), io.StringIO()
        self.assertEqual(w.command("stop", tempfile.mkdtemp(), lambda: None, out, err), 0)


if __name__ == "__main__":
    unittest.main()
