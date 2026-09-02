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

    def test_explicit_permission_mode(self):
        self.assertEqual(cli.build_launch_args(False, None, "bypassPermissions"),
                          ["--permission-mode", "bypassPermissions"])

    def test_permission_modes_tuple(self):
        self.assertEqual(cli.PERMISSION_MODES,
                          ("manual", "acceptEdits", "auto", "plan", "dontAsk", "bypassPermissions"))

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

    def test_flags_match_false_when_model_value_differs(self):
        argv = ["node", "/x/claude", "--resume", "abc", "--permission-mode", "manual", "--model", "sonnet"]
        self.assertFalse(cli.flags_match(cli.build_launch_args(False, "opus"), argv))

    def test_flags_match_true_with_same_pairs_in_different_order(self):
        argv = ["node", "/x/claude", "--model", "opus", "--permission-mode", "manual"]
        stored = ["--permission-mode", "manual", "--model", "opus"]
        self.assertTrue(cli.flags_match(stored, argv))

    def test_flags_match_true_with_real_executable_argv_shape(self):
        # Live argv as herdr/psutil actually report it for a real Claude Code launch: argv[0] is
        # the executable itself (a Homebrew-installed binary), not a "node" wrapper in front of a
        # separate script path -- _find_claude_argv_tail must still locate "claude" at index 0.
        argv = ["/opt/homebrew/Caskroom/claude-code/2.1.236/claude", "--permission-mode", "manual",
                "--allowedTools", cli.READ_ONLY_ALLOWED, "--disallowedTools", cli.READ_ONLY_DENIED] + cli.READ_ONLY_MCP
        self.assertTrue(cli.flags_match(cli.build_launch_args(True, None), argv))

    def test_flags_match_pair_aware_not_token_containment(self):
        # A stale --model value token can coincidentally reappear elsewhere in the live argv (here
        # as the value of an unrelated flag); naive token-containment would false-positive-match on
        # it. Pair-aware parsing must bind "opus" to its actual flag (--fallback-model) and see that
        # --model's own live value ("sonnet") doesn't match what's stored.
        argv = ["node", "/x/claude", "--model", "sonnet", "--fallback-model", "opus"]
        self.assertFalse(cli.flags_match(["--model", "opus"], argv))

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

    def test_open_yolo_starts_with_bypass_permissions(self):
        flags = cli.build_launch_args(False, None, "bypassPermissions")
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[]), ok("agent_list", agents=[]), ok("agent_list", agents=[cagent(name="y", session="C4")])],
                       "tab create": [ok("tab_created", tab={"tab_id": "w2:t1"}, root_pane={"pane_id": "w2:p1"})],
                       "agent start": [ok("agent_started", agent=cagent(name="y", session="C4"))],
                       "pane get": [ok("pane_info", pane={"pane_id": "w2:p1"})],
                       "pane process-info": [READY_SHELL,
                           ok("pane_process_info", process_info={"foreground_processes": [{"name": "node", "argv": ["node", "/x/claude"] + flags}]})]})
        rc, out, _, store = run(["open", "y", "--yolo"], h)
        self.assertEqual(rc, 0)
        start = [c for c in h.calls if c[:3] == ("cli", "agent", "start")][0]
        argv_sent = start[start.index("--") + 1:]
        self.assertIn("--permission-mode", argv_sent)
        self.assertIn("bypassPermissions", argv_sent)
        self.assertEqual(store.load("y")["launch_flags"], flags)

    def test_open_explicit_permission_mode_plan(self):
        flags = cli.build_launch_args(False, None, "plan")
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[]), ok("agent_list", agents=[]), ok("agent_list", agents=[cagent(name="y", session="C6")])],
                       "tab create": [ok("tab_created", tab={"tab_id": "w2:t1"}, root_pane={"pane_id": "w2:p1"})],
                       "agent start": [ok("agent_started", agent=cagent(name="y", session="C6"))],
                       "pane get": [ok("pane_info", pane={"pane_id": "w2:p1"})],
                       "pane process-info": [READY_SHELL,
                           ok("pane_process_info", process_info={"foreground_processes": [{"name": "node", "argv": ["node", "/x/claude"] + flags}]})]})
        rc, out, _, store = run(["open", "y", "--permission-mode", "plan"], h)
        self.assertEqual(rc, 0)
        start = [c for c in h.calls if c[:3] == ("cli", "agent", "start")][0]
        argv_sent = start[start.index("--") + 1:]
        self.assertIn("plan", argv_sent)
        self.assertEqual(store.load("y")["launch_flags"], flags)

    def test_open_read_only_with_yolo_is_usage_error(self):
        h = FakeHerdr({})
        rc, _, err, _ = run(["open", "ro", "--read-only", "--yolo"], h)
        self.assertEqual(rc, 2)
        self.assertIn("--read-only requires --permission-mode manual", err)

    def test_open_yolo_conflicts_with_explicit_permission_mode(self):
        h = FakeHerdr({})
        rc, _, err, _ = run(["open", "y", "--yolo", "--permission-mode", "plan"], h)
        self.assertEqual(rc, 2)
        self.assertIn("--yolo", err)
        self.assertIn("--permission-mode", err)

    def test_ask_yolo_on_live_manual_session_refuses_before_prompt(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[cagent()])]})
        store = hb.StateStore(tempfile.mkdtemp())
        store.save("cv", launch_flags=cli.build_launch_args(False, None))
        rc, _, err, _ = run(["ask", "cv", "hi", "--yolo"], h, store)
        self.assertEqual(rc, 1)
        self.assertIn("already running", err)
        self.assertIn("--permission-mode bypassPermissions", err)
        self.assertFalse([c for c in h.calls if c[:3] == ("cli", "agent", "prompt")])

    def test_ask_bare_on_live_yolo_session_is_allowed(self):
        # The other half of the (a) fix: a bare ask/open on an already-live --yolo session must
        # NOT be refused just because it didn't repeat --yolo -- only an explicit, different mode
        # should trigger the close/open remediation (covered by the two tests above/below).
        after = "> hi\n\n⏺ hi there\n\n╭──╮\n│ ❯ │\n╰──╯\n"
        flags = cli.build_launch_args(False, None, "bypassPermissions")
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[cagent()])],
                       "agent prompt": [ok("agent_prompt", agent=cagent())],
                       "pane process-info": [ok("pane_process_info", process_info={"foreground_processes": [{"name": "node", "argv": ["node", "/x/claude", "--resume", "C1"] + flags}]})]},
                      {"agent read": ["", after]})
        store = hb.StateStore(tempfile.mkdtemp())
        store.save("cv", launch_flags=flags)
        rc, out, _, _ = run(["ask", "cv", "hi"], h, store)
        self.assertEqual((rc, out.strip()), (0, "hi there"))
        self.assertTrue([c for c in h.calls if c[:3] == ("cli", "agent", "prompt")])

    def test_ask_explicit_manual_on_live_yolo_session_refuses(self):
        # An explicit ask always wins over "session is already live under something else" --
        # asking for manual on a live --yolo session must still be refused with the close/open
        # remediation, exactly like asking for --yolo on a live manual session already is above.
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[cagent()])]})
        store = hb.StateStore(tempfile.mkdtemp())
        store.save("cv", launch_flags=cli.build_launch_args(False, None, "bypassPermissions"))
        rc, _, err, _ = run(["ask", "cv", "hi", "--permission-mode", "manual"], h, store)
        self.assertEqual(rc, 1)
        self.assertIn("already running", err)
        self.assertIn("--permission-mode manual", err)
        self.assertIn("close cv", err); self.assertIn("open cv", err)
        self.assertFalse([c for c in h.calls if c[:3] == ("cli", "agent", "prompt")])

    def test_live_yolo_open_manual_remediation_includes_permission_mode_and_round_trips(self):
        # Critical 1 fix: the live-session refusal for an explicit `open cv --permission-mode
        # manual` must include `--permission-mode manual` in the printed remediation itself (not
        # just the "missing flags" token list) -- omitting it (as build_launch_args's ambient
        # default, correct only for check_flags) would have the printed `close cv` then `open cv`
        # remediation silently re-launch under the still-stored bypassPermissions mode, undoing
        # the very ask that was refused.
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[cagent(name="cv")])]})
        store = hb.StateStore(tempfile.mkdtemp())
        store.save("cv", launch_flags=cli.build_launch_args(False, None, "bypassPermissions"))
        rc, _, err, _ = run(["open", "cv", "--permission-mode", "manual"], h, store)
        self.assertEqual(rc, 1)
        self.assertIn("open cv --permission-mode manual", err)

        # Round-trip: running exactly that remediation ("close cv" then "open cv
        # --permission-mode manual") through the restart merge must actually land the session in
        # manual mode, not re-grant the stored bypassPermissions.
        h2 = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                        "agent list": [ok("agent_list", agents=[]), ok("agent_list", agents=[]),
                                       ok("agent_list", agents=[cagent(name="cv", session="C9")])],
                        "tab create": [ok("tab_created", tab={"tab_id": "w2:t1"}, root_pane={"pane_id": "w2:p1"})],
                        "agent start": [ok("agent_started", agent=cagent(name="cv", session="C9"))],
                        "pane get": [ok("pane_info", pane={"pane_id": "w2:p1"})],
                        "pane process-info": [READY_SHELL,
                            ok("pane_process_info", process_info={"foreground_processes": [{"name": "node", "argv": ["node", "/x/claude"] + cli.build_launch_args(False, None, "manual")}]})]})
        rc2, _, _, store2 = run(["open", "cv", "--permission-mode", "manual"], h2, store)
        self.assertEqual(rc2, 0)
        start = [c for c in h2.calls if c[:3] == ("cli", "agent", "start")][0]
        argv_sent = start[start.index("--") + 1:]
        self.assertIn("manual", argv_sent)
        self.assertNotIn("bypassPermissions", argv_sent)
        self.assertEqual(store2.load("cv")["launch_flags"], cli.build_launch_args(False, None, "manual"))

    def test_open_read_only_on_restart_with_stored_yolo_forces_manual_and_warns(self):
        # Critical 2 fix: on the restart/missing branch, --read-only with an unspecified
        # --permission-mode must not silently inherit a stored non-manual mode (which would
        # launch the exact bypassPermissions+read-only combination the CLI rejects with exit 2
        # when both flags are typed explicitly). It must force manual and warn on stderr.
        stored_flags = cli.build_launch_args(False, None, "bypassPermissions")
        flags_final = cli.build_launch_args(True, None, "manual")
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[]), ok("agent_list", agents=[]),
                                      ok("agent_list", agents=[cagent(name="y", session="C10")])],
                       "tab create": [ok("tab_created", tab={"tab_id": "w2:t1"}, root_pane={"pane_id": "w2:p1"})],
                       "agent start": [ok("agent_started", agent=cagent(name="y", session="C10"))],
                       "pane get": [ok("pane_info", pane={"pane_id": "w2:p1"})],
                       "pane process-info": [READY_SHELL,
                           ok("pane_process_info", process_info={"foreground_processes": [{"name": "node", "argv": ["node", "/x/claude"] + flags_final}]})]})
        store = hb.StateStore(tempfile.mkdtemp())
        store.save("y", launch_flags=stored_flags)
        rc, out, err, store = run(["open", "y", "--read-only"], h, store)
        self.assertEqual(rc, 0)
        start = [c for c in h.calls if c[:3] == ("cli", "agent", "start")][0]
        argv_sent = start[start.index("--") + 1:]
        self.assertIn("manual", argv_sent)
        self.assertNotIn("bypassPermissions", argv_sent)
        self.assertIn("--allowedTools", argv_sent)
        self.assertIn(
            "claude-bridge: --read-only forces --permission-mode manual (stored bypassPermissions dropped)",
            err)
        self.assertEqual(store.load("y")["launch_flags"], flags_final)

    def test_open_read_only_with_explicit_non_manual_mode_stays_usage_error(self):
        # The other half of Critical 2: an explicit non-manual --permission-mode combined with
        # --read-only remains a usage error (exit 2), unchanged.
        h = FakeHerdr({})
        rc, _, err, _ = run(["open", "y", "--read-only", "--permission-mode", "plan"], h)
        self.assertEqual(rc, 2)
        self.assertIn("--read-only requires --permission-mode manual", err)

    def test_open_reset_flags_builds_from_cli_only_and_replaces_stored(self):
        stored_flags = cli.build_launch_args(True, None)  # stored read-only, manual
        flags_final = cli.build_launch_args(False, None, "manual")
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[]), ok("agent_list", agents=[]),
                                      ok("agent_list", agents=[cagent(name="y", session="C11")])],
                       "tab create": [ok("tab_created", tab={"tab_id": "w2:t1"}, root_pane={"pane_id": "w2:p1"})],
                       "agent start": [ok("agent_started", agent=cagent(name="y", session="C11"))],
                       "pane get": [ok("pane_info", pane={"pane_id": "w2:p1"})],
                       "pane process-info": [READY_SHELL,
                           ok("pane_process_info", process_info={"foreground_processes": [{"name": "node", "argv": ["node", "/x/claude"] + flags_final}]})]})
        store = hb.StateStore(tempfile.mkdtemp())
        store.save("y", launch_flags=stored_flags, agent_session_id="OLDSESS")
        rc, out, _, store = run(["open", "y", "--reset-flags"], h, store)
        self.assertEqual(rc, 0)
        start = [c for c in h.calls if c[:3] == ("cli", "agent", "start")][0]
        argv_sent = start[start.index("--") + 1:]
        self.assertNotIn("--allowedTools", argv_sent)
        self.assertIn("--resume", argv_sent)
        self.assertIn("OLDSESS", argv_sent)
        self.assertEqual(store.load("y")["launch_flags"], flags_final)

    def test_open_reset_flags_refused_on_live_session(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[cagent(name="cv")])]})
        store = hb.StateStore(tempfile.mkdtemp())
        store.save("cv", launch_flags=cli.build_launch_args(True, None))
        rc, _, err, _ = run(["open", "cv", "--reset-flags"], h, store)
        self.assertEqual(rc, 1)
        self.assertIn("close cv", err)
        self.assertIn("--reset-flags", err)
        self.assertFalse([c for c in h.calls if c[:3] == ("cli", "agent", "start")])

    def test_open_fresh_on_live_session_refused(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[cagent(name="cv")])]})
        store = hb.StateStore(tempfile.mkdtemp())
        store.save("cv", launch_flags=cli.build_launch_args(False, None))
        rc, _, err, _ = run(["open", "cv", "--fresh"], h, store)
        self.assertEqual(rc, 1)
        self.assertIn("close cv", err)
        self.assertIn("--fresh", err)
        self.assertFalse([c for c in h.calls if c[:3] == ("cli", "agent", "start")])

    def test_open_after_restart_bare_open_keeps_stored_yolo(self):
        flags = cli.build_launch_args(False, None, "bypassPermissions")
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[]), ok("agent_list", agents=[]), ok("agent_list", agents=[cagent(name="y", session="C5")])],
                       "tab create": [ok("tab_created", tab={"tab_id": "w2:t1"}, root_pane={"pane_id": "w2:p1"})],
                       "agent start": [ok("agent_started", agent=cagent(name="y", session="C5"))],
                       "pane get": [ok("pane_info", pane={"pane_id": "w2:p1"})],
                       "pane process-info": [READY_SHELL,
                           ok("pane_process_info", process_info={"foreground_processes": [{"name": "node", "argv": ["node", "/x/claude"] + flags}]})]})
        store = hb.StateStore(tempfile.mkdtemp())
        store.save("y", launch_flags=flags)
        rc, out, _, store = run(["open", "y"], h, store)
        self.assertEqual(rc, 0)
        start = [c for c in h.calls if c[:3] == ("cli", "agent", "start")][0]
        argv_sent = start[start.index("--") + 1:]
        self.assertIn("bypassPermissions", argv_sent)
        self.assertEqual(store.load("y")["launch_flags"], flags)

    def test_open_after_restart_explicit_manual_overrides_stored_yolo(self):
        # The (b) fix: on a restart, an explicit ask always wins over whatever was stored, even
        # when the explicit ask is "manual" downgrading a previously granted --yolo.
        stored_flags = cli.build_launch_args(False, None, "bypassPermissions")
        flags_final = cli.build_launch_args(False, None, "manual")
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[]), ok("agent_list", agents=[]), ok("agent_list", agents=[cagent(name="y", session="C7")])],
                       "tab create": [ok("tab_created", tab={"tab_id": "w2:t1"}, root_pane={"pane_id": "w2:p1"})],
                       "agent start": [ok("agent_started", agent=cagent(name="y", session="C7"))],
                       "pane get": [ok("pane_info", pane={"pane_id": "w2:p1"})],
                       "pane process-info": [READY_SHELL,
                           ok("pane_process_info", process_info={"foreground_processes": [{"name": "node", "argv": ["node", "/x/claude"] + flags_final}]})]})
        store = hb.StateStore(tempfile.mkdtemp())
        store.save("y", launch_flags=stored_flags)
        rc, out, _, store = run(["open", "y", "--permission-mode", "manual"], h, store)
        self.assertEqual(rc, 0)
        start = [c for c in h.calls if c[:3] == ("cli", "agent", "start")][0]
        argv_sent = start[start.index("--") + 1:]
        self.assertIn("manual", argv_sent)
        self.assertNotIn("bypassPermissions", argv_sent)
        self.assertEqual(store.load("y")["launch_flags"], flags_final)

    def test_open_after_restart_yolo_overrides_stored_manual(self):
        stored_flags = cli.build_launch_args(False, None, "manual")
        flags_final = cli.build_launch_args(False, None, "bypassPermissions")
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[]), ok("agent_list", agents=[]), ok("agent_list", agents=[cagent(name="y", session="C8")])],
                       "tab create": [ok("tab_created", tab={"tab_id": "w2:t1"}, root_pane={"pane_id": "w2:p1"})],
                       "agent start": [ok("agent_started", agent=cagent(name="y", session="C8"))],
                       "pane get": [ok("pane_info", pane={"pane_id": "w2:p1"})],
                       "pane process-info": [READY_SHELL,
                           ok("pane_process_info", process_info={"foreground_processes": [{"name": "node", "argv": ["node", "/x/claude"] + flags_final}]})]})
        store = hb.StateStore(tempfile.mkdtemp())
        store.save("y", launch_flags=stored_flags)
        rc, out, _, store = run(["open", "y", "--yolo"], h, store)
        self.assertEqual(rc, 0)
        start = [c for c in h.calls if c[:3] == ("cli", "agent", "start")][0]
        argv_sent = start[start.index("--") + 1:]
        self.assertIn("bypassPermissions", argv_sent)
        self.assertEqual(store.load("y")["launch_flags"], flags_final)

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

    def test_remediation_suffix_flags_read_only_when_only_mcp_config_missing(self):
        # The read-only bundle is 4 flag pairs (--allowedTools/--disallowedTools/
        # --strict-mcp-config/--mcp-config); a live session that dropped only one of them (e.g.
        # herdr's restore losing just --mcp-config) is still a broken read-only session and must
        # still be told to pass --read-only again, not silently omitted because --allowedTools
        # itself happened to survive.
        missing = {"--mcp-config": '{"mcpServers":{}}'}
        self.assertIn("--read-only", cli._remediation_suffix(missing))

    def test_ask_refuses_with_read_only_hint_when_only_mcp_config_flag_missing(self):
        argv = ["node", "/x/claude", "--resume", "C1", "--permission-mode", "manual",
                "--allowedTools", cli.READ_ONLY_ALLOWED, "--disallowedTools", cli.READ_ONLY_DENIED,
                "--strict-mcp-config"]  # --mcp-config (and its value) dropped by the restore
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[cagent()])],
                       "pane process-info": [ok("pane_process_info", process_info={"foreground_processes": [{"name": "node", "argv": argv}]})]})
        store = hb.StateStore(tempfile.mkdtemp()); store.save("cv", launch_flags=cli.build_launch_args(True, None), pane_id="w2:p1")
        rc, _, err, _ = run(["ask", "cv", "hello"], h, store)
        self.assertEqual(rc, 1); self.assertIn("--read-only", err); self.assertIn("close", err)

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

    def test_ask_missing_permission_mode_in_stored_flags_no_longer_refuses(self):
        # Before the fix, a live session whose stored flags predate the --permission-mode pin (or
        # never recorded any launch flags at all) was refused on every bare `ask`/`open` until the
        # user closed/reopened it, because build_launch_args always emitted a "manual" pin that got
        # compared against whatever (or nothing) was stored -- the same root cause as a granted
        # --yolo needing to be repeated on every call. Now permission_mode can be None (unspecified)
        # and an unspecified ask is dropped from that comparison entirely, so this must succeed.
        after = "> hi\n\n⏺ hi there\n\n╭──╮\n│ ❯ │\n╰──╯\n"
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[cagent()])],
                       "agent prompt": [ok("agent_prompt", agent=cagent())]},
                      {"agent read": ["", after]})
        store = hb.StateStore(tempfile.mkdtemp()); store.save("cv", launch_flags=[])
        rc, out, _, _ = run(["ask", "cv", "hi"], h, store)
        self.assertEqual((rc, out.strip()), (0, "hi there"))

    def test_ask_missing_permission_mode_with_stored_model_no_longer_refuses(self):
        after = "> hi\n\n⏺ hi there\n\n╭──╮\n│ ❯ │\n╰──╯\n"
        argv = ["node", "/x/claude", "--resume", "C1", "--model", "opus"]
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[cagent()])],
                       "agent prompt": [ok("agent_prompt", agent=cagent())],
                       "pane process-info": [ok("pane_process_info", process_info={"foreground_processes": [{"name": "node", "argv": argv}]})]},
                      {"agent read": ["", after]})
        store = hb.StateStore(tempfile.mkdtemp()); store.save("cv", launch_flags=["--model", "opus"])
        rc, out, _, _ = run(["ask", "cv", "hi"], h, store)
        self.assertEqual((rc, out.strip()), (0, "hi there"))

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

    def test_keys_sends_each_key_on_idle(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[cagent(status="idle")])],
                       "agent send-keys": [ok("agent_send_keys")]})
        rc, _, _, _ = run(["keys", "cv", "down", "enter"], h)
        self.assertEqual(rc, 0)
        self.assertEqual([c for c in h.calls if c[:3] == ("cli", "agent", "send-keys")][0][3:], ("cv", "down", "enter"))

    def test_keys_confirming_key_on_approval_refused_without_user_decided(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[cagent(status="blocked")])],
                       "agent explain": [{"matched_rule": {"id": "bash_permission_prompt"}}]})
        rc, _, err, _ = run(["keys", "cv", "enter"], h)
        self.assertEqual(rc, 1)
        self.assertIn("--user-decided", err)
        self.assertFalse([c for c in h.calls if c[:3] == ("cli", "agent", "send-keys")])

    def test_keys_confirming_key_case_insensitive_on_approval_refused(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[cagent(status="blocked")])],
                       "agent explain": [{"matched_rule": {"id": "bash_permission_prompt"}}]})
        rc, _, err, _ = run(["keys", "cv", "Y"], h)
        self.assertEqual(rc, 1)
        self.assertIn("--user-decided", err)

    def test_keys_esc_on_approval_allowed_without_user_decided(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[cagent(status="blocked")])],
                       "agent explain": [{"matched_rule": {"id": "bash_permission_prompt"}}],
                       "agent send-keys": [ok("agent_send_keys")]})
        rc, _, _, _ = run(["keys", "cv", "esc"], h)
        self.assertEqual(rc, 0)
        self.assertEqual([c for c in h.calls if c[:3] == ("cli", "agent", "send-keys")][0][3:], ("cv", "esc"))

    def test_keys_confirming_key_on_approval_allowed_with_user_decided(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[cagent(status="blocked")])],
                       "agent explain": [{"matched_rule": {"id": "bash_permission_prompt"}}],
                       "agent send-keys": [ok("agent_send_keys")]})
        rc, _, _, _ = run(["keys", "cv", "enter", "--user-decided"], h)
        self.assertEqual(rc, 0)
        self.assertEqual([c for c in h.calls if c[:3] == ("cli", "agent", "send-keys")][0][3:], ("cv", "enter"))

    def test_keys_confirming_key_on_idle_allowed_without_user_decided(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[cagent(status="idle")])],
                       "agent send-keys": [ok("agent_send_keys")]})
        rc, _, _, _ = run(["keys", "cv", "enter"], h)
        self.assertEqual(rc, 0)
        self.assertEqual([c for c in h.calls if c[:3] == ("cli", "agent", "send-keys")][0][3:], ("cv", "enter"))

    def test_answer_on_clarify_refused_without_user_decided(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[cagent(status="blocked")])],
                       "agent explain": [{"matched_rule": {"id": "live_blocked_form"}}]})
        rc, _, err, _ = run(["answer", "cv", "42"], h)
        self.assertEqual(rc, 1)
        self.assertIn("--user-decided", err)
        self.assertFalse([c for c in h.calls if c[:3] == ("cli", "pane", "send-text")])

    def test_answer_on_clarify_allowed_with_user_decided(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[cagent(status="blocked")]),
                                      ok("agent_list", agents=[cagent(status="blocked")]),
                                      ok("agent_list", agents=[cagent(status="idle")])],
                       "agent explain": [{"matched_rule": {"id": "live_blocked_form"}}],
                       "pane send-text": [ok("pane_send_text")],
                       "pane send-keys": [ok("pane_send_keys")]})
        rc, out, _, _ = run(["answer", "cv", "42", "--user-decided"], h)
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "idle")

    def test_answer_on_approval_refused_without_user_decided(self):
        # Important 5: `answer` must be gated the same way `keys` already is in every state where
        # a prompt might be open, not just `clarify` -- approval/secret/blocked too.
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[cagent(status="blocked")])],
                       "agent explain": [{"matched_rule": {"id": "bash_permission_prompt"}}]})
        rc, _, err, _ = run(["answer", "cv", "yes"], h)
        self.assertEqual(rc, 1)
        self.assertIn("--user-decided", err)
        self.assertFalse([c for c in h.calls if c[:3] == ("cli", "pane", "send-text")])

    def test_answer_on_idle_keeps_current_behavior_without_user_decided(self):
        # idle/busy are unaffected by the new gate: Bridge.answer's own "not clarify" refusal
        # still fires (unchanged), with no --user-decided needed to reach it.
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[cagent(status="idle")])]})
        rc, _, err, _ = run(["answer", "cv", "hi"], h)
        self.assertEqual(rc, 1)
        self.assertNotIn("--user-decided", err)
        self.assertIn("not clarify", err)

    def test_open_live_session_missing_read_only_and_model_lists_both(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[cagent()])]})
        store = hb.StateStore(tempfile.mkdtemp())
        store.save("cv", launch_flags=cli.build_launch_args(False, None))
        rc, _, err, _ = run(["open", "cv", "--read-only", "--model", "sonnet"], h, store)
        self.assertEqual(rc, 1)
        self.assertIn("--read-only", err)
        self.assertIn("--model sonnet", err)
        self.assertIn("close cv", err)
        self.assertNotIn("Traceback", err)

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
