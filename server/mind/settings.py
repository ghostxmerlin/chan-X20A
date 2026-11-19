"""mind settings and time helpers.

The real settings file (with real names) lives only on the server, ~/soul-mind/settings.json;
settings.example.json in the repository uses placeholders. Missing fields take DEFAULTS.
"""
import copy
import datetime
import json
from zoneinfo import ZoneInfo
from . import speech_settings

DEFAULTS = {
    "tz": "Asia/Shanghai",
    "owner": {"display": "主人", "aliases": [], "voice_names": ["me"], "face_names": ["me"]},   # aliases: one per day
    "people": [],                 # [{"display", "voice_names", "face_names"}]
    "work": {"days": [1, 2, 3, 4, 5], "start": "09:00", "end": "19:00"},
    "thresholds": {"just_left_min": 2, "voice_recent_min": 5, "face_recent_min": 10, "absent_after_min": 20,
                   "coverage_min_s": 30, "open_session_tail_s": 120, "face_tail_s": 120, "drift_warn_s": 5},
    # phase 1
    "self_facts": "我叫 hello，是主人的宠物机器人，主人常带着我走。我有一个麦克风和一个会认人脸的摄像头（M12），头会转向看到的脸。",
    # place.default is no longer read (2026-11-15, owner: no pinned place): where hello is now = mind/place.py (the latest
    # place report, else unknown). Kept so stored settings and the page's 默认地点 field still validate.
    "place": {"default": ""},
    # 环境说明 (2026-11-11): the office, the company, the people, the work and its words, the day; shared by mind,
    # eye and the conversation notes. Real content lives only in the settings database, never in the repo.
    "environment": "",
    "persona": "",                # stored only, never sent to the perception prompt
    "sources": {"speech_text": True, "tingwu": True, "sounds": True, "vision": True},
    "fusion": {"enabled": True, "model": "gpt-6-luna", "prompt": "v3", "max_age_min": 60, "review_after_min": [5, 10, 20, 30]},
    # The one daily cap and the away pause (2026-11-17, mind/pause.py): today's spend over every paid item (the run
    # log's ledger, USD at usd_cny) against cap_cny_per_day; nobody seen / known voice / owner input for away_min
    # minutes pauses the paid items. Replaces budget_usd_per_day (mind's own model calls only), which is not read.
    # owner 2026-11-19: money in USD, the daily cap is $10 (was cap_cny_per_day ¥20, no longer read; dropped on save)
    "pause": {"away_min": 10, "cap_usd_per_day": 10.0, "usd_cny": 7.2},
    "speech": speech_settings.defaults(),
    "memory": {"learning": True, "apply": True},
    # 深入研究 (2026-11-18, mind/research.py): aihubmix model (Responses API + web_search), the per-task cap (counted in
    # the daily cap too), how many jobs run at once (1–2).
    "research": {"model": "gpt-6.2-sol", "cap_cny_per_task": 2.0, "max_jobs": 1},
}
# Keys older settings versions still carry; dropped on every read and save, never an "unknown setting" error.
LEGACY_KEYS = ("budget_usd_per_day",)       # replaced by pause.cap_cny_per_day (2026-11-17)
MINUTE_MS = 60_000


def drop_legacy(s):
    if isinstance(s, dict):
        for k in LEGACY_KEYS:
            s.pop(k, None)
        if isinstance(s.get("pause"), dict):
            s["pause"].pop("cap_cny_per_day", None)      # replaced by pause.cap_usd_per_day (2026-11-19)
    return s


def _merge(base, extra):
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def load(path):
    """Settings from a JSON file merged over DEFAULTS; a missing or unreadable file gives DEFAULTS."""
    data = {}
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
    return drop_legacy(_merge(DEFAULTS, data if isinstance(data, dict) else {}))


def load_checked(path, last_good):
    """(settings, error). A missing file gives DEFAULTS with no error; a file that does not parse keeps
    the last good settings (or DEFAULTS) and returns the error, so a typo never silently resets names."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return _merge(DEFAULTS, {}), ""
    except (OSError, ValueError) as e:
        return (last_good if last_good is not None else _merge(DEFAULTS, {})), f"{type(e).__name__} (JSON): {str(e)[:160]}"
    if not isinstance(data, dict):
        return (last_good if last_good is not None else _merge(DEFAULTS, {})), "settings must be a JSON object"
    return drop_legacy(_merge(DEFAULTS, data)), ""


def load_env(path):
    """KEY=VALUE lines (quotes stripped, # lines ignored); missing file → {}."""
    cfg = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    cfg[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return cfg


def owner_name(s, ms):
    """How the owner is called on the local day of ms: one of owner.aliases, the same all day, rotating
    from day to day (stable within a day so the model does not read it as a new person); else display."""
    aliases = [a for a in (s.get("owner") or {}).get("aliases") or [] if a]
    if not aliases:
        return s["owner"]["display"]
    day = local_time(ms, s["tz"]).date()
    import hashlib
    return aliases[int(hashlib.sha1(day.isoformat().encode()).hexdigest(), 16) % len(aliases)]


def minute_floor(ms):
    return ms - ms % MINUTE_MS


def local_time(ms, tz):
    return datetime.datetime.fromtimestamp(ms / 1000, ZoneInfo(tz))


def local_label(ms, tz):
    return local_time(ms, tz).strftime("%m-%d %H:%M")


def in_work_hours(ms, s):
    t = local_time(ms, s["tz"])
    hm = t.strftime("%H:%M")
    return t.isoweekday() in s["work"]["days"] and s["work"]["start"] <= hm < s["work"]["end"]
