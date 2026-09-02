import io, os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "claude-bridge", "scripts"))
import herdrbridge as hb
import claude_bridge_cli as cli
from fakes import FakeHerdr, agent, ok

WS = {"workspace_id": "w2", "label": "claude-bridge", "active_tab_id": "w2:t1"}
def cagent(name="cv", pane="w2:p1", tab="w2:t1", status="idle", session=None):
    return agent(name, pane=pane, tab=tab, ws="w2", status=status, session=session, kind="claude")


def run(argv, h, store=None):
    out, err = io.StringIO(), io.StringIO()
    store = store or hb.StateStore(tempfile.mkdtemp())
    rc = cli.main(argv, bridge_factory=lambda: hb.Bridge(h, cli.CLAUDE_CFG, store), stdout=out, stderr=err)
    return rc, out.getvalue(), err.getvalue(), store


class LaunchArgTests(unittest.TestCase):
    def test_default_is_empty(self):
        self.assertEqual(cli.build_launch_args(False, None), [])

    def test_read_only_and_model(self):
        self.assertEqual(cli.build_launch_args(True, "opus"),
                         ["--allowedTools", cli.READ_ONLY_ALLOWED, "--disallowedTools", cli.READ_ONLY_DENIED, "--model", "opus"])

    def test_flags_match(self):
        argv = ["node", "/x/claude", "--resume", "abc", "--allowedTools", cli.READ_ONLY_ALLOWED, "--disallowedTools", cli.READ_ONLY_DENIED]
        self.assertTrue(cli.flags_match(cli.build_launch_args(True, None), argv))
        self.assertFalse(cli.flags_match(cli.build_launch_args(True, None), ["node", "/x/claude", "--resume", "abc"]))
        self.assertTrue(cli.flags_match([], ["claude", "--resume", "abc"]))


class OpenAskTests(unittest.TestCase):
    def test_open_creates_tab_and_starts_claude_with_flags(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[]), ok("agent_list", agents=[]), ok("agent_list", agents=[cagent(session="C1")])],
                       "tab create": [ok("tab_created", tab={"tab_id": "w2:t1"}, root_pane={"pane_id": "w2:p1"})],
                       "agent start": [ok("agent_started", agent=cagent(session="C1"))],
                       "pane process-info": [ok("pane_process_info", process_info={"foreground_processes": [{"name": "node", "argv": ["node", "/x/claude", "--allowedTools", cli.READ_ONLY_ALLOWED, "--disallowedTools", cli.READ_ONLY_DENIED]}]})]})
        rc, out, _, store = run(["open", "cv", "--cwd", "/tmp", "--read-only"], h)
        self.assertEqual(rc, 0)
        start = [c for c in h.calls if c[:3] == ("cli", "agent", "start")][0]
        self.assertEqual(start[3:8], ("cv", "--kind", "claude", "--pane", "w2:p1"))
        self.assertEqual(start[start.index("--") + 1:], ("--allowedTools", cli.READ_ONLY_ALLOWED, "--disallowedTools", cli.READ_ONLY_DENIED))
        tab = [c for c in h.calls if c[:3] == ("cli", "tab", "create")][0]
        self.assertIn("/tmp", tab)
        self.assertEqual(store.load("cv")["launch_flags"], cli.build_launch_args(True, None))
        self.assertEqual(store.load("cv")["agent_session_id"], "C1")

    def test_ask_auto_opens_then_prompts(self):
        after = "> hello\n\n⏺ hi there\n\n╭──╮\n│ ❯ │\n╰──╯\n"
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[]), ok("agent_list", agents=[]), ok("agent_list", agents=[cagent()])],
                       "tab create": [ok("tab_created", tab={"tab_id": "w2:t1"}, root_pane={"pane_id": "w2:p1"})],
                       "agent start": [ok("agent_started", agent=cagent())],
                       "agent prompt": [ok("agent_prompt", agent=cagent())]},
                      {"agent read": ["", after]})
        rc, out, _, _ = run(["ask", "cv", "hello"], h)
        self.assertEqual((rc, out.strip()), (0, "hi there"))
        self.assertTrue([c for c in h.calls if c[:3] == ("cli", "agent", "start")])

    def test_ask_blocked_prints_dialog_exit_3(self):
        blocked = cagent(status="blocked")
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[cagent()]), ok("agent_list", agents=[cagent()]), ok("agent_list", agents=[cagent()]), ok("agent_list", agents=[blocked])],
                       "agent prompt": [ok("agent_prompt", agent=blocked)],
                       "agent explain": [{"matched_rule": {"id": "bash_permission_prompt"}}]},
                      {"agent read": ["", "> do it\n⏺ Bash(rm -rf x)\nDo you want to proceed?\n❯ 1. Yes\n  2. No\n", "Do you want to proceed?\n❯ 1. Yes\n  2. No\n"]})
        rc, out, _, _ = run(["ask", "cv", "do it"], h)
        self.assertEqual(rc, 3); self.assertIn("Do you want to proceed?", out); self.assertIn("approval", out)

    def test_ask_refuses_on_flag_mismatch_after_restore(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[cagent()])],
                       "pane process-info": [ok("pane_process_info", process_info={"foreground_processes": [{"name": "node", "argv": ["node", "/x/claude", "--resume", "C1"]}]})]})
        store = hb.StateStore(tempfile.mkdtemp()); store.save("cv", launch_flags=cli.build_launch_args(True, None), pane_id="w2:p1")
        rc, _, err, _ = run(["ask", "cv", "hello"], h, store)
        self.assertEqual(rc, 1); self.assertIn("read-only", err); self.assertIn("close", err)

    def test_keys_sends_each_key(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[cagent(status="blocked")])],
                       "agent send-keys": [ok("agent_send_keys")]})
        rc, _, _, _ = run(["keys", "cv", "down", "enter"], h)
        self.assertEqual(rc, 0)
        self.assertEqual([c for c in h.calls if c[:3] == ("cli", "agent", "send-keys")][0][3:], ("cv", "down", "enter"))

    def test_state_and_close(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[cagent()]), ok("agent_list", agents=[cagent()]), ok("agent_list", agents=[])],
                       "agent prompt": [ok("agent_prompt", agent=cagent())],
                       "tab close": [ok("tab_closed")]})
        rc, out, _, _ = run(["state", "cv"], h); self.assertEqual((rc, out.strip()), (0, "idle"))
        rc, out, _, _ = run(["close", "cv"], h); self.assertEqual(rc, 0); self.assertIn("closed", out)
        self.assertEqual([c for c in h.calls if c[:3] == ("cli", "agent", "prompt")][0][3:5], ("cv", "/exit"))


if __name__ == "__main__":
    unittest.main()
