"""Conversation notes (spec docs/superpowers/specs/2026-11-11-conversation-notes-design.md).

People's sentences (SenseVoice and 听悟, hello's own clips removed) are cut into conversations by rule:
a gap of more than 60 s ends one. luna writes a ~10-character title and ~50-character notes for the
running conversation, and on its first update says whether it continues one from earlier today.
"""
from . import package, selfvoice

GAP_MS = 60_000                  # no one spoke for a minute: the conversation is over (user 2026-11-11)
CONTINUE_WINDOW_MS = 10 * 60_000  # the previous conversation is always a candidate within 10 minutes
CANDIDATES = 4
UNKNOWN = {"kind": "unknown", "name": None, "best": None, "score": None}


def _sounding(d):
    return bool((d.get("self") or {}).get("sounding"))


def new_sentences(db, records, since_ms, until_ms, seen):
    """People's sentences with wall time in [since_ms, until_ms), oldest first, minus `seen` refs."""
    w = records.window(since_ms, until_ms)
    out = []
    for d in w.get("speech", []):
        ref = ("speech", d.get("session"), d.get("id"))
        if ref in seen or not (d.get("text") or "").strip() or selfvoice.is_hello(d.get("text"), _sounding(d)):
            continue
        out.append({"ref": ref, "at": d["_wall"], "text": d["text"], "who": package._who(d)})
    for d in w.get("tingwu", []):
        if d.get("kind", "sentence") != "sentence":
            continue
        ref = ("tingwu", d.get("session"), d.get("n"))
        if ref in seen or not (d.get("text") or "").strip() or selfvoice.is_hello(d.get("text"), _sounding(d)):
            continue
        out.append({"ref": ref, "at": d["_wall"], "text": d["text"], "who": dict(UNKNOWN), "engine": "tingwu"})
    return sorted(out, key=lambda x: x["at"])


def segment(open_conv, sentences, now_ms):
    """{"close": the open conversation is over, "append": its new sentences, "new": [[sentences], ...]}.
    Every group in "new" but the last is already over (a gap followed it)."""
    last = open_conv["last_ms"] if open_conv else None
    close, append, new = False, [], []
    for s in sentences:
        if last is not None and s["at"] - last <= GAP_MS:
            (new[-1] if new else append).append(s)
        else:
            if open_conv and not new and not close:
                close = True             # the open conversation ended before this sentence
            new.append([s])
        last = s["at"]
    if open_conv and not new and not append and now_ms - open_conv["last_ms"] > GAP_MS:
        close = True                     # silence for a minute: over
    return {"close": close, "append": append, "new": new}
