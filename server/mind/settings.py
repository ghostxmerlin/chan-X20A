"""mind settings and time helpers.

The real settings file (with real names) lives only on the server, ~/soul-mind/settings.json;
settings.example.json in the repository uses placeholders. Missing fields take DEFAULTS.
"""
import copy
import datetime
import json
from zoneinfo import ZoneInfo

DEFAULTS = {
    "tz": "Asia/Shanghai",
    "owner": {"display": "主人", "voice_names": ["me"], "face_names": ["me"]},
    "people": [],                 # [{"display", "voice_names", "face_names"}]
    "work": {"days": [1, 2, 3, 4, 5], "start": "09:00", "end": "19:00"},
    "thresholds": {"just_left_min": 2, "voice_recent_min": 5, "face_recent_min": 10, "absent_after_min": 20,
                   "coverage_min_s": 30, "open_session_tail_s": 120, "face_tail_s": 120, "drift_warn_s": 5},
    # phase 1
    "self_facts": "我叫 hello，是主人放在办公桌上的桌面宠物机器人。我有一个麦克风和一个会认人脸的摄像头（M12），头会转向看到的脸。",
    "place": {"default": "办公室，主人的办公桌上"},
    "persona": "",                # stored only, never sent to the perception prompt
    "sources": {"speech_text": True, "tingwu": True, "sounds": True, "vision": True},
    "fusion": {"enabled": True, "model": "gpt-6-luna", "prompt": "v1", "max_age_min": 60, "review_after_min": [5, 10, 20, 30]},
    "budget_usd_per_day": 20.0,   # runaway guard only (user: cost is not a concern, but keep statistics)
}
MINUTE_MS = 60_000


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
    return _merge(DEFAULTS, data if isinstance(data, dict) else {})


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
    return _merge(DEFAULTS, data), ""


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
