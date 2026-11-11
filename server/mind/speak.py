"""hello speaks (spec docs/superpowers/specs/2026-11-11-speak-design.md).

The page picks a role (config/roles/*.json: 吐槽 = a sharp but kind stand-up comic, 鼓励 = a gentle
programmers' comforter speaking Shanghainese); luna writes one spoken sentence from what hello knows now
(environment, the scene, the latest eye result, the current conversation notes, the last words heard and
her own last lines), and soul-tts says it through the robot's /mouth stream. No device on /mouth: refused,
nothing is called. Every line goes to hello.utterances (and its tts usage once it has been said).
"""
import json
import os
import re
import threading
import time

from . import llm, notes, settings as settings_mod
from .records import Records
from .store import LANE

CHAT = llm.chat
MODEL = "gpt-6-luna"
PROMPT = "say-v1"
MAX_CHARS = 30
FRESH_MS = 5 * 60_000
TTS_CNY_PER_M = (1.5, 12.0)          # qwen-audio-3.1-tts-flash, per million input / output tokens (user 2026-11-11)
REQUIRED = ("id", "button", "name", "persona", "rules", "voice")
SAY_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["text"],
              "properties": {"text": {"type": "string"}}}


class NotConnected(Exception):
    pass


def load_roles(directory):
    roles = {}
    for name in sorted(os.listdir(directory)) if os.path.isdir(directory) else []:
        if not name.endswith(".json"):
            continue
        try:
            r = json.load(open(os.path.join(directory, name), encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(r, dict) and all(r.get(k) for k in REQUIRED) and isinstance(r["rules"], list):
            roles[r["id"]] = r
    return roles


def context(db, store, s, now_ms):
    scene = store.latest_scene_any(LANE)
    sc = (scene or {}).get("scene") or {}
    fresh_scene = scene and now_ms - scene["minute"] <= FRESH_MS + 60_000
    eye = db["eye_minutes"].find_one({"source": "model"}, {"_id": 0}, sort=[("minute", -1)])
    conv = db["conversations"].find_one({}, {"_id": 0}, sort=[("start_ms", -1)])
    conv_ok = conv and (conv.get("notes") or conv.get("title")) and \
        (conv.get("end_ms") is None or now_ms - conv["end_ms"] <= FRESH_MS)
    heard = notes.new_sentences(db, Records(db), now_ms - 2 * 60_000, now_ms, set())[-10:]
    mine = [u["text"] for u in db["utterances"].find({}, {"_id": 0, "text": 1}).sort("at", -1).limit(5)]
    return {"environment": (s.get("environment") or "").strip(),
            "time": settings_mod.local_label(now_ms, s["tz"]),
            "doing": (sc.get("people_doing") or {}).get("text") if fresh_scene else None,
            "summary": sc.get("summary") if fresh_scene else None,
            "eye": (eye.get("result") or {}).get("activity") if eye and now_ms - eye["minute"] <= FRESH_MS + 60_000 else None,
            "conversation": f"{conv.get('title') or ''}：{conv.get('notes') or ''}" if conv_ok else None,
            "heard": [x["text"] for x in heard], "mine": mine}


def clean(text):
    t = re.sub(r"[（(\[【][^）)\]】]*[）)\]】]", "", str(text or ""))          # stage directions
    t = re.sub(r"[“”\"'「」『』]", "", t).strip()
    if len(t) <= MAX_CHARS:
        return t
    head = t[:MAX_CHARS]
    for marks, keep in (("。！？…!?", True), ("，、；,;", False)):
        i = max(head.rfind(m) for m in marks)
        if i > 0:
            return head[:i + 1] if keep else head[:i] + "。"
    return head + "。"


def _messages(role, ctx):
    system = (role["persona"] + "\n\n【要求】\n" + "\n".join(f"- {r}" for r in role["rules"]) +
              f"\n- 只输出一句要说出口的话（JSON 的 text），不超过 {MAX_CHARS} 字，不加引号、括号、动作描写。"
              "\n- 说的内容要和此刻的情景有关；不要重复自己最近说过的话。")
    L = []
    if ctx["environment"]:
        L.append(f"环境：{ctx['environment']}")
    L.append(f"现在：{ctx['time']}")
    if ctx["doing"]:
        L.append(f"此刻的情景：{ctx['doing']}（{ctx['summary'] or ''}）")
    if ctx["eye"]:
        L.append(f"刚才看到：{ctx['eye']}")
    if ctx["conversation"]:
        L.append(f"正在进行的对话：{ctx['conversation']}")
    if ctx["heard"]:
        L.append("刚听到的话：" + " / ".join(ctx["heard"]))
    if ctx["mine"]:
        L.append("我最近说过：" + " / ".join(ctx["mine"]))
    if len(L) <= 2:
        L.append("（此刻没有什么特别的情况）")
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(L)}]


def tts_cost(status):
    tin = tout = 0
    for a in (status or {}).get("attempts") or []:
        u = a.get("usage") or {}
        tin += u.get("input_tokens") or 0
        tout += u.get("output_tokens") or 0
    return (tin * TTS_CNY_PER_M[0] + tout * TTS_CNY_PER_M[1]) / 1e6, tin, tout


def _finish(db, tts, uid, tts_id):
    try:
        st = tts.status(tts_id, wait=60)
    except Exception as e:                       # noqa: BLE001 - recorded
        db["utterances"].update_one({"uid": uid}, {"$set": {"state": "unknown", "error": f"{type(e).__name__}"}})
        return
    cost, tin, tout = tts_cost(st)
    db["utterances"].update_one({"uid": uid}, {"$set": {"state": st.get("state"), "tts_cost_cny": cost, "tts_tokens": [tin, tout],
                                                        "timings_s": st.get("timings_s")}})


def say(env, store, s, roles, role_id, tts, now_ms, wait=False):
    role = roles[role_id]                        # KeyError: unknown role
    if not ((tts.health().get("mouth") or {}).get("device")):
        raise NotConnected("hello 的嘴没连上")
    db = store.db
    ctx = context(db, store, s, now_ms)
    body = {"model": MODEL, "messages": _messages(role, ctx), "temperature": 0.9, "max_tokens": 2000, "reasoning_effort": "low",
            "response_format": {"type": "json_schema", "json_schema": {"name": "say", "strict": True, "schema": SAY_SCHEMA}}}
    resp, ms, err = CHAT(env["base_url"], env["api_key"], body)
    text = ""
    if resp and not err:
        try:
            text = clean(json.loads(resp["choices"][0]["message"]["content"]).get("text"))
        except (KeyError, IndexError, TypeError, ValueError):
            text = ""
    usage = (resp or {}).get("usage") or {}
    run_id = store.put_run({"lane": "live", "minute": settings_mod.minute_floor(now_ms), "step": "say", "prompt": PROMPT,
                            "model": MODEL, "request": body, "response": resp, "error": err if not text else None,
                            "latency_ms": ms, "usage": usage, "cost_usd": llm.cost_usd(MODEL, usage), "at": now_ms, "role": role_id})
    if not text:
        raise RuntimeError(err or "没写出句子")
    voice = role["voice"]
    opts = {k: role[k] for k in ("instruction", "rate") if role.get(k) is not None}
    tts_id = tts.say(text, voice=voice, interrupt=True, **opts)
    uid = f"u{now_ms}"
    rec = {"uid": uid, "role": role_id, "text": text, "voice": voice, "tts_id": tts_id, "at": now_ms, "state": "sent",
           "run_id": run_id, "llm_ms": ms, "context": {k: ctx[k] for k in ("doing", "eye", "conversation")}}
    db["utterances"].insert_one(dict(rec))
    if wait:
        _finish(db, tts, uid, tts_id)
    else:
        threading.Thread(target=_finish, args=(db, tts, uid, tts_id), daemon=True).start()
    return rec


class TtsHttp:
    """Minimal client for soul-tts on the loopback (standard library only)."""
    def __init__(self, base, token, timeout=15):
        self.base, self.token, self.timeout = base.rstrip("/"), token, timeout

    @classmethod
    def from_file(cls, base, token_file):
        """None when the token file is missing (soul-tts not deployed): /say then answers 409."""
        try:
            token = open(token_file).read().strip()
        except OSError:
            return None
        return cls(base, token) if token else None

    def _call(self, method, path, body=None, timeout=None):
        import urllib.error
        import urllib.request
        req = urllib.request.Request(self.base + path, method=method, data=json.dumps(body or {}).encode() if method == "POST" else None,
                                     headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code in (409, 429):         # no device on /mouth, or too many queued: nothing was paid for
                raise NotConnected("hello 的嘴没连上" if e.code == 409 else "hello 还有话没说完") from None
            raise

    def health(self):
        return self._call("GET", "/health")

    def say(self, text, **kw):
        return self._call("POST", "/v1/say", {"text": text, **kw})["id"]

    def status(self, uid, wait=0):
        return self._call("GET", f"/v1/say/{uid}" + (f"?wait={wait:g}" if wait else ""), timeout=wait + 10)

    def stop(self):
        return self._call("POST", "/v1/stop").get("cancelled", [])
