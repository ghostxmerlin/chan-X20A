import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB, T0, session, speech   # noqa: E402
from mind import api, runner, settings                  # noqa: E402
from mind.records import Records                        # noqa: E402
from mind.store import Store                            # noqa: E402

MIN = 60_000


class Reply:
    def __init__(self, status, body):
        self.status, self.body = status, body

    def json(self):
        return json.loads(self.body)


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db = FakeDB()
        db.add("sessions", session("H", T0 - 3600_000, source="stackchan-dev"))
        db.add("speech", speech("H", "s-1", T0 + 1000))
        cls.store = Store(db)
        rec = Records(db)
        s = settings.load(None)
        runner.process_minute(rec, cls.store, T0, s, T0 + 10 * MIN)
        db.add("speech", speech("H", "s-2", T0 + 2000))
        runner.process_minute(rec, cls.store, T0, s, T0 + 10 * MIN)
        cls.store.set_state(cursor_minute=T0)
        cls.server = api.make_server(cls.store, "k", "127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def call(self, path, token="k", method="GET"):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return Reply(r.status, r.read())
        except urllib.error.HTTPError as e:
            with e:
                return Reply(e.code, e.read())

    def test_requires_token(self):
        self.assertEqual(self.call("/state", token=None).status, 401)
        self.assertEqual(self.call("/state", token="wrong").status, 401)

    def test_state(self):
        r = self.call("/state").json()
        self.assertEqual((r["lane"], r["cursor_minute"]), ("live", T0))
        self.assertIn("now_ms", r)

    def test_timeline(self):
        rows = self.call(f"/timeline?from={T0}&to={T0 + MIN}").json()
        self.assertEqual([(x["minute"], x["version"]) for x in rows], [(T0, 2)])

    def test_timeline_range_limit(self):
        self.assertEqual(self.call(f"/timeline?from=0&to={25 * 3600_000}").status, 400)
        self.assertEqual(self.call("/timeline?from=x&to=1").status, 400)

    def test_minute_versions(self):
        r = self.call(f"/minute?t={T0}").json()
        self.assertEqual((r["versions"], r["version"]), ([1, 2], 2))
        self.assertEqual(len(r["package"]["speech"]), 2)
        self.assertEqual(self.call(f"/minute?t={T0}&version=1").json()["version"], 1)

    def test_minute_missing(self):
        self.assertEqual(self.call(f"/minute?t={T0 + 600_000}").status, 404)

    def test_unknown_path(self):
        self.assertEqual(self.call("/nope").status, 404)

    def test_post_rejected(self):
        self.assertEqual(self.call("/state", method="POST").status, 405)


if __name__ == "__main__":
    unittest.main()
