"""Shared builder for mind tests: a real minute package and strong elements from fixture records."""
from mind_fixtures import FakeDB, T0, session, sound, speech, tingwu, vision   # noqa: F401
from mind import package, settings, strong
from mind.records import Records

MIN = 60_000
S = settings.load(None)


def build(docs, sessions=None, minute=T0, s=S, now=None):
    db = FakeDB()
    db.add("sessions", *(sessions or [session("S", T0 - 3600_000, source="stackchan-dev"),
                                      session("V", T0 - 3600_000, source="stackchan-dev", kind="vision")]))
    for coll, d in docs:
        db.add(coll, d)
    rec = Records(db)
    window = rec.window(minute, minute + MIN)
    lookback = rec.window(minute - 30 * MIN, minute + MIN)
    pkg = package.build(window, minute, s, lookback)
    return pkg, strong.evaluate(pkg, lookback, minute, s, now if now is not None else minute + 10 * MIN)
