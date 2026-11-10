import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mind_fixtures  # noqa: E402,F401
from mind.validate import apply  # noqa: E402

T = 1794300000000


def out(entries=(), owner_view="present", summary="在桌前"):
    a = {"text": "x", "confidence": "mid"}
    return {"who_am_i": a, "where_am_i": a, "people_doing": a, "owner_view": owner_view, "entries": list(entries),
            "changes": [], "summary": summary, "want_to_know": ""}


def entry(key, op="add", dimension="people", text="t", basis="inferred", confidence="mid", evidence=("rule:owner",)):
    return {"key": key, "dimension": dimension, "op": op, "text": text, "basis": basis, "confidence": confidence,
            "evidence": list(evidence)}


def prev(key, text="旧", confidence="mid", since=T - 600_000, last_evidence=T - 60_000, dimension="people"):
    return {"key": key, "dimension": dimension, "op": "add", "text": text, "basis": "inferred", "confidence": confidence,
            "evidence": ["rule:owner"], "since": since, "last_evidence": last_evidence}


PKG = {"codes": {"s1": {}}}
PKG_A = {"codes": {"s1": {}, "a1": {}}}
STRONG = {"owner": {"state": "likely_present"}}
SEEN = {"owner": {"state": "seen"}}


class ValidateTest(unittest.TestCase):
    def test_invalid_codes_dropped_and_seen_without_evidence_downgraded(self):
        scene, c = apply(out([entry("people.owner", basis="seen", evidence=["v9", "rule:owner"])]), [], PKG, STRONG, T)
        self.assertEqual((scene["entries"][0]["basis"], scene["entries"][0]["evidence"]), ("inferred", ["rule:owner"]))
        self.assertEqual((c["invalid_refs"], c["downgraded"]), (1, 1))

    def test_heard_with_current_code_stays(self):
        scene, c = apply(out([entry("topic.1", dimension="topic", basis="heard", evidence=["s1"])]), [], PKG, STRONG, T)
        self.assertEqual((scene["entries"][0]["basis"], c["downgraded"]), ("heard", 0))

    def test_keep_reuses_previous_text(self):
        scene, _ = apply(out([entry("activity.desk", op="keep", text="换了说法")]), [prev("activity.desk", text="在桌前打字")], PKG, STRONG, T)
        self.assertEqual(scene["entries"][0]["text"], "在桌前打字")

    def test_keep_of_unknown_key_is_an_add(self):
        scene, _ = apply(out([entry("activity.new", op="keep", text="新的")]), [], PKG, STRONG, T)
        self.assertEqual((scene["entries"][0]["text"], scene["entries"][0]["since"]), ("新的", T))

    def test_raise_without_new_evidence_is_blocked(self):
        scene, c = apply(out([entry("activity.desk", op="update", confidence="high", evidence=["rule:owner"])]),
                         [prev("activity.desk", confidence="low")], PKG, STRONG, T)
        self.assertEqual((scene["entries"][0]["confidence"], c["raise_blocked"]), ("low", 1))
        scene2, c2 = apply(out([entry("activity.desk", op="update", confidence="high", evidence=["s1"])]),
                           [prev("activity.desk", confidence="low")], PKG, STRONG, T)
        self.assertEqual((scene2["entries"][0]["confidence"], c2["raise_blocked"]), ("high", 0))

    def test_end_removes_and_omitted_is_kept(self):
        scene, c = apply(out([entry("topic.1", op="end")]), [prev("topic.1"), prev("people.owner")], PKG, STRONG, T)
        self.assertEqual([e["key"] for e in scene["entries"]], ["people.owner"])
        self.assertEqual((c["ended"], c["omitted"]), (1, 1))

    def test_rule_conflict(self):
        self.assertTrue(apply(out(owner_view="absent"), [], PKG, SEEN, T)[1]["rule_conflict"])
        self.assertFalse(apply(out(owner_view="unknown"), [], PKG, SEEN, T)[1]["rule_conflict"])
        self.assertFalse(apply(out(owner_view="present"), [], PKG, SEEN, T)[1]["rule_conflict"])

    def test_language_only_ratio(self):
        _, c = apply(out([entry("activity.a", dimension="activity", evidence=["s1"]),
                          entry("activity.b", dimension="activity", evidence=["s1", "a1"])]), [], PKG_A, STRONG, T)
        self.assertEqual(c["language_only"], 0.5)
        _, c2 = apply(out([entry("activity.a", dimension="activity", evidence=["s1"])]), [], PKG, STRONG, T)
        self.assertEqual(c2["language_only"], 0.0)          # no non-speech evidence existed this minute

    def test_since_and_last_evidence(self):
        scene, _ = apply(out([entry("people.owner", op="update", evidence=["rule:owner"])]),
                         [prev("people.owner", since=T - 600_000, last_evidence=T - 300_000)], PKG, STRONG, T)
        self.assertEqual((scene["entries"][0]["since"], scene["entries"][0]["last_evidence"]), (T - 600_000, T - 300_000))
        scene2, _ = apply(out([entry("people.owner", op="update", evidence=["s1"])]), [prev("people.owner")], PKG, STRONG, T)
        self.assertEqual(scene2["entries"][0]["last_evidence"], T)

    def test_enums_enforced_whatever_the_model_sends(self):
        bad = entry("people.x", dimension="<img onerror=x>", basis="<b>", confidence="<i>")
        scene, _ = apply(dict(out([bad]), owner_view="<svg>", who_am_i={"text": "a", "confidence": "<x>"}), [], PKG, STRONG, T)
        e = scene["entries"][0]
        self.assertEqual((e["dimension"], e["basis"], e["confidence"]), ("other", "assumed", "low"))
        self.assertEqual((scene["owner_view"], scene["who_am_i"]["confidence"]), ("unknown", "low"))

    def test_lengths_capped(self):
        scene, _ = apply(out([entry(f"k{i}", text="x" * 500) for i in range(25)], summary="一" * 40), [], PKG, STRONG, T)
        self.assertEqual((len(scene["entries"]), len(scene["entries"][0]["text"]), len(scene["summary"])), (20, 200, 20))


if __name__ == "__main__":
    unittest.main()
