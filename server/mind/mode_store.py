"""hello's modes: the store around the pure state machine (plan task A2; spec 2026-11-14-hello-modes-design-v2).

mind is the only place that changes the mode. ModeStore keeps the current state in memory and in
hello.mode {_id: 'current'}, logs every input to hello.mode_log, and is the only writer of the master switch
(billing), so every change of either bumps `seq` and ear / the robot see it.

apply() for every input, in this order:
  1. device inputs (device + boot + dev_seq all given) are deduplicated: a repeat returns the first result and
     does nothing (also after a mind restart: the result is kept in the log row). A repeat that arrives while the
     first is still running waits for it.
  2. age_ms > 3000 -> stale, nothing done.
  3. routing: digit -> preset_fn (A3 apply_preset), billing -> billing_fn (A3 Billing on/off), both outside the
     lock (they may call apply / set_billing themselves); everything else -> modes.transition under the lock.
  4. paid effects (say_round, prefetch_on) pass the pre-check: billing off -> billing_off, then guard(effect, meta);
     a returned code drops the effect and becomes result.code. Mode changes are never blocked.
  5. seq += 1 and hello.mode written only when the state changed; one mode_log row for every input.
Then, outside the lock, the on_change hooks run in order with the effects: they only carry them out (effects are
data from modes.transition, executed by name; ModeStore interprets none of them beyond the pre-check).

Superseded paid effects (review I1): every later mode change and every billing change bumps seq, so before each
hook a paid effect is dropped when seq() no longer equals the committed snapshot's seq. Hooks that start paid work
must also hand it `is_current=lambda: store.seq() == new_snapshot['seq']` (speak.say_round checks it again after
writing and before speaking), because the input that replaces it can land after the hook started.

Lock contract (review I2): one process-wide lock covers each commit, including its Mongo calls.
  - speaking_fn is called before the lock is taken; guard is called under it. Both must be in-memory reads that take
    no lock (e.g. speak._SAY_LOCK.locked() is fine; speak._ENQUEUE_LOCK, HTTP to soul-tts or a Billing lock are not)
    and never call ModeStore.
  - Callers must not hold any lock of their own while calling apply / set_billing / robot_event / tick (A3's
    Billing calls set_billing with no lock held). preset_fn / billing_fn / hooks run outside the lock and may call
    ModeStore, but preset_fn must not re-enter apply with the device key it is handling (raises ValueError).

Reads (current, seq, billing, robot) are memory only and take no lock; seq() / billing() read the snapshot itself,
so they never disagree with current().
"""
import collections
import copy
import threading
import time

from .mode_table import MODES, SUBTITLE_DIRS
from .modes import SOURCES, result, snapshot as _snapshot, transition

STALE_MS = 3000                  # device inputs older than this are dropped (DualKey resends after a reconnect)
SETTINGS_TIMEOUT_MS = 300_000    # settings closes itself after 5 minutes without input
PAID = ('say_round', 'prefetch_on')
FIELDS = ('mode', 'dir', 'host', 'return_to')
BOOT_STATE = {'mode': 'talk', 'dir': None, 'host': None, 'return_to': None}
DOC_ID = 'current'
_RECENT = 1024                   # device-input results kept in memory (older ones are looked up in mode_log)
_WAIT_S = 10                     # a duplicate waits this long for the first copy before answering busy
_LOCK = threading.RLock()        # one mode per mind process


def _log(msg):
    print(f'mind mode: {msg}', flush=True)


def ensure_indexes(db):
    db['mode_log'].create_index([('at', 1)])
    db['mode_log'].create_index([('device', 1), ('boot', 1), ('dev_seq', 1)])


def valid_state(st):
    """True only for states the machine can be in: no host on event / settings / idle, return_to only (and
    always) in settings and never settings / event itself, dir exactly when subtitle shows (or will on return)."""
    if not isinstance(st, dict) or any(k not in st for k in FIELDS):
        return False
    mode, d, host, rt = (st[k] for k in FIELDS)
    if mode not in MODES or host not in (None, 'event') or d not in (None, *SUBTITLE_DIRS) or \
            rt not in (None, *MODES):
        return False
    if host == 'event' and mode in ('event', 'settings', 'idle'):
        return False
    if (rt is not None) != (mode == 'settings') or rt in ('settings', 'event'):
        return False
    return (d is not None) == (mode == 'subtitle' or (mode == 'settings' and rt == 'subtitle'))


def _undefined_preset(n, **kw):
    return {'result': result('undefined_preset')}


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


class ModeStore:
    def __init__(self, db, *, guard=None, speaking_fn=lambda: False, clock_ms=lambda: int(time.time() * 1000),
                 preset_fn=None, billing_fn=None):
        """guard(effect, meta) -> code | None for say_round / prefetch_on; speaking_fn() -> stop_speech has
        something to cancel; preset_fn(n, *, source, now_ms, meta) -> {result, ...extra}; billing_fn(on, *, source,
        now_ms, meta) must end in set_billing (default: set_billing alone). preset_fn / billing_fn may be set after
        construction (A3's Billing needs this store first). guard and speaking_fn: lock-free in-memory reads that
        never call ModeStore (see the module docstring's lock contract)."""
        self.db = db
        self.coll = db['mode']
        self.log_coll = db['mode_log']
        self.guard = guard
        self.speaking_fn = speaking_fn
        self.clock_ms = clock_ms
        self.preset_fn = preset_fn or _undefined_preset
        self.billing_fn = billing_fn
        self.on_change = []
        self._recent = collections.OrderedDict()
        self._pending = {}
        self._robot = {'connected': False, 'boot': None, 'conn': None, 'applied_seq': None, 'at': None}
        with _LOCK:
            self._load()

    # ---- reads (memory only) ----
    def current(self):
        return copy.deepcopy(self._snap)

    def seq(self):
        return self._snap['seq']

    def billing(self):
        return self._snap['billing']

    def robot(self):
        return dict(self._robot)

    def log(self, limit=10):
        rows = list(self.log_coll.find({}, {'_id': 0}).sort('at', -1).limit(limit))
        for r in rows:
            r.pop('_id', None)
        return rows

    # ---- persistence ----
    def _load(self):
        now = self.clock_ms()
        doc = self.coll.find_one({'_id': DOC_ID})
        self._robot_boot = doc.get('robot_boot') if doc else None
        if doc is None:
            self._reset(now, seq=1, billing=True)
            return
        st = {k: doc.get(k) for k in FIELDS}
        seq, billing = doc.get('seq'), doc.get('billing')
        if valid_state(st) and _is_int(seq) and seq >= 1 and isinstance(billing, bool):
            self._state, self._seq, self._billing = st, seq, billing
            self._since = doc.get('since') if _is_int(doc.get('since')) else now
            self._by = doc.get('by') if doc.get('by') in SOURCES else 'boot'
            self._last_input = doc.get('last_input') if _is_int(doc.get('last_input')) else self._since
            self._rebuild()
            return
        # An impossible stored state: back to the boot default (keeping a valid billing, seq still grows) and say so.
        _log(f'bad stored mode state {st!r} seq={seq!r} billing={billing!r}: back to talk')
        prev = seq if _is_int(seq) and seq >= 1 else 0
        self._reset(now, seq=prev + 1, billing=billing if isinstance(billing, bool) else True)
        self._append({'at': now, 'source': 'boot', 'device': None, 'boot': None, 'dev_seq': None, 'sid': None,
                      'input': 'load', 'value': st, 'from': st['mode'] if isinstance(st['mode'], str) else None,
                      'to': 'talk', 'code': 'bad_state', 'result': None, 'seq': self._seq, 'effects': []})

    def _reset(self, now, *, seq, billing):
        self._state, self._seq, self._billing = dict(BOOT_STATE), seq, billing
        self._since, self._by, self._last_input = now, 'boot', now
        self._rebuild()
        self._write()

    def _rebuild(self):
        self._snap = _snapshot(self._state, seq=self._seq, since=self._since, by=self._by, billing=self._billing)

    def _write(self):
        if not valid_state(self._state):
            raise ValueError(f'refusing to store impossible mode state {self._state!r}')
        doc = dict(self._state, seq=self._seq, billing=self._billing, since=self._since, by=self._by,
                   last_input=self._last_input, robot_boot=self._robot_boot)
        try:
            self.coll.update_one({'_id': DOC_ID}, {'$set': doc}, upsert=True)
        except Exception as e:          # keep working from memory; the next write stores the whole document again
            _log(f'hello.mode write failed: {type(e).__name__}')

    def _append(self, row):
        try:
            self.log_coll.insert_one(dict(row))
        except Exception as e:
            _log(f'hello.mode_log write failed: {type(e).__name__}')

    # ---- dedup ----
    def _seen(self, key):
        if key in self._recent:
            return self._recent[key]
        try:
            row = self.log_coll.find_one({'device': key[0], 'boot': key[1], 'dev_seq': key[2]})
        except Exception:
            row = None
        return row.get('result') if row and row.get('result') else None

    def _remember(self, key, res):
        self._recent[key] = copy.deepcopy(res)
        while len(self._recent) > _RECENT:
            self._recent.popitem(last=False)

    def _record(self, base, frm, res, effects, key):
        """Called under the lock: one log row (with the result, for dedup) and the in-memory dedup entry."""
        self._append(dict(base, **{'from': frm, 'to': self._state['mode'], 'code': res['code'], 'result': res,
                                   'seq': self._seq, 'effects': list(effects)}))
        if key:
            self._remember(key, res)

    # ---- inputs ----
    def apply(self, input, value=None, *, source, device=None, boot=None, dev_seq=None, age_ms=0, sid=None,
              now_ms=None):
        """-> {snapshot, result: {code, toast}, effects} (+ duplicate: True for a repeat, + extra keys from
        preset_fn such as robot)."""
        if source not in SOURCES:
            raise ValueError(f'unknown source {source!r}')
        now = self.clock_ms() if now_ms is None else now_ms
        key = (device, boot, dev_seq) if device is not None and boot is not None and dev_seq is not None else None
        if key:
            while True:
                with _LOCK:
                    hit = self._seen(key)
                    if hit is not None:
                        return {'snapshot': self.current(), 'result': copy.deepcopy(hit), 'effects': [],
                                'duplicate': True}
                    pend = self._pending.get(key)
                    if pend is None:
                        self._pending[key] = (threading.Event(), threading.get_ident())
                        break
                    ev, owner = pend
                    if owner == threading.get_ident():
                        raise ValueError(f'apply re-entered with the device key it is handling: {key!r}')
                if not ev.wait(_WAIT_S):
                    return {'snapshot': self.current(), 'result': result('busy'), 'effects': [], 'duplicate': True}
        meta = {'source': source, 'input': input, 'value': value, 'device': device, 'sid': sid, 'mode': None}
        base = {'at': now, 'source': source, 'device': device, 'boot': boot, 'dev_seq': dev_seq, 'sid': sid,
                'input': input, 'value': value}
        try:
            out, hook = self._run(input, value, source, now, age_ms, meta, base, key)
        finally:
            if key:
                with _LOCK:
                    self._pending.pop(key)[0].set()
        if hook:
            old_snap, new_snap, effects, meta, t = hook
            changed = any(old_snap[k] != new_snap[k] for k in FIELDS)
            for f in list(self.on_change):
                effects = self._still_current(effects, new_snap)
                if not (changed or effects):
                    continue
                try:
                    f(old_snap, new_snap, list(effects), dict(meta), t)
                except Exception as e:      # a hook only carries out effects; one failing must not stop the rest
                    _log(f'on_change hook {getattr(f, "__name__", f)!r} failed: {type(e).__name__}: {e}')
            out['effects'] = self._still_current(effects, new_snap)
        return out

    def _still_current(self, effects, snap):
        """Paid effects only while nothing replaced this commit (a later mode change or billing change bumps seq)."""
        return list(effects) if self.seq() == snap['seq'] else [e for e in effects if e not in PAID]

    def _run(self, input, value, source, now, age_ms, meta, base, key):
        if age_ms is not None and age_ms > STALE_MS:
            with _LOCK:
                meta['mode'] = self._state['mode']
                res = result('stale')
                self._record(base, self._state['mode'], res, [], key)
                return {'snapshot': self.current(), 'result': res, 'effects': []}, None
        if input in ('digit', 'billing'):
            return self._route(input, value, source, now, meta, base, key), None
        speaking = bool(self.speaking_fn())             # outside the lock: a point sample (review I2)
        with _LOCK:
            old, old_snap = dict(self._state), self._snap
            new, res, effects = transition(old, input, value, source=source, speaking=speaking)
            if not valid_state(new):
                raise ValueError(f'transition produced an impossible state {new!r} from {old!r} on {input!r}')
            meta['mode'] = new['mode']
            kept, code = [], None
            for e in effects:
                if e in PAID:
                    c = 'billing_off' if not self._billing else (self.guard(e, dict(meta)) if self.guard else None)
                    if c:
                        code = code or c
                        continue
                kept.append(e)
            if code:
                res = result(code)
            changed = new != old
            if changed:
                self._state, self._seq, self._since, self._by = new, self._seq + 1, now, source
            self._last_input = now
            if changed or new['mode'] == 'settings':     # settings: the 5-minute timer survives a mind restart
                self._rebuild()
                self._write()
            self._record(base, old['mode'], res, kept, key)
            snap = self._snap
            out = {'snapshot': copy.deepcopy(snap), 'result': res, 'effects': list(kept)}
            hook = (copy.deepcopy(old_snap), copy.deepcopy(snap), list(kept), dict(meta), now) if changed or kept else None
            return out, hook

    def _route(self, input, value, source, now, meta, base, key):
        """digit / billing: run the injected function outside the lock (it may apply modes or set billing itself),
        then log one row for this input."""
        with _LOCK:
            frm = self._state['mode']
            meta['mode'] = frm
            self._last_input = now
        extra = {}
        if input == 'digit':
            if not (_is_int(value) and 0 <= value <= 9):
                res = result('unknown_input')
            else:
                r = self.preset_fn(value, source=source, now_ms=now, meta=dict(meta)) or {}
                res = r.get('result') or result('ok')
                extra = {k: v for k, v in r.items() if k not in ('result', 'snapshot', 'effects')}
        elif not isinstance(value, bool):
            res = result('unknown_input')
        else:
            was = self._billing
            fn = self.billing_fn or (lambda on, *, source, now_ms, meta: self.set_billing(on, source=source, now_ms=now_ms))
            r = fn(value, source=source, now_ms=now, meta=dict(meta))
            res = r['result'] if isinstance(r, dict) and isinstance(r.get('result'), dict) else \
                result('ok' if self._billing != was else 'already')
        with _LOCK:
            self._record(base, frm, res, [], key)
            return {'snapshot': self.current(), 'result': res, 'effects': [], **extra}

    def set_billing(self, on, *, source, now_ms=None):
        """The master switch's only writer: a change bumps seq, is stored and logged (input = 'billing')."""
        if not isinstance(on, bool):
            raise ValueError(f'billing must be True / False, not {on!r}')
        if source not in SOURCES:
            raise ValueError(f'unknown source {source!r}')
        now = self.clock_ms() if now_ms is None else now_ms
        with _LOCK:
            if on != self._billing:
                self._billing, self._seq = on, self._seq + 1
                self._rebuild()
                self._write()
                mode = self._state['mode']
                self._append({'at': now, 'source': source, 'device': None, 'boot': None, 'dev_seq': None,
                              'sid': None, 'input': 'billing', 'value': on, 'from': mode, 'to': mode, 'code': 'ok',
                              'result': result('ok'), 'seq': self._seq, 'effects': []})
            return self.current()

    def robot_event(self, boot, conn, event, applied_seq=None, *, now_ms=None):
        """/mouth connection events from ear. hello: conn becomes the current connection, a new boot id = the robot
        rebooted -> apply('boot'). applied / closed count only for the current connection (a late close of an old
        connection is ignored). The connection is not persisted: after a mind restart the first `applied` adopts
        its conn (same boot check as hello), so the robot shows online without reconnecting (review I3)."""
        now = self.clock_ms() if now_ms is None else now_ms
        rebooted = False
        with _LOCK:
            r = self._robot
            adopt = event == 'applied' and r['conn'] is None   # after a mind restart: /mouth on ear never dropped
            if event == 'hello' or adopt:
                r.update(connected=True, boot=boot, conn=conn, applied_seq=applied_seq if adopt else None, at=now)
                if boot != self._robot_boot:
                    self._robot_boot, rebooted = boot, True
                    self._write()
            elif event in ('applied', 'closed'):
                if conn == r['conn']:
                    if event == 'applied':
                        r.update(applied_seq=applied_seq, at=now)
                    else:
                        r.update(connected=False, at=now)
            else:
                raise ValueError(f'unknown robot event {event!r}')
        if rebooted:
            self.apply('boot', None, source='boot', now_ms=now)
        return self.current()

    def tick(self, now_ms=None):
        """Main-loop tick: settings with no input for more than 5 minutes closes (back to return_to)."""
        now = self.clock_ms() if now_ms is None else now_ms
        with _LOCK:
            due = self._state['mode'] == 'settings' and now - self._last_input > SETTINGS_TIMEOUT_MS
        if due:
            return self.apply('timeout', None, source='timeout', now_ms=now)
        return None
