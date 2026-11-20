"""hello's persona file (config/persona, owner 2026-11-18: 鹿野): the active file exists, 围观 (ear realtime_link) and
chat (mind) both put it first, and without it each falls back to what it had before.

    .private/venv-ear/bin/python -B -m unittest tests.test_persona -v
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server" / "ear"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import realtime_link as rl                      # noqa: E402
from mind_fixtures import FakeDB  # noqa: E402,F401  (puts server/ on the path for mind)
from mind import chat as chat_mod  # noqa: E402

PERSONA = ROOT / "config" / "persona"


class PersonaFileTest(unittest.TestCase):
    def test_the_active_persona_is_luye(self):
        active = (PERSONA / "active").read_text().strip()
        text = (PERSONA / active).read_text(encoding="utf-8")
        self.assertEqual(active, "luye.md")
        self.assertTrue(text.startswith("你是鹿野"))
        self.assertIn("追毫", text)
        self.assertLess(len(text), 4000)          # both readers cut at 4000

    def test_crowd_puts_the_persona_first_and_keeps_its_rules(self):
        p = rl.build_persona("你是鹿野。")
        self.assertTrue(p.startswith("【人设】\n你是鹿野。"))
        self.assertIn("Providence", p)                      # owner 2026-11-20: renamed from Dynames
        self.assertNotIn("Dynames", p)
        self.assertNotIn("傲娇又霸气", p)
        self.assertIn("只回一个字「嗯」", p)
        self.assertIn("现在的地点", p)
        self.assertNotIn("办公桌", p)

    def test_crowd_without_a_file_keeps_the_old_identity(self):
        self.assertEqual(rl.load_persona("/nonexistent/persona.md"), "")
        self.assertTrue(rl.build_persona("").startswith("你是 hello"))

    def test_chat_reads_the_file_and_drops_the_role_character(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("你是鹿野。\n")
        c = chat_mod.Chat.__new__(chat_mod.Chat)
        c.roles_fn = lambda: {chat_mod.ROLE: {"id": chat_mod.ROLE, "persona": "一本正经", "voice": "wuxian"}}
        c.persona_path = f.name
        role = c._load_persona(0)
        self.assertEqual(role["persona_text"], "你是鹿野。")
        ctx = {"persona": role, "self_facts": "这台机器人叫 Dynames。"}
        system = chat_mod.messages(ctx, {"text": "你好"}, [], {"max_chars": 40}, None)[0]["content"]
        self.assertTrue(system.startswith("【人设】\n你是鹿野。"))
        self.assertIn("这台机器人叫 Dynames。", system)
        self.assertNotIn("一本正经", system)
        self.assertIn("照【人设】的性格和语气说话", system)
        c.persona_path = "/nonexistent/persona.md"
        self.assertNotIn("persona_text", c._load_persona(0))


if __name__ == "__main__":
    unittest.main()
