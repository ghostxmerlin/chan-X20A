"""S1: one minute of raw records → a minute package (no model involved).

Every item gets a short code (s1, t1, a1, v1 …) unique within the minute; `codes` maps each code
back to its raw record. Rules are in docs/superpowers/plans/2026-11-10-mind-phase0.md, Task 2.
"""
import hashlib
import json
from collections import Counter

from . import selfvoice

BUILDER_VERSION = 4            # 2: pos; 3: the eye result as e1; 4: hello's own voice by clip text (selfvoice)
MINUTE_MS = 60_000
STATUS_EVENTS = ("vision_on", "vision_off", "m12_online", "m12_offline")
CLOSERS = {"m12_offline": "m12_offline", "vision_off": "vision_off"}


def _dedupe(docs, key):
    seen, out = set(), []
    for d in docs:
        k = key(d)
        if k not in seen:
            seen.add(k)
            out.append(d)
    return out


def _sounding(d):
    return bool((d.get("self") or {}).get("sounding"))


def _who(d):
    sp = d.get("speaker") or {}
    band = sp.get("band")
    if selfvoice.is_hello(d.get("text"), _sounding(d)):
        kind = "self"
    elif band == "known":
        kind = "known"
    elif band == "unsure":
        kind = "unsure"
    else:
        kind = "unknown"
    return {"kind": kind, "name": sp.get("name") if kind == "known" else None, "best": sp.get("best"),
            "score": sp.get("score")}


def _overlap(a, b):
    if not (isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)) and len(a) == 2 and len(b) == 2):
        return 0
    return max(0, min(a[1], b[1]) - max(a[0], b[0]))


def _session_ends(sessions):
    """session → (end ms, how): end_ms, else the start of the next session of the same source and kind."""
    ends = {}
    for s in sessions:
        if s.get("end_ms") is not None:
            ends[s["session"]] = (s["end_ms"], "session_end")
            continue
        later = [o["start_ms"] for o in sessions
                 if o is not s and o.get("source") == s.get("source") and o.get("kind") == s.get("kind")
                 and o.get("start_ms", 0) > s.get("start_ms", 0)]
        if later:
            ends[s["session"]] = (min(later), "session_end")
    return ends


def _intervals(lookback, end_ms, id_field, prefix):
    """Intervals of face (or person) ids from change events, closed by lost / M12 offline / vision off /
    session end; still open (to = end_ms) when nothing closed them."""
    events = sorted(_dedupe(lookback.get("vision", []),
                            lambda d: (d.get("session"), d.get("t_us"), d.get("event"), d.get("face"), d.get("person"))),
                    key=lambda d: d["_wall"])
    ends = _session_ends(lookback.get("sessions", []))
    lost_event = prefix + "_lost"
    out, live = [], {}
    for e in events:
        ev = e.get("event", "")
        if ev in CLOSERS:                        # vision off / M12 offline ends everything open in that session now,
            for key in [k for k in live if k[0] == e.get("session")]:     # so a later found opens a new interval
                cur = live.pop(key)
                cur["to"], cur["closed_by"] = e["_wall"], CLOSERS[ev]
                out.append(cur)
            continue
        if not ev.startswith(prefix + "_") or e.get(id_field) is None:
            continue
        key = (e.get("session"), e[id_field])
        cur = live.get(key)
        if cur is None:
            cur = {"session": key[0], id_field: key[1], "from": e["_wall"], "events": []}
            live[key] = cur
        cur["events"].append(e)
        if ev == lost_event:
            cur["to"], cur["closed_by"] = e["_wall"], "lost"
            out.append(live.pop(key))
    for cur in live.values():
        options = []
        if cur["session"] in ends and ends[cur["session"]][0] >= cur["from"]:
            options.append(ends[cur["session"]])
        if options:
            cur["to"], cur["closed_by"] = min(options)
        else:
            cur["to"], cur["closed_by"] = end_ms, None
        out.append(cur)
    for cur in out:
        cur["open"] = cur["closed_by"] is None
    return sorted(out, key=lambda c: (c["from"], c[id_field]))


def face_intervals(lookback, end_ms):
    """All face intervals in the lookback, each with name / gender / age_group (shared with strong.py)."""
    faces = []
    for c in _intervals(lookback, end_ms, "face", "face"):
        names = [e.get("name") for e in c["events"] if e.get("name")]
        genders = Counter(e.get("gender") for e in c["events"] if e.get("gender"))
        ages = Counter(e.get("age_group") for e in c["events"] if e.get("age_group"))
        faces.append({"face": c["face"], "session": c["session"], "name": names[-1] if names else None,
                      "from": c["from"], "to": c["to"], "open": c["open"], "closed_by": c["closed_by"],
                      "gender": genders.most_common(1)[0][0] if genders else None,
                      "age_group": ages.most_common(1)[0][0] if ages else None})
    return faces


def _union_ms(spans):
    total, cur_s, cur_e = 0, None, None
    for s, e in sorted(spans):
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        total += cur_e - cur_s
    return total


def build(window, minute_ms, s, lookback):
    end = minute_ms + MINUTE_MS
    drift_ms = s["thresholds"]["drift_warn_s"] * 1000
    codes, warnings = {}, []

    def code(prefix, n, ref, doc):
        c = f"{prefix}{n}"
        codes[c] = ref
        if doc.get("_drift") is not None and abs(doc["_drift"]) > drift_ms:
            warnings.append({"kind": "drift", "code": c, "seconds": round(abs(doc["_drift"]) / 1000, 1)})
        return c

    speech_docs = _dedupe(sorted(window.get("speech", []), key=lambda d: d["_wall"]),
                          lambda d: (d.get("session"), d.get("id")))
    tingwu_docs = _dedupe(sorted([d for d in window.get("tingwu", []) if d.get("kind", "sentence") == "sentence"
                                  and not selfvoice.is_hello(d.get("text"), _sounding(d))],
                                 key=lambda d: d["_wall"]), lambda d: (d.get("session"), d.get("kind"), d.get("n")))
    attach = {}
    for t in tingwu_docs:
        best, best_ov = None, 0
        for i, d in enumerate(speech_docs):
            if d.get("session") == t.get("session"):
                ov = _overlap(d.get("pos"), t.get("pos"))
                if ov > best_ov:
                    best, best_ov = i, ov
        attach.setdefault(best, []).append(t)

    tcount = 0

    def tingwu_item(t, alone):
        nonlocal tcount
        tcount += 1
        ref = {"c": "tingwu", "session": t.get("session"), "kind": t.get("kind", "sentence"), "n": t.get("n")}
        item = {"code": code("t", tcount, ref, t), "ref": ref, "text": t.get("text", ""), "pos": t.get("pos")}
        if alone:
            item["at"] = t["_wall"]
        return item

    speech, self_speech = [], []
    for i, d in enumerate(speech_docs):
        ref = {"c": "speech", "session": d.get("session"), "id": d.get("id")}
        voice = d.get("voice") or {}
        item = {"code": code("s", i + 1, ref, d), "ref": ref, "at": d["_wall"], "text": d.get("text", ""), "pos": d.get("pos"),
                "dur": d.get("dur"), "who": _who(d), "loud_db": voice.get("loud_db"), "voice_p": voice.get("p"),
                "emotion": d.get("emotion"), "tingwu": [tingwu_item(t, False) for t in attach.get(i, [])]}
        if _sounding(d) and item["who"]["kind"] != "self":
            item["hello_sounding"] = True      # people spoke while hello made sound (that part was silence)
        (self_speech if item["who"]["kind"] == "self" else speech).append(item)
    tingwu_only = [tingwu_item(t, True) for t in attach.get(None, [])]

    sounds, by_kind = [], {}
    sound_docs = _dedupe(sorted(window.get("sounds", []), key=lambda d: d["_wall"]),
                         lambda d: (d.get("session"), d.get("id")))
    for i, d in enumerate(sound_docs):
        ref = {"c": "sounds", "session": d.get("session"), "id": d.get("id")}
        c = code("a", i + 1, ref, d)
        g = by_kind.get(d.get("kind"))
        if g is None:
            g = {"kind": d.get("kind"), "label": d.get("label"), "count": 0, "max_loud_db": None, "codes": []}
            by_kind[d.get("kind")] = g
            sounds.append(g)
        g["count"] += 1
        g["codes"].append(c)
        if d.get("loud_db") is not None:
            g["max_loud_db"] = d["loud_db"] if g["max_loud_db"] is None else max(g["max_loud_db"], d["loud_db"])

    faces = []
    for f in face_intervals(lookback, end):
        if f["from"] < end and f["to"] > minute_ms:
            ref = {"c": "vision", "session": f["session"], "face": f["face"], "from": f["from"]}
            faces.append({"code": code("v", len(faces) + 1, ref, {}), **f})

    persons = [p for p in _intervals(lookback, end, "person", "person") if p["from"] < end and p["to"] > minute_ms]
    person_ms = _union_ms([(max(p["from"], minute_ms), min(p["to"], end)) for p in persons])

    events = {k: [] for k in STATUS_EVENTS}
    for e in sorted(window.get("vision", []), key=lambda d: d["_wall"]):
        if e.get("event") in events:
            events[e["event"]].append(e["_wall"])

    eye_docs = [d for d in window.get("eye", []) if d.get("minute") == minute_ms and d.get("source") == "model" and d.get("result")]
    eye = None
    if eye_docs:
        d = max(eye_docs, key=lambda d: d.get("version", 0))
        ref = {"c": "eye", "minute": minute_ms, "version": d.get("version")}
        eye = {"code": code("e", 1, ref, {}), "ref": ref, "result": d["result"], "frames": len(d.get("frames") or [])}

    refs = sorted(json.dumps(r, sort_keys=True, ensure_ascii=False) for r in codes.values())
    face_keys = sorted((f["face"], f["name"] or "", f["from"], f["to"]) for f in faces)
    digest = hashlib.sha1(json.dumps([refs, face_keys, BUILDER_VERSION], ensure_ascii=False).encode()).hexdigest()
    return {"minute": minute_ms, "builder": BUILDER_VERSION, "speech": speech, "tingwu_only": tingwu_only,
            "self_speech": self_speech, "sounds": sounds, "faces": faces, "eye": eye,
            "persons": {"seconds": round(person_ms / 1000), "ids": len({p["person"] for p in persons})},
            "unknown_faces": sum(1 for f in faces if not f["name"]), "events": events, "codes": codes,
            "warnings": warnings, "fingerprint": digest[:16]}
