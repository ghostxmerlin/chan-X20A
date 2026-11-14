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
import uuid

from . import llm, notes, settings as settings_mod
from . import attention, speech_context, scene_speech, speech_settings, say_moment, say_prompts
from . import say_round as rounds
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
_SAY_LOCK = threading.Lock()
_ENQUEUE_LOCK = threading.RLock()
_EPOCH = 0


class NotConnected(Exception):
    pass


def cancel_pending(tts=None):
    global _EPOCH
    with _ENQUEUE_LOCK:
        _EPOCH += 1
        return tts.stop() if tts else []


def _check_pending(epoch, is_current):
    if epoch != _EPOCH or (is_current is not None and not is_current()):
        raise NotConnected('这句已取消或设置已改变，请重新点一次')


def load_roles(directory):
    roles = {}
    for name in sorted(os.listdir(directory)) if os.path.isdir(directory) else []:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as f:
                r = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(r, dict) and all(r.get(k) for k in REQUIRED) and isinstance(r["rules"], list):
            roles[r["id"]] = r
    return roles


def context(db, store, s, now_ms):
    s = speech_settings.effective(s)
    scope = speech_settings.scope(s)
    scene = store.latest_scene_any(LANE)
    sc = (scene or {}).get("scene") or {}
    fresh_scene = scene and scene['minute'] >= scope['since'] and now_ms - scene["minute"] <= FRESH_MS + 60_000
    eye = db["eye_minutes"].find_one({"source": "model",'minute':{'$gte':scope['since']}}, {"_id": 0}, sort=[("minute", -1)])
    conv = db["conversations"].find_one({'start_ms':{'$gte':scope['since']}}, {"_id": 0}, sort=[("start_ms", -1)])
    conv_ok = conv and (conv.get("notes") or conv.get("title")) and \
        (conv.get("end_ms") is None or now_ms - conv["end_ms"] <= FRESH_MS)
    heard = notes.new_sentences(db, Records(db), max(scope['since'],now_ms-2*60_000), now_ms, set())[-10:]
    mine = [u['text'] for u in db['utterances'].find({'at':{'$gte':scope['since']}}, {'_id':0}).sort('at',-1).limit(100)
            if u.get('scope_id','office')==scope['id'] and u.get('role')!='try'][:5]
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
    timings = st.get("timings_s") or {}
    fields = {"state": st.get("state"), "tts_cost_cny": cost, "tts_tokens": [tin, tout],
              "played": st.get("state") == "done" or timings.get("first_accepted") is not None, "timings_s": st.get("timings_s")}
    submit = (db["utterances"].find_one({"uid": uid}, {"tts_submit_at": 1}) or {}).get("tts_submit_at")
    # Wall-clock play window (spec 11): remote ratings are matched to the line playing then. soul-tts's own
    # created / speaking_until are wall clock; first_sound exists only for the stream player (final review I7).
    base = int(st["created"] * 1000) if isinstance(st.get("created"), (int, float)) else submit
    if base:
        start = next((timings[k] for k in ("first_sound", "first_accepted") if isinstance(timings.get(k), (int, float))), None)
        if start is not None:
            fields["play_start_at"] = base + int(round(start * 1000))
        if isinstance(st.get("speaking_until"), (int, float)):
            fields["play_end_at"] = int(round(st["speaking_until"] * 1000))
        elif isinstance(timings.get("done"), (int, float)):
            fields["play_end_at"] = base + int(round(timings["done"] * 1000))
    db["utterances"].update_one({"uid": uid}, {"$set": fields})


def _say_v1(env, store, s, roles, role_id, tts, now_ms, wait=False, is_current=None,settings_version=None):
    epoch = _EPOCH
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
    with _ENQUEUE_LOCK:
        _check_pending(epoch,is_current)
        tts_id = tts.say(text, voice=voice, interrupt=True, **opts)
    uid = f"u{now_ms}"
    rec = {"uid": uid, "role": role_id, "text": text, "voice": voice, "tts_id": tts_id, "at": now_ms, "state": "sent",
           "run_id": run_id, "llm_ms": ms, 'settings_version':settings_version,'prompt':PROMPT,
           'scope_id':speech_settings.scope(s)['id'],"context": {k: ctx[k] for k in ("doing", "eye", "conversation")}}
    db["utterances"].insert_one(dict(rec))
    if wait:
        _finish(db, tts, uid, tts_id)
    else:
        threading.Thread(target=_finish, args=(db, tts, uid, tts_id), daemon=True).start()
    return rec


def say(env, store, s, roles, role_id, tts, now_ms, wait=False, is_current=None,settings_version=None):
    started = time.monotonic()
    epoch = _EPOCH
    role = speech_settings.role_config(roles[role_id], s)
    if s['speech']['pipeline'] == 'v1':
        return _say_v1(env, store, s, {**roles, role_id: role}, role_id, tts, now_ms, wait=wait,is_current=is_current,settings_version=settings_version)
    if not ((tts.health().get('mouth') or {}).get('device')):
        raise NotConnected('hello 的嘴没连上')
    if not _SAY_LOCK.acquire(blocking=False):
        raise NotConnected('正在写上一句，请稍等或先停止')
    try:
        _check_pending(epoch,is_current)
        ctx = speech_context.build(store.db, store, s, now_ms)
        context_ms = int((time.monotonic()-started)*1000)
        output = scene_speech.generate(env, store, s, role, ctx, now_ms, chat=CHAT)
        _check_pending(epoch,is_current)
        finish_ms = now_ms+int((time.monotonic()-started)*1000)
        scene = ctx.get('scene')
        if not (scene and scene['at']>=finish_ms-360_000) and not any(e['quality']=='usable' and e['at']>=finish_ms-120_000 for e in ctx['evidence']):
            raise attention.NoSituation('生成耗时过长，刚才的情况已过期，请重新点一次')
        uid = 'u'+uuid.uuid4().hex
        rec = {'uid':uid, 'role':role_id, 'text':output['text'], 'voice':role['voice'], 'at':now_ms,
               'state':'ready', 'run_id':output['run_id'], 'run_ids':[output['run_id']], 'scope_id':ctx['scope']['id'],
               'pipeline':'scene-v1', 'instruction':role['instruction'], 'llm_ms':finish_ms-now_ms,
               'context_ms':context_ms,'settings_version':settings_version,'prompt':scene_speech.PROMPT,'context':ctx,
               'focus':output.get('point'),'joke_type':output.get('device')}
        store.db['utterances'].insert_one(dict(rec))
        try:
            with _ENQUEUE_LOCK:
                _check_pending(epoch,is_current)
                tts_id = tts.say(output['text'],voice=role['voice'],interrupt=True,
                                 **{k:role[k] for k in ('instruction','rate') if role.get(k) is not None})
        except Exception as e:
            store.db['utterances'].update_one({'uid':uid},{'$set':{'state':'error','error':type(e).__name__}})
            raise
        rec.update(tts_id=tts_id, state='sent')
        store.db['utterances'].update_one({'uid':uid},{'$set':{'tts_id':tts_id,'state':'sent'}})
        from . import selfvoice
        selfvoice.refresh_spoken(store.db, now_ms)
        rec['server_ms']=int((time.monotonic()-started)*1000)
        store.db['utterances'].update_one({'uid':uid},{'$set':{'server_ms':rec['server_ms']}})
        if wait:
            _finish(store.db,tts,uid,tts_id)
        else:
            threading.Thread(target=_finish,args=(store.db,tts,uid,tts_id),daemon=True).start()
        return rec
    finally:
        _SAY_LOCK.release()


def compact_round(r):
    keep = ('key', 'text', 'gloss', 'point', 'device', 'dialect', 'score', 'dropped')
    return {'rid': r['rid'], 'moment_id': r['moment_id'], 'at': r['at'], 'written_at': r['written_at'],
            'lines': [{**{k: x.get(k) for k in keep}, 'voice_label': say_prompts.VOICE_LABELS.get(x.get('voice'), '其他嗓子')}
                      for x in r['lines']],
            'order': r['judge']['order'], 'weak': r['judge']['weak'], 'timings': r.get('timings'),
            'plays': [{k: p.get(k) for k in ('key', 'at', 'why', 'uid')} for p in r.get('plays', [])],
            'thin_moment': r.get('thin_moment', False), 'cost_usd': r.get('cost_usd'),
            'source': (r.get('trigger') or {}).get('source')}


def _speak_line(store, tts, r, key, now_ms, why, casting=None, epoch=None, is_current=None):
    """Hand one line of round r to soul-tts and record the play (utterances + say_rounds.plays)."""
    line = rounds.line(r, key)
    cast = casting or {k: line.get(k) for k in ('voice', 'dialect', 'instruction', 'rate')}
    uid = 'u' + uuid.uuid4().hex
    submit = int(time.time() * 1000)
    rec = {'uid': uid, 'role': key, 'text': line['text'], 'voice': cast['voice'], 'at': now_ms, 'state': 'ready',
           'scope_id': r['scope_id'], 'pipeline': 'round', 'rid': r['rid'], 'line': key, 'moment_id': r['moment_id'],
           'casting': cast, 'tts_submit_at': submit, 'why': why}
    store.db['utterances'].insert_one(dict(rec))
    try:
        with _ENQUEUE_LOCK:
            if epoch is not None:
                _check_pending(epoch, is_current)           # a stop that landed after the last check still wins
            tts_id = tts.say(line['text'], voice=cast['voice'], interrupt=True,
                             **{k: cast[k] for k in ('instruction', 'rate') if cast.get(k) is not None})
    except NotConnected:
        store.db['utterances'].update_one({'uid': uid}, {'$set': {'state': 'cancelled'}})
        raise
    except Exception as e:
        store.db['utterances'].update_one({'uid': uid}, {'$set': {'state': 'error', 'error': type(e).__name__}})
        raise
    store.db['utterances'].update_one({'uid': uid}, {'$set': {'tts_id': tts_id, 'state': 'sent'}})
    r.setdefault('plays', []).append({'key': key, 'at': now_ms, 'why': why, 'uid': uid, 'casting': cast})
    store.db['say_rounds'].update_one({'rid': r['rid']}, {'$set': {'plays': r['plays']}})
    from . import selfvoice
    selfvoice.refresh_spoken(store.db, now_ms)
    return uid, tts_id


def _note_trigger(store, r, now_ms, why, trigger):
    r.setdefault('triggers', []).append({'at': now_ms, 'why': why, 'source': (trigger or {}).get('source', 'page')})
    store.db['say_rounds'].update_one({'rid': r['rid']}, {'$set': {'triggers': r['triggers'], 'thin_moment': r.get('thin_moment', False)}})


STOCK_TTL_MS = 300_000


def _quiet_continuation(r, ctx, now_ms):
    # A thin moment in a quiet room has no sentences to compare: it continues while nobody speaks and the
    # last trigger is within the moment gap (final review I2).
    if not r.get('thin_moment'):
        return False
    last = max([x['at'] for x in r.get('triggers', [])] or [r['at']])
    quiet = not any(e.get('kind') == 'speech' and e.get('at', 0) >= now_ms - 60_000 for e in ctx.get('evidence', []))
    return quiet and now_ms - last <= say_moment.GAP_MS


def say_round(env, store, s, roles, tts, now_ms, *, worker, force_new=False, trigger=None, wait=False,
              is_current=None, settings_version=None):
    """One trigger of the talk show (spec 6, 6A): play a pre-written line, check it once against newer
    speech, or write fresh; inside a thin moment the first trigger plays the Shanghainese line and later
    ones stay silent. Returns {rid, played, why, uid, round}."""
    started = time.monotonic()
    epoch = _EPOCH
    if not ((tts.health().get('mouth') or {}).get('device')):
        raise NotConnected('hello 的嘴没连上')
    if not _SAY_LOCK.acquire(blocking=False):
        raise NotConnected('正在说上一轮，请稍等或先停止')
    try:
        _check_pending(epoch, is_current)
        ctx = speech_context.build(store.db, store, s, now_ms)
        scope_id = ctx['scope']['id']
        sig = say_moment.signature(ctx, now_ms)
        family = rounds.family_on(s)
        huyu_on = (s['speech'].get('round') or {}).get('huyu', True) and not family
        key, why, thin_path = None, None, False
        with worker.writing():
            r = worker.stock(scope_id)
            if r and bool(r.get('family')) != family:
                r = None                                     # written for the other voice mode (家人声音 switch)
            triggered = bool(r) and bool(r.get('plays') or r.get('triggers'))
            same = bool(r) and (say_moment.same_moment(r.get('signature'), sig) or _quiet_continuation(r, ctx, now_ms))
            if triggered and same and not force_new:
                if r.get('thin_moment'):
                    why = 'silent'
                else:
                    played = {p['key'] for p in r.get('plays', [])}
                    key = next((k for k in rounds.ranked_keys(r) if k not in played), None)
                    why = 'next' if key else None
            elif r and not triggered and not force_new and now_ms - r['written_at'] <= STOCK_TTL_MS:
                # Pre-written stock is used whenever it is fresh, not only inside the same moment (final review I4).
                newer = any(e.get('kind') == 'speech' and e['at'] > r['written_at'] for e in ctx['evidence'])
                if rounds.is_thin(r, ctx, now_ms, s):
                    key, why, thin_path = 'huyu', 'huyu', True
                elif newer:
                    key = rounds.check_round(env, store, s, r, ctx, now_ms, chat=CHAT)
                    key, why, thin_path = (key, 'checked', False) if key else ('huyu', 'huyu', True)
                else:
                    key, why = rounds.ranked_keys(r)[0], 'stock'
            if why is None:
                # "重新写五句" stays in the same moment (final review I5).
                moment = r['moment_id'] if r and same else None
                r = rounds.write_round(env, store, s, roles, ctx, now_ms, chat=CHAT, trigger=trigger, moment_id=moment)
                worker.put(scope_id, r)
                _check_pending(epoch, is_current)
                if rounds.is_thin(r, ctx, now_ms, s):
                    key, why, thin_path = 'huyu', 'huyu', True
                else:
                    key, why = rounds.ranked_keys(r)[0], 'fresh'
            if key == 'huyu' and (not huyu_on or rounds.line(r, 'huyu').get('dropped')):
                key, why = None, 'silent'
            if thin_path:
                r['thin_moment'] = True                      # later triggers in this moment stay silent (I8)
        _check_pending(epoch, is_current)
        _note_trigger(store, r, now_ms, why, trigger)
        uid = None
        if key:
            uid, tts_id = _speak_line(store, tts, r, key, now_ms, why, epoch=epoch, is_current=is_current)
            if wait:
                _finish(store.db, tts, uid, tts_id)
            else:
                threading.Thread(target=_finish, args=(store.db, tts, uid, tts_id), daemon=True).start()
        server_ms = int((time.monotonic() - started) * 1000)
        store.db['say_rounds'].update_one({'rid': r['rid']}, {'$set': {'timings.last_server_ms': server_ms}})
        return {'rid': r['rid'], 'played': key, 'why': why, 'uid': uid, 'server_ms': server_ms, 'round': compact_round(r)}
    finally:
        _SAY_LOCK.release()


def play_line(store, s, roles, tts, rid, key, now_ms, *, voice=None, dialect=None, wait=False):
    """Replay one line of a round, optionally in another voice and dialect (spec 8: 换副嗓子再说一遍)."""
    if not ((tts.health().get('mouth') or {}).get('device')):
        raise NotConnected('hello 的嘴没连上')
    r = store.db['say_rounds'].find_one({'rid': rid}, {'_id': 0})
    if not r:
        raise KeyError(rid)
    line = rounds.line(r, key)
    if not line.get('text'):
        raise KeyError(key)
    casting = None
    if voice or dialect:
        base_role = roles.get(key if key in roles else 'comfort', {})
        d = dialect or line.get('dialect') or '普通话'
        casting = {'voice': voice or line['voice'], 'dialect': d, 'rate': None if voice and voice != line['voice'] else line.get('rate'),
                   'instruction': speech_settings.DIALECTS[d] + base_role.get('delivery', '')}
    uid, tts_id = _speak_line(store, tts, r, key, now_ms, 'page', casting)
    if wait:
        _finish(store.db, tts, uid, tts_id)
    else:
        threading.Thread(target=_finish, args=(store.db, tts, uid, tts_id), daemon=True).start()
    return {'uid': uid, 'rid': rid, 'line': key, 'casting': casting or {k: line.get(k) for k in ('voice', 'dialect')}}


TRY_TEXT = "我是{label}。这是一句试听：今天的调试就到这里，大家辛苦了。"
ROLE_TRY_TEXT = '今天又加了需求，交期还是明天。先吃饭，吃完再想办法。'


def try_role(store, tts, role, s, now_ms, wait=False):
    epoch = _EPOCH
    if not ((tts.health().get('mouth') or {}).get('device')):
        raise NotConnected('hello 的嘴没连上')
    resolved = speech_settings.role_config(role,s)
    with _ENQUEUE_LOCK:
        _check_pending(epoch,None)
        cancel_pending()
        tts_id = tts.say(ROLE_TRY_TEXT,voice=resolved['voice'],interrupt=True,
                        **{k:resolved[k] for k in ('instruction','rate') if resolved.get(k) is not None})
    rec = {'uid':'u'+uuid.uuid4().hex,'role':'try','audition_role':role['id'],'text':ROLE_TRY_TEXT,
           'voice':resolved['voice'],'instruction':resolved['instruction'],'tts_id':tts_id,'at':now_ms,'state':'sent'}
    store.db['utterances'].insert_one(dict(rec))
    from . import selfvoice
    selfvoice.refresh_spoken(store.db,now_ms)
    if wait:
        _finish(store.db,tts,rec['uid'],tts_id)
    else:
        threading.Thread(target=_finish,args=(store.db,tts,rec['uid'],tts_id),daemon=True).start()
    return rec


def voice_list(tts):
    """soul-tts's voice aliases for the page: id, label, rate (never the cloned voice ids)."""
    v = tts.voices().get("voices") or {}
    return [{"id": k, "label": x.get("label") or k, "rate": x.get("rate")} for k, x in v.items()]


def try_voice(store, tts, voice_id, now_ms, wait=False):
    """One fixed sample sentence in `voice_id` through the robot's mouth (voice audition, user 2026-11-11)."""
    epoch = _EPOCH
    voices = (tts.voices().get("voices") or {})
    if voice_id not in voices:
        raise KeyError(voice_id)
    if not ((tts.health().get("mouth") or {}).get("device")):
        raise NotConnected("hello 的嘴没连上")
    label = re.sub(r"[（(].*?[）)]", "", voices[voice_id].get("label") or voice_id).split("·")[0].strip()
    text = TRY_TEXT.format(label=label)
    with _ENQUEUE_LOCK:
        _check_pending(epoch,None)
        cancel_pending()
        tts_id = tts.say(text, voice=voice_id, interrupt=True)
    uid = 'u'+uuid.uuid4().hex
    rec = {"uid": uid, "role": "try", "text": text, "voice": voice_id, "tts_id": tts_id, "at": now_ms, "state": "sent"}
    store.db["utterances"].insert_one(dict(rec))       # selfvoice treats it as hello's own voice
    if wait:
        _finish(store.db, tts, uid, tts_id)
    else:
        threading.Thread(target=_finish, args=(store.db, tts, uid, tts_id), daemon=True).start()
    return rec


class TtsHttp:
    """Minimal client for soul-tts on the loopback (standard library only)."""
    def __init__(self, base, token, timeout=15):
        self.base, self.token, self.timeout = base.rstrip("/"), token, timeout

    @classmethod
    def from_file(cls, base, token_file):
        """None when the token file is missing (soul-tts not deployed): /say then answers 409."""
        try:
            with open(token_file) as f:
                token = f.read().strip()
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
            e.close()
            if e.code in (409, 429):         # no device on /mouth, or too many queued: nothing was paid for
                raise NotConnected("hello 的嘴没连上" if e.code == 409 else "hello 还有话没说完") from None
            raise

    def health(self):
        return self._call("GET", "/health")

    def voices(self):
        return self._call("GET", "/v1/voices")

    def say(self, text, **kw):
        return self._call("POST", "/v1/say", {"text": text, **kw})["id"]

    def status(self, uid, wait=0):
        return self._call("GET", f"/v1/say/{uid}" + (f"?wait={wait:g}" if wait else ""), timeout=wait + 10)

    def stop(self):
        return self._call("POST", "/v1/stop").get("cancelled", [])
