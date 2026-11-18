"""HTTP API of mind on the server's loopback only (the Mac reaches it over ssh). Every call needs
"Authorization: Bearer <token>". POST bodies: application/json, at most 16 KiB (validated settings: 512 KiB). POSTs write only
mind tables (settings versions, labels/marks, rerun lane) — never raw records, never the live
scenes. Sentence text only leaves through this API; nothing is logged.

GET  /state /timeline /minute /now /segments /scene /settings /names /prompts /labels /stats /switches /frames/latest /eye/latest /eye /notes /roles /utterances /voices
POST /settings /mark /label /rerun /place /switch /switches /say /say/stop /voice/try
GET  /place; POST /place {label: text | null, source: page | robot} /place/choices {choices} | {add} | {remove}
     /place/told {text, source: crowd | chat} (where hello is now, mind/place.py; spec 2026-11-15-place-contract.md)
POST /say/round /say/play /signal /say/prefetch /say/auto (talk show; 连续说 state is in GET /say/stats under auto)
GET  /db/overview /db/people /db/person /db/memories /db/knowledge /db/item (library, read only)
     /db/changes (extracted memories, latest change first; lane live / replay) /db/sources (one memory's sources)
     /db/extract (记忆整理: switch, caps, spend, recent jobs; long-term memory phase A, mind/memory_extract.py)
POST /db/person /db/person/note /db/memory /db/knowledge (library, append-only versions; no delete)
     /db/memory/review (对 confirm / 错 wrong = disabled / 合并 merge; 改 is /db/memory) /db/extract/switch {on}
     /db/extract/run {day?} (ask the memory worker to run that day now; it still checks the switch and caps)
GET  /realtime/context (ear: 围观 realtime voice session's context block + owner face names; mind/realtime.py)
GET  /looks?limit= /looks/frame?lid= (page: 视觉爆发 looks, bursts, running; one look's photo as jpeg_b64)
GET  /log?from&to&kind&svc&trace&cost&limit&before /log/body?at&id /log/chain?trace&at /log/ledger?day /log/gaps?from&to
     /log/last (日志 page: the run/audit log through server/common/hello_log_query.py, read only; kind ending in "."
     is a prefix; bodies only by id, one per /log/body; 503 when hello_log_query is not deployed; docs/hello-log.md)
GET  /chat/turns /chat/state; POST /chat/heard /chat/stage (ear: a speech onset in chat, the page's dots) (ear: one sentence heard while hello is in chat; chat v0, mind/chat.py)
GET  /mode /mode/seq; POST /mode/report (ear: hello's own mode change) /mode/robot (ear: hello's /mouth closed)
     /mode/input (ear: hello's digits 0 / 9) /mode (page: billing only) /preset (modes step 1b: hello owns the mode,
     mind mirrors it; /switches also takes {name: billing, on} = preset 9 / preset 0). With a mode store in ctx, 说话类
     接口 answer 409 {code: only_talk} outside talk (the mirrored mode); /say/stop always works. Billing gates nothing
     (owner 2026-11-14 evening): single switches always work.
     The pause (2026-11-17, mind/pause.py): GET /switches carries `pause` {paused, why, since, away_since, spent_cny,
     cap_cny, ...}; every explicit owner input (OWNER_POSTS, hello's own reports and digits except her power-on 9)
     goes to pause.Controller.note_owner (lifts an away pause; a 9 also lifts today's budget pause).
"""
import base64
import copy
import hmac
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import fusion, memory_extract, pause, place, prompts, runlog, segments, settings as settings_mod, speak
from .settings_store import MODELS, SettingsConflict
from . import speech_settings
from .memory import MemoryStore, MemoryConflict
from .attention import NoSituation
from .model_calls import BudgetExceeded
from .store import LANE
from . import owner_signals
from .library import Library, LibraryConflict, COLLECTIONS, ID_RE
from .modes import INPUTS as MODE_INPUTS
from . import tool_impls
from .tools import Tools, load_registry

try:                                             # ~/soul-mind/hello_log_query.py; server/common in the repository
    import hello_log_query as log_query          # (runlog puts server/common on sys.path when hello_log is not found)
except ImportError:
    log_query = None

MAX_RANGE_MS = 24 * 3600_000
MAX_BODY = 16 * 1024
MAX_SETTINGS_BODY = 512 * 1024  # validated multilingual profiles and existing identity lists
MARKS = ("arrive", "leave", "meeting", "others_near", "alone")
VERDICTS = ("right", "wrong", "unsure")
FIELDS = ("owner", "people", "activity", "topic", "summary", "other")
UTTERANCE_FIELDS = ('uid','at','role','text','state','tts_cost_cny','timings_s','pipeline','scope_id','server_ms')


def compact_utterance(row):
    return {k: row[k] for k in UTTERANCE_FIELDS if k in row}


class BadRequest(Exception):
    pass


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class ModeConflict(Conflict):
    """409 with a mode result code (only_talk / on_hello) next to the message, for the page."""
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


# Step 1b (owner 2026-11-14): hello switches the mode itself and reports it (/mode/report). mind only takes the inputs
# that are its own: the billing switch (= preset 9 / 0) and the presets (digit; only 0 and 9 do something).
MIND_INPUTS = ('digit', 'billing')
DEVICE_SOURCES = ('asr', 'touch', 'select', 'quick')                               # what ear forwards for hello
# The page's / DualKey's posts that are an owner's explicit input (the pause, mind/pause.py: "back"). The ear's own
# 闭嘴 post carries by: "hush" and is not one (a TV can say 闭嘴).
OWNER_POSTS = ('/switch', '/switches', '/mode', '/preset', '/signal', '/say', '/say/round', '/say/play', '/say/stop',
               '/say/prefetch', '/say/auto', '/db/extract/switch', '/db/extract/run')
ON_HELLO = 'mode is switched on hello'


def _int(q, name, required=True):
    values = q.get(name)
    if not values:
        if required:
            raise BadRequest(f"{name} required")
        return None
    try:
        return int(values[0])
    except ValueError:
        raise BadRequest(f"{name} must be an integer") from None


def _range(q):
    lo, hi = _int(q, "from"), _int(q, "to")
    if hi <= lo or hi - lo > MAX_RANGE_MS:
        raise BadRequest("range must be positive and at most 24 h")
    return lo, hi


LOG_SVCS = ("ear", "mind", "eye", "notes", "memory", "tts")
LOG_MAX_RANGE_MS = 7 * 86400_000                # /log: at most 7 days per query
LOG_GAPS_MAX_MS = 48 * 3600_000                  # /log/gaps: at most 48 h (lifecycle rows only, but bounded)
LOG_DEFAULT_MS = 3600_000
LOG_LAST_CACHE_MS = 60_000                       # /log/last (hello_log_query.last_seen) is read at most once a minute
LOG_KIND_RE = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")
LOG_TRACE_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
LOG_ID_RE = re.compile(r"^[a-z]{1,16}:[A-Za-z0-9_-]{1,32}:\d{1,12}$")
LOG_BODY_RE = re.compile(r"^[a-f0-9]{64}$")
LOG_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class Unavailable(Exception):
    """503 with a message: a part mind needs is not deployed here."""


def _one(q, name, pattern=None):
    v = (q.get(name) or [None])[0]
    if v is not None and pattern is not None and not pattern.fullmatch(v):
        raise BadRequest(f"bad {name}")
    return v


def _log_ms(q, name, required=True):
    """A ms timestamp parameter between 1970 and 2286 (beyond that the month tables' dates overflow)."""
    v = _int(q, name, required)
    if v is not None and not 0 <= v < 10**13:
        raise BadRequest(f"{name} out of range")
    return v


def _log_range(q, now, max_ms=LOG_MAX_RANGE_MS):
    """from / to of a /log query (default: the last hour), at most max_ms (7 days; /log/gaps 48 h)."""
    hi = _log_ms(q, "to", required=False)
    hi = now if hi is None else hi
    lo = _log_ms(q, "from", required=False)
    lo = hi - LOG_DEFAULT_MS if lo is None else lo
    if hi <= lo or hi - lo > max_ms or lo < 0:
        span = f"{max_ms // 86400_000} days" if max_ms % 86400_000 == 0 else f"{max_ms // 3600_000} h"
        raise BadRequest(f"range must be positive and at most {span}")
    return lo, hi


def _str(v, n):
    return str(v)[:n] if v is not None else ""


def make_server(store, token, host="127.0.0.1", port=52740, ctx=None):
    ctx = ctx or {}
    db = store.db

    def current_settings():
        ss = ctx.get("settings_store")
        return ss.current() if ss else (settings_mod.load(None), 0)

    def speech_config():
        s, v = current_settings()
        roles = speak.load_roles(ctx.get('roles_dir') or '')
        return {'speech':s['speech'], 'memory':s['memory'], 'version':v,
                'environments':{k:p['environment'] if p['environment'] is not None else s.get('environment','')
                                for k,p in s['speech']['profiles'].items()},
                'roles':[{k:r.get(k) for k in ('id','button','name','dialect','delivery','rate','instruction')}
                         for r in [speech_settings.role_config(r,s) for r in roles.values()]],
                'history':ctx['settings_store'].history() if ctx.get('settings_store') else [],
                'worker':{k:v for k,v in (db['mind_memory_jobs'].find_one({'_id':'worker:live'}) or {}).items()
                          if k in ('state','cursor_window','last_tick','last_error','skipped_range','last_reconcile','deployed_at')}}

    def memory_row(mid):
        if not isinstance(mid,str) or not re.fullmatch(r'm[a-f0-9]{24}',mid):
            raise BadRequest('invalid memory id')
        mem = MemoryStore(db)
        row = mem._current(mid)
        if not row or row['scope_id'] != speech_settings.scope(current_settings()[0])['id']:
            raise NotFound('当前场景没有这条认识')
        return mem

    def names():
        voice, face = [], []
        for d in db["speech"].find({"speaker.band": "known"}, {"speaker.name": 1}).limit(5000):
            n = (d.get("speaker") or {}).get("name")
            if n and n not in voice:
                voice.append(n)
        for d in db["vision"].find({"name": {"$exists": True}}, {"name": 1}).limit(5000):
            if d.get("name") and d["name"] not in face:
                face.append(d["name"])
        return {"voice": voice[:50], "face": face[:50]}

    def stats(days):
        s, _ = current_settings()
        now = int(time.time() * 1000)
        start = now - days * 86400_000
        rows = {}
        for d in db["mind_runs"].find({"at": {"$gte": start}}, {"at": 1, "step": 1, "model": 1, "usage": 1, "cost_usd": 1, "error": 1, "lane": 1}):
            day = settings_mod.local_time(d["at"], s["tz"]).strftime("%Y-%m-%d")
            key = (day, d.get("step"), d.get("model"), d.get("lane"))
            r = rows.setdefault(key, {"day": day, "step": d.get("step"), "model": d.get("model"), "lane": d.get("lane"), "calls": 0,
                                      "failures": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0})
            u = d.get("usage") or {}
            r["calls"] += 1
            r["failures"] += 1 if d.get("error") else 0
            r["prompt_tokens"] += u.get("prompt_tokens", 0) or 0
            r["completion_tokens"] += u.get("completion_tokens", 0) or 0
            r["cost_usd"] = round(r["cost_usd"] + (d.get("cost_usd") or 0), 6)
        out = sorted(rows.values(), key=lambda r: (r["day"], r["step"] or "", r["model"] or ""), reverse=True)
        today = settings_mod.local_time(now, s["tz"]).strftime("%Y-%m-%d")
        return {"days": days, "today": today, "rows": out,
                "today_cost_usd": round(sum(r["cost_usd"] for r in out if r["day"] == today), 6),
                "today_calls": sum(r["calls"] for r in out if r["day"] == today)}

    SWITCHES = ("tingwu", "eye", "notes", "realtime")    # spec 2026-11-11 §3; realtime: 围观 voice session (2026-11-14)
    MOUTH_MODES = ("off", "clips", "tts")    # 嘴: emotion clips (default) or soul-tts, never both (user 2026-11-11)

    def mouth_mode():
        m = (db["switches"].find_one({"_id": "mouth"}) or {}).get("mode")
        return m if m in MOUTH_MODES else "clips"

    def switches():
        now = int(time.time() * 1000)
        out = {}
        for name in SWITCHES:
            sw = db["switches"].find_one({"_id": name}) or {}
            st = db["switches"].find_one({"_id": name + "_status"}) or {}
            out[name] = {"on": sw.get("on") is True, "at": sw.get("at")}
            out[name + "_status"] = {"running": st.get("running") or [], "available": st.get("available"), "at": st.get("at"),
                                     "stale": not isinstance(st.get("at"), int) or now - st["at"] > 90_000}
        out["mouth"] = {"mode": mouth_mode(), "at": (db["switches"].find_one({"_id": "mouth"}) or {}).get("at")}
        if ctx.get("mode_store"):
            last = db["mode_log"].find_one({"input": "billing", "device": None, "code": "ok"}, sort=[("at", -1)]) or {}
            out["billing"] = {"on": ctx["mode_store"].billing(), "at": last.get("at")}
        pc = ctx.get("pause")
        out["pause"] = pc.view(now) if pc else pause.view(pause.read(db), now)
        return out

    # 工具 (B1, 2026-11-18; docs/hello-tools.md): GET /tools = the function-calling list, POST /tool/call = one call
    registry = load_registry()
    ear_ask = ctx.get("ear_ask") or tool_impls.ear_asker(token)
    from . import llm as _llm
    from .proposals import Proposals
    from .daily import tools as daily_tools
    proposals = ctx.get("proposals") or Proposals(db)

    def write_switch(name, on):
        """A C tool's switch change (switch_propose, owner in person or approved)."""
        now_ = int(time.time() * 1000)
        if name == "mind":
            with runlog.switch("mind", runlog.value(store.enabled), on, "tool"):
                store.set_state(enabled=on, switched_at=now_)
            return
        if name not in SWITCHES:
            raise ValueError(name)
        with runlog.switch(name, runlog.read_switch(db, name), on, "tool"):
            db["switches"].update_one({"_id": name}, {"$set": {"on": on, "at": now_, "by": "tool"}}, upsert=True)

    tools = ctx.get("tools") or Tools(registry, tool_impls.mind_tools(
        switches=switches, store=store, mode_store=ctx.get("mode_store"), write_switch=write_switch,
        scope_fn=lambda: speech_settings.scope(current_settings()[0])["id"],
        research=ctx.get("research"),
        ledger=(lambda day: log_query.ledger(db, day)) if log_query is not None else None,
        settings_fn=lambda: current_settings()[0], registry=registry,
        flows=(lambda lo, hi, kinds, limit: log_query.flows(db, lo, hi, kinds=kinds, limit=limit)) if log_query is not None else None)
        | tool_impls.ear_tools(ear_ask)
        | tool_impls.live_tools(ear_search=lambda q: ear_ask("web_search", {"query": q}), llm_env=ctx.get("env"),
                                chat=_llm.chat, tz=current_settings()[0].get("tz") or "Asia/Shanghai")
        | (daily_tools(ctx["daily"]) if ctx.get("daily") else {}),
        proposals=proposals, describe=tool_impls.DESCRIBE)

    def note_owner(path, body, reply):
        """After a POST went through: tell the pause about an explicit owner input (never raises)."""
        pc = ctx.get("pause")
        if pc is None:
            return
        try:
            what, nine = None, False
            if path in OWNER_POSTS:
                if path == "/switches" and body.get("by") == "hush":
                    return
                what = "page" if path != "/signal" else "dualkey"
                nine = (path in ("/switches", "/mode") and body.get("name", "billing") == "billing" and
                        (body.get("on") is True or body.get("value") is True)) or (path == "/preset" and body.get("n") == 9)
            elif path == "/mode/input":
                if not isinstance(reply, dict) or reply.get("power_on") or reply.get("duplicate"):
                    return
                what = f"{body.get('source')}:digit"
                nine = (body.get("input") == "digit" and body.get("value") == 9) or \
                       (body.get("input") == "billing" and body.get("value") is True)
            elif path == "/mode/report":
                inp = body.get("input")
                if body.get("by") not in pause.OWNER_SOURCES or not isinstance(inp, str) or inp in ("boot", "report"):
                    return
                if isinstance(reply, dict) and (reply.get("duplicate") or (reply.get("result") or {}).get("code") == "stale"):
                    return
                what = f"{body.get('by')}:{inp}"
            if what:
                pc.note_owner(what[:32], nine=nine)
        except Exception as e:                   # noqa: BLE001 - the input itself was answered already
            print(f"mind pause: owner input not noted: {type(e).__name__}", flush=True)

    # ---- modes (step 1) ----
    def modes():
        ms = ctx.get("mode_store")
        if not ms:
            raise Conflict("modes are not configured here")
        return ms

    def need_talk():
        """说话类: only in the talk show (the mirrored mode). No mode store: as before."""
        ms = ctx.get("mode_store")
        if ms and ms.current()["mode"] != "talk":
            raise ModeConflict("只在脱口秀模式", "only_talk")

    def _is_int(v):
        return isinstance(v, int) and not isinstance(v, bool)

    def mode_input(body):
        """ear forwards hello's digit (0 / 9): device + boot + seq is the dedup key, so all three are required."""
        if not (isinstance(body.get("device"), str) and body["device"] and isinstance(body.get("boot"), str) and body["boot"]
                and _is_int(body.get("seq")) and _is_int(body.get("age_ms")) and body["age_ms"] >= 0):
            raise BadRequest("device, boot (text), seq and age_ms (integers) required")
        if body.get("source") not in DEVICE_SOURCES + ("boot",):      # boot: hello's power-on digit (billing.power_on)
            raise BadRequest(f"source must be one of {'/'.join(DEVICE_SOURCES + ('boot',))}")
        if body.get("input") not in MODE_INPUTS or body["input"] in ("boot", "timeout"):
            raise BadRequest("unknown input")
        if body["input"] not in MIND_INPUTS:
            raise ModeConflict(ON_HELLO, "on_hello")
        return modes().apply(body["input"], body.get("value"), source=body["source"], device=body["device"],
                             boot=body["boot"], dev_seq=body["seq"], age_ms=body["age_ms"], sid=None)

    def _read_jpeg(rel):
        """A frame file under recordings_root (realpath-checked) -> base64 text, or None."""
        root = ctx.get("recordings_root")
        if not root or not isinstance(rel, str) or not rel:
            return None
        base = os.path.realpath(root)
        full = os.path.realpath(os.path.join(base, rel))
        if not full.startswith(base + os.sep) or not os.path.isfile(full):
            return None
        with open(full, "rb") as f:
            return base64.b64encode(f.read()).decode()

    def looks(limit):
        """视觉爆发 (ear vision_burst.py) for the page: the newest looks (no photos: /looks/frame), the last bursts, and
        whether one is running now (its newest look under 15 s old and no end row yet)."""
        rows = list(db["vision_looks"].find({"kind": "look"}, {"_id": 0, "usage": 0}).sort("at", -1).limit(limit))
        bursts = list(db["vision_looks"].find({"kind": "burst"}, {"_id": 0}).sort("at", -1).limit(5))
        now = int(time.time() * 1000)
        running = None
        if rows and now - (rows[0].get("at") or 0) < 15_000 and \
                not any(b.get("burst_id") == rows[0].get("burst_id") for b in bursts):
            bid = rows[0].get("burst_id")
            mine = [r for r in rows if r.get("burst_id") == bid]
            running = {"burst_id": bid, "looks": len(mine), "since": min(r.get("at") or now for r in mine),
                       "last_at": rows[0].get("at")}
        keep = ("lid", "burst_id", "at", "seq", "snap_id", "text", "error", "latency_ms", "snap_ms", "bytes", "cost_usd")
        return {"running": running, "bursts": bursts,
                "looks": [{k: r.get(k) for k in keep} | {"has_frame": bool(r.get("path"))} for r in rows]}

    def look_frame(lid):
        if not lid or len(lid) > 80:
            raise BadRequest("lid required")
        doc = db["vision_looks"].find_one({"kind": "look", "lid": lid}, {"path": 1, "at": 1})
        if not doc:
            raise NotFound("no such look")
        b64 = _read_jpeg(doc.get("path"))
        return {"lid": lid, "at": doc.get("at"), "jpeg_b64": b64, "missing": b64 is None}

    def latest_frame():
        """Newest body-camera frame the ear stored (hello.frames + the file under recordings/)."""
        doc = db["frames"].find_one({}, sort=[("at", -1)])
        if not doc:
            raise NotFound("no frames yet")
        now = int(time.time() * 1000)
        tz = current_settings()[0]["tz"]
        day0 = int(settings_mod.local_time(now, tz).replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
        out = {k: doc.get(k) for k in ("at", "session", "seq", "diff", "bytes", "w", "h")}
        out["today"] = db["frames"].count_documents({"at": {"$gte": day0}})
        out["jpeg_b64"], out["missing"] = None, True
        root = ctx.get("recordings_root")
        if root and isinstance(doc.get("path"), str):
            base = os.path.realpath(root)
            full = os.path.realpath(os.path.join(base, doc["path"]))
            if full.startswith(base + os.sep) and os.path.isfile(full):
                with open(full, "rb") as f:
                    out["jpeg_b64"], out["missing"] = base64.b64encode(f.read()).decode(), False
        return out

    DB_POST = {'/db/person': 'people_cards', '/db/person/note': 'people_notes', '/db/memory': 'memory_items',
               '/db/knowledge': 'knowledge_items'}

    def lib_id(v):
        if not isinstance(v, str) or not re.fullmatch(ID_RE, v):
            raise BadRequest('invalid id')
        return v

    def lib_call(fn):
        try:
            return fn()
        except ValueError as e:
            raise BadRequest(str(e)) from None
        except KeyError as e:
            raise NotFound(str(e.args[0]) if e.args else 'not found') from None

    def lib_get(path, q):
        lib = Library(db)
        one = lambda k: (q.get(k) or [None])[0]
        if path == '/db/overview':
            return lib.overview(current_settings()[0])
        if path == '/db/people':
            return {'items': lib.people()}
        if path == '/db/person':
            return lib_call(lambda: lib.person(lib_id(one('id'))))
        if path == '/db/item':
            if one('c') not in COLLECTIONS:
                raise BadRequest('invalid collection')
            return lib_call(lambda: lib.item(one('c'), lib_id(one('id'))))
        offset, limit = _int(q, 'offset', False), _int(q, 'limit', False)
        offset, limit = 0 if offset is None else offset, 20 if limit is None else limit
        if path == '/db/memories':
            return lib_call(lambda: lib.memories(one('kind'), one('person'), one('topic'), one('q'),
                                                 _int(q, 'from', False), _int(q, 'to', False), offset, limit,
                                                 lane=one('lane') or 'live'))
        if path == '/db/changes':
            return lib_call(lambda: lib.changes(one('lane') or 'live', offset, limit))
        if path == '/db/sources':
            mid = one('id')
            if not isinstance(mid, str) or not re.fullmatch(r'e_[a-f0-9]{12}', mid):
                raise BadRequest('invalid id')
            return lib_call(lambda: lib.sources(mid))
        if path == '/db/extract':
            return memory_extract.status(db, store, current_settings()[0], int(time.time() * 1000))
        return lib_call(lambda: lib.knowledge(one('kind'), one('person'), one('q'), offset, limit))

    def lib_post(path, body):
        if set(body) - {'id', 'expected_version', 'fields', 'reason', 'restore_version'}:
            raise BadRequest('unexpected fields')
        c, now = DB_POST[path], int(time.time() * 1000)
        item_id = lib_id(body['id']) if body.get('id') is not None else None
        lib = Library(db)
        if 'restore_version' in body:
            if item_id is None or 'fields' in body:
                raise BadRequest('restore needs id and no fields')
            return lib_call(lambda: lib.restore(c, item_id, body.get('expected_version'), body['restore_version'], now))
        reason = body.get('reason', '')
        if not isinstance(reason, str):
            raise BadRequest('reason must be text')
        scope_id = speech_settings.scope(current_settings()[0])['id']
        return lib_call(lambda: lib.put(c, body.get('fields'), item_id=item_id, expected_version=body.get('expected_version'),
                                        scope_id=scope_id, now_ms=now, reason=reason[:200]))

    log_last, log_last_lock = {}, threading.Lock()

    def log_get(path, q):
        """日志 page: the run/audit log (hello_log_query), read only."""
        if log_query is None:
            raise Unavailable("hello_log_query is not deployed next to mind")
        now = int(time.time() * 1000)
        try:
            if path == '/log':
                lo, hi = _log_range(q, now)
                kind = _one(q, 'kind', LOG_KIND_RE)
                svc = _one(q, 'svc')
                if svc is not None and svc not in LOG_SVCS:
                    raise BadRequest("bad svc")
                cost = _one(q, 'cost')
                if cost not in (None, '0', '1'):
                    raise BadRequest("cost must be 0 or 1")
                limit = max(1, min(_int(q, 'limit', required=False) or 200, log_query.MAX_LIMIT))
                rows = log_query.flows(db, lo, hi, kinds=[kind] if kind and not kind.endswith('.') else None,
                                       kind_prefix=kind if kind and kind.endswith('.') else None, svc=svc,
                                       trace=_one(q, 'trace', LOG_TRACE_RE),
                                       has_cost=None if cost is None else cost == '1', limit=limit,
                                       before_id=_one(q, 'before', LOG_ID_RE))
                return {"from": lo, "to": hi, "rows": rows, "more": len(rows) >= limit}
            if path == '/log/body':
                at, bid = _log_ms(q, 'at'), _one(q, 'id', LOG_BODY_RE)
                if not bid:
                    raise BadRequest("id required")
                text = log_query.body(db, at, bid)
                if text is None:
                    raise NotFound("no such body")
                return {"text": text}
            if path == '/log/chain':
                trace = _one(q, 'trace', LOG_TRACE_RE)
                if not trace:
                    raise BadRequest("trace required")
                return {"trace": trace, "rows": log_query.chain(db, trace, _log_ms(q, 'at'))}
            if path == '/log/ledger':
                day = _one(q, 'day', LOG_DAY_RE)
                if day is None:
                    day = time.strftime("%Y-%m-%d", time.gmtime(now / 1000 + 8 * 3600))    # today, Beijing
                return {"day": day, **log_query.ledger(db, day)}
            if path == '/log/gaps':
                lo, hi = _log_range(q, now, LOG_GAPS_MAX_MS)
                return {"from": lo, "to": hi, "gaps": log_query.gaps(db, lo, hi, now_ms=now)}
            if path == '/log/last':             # cached: the answer keeps its own now, so ages are as of then
                with log_last_lock:
                    hit = log_last.get('value')
                    if hit is None or not 0 <= now - hit['now'] < LOG_LAST_CACHE_MS:
                        hit = log_last['value'] = {"now": now, "svcs": log_query.last_seen(db, LOG_SVCS, now)}
                    return hit
        except ValueError as e:                  # a bad day, a span over 60 months
            raise BadRequest(str(e)[:120]) from None
        raise NotFound("not found")

    def get(path, q):
        if path == '/log' or path.startswith('/log/'):
            return log_get(path, q)
        if path.startswith('/db/'):
            if path not in ('/db/overview', '/db/people', '/db/person', '/db/memories', '/db/knowledge', '/db/item',
                            '/db/changes', '/db/sources', '/db/extract'):
                raise NotFound('not found')
            return lib_get(path, q)
        if path == '/speech/settings':
            return speech_config()
        if path in ('/memories','/memory/history'):
            offset = _int(q,'offset',False)
            limit = _int(q,'limit',False)
            offset, limit = 0 if offset is None else offset, 20 if limit is None else limit
            try:
                if path == '/memories':
                    return MemoryStore(db).list(speech_settings.scope(current_settings()[0])['id'], offset, limit,
                                                (q.get('status') or [None])[0])
                mid = (q.get('id') or [''])[0]
                return memory_row(mid).history(mid,offset,limit)
            except ValueError as e:
                raise BadRequest(str(e)) from None
        if path == '/say/explain' and q.get('rid'):
            rid = (q.get('rid') or [''])[0]
            if not re.fullmatch(r'r[a-f0-9]{1,40}', rid):
                raise BadRequest('invalid round id')
            r = db['say_rounds'].find_one({'rid': rid}, {'_id': 0})
            if not r:
                raise NotFound('没有这一轮')
            return {**speak.compact_round(r), 'checks': [{k: c.get(k) for k in ('at', 'pick', 'ms', 'error')} for c in r.get('checks', [])],
                    'triggers': r.get('triggers', []), 'judge': {k: r['judge'].get(k) for k in ('order', 'best_key', 'weak', 'error')},
                    'context': {k: (r.get('context') or {}).get(k) for k in ('time', 'scene', 'evidence')}}
        if path == '/say/explain':
            uid = (q.get('uid') or [''])[0]
            if not re.fullmatch(r'u[a-zA-Z0-9._-]{1,80}',uid):
                raise BadRequest('invalid utterance id')
            row = db['utterances'].find_one({'uid':uid})
            if not row:
                raise NotFound('没有这句话')
            context = row.get('context') or {}
            return {**compact_utterance(row),'attention':row.get('attention'),
                    **{k:row.get(k) for k in ('settings_version','prompt','llm_ms')},
                    'context':{k:context.get(k) for k in ('scope','time','profile','scene','evidence','memories','warnings')},
                    'instruction':row.get('instruction')}
        if path == "/state":
            return {**store.state(), "enabled": store.enabled(), "lane": LANE, "now_ms": int(time.time() * 1000)}
        if path == "/timeline":
            return store.timeline(LANE, *_range(q))
        if path == "/minute":
            t, version = _int(q, "t"), _int(q, "version", required=False)
            doc = store.get(LANE, t, version) if version is not None else store.latest(LANE, t)
            if not doc:
                raise NotFound("no such minute")
            return {**doc, "versions": store.versions(LANE, t)}
        if path == "/now":
            s, _ = current_settings()
            scene = store.latest_scene_any(LANE)
            pkg = store.latest(LANE, scene["minute"]) if scene else None
            now = int(time.time() * 1000)
            segs = segments.update(None, store, s, now - 24 * 3600_000 if not scene else scene["minute"] - 12 * 3600_000, now,
                                   LANE, summarize_now=False)
            return {"minute": scene["minute"] if scene else None, "scene": scene, "strong": pkg["strong"] if pkg else None,
                    "conv": (pkg["package"].get("conv") if pkg else None),
                    "segment": segs[-1] if segs else None, "state": {**store.state(), "enabled": store.enabled()}}
        if path == "/segments":
            s, _ = current_settings()
            lo, hi = _range(q)
            return segments.update(None, store, s, lo, hi, LANE, summarize_now=False)
        if path == "/scene":
            t = _int(q, "t")
            lane = (q.get("lane") or [LANE])[0]
            if lane not in (LANE, "rerun"):
                raise BadRequest("lane must be live or rerun")
            versions = store.scene_versions(lane, t)
            if not versions:
                raise NotFound("no scene for this minute")
            runs = [store.get_run(v["run_id"]) for v in versions if v.get("run_id")]
            return {"versions": versions, "runs": [r for r in runs if r]}
        if path == "/settings":
            s, v = current_settings()
            ss = ctx.get("settings_store")
            return {"settings": s, "version": v, "history": ss.history() if ss else []}
        if path == "/names":
            return names()
        if path == "/prompts":
            return {"versions": prompts.OFFERED, "models": list(MODELS)}
        if path == "/place":
            return place.state(db)
        if path == "/labels":
            lo, hi = _range(q)
            return list(db["mind_labels"].find({"at": {"$gte": lo, "$lt": hi}}, {"_id": 0}).sort("at", 1))
        if path == "/switches":
            return switches()
        if path == "/tools":
            return {"tools": tools.export(include_unlisted=q.get("all", ["0"])[0] == "1"), "available": tools.available()}
        if path == "/proposals":
            state = (q.get("state") or ["pending"])[0]
            if state not in ("pending", "all", "approved", "rejected", "expired", "failed"):
                raise BadRequest("bad state")
            return {"items": proposals.list(state)}
        if path == "/mode":
            ms = modes()
            return {"snapshot": ms.current(), "robot": ms.robot(), "log": ms.log(10)}
        if path == "/mode/seq":
            return {"seq": modes().seq()}
        if path == "/frames/latest":
            return latest_frame()
        if path == "/eye/latest":
            doc = db["eye_minutes"].find_one({}, {"_id": 0}, sort=[("minute", -1), ("version", -1)])
            if not doc:
                raise NotFound("no eye result yet")
            return doc
        if path == "/roles":
            return [{k: r.get(k) for k in ("id", "button", "name", "voice")} for r in speak.load_roles(ctx.get("roles_dir") or "").values()]
        if path == "/voices":
            tts = ctx.get("tts")
            if not tts:
                raise Conflict("soul-tts not configured")
            return speak.voice_list(tts)
        if path == "/utterances":
            lo, hi = _range(q)
            return list(reversed([compact_utterance(r) for r in db['utterances'].find(
                {'at':{'$gte':lo,'$lt':hi}}, {**{k:1 for k in UTTERANCE_FIELDS},'_id':0}).sort('at',-1).limit(200)]))
        if path == "/notes":
            lo, hi = _range(q)
            return list(db["conversations"].find({"start_ms": {"$gte": lo, "$lt": hi}}, {"_id": 0}).sort("start_ms", 1).limit(500))
        if path == "/eye":
            lo, hi = _range(q)
            return list(db["eye_minutes"].find({"minute": {"$gte": lo, "$lt": hi}}, {"_id": 0}).sort("minute", 1).limit(1440))
        if path == "/stats":
            days = _int(q, "days", required=False) or 7
            return stats(max(1, min(days, 3650)))
        if path == '/say/rounds':
            lo, hi = _range(q)
            scope_id = speech_settings.scope(current_settings()[0])['id']
            rows = list(db['say_rounds'].find({'scope_id': scope_id, 'at': {'$gte': lo, '$lt': hi}}, {'_id': 0}).sort('at', -1).limit(50))
            return [{**speak.compact_round(r), 'triggered': bool(r.get('plays') or r.get('triggers')),
                     'marks': owner_signals.round_marks(db, r['rid'])} for r in reversed(rows)]
        if path == '/scenes':
            lo, hi = _range(q)
            out = []
            for d in store.scenes_range(LANE, lo, hi)[-60:]:
                sc = d.get('scene') or {}
                said = lambda dim: [e.get('text') for e in sc.get('entries') or [] if e.get('dimension') == dim and e.get('text')]
                # Hearing too (owner 2026-11-14): the topic and sound_scene entries, not only what was seen.
                out.append({'minute': d['minute'], 'source': d.get('source'), 'version': d.get('version'),
                            'text': (sc.get('people_doing') or {}).get('text') or '', 'summary': sc.get('summary') or '',
                            'topic': said('topic')[:3], 'sound': '；'.join(said('sound_scene')[:2])})
            return out
        if path == '/signals':
            lo, hi = _range(q)
            return owner_signals.recent(db, lo, hi)
        if path == '/realtime/context':
            rc = ctx.get('realtime_ctx')
            if rc is None:
                from .realtime import RealtimeContext
                rc = ctx['realtime_ctx'] = RealtimeContext(db, ctx.get('chat'), lambda: current_settings()[0])
            return rc.block()
        if path == '/chat/state':
            chat = ctx.get('chat')
            if not chat:
                raise Conflict('chat is not configured here')
            return chat.state()
        if path == '/looks':
            return looks(max(1, min(_int(q, 'limit', required=False) or 40, 200)))
        if path == '/looks/frame':
            return look_frame((q.get('lid') or [''])[0])
        if path == '/chat/turns':
            chat = ctx.get('chat')
            if not chat:
                raise Conflict('chat is not configured here')
            return chat.turns(max(1, min(_int(q, 'limit', required=False) or 5, 20)))
        if path == '/say/stats':
            days = max(1, min(_int(q, 'days', required=False) or 7, 365))
            now = int(time.time() * 1000)
            scope_id = speech_settings.scope(current_settings()[0])['id']
            return {**owner_signals.stats(db, scope_id, now - days * 86400_000, now + 60_000), 'days': days,
                    'prefetch': {'on': bool(ctx.get('say_worker') and ctx['say_worker'].enabled()),
                                 'last': ctx['say_worker'].last if ctx.get('say_worker') else None},
                    'auto': ctx['say_auto'].status() if ctx.get('say_auto') else {'on': False, 'next_at': None, 'last': None}}
        raise NotFound("not found")

    def post(path, body):
        now = int(time.time() * 1000)
        if path == "/tool/call":                          # B1: one tool call -> the envelope (errors inside it)
            trace = body.get("trace")
            trace = trace if isinstance(trace, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,64}", trace) else None
            return tools.call(_str(body.get("name"), 60), body.get("args"), trace=trace, caller=_str(body.get("caller"), 32) or None,
                              owner=body.get("owner") is True)
        if path == "/proposal/decide":                    # L3: the owner's 同意 / 不同意 on the 待确认 list
            if not isinstance(body.get("id"), str) or not isinstance(body.get("approve"), bool):
                raise BadRequest("id and approve (true/false) required")
            try:
                doc = proposals.decide(body["id"], body["approve"],
                                       lambda name, args: tools.call(name, args, caller="owner:page", owner=True))
            except KeyError as e:
                raise NotFound(str(e.args[0])) from None
            except ValueError as e:
                raise Conflict(str(e)) from None
            return {"proposal": doc}
        if path in DB_POST:
            return lib_post(path, body)
        if path == '/db/memory/review':
            if set(body) - {'id', 'expected_version', 'action', 'into', 'reason'}:
                raise BadRequest('unexpected fields')
            if not isinstance(body.get('reason', ''), str):
                raise BadRequest('reason must be text')
            mid = body.get('id')
            if not isinstance(mid, str) or not re.fullmatch(r'e_[a-f0-9]{12}', mid):
                raise BadRequest('invalid id')
            return lib_call(lambda: Library(db).review(mid, body.get('expected_version'), body.get('action'), now_ms=now,
                                                       into=body.get('into'), reason=body.get('reason', '')))
        if path == '/db/extract/switch':
            try:
                on = memory_extract.set_switch(db, body.get('on'), now)
            except ValueError as e:
                raise BadRequest(str(e)) from None
            return memory_extract.status(db, store, current_settings()[0], now) | {'on': on}
        if path == '/db/extract/run':
            if set(body) - {'day'}:
                raise BadRequest('unexpected fields')
            try:
                req = memory_extract.request_run(db, current_settings()[0], now, body.get('day'))
            except ValueError as e:
                raise BadRequest(str(e)) from None
            return {**memory_extract.status(db, store, current_settings()[0], now), 'requested': req}
        if path == '/speech/settings':
            ss = ctx.get('settings_store')
            if not ss or type(body.get('expected_version')) is not int or set(body)-{'speech','memory','expected_version','note'}:
                raise BadRequest('speech, memory and expected_version required')
            s, _ = current_settings()
            s = copy.deepcopy(s)
            s.update(speech=body.get('speech'),memory=body.get('memory'))
            errors = speech_settings.validate(s['speech'],s['memory'])
            if errors:
                raise BadRequest('; '.join(errors))
            office = s['speech']['profiles']['office']
            if office['environment'] is not None:
                s['environment'], office['environment'] = office['environment'], None
            try:
                ss.save(s,_str(body.get('note'),200) or '场景与说话设置',expected_version=body['expected_version'])
            except ValueError as e:
                raise BadRequest(str(e)) from None
            speak.cancel_pending()
            return speech_config()
        if path == '/memory/edit':
            mem = memory_row(body.get('id'))
            try:
                return mem.edit(body['id'],body.get('expected_version'),body.get('action'),
                                body.get('statement'),body.get('restore_version'),now)
            except ValueError as e:
                raise BadRequest(str(e)) from None
            except KeyError as e:
                raise NotFound(str(e)) from None
        if path == '/role/try':
            tts = ctx.get('tts')
            roles = speak.load_roles(ctx.get('roles_dir') or '')
            if not isinstance(body.get('role'),str) or body['role'] not in roles:
                raise BadRequest('unknown role')
            if not tts or mouth_mode() != 'tts':
                raise Conflict('嘴没切到 TTS 模式')
            s, _ = current_settings()
            return compact_utterance(speak.try_role(store,tts,roles[body['role']],s,now))
        if path == "/settings":
            ss = ctx.get("settings_store")
            if not ss:
                raise BadRequest("settings are not configurable here")
            try:
                version = ss.save(body.get("settings"), _str(body.get("note"), 200),expected_version=body.get('expected_version'))
                speak.cancel_pending()
                return {'version':version}
            except ValueError as e:
                raise BadRequest(str(e)) from None
        if path == "/say":
            need_talk()
            tts = ctx.get("tts")
            if not tts or not ctx.get("env"):
                raise Conflict("soul-tts or model access not configured")
            roles = speak.load_roles(ctx.get("roles_dir") or "")
            if body.get("role") not in roles:
                raise BadRequest(f"role must be one of {', '.join(sorted(roles))}")
            if mouth_mode() != "tts":
                raise Conflict("嘴没切到 TTS 模式")
            s, version = current_settings()
            try:
                rec = speak.say(ctx["env"], store, s, roles, body["role"], tts, now,
                                is_current=lambda: current_settings()[1] == version and mouth_mode() == 'tts',settings_version=version)
            except speak.NotConnected as e:
                raise Conflict(str(e)) from None
            return compact_utterance(rec)
        if path == '/say/round':
            need_talk()
            tts, worker = ctx.get('tts'), ctx.get('say_worker')
            if not tts or not ctx.get('env') or not worker:
                raise Conflict('soul-tts or model access not configured')
            if mouth_mode() != 'tts':
                raise Conflict('嘴没切到 TTS 模式')
            s, version = current_settings()
            roles = speak.load_roles(ctx.get('roles_dir') or '')
            if ctx.get('round_gate'):
                ctx['round_gate'].note(now)          # a robot forward right after this one answers too_soon
            try:
                return speak.say_round(ctx['env'], store, s, roles, tts, now, worker=worker, force_new=bool(body.get('force_new')),
                                       trigger={'source': 'page'}, settings_version=version,
                                       is_current=lambda: current_settings()[1] == version and mouth_mode() == 'tts')
            except speak.NotConnected as e:
                raise Conflict(str(e)) from None
            except RuntimeError as e:
                raise Conflict(str(e)) from None
        if path == '/say/play':
            need_talk()
            tts = ctx.get('tts')
            if not tts:
                raise Conflict('soul-tts not configured')
            if mouth_mode() != 'tts':
                raise Conflict('嘴没切到 TTS 模式')
            if not isinstance(body.get('rid'), str) or not isinstance(body.get('line'), str):
                raise BadRequest('rid and line required')
            if body.get('dialect') is not None and body['dialect'] not in speech_settings.DIALECTS:
                raise BadRequest('unsupported dialect')
            if body.get('voice') is not None and body['voice'] not in {v['id'] for v in speak.voice_list(tts)}:
                raise BadRequest('unknown voice')
            s, _ = current_settings()
            try:
                return speak.play_line(store, s, speak.load_roles(ctx.get('roles_dir') or ''), tts, body['rid'], body['line'], now,
                                       voice=body.get('voice'), dialect=body.get('dialect'))
            except (KeyError, StopIteration):
                raise NotFound('没有这一句') from None
            except speak.NotConnected as e:
                raise Conflict(str(e)) from None
        if path == '/signal':
            try:
                return owner_signals.put_signal(db, body, now)      # DualKey: ratings only (step 1b), nothing else
            except ValueError as e:
                raise BadRequest(str(e)) from None
        if path == '/say/prefetch':
            worker = ctx.get('say_worker')
            if not worker:
                raise Conflict('pre-writing is not configured here')
            if not isinstance(body.get('enabled'), bool):
                raise BadRequest('enabled must be true or false')
            if body['enabled']:
                need_talk()
            with runlog.hello_log.scope(by='page'):           # the run log's switch.set by
                worker.set_enabled(body['enabled'], now)
            return {'enabled': worker.enabled()}
        if path == '/say/auto':
            auto = ctx.get('say_auto')
            if not auto:
                raise Conflict('连续说 is not configured here')
            if not isinstance(body.get('enabled'), bool):
                raise BadRequest('enabled must be true or false')
            if body['enabled']:
                need_talk()
            with runlog.hello_log.scope(by='page'):
                auto.set_enabled(body['enabled'], now)             # on also turns 提前写 on
            return {'enabled': auto.enabled(), 'prefetch': auto.prefetch.enabled()}
        if path == "/voice/try":
            tts = ctx.get("tts")
            if not tts:
                raise Conflict("soul-tts not configured")
            if not isinstance(body.get("voice"), str) or body["voice"] not in {v["id"] for v in speak.voice_list(tts)}:
                raise BadRequest("voice must be one of the soul-tts voices")
            if mouth_mode() != "tts":
                raise Conflict("嘴没切到 TTS 模式")
            try:
                rec = speak.try_voice(store, tts, body["voice"], now)
            except speak.NotConnected as e:
                raise Conflict(str(e)) from None
            return {k: v for k, v in rec.items() if k != "_id"}
        if path == "/say/stop":
            tts = ctx.get("tts")
            return {'cancelled':speak.cancel_pending(tts)}
        if path == "/switches" and body.get("name") == "mouth":
            if body.get("mode") not in MOUTH_MODES:
                raise BadRequest(f"mouth mode must be one of {'/'.join(MOUTH_MODES)}")
            was = mouth_mode()
            with runlog.switch("mouth", was, body["mode"], "page"):
                db["switches"].update_one({"_id": "mouth"}, {"$set": {"mode": body["mode"], "at": now, "by": "page"}}, upsert=True)
            if was == "tts" and body["mode"] != "tts" and ctx.get("tts"):
                try:
                    speak.cancel_pending(ctx['tts'])
                except Exception:                        # noqa: BLE001 - the switch itself is saved
                    pass
            return switches()
        if path == "/switches" and body.get("name") == "billing":
            ms = modes()
            if not isinstance(body.get("on"), bool):
                raise BadRequest("on (true/false) required")
            r = ms.apply("billing", body["on"], source="page", now_ms=now)      # = preset 9 / preset 0
            return {**switches(), "result": r["result"], "robot": r.get("robot", {}),
                    **({"failed": r["failed"]} if r.get("failed") else {})}
        if path == "/switches":
            if body.get("name") not in SWITCHES or not isinstance(body.get("on"), bool):
                raise BadRequest(f"name ({'/'.join(SWITCHES)}) and on (true/false) required")
            with runlog.switch(body["name"], runlog.read_switch(db, body["name"]), body["on"], "page"):
                db["switches"].update_one({"_id": body["name"]}, {"$set": {"on": body["on"], "at": now, "by": "page"}}, upsert=True)
            return switches()
        if path == "/switch":
            if not isinstance(body.get("enabled"), bool):
                raise BadRequest("enabled (true/false) required")
            with runlog.switch("mind", runlog.value(store.enabled), body["enabled"], "page"):
                store.set_state(enabled=body["enabled"], switched_at=now)
            return {"enabled": store.enabled(), "at": now}
        if path == "/mode":
            if body.get("input") not in MODE_INPUTS or body["input"] in ("boot", "timeout"):
                raise BadRequest("unknown input")
            if body["input"] != "billing":          # the page switches modes on hello (/api/control target mode)
                raise ModeConflict(ON_HELLO, "on_hello")
            return modes().apply("billing", body.get("value"), source="page", now_ms=now)
        if path == "/mode/input":
            return mode_input(body)
        if path == "/chat/stage":
            chat = ctx.get("chat")
            if not chat:
                raise Conflict("chat is not configured here")
            try:
                chat.note_stage(body.get("stage"), body.get("at_ms"))
            except ValueError as e:
                raise BadRequest(str(e)) from None
            return {"ok": True}
        if path == "/chat/heard":
            chat = ctx.get("chat")
            if not chat:
                raise Conflict("chat is not configured here")
            try:
                return chat.heard(body)
            except ValueError as e:
                raise BadRequest(str(e)) from None
        if path == "/mode/report":
            ms = modes()
            if not (_is_int(body.get("age_ms", 0)) and body.get("age_ms", 0) >= 0):
                raise BadRequest("age_ms must be a non-negative integer")
            rep = {k: body.get(k) for k in ("seq", "mode", "dir", "host", "return_to", "by", "input")}
            rep["effects"] = body.get("effects", [])
            try:
                return ms.report(rep, boot=body.get("boot"), conn=body.get("conn"), age_ms=body.get("age_ms", 0), now_ms=now)
            except ValueError as e:
                raise BadRequest(str(e)) from None
        if path == "/mode/robot":
            if not (isinstance(body.get("boot"), str) and body["boot"] and _is_int(body.get("conn"))
                    and body.get("event") == "closed"):
                raise BadRequest("boot (text), conn (integer) and event closed required")
            return {"snapshot": modes().robot_closed(body["boot"], body["conn"], now_ms=now)}
        if path == "/preset":
            n, source = body.get("n"), body.get("source", "page")
            if not _is_int(n) or not 0 <= n <= 9:
                raise BadRequest("n must be 0-9")
            if source not in DEVICE_SOURCES + ("page",):
                raise BadRequest("unknown source")
            return modes().apply("digit", n, source=source, now_ms=now)
        if path == "/mark":
            if body.get("kind") not in MARKS:
                raise BadRequest(f"kind must be one of {', '.join(MARKS)}")
            db["mind_labels"].insert_one({"lane": LANE, "kind": "mark", "mark": body["kind"], "at": now, "note": _str(body.get("note"), 300)})
            return {"ok": True, "at": now}
        if path == "/place":
            # where hello is now (mind/place.py): label text (1-40) or null = unknown; source page (default) / robot
            # (the ear forwards hello's info-page pick: must be one of the common choices). voice is /place/told only.
            if set(body) - {"label", "source"} or "label" not in body or body.get("source", "page") not in ("page", "robot"):
                raise BadRequest("label (1–40 characters, or null for unknown) and source page / robot")
            try:
                st = place.set_current(db, body["label"], body.get("source", "page"), now, lane=LANE)
            except ValueError as e:
                raise BadRequest(str(e)) from None
            return {"ok": True, "at": now, **st}
        if path == "/place/choices":
            try:
                return place.edit_choices(db, body, now)
            except ValueError as e:
                raise BadRequest(str(e)) from None
        if path == "/place/told":
            # the ear's crowd transcripts (and tests): a sentence that may say where we are (local rule, no model)
            if set(body) - {"text", "source"} or not isinstance(body.get("text"), str) or len(body["text"]) > 512 \
                    or body.get("source") not in ("crowd", "chat"):
                raise BadRequest("text (up to 512 characters) and source crowd / chat required")
            return place.told(db, body["text"], body["source"], now)
        if path == "/label":
            minute = body.get("minute")
            if not isinstance(minute, int) or body.get("verdict") not in VERDICTS or body.get("field") not in FIELDS:
                raise BadRequest(f"minute (int), verdict ({'/'.join(VERDICTS)}) and field ({'/'.join(FIELDS)}) required")
            scene = store.latest_scene(LANE, minute)
            if not scene:
                raise NotFound("no live scene for this minute")
            db["mind_labels"].insert_one({"lane": LANE, "kind": "label", "minute": minute, "scene_version": scene["version"],
                                          "verdict": body["verdict"], "field": body["field"], "correct": _str(body.get("correct"), 300),
                                          "note": _str(body.get("note"), 300), "at": now})
            return {"ok": True}
        if path == "/rerun":
            minute, prompt, model = body.get("minute"), body.get("prompt"), body.get("model")
            if not isinstance(minute, int) or prompt not in prompts.OFFERED or model not in MODELS:
                raise BadRequest("minute (int), prompt (known version) and model (known model) required")
            if not ctx.get("env"):
                raise BadRequest("no model access configured")
            if not store.enabled():
                raise Conflict("mind is switched off: no model calls")
            doc = store.latest(LANE, minute)
            if not doc:
                raise NotFound("no package for this minute")
            s, v = current_settings()
            live = store.latest_scene(LANE, minute)
            ss = ctx.get("settings_store")
            if live and ss and live.get("settings_version") is not None and ss.get(live["settings_version"]):
                v = live["settings_version"]          # same inputs as the live run: only prompt / model differ
                s = ss.get(v)
            segs = segments.update(None, store, s, minute - 12 * 3600_000, minute, LANE, summarize_now=False)
            titled = [{"start": g["start"], "end": g["end"], "cls": g["cls"], "title": (g.get("summary") or {}).get("title")}
                      for g in segs if g["cls"] != "gap" and g["start"] < minute]
            prev = store.latest(LANE, minute - 60_000)
            return fusion.run_minute(ctx["env"], store, s, v, minute, doc["package"], doc["strong"],
                                     {"prev": prev["package"] if prev else None}, lane="rerun",
                                     force_prompt=prompt, force_model=model, now_ms=now, segments=titled,
                                     package_version=doc["version"])
        raise NotFound("not found")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, payload):
            body = json.dumps(payload, ensure_ascii=False, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self):
            supplied = self.headers.get("Authorization", "")
            supplied = supplied[7:] if supplied.startswith("Bearer ") else ""
            return bool(token) and hmac.compare_digest(supplied.encode(), token.encode())

        def _run(self, fn):
            try:
                self._send(200, fn())
            except BadRequest as e:
                self._send(400, {"error": str(e)})
            except NotFound as e:
                self._send(404, {"error": str(e)})
            except Unavailable as e:
                self._send(503, {"error": str(e)})
            except NoSituation as e:
                self._send(409, {"error": str(e), "code": "no_situation"})
            except ModeConflict as e:
                self._send(409, {"error": str(e), "code": e.code})
            except (Conflict, SettingsConflict, MemoryConflict, LibraryConflict, speak.NotConnected, BudgetExceeded) as e:
                self._send(409, {"error": str(e)})
            except Exception as e:               # noqa: BLE001 - database trouble shows as 503
                self._send(503, {"error": f"{type(e).__name__}"})

        def do_GET(self):
            if not self._authorized():
                self._send(401, {"error": "bad token"})
                return
            url = urlsplit(self.path)
            self._run(lambda: get(url.path, parse_qs(url.query)))

        def do_POST(self):
            if not self._authorized():
                self._send(401, {"error": "bad token"})
                return
            url = urlsplit(self.path)
            if url.path not in ("/settings", "/mark", "/label", "/rerun", "/place", "/place/choices", "/place/told", "/switch", "/switches", "/say", "/say/stop", "/voice/try", '/speech/settings', '/memory/edit', '/role/try',
                                '/say/round', '/say/play', '/signal', '/say/prefetch', '/say/auto', '/db/person', '/db/person/note', '/db/memory', '/db/knowledge',
                                '/db/memory/review', '/db/extract/switch', '/db/extract/run',
                                '/mode', '/mode/input', '/mode/report', '/mode/robot', '/preset', '/chat/heard', '/chat/stage',
                                '/tool/call', '/proposal/decide'):
                if url.path in ("/state", "/timeline", "/minute", "/now", "/segments", "/scene", "/names", "/prompts", "/labels", "/stats", "/mode/seq"):
                    self._send(405, {"error": "read only"})
                else:
                    self._send(404, {"error": "not found"})
                return
            if not (self.headers.get("Content-Type", "").split(";")[0].strip() == "application/json"):
                self._send(415, {"error": "application/json only"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > (MAX_SETTINGS_BODY if url.path in ('/settings','/speech/settings') else MAX_BODY):
                self._send(413, {"error": "body too large"})
                return
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self._send(400, {"error": "bad json"})
                return
            if not isinstance(body, dict):
                self._send(400, {"error": "body must be an object"})
                return
            def run():
                reply = post(url.path, body)
                note_owner(url.path, body, reply)
                return reply
            self._run(run)

        def do_PUT(self):
            self._send(405, {"error": "use POST"})

        do_DELETE = do_PATCH = do_PUT

    return ThreadingHTTPServer((host, port), Handler)


def serve(store, token, host="127.0.0.1", port=52740, ctx=None):
    make_server(store, token, host, port, ctx).serve_forever()
