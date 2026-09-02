# tests/test_vendored_lib.py
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "claude-bridge", "scripts"))
import herdrbridge as hb
from fakes import FakeHerdr, agent, ok


class VendoredLibTests(unittest.TestCase):
    def test_library_surface(self):
        for attr in ("Herdr", "StateStore", "BridgeConfig", "Bridge", "classify", "state_exit",
                     "extract_reply", "plan_menu_step", "validate_name", "session_name",
                     "BridgeError", "UsageError", "ServerUnavailable", "HerdrError"):
            self.assertTrue(hasattr(hb, attr), attr)

    def test_claude_rules_classify(self):
        self.assertEqual(hb.classify("blocked", "bash_permission_prompt"), "approval")
        self.assertEqual(hb.classify("blocked", "live_blocked_form"), "clarify")

    def test_fake_works(self):
        h = FakeHerdr({"agent list": [ok("agent_list", agents=[agent("x", kind="claude")])]})
        self.assertEqual(h.cli("agent", "list")["result"]["agents"][0]["agent"], "claude")

    def test_version_stamp_present(self):
        p = os.path.join(os.path.dirname(__file__), "..", "claude-bridge", "scripts", "herdrbridge.version")
        self.assertTrue(os.path.exists(p))


if __name__ == "__main__":
    unittest.main()
