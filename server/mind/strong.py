"""S2: sensor coverage, strong elements and the owner state, by rules only.

Everything is a function of (minute package, lookback records, settings, now), so a replay with
other thresholds gives the same answer the live run would have given.
Rules: docs/superpowers/plans/2026-11-10-mind-phase0.md, Task 3.
"""
import re

from . import settings as settings_mod
from .package import face_intervals

MIN_MS = 60_000
VISION_SESSION = re.compile(r"-v\d+$")


def _overlap(a0, a1, b0, b1):
    return max(0, min(a1, b1) - max(a0, b0))


def _union_overlap(spans, m0, m1):
    clipped = sorted((max(s, m0), min(e, m1)) for s, e in spans if e > m0 and s < m1)
    total, cs, ce = 0, None, None
    for s, e in clipped:
        if ce is None or s > ce:
            if ce is not None:
                total += ce - cs
            cs, ce = s, e
        else:
            ce = max(ce, e)
    if ce is not None:
        total += ce - cs
    return total


def _is_hearing(sess):
    return (str(sess.get("source", "")).startswith("stackchan-") and sess.get("kind") != "vision"
            and not VISION_SESSION.search(str(sess.get("session", ""))))


def _hearing_spans(lookback, s, now_ms):
    tail = s["thresholds"]["open_session_tail_s"] * 1000
    sessions = [x for x in lookback.get("sessions", []) if _is_hearing(x)]
    last = {}
    for name in ("speech", "sounds", "tingwu"):
        for d in lookback.get(name, []):
            last[d.get("session")] = max(last.get(d.get("session"), 0), d["_wall"])
    spans, estimated = [], False
    for x in sessions:
        start = x.get("start_ms", 0)
        if x.get("end_ms") is not None:
            end = x["end_ms"]
        else:
            estimated = True
            nexts = [o["start_ms"] for o in sessions if o is not x and o.get("source") == x.get("source")
                     and o.get("start_ms", 0) > start]
            end = min([last.get(x["session"], start) + tail, now_ms] + nexts)
        spans.append((start, min(end, now_ms)))
    return spans, estimated


def _toggle_spans(events, on_name, off_name, window_start, end_ms):
    """On-intervals from on/off events; an off seen first means it was on since the window start."""
    marks = sorted((e["_wall"], e["event"]) for e in events if e.get("event") in (on_name, off_name))
    if not marks:
        return None
    spans, on_since = [], window_start if marks[0][1] == off_name else None
    for t, ev in marks:
        if ev == on_name and on_since is None:
            on_since = t
        elif ev == off_name and on_since is not None:
            spans.append((on_since, t))
            on_since = None
    if on_since is not None:
        spans.append((on_since, end_ms))
    return spans


def _display(name, s):
    for p in s.get("people", []):
        if name in p.get("voice_names", []) or name in p.get("face_names", []):
            return p.get("display", name)
    return name


def evaluate(pkg, lookback, minute_ms, s, now_ms):
    th = s["thresholds"]
    m0, m1 = minute_ms, minute_ms + MIN_MS
    horizon = min(m1, now_ms)
    owner_voice_names = set(s["owner"]["voice_names"])
    owner_face_names = set(s["owner"]["face_names"])
    lookback_start = min([d["_wall"] for name in ("speech", "sounds", "tingwu", "vision") for d in lookback.get(name, [])]
                         + [m0 - 30 * MIN_MS])

    # --- coverage ---
    hear_spans, estimated = _hearing_spans(lookback, s, now_ms)
    hearing_s = round(_union_overlap(hear_spans, m0, m1) / 1000)
    vis_events = lookback.get("vision", [])
    detections_in_minute = any(str(e.get("event", "")).startswith(("face_", "person_")) and m0 <= e["_wall"] < m1
                               for e in vis_events)
    cover = {}
    for key, on, off in (("vision_s", "vision_on", "vision_off"), ("m12_s", "m12_online", "m12_offline")):
        spans = _toggle_spans(vis_events, on, off, lookback_start, horizon)
        if spans is None:
            spans = [(m0, horizon)] if detections_in_minute else []
            estimated = estimated or bool(spans)
        cover[key] = round(_union_overlap(spans, m0, horizon) / 1000)

    # --- owner face / voice ---
    faces = face_intervals(lookback, m1)
    owner_faces = [f for f in faces if f["name"] in owner_face_names and f["from"] < m1]
    face_last = max((min(f["to"], horizon) for f in owner_faces), default=None)
    in_view = any(f["from"] < m1 and f["to"] > m0 for f in owner_faces)
    face_codes = [f["code"] for f in pkg["faces"] if f["name"] in owner_face_names]

    def face_near(t):
        r = th["face_recent_min"] * MIN_MS
        return any(f["from"] - r <= t <= f["to"] + r for f in owner_faces)

    known_last = likely_last = None
    for d in lookback.get("speech", []):
        if d["_wall"] >= m1 or (d.get("self") or {}).get("sounding"):
            continue
        sp = d.get("speaker") or {}
        if sp.get("band") == "known" and sp.get("name") in owner_voice_names:
            known_last = max(known_last or 0, d["_wall"])
        elif sp.get("band") == "unsure" and sp.get("best") in owner_voice_names and face_near(d["_wall"]):
            likely_last = max(likely_last or 0, d["_wall"])
    voice_codes = [x["code"] for x in pkg["speech"]
                   if (x["who"]["kind"] == "known" and x["who"]["name"] in owner_voice_names)
                   or (x["who"]["kind"] == "unsure" and x["who"]["best"] in owner_voice_names and face_near(x["at"]))]

    # --- others ---
    known = []
    for name in [f["name"] for f in pkg["faces"] if f["name"] and f["name"] not in owner_face_names] + \
                [x["who"]["name"] for x in pkg["speech"] if x["who"]["kind"] == "known" and x["who"]["name"] not in owner_voice_names]:
        shown = _display(name, s)
        if shown not in known:
            known.append(shown)
    stranger_codes = [x["code"] for x in pkg["speech"]
                      if x["who"]["kind"] == "unknown" and x["who"]["best"] not in owner_voice_names]

    # --- owner state ---
    typing = any(g["kind"] == "typing" for g in pkg["sounds"])
    since = lambda t: None if t is None else m1 - t        # noqa: E731
    voice_recent = th["voice_recent_min"] * MIN_MS
    if hearing_s == 0 and cover["vision_s"] == 0:
        state, why, codes = "sensors_off", "听觉和视觉这一分钟都没有工作", []
    elif in_view:
        state, why, codes = "seen", "主人的脸在画面里", face_codes
    elif face_last is not None and since(face_last) <= th["just_left_min"] * MIN_MS:
        state, why, codes = "just_left", f"主人的脸 {since(face_last) // 1000} 秒前还在", []
    elif (known_last is not None and since(known_last) <= voice_recent) or \
            (likely_last is not None and since(likely_last) <= voice_recent):
        state, why, codes = "likely_present", "最近听到主人的声音", voice_codes
    elif face_last is not None and since(face_last) <= th["face_recent_min"] * MIN_MS and typing:
        state, why, codes = "likely_present", "主人的脸不久前还在，现在有打字声", [c for g in pkg["sounds"] if g["kind"] == "typing" for c in g["codes"]]
    elif max(hearing_s, cover["vision_s"]) >= th["coverage_min_s"] and \
            all(t is None or since(t) > th["absent_after_min"] * MIN_MS for t in (face_last, known_last, likely_last)):
        state, why, codes = "likely_absent", f"感官开着，{th['absent_after_min']} 分钟以上没有主人的脸和声音", []
    else:
        state, why, codes = "unknown", "证据不足", []

    contradictions = []
    if in_view and stranger_codes:
        contradictions.append({"kind": "face_owner_voice_stranger", "codes": face_codes + stranger_codes})

    t = settings_mod.local_time(m0, s["tz"])
    return {"coverage": {"hearing_s": hearing_s, "vision_s": cover["vision_s"], "m12_s": cover["m12_s"],
                         "estimated": estimated},
            "time": {"label": settings_mod.local_label(m0, s["tz"]), "weekday": t.isoweekday(),
                     "work_hours": settings_mod.in_work_hours(m0, s)},
            "place": {"label": None, "source": "no_wifi_data"},
            "owner_face": {"last_ms": face_last, "in_view": in_view, "codes": face_codes},
            "owner_voice": {"known_last_ms": known_last, "likely_last_ms": likely_last, "codes": voice_codes},
            "others": {"known": known, "unknown_faces": pkg["unknown_faces"], "unknown_voices": bool(stranger_codes)},
            "owner": {"state": state, "why": why, "codes": codes},
            "contradictions": contradictions}
