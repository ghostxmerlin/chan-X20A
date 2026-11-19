"""Tool contract (2026-11-19 refactor 4.1, docs/plans/2026-11-19-server-layers.md): config/tools/registry.json is the one
list of tools; every live tool has an implementation where it runs, and every implementation is registered (or listed
below as internal). Before this test the three lists (registry, mind's EAR_TOOLS, the ear's ask NAMES) drifted
silently."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "server" / "ear"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

REG = json.loads((ROOT / "config" / "tools" / "registry.json").read_text(encoding="utf-8"))
# implemented but not a model tool: mind / tests call them (debug_alert: mind's debug worker; test_turn: the long-run
# check; face_learn_propose: reached through mind's proposal flow)
INTERNAL_ASKS = {"debug_alert", "test_turn", "face_learn_propose"}
# a live tool whose implementation is mind's own wrapper around an ear ask (api.py set_voice_kept)
MIND_WRAPS_EAR = {"set_voice"}


def mind_impl_names():
    from mind import tool_impls, daily, research, docs_qa, debug_mode
    from mind_fixtures import FakeDB
    from mind.store import Store
    db = FakeDB()
    names = set(tool_impls.mind_tools(switches=lambda: {}, store=Store(db), clock_ms=lambda: 0))
    names |= set(tool_impls.live_tools())
    names |= set(daily.tools(None)) if callable(getattr(daily, "tools", None)) else set()
    names |= set(research.tools(None))
    names |= set(docs_qa.tools(db)) | set(debug_mode.tools(db))
    return names


class ToolContractTest(unittest.TestCase):
    def setUp(self):
        self.live = {t["name"]: t for t in REG["tools"] if t["status"] == "live"}

    def test_ear_tools_line_up(self):
        from mind import tool_impls
        try:
            import ask_api
        except ImportError as e:
            self.skipTest(f"ear modules: {e}")
        ear_side = {n for n, t in self.live.items() if t.get("runs_on") in ("ear", "robot")} - MIND_WRAPS_EAR
        self.assertEqual(sorted(ear_side - set(tool_impls.EAR_TOOLS)), [], "live ear / robot tools mind does not forward")
        self.assertEqual(sorted(set(tool_impls.EAR_TOOLS) - set(ask_api.Asks.NAMES)), [], "forwarded tools the ear does not answer")
        self.assertEqual(sorted(set(ask_api.Asks.NAMES) - set(self.live) - INTERNAL_ASKS), [], "ear asks nobody registered")
        for n in ask_api.Asks.NAMES:
            self.assertTrue(callable(getattr(ask_api.Asks, n, None)), f"ask {n} has no handler")

    def test_mind_tools_have_an_implementation(self):
        try:
            names = mind_impl_names()
        except Exception as e:                         # noqa: BLE001 - an impl table that does not build is a failure
            self.fail(f"mind tool tables: {type(e).__name__}: {e}")
        mind_side = {n for n, t in self.live.items() if t.get("runs_on") in ("mind", "external")} | MIND_WRAPS_EAR
        from mind import tool_impls
        missing = sorted(n for n in mind_side - names - set(tool_impls.EAR_TOOLS) - MIND_WRAPS_EAR)
        self.assertEqual(missing, [], "live mind tools without an implementation")
        unregistered = sorted(names - set(n for n in (t["name"] for t in REG["tools"])))
        self.assertEqual(unregistered, [], "mind implementations nobody registered")


if __name__ == "__main__":
    unittest.main()
