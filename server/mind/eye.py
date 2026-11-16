"""eye (plan 2026-11-11-eye, Task 1): one claude-haiku call per minute on that minute's body-camera
frames (hello.frames, at most 6) plus the M12 events, written to hello.eye_minutes.

No frames in a minute → no call and no record: frames are only uploaded when the picture changed,
so a minute without frames is either unchanged or the camera is off. Runs are logged in mind_runs
(step "eye") and share mind's daily budget. The switch (hello.switches.eye) is checked by eye_runner.
"""
import base64
import json
import os
import time

from . import llm, place, speech_settings, settings as settings_mod
from .runlog import hello_log, trace as log_trace

CHAT = llm.chat                       # injectable in tests
# 2026-11-15 bake-off (owner chose it): qwen3.8-flash direct on DashScope, thinking off: most accurate on people / place,
# p50 4.8 s / p90 6 s (haiku via aihubmix: 5.6 / 35 s), about 55 % of haiku's cost. .private/vision-bake-20261115/.
EYE_MODEL = "qwen3.8-flash"
EYE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
EYE_EXTRA = {"enable_thinking": False}
EYE_PROMPT = "eye-v6"            # v6: no guessing the owner (owner: 宁可叫不出名字，也不要叫错)
# history: v4 = forced tool call (aihubmix stopped honouring json_schema for haiku, 2026-11-11 ~15:00);
# v5 (2026-11-15, owner: no pinned place): no assumed office / desk; place_seen = what kind of place the picture looks like
# (same call, a few more output tokens), used by mind/place.py to let hello ask when it looks like somewhere else.
MIN_MS = 60_000
LOOKS_MS = 3 * 60_000            # 视觉爆发 looks (ear vision_burst.py, hello.vision_looks) used while this fresh ...
LOOKS_N = 5                      # ... at most this many, newest first
MAX_FRAMES = 6
MAX_EVENTS = 40
BACKOFF_MIN = [1, 2, 5, 10]

EYE_SCHEMA = {"type": "object", "additionalProperties": False,
              "required": ["people_count", "people", "activity", "scene", "changes", "confidence"],
              "properties": {
                  "people_count": {"type": "integer"},
                  "people": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                        "required": ["who", "where", "doing"],
                                                        "properties": {"who": {"type": "string"}, "where": {"type": "string"},
                                                                       "doing": {"type": "string"}}}},
                  "activity": {"type": "string"}, "scene": {"type": "string"}, "changes": {"type": "string"},
                  "place_seen": {"type": "string"},
                  "confidence": {"type": "string", "enum": ["high", "mid", "low"]}}}
EYE_SCHEMA["required"].insert(5, "place_seen")
CHECKED = [k for k in EYE_SCHEMA["required"] if k != "place_seen"]     # an answer without place_seen is still usable

SYSTEM = ("你是宠物机器人 hello 的视觉整理者。hello 个头很小，主人常带着它走：可能在办公室、家里、车上、饭店、户外，"
          "没有固定的地方。机身摄像头 320×240，头会转向看到的脸，"
          "所以画面只是 hello 正看着的方向。你根据这一分钟按时间排列的几张画面和 M12 人脸模块的记录，写出画面里有几个人、"
          "各是谁、在哪、在做什么、画面环境，以及这一分钟里的变化。\n"
          "- 只写看到的东西，不要假定 hello 在某个地方，也不要拿画面跟某个地方比较（不写“这不是办公室”“不像工位”这类话）。"
          "place_seen 写画面看起来像什么地方，2–8 个字，例如 办公室、家里客厅、车里、饭店、室外街边、湖边；看不出就写 看不出。\n"
          "- M12 认出的名字可信，写成给出的称呼。M12 没认出的人一律写“未认出的人”，不要猜是谁"
          "（宁可不叫名字，也不要叫错）。年龄、性别不可靠，不要写。\n"
          "- 每个看到的人在 people 里写一条，条数等于 people_count；activity 和 scene 都要写成完整的一句话。\n"
          "- M12 的“人形”是人体检测，近处的人也算在里面，人形数不代表另外还有人。人数以画面为准。\n"
          "- 画面只在有变化时才拍，所以几张之间可能隔了几十秒。\n"
          "- 用最可能的正向说法，不要堆否定句；拿不准就把 confidence 调低。只描述，不提建议。\n"
          "只输出一个 JSON 对象，不要代码块，字段名一字不改：people_count（整数）、people（数组，每项 who、where、doing）、"
          "activity、scene、changes、place_seen、confidence（high / mid / low）。")


def _hms(ms, tz):
    return settings_mod.local_time(ms, tz).strftime("%H:%M:%S")


def recent_looks(db, now_ms, window_ms=LOOKS_MS, n=LOOKS_N):
    """视觉爆发 (2026-11-15): the ear's recognised snap photos of the last window_ms, newest first: [(at ms, text)].
    One small read of hello.vision_looks (kind look); a failed look (no text) is left out."""
    rows = db["vision_looks"].find({"kind": "look", "at": {"$gte": now_ms - window_ms, "$lte": now_ms + 5_000}},
                                   {"_id": 0, "at": 1, "text": 1}).sort("at", -1).limit(n * 2)
    out = [(r["at"], r["text"].strip()) for r in rows if isinstance(r.get("text"), str) and r["text"].strip()]
    return out[:n]


def looks_line(looks, tz):
    """[(at, text)] -> '注视：HH:MM:SS text；…' (newest first), or None. The ear's realtime instructions read this prefix."""
    if not looks:
        return None
    return "注视：" + "；".join(f"{_hms(at, tz)} {str(text)[:120]}" for at, text in looks)


def _inside(root, rel):
    if not root or not isinstance(rel, str):
        return None
    base = os.path.realpath(root)
    full = os.path.realpath(os.path.join(base, rel))
    return full if full.startswith(base + os.sep) and os.path.isfile(full) else None


def _thin(items, n):
    if len(items) <= n:
        return items
    return [items[round(i * (len(items) - 1) / (n - 1))] for i in range(n)]


def event_line(e, s, owner):
    who = lambda name: owner if name in s["owner"]["face_names"] else name     # noqa: E731
    ev, t = e.get("event", ""), _hms(e["at"], s["tz"])
    if ev == "face_found":
        return f"{t} 脸#{e.get('face')} 出现"
    if ev == "face_lost":
        return f"{t} 脸#{e.get('face')} 离开画面"
    if ev == "face_named" and e.get("name"):
        return f"{t} 脸#{e.get('face')} 认出 {who(e['name'])}"
    if ev == "person_found":
        return f"{t} 人形#{e.get('person')} 出现"
    if ev == "person_lost":
        return f"{t} 人形#{e.get('person')} 离开"
    if ev == "vision_on":
        return f"{t} M12 开始检测"
    if ev == "vision_off":
        return f"{t} M12 待机"
    return None


def minute_inputs(db, recordings_root, minute_ms, s=None):
    """{"frames": [{seq, session, at, path, b64}], "events": [line], "skipped": n} for one minute."""
    s = s or settings_mod.load(None)
    end = minute_ms + MIN_MS
    docs = list(db["frames"].find({"at": {"$gte": minute_ms, "$lt": end}}, {"_id": 0}).sort("at", 1))
    frames, skipped = [], 0
    for d in docs:
        full = _inside(recordings_root, d.get("path"))
        if not full:
            skipped += 1
            continue
        with open(full, "rb") as f:
            frames.append({"seq": d.get("seq"), "session": d.get("session"), "at": d.get("at"), "path": d.get("path"),
                           "b64": base64.b64encode(f.read()).decode()})
    owner = settings_mod.owner_name(s, minute_ms)
    events = [line for line in (event_line(e, s, owner)
                                for e in db["vision"].find({"at": {"$gte": minute_ms, "$lt": end}}, {"_id": 0}).sort("at", 1))
              if line][:MAX_EVENTS]
    return {"frames": _thin(frames, MAX_FRAMES), "events": events, "skipped": skipped}


def _parse(resp):
    if not resp:
        return None, "no response"
    try:
        msg = resp["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return None, "no choices in the response"
    calls = msg.get("tool_calls") or []
    content = ((calls[0].get("function") or {}).get("arguments") if calls else None) or msg.get("content")
    text = (content or "").strip() if isinstance(content, str) else content
    if isinstance(text, str) and text.startswith("```"):          # live 2026-11-11: fenced despite json_schema
        text = text.strip("`").strip()
        text = text[text.find("{"):text.rfind("}") + 1] if "{" in text else text
    try:
        out = json.loads(text or "")
    except (ValueError, TypeError) as e:          # TypeError: content came back as a list of parts
        return None, f"not valid JSON: {e}"
    if not isinstance(out, dict):
        return None, "not a JSON object"
    missing = [k for k in CHECKED if k not in out]
    if missing:
        return None, f"JSON missing fields: {', '.join(missing)}"
    # strict json_schema still lets a hollow answer through (seen live 2026-11-11 12:28: count 1, people [], scene "x")
    people, count = out.get("people"), out.get("people_count")
    if not isinstance(people, list) or not isinstance(count, int) or len(people) != count:
        return None, "people 的条数必须等于 people_count"
    if count > 0 and not str(out.get("activity") or "").strip():
        return None, "有人时 activity 不能为空"
    if len(str(out.get("scene") or "").strip()) < 2:
        return None, "scene 要写成一句话"
    return out, None


def messages_for(s, minute_ms, inputs, prev):
    s = speech_settings.effective(s)
    if prev and prev.get('minute', 0) < speech_settings.scope(s)['since']:
        prev = None
    owner = settings_mod.owner_name(s, minute_ms)
    env_text = (s.get("environment") or "").strip()
    lines = ([f"{place.ENV_LABEL}：{env_text}"] if env_text else []) + [f"时间：{settings_mod.local_label(minute_ms, s['tz'])}；主人的称呼：{owner}",
             "M12 人脸模块这一分钟的记录：", *(inputs["events"] or ["（没有事件）"])]
    if prev and prev.get("result"):
        r = prev["result"]
        lines.append(f"上一次的视觉结果（{_hms(prev['minute'], s['tz'])}）：{r.get('people_count')} 人；{r.get('activity', '')}")
    lines.append(f"下面是这一分钟按时间顺序的 {len(inputs['frames'])} 张画面（画面有变化时才拍，最多每秒一张，这里挑了其中几张）。")
    content = [{"type": "text", "text": "\n".join(lines)}] + [
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + f["b64"]}} for f in inputs["frames"]]
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}]


def _state(db):
    return db["eye_state"].find_one({"_id": "live"}) or {}


def _set_state(db, **fields):
    db["eye_state"].update_one({"_id": "live"}, {"$set": fields}, upsert=True)


def _put(db, minute_ms, rec):
    prev = db["eye_minutes"].find_one({"minute": minute_ms}, {"version": 1}, sort=[("version", -1)])
    rec = {**rec, "minute": minute_ms, "version": (prev["version"] + 1) if prev else 1}
    db["eye_minutes"].insert_one(dict(rec))
    return rec


def run_minute(env, store, s, minute_ms, recordings_root, now_ms=None):
    """Process one minute; None when it has no readable frame (nothing is written)."""
    db = store.db
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    inputs = minute_inputs(db, recordings_root, minute_ms, s)
    if not inputs["frames"]:
        return None
    base = {"frames": [{k: f[k] for k in ("seq", "session", "at", "path")} for f in inputs["frames"]],
            "skipped": inputs["skipped"], "events_n": len(inputs["events"]), "prompt": EYE_PROMPT, "model": EYE_MODEL,
            "at": now_ms, "run_id": None, "result": None, "error": None}
    if store.cost_today(s["tz"], now_ms) >= s["budget_usd_per_day"]:
        return _put(db, minute_ms, {**base, "source": "budget"})
    if (_state(db).get("backoff_until") or 0) > now_ms:
        return _put(db, minute_ms, {**base, "source": "backoff"})
    prev = db["eye_minutes"].find_one({"minute": {"$lt": minute_ms}, "source": "model"}, {"_id": 0}, sort=[("minute", -1)])
    messages = messages_for(s, minute_ms, inputs, prev)
    # a forced tool call, not response_format: via aihubmix, haiku ignores json_schema (2026-11-11 probe: 3/3 fenced,
    # thinking on) but returns the exact schema as tool arguments (3/3, ~3 s, no thinking, ~40 % fewer tokens)
    body = {"model": EYE_MODEL, "messages": messages, "temperature": 0, "max_tokens": 1200, **EYE_EXTRA,
            "tools": [{"type": "function", "function": {"name": "eye", "description": "这一分钟画面的视觉结果",
                                                        "parameters": EYE_SCHEMA}}],
            "tool_choice": {"type": "function", "function": {"name": "eye"}}}
    t = log_trace()                              # the retry shares the first call's trace
    with hello_log.scope("call.eye", trace=t, ref={"minute": minute_ms}):
        resp, ms, err = CHAT(env["base_url"], env["api_key"], body)
    out, perr = _parse(resp) if not err else (None, err)
    usage, total_ms, retries = dict((resp or {}).get("usage") or {}), ms, 0
    if out is None:
        retries = 1
        body = dict(body, messages=messages + [{"role": "user", "content": f"上一次输出不合格（{perr}）。请严格按要求的 JSON 结构重新输出。"}])
        with hello_log.scope("call.eye", trace=t, ref={"minute": minute_ms}):
            resp, ms, err = CHAT(env["base_url"], env["api_key"], body)
        total_ms += ms
        out, perr = _parse(resp) if not err else (None, err)
        for k, v in ((resp or {}).get("usage") or {}).items():
            if isinstance(v, (int, float)):
                usage[k] = usage.get(k, 0) + v
    logged = {k: v for k, v in body.items() if k != "messages"}
    logged["messages"] = [messages[0], {"role": "user", "content": [c if c["type"] == "text" else
                                                                     {"type": "image_url", "frame": f["seq"]}
                                                                     for c, f in zip(messages[1]["content"],
                                                                                     [None] + inputs["frames"])]}]
    run_id = store.put_run({"lane": "live", "minute": minute_ms, "step": "eye", "prompt": EYE_PROMPT, "model": EYE_MODEL,
                            "request": logged, "response": resp, "parsed": out, "error": None if out else perr,
                            "latency_ms": total_ms, "usage": usage, "cost_usd": llm.cost_usd(EYE_MODEL, usage),
                            "retries": retries, "at": now_ms})
    if out is None:
        failures = (_state(db).get("failures") or 0) + 1
        _set_state(db, failures=failures, backoff_until=now_ms + BACKOFF_MIN[min(failures - 1, 3)] * MIN_MS,
                   last_error=str(perr)[:200])
        return _put(db, minute_ms, {**base, "source": "failed", "run_id": run_id, "error": str(perr)[:300]})
    _set_state(db, failures=0, backoff_until=0)
    return _put(db, minute_ms, {**base, "source": "model", "run_id": run_id, "result": out, "latency_ms": total_ms})
