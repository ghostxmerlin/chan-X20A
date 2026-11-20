"""hello's bodies (owner 2026-11-20: one robot, several bodies): the kinds and their abilities / tuning
(server/common/bodies.py), the ear's registry rows (hello_db), the vision link's live counts, mind's GET /bodies,
and the deploys shipping bodies.py. No network, no models."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from mind_fixtures import FakeDB as MindDB      # noqa: E402  (puts server/ and server/common on sys.path)
sys.path.insert(0, str(ROOT / "server" / "ear"))
import bodies                                    # noqa: E402
import hello_db                                  # noqa: E402
import vision_link                               # noqa: E402
from mind import body_roster, strong             # noqa: E402

ROBOT, MAC = "stackchan-7c4fadaf90b4", "stackchan-mac-a1b2c3"


class KindsTest(unittest.TestCase):
    def test_the_source_tells_the_kind(self):
        self.assertEqual(bodies.kind_of(ROBOT), "stackchan")
        self.assertEqual(bodies.kind_of(MAC), "mac")
        self.assertEqual(bodies.kind_of("stackchan-protocol-smoke"), "stackchan")   # the smoke client, as before
        for other in ("mac-mic", "", None, "web"):
            self.assertIsNone(bodies.kind_of(other))
            self.assertIsNone(bodies.body(other))

    def test_the_robot_keeps_every_robot_mic_rule_and_the_mac_keeps_the_mac_mic_gate(self):
        r, m = bodies.body(ROBOT), bodies.body(MAC)
        self.assertEqual(r["tuning"], {"quiet_labels": True, "unclear_is_voice": True, "denoise": True})
        self.assertEqual(m["tuning"], {"quiet_labels": False, "unclear_is_voice": False, "denoise": True})
        for k in bodies.TUNING_NONE:                     # no body: as the ear treated the page's mic
            self.assertFalse(bodies.tuning(None, k))
        self.assertEqual((r["id"], m["id"], r["name"], m["name"]), (ROBOT, MAC, "机身", "Mac"))

    def test_abilities_differ(self):
        r, m = bodies.body(ROBOT), bodies.body(MAC)
        self.assertTrue(bodies.can(r, "motion", "servo") and bodies.can(m, "motion", "eyes"))
        self.assertTrue(bodies.can(r, "body_sense") and not bodies.can(m, "body_sense"))
        self.assertNotEqual(r["abilities"]["faces"], m["abilities"]["faces"])       # prints never compare
        self.assertFalse(bodies.can(None, "hearing"))
        self.assertEqual({k["kind"] for k in bodies.catalog()}, {"stackchan", "mac"})

    def test_a_body_may_declare_its_abilities_but_only_known_ones_of_the_right_type(self):
        b = bodies.body(MAC, {"abilities": {"voice_module": None, "camera": False, "body_sense": "yes",
                                            "screen": True, "wings": True, "clips": "x" * 99}})
        self.assertTrue(b["declared"])
        self.assertIsNone(b["abilities"]["voice_module"])       # this Mac cannot recognise English on device
        self.assertFalse(b["abilities"]["camera"])
        self.assertFalse(b["abilities"]["body_sense"])          # a string is no bool
        self.assertEqual(b["abilities"]["screen"], "window")    # a bool is no string
        self.assertNotIn("wings", b["abilities"])
        self.assertEqual(len(b["abilities"]["clips"]), 32)
        self.assertEqual(b["tuning"], bodies.KINDS["mac"]["tuning"])   # tuning is the server's, never declared

    def test_a_declared_kind_wins_and_a_bad_one_falls_back(self):
        self.assertEqual(bodies.body("hello-mac-1", {"kind": "mac"})["kind"], "mac")
        self.assertEqual(bodies.body(ROBOT, {"kind": ["mac"]})["kind"], "stackchan")
        self.assertEqual(bodies.body(ROBOT, {"kind": "toaster"})["kind"], "stackchan")
        self.assertIsNone(bodies.body("web", {"kind": "toaster"}))
        self.assertEqual(bodies.body(ROBOT, "junk")["declared"], False)


class Coll:
    def __init__(self, ops, name): self.ops, self.name = ops, name
    def update_one(self, key, update, upsert=False): self.ops.append(("update_one", self.name, key, update, upsert))
    def update_many(self, key, update): self.ops.append(("update_many", self.name, key, update))
    def replace_one(self, key, doc, upsert=False): self.ops.append(("replace_one", self.name, key, doc))


class Fake(dict):
    def __init__(self): super().__init__(); self.ops = []
    def __getitem__(self, name): return Coll(self.ops, name)


class RegistryTest(unittest.TestCase):
    def test_body_seen_sets_the_row_and_first_seen_only_once(self):
        fake = Fake()
        db = hello_db.HelloDB("unused", client_factory=lambda: fake)
        db.body_seen(bodies.body(MAC), "hearing", 1, {"device_id": "a1b2c3", "boot_id": "b9", "firmware": "mac-0.1",
                                                      "extra": "dropped"})
        db.body_seen(None, "hearing", 0)                         # no body: nothing
        db.bodies_reset()
        db.flush()
        (op, coll, key, upd, upsert), (op2, coll2, key2, upd2) = fake.ops
        self.assertEqual((op, coll, key, upsert), ("update_one", "bodies", {"_id": MAC}, True))
        self.assertEqual(upd["$set"]["kind"], "mac")
        self.assertEqual(upd["$set"]["links.hearing"], 1)
        self.assertEqual(upd["$set"]["device"], {"device_id": "a1b2c3", "boot_id": "b9", "firmware": "mac-0.1"})
        self.assertEqual(set(upd["$setOnInsert"]), {"first_seen_ms"})
        self.assertEqual((op2, coll2, key2, upd2), ("update_many", "bodies", {}, {"$set": {"links.hearing": 0,
                                                                                            "links.vision": 0}}))


class VisionDB:
    def __init__(self): self.seen = []
    def session_open(self, *a): pass
    def session_update(self, *a): pass
    def body_seen(self, b, part, links, device=None): self.seen.append((b["id"], part, links))


class VisionRegistryTest(unittest.TestCase):
    def setUp(self):
        vision_link._live.clear()

    def test_overlapping_links_keep_the_body_online_until_the_last_closes(self):
        db = VisionDB()
        a, b = vision_link.VisionLink(db, 1, "p"), vision_link.VisionLink(db, 2, "p")
        hello = '{"type":"hello","source":"%s","device":{"boot_id":"b1"}}' % ROBOT
        a.on_text(hello)
        b.on_text(hello)                                         # the reconnect opens before the old link ends
        a.close()
        b.close()
        b.close()                                                # a second close changes nothing
        self.assertEqual(db.seen, [(ROBOT, "vision", 1), (ROBOT, "vision", 2), (ROBOT, "vision", 1),
                                   (ROBOT, "vision", 0)])

    def test_no_body_no_registry(self):
        db = VisionDB()
        link = vision_link.VisionLink(db, 1, "p")
        link.on_text('{"type":"hello","source":"webcam"}')
        link.close()
        self.assertEqual(db.seen, [])


class RosterTest(unittest.TestCase):
    def test_kinds_with_the_bodies_seen_and_who_is_online(self):
        db = MindDB()
        for doc in ([
            {"_id": ROBOT, "kind": "stackchan", "name": "机身", "abilities": {}, "last_seen_ms": 5,
             "links": {"hearing": 1, "vision": 0}},
            {"_id": MAC, "kind": "mac", "name": "Mac", "abilities": {}, "last_seen_ms": 9, "links": {"hearing": 0}},
            {"_id": "stackchan-old", "kind": "toaster", "last_seen_ms": 1}]):
            db["bodies"].insert_one(doc)
        r = body_roster.roster(db)
        kinds = {k["kind"]: k for k in r["kinds"]}
        self.assertEqual(set(kinds), {"stackchan", "mac"})
        self.assertTrue(kinds["stackchan"]["online"])
        self.assertFalse(kinds["mac"]["online"])
        self.assertEqual(kinds["mac"]["bodies"][0]["links"], {"hearing": 0, "vision": 0})
        self.assertEqual(r["online"], 1)
        self.assertEqual([u["id"] for u in r["unknown"]], ["stackchan-old"])
        self.assertIn("motion", kinds["mac"]["abilities"])

    def test_an_empty_registry_still_lists_every_kind(self):
        r = body_roster.roster(MindDB())
        self.assertEqual([k["bodies"] for k in r["kinds"]], [[], []])


class HearingCheckTest(unittest.TestCase):
    def test_hearing_sessions_of_both_bodies_count_and_vision_or_others_do_not(self):
        self.assertTrue(strong._is_hearing({"source": ROBOT, "session": "20261120/090000-x-c1"}))
        self.assertTrue(strong._is_hearing({"source": MAC, "session": "20261120/090000-x-c2"}))
        self.assertFalse(strong._is_hearing({"source": ROBOT, "kind": "vision", "session": "s"}))
        self.assertFalse(strong._is_hearing({"source": ROBOT, "session": "20261120/090000-x-v3"}))
        self.assertFalse(strong._is_hearing({"source": "mac-mic", "session": "s"}))


class DeployTest(unittest.TestCase):
    def test_every_deploy_ships_bodies_py(self):
        for f in ("server/ear/deploy.sh", "server/mind/deploy.sh", "tools/mac/deploy_stack.sh"):
            text = (ROOT / f).read_text(encoding="utf-8")
            self.assertTrue(re.search(r"bodies\.py", text), f)

    def test_no_server_code_branches_on_the_stackchan_name_any_more(self):
        hits = []
        for p in (ROOT / "server").rglob("*.py"):
            if p.name == "bodies.py" or p.name == "smoke_client.py":
                continue
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"""startswith\(\s*["']stackchan""", line):
                    hits.append(f"{p.relative_to(ROOT)}:{n}")
        self.assertEqual(hits, [], "use bodies.body() / bodies.kind_of()")


if __name__ == "__main__":
    unittest.main()
