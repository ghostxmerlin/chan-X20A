"""Versioned settings in hello.mind_settings. Every save is a new version; the newest is in force.
With no version stored yet, the settings file (phase 0) becomes version 0."""
import re
import time
from zoneinfo import ZoneInfo

from . import settings as settings_mod

MODELS = ("gpt-6-luna", "gpt-6.1-terra")
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _prompt_versions():
    try:
        from . import prompts
        return set(prompts.OFFERED)
    except ImportError:
        return {"v2"}


def _names(errs, where, value, min_items, max_items):
    if not isinstance(value, list) or not (min_items <= len(value) <= max_items):
        errs.append(f"{where}: needs {min_items}–{max_items} names")
        return
    for n in value:
        if not isinstance(n, str) or not (1 <= len(n) <= 48):
            errs.append(f"{where}: each name 1–48 characters")


def _text(errs, where, value, lo, hi):
    if not isinstance(value, str) or not (lo <= len(value) <= hi):
        errs.append(f"{where}: text of {lo}–{hi} characters")


def validate(s):
    errs = []
    if not isinstance(s, dict):
        return ["settings must be an object"]
    for k in s:
        if k not in settings_mod.DEFAULTS:
            errs.append(f"unknown setting {k}")
    s = dict(s)
    for k in ("owner", "place", "sources", "work", "fusion", "thresholds"):
        if k in s and not isinstance(s[k], dict):
            errs.append(f"{k}: must be an object")
            s[k] = {}
    try:
        if not isinstance(s.get("tz"), str):
            raise ValueError
        ZoneInfo(s["tz"])
    except Exception:                            # noqa: BLE001 - any failure means an unusable zone
        errs.append("tz: an IANA time zone such as Asia/Shanghai")
    o = s.get("owner") or {}
    _text(errs, "owner.display", o.get("display"), 1, 24)
    _names(errs, "owner.voice_names", o.get("voice_names"), 1, 8)
    aliases = o.get("aliases", [])
    if not isinstance(aliases, list) or len(aliases) > 20 or not all(isinstance(a, str) and 1 <= len(a) <= 24 for a in aliases):
        errs.append("owner.aliases: at most 20 names of 1–24 characters")
    _names(errs, "owner.face_names", o.get("face_names"), 1, 8)
    people = s.get("people", [])
    if not isinstance(people, list) or len(people) > 30:
        errs.append("people: at most 30")
    else:
        for i, p in enumerate(people):
            p = p if isinstance(p, dict) else {}
            _text(errs, f"people[{i}].display", p.get("display"), 1, 24)
            _names(errs, f"people[{i}].voice_names", p.get("voice_names", []), 0, 8)
            _names(errs, f"people[{i}].face_names", p.get("face_names", []), 0, 8)
    _text(errs, "self_facts", s.get("self_facts", ""), 0, 2000)
    _text(errs, "persona", s.get("persona", ""), 0, 2000)
    _text(errs, "place.default", (s.get("place") or {}).get("default", ""), 0, 100)
    w = s.get("work") or {}
    if not isinstance(w.get("days"), list) or not all(isinstance(d, int) and 1 <= d <= 7 for d in w.get("days", [])):
        errs.append("work.days: integers 1–7")
    if not (isinstance(w.get("start"), str) and isinstance(w.get("end"), str) and HHMM.match(w["start"]) and HHMM.match(w["end"])
            and w["start"] < w["end"]):
        errs.append("work.start / work.end: HH:MM with start before end")
    f = s.get("fusion") or {}
    if f.get("model") not in MODELS:
        errs.append(f"fusion.model: one of {', '.join(MODELS)}")
    if f.get("prompt") not in _prompt_versions():
        errs.append("fusion.prompt: unknown prompt version")
    if not isinstance(f.get("enabled"), bool):
        errs.append("fusion.enabled: true or false")
    if isinstance(f.get("max_age_min"), bool) or not (isinstance(f.get("max_age_min"), (int, float)) and f["max_age_min"] > 0):
        errs.append("fusion.max_age_min: positive number")
    r = f.get("review_after_min")
    if not (isinstance(r, list) and len(r) == 4 and all(isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0 for x in r)):
        errs.append("fusion.review_after_min: four positive numbers")
    src = s.get("sources") or {}
    for k in ("speech_text", "tingwu", "sounds", "vision"):
        if not isinstance(src.get(k), bool):
            errs.append(f"sources.{k}: true or false")
    b = s.get("budget_usd_per_day")
    if isinstance(b, bool) or not isinstance(b, (int, float)) or not (0 <= b <= 20):
        errs.append("budget_usd_per_day: number 0–20")
    for k, v in (s.get("thresholds") or {}).items():
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
            errs.append(f"thresholds.{k}: positive number")
    return errs


class SettingsStore:
    def __init__(self, db, file_path):
        self.coll = db["mind_settings"]
        self.file_path = file_path

    def _latest(self):
        return self.coll.find_one({"lane": "live"}, {"_id": 0}, sort=[("version", -1)])

    def current(self):
        doc = self._latest()
        if doc is None:
            s, _ = settings_mod.load_checked(self.file_path, None)
            self.coll.insert_one({"lane": "live", "version": 0, "settings": s, "note": "from settings file",
                                  "saved_at": int(time.time() * 1000)})
            return s, 0
        return settings_mod._merge(settings_mod.DEFAULTS, doc["settings"]), doc["version"]

    def save(self, s, note=""):
        merged = settings_mod._merge(settings_mod.DEFAULTS, s if isinstance(s, dict) else {})
        errs = validate(s if isinstance(s, dict) else {}) + ([] if isinstance(s, dict) else ["settings must be an object"])
        if errs:
            raise ValueError("; ".join(errs))
        _, version = self.current()
        self.coll.insert_one({"lane": "live", "version": version + 1, "settings": merged, "note": str(note)[:200],
                              "saved_at": int(time.time() * 1000)})
        return version + 1

    def get(self, version):
        doc = self.coll.find_one({"lane": "live", "version": version}, {"_id": 0})
        return settings_mod._merge(settings_mod.DEFAULTS, doc["settings"]) if doc else None

    def history(self, limit=20):
        return [{"version": d["version"], "note": d.get("note", ""), "saved_at": d.get("saved_at")}
                for d in self.coll.find({"lane": "live"}, {"_id": 0, "version": 1, "note": 1, "saved_at": 1})
                .sort("version", -1).limit(limit)]
