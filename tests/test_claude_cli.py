import io, os, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "claude-bridge", "scripts"))
import herdrbridge as hb
import claude_bridge_cli as cli
from fakes import FakeHerdr, agent, ok

SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "claude-bridge", "scripts")

WS = {"workspace_id": "w2", "label": "claude-bridge", "active_tab_id": "w2:t1"}
READY_SHELL = ok("pane_process_info", process_info={"foreground_processes": [{"name": "zsh", "argv": ["-zsh"]}]})
def cagent(name="cv", pane="w2:p1", tab="w2:t1", status="idle", session=None):
    return agent(name, pane=pane, tab=tab, ws="w2", status=status, session=session, kind="claude")


def run(argv, h, store=None):
    out, err = io.StringIO(), io.StringIO()
    store = store or hb.StateStore(tempfile.mkdtemp())
    rc = cli.main(argv, bridge_factory=lambda: hb.Bridge(h, cli.CLAUDE_CFG, store), stdout=out, stderr=err)
    return rc, out.getvalue(), err.getvalue(), store


class LaunchArgTests(unittest.TestCase):
    def test_default_pins_permission_mode(self):
        self.assertEqual(cli.build_launch_args(False, None), ["--permission-mode", "manual"])

    def test_read_only_and_model(self):
        self.assertEqual(cli.build_launch_args(True, "opus"),
                         ["--permission-mode", "manual",
                          "--allowedTools", cli.READ_ONLY_ALLOWED, "--disallowedTools", cli.READ_ONLY_DENIED]
                         + cli.READ_ONLY_MCP + ["--model", "opus"])

    def test_read_only_disables_inherited_mcp_servers(self):
        args = cli.build_launch_args(True, None)
        self.assertIn("--strict-mcp-config", args)
        i = args.index("--strict-mcp-config")
        self.assertEqual(args[i:i + 3], ["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}'])

    def test_flags_match(self):
        argv = (["node", "/x/claude", "--resume", "abc", "--permission-mode", "manual",
                  "--allowedTools", cli.READ_ONLY_ALLOWED,
                  "--disallowedTools", cli.READ_ONLY_DENIED] + cli.READ_ONLY_MCP)
        self.assertTrue(cli.flags_match(cli.build_launch_args(True, None), argv))
        self.assertFalse(cli.flags_match(cli.build_launch_args(True, None), ["node", "/x/claude", "--resume", "abc"]))
        self.assertTrue(cli.flags_match([], ["claude", "--resume", "abc"]))

    def test_read_only_denied_includes_escalating_builtins(self):
        for name in ("Bash", "Edit", "Write", "NotebookEdit", "Agent", "Workflow", "Skill", "Artifact", "Task"):
            self.assertIn(name, cli.READ_ONLY_DENIED.split(","))

    def test_merge_launch_args_identity_on_either_empty_side(self):
        # Guards _LAUNCH_FLAG_ORDER against silently dropping a flag that build_launch_args
        # gained but merge_launch_args's flag table didn't: unioning a full build_launch_args()
        # list with an empty list, either direction, must reproduce it exactly.
        for read_only in (False, True):
            for model in (None, "opus"):
                built = cli.build_launch_args(read_only, model)
                self.assertEqual(cli.merge_launch_args(built, []), built,
                                  "merge_launch_args(built, []) for read_only=%r model=%r" % (read_only, model))
                self.assertEqual(cli.merge_launch_args([], built), built,
                                  "merge_launch_args([], built) for read_only=%r model=%r" % (read_only, model))


class StateDirTests(unittest.TestCase):
    def test_env_override_honored(self):
        d = tempfile.mkdtemp()
        script = (
            "import os, sys\n"
            "os.environ['CLAUDE_BRIDGE_STATE_DIR'] = %r\n"
            "sys.path.insert(0, %r)\n"
            "import claude_bridge_cli as cli\n"
            "print(cli.STATE_DIR)\n"
        ) % (d, SCRIPTS_DIR)
        res = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.strip(), d)

    def test_default_state_dir_under_skill_dir(self):
        script = (
            "import sys\n"
            "sys.path.insert(0, %r)\n"
            "import claude_bridge_cli as cli\n"
            "print(cli.STATE_DIR)\n"
        ) % (SCRIPTS_DIR,)
        env = dict(os.environ)
        env.pop("CLAUDE_BRIDGE_STATE_DIR", None)
        res = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.strip(), os.path.join(cli.SKILL_DIR, "state"))


class OpenAskTests(unittest.TestCase):
    def test_open_creates_tab_and_starts_claude_with_flags(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[]), ok("agent_list", agents=[]), ok("agent_list", agents=[cagent(session="C1")])],
                       "tab create": [ok("tab_created", tab={"tab_id": "w2:t1"}, root_pane={"pane_id": "w2:p1"})],
                       "agent start": [ok("agent_started", agent=cagent(session="C1"))],
                       "pane get": [ok("pane_info", pane={"pane_id": "w2:p1"})],
                       "pane process-info": [READY_SHELL,
                           ok("pane_process_info", process_info={"foreground_processes": [{"name": "node", "argv": ["node", "/x/claude"] + cli.build_launch_args(True, None)}]})]})
        rc, out, _, store = run(["open", "cv", "--cwd", "/tmp", "--read-only"], h)
        self.assertEqual(rc, 0)
        start = [c for c in h.calls if c[:3] == ("cli", "agent", "start")][0]
        self.assertEqual(start[3:8], ("cv", "--kind", "claude", "--pane", "w2:p1"))
        self.assertEqual(start[start.index("--") + 1:], tuple(cli.build_launch_args(True, None)))
        tab = [c for c in h.calls if c[:3] == ("cli", "tab", "create")][0]
        self.assertIn("/tmp", tab)
        self.assertEqual(store.load("cv")["launch_flags"], cli.build_launch_args(True, None))
        self.assertEqual(store.load("cv")["agent_session_id"], "C1")

    def test_open_after_restart_unions_stored_read_only_with_requested_model(self):
        flags_final = cli.build_launch_args(True, None) + ["--model", "opus"]
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[]), ok("agent_list", agents=[]), ok("agent_list", agents=[cagent(name="ro", session="C2")])],
                       "tab create": [ok("tab_created", tab={"tab_id": "w2:t1"}, root_pane={"pane_id": "w2:p1"})],
                       "agent start": [ok("agent_started", agent=cagent(name="ro", session="C2"))],
                       "pane get": [ok("pane_info", pane={"pane_id": "w2:p1"})],
                       "pane process-info": [READY_SHELL,
                           ok("pane_process_info", process_info={"foreground_processes": [{"name": "node", "argv": ["node", "/x/claude"] + flags_final}]})]})
        store = hb.StateStore(tempfile.mkdtemp())
        store.save("ro", launch_flags=cli.build_launch_args(True, None))
        rc, out, _, store = run(["open", "ro", "--model", "opus"], h, store)
        self.assertEqual(rc, 0)
        start = [c for c in h.calls if c[:3] == ("cli", "agent", "start")][0]
        argv_sent = start[start.index("--") + 1:]
        for tok in cli.build_launch_args(True, None):
            self.assertIn(tok, argv_sent)
        self.assertIn("--model", argv_sent)
        self.assertIn("opus", argv_sent)
        self.assertEqual(store.load("ro")["launch_flags"], flags_final)

    def test_open_after_restart_replaces_model_value_without_duplicating(self):
        stored_flags = ["--permission-mode", "manual", "--model", "opus"]
        flags_final = ["--permission-mode", "manual", "--model", "sonnet"]
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[]), ok("agent_list", agents=[]), ok("agent_list", agents=[cagent(session="C3")])],
                       "tab create": [ok("tab_created", tab={"tab_id": "w2:t1"}, root_pane={"pane_id": "w2:p1"})],
                       "agent start": [ok("agent_started", agent=cagent(session="C3"))],
                       "pane get": [ok("pane_info", pane={"pane_id": "w2:p1"})],
                       "pane process-info": [READY_SHELL,
                           ok("pane_process_info", process_info={"foreground_processes": [{"name": "node", "argv": ["node", "/x/claude"] + flags_final}]})]})
        store = hb.StateStore(tempfile.mkdtemp())
        store.save("cv", launch_flags=stored_flags)
        rc, out, _, store = run(["open", "cv", "--model", "sonnet"], h, store)
        self.assertEqual(rc, 0)
        start = [c for c in h.calls if c[:3] == ("cli", "agent", "start")][0]
        argv_sent = start[start.index("--") + 1:]
        self.assertEqual(argv_sent.count("--model"), 1)
        self.assertEqual(argv_sent, tuple(flags_final))
        self.assertEqual(store.load("cv")["launch_flags"], flags_final)

    def test_ask_auto_opens_then_prompts(self):
        after = "> hello\n\n⏺ hi there\n\n╭──╮\n│ ❯ │\n╰──╯\n"
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[]), ok("agent_list", agents=[]), ok("agent_list", agents=[cagent()])],
                       "tab create": [ok("tab_created", tab={"tab_id": "w2:t1"}, root_pane={"pane_id": "w2:p1"})],
                       "agent start": [ok("agent_started", agent=cagent())],
                       "agent prompt": [ok("agent_prompt", agent=cagent())],
                       "pane get": [ok("pane_info", pane={"pane_id": "w2:p1"})],
                       "pane process-info": [READY_SHELL,
                           ok("pane_process_info", process_info={"foreground_processes": [{"name": "node", "argv": ["node", "/x/claude"] + cli.build_launch_args(False, None)}]})]},
                      {"agent read": ["", after]})
        rc, out, _, _ = run(["ask", "cv", "hello"], h)
        self.assertEqual((rc, out.strip()), (0, "hi there"))
        self.assertTrue([c for c in h.calls if c[:3] == ("cli", "agent", "start")])

    def test_ask_blocked_prints_dialog_exit_3(self):
        blocked = cagent(status="blocked")
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[cagent()]), ok("agent_list", agents=[cagent()]), ok("agent_list", agents=[cagent()]), ok("agent_list", agents=[blocked])],
                       "agent prompt": [ok("agent_prompt", agent=blocked)],
                       "agent explain": [{"matched_rule": {"id": "bash_permission_prompt"}}],
                       "pane process-info": [ok("pane_process_info", process_info={"foreground_processes": [{"name": "node", "argv": ["node", "/x/claude"] + cli.build_launch_args(False, None)}]})]},
                      {"agent read": ["", "> do it\n⏺ Bash(rm -rf x)\nDo you want to proceed?\n❯ 1. Yes\n  2. No\n", "Do you want to proceed?\n❯ 1. Yes\n  2. No\n"]})
        store = hb.StateStore(tempfile.mkdtemp())
        store.save("cv", launch_flags=cli.build_launch_args(False, None))
        rc, out, _, _ = run(["ask", "cv", "do it"], h, store)
        self.assertEqual(rc, 3); self.assertIn("Do you want to proceed?", out); self.assertIn("approval", out)

    def test_ask_refuses_on_flag_mismatch_after_restore(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[cagent()])],
                       "pane process-info": [ok("pane_process_info", process_info={"foreground_processes": [{"name": "node", "argv": ["node", "/x/claude", "--resume", "C1"]}]})]})
        store = hb.StateStore(tempfile.mkdtemp()); store.save("cv", launch_flags=cli.build_launch_args(True, None), pane_id="w2:p1")
        rc, _, err, _ = run(["ask", "cv", "hello"], h, store)
        self.assertEqual(rc, 1); self.assertIn("read-only", err); self.assertIn("close", err)

    def test_ask_refuses_on_flag_mismatch_after_restore_non_read_only(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[cagent()])],
                       "pane process-info": [ok("pane_process_info", process_info={"foreground_processes": [{"name": "node", "argv": ["node", "/x/claude", "--resume", "C1"]}]})]})
        store = hb.StateStore(tempfile.mkdtemp()); store.save("cv", launch_flags=cli.build_launch_args(False, None), pane_id="w2:p1")
        rc, _, err, _ = run(["ask", "cv", "hello"], h, store)
        self.assertEqual(rc, 1)
        self.assertNotIn("--read-only", err)
        self.assertIn("close cv", err); self.assertIn("open cv", err)

    def test_open_live_session_model_value_mismatch_lists_flag_value_pair(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[cagent()])]})
        store = hb.StateStore(tempfile.mkdtemp())
        store.save("cv", launch_flags=["--permission-mode", "manual", "--model", "opus"])
        rc, _, err, _ = run(["open", "cv", "--model", "sonnet"], h, store)
        self.assertEqual(rc, 1)
        self.assertIn("--model sonnet", err)
        self.assertIn("close cv", err)
        self.assertNotIn("Traceback", err)

    def test_ask_live_session_missing_only_permission_mode_no_traceback(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[cagent()])]})
        store = hb.StateStore(tempfile.mkdtemp()); store.save("cv", launch_flags=[])
        rc, _, err, _ = run(["ask", "cv", "hi"], h, store)
        self.assertEqual(rc, 1)
        self.assertIn("close cv", err); self.assertIn("open cv", err)
        self.assertNotIn("--read-only", err); self.assertNotIn("--model", err)
        self.assertNotIn("Traceback", err)

    def test_ask_live_session_missing_permission_mode_with_stored_model_no_traceback(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[cagent()])]})
        store = hb.StateStore(tempfile.mkdtemp()); store.save("cv", launch_flags=["--model", "opus"])
        rc, _, err, _ = run(["ask", "cv", "hi"], h, store)
        self.assertEqual(rc, 1)
        self.assertIn("close cv", err); self.assertIn("open cv", err)
        self.assertNotIn("Traceback", err)

    def test_open_read_only_on_live_default_session_refuses(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[cagent()])]})
        store = hb.StateStore(tempfile.mkdtemp()); store.save("cv", launch_flags=[])
        rc, _, err, _ = run(["open", "cv", "--read-only"], h, store)
        self.assertEqual(rc, 1); self.assertIn("already running", err); self.assertIn("close", err)
        self.assertFalse([c for c in h.calls if c[:3] == ("cli", "agent", "prompt")])

    def test_ask_read_only_on_live_default_session_refuses_before_prompt(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[cagent()])]})
        store = hb.StateStore(tempfile.mkdtemp()); store.save("cv", launch_flags=[])
        rc, _, err, _ = run(["ask", "cv", "hi", "--read-only"], h, store)
        self.assertEqual(rc, 1); self.assertIn("already running", err); self.assertIn("close", err)
        self.assertFalse([c for c in h.calls if c[:3] == ("cli", "agent", "prompt")])

    def test_open_read_only_on_live_read_only_session_is_ok(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[cagent()])],
                       "pane process-info": [ok("pane_process_info", process_info={"foreground_processes": [{"name": "node", "argv": ["node", "/x/claude"] + cli.build_launch_args(True, None)}]})]})
        store = hb.StateStore(tempfile.mkdtemp()); store.save("cv", launch_flags=cli.build_launch_args(True, None), pane_id="w2:p1")
        rc, out, _, _ = run(["open", "cv", "--read-only"], h, store)
        self.assertEqual(rc, 0)

    def test_ask_missing_file_is_usage_error(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[])]})
        rc, _, err, _ = run(["ask", "cv", "-f", "/nonexistent/file-that-does-not-exist"], h)
        self.assertEqual(rc, 2); self.assertIn("cannot read", err)

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
