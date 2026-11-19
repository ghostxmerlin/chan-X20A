"""Snapshots for the 2026-11-19 layered refactor (docs/plans/2026-11-19-server-layers.md 4.0): the outputs of the ear's
pure functions and of the tool lists, taken before the refactor, must stay byte for byte the same after every step.
Behaviour changes are not allowed in the refactor; a snapshot that has to change means the step changed behaviour.

    python3 -B tests/test_refactor_snapshots.py --write      # (once, before the refactor) writes tests/snapshots/
    python3 -B -m unittest tests.test_refactor_snapshots
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server" / "ear"))
sys.path.insert(0, str(ROOT / "server"))
SNAP = Path(__file__).resolve().parent / "snapshots"

SENTENCES = ["你好呀", "hello，今天天气怎么样？", "Dynames 过来", "别说话了。", "没叫你安静点儿。", "你先安静三十分钟",
             "先安静，直到我说退出", "我们在聊天", "当个大姐姐，陪陪老人", "进入调试模式", "退出调试模式", "帮我调试一个小时",
             "帮我查一下比特币", "你看看我手里拿的什么", "注意看", "上次说的那个板子", "OK.", "嗯嗯。", "可以说话了",
             "ICN7332 录入几次", "转过身去", "Hello. I'm here. 查一下比特币", "做回你自己", "直到我叫你再说话", "结束吧"]
REPLIES = [("嗯。", True), ("嗯", False), ("好的，我来查一下。", True), ("[事件] 主人出现了", True), ("【背景】xx", True),
           ("今天北京晴，最高六度。", True), ("嗯，我在呢", True), ("", True), ("哈哈哈", True), ("你说啥？", True),
           ("查完了，比特币六万四。", True), ("我不知道。", True), ("(笑) 好啊", True), ("嗯……", True), ("在呢在呢", False),
           ("我查查哈", True), ("好的", True), ("这个我得想想", True), ("…", True), ("你好你好你好", True)]


def ear_outputs():
    import realtime_link as rl
    import mode_span
    import intent
    out = {}
    out["text"] = {s: {"norm": rl.norm_text(s), "hush": rl.is_hush(s), "addressed": rl.addressed(s),
                       "call_rest": rl.call_rest(s), "order": rl.order_word(s), "question": rl.is_question(s),
                       "look": rl.is_look_ask(s), "search": rl.is_search_ask(s), "ask_kind": rl.ask_kind(s),
                       "span": (lambda v: "inf" if v == float("inf") else v)(mode_span.parse(s)),
                       "ended": mode_span.ended(s), "ended_loose": mode_span.ended(s, loose=True),
                       "intent_rule": intent.rule(s), "debug_re": bool(rl.DEBUG_RE.search(s)),
                       "quiet_re": bool(rl.QUIET_RE.search(rl.norm_text(s))), "low_re": bool(rl.LOW_RE.search(rl.norm_text(s)))}
                   for s in SENTENCES}
    out["verdict"] = [[t, d, rl.reply_verdict(t, d)] for t, d in REPLIES]
    cases = {
        "empty": {},
        "context": {"context_text": "此刻的情景：大家在聊午饭\n看到的人（M12 认出）：主人\n画面：桌上有电脑", "seen": "眼前有 1 个人：主人（阿大）。"},
        "recent": {"recent": [("你好", "你好呀！"), ("今天几号", "十九号"), ("再说一遍", "你好呀！")]},
        "manner": {"manner": "主人让你先安静（直到主人说退出）：除非有人叫你的名字或 hello，否则什么都不说；被叫到时只回答那一句，之后接着安静。",
                   "length": "简短", "role": "当个老师"},
        "eyes": {"looks": ["注视（12:00:00，3 秒前）：手里拿着一块板子"], "scene": "2 秒前：桌上有电脑", "speaker": "主人（阿大）",
                 "body": "身体感觉：被拿起来了（3 秒前）"},
        "skills": {"skills": "## 资料问答\n先查再答", "persona": "你是测试人设。"},
    }
    out["instructions"] = {k: rl.instructions(**v) for k, v in cases.items()}
    out["session_config"] = [rl.session_config("v1", "instr"), rl.session_config("v1", "i", search=True),
                             rl.session_config("v1", "i", turn="smart_turn_v2", tools=[{"type": "function", "name": "x"}])]
    out["persona_rules"] = rl.PERSONA_RULES
    out["seen"] = [rl.seen_text([], {}, [], "阿大"),
                   rl.seen_text([{"name": "boss"}, {"name": None, "gender": "F", "age_group": "adult"}], {}, ["boss"], "阿大")]
    out["greet"] = [rl.greet_text("阿大"), rl.greet_text("阿大", "short", word="嗨")]
    out["constants"] = {k: getattr(rl, k) for k in ("CALLED_S", "TOUCH_S", "SHAKEN_SAY_S", "QUIET_MAX_S", "LOW_S", "ROLE_S",
                                                     "TOOL_TIMEOUT_S", "SKILL_DEPTH", "TOOL_DEPTH", "LENGTH_S") if hasattr(rl, k)}
    out["tool_words"] = {"TOOL_WORD": rl.TOOL_WORD, "TOOL_WORD_GROUP": rl.TOOL_WORD_GROUP, "FILLER_GROUP": rl.FILLER_GROUP,
                         "SLOW": rl.SLOW_TOOL_TIMEOUT_S, "SKIP": sorted(rl.TOOL_SKIP), "SKILL_SKIP": sorted(rl.SKILL_TOOL_SKIP)}
    import ask_api
    out["ask_names"] = list(ask_api.Asks.NAMES)
    return out


def mind_outputs():
    from mind import tool_impls
    from mind.tools import load_registry
    reg = load_registry()
    return {"ear_tools": list(tool_impls.EAR_TOOLS),
            "registry": [[t["name"], t["status"], t["level"], t.get("runs_on"), sorted((t.get("parameters") or {}).get("properties", {}))]
                         for t in reg["tools"]]}


def jsonable(x):
    return json.loads(json.dumps(x, ensure_ascii=False, default=str))


class SnapshotTest(unittest.TestCase):
    def check(self, name, got):
        path = SNAP / f"{name}.json"
        if not path.exists():
            self.skipTest(f"no snapshot {name} (write it before the refactor)")
        want = json.loads(path.read_text(encoding="utf-8"))
        got = jsonable(got)
        for k in want:
            self.assertEqual(got.get(k), want[k], f"{name}.{k} changed")
        self.assertEqual(sorted(got), sorted(want), f"{name}: keys changed")

    def test_ear(self):
        try:
            out = ear_outputs()
        except ImportError as e:                       # a Python without the ear's deps
            self.skipTest(f"ear modules: {e}")
        self.check("ear", out)

    def test_mind(self):
        self.check("mind", mind_outputs())


if __name__ == "__main__":
    if "--write" in sys.argv:
        SNAP.mkdir(exist_ok=True)
        for name, fn in (("ear", ear_outputs), ("mind", mind_outputs)):
            (SNAP / f"{name}.json").write_text(json.dumps(jsonable(fn()), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            print(f"wrote {name}")
    else:
        unittest.main()
