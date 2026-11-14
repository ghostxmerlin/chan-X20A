"""Chat v0 (plan docs/superpowers/plans/2026-11-14-chat-v0.md): hello answers what people say to her in `chat` mode.

Flow: the ear POSTs every recognised sentence to /chat/heard while hello reports `chat` (ear/mouth_link.py ModeRelay).
heard() gates it, writes one chat_turns doc and leaves it as the single pending sentence; one worker thread answers it:
context from the cache, one plain-text llm.chat call, then soul-tts says it in 无限's voice through chat's own speak path.

Gate (each skip is logged without the text and recorded with its reason):
  not_chat        mind's mirror is not chat (hello owns the mode; the ear only sends in chat)
  self            the ear flagged hello's own sound during the sentence (self.sounding)
  hello_voice     the text matches hello's own clips / recent lines (selfvoice.is_hello)
  hello_speaking  hello is speaking now (speak.speaking())
  empty / filler  no text, or only 嗯 / 啊 / um ...
  far_band        the ear's voice band is far / none
  far_quiet       less than chat.near_db dB above the ear's floor (owner: answer everyone, but not far voices)
  mouth_not_tts   嘴 is not TTS (chat speaks only through soul-tts)
  no_model / no_tts  nothing configured to think or speak with
One turn at a time: a sentence that arrives while hello is thinking replaces the waiting one (replaced); a waiting one
older than STALE_MS is dropped (stale). When thinking ends and a newer sentence is already waiting, the reply is
dropped once (superseded) so hello answers the newest; never twice in a row, so a talker who never pauses still gets
answers. An empty model reply is a skip (empty_reply), nothing is said.

Context (refreshed part by part when its TTL ran out: while idle, and at turn time within ~100 ms): the latest scene
(summary, topic, sound), the latest conversation notes, MemoryStore.recall (8, of which at most 3 that share words with
the sentence go in), hello's persona (config/roles deadpan, 无限's delivery), the recognised speaker's library card and
active notes (links.voice_names), and the last chat turns (chat_turns).

Leaving chat (the on_change hook) cancels the current speech and the pending turn; the speak path refuses outside chat.
Billing gates nothing (owner); chat costs go to mind_runs (step chat) like every other model call.
"""
import collections
import re
import threading
import time
import uuid

from . import llm, selfvoice, settings as settings_mod, speak, speech_settings
from .library import Library
from .memory import MemoryStore
from .store import LANE

ROLE = 'deadpan'                     # 无限's delivery (config/roles/deadpan.json, voice wuxian)
VOICE = 'wuxian'                     # owner 2026-11-14: 声音用无限
FALLBACK_ROLE = {'id': ROLE, 'persona': '', 'voice': VOICE, 'dialect': '普通话', 'delivery': '平静、一本正经地聊天。'}
STALE_MS = 5000
LLM_TIMEOUT_S = 6.0
RETRY_BEFORE_S = 3.0                 # an error this early leaves time for one more try
MAX_TOKENS = 300
RECALL = 8
MAX_MEMORIES = 3                     # coordinator 2026-11-14: terra over-uses one memory; only related ones, at most 3
HISTORY_MAX_AGE_MS = 3600_000
SCENE_FRESH_MS = 4 * 60_000
NOTES_FRESH_MS = 10 * 60_000
TTL_MS = {'persona': 300_000, 'scene': 20_000, 'notes': 30_000, 'people': 120_000, 'memories': 120_000}
REFRESH_BUDGET_MS = 100
IDLE_WAIT_S = 10
FAR_BANDS = ('far', 'none')
FILLER_CHARS = set('嗯啊哦呃额唉哈嘿呀噢喔诶欸哎呢吧嘛哼呵唔')
FILLER_WORDS = {'um', 'uh', 'umm', 'hmm', 'hm', 'mm', 'mhm', 'ah', 'oh', 'eh', 'er', 'ha', 'haha'}
STATES = ('queued', 'thinking', 'answered', 'skipped', 'superseded')


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float('inf')


def _norm(text):
    return re.sub(r'[\W_]', '', str(text or '').lower())


def _short(v, n):
    return v[:n] if isinstance(v, str) else None


def parse(body):
    """The ear's /chat/heard body -> a clean item; ValueError (400) for a malformed one."""
    if not isinstance(body, dict):
        raise ValueError('body must be an object')
    text = body.get('text')
    if not isinstance(text, str) or len(text) > 512:
        raise ValueError('text (up to 512 characters) required')
    for k in ('session', 'id'):
        if body.get(k) is not None and not (isinstance(body[k], str) and len(body[k]) <= 80):
            raise ValueError(f'{k} must be a short text')
    pos = body.get('pos')
    if pos is not None and not (isinstance(pos, list) and len(pos) == 2 and all(p is None or _num(p) for p in pos)):
        raise ValueError('pos must be [start, end]')
    for k in ('speaker', 'voice', 'self', 'debug'):
        if body.get(k) is not None and not isinstance(body[k], dict):
            raise ValueError(f'{k} must be an object')
    for k in ('loud_db', 't', 'dur', 'sent_at'):
        if body.get(k) is not None and not _num(body[k]):
            raise ValueError(f'{k} must be a number')
    sp, v, me, dbg = (body.get(k) or {} for k in ('speaker', 'voice', 'self', 'debug'))
    loud = body.get('loud_db', v.get('loud_db'))
    return {'session': body.get('session'), 'id': body.get('id'), 'pos': pos, 'text': text.strip(),
            'lang': _short(body.get('lang'), 16), 'emotion': _short(body.get('emotion'), 16),
            'event': _short(body.get('event'), 16), 't': body.get('t'), 'dur': body.get('dur'),
            'sent_at': body.get('sent_at'),
            'speaker': {'band': _short(sp.get('band'), 16), 'name': _short(sp.get('name'), 32),
                        'score': sp.get('score') if _num(sp.get('score')) else None},
            'voice': {'p': v.get('p') if _num(v.get('p')) else None, 'band': _short(v.get('band'), 16),
                      'acoustic_band': _short(v.get('acoustic_band'), 16),
                      'loud_db': v.get('loud_db') if _num(v.get('loud_db')) else None},
            'loud_db': loud if _num(loud) else None,
            'self': {'sounding': me.get('sounding') is True, 'moving': me.get('moving') is True},
            'debug': {str(k)[:24]: x for k, x in list(dbg.items())[:16] if _num(x)}}


def is_filler(text):
    n = _norm(text)
    return bool(n) and (all(c in FILLER_CHARS for c in n) or n in FILLER_WORDS)


def gate(item, cfg, *, mode, speaking, mouth):
    """None = answer it, else the skip reason (order: the cheapest certain reasons first)."""
    if mode != 'chat':
        return 'not_chat'
    if item['self']['sounding']:
        return 'self'
    if not _norm(item['text']):
        return 'empty'
    if selfvoice.is_hello(item['text'], item['self']['sounding']):
        return 'hello_voice'
    if speaking:
        return 'hello_speaking'
    if is_filler(item['text']):
        return 'filler'
    if item['voice'].get('band') in FAR_BANDS:
        return 'far_band'
    if item['loud_db'] is not None and item['loud_db'] < cfg['near_db']:
        return 'far_quiet'
    if mouth != 'tts':
        return 'mouth_not_tts'
    return None


def trim(text, max_chars):
    """One spoken reply: no stage directions, quotes, emoji or a leading name; cut at a sentence end within max_chars."""
    t = re.sub(r'[（(\[【][^）)\]】]*[）)\]】]', '', str(text or ''))
    t = re.sub(r'[“”"\'「」『』*#`]', '', t)
    t = re.sub(r'[\U0001F000-\U0001FAFF☀-➿]', '', t)
    t = re.sub(r'\s*\n+\s*', '，', t).strip(' ，,')
    t = re.sub(r'^(hello|hello：|hello:)\s*[：:]?\s*', '', t, flags=re.I).strip()
    if len(t) <= max_chars:
        return t
    head = t[:max_chars]
    for marks, keep in (('。！？…!?', True), ('，、；,;', False)):
        i = max(head.rfind(m) for m in marks)
        if i >= max_chars // 3:
            return head[:i + 1] if keep else head[:i] + '。'
    return head + '。'


def _bigrams(text):
    n = _norm(text)
    return {n[i:i + 2] for i in range(len(n) - 1)} | ({n} if len(n) == 1 else set())


def related_memories(statements, text, limit=MAX_MEMORIES):
    """At most `limit` remembered statements that share a word (two characters) with the sentence, best first."""
    words = _bigrams(text)
    scored = [(len(words & _bigrams(m)), -i, m) for i, m in enumerate(statements)]
    return [m for score, _, m in sorted(scored, reverse=True) if score > 0][:limit]


def messages(ctx, item, history, cfg, who):
    """The prompt: hello's persona + rules + context in the system message, recent turns, then the sentence."""
    role = ctx.get('persona') or {}
    L = [ctx.get('self_facts') or '我叫 hello，是放在办公桌上的桌面宠物机器人。']
    if role.get('persona'):
        L.append('【性格】' + role['persona'])
    L.append('【现在是对话模式】有人在跟你说话，你直接回答他。')
    L.append('【要求】\n' + '\n'.join('- ' + r for r in (
        f'用一两句口语回答，最多 {cfg["max_chars"]} 字，越短越好；像桌宠，有自己的脾气，可以带一点冷幽默。',
        '只输出要说出口的话：不加引号、括号、动作描写和表情符号，不说“作为AI”“我是语言模型”。',
        '不给医疗、健康、用药方面的建议；纪要或记忆里提到身体情况，也不要劝人看医生、注意身体。',
        '听不清、没头没尾，就简短反问一句；不确定的事不要编。',
        '下面的背景只在和这句话有关时才用，不要硬扯，不要每句都提同一件事。')))
    bg = []
    if ctx.get('time'):
        bg.append(f'现在：{ctx["time"]}')
    if ctx.get('environment'):
        bg.append(f'环境：{ctx["environment"][:600]}')
    sc = ctx.get('scene')
    if sc:
        if sc.get('doing') or sc.get('summary'):
            bg.append(f'此刻的情景：{sc.get("doing") or ""}（{sc.get("summary") or ""}）')
        if sc.get('topic'):
            bg.append('在聊：' + ' / '.join(sc['topic']))
        if sc.get('sound'):
            bg.append(f'声音：{sc["sound"]}')
    if ctx.get('notes'):
        bg.append(f'对话纪要：{ctx["notes"]}')
    mem = related_memories(ctx.get('memories') or [], item['text'])
    if mem:
        bg.append('你记得（只在相关时提）：' + '；'.join(mem))
    person = ctx.get('person')
    if person:
        p = f'说话的人：{person["display"]}'
        if person.get('notes'):
            p += '（' + '；'.join(person['notes']) + '）'
        if person.get('tease') == 'never':
            p += '。不要调侃他。'
        bg.append(p)
    if bg:
        L.append('【背景】\n' + '\n'.join(bg))
    out = [{'role': 'system', 'content': '\n\n'.join(L)}]
    for h in history:
        user = f'【{h["who"]}】{h["heard"]}' if h.get('who') else h['heard']
        if out[-1]['role'] == 'user':
            out[-1]['content'] += '\n' + user
        else:
            out.append({'role': 'user', 'content': user})
        if h.get('reply'):
            out.append({'role': 'assistant', 'content': h['reply']})
    now = f'【{who}】{item["text"]}' if who else item['text']
    if out[-1]['role'] == 'user':
        out[-1]['content'] += '\n' + now
    else:
        out.append({'role': 'user', 'content': now})
    return out


class NotInChat(speak.NotConnected):
    pass


class Chat:
    """The chat worker. enter() / leave() come from the mode hook (runner.wire_modes); heard() from POST /chat/heard.
    threaded=False (tests, demo): nothing runs by itself, process_once() answers the pending sentence."""

    def __init__(self, db, store, *, settings_fn, roles_fn, tts, env, mode_fn=lambda: None, mouth_fn=None,
                 chat_fn=None, clock_ms=None, mono=time.monotonic, log=print, threaded=True):
        self.db, self.store, self.settings_fn, self.roles_fn = db, store, settings_fn, roles_fn
        self.tts, self.env, self.mode_fn = tts, env, mode_fn
        self.mouth_fn = mouth_fn or (lambda: (db['switches'].find_one({'_id': 'mouth'}) or {}).get('mode', 'clips'))
        self.chat_fn = chat_fn or llm.chat
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self.mono, self.log, self.threaded = mono, log, threaded
        self._cond = threading.Condition()
        self._rec_lock = threading.Lock()
        self._pending = None
        self._active = False
        self._gen = 0
        self._streak = 0                 # replies dropped in a row for a newer sentence (at most 1)
        self._thread = None
        self._seen = collections.deque(maxlen=128)
        self._ctx = {}                   # part -> (fetched at, monotonic, value)
        self._history = collections.deque(maxlen=20)
        self._history_loaded = False
        self.thinking = False

    def ensure_indexes(self):
        self.db['chat_turns'].create_index([('at', 1)])
        self.db['chat_turns'].create_index([('tid', 1)], unique=True)
        self.db['chat_turns'].create_index([('session', 1), ('id', 1)])

    def active(self):
        return self._active

    # ---- mode hooks ----
    def enter(self, now_ms=None):
        with self._cond:
            if self._active:
                return
            self._active, self._gen, self._streak = True, self._gen + 1, 0
            self._history_loaded = False
            if self.threaded and self._thread is None:
                self._thread = threading.Thread(target=self._loop, name='mind-chat', daemon=True)
                self._thread.start()
        self.log('mind chat: on', flush=True)

    def leave(self, now_ms=None):
        """Leave chat: drop the waiting sentence, make the one being thought about stale, stop what hello says."""
        with self._cond:
            was, pending = self._active, self._pending
            self._active, self._pending, self._gen = False, None, self._gen + 1
            self._cond.notify_all()
        if pending:
            self._update(pending['tid'], state='skipped', skip='left_chat')
        if self.tts:
            try:
                speak.cancel_pending(self.tts)
            except Exception as e:           # noqa: BLE001 - the mode change itself happened
                self.log(f'mind chat: stop on leave failed: {type(e).__name__}', flush=True)
        if was:
            self.log('mind chat: off', flush=True)

    # ---- heard ----
    def heard(self, body, now_ms=None):
        """One sentence from the ear -> {tid, accepted, skip}. Never waits for the model."""
        item = parse(body)
        now = self.clock_ms() if now_ms is None else now_ms
        key = (item['session'], item['id'])
        if item['id'] is not None and key in self._seen:
            return {'tid': None, 'accepted': False, 'skip': 'duplicate'}
        self._seen.append(key)
        mode = self.mode_fn()
        if mode == 'chat' and not self._active:
            self.enter(now)                  # mind restarted while hello was chatting: no enter hook came
        s = self.settings_fn()
        cfg = speech_settings.chat_config(s)
        reason = gate(item, cfg, mode=mode, speaking=speak.speaking(), mouth=self.mouth_fn())
        if reason is None and not (self.env and self.env.get('api_key')):
            reason = 'no_model'
        if reason is None and not self.tts:
            reason = 'no_tts'
        tid = 't' + uuid.uuid4().hex[:16]
        end_at = item['t'] + int((item['dur'] or 0) * 1000) if _num(item['t']) else None
        doc = {'tid': tid, 'at': now, 'session': item['session'], 'id': item['id'], 'pos': item['pos'],
               'text': item['text'], 'lang': item['lang'], 'emotion': item['emotion'], 'speaker': item['speaker'],
               'voice': item['voice'], 'loud_db': item['loud_db'], 'self': item['self'], 'mode': mode,
               'state': 'skipped' if reason else 'queued', 'skip': reason, 'reply': None, 'model': cfg['model'],
               'cost_usd': None, 'scope_id': speech_settings.scope(s)['id'],
               'timings': {'ear': item['debug'], 'speech_end_at': end_at,
                           'push_ms': now - int(item['sent_at']) if _num(item['sent_at']) else None}}
        try:
            self.db['chat_turns'].insert_one(dict(doc))
        except Exception as e:               # noqa: BLE001 - a record failing never stops the turn
            self.log(f'mind chat: record failed: {type(e).__name__}', flush=True)
        if reason:
            self.log(f'mind chat: skip {reason} ({item["session"]} {item["id"]})', flush=True)
            return {'tid': tid, 'accepted': False, 'skip': reason}
        with self._cond:
            if not self._active:
                old, gone = None, True
            else:
                old, gone = self._pending, False
                self._pending = {**item, 'tid': tid, 'at': now, 'received': self.mono(), 'gen': self._gen,
                                 'end_at': end_at}
                self._cond.notify_all()
        if gone:
            self._update(tid, state='skipped', skip='left_chat')
            return {'tid': tid, 'accepted': False, 'skip': 'left_chat'}
        if old:
            self._update(old['tid'], state='skipped', skip='replaced')
            self.log(f'mind chat: skip replaced ({old["session"]} {old["id"]})', flush=True)
        return {'tid': tid, 'accepted': True, 'skip': None}

    # ---- the worker ----
    def _loop(self):
        while True:
            with self._cond:
                if not self._active:
                    self._thread = None
                    return
                warm = not self._history_loaded
                if not warm and self._pending is None:
                    self._cond.wait(IDLE_WAIT_S)
                    if not self._active:
                        self._thread = None
                        return
                idle = self._pending is None
            try:
                if warm:                     # just entered: the whole context and the recent turns, off any request
                    self._refresh(self.clock_ms(), budget_ms=None)
                    self._load_history(self.clock_ms())
                elif idle:
                    self._refresh(self.clock_ms(), budget_ms=None)
                else:
                    self.process_once()
            except Exception as e:           # noqa: BLE001 - keep listening
                self.log(f'mind chat: worker error {type(e).__name__}: {str(e)[:120]}', flush=True)
                time.sleep(1)

    def process_once(self):
        """Answer the waiting sentence, if any -> the final chat_turns fields, or None."""
        with self._cond:
            item, self._pending = self._pending, None
            gen = self._gen
            if item is None:
                return None
            self.thinking = True
        try:
            return self._turn(item, gen)
        except Exception as e:               # noqa: BLE001 - recorded; the worker logs it and goes on
            self._skip(item, 'error', error=type(e).__name__)
            raise
        finally:
            self.thinking = False

    def _alive(self, gen):
        return self._active and self._gen == gen and self.mode_fn() == 'chat'

    def _skip(self, item, reason, **fields):
        self.log(f'mind chat: skip {reason} ({item["session"]} {item["id"]})', flush=True)
        return self._update(item['tid'], state='superseded' if reason == 'superseded' else 'skipped', skip=reason, **fields)

    def _turn(self, item, gen):
        wait_ms = int((self.mono() - item['received']) * 1000)
        if item['gen'] != gen or not self._alive(gen):
            return self._skip(item, 'left_chat')
        if wait_ms > STALE_MS:
            return self._skip(item, 'stale', **{'timings.wait_ms': wait_ms})
        epoch = speak._EPOCH
        s = self.settings_fn()
        cfg = speech_settings.chat_config(s)
        t0 = self.mono()
        self._refresh(self.clock_ms(), budget_ms=REFRESH_BUDGET_MS)
        if not self._history_loaded:
            self._load_history(self.clock_ms())
        ctx = self.context(s, item)
        context_ms = int((self.mono() - t0) * 1000)
        hist = list(self._history)[-cfg['history_turns']:] if cfg['history_turns'] else []
        who = (ctx.get('person') or {}).get('display')
        msgs = messages(ctx, item, hist, cfg, who)
        reply, run = self._ask(cfg, msgs)
        timings = {'timings.wait_ms': wait_ms, 'timings.context_ms': context_ms, 'timings.llm_ms': run['llm_ms'],
                   'timings.tries': run['tries']}
        fields = {'person_id': (ctx.get('person') or {}).get('person_id'), 'model': cfg['model'],
                  'cost_usd': run['cost_usd'], 'usage': run['usage'], 'run_id': run['run_id'], 'raw_reply': run['raw'],
                  **timings}
        if not reply:
            return self._skip(item, 'llm_error' if run['error'] else 'empty_reply', error=run['error'], **fields)
        if not self._alive(gen) or epoch != speak._EPOCH:
            return self._skip(item, 'left_chat' if not self._alive(gen) else 'cancelled', reply=reply, **fields)
        with self._cond:
            newer = self._pending is not None
            drop = newer and self._streak == 0
            self._streak = 1 if drop else 0
        if drop:
            self._history.append({'heard': item['text'], 'reply': None, 'who': who})
            return self._skip(item, 'superseded', reply=reply, **fields)
        try:
            uid, tts_id, submit_at = self.speak_line(reply, s, gen=gen, epoch=epoch, tid=item['tid'])
        except speak.NotConnected as e:
            why = 'left_chat' if isinstance(e, NotInChat) else 'cancelled' if '取消' in str(e) else \
                'mouth_not_tts' if '嘴不是' in str(e) else 'no_mouth'
            return self._skip(item, why, reply=reply, **fields)
        except Exception as e:               # noqa: BLE001 - soul-tts trouble: recorded
            return self._skip(item, 'tts_error', reply=reply, error=type(e).__name__, **fields)
        self._history.append({'heard': item['text'], 'reply': reply, 'who': who})
        end_at = item.get('end_at')
        out = self._update(item['tid'], state='answered', skip=None, reply=reply, uid=uid, tts_id=tts_id,
                           **fields, **{'timings.submit_at': submit_at,
                                        'timings.end_to_submit_ms': submit_at - end_at if end_at else None,
                                        'timings.received_to_submit_ms': submit_at - item['at']})
        self.log(f'mind chat: answered ({item["session"]} {item["id"]}) llm {run["llm_ms"]} ms', flush=True)
        return out

    def _ask(self, cfg, msgs):
        """One plain-text model call (one retry after an early error) -> (reply text or '', run info)."""
        body = {'model': cfg['model'], 'messages': msgs, 'max_tokens': MAX_TOKENS, 'temperature': 0.8,
                'reasoning_effort': 'none'}       # coordinator 2026-11-14: always none (haiku burns tokens otherwise)
        t0 = self.mono()
        tries, resp, err, ms = 0, None, None, 0
        while True:
            tries += 1
            left = LLM_TIMEOUT_S - (self.mono() - t0)
            resp, ms, err = self.chat_fn(self.env['base_url'], self.env['api_key'], body, timeout=max(1.0, left))
            if not err or tries >= 2 or self.mono() - t0 >= RETRY_BEFORE_S:
                break
        raw = ''
        if resp and not err:
            try:
                raw = resp['choices'][0]['message']['content'] or ''
            except (KeyError, IndexError, TypeError):
                raw = ''
        raw = raw if isinstance(raw, str) else ''
        usage = (resp or {}).get('usage') or {}
        cost = llm.cost_usd(cfg['model'], usage)
        now = self.clock_ms()
        run_id = None
        try:
            run_id = self.store.put_run({'lane': 'live', 'minute': settings_mod.minute_floor(now), 'step': 'chat',
                                         'prompt': 'chat-v0', 'model': cfg['model'], 'request': body, 'response': resp,
                                         'error': err, 'latency_ms': ms, 'usage': usage, 'cost_usd': cost, 'at': now})
        except Exception as e:               # noqa: BLE001
            self.log(f'mind chat: run record failed: {type(e).__name__}', flush=True)
        return trim(raw, cfg['max_chars']), {'llm_ms': int((self.mono() - t0) * 1000), 'tries': tries,
                                            'usage': usage, 'cost_usd': cost, 'error': err, 'run_id': run_id,
                                            'raw': raw[:400]}

    # ---- the chat speak path ----
    def speak_line(self, text, s, *, gen=None, epoch=None, tid=None):
        """Say one chat reply in 无限's voice -> (uid, tts_id, submit_at). Refuses outside chat (NotInChat), when 嘴 is
        not TTS, or when a stop / mode change came after `epoch` (speak.NotConnected). Not the talk show's path:
        no only_talk, no _SAY_LOCK (thinking is not speaking)."""
        if self.mode_fn() != 'chat' or not self._active or (gen is not None and gen != self._gen):
            raise NotInChat('只在对话模式')
        if self.mouth_fn() != 'tts':
            raise speak.NotConnected('嘴不是 TTS')
        role = speech_settings.role_config((self.roles_fn() or {}).get(ROLE) or FALLBACK_ROLE, s)
        opts = {k: role[k] for k in ('instruction', 'rate') if role.get(k) is not None}
        epoch = speak._EPOCH if epoch is None else epoch
        with speak._ENQUEUE_LOCK:
            speak._check_pending(epoch, lambda: self._active and self.mode_fn() == 'chat' and
                                 (gen is None or gen == self._gen))
            tts_id = self.tts.say(text, voice=VOICE, interrupt=True, **opts)
            speak._PLAYING.add(tts_id)       # speaking() at once (the finish thread keeps it until the line ends)
        submit_at = self.clock_ms()
        uid = 'u' + uuid.uuid4().hex
        self.db['utterances'].insert_one({'uid': uid, 'role': 'chat', 'text': text, 'voice': VOICE, 'tts_id': tts_id,
                                          'at': submit_at, 'state': 'sent', 'pipeline': 'chat',
                                          'scope_id': speech_settings.scope(s)['id'], 'tts_submit_at': submit_at,
                                          'instruction': opts.get('instruction'), 'chat_tid': tid})
        if self.threaded:
            threading.Thread(target=self._finish, args=(uid, tts_id, tid), daemon=True).start()
        else:
            self._finish(uid, tts_id, tid)
        return uid, tts_id, submit_at

    def _finish(self, uid, tts_id, tid):
        selfvoice.refresh_spoken(self.db, self.clock_ms())     # her own reply counts as her voice from now on
        speak._finish(self.db, self.tts, uid, tts_id)
        if not tid:
            return
        u = self.db['utterances'].find_one({'uid': uid}) or {}
        t = u.get('timings_s') or {}
        sec = lambda k: int(round(t[k] * 1000)) if _num(t.get(k)) else None
        self._update(tid, **{'timings.tts_first_sound_ms': sec('first_sound'),
                             'timings.tts_first_accepted_ms': sec('first_accepted'),
                             'timings.tts_done_ms': sec('done'), 'tts_state': u.get('state'),
                             'played': u.get('played'), 'tts_cost_cny': u.get('tts_cost_cny')})

    # ---- records ----
    def _update(self, tid, **fields):
        sets = dict(fields)
        try:
            with self._rec_lock:
                return self._update_locked(tid, sets)
        except Exception as e:               # noqa: BLE001
            self.log(f'mind chat: record failed: {type(e).__name__}', flush=True)
            return {'tid': tid, **sets}

    def _update_locked(self, tid, sets):
        row = self.db['chat_turns'].find_one({'tid': tid}) or {}
        timings = dict(row.get('timings') or {})
        plain = {}
        for k, v in sets.items():
            if k.startswith('timings.'):
                timings[k[8:]] = v
            else:
                plain[k] = v
        self.db['chat_turns'].update_one({'tid': tid}, {'$set': {**plain, 'timings': timings}})
        row.update(plain, timings=timings)
        row.pop('_id', None)
        return row

    def _load_history(self, now_ms):
        self._history_loaded = True
        try:
            rows = list(self.db['chat_turns'].find({'at': {'$gte': now_ms - HISTORY_MAX_AGE_MS},
                                                    'state': {'$in': ['answered', 'superseded']}})
                        .sort('at', -1).limit(self._history.maxlen))
        except Exception as e:               # noqa: BLE001
            self.log(f'mind chat: history unavailable: {type(e).__name__}', flush=True)
            return
        people = self._part('people') or {}
        self._history.clear()
        for r in reversed(rows):
            name = (r.get('speaker') or {}).get('name') if (r.get('speaker') or {}).get('band') == 'known' else None
            self._history.append({'heard': r.get('text') or '', 'reply': r.get('reply') if r.get('state') == 'answered'
                                  else None, 'who': (people.get(name) or {}).get('display') if name else None})

    # ---- context ----
    def _part(self, name):
        got = self._ctx.get(name)
        return got[1] if got else None

    def _refresh(self, now_ms, budget_ms=REFRESH_BUDGET_MS):
        """Refresh the parts whose TTL ran out, oldest need first, until budget_ms is spent (None: all)."""
        t0 = self.mono()
        for name in ('persona', 'scene', 'notes', 'people', 'memories'):
            got = self._ctx.get(name)
            if got and self.mono() - got[0] < TTL_MS[name] / 1000:
                continue
            if budget_ms is not None and (self.mono() - t0) * 1000 >= budget_ms:
                break
            try:
                value = getattr(self, '_load_' + name)(now_ms)
            except Exception as e:           # noqa: BLE001 - keep the old value; try again next time
                self.log(f'mind chat: context {name} unavailable: {type(e).__name__}', flush=True)
                if got:
                    self._ctx[name] = (self.mono(), got[1])
                continue
            self._ctx[name] = (self.mono(), value)

    def _load_persona(self, now_ms):
        return dict((self.roles_fn() or {}).get(ROLE) or FALLBACK_ROLE)

    def _load_scene(self, now_ms):
        row = self.store.latest_scene_any(LANE)
        if not row or row.get('source') not in ('model', 'carried') or now_ms - row['minute'] > SCENE_FRESH_MS:
            return None
        sc = row.get('scene') or {}
        said = lambda dim: [e.get('text') for e in sc.get('entries') or [] if e.get('dimension') == dim and e.get('text')]
        return {'doing': (sc.get('people_doing') or {}).get('text') or '', 'summary': sc.get('summary') or '',
                'topic': said('topic')[:2], 'sound': '；'.join(said('sound_scene')[:1]), 'minute': row['minute']}

    def _load_notes(self, now_ms):
        since = speech_settings.scope(self.settings_fn())['since']
        conv = self.db['conversations'].find_one({'start_ms': {'$gte': since}}, {'_id': 0}, sort=[('start_ms', -1)])
        if not conv or not (conv.get('notes') or conv.get('title')):
            return None
        if conv.get('end_ms') is not None and now_ms - conv['end_ms'] > NOTES_FRESH_MS:
            return None
        return f'{conv.get("title") or ""}：{conv.get("notes") or ""}'[:400]

    def _load_memories(self, now_ms):
        if not (self.settings_fn().get('memory') or {}).get('apply', True):
            return []
        scope = speech_settings.scope(self.settings_fn())['id']
        return [m['statement'] for m in MemoryStore(self.db).recall(scope, now_ms, limit=RECALL) if m.get('statement')]

    def _load_people(self, now_ms):
        """voice name -> {person_id, display, notes (active, up to 6), tease} from the library (links.voice_names)."""
        lib = Library(self.db)
        notes = collections.defaultdict(list)
        for n in sorted(lib._heads('people_notes'), key=lambda r: -(r.get('changed_at') or 0)):
            if n.get('status') == 'active' and len(notes[n['person_id']]) < 6:
                notes[n['person_id']].append(n['text'])
        out = {}
        for c in lib._heads('people_cards'):
            if c.get('status') == 'retired':
                continue
            for name in (c.get('links') or {}).get('voice_names') or []:
                out[name] = {'person_id': c['person_id'], 'display': c['display'], 'notes': notes[c['person_id']],
                             'tease': (c.get('treatment') or {}).get('tease')}
        return out

    def person(self, s, speaker):
        """The recognised speaker's card, or None (band known only; the settings owner as a fallback)."""
        if not speaker or speaker.get('band') != 'known' or not speaker.get('name'):
            return None
        name = speaker['name']
        got = (self._part('people') or {}).get(name)
        if got:
            return got
        owner = s.get('owner') or {}
        if name in (owner.get('voice_names') or []):
            return {'person_id': 'owner', 'display': owner.get('display') or '主人', 'notes': [], 'tease': None}
        return None

    def context(self, s, item):
        eff = speech_settings.effective(s)
        now = self.clock_ms()
        return {'self_facts': s.get('self_facts'), 'environment': (eff.get('environment') or '').strip(),
                'time': settings_mod.local_label(now, s['tz']), 'persona': self._part('persona'),
                'scene': self._part('scene'), 'notes': self._part('notes'), 'memories': self._part('memories') or [],
                'person': self.person(s, item['speaker'])}

    # ---- the page ----
    def turns(self, limit=5):
        rows = list(self.db['chat_turns'].find({}, {'_id': 0}).sort('at', -1).limit(limit))
        keep = ('tid', 'at', 'text', 'reply', 'state', 'skip', 'model', 'cost_usd', 'timings', 'loud_db')
        out = []
        for r in rows:
            row = {k: r.get(k) for k in keep}
            row['who'] = (self.person(self.settings_fn(), r.get('speaker')) or {}).get('display')
            out.append(row)
        return out
