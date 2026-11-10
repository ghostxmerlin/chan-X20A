"""Deterministic checks on a model scene (spec 5.4) and merging its entry operations onto the previous
minute's entries (spec 5.3). The model never gets the last word on evidence or confidence."""
from .prompts import DIMENSIONS, RULE_CODES

BASES = ("seen", "heard", "inferred", "assumed")
CONFS = ("high", "mid", "low")
VIEWS = ("present", "absent", "unknown")

RANK = {"low": 0, "mid": 1, "high": 2}
OWNER_CLASS = {"seen": "present", "just_left": "present", "likely_present": "present", "likely_absent": "absent",
               "unknown": "unknown", "sensors_off": "unknown"}
MAX_ENTRIES, MAX_TEXT, MAX_SUMMARY = 20, 200, 20


def _is_pkg_code(code, pkg):
    return code in pkg.get("codes", {})


def apply(out, prev_entries, pkg, strong, minute_ms):
    checks = {"invalid_refs": 0, "downgraded": 0, "raise_blocked": 0, "ended": 0, "omitted": 0,
              "rule_conflict": False, "language_only": 0.0}
    prev = {e["key"]: e for e in prev_entries or []}
    merged, seen = [], set()
    for e in (out.get("entries") or [])[:MAX_ENTRIES]:
        key = str(e.get("key", ""))[:60]
        if not key or key in seen:
            continue
        seen.add(key)
        if e.get("op") == "end":
            checks["ended"] += 1
            continue
        if e.get("op") == "keep" and key in prev:
            merged.append(dict(prev[key]))
            continue
        ev = []
        for c in e.get("evidence") or []:
            if isinstance(c, str) and (_is_pkg_code(c, pkg) or c in RULE_CODES):
                ev.append(c)
            else:
                checks["invalid_refs"] += 1
        fresh = any(_is_pkg_code(c, pkg) for c in ev)
        basis = e.get("basis") if e.get("basis") in BASES else "assumed"      # never trust enum fields
        conf = e.get("confidence") if e.get("confidence") in CONFS else "low"
        if basis in ("seen", "heard") and not fresh:
            basis = "inferred"
            checks["downgraded"] += 1
        old = prev.get(key)
        if old and RANK.get(conf, 0) > RANK.get(old.get("confidence"), 0) and not fresh:
            conf = old.get("confidence")
            checks["raise_blocked"] += 1
        merged.append({"key": key, "dimension": e.get("dimension") if e.get("dimension") in DIMENSIONS else "other",
                       "op": "update" if old else "add",
                       "text": str(e.get("text", ""))[:MAX_TEXT], "basis": basis, "confidence": conf, "evidence": ev,
                       "since": old.get("since", minute_ms) if old else minute_ms,
                       "last_evidence": minute_ms if fresh else (old.get("last_evidence", minute_ms) if old else minute_ms)})
    for key, old in prev.items():
        if key not in seen:
            merged.append(dict(old))
            checks["omitted"] += 1
    merged = merged[:MAX_ENTRIES]

    rule = OWNER_CLASS.get((strong.get("owner") or {}).get("state"), "unknown")
    view = out.get("owner_view") if out.get("owner_view") in VIEWS else "unknown"
    checks["rule_conflict"] = rule != "unknown" and view != "unknown" and rule != view

    has_other = any(c[0] in "av" for c in pkg.get("codes", {}))
    judged = [e for e in merged if e.get("dimension") in ("people", "activity")]
    if has_other and judged:
        only_lang = sum(1 for e in judged if e["evidence"] and all(c[0] in "st" and not c.startswith("rule:") for c in e["evidence"]))
        checks["language_only"] = round(only_lang / len(judged), 3)

    def answer(a):
        a = a if isinstance(a, dict) else {}
        return {"text": str(a.get("text", ""))[:MAX_TEXT], "confidence": a.get("confidence") if a.get("confidence") in CONFS else "low"}

    scene = {"who_am_i": answer(out.get("who_am_i")), "where_am_i": answer(out.get("where_am_i")),
             "people_doing": answer(out.get("people_doing")), "owner_view": view, "entries": merged,
             "changes": [str(c)[:MAX_TEXT] for c in (out.get("changes") or [])[:10]],
             "summary": str(out.get("summary", ""))[:MAX_SUMMARY], "want_to_know": str(out.get("want_to_know", ""))[:MAX_TEXT]}
    return scene, checks
