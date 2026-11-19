"""hello's pause (2026-11-17): no unattended spending, and one daily cap over everything paid.

The pause is its own state, next to the switches and never written into them: on return everything runs exactly as
the owner left it. mind owns it (it already owns the switches, sees every owner input and reads the run log's
ledger); the ear only reports what it saw (presence) and obeys the pause in its own paid paths.

Two reasons, either one pauses:
  away    M12 saw no face, no known voice (the ear's speaker band "known": owner or family) was heard and there was
          no explicit owner input for settings pause.away_min minutes (default 10). Ordinary speech is NOT presence
          (a TV talks all evening). Back = any M12 face, a known voice, or an explicit owner input (ASR word, DualKey,
          screen tap / quick page, a web switch).
  budget  today's spend (Beijing day) over all paid items reached pause.cap_usd_per_day (default $10, owner 2026-11-19; was ¥20). Computed
          from the run log's ledger (every record with a cost: 听悟, 围观 realtime + search, 视觉爆发, eye, tts, every
          luna / terra call), USD converted at pause.usd_cny (default 7.2). A warning (budget.warn) at 80 %. Lasts
          until Beijing midnight, until the owner presses 9 (that day only), or until the cap is raised above today's
          spend.

While paused (readers: active() / paused() / budget_stop()): 听悟 streaming, the 围观 realtime session (also no
re-open on new speech; the ear), 视觉爆发 (the ear), eye looks (soul-eye), 纪要 (soul-notes), 情景 (mind's fusion
and segment summaries), the memory worker's 10-minute windows and the talk show's 提前写 / 连续说 do nothing. The
daily memory consolidation (memory_extract) still runs while away (as designed); at the budget pause every model
call through model_calls.run_json is refused (BudgetExceeded), consolidation included, as the old daily cap did.

Documents (Mongo hello.switches):
  {_id: 'presence_status'}  written only by the ear (ear/presence.py): face_at, voice_at, owner_at, owner_what,
                            faces (M12 faces in view now), at.
  {_id: 'pause'}            written only by mind's Controller: on, why, reasons, since, away_since, budget_until,
                            last_presence, presence_by, owner_at, owner_what, budget {...}, override_day,
                            warned_day, away_min, at.
Run log: switch.pause (paused, or the set of reasons changed while paused), switch.resume (no reason left; extra.why =
what lifted it: face / voice / owner:<what> / midnight / nine / cap), budget.warn (once a day at 80 %).
"""
import datetime
import threading
import time
from zoneinfo import ZoneInfo

from .runlog import hello_log

DOC = 'pause'
PRESENCE = 'presence_status'
BJ = ZoneInfo('Asia/Shanghai')
DEFAULTS = {'away_min': 10, 'cap_usd_per_day': 10.0, 'usd_cny': 7.2}
WARN_RATIO = 0.8
TICK_S = 5
SPEND_EVERY_MS = 30_000          # the ledger is read at most this often (and at once after a day change)
PRESENCE_FRESH_MS = 150_000      # faces "in view" count only while the ear's report is this fresh (it writes every 60 s)
WRITE_EVERY_MS = 60_000          # the pause document is rewritten at least this often (spend, at)
OWNER_SOURCES = ('asr', 'touch', 'select', 'quick')    # hello's reports that are an owner's input (not boot / timeout)
REASON_ZH = {'away': '没人', 'budget': '超出今天的花费上限'}


def _log(msg):
    print(f'mind pause: {msg}', flush=True)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def config(s):
    """{away_ms, away_min, cap_usd, cap_cny, usd_cny} from settings (pause.*), defaults for anything missing. The cap is
    set in USD; the ledger mixes CNY and USD, so the comparison itself stays in CNY (cap_cny = cap_usd × usd_cny). An
    old pause.cap_cny_per_day is not read."""
    p = {**DEFAULTS, **{k: v for k, v in ((s or {}).get('pause') or {}).items() if _is_num(v)}}
    off = ((s or {}).get('pause') or {}).get('away_off_until')
    cap_usd, rate = float(p['cap_usd_per_day']), float(p['usd_cny'])
    return {'away_min': p['away_min'], 'away_ms': int(p['away_min'] * 60_000), 'cap_usd': cap_usd,
            'cap_cny': round(cap_usd * rate, 4), 'usd_cny': rate,
            # owner 2026-11-18 (a night of development): no away pause until this epoch ms; the budget cap stays
            'away_off_until': int(off) if _is_num(off) else None}


def _usd(cny, b):
    rate = b.get('usd_cny') if _is_num(b.get('usd_cny')) and b.get('usd_cny') > 0 else DEFAULTS['usd_cny']
    return round(cny / rate, 4) if _is_num(cny) else None


def day_of(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, BJ).strftime('%Y-%m-%d')


def next_midnight(ms):
    d = datetime.datetime.fromtimestamp(ms / 1000, BJ).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((d + datetime.timedelta(days=1)).timestamp() * 1000)


def spend_cny(ledger, usd_cny):
    """A ledger() result -> (CNY total with USD converted, records whose price is unknown). Other currencies: none."""
    total = (ledger or {}).get('total') or {}
    cny = float(total.get('CNY') or 0) + float(total.get('USD') or 0) * usd_cny
    return round(cny, 4), int((ledger or {}).get('unknown_n') or 0)


# ---- readers (every paid path; separate processes read the document) ----------------------------------------------

def read(db):
    """The pause document, or None (none yet, or the database unreadable: never raises)."""
    try:
        return db['switches'].find_one({'_id': DOC})
    except Exception:                            # noqa: BLE001 - a failed read is "not paused": the switches decide
        return None


def _budget_active(doc, now_ms):
    until = doc.get('budget_until')
    return 'budget' in (doc.get('reasons') or []) and (not isinstance(until, int) or now_ms < until)


def active(doc, now_ms=None):
    """Is hello paused by this document? A budget pause ends by itself at its budget_until (Beijing midnight), even
    when mind is not there to lift it."""
    if not isinstance(doc, dict) or doc.get('on') is not True:
        return False
    now = int(time.time() * 1000) if now_ms is None else now_ms
    return 'away' in (doc.get('reasons') or []) or _budget_active(doc, now)


def paused(db, now_ms=None):
    return active(read(db), now_ms)


def away(db, now_ms=None):
    """Paused because nobody is there (a budget-only pause is not this)."""
    doc = read(db)
    return isinstance(doc, dict) and doc.get('on') is True and 'away' in (doc.get('reasons') or [])


def budget_stop(db, now_ms=None):
    """The budget pause (the one daily cap): model calls refuse while it holds."""
    doc = read(db)
    now = int(time.time() * 1000) if now_ms is None else now_ms
    return isinstance(doc, dict) and doc.get('on') is True and _budget_active(doc, now)


# ---- the decision (pure) -------------------------------------------------------------------------------------------

def _latest(*pairs):
    """(ms, by) with the largest ms among the pairs whose ms is an int; (None, None) when none is."""
    best = (None, None)
    for ms, by in pairs:
        if isinstance(ms, int) and not isinstance(ms, bool) and (best[0] is None or ms > best[0]):
            best = (ms, by)
    return best


def decide(prev, *, now, cfg, presence, owner_at, owner_what, spend, unknown_n, started_at):
    """-> (new document, events). prev: the last document (or {}); presence: the ear's presence_status (or None);
    owner_at / owner_what: mind's own latest owner input; spend: today's CNY (None = unknown: no budget pause).
    events: [(kind, fields)] for the run log."""
    prev = dict(prev or {})
    day = day_of(now)
    pr = presence or {}
    fresh = isinstance(pr.get('at'), int) and now - pr['at'] <= PRESENCE_FRESH_MS
    faces = pr.get('faces') if fresh and isinstance(pr.get('faces'), int) else 0
    owner_ear = pr.get('owner_what') or 'robot'
    last, by = _latest((prev.get('last_presence'), prev.get('presence_by')), (pr.get('face_at'), 'face'),
                       (pr.get('voice_at'), 'voice'), (pr.get('owner_at'), f'owner:{owner_ear}'),
                       (owner_at, f'owner:{owner_what or "?"}'), (now if faces > 0 else None, 'face'))
    if last is None:
        last, by = started_at, 'start'
    away = now - last >= cfg['away_ms'] and not (cfg.get('away_off_until') and now < cfg['away_off_until'])
    override = prev.get('override_day') == day
    over = spend is not None and spend >= cfg['cap_cny']
    budget = over and not override
    reasons = (['budget'] if budget else []) + (['away'] if away else [])
    was = list(prev.get('reasons') or []) if prev.get('on') is True else []
    warn = spend is not None and spend >= WARN_RATIO * cfg['cap_cny']
    doc = {'on': bool(reasons), 'why': reasons[0] if reasons else None, 'reasons': reasons,
           'last_presence': last, 'presence_by': by, 'owner_at': owner_at, 'owner_what': owner_what,
           'away_min': cfg['away_min'], 'override_day': prev.get('override_day'), 'warned_day': prev.get('warned_day'),
           'budget': {'day': day, 'spent_cny': spend, 'cap_cny': cfg['cap_cny'], 'usd_cny': cfg['usd_cny'],
                      'unknown_n': unknown_n, 'warn': warn, 'over': over, 'override': override and over},
           'at': now}
    doc['since'] = (prev.get('since') if was else now) if reasons else None
    doc['away_since'] = (prev.get('away_since') if 'away' in was and prev.get('away_since') else now) if away else None
    doc['budget_until'] = next_midnight(now) if budget else None
    events = []
    money = {'spent_cny': spend, 'cap_cny': cfg['cap_cny']}
    if warn and doc['warned_day'] != day:
        doc['warned_day'] = day
        events.append(('budget.warn', {'by': 'rule', 'sum': f'今天已花 ${spend / cfg["usd_cny"]:.2f} / 上限 ${cfg["cap_cny"] / cfg["usd_cny"]:.2f}（80%）',
                                       'extra': {**money, 'day': day}}))
    if reasons and reasons != was:
        added = [r for r in reasons if r not in was]
        lifted = [r for r in was if r not in reasons]
        events.append(('switch.pause', {'by': 'rule', 'sum': '暂停：' + '、'.join(REASON_ZH[r] for r in reasons),
                                        'extra': {'why': reasons[0], 'reasons': reasons, 'added': added, 'lifted': lifted,
                                                  'lifted_by': _lifted_by(lifted, by, day, prev, cfg) if lifted else None,
                                                  'away_since': doc['away_since'], 'last_presence': last, **money}}))
    elif was and not reasons:
        why = _lifted_by(was, by, day, prev, cfg)
        since = prev.get('since') if isinstance(prev.get('since'), int) else now
        events.append(('switch.resume', {'by': 'rule', 'sum': f'恢复（{why}）',
                                         'extra': {'why': why, 'was': was, 'paused_ms': now - since, **money}}))
    return doc, events


def _lifted_by(lifted, presence_by, day, prev, cfg):
    """What ended the pause: the presence that came back for away; midnight / nine / cap for budget."""
    if 'away' in lifted:
        return presence_by
    old = prev.get('budget') or {}
    if old.get('day') != day:
        return 'midnight'
    if prev.get('override_day') == day:
        return 'nine'
    if _is_num(old.get('cap_cny')) and cfg['cap_cny'] > old['cap_cny']:
        return 'cap'
    return 'budget'


# ---- the controller (mind) -----------------------------------------------------------------------------------------

class Controller:
    """mind's owner of the pause. tick() every TICK_S (run() on a thread); note_owner() from the API on every explicit
    owner input (it ticks at once, so a pause lifts before the reply). paused() / view() are memory reads.
    ledger_fn(day) -> hello_log_query.ledger(db, day) (None: spend unknown, no budget pause)."""

    def __init__(self, db, *, settings_fn, ledger_fn=None, clock_ms=lambda: int(time.time() * 1000), emit=None):
        self.db, self.settings_fn, self.ledger_fn, self.clock_ms = db, settings_fn, ledger_fn, clock_ms
        self.emit = emit or (lambda kind, **f: hello_log.emit(kind, **f))
        self._lock = threading.RLock()
        self.started_at = clock_ms()
        prev = read(db) or {}
        prev.pop('_id', None)
        self._doc = prev
        self._owner_at, self._owner_what = prev.get('owner_at'), prev.get('owner_what')
        self._spend = (None, 0)
        self._spend_at = self._spend_day = self._written_at = None
        self._written = None

    # ---- reads ----
    def doc(self):
        with self._lock:
            return dict(self._doc)

    def paused(self, now_ms=None):
        return active(self.doc(), self.clock_ms() if now_ms is None else now_ms)

    def budget_stopped(self, now_ms=None):
        d = self.doc()
        return d.get('on') is True and _budget_active(d, self.clock_ms() if now_ms is None else now_ms)

    def view(self, now_ms=None):
        """What the 观察台 shows: paused and why, since when, today's spend against the cap."""
        now = self.clock_ms() if now_ms is None else now_ms
        return view(self.doc(), now)

    # ---- inputs ----
    def note_owner(self, what, *, nine=False, now_ms=None):
        """An explicit owner input (lifts an away pause at once). nine: the owner's 9 (hello's quick page / ASR, the
        page's 9 / billing on): it also lifts today's budget pause (for the rest of the day)."""
        now = self.clock_ms() if now_ms is None else now_ms
        with self._lock:
            self._owner_at, self._owner_what = now, str(what)[:32]
            if nine:
                cfg = config(self.settings_fn())
                spend = self._spend[0]
                if spend is not None and spend >= cfg['cap_cny']:
                    self._doc = {**self._doc, 'override_day': day_of(now)}
            self._tick(now, force_write=True)

    # ---- the loop ----
    def tick(self, now_ms=None):
        now = self.clock_ms() if now_ms is None else now_ms
        with self._lock:
            return self._tick(now)

    def _refresh_spend(self, now, cfg):
        day = day_of(now)
        if self.ledger_fn is None:
            self._spend = (None, 0)
            return
        if self._spend_day == day and self._spend_at is not None and now - self._spend_at < SPEND_EVERY_MS:
            return
        try:
            self._spend = spend_cny(self.ledger_fn(day), cfg['usd_cny'])
        except Exception as e:                   # noqa: BLE001 - keep the last value of this day, try again later
            _log(f'ledger unreadable: {type(e).__name__}')
            if self._spend_day != day:
                self._spend = (None, 0)
        self._spend_at, self._spend_day = now, day

    def _tick(self, now, force_write=False):
        cfg = config(self.settings_fn())
        self._refresh_spend(now, cfg)
        try:
            presence = self.db['switches'].find_one({'_id': PRESENCE})
        except Exception:                        # noqa: BLE001 - no presence report: only mind's own inputs count
            presence = None
        doc, events = decide(self._doc, now=now, cfg=cfg, presence=presence, owner_at=self._owner_at,
                             owner_what=self._owner_what, spend=self._spend[0], unknown_n=self._spend[1],
                             started_at=self.started_at)
        for kind, fields in events:
            try:
                self.emit(kind, **fields)
            except Exception:                    # noqa: BLE001 - logging never touches the pause
                pass
            if kind != 'budget.warn':
                _log(f'{kind}: {fields.get("sum")}')
        self._doc = doc
        key = {k: v for k, v in doc.items() if k not in ('at', 'last_presence', 'presence_by', 'owner_at', 'owner_what')}
        if force_write or events or key != self._written or self._written_at is None or \
                now - self._written_at >= WRITE_EVERY_MS:
            try:
                self.db['switches'].update_one({'_id': DOC}, {'$set': doc}, upsert=True)
                self._written, self._written_at = key, now
            except Exception as e:               # noqa: BLE001 - kept in memory; the next tick writes it again
                _log(f'pause write failed: {type(e).__name__}')
        return doc

    def run(self, sleep=time.sleep):
        while True:
            try:
                self.tick()
            except Exception as e:               # noqa: BLE001 - keep running
                _log(f'tick failed: {type(e).__name__}: {str(e)[:160]}')
            sleep(TICK_S)


def view(doc, now_ms):
    """The page's pause block from a pause document (also used without a Controller, straight from the database)."""
    doc = doc if isinstance(doc, dict) else {}
    b = doc.get('budget') or {}
    on = active(doc, now_ms)
    reasons = [r for r in (doc.get('reasons') or []) if on and (r != 'budget' or _budget_active(doc, now_ms))]
    return {'paused': on, 'why': reasons[0] if reasons else None, 'reasons': reasons, 'since': doc.get('since') if on else None,
            'away_since': doc.get('away_since') if 'away' in reasons else None,
            'budget_until': doc.get('budget_until') if 'budget' in reasons else None,
            'last_presence': doc.get('last_presence'), 'presence_by': doc.get('presence_by'),
            'away_min': doc.get('away_min'), 'day': b.get('day'), 'spent_cny': b.get('spent_cny'),
            'cap_cny': b.get('cap_cny'), 'spent_usd': _usd(b.get('spent_cny'), b), 'cap_usd': _usd(b.get('cap_cny'), b),
            'warn': bool(b.get('warn')), 'over': bool(b.get('over')),
            'override': bool(b.get('override')), 'unknown_n': b.get('unknown_n') or 0, 'at': doc.get('at'),
            'known': bool(doc)}
