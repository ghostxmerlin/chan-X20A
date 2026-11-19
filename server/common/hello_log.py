"""hello run/audit log writer (2026-11-16), shared by ear, mind, eye, notes, memory and tts.

    import hello_log
    hello_log.init("mind", db_uri=MONGO_URI, spool_dir="~/soul-spike/log-spool")   # once per process
    hello_log.emit("call.eye", trace=t, ok=True, ms=812, model="...", sum="一行摘要",
                   body={"system": prompt, "input": messages, "output": text})
    with hello_log.scope("call.notes", trace=hello_log.new_trace(), ref={"minute": m}):
        ...                                     # emit(None, ...) in here takes kind, trace and ref from the scope
    hello_log.error(exc, where="mind.loop")    # svc.error from a top-level handler (≤ 1 per message a minute)

Lifecycle (init(lifecycle=True), the default): svc.start {code_version, args (redacted), pid} as seq 1, a
svc.beat every minute {counts per kind this minute, dropped, rejected, spool_errors, db_errors, backlog_lines,
errors_throttled, state from set_beat_state(fn)}, and svc.stop {reason: "sigterm"|"exit"} from a SIGTERM handler (main thread
only, chains to the previous handler; install_signal=False leaves signals to the caller, who calls
get().on_stop("sigterm")) or atexit, followed by flush(3).

Two layers in the Mongo database "hello", one pair of tables per Beijing month:
  log_YYYYMM      flow: one small record per event. _id "{svc}:{run}:{seq}", at (ms), svc, run, seq,
                  kind, plus the caller's envelope fields (spec §7.3); `body` holds {name: sha256}.
  logbody_YYYYMM  bodies: {_id: sha256, text}; full prompts / inputs / outputs, deduplicated per month.

emit() only snapshots the fields and puts them on a bounded queue; it never blocks and never raises.
One daemon thread cleans each record (redaction, no images, size caps), appends it to the local spool
<spool>/<svc>-YYYYMMDD.jsonl (fsync), then ships the spool to Mongo and records how far it got in
<svc>-YYYYMMDD.offset (its mtime = when it last advanced). Records queued while Mongo is slow still
reach the spool between database operations. If Mongo is down or slow (5 s timeouts) the lines wait
in the spool and are replayed later, also after a restart; replay is idempotent (duplicate _id =
already shipped). A record Mongo keeps refusing while others go through (or that cannot be encoded
while Mongo answers a ping) is counted as rejected and skipped, so it cannot block the spool; any
other failure (bad URI, index creation, disk full, auth, a refusal of everything) keeps everything. If the spool folder is unwritable records go straight to
Mongo. A spool file is deleted 7 days after it was fully shipped. stdlib only; pymongo is imported
when a real connection (or its UpdateOne) is first needed.
"""
import atexit
import contextlib
import contextvars
import functools
import hashlib
import itertools
import json
import logging
import os
import queue
import re
import secrets
import signal
import sys
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path

DB_NAME = os.environ.get("SOUL_DB") or "hello"  # 2026-11-19 night: a second stack (the Mac stand-in) runs on its own database
QUEUE_MAX = 20000
BATCH_MAX = 500
BATCH_WAIT_S = 0.5
BODY_MAX = 4 * 1024 * 1024           # bytes of one body text
FLOW_STR_MAX = 64 * 1024             # bytes of one string in a flow record (big content belongs in body)
BODY_CACHE = 5000                    # recently shipped (month, sha) pairs
SPOOL_KEEP_DAYS = 7
SHIP_TRIES = 3                       # failures of one chunk / one record before it is split / rejected
REFUSE_ALL = 5                       # first records of a split pass failing alike = the DB refuses everything
RESERVED = ("_id", "svc", "run", "seq")
BEAT_MS = 60000                      # svc.beat period on the logger's clock
COUNT_KINDS_MAX = 256                # distinct kinds counted per beat; the rest add up under "other"
STOP_FLUSH_S = 3
ERROR_TEXT_MAX = 300                 # svc.error err: "Type: message", one line, at most this many characters
ERROR_SCAN_MAX = 8000                # characters of an error text redacted before the cut (bounds the caller's cost)
ERROR_EVERY_MS = 60000               # at most one svc.error per distinct (where, err) per this long
ERROR_KEYS_MAX = 256                 # distinct (where, err) remembered for the throttle
_BEAT_FIELDS = {"counts", "state", "dropped", "rejected", "spool_errors", "db_errors", "backlog_lines",
                "errors_throttled"}

log = logging.getLogger("hello_log")
_current = None
_scope = contextvars.ContextVar("hello_log_scope", default=None)    # dict of fields, or None
_sigterm_prev = None                 # the SIGTERM handler ours replaced (chained to)
_atexit_hooked = False
_EPOCH_BJ = datetime(1970, 1, 1) + timedelta(hours=8)        # naive Beijing wall clock at epoch (no DST)
_SECRET_SEGMENTS = {"key", "apikey", "token", "secret", "password", "passwd", "pwd", "authorization", "auth",
                    "uri", "dsn", "cookie", "credential", "credentials"}
_SECRET_SUFFIXES = ("token", "secret", "password", "passwd", "apikey", "accesskey", "credential")
_SECRET_PAIRS = {("api", "key"), ("session", "token")}
_NAME_PARTS = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/?#@\s]+@")
# sk- keys: 20+ characters anywhere (real keys are long; also after an escaped \n or %0A), 8+ after a non-alphanumeric
# (so the end of task-finished stays). Bearer tokens: one with a digit, 16+ characters anywhere (%20Bearer …), 8+
# after a non-alphanumeric; "bearer responsibility" (no digit) stays.
_FREE_SK = re.compile(r"sk-[A-Za-z0-9_-]{20,}|(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{8,}")
_FREE_BEARER = re.compile(r"(?i)bearer\s+(?=[A-Za-z0-9._~+/=-]*\d)[A-Za-z0-9._~+/=-]{16,}"
                          r"|(?<![A-Za-z0-9])bearer\s+(?=[A-Za-z0-9._~+/=-]*\d)[A-Za-z0-9._~+/=-]{8,}")
_URL_PARAM_NAME = re.compile(r"[?&;]([^=&#;?\s\"']+)=")
_PARAM_END = re.compile(r"[&#;?\s\"']")
_SECRET_PARAM_END = re.compile(r"[&#\s\"']")       # a secret value is masked through ; and ? too
_DATA_URL_WHOLE = re.compile(r"data:([\w.+-]+)/[\w.+-]+[;,]")
_DATA_URL_INLINE = re.compile(r"data:([\w.+-]+)/[\w.+-]+(?:;[\w.+-]+=[\w.+-]+)*;base64,[A-Za-z0-9+/=\r\n]*")
_SPOOL_NAME = r"^{}-(\d{{8}})\.jsonl$"
_INT64 = 2 ** 63
# errors that mean "the database is unreachable", not "this record is bad" (pymongo names, no import)
_UNREACHABLE = {"ConnectionFailure", "AutoReconnect", "ServerSelectionTimeoutError", "NetworkTimeout",
                "NotPrimaryError", "WaitQueueTimeoutError"}
# server error codes that are about the database, never about one document (also inside writeErrors):
# 6 HostUnreachable, 7 HostNotFound, 13 Unauthorized, 18 AuthenticationFailed, 24 LockTimeout, 46 LockBusy,
# 50 MaxTimeMSExpired, 64 WriteConcernFailed, 89 NetworkTimeout, 91 ShutdownInProgress, 189 PrimarySteppedDown,
# 262 ExceededTimeLimit, 9001 SocketException, 10107 NotWritablePrimary, 11600 InterruptedAtShutdown,
# 11602 InterruptedDueToReplStateChange, 13435 NotPrimaryNoSecondaryOk, 13436 NotPrimaryOrSecondary,
# 14031 OutOfDiskSpace
_DB_WIDE_CODES = {6, 7, 13, 18, 24, 46, 50, 64, 89, 91, 189, 262, 9001, 10107, 11600, 11602, 13435, 13436, 14031}
# errors raised on our side while encoding one record (bson / pymongo names, no import): that record is bad
_RECORD_FAULTS = {"InvalidDocument", "InvalidStringData", "InvalidBSON", "DocumentTooLarge", "BSONError",
                  "TypeError", "ValueError", "OverflowError", "UnicodeError", "RecursionError"}
# create_index: an index on these keys is already there (other name / options) = good enough
_INDEX_EXISTS_CODES = {68, 85, 86}


class _NoDatabase(Exception):
    pass


# ---------------------------------------------------------------- helpers (pure)

def _bj(at_ms, fmt):
    return (_EPOCH_BJ + timedelta(milliseconds=int(at_ms))).strftime(fmt)


def month_of(at_ms):
    """'YYYYMM' of a ms timestamp in Beijing time (the month suffix of log_ / logbody_)."""
    return _bj(at_ms, "%Y%m")


def key_fp(key):
    """Fingerprint of an API key: its first 7 characters ('sk-xTVl'); never the whole key."""
    if key is None or key == "":
        return None
    return str(key)[:7]


def _utf8(s):
    """s with lone surrogates replaced, so it always encodes as UTF-8 (JSON lines, BSON)."""
    if s.isascii():
        return s
    try:
        s.encode("utf-8")
        return s
    except UnicodeEncodeError:
        return s.encode("utf-8", "replace").decode("utf-8")


def _omitted(match_text, mime_major):
    kind = mime_major if mime_major in ("image", "audio", "video") else "data"
    return f"[{kind} omitted, {len(match_text.encode('utf-8', 'replace'))} bytes]"


def _cap(text, limit):
    raw = text.encode("utf-8", "replace")
    if len(raw) <= limit:
        return text
    return raw[:limit].decode("utf-8", "ignore") + f"…[truncated, {len(raw)} bytes total]"


def _clean_str(s, limit):
    m = _DATA_URL_WHOLE.match(s)
    if m:
        return _omitted(s, m.group(1))
    if "base64," in s:
        s = _DATA_URL_INLINE.sub(lambda mm: _omitted(mm.group(0), mm.group(1)), s)
    if "=" in s:
        s = _mask_params(s)
    if "@" in s and "://" in s:
        s = _URL_USERINFO.sub(r"\1***@", s)
    if "sk-" in s:
        s = _FREE_SK.sub("sk-***", s)
    s = _FREE_BEARER.sub("Bearer ***", s)
    if limit and len(s) * 4 > limit:
        s = _cap(s, limit)
    return s


def _mask_params(s):
    """URL query values whose parameter name is secret become '***'."""
    out, i = [], 0
    while True:
        m = _URL_PARAM_NAME.search(s, i)
        if not m:
            break
        secret = _secret_name(m.group(1))
        end = (_SECRET_PARAM_END if secret else _PARAM_END).search(s, m.end())
        stop = end.start() if end else len(s)
        out.append(s[i:m.end()] + "***" if secret else s[i:stop])
        i = stop
    out.append(s[i:])
    return "".join(out)


def _secret_value(s):
    return s.startswith("sk-") or s[:7].lower() == "bearer "


@functools.lru_cache(maxsize=4096)
def _secret_name(name):
    """api_key, apiKey, X-Api-Key, MONGO_URI, tokenValue, sessionToken, auth, githubtoken, dbpassword …
    but not keyword, keys, tokens, maxtokens, prompt_tokens, usage, session."""
    parts = [p.lower() for chunk in re.split(r"[_\-.\s]+", name) for p in _NAME_PARTS.findall(chunk)]
    if any(p in _SECRET_SEGMENTS or p.endswith(_SECRET_SUFFIXES) for p in parts):
        return True
    return any(pair in _SECRET_PAIRS for pair in zip(parts, parts[1:]))


def _clean(obj, limit=0, depth=0, masked=False):
    """JSON-safe copy: secrets masked, data: URLs and bytes replaced by a size note, odd objects as repr,
    strings capped at `limit` bytes (0 = no cap). Under a secret name every string leaf is '***'."""
    if obj is None or isinstance(obj, (bool, float)):
        return obj
    if isinstance(obj, int):
        return obj if -_INT64 <= obj < _INT64 else str(obj)
    if masked and not isinstance(obj, (dict, list, tuple)):
        return "***"
    if isinstance(obj, str):
        return "***" if _secret_value(obj) else _clean_str(_utf8(obj), limit)
    if depth > 40:
        return "[too deep]"
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            name = _utf8(k if isinstance(k, str) else str(k))
            out[name] = _clean(v, limit, depth + 1, masked or _secret_name(name))
        return out
    if isinstance(obj, (list, tuple)):
        return [_clean(v, limit, depth + 1, masked) for v in obj]
    if isinstance(obj, (bytes, bytearray, memoryview)):
        return f"[bytes omitted, {len(bytes(obj))} bytes]"
    try:
        text = repr(obj)
    except Exception:
        text = f"<{type(obj).__name__} without repr>"
    return _clean_str(_utf8(text), limit)


def redact(obj):
    """Copy of obj with secrets as '***'. A key name is secret when one of its parts (split on _ - . space
    and camelCase) is key / apikey / token / secret / password / passwd / pwd / authorization / auth /
    uri / dsn / cookie / credential(s), or ends with token / secret / password / passwd / apikey /
    accesskey / credential, or two neighbours read api+key or session+token; every string under it
    becomes '***' (numbers stay). keyword, keys, tokens, maxtokens, prompt_tokens, usage, session stay.
    Strings starting with 'sk-' or 'Bearer ' become '***'; inside any text sk-… keys become 'sk-***',
    'Bearer <token>' 'Bearer ***', URL query values whose name is secret by the same rule, and
    user:pass@ are masked. data: URLs are omitted."""
    return _clean(obj)


def _snap(obj, depth=0):
    """Cheap snapshot of the containers (leaves shared) so later caller mutations don't leak in."""
    if depth > 40:
        return "[too deep]"
    if isinstance(obj, dict):
        return {k: _snap(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_snap(v, depth + 1) for v in obj]
    return obj


def _write_errors(exc):
    """writeErrors of a pymongo BulkWriteError (or anything shaped like one), else None."""
    details = getattr(exc, "details", None)
    if isinstance(details, dict) and isinstance(details.get("writeErrors"), list) \
            and not details.get("writeConcernErrors"):
        return details["writeErrors"]
    return None


def _is_dup(exc):
    errs = _write_errors(exc)
    if errs is not None:
        return all(e.get("code") == 11000 for e in errs)
    return getattr(exc, "code", None) == 11000


def _unreachable(exc):
    """Network / DB-down / auth / disk-full … (by class name or error code, never by message text)
    problems: everything waits, nothing is rejected."""
    if isinstance(exc, (OSError, ImportError, _NoDatabase)):
        return True
    if any(c.__name__ in _UNREACHABLE for c in type(exc).__mro__):
        return True
    if getattr(exc, "code", None) in _DB_WIDE_CODES:
        return True
    errs = _write_errors(exc)
    return bool(errs) and any(e.get("code") in _DB_WIDE_CODES for e in errs)


def _record_fault(exc):
    """The record itself could not be encoded (raised on our side): retrying it will never help."""
    return any(c.__name__ in _RECORD_FAULTS for c in type(exc).__mro__)


class _Upsert:
    """Stand-in for pymongo.UpdateOne when pymongo is not installed (tests)."""

    def __init__(self, flt, update, upsert=True):
        self.filter, self.update, self.upsert = flt, update, upsert


_update_one = None


def _upsert_op(digest, text):
    global _update_one
    if _update_one is None:
        try:
            from pymongo import UpdateOne as _update_one
        except ImportError:
            _update_one = _Upsert
    return _update_one({"_id": digest}, {"$setOnInsert": {"text": text}}, upsert=True)


# ---------------------------------------------------------------- the writer

class Logger:
    """One per process (see init()). Counters in stats(); everything else happens on the writer thread."""

    def __init__(self, svc, *, db=None, db_uri=None, spool_dir, code_version="", args=None, clock_ms=None,
                 lifecycle=True):
        self.svc = svc
        self.run = secrets.token_hex(4)
        self.code_version = code_version
        self.args = args or {}
        self.spool_dir = Path(os.path.expanduser(str(spool_dir)))
        self._clock = clock_ms or (lambda: int(time.time() * 1000))
        self._db, self._uri = db, db_uri
        self._q = queue.Queue(maxsize=QUEUE_MAX)
        self._seq = itertools.count(1)
        self._lock = threading.Lock()
        self._cv = threading.Condition()
        self._queued = self._shipped = self._dropped = self._rejected = 0
        self._spool_errors = self._db_errors = self._backlog = 0
        self._enqueued = self._taken = 0            # records put / taken (flush waits on these)
        self._kick_gen = self._kick_done = 0       # flush() requests / requests served by the writer
        self._pending = {}                          # spool path -> shipped offset, files that may lag
        self._direct = []                           # (flow, bodies) the spool could not take
        self._indexed = set()                       # months whose indexes this process has created
        self._cache = OrderedDict()                 # (month, sha) shipped recently
        self._fails = 0
        self._retry_at = 0.0
        self._stuck = (None, 0)                     # ((path, offset), failures) of the chunk that fails
        self._stop = False
        self._prune_day = None
        self._name_re = re.compile(_SPOOL_NAME.format(re.escape(svc)))
        self._counts = {}                           # kind -> records emitted since the last beat
        self._beat_state = None
        self._beat_lock = threading.Lock()
        self._beat_stop = threading.Event()
        self._next_beat = self._clock() + BEAT_MS
        self._stopped = False                       # svc.stop recorded
        self._err_last = OrderedDict()              # (where, err) -> logger-clock ms of its last svc.error
        self._err_throttled = 0                     # svc.error calls swallowed by the throttle
        self._bumps = {}                            # name -> count for the next svc.beat's extra (bump())
        self._lifecycle = lifecycle
        self._thread = threading.Thread(target=self._run, name=f"hello-log-{svc}", daemon=True)
        self._thread.start()
        self._beat_thread = None
        if lifecycle:
            self._put("svc.start", {"extra": {"code_version": code_version, "args": redact(self._args_dict()),
                                              "pid": os.getpid()}})
            self._beat_thread = threading.Thread(target=self._beat_loop, name=f"hello-log-beat-{svc}", daemon=True)
            self._beat_thread.start()

    # ---- caller side

    def emit(self, kind, /, **fields):
        """Fields left out (or None) are taken from the current scope(); kind None = the scope's, else "event"."""
        try:
            sc = _scope.get()
            if sc:
                for k, v in sc.items():
                    if k == "kind":
                        kind = v if kind is None else kind
                    elif fields.get(k) is None:
                        fields[k] = v
        except Exception:
            pass
        self._put("event" if kind is None else kind, fields)

    def _put(self, kind, fields):
        try:
            rec = {k: _snap(v) for k, v in fields.items() if v is not None and k not in RESERVED}
            at = rec.pop("at", None)
            if isinstance(at, bool) or not isinstance(at, (int, float)):
                at = self._clock()
            beat = kind == "svc.beat" and isinstance(rec.get("extra"), dict)
            # seq, the per-beat counts and the enqueue happen under one lock, so a beat's counts are exactly
            # the records queued with a seq between the previous beat's and its own (gaps() relies on it)
            with self._lock:
                seq = next(self._seq)
                rec.update({"_id": f"{self.svc}:{self.run}:{seq}", "at": int(at), "svc": self.svc,
                            "run": self.run, "seq": seq, "kind": kind})
                if beat:
                    rec["extra"] = dict(rec["extra"], counts=self._counts)
                try:
                    self._q.put_nowait(rec)
                except queue.Full:
                    self._dropped += 1
                    return
                if beat:
                    self._counts = {}
                self._queued += 1
                self._enqueued += 1
                if kind != "svc.beat":
                    name = kind if kind in self._counts or len(self._counts) < COUNT_KINDS_MAX else "other"
                    self._counts[name] = self._counts.get(name, 0) + 1
        except Exception:
            try:
                with self._lock:
                    self._dropped += 1
            except Exception:
                pass

    def error(self, exc, *, where=None, **fields):
        """svc.error {ok: False, err: "Type: message" (one line, ≤ 300 chars, no traceback), extra.where}
        for an unhandled exception (or a message string); at most one per distinct (where, err) a minute,
        the rest counted (svc.beat extra.errors_throttled). Never raises."""
        try:
            if isinstance(exc, BaseException):
                text = f"{type(exc).__name__}: {exc}"
            else:
                text = str(exc)
            # one line, redacted whole (a secret cut in half must not survive half-masked), then cut
            err = redact(" ".join(text[:ERROR_SCAN_MAX].split()))
            err = (err if isinstance(err, str) else "***")[:ERROR_TEXT_MAX]
            now = self._clock()
            key = (str(where), err)
            with self._lock:
                last = self._err_last.get(key)
                if last is not None and 0 <= now - last < ERROR_EVERY_MS:
                    self._err_throttled += 1
                    return
                self._err_last[key] = now
                self._err_last.move_to_end(key)
                while len(self._err_last) > ERROR_KEYS_MAX:
                    self._err_last.popitem(last=False)
            extra = dict(fields.pop("extra", None) or {})
            if where is not None:
                extra["where"] = where
            self.emit("svc.error", ok=False, err=err, extra=extra or None, **fields)
        except Exception:
            pass

    def bump(self, name, n=1):
        """Add n to a counter that the next svc.beat reports as extra[name] (then it starts again at 0), for
        events the caller records only now and then (e.g. refused links: one record, the rest counted).
        Names already in the beat (counts, state, dropped …) are ignored. Never raises."""
        try:
            if name in _BEAT_FIELDS:
                return
            with self._lock:
                self._bumps[name] = self._bumps.get(name, 0) + n
        except Exception:
            pass

    def stats(self):
        """Cumulative counts since init (queued = accepted by emit), plus lines still waiting in the spool."""
        return {"queued": self._queued, "shipped": self._shipped, "dropped": self._dropped,
                "rejected": self._rejected, "spool_errors": self._spool_errors, "db_errors": self._db_errors,
                "backlog_lines": self._backlog}

    def flush(self, timeout_s=5):
        """Wait until everything emitted so far has been through the writer and one ship attempt.
        True when it is all in Mongo (nothing left in the spool backlog, nothing lost)."""
        deadline = time.monotonic() + timeout_s
        with self._lock:
            target, dropped0 = self._enqueued, self._dropped
        with self._cv:
            self._kick_gen += 1
            gen = self._kick_gen
        self._wake()
        with self._cv:
            while self._taken < target or self._kick_done < gen:
                left = deadline - time.monotonic()
                if left <= 0 or not self._thread.is_alive():
                    return False
                self._cv.wait(min(left, 0.1))
        return self._backlog == 0 and not self._direct and self._dropped == dropped0

    def close(self, timeout_s=5):
        """Stop the writer after it has spooled what is queued (waits at most timeout_s); stops the beat."""
        self._stop = True
        self._beat_stop.set()
        self._wake()
        if timeout_s > 0 and self._thread is not threading.current_thread():
            self._thread.join(timeout_s)

    # ---- lifecycle: heartbeat and stop

    def set_beat_state(self, fn):
        """fn() -> dict of the switches' current state, called on the beat thread for every svc.beat."""
        self._beat_state = fn

    def _args_dict(self):
        args = self.args
        if not isinstance(args, (dict, list, tuple, str)) and hasattr(args, "__dict__"):
            args = vars(args)                       # argparse.Namespace
        return args

    def _beat_once(self):
        """Emit svc.beat when a minute has passed on the logger's clock. True when it did. Never raises."""
        try:
            with self._beat_lock:
                now = self._clock()
                if now < self._next_beat:
                    if self._next_beat - now > 2 * BEAT_MS:           # the clock went back
                        self._next_beat = now + BEAT_MS
                    return False
                self._next_beat += BEAT_MS
                if self._next_beat <= now:                           # slept through beats: no catch-up burst
                    self._next_beat = now + BEAT_MS
                fn = self._beat_state
                try:
                    state = fn() if fn is not None else None
                except Exception as e:
                    state = {"error": f"{type(e).__name__}: {e}"[:200]}
                st = self.stats()
                extra = {"counts": None, "state": state}          # counts filled in atomically by _put
                for k in ("dropped", "rejected", "spool_errors", "db_errors", "backlog_lines"):
                    extra[k] = st[k]
                extra["errors_throttled"] = self._err_throttled
                with self._lock:
                    bumps, self._bumps = self._bumps, {}
                extra.update(bumps)
                self._put("svc.beat", {"extra": extra})
                return True
        except Exception:
            return False

    def _beat_loop(self):
        while True:
            try:
                wait_s = min(5.0, max(0.2, (self._next_beat - self._clock()) / 1000))
            except Exception:
                wait_s = 5.0
            if self._beat_stop.wait(wait_s):
                return
            self._beat_once()

    def _on_stop(self, reason):
        """Record svc.stop {reason} once and flush (at most 3 s). Never raises."""
        try:
            with self._lock:
                if self._stopped or self._stop:
                    return
                self._stopped = True
            self._beat_stop.set()
            self._put("svc.stop", {"extra": {"reason": reason}})
            self.flush(STOP_FLUSH_S)
        except Exception:
            pass

    on_stop = _on_stop

    def _stop_from_signal(self):
        """From a signal handler: the handler interrupted the main thread, which may hold the queue's or
        the seq lock, so the record is written from a helper thread the handler waits for (bounded)."""
        try:
            t = threading.Thread(target=self._on_stop, args=("sigterm",), name="hello-log-stop", daemon=True)
            t.start()
        except Exception:
            self._on_stop("sigterm")
            return
        t.join(STOP_FLUSH_S + 1)

    def _wake(self):
        try:
            self._q.put_nowait(None)
        except queue.Full:
            pass

    # ---- writer thread

    def _run(self):
        try:
            self._startup()
        except Exception:
            log.exception("hello_log startup")
        while True:
            gen = self._kick_done
            try:
                batch = self._take(BATCH_WAIT_S)
                with self._cv:
                    gen = self._kick_gen              # flush requests made before this point are served now
                kick = gen > self._kick_done
                self._handle(batch)
                self._ship_direct(kick)
                if self._backlog and (kick or time.monotonic() >= self._retry_at):
                    self._ship_backlog()
                self._ship_direct(kick)
                self._maybe_prune()
            except Exception:
                log.exception("hello_log writer")
            with self._cv:
                self._kick_done = gen
                self._cv.notify_all()
            if self._stop and self._q.empty():
                return

    def _take(self, wait_s):
        batch = []
        try:
            item = self._q.get(timeout=wait_s) if wait_s else self._q.get_nowait()
            if item is not None:
                batch.append(item)
            while len(batch) < BATCH_MAX:
                item = self._q.get_nowait()
                if item is not None:
                    batch.append(item)
        except queue.Empty:
            pass
        with self._cv:
            self._taken += len(batch)
        return batch

    def _between(self):
        """Called between database operations: newly queued records go to the spool now, however slow
        Mongo is (they are shipped later, in order)."""
        self._handle(self._take(0))

    def _prepare(self, rec):
        """(flow, {sha: text}) from a queued record."""
        body = rec.pop("body", None)
        key = rec.pop("key", None)
        flow = _clean(rec, FLOW_STR_MAX)
        if key is not None:
            flow["key"] = key_fp(key)
        bodies = {}
        if body is not None:
            refs = {}
            for name, value in (body.items() if isinstance(body, dict) else [("text", body)]):
                if value is None:
                    continue
                value = _clean(value)
                text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                text = _cap(text, BODY_MAX)
                digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
                refs[_utf8(str(name))] = digest
                bodies[digest] = text
            flow["body"] = refs
        return flow, bodies

    def _handle(self, batch):
        """Clean and spool a batch; what the spool can't take waits in _direct for a direct ship."""
        items, lines = [], []
        for rec in batch:
            try:
                flow, bodies = self._prepare(rec)
                line = json.dumps({"flow": flow, "bodies": bodies}, ensure_ascii=False, separators=(",", ":"))
                lines.append(line.encode("utf-8", "replace") + b"\n")
                items.append((flow, bodies))
            except Exception:
                with self._lock:
                    self._dropped += 1
        if items and not self._append(lines):
            room = max(0, QUEUE_MAX - len(self._direct))
            self._direct.extend(items[:room])
            if len(items) > room:
                with self._lock:
                    self._dropped += len(items) - room

    def _ship_direct(self, kick=True):
        """Ship what the spool could not take. Unshipped records stay in memory (at most QUEUE_MAX)."""
        if not self._direct or not (kick or time.monotonic() >= self._retry_at):
            return
        items, self._direct = self._direct, []
        done, rejected, err = self._ship_chunk(items)
        self._shipped += done - rejected
        if err is None:
            self._fails = 0
            return
        self._failed(err)
        self._direct = items[done:] + self._direct
        extra = len(self._direct) - QUEUE_MAX
        if extra > 0:
            del self._direct[QUEUE_MAX:]
            with self._lock:
                self._dropped += extra

    def _spool_path(self, day, ext):
        return self.spool_dir / f"{self.svc}-{day}.{ext}"

    def _append(self, lines):
        path = self._spool_path(_bj(self._clock(), "%Y%m%d"), "jsonl")
        try:
            self.spool_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                size = os.fstat(fd).st_size
                try:
                    view = memoryview(b"".join(lines))
                    while view:
                        view = view[os.write(fd, view):]
                    os.fsync(fd)
                except Exception:
                    try:
                        os.ftruncate(fd, size)      # disk full: no half line left behind
                    except OSError:
                        pass
                    raise
            finally:
                os.close(fd)
            if path not in self._pending:
                self._pending[path] = self._read_offset(path)
            self._backlog += len(lines)
            return True
        except Exception as exc:
            self._spool_errors += 1
            log.info("hello_log spool write failed: %s", exc)
            return False

    def _read_offset(self, path):
        try:
            return int(path.with_suffix(".offset").read_text().strip() or 0)
        except (OSError, ValueError):
            return 0

    def _write_offset(self, path, offset):
        """Save the shipped offset; the file's mtime (on the logger's clock) = when shipping last advanced."""
        target = path.with_suffix(".offset")
        tmp = target.with_suffix(".offset.tmp")
        try:
            tmp.write_text(str(offset))
            now_s = self._clock() / 1000
            os.utime(tmp, (now_s, now_s))
            os.replace(tmp, target)
        except OSError:
            self._spool_errors += 1

    def _startup(self):
        """Find spool lines not yet shipped (earlier runs, earlier days) so the loop replays them first."""
        try:
            names = sorted(os.listdir(self.spool_dir))
        except OSError:
            return
        for name in names:
            if not self._name_re.match(name):
                continue
            path = self.spool_dir / name
            try:
                size = path.stat().st_size
                offset = self._read_offset(path)
                if offset > size:
                    offset = 0
                if offset == size:
                    continue
                with open(path, "rb") as f:
                    f.seek(offset)
                    rest = f.read()
                if not rest.endswith(b"\n"):        # a line cut off by a kill: end it so it is skipped
                    with open(path, "ab") as f:
                        f.write(b"\n")
                self._backlog += rest.count(b"\n") + (0 if rest.endswith(b"\n") else 1)
                self._pending[path] = offset
                self._retry_at = 0.0
            except OSError:
                self._spool_errors += 1

    def _read_chunk(self, path, offset):
        """Up to BATCH_MAX complete lines from offset: (items, ends, pos, n); ends[i] = (offset, lines)
        just after item i, pos / n = after the whole chunk (unreadable lines are skipped)."""
        items, ends, pos, n = [], [], offset, 0
        with open(path, "rb") as f:
            f.seek(offset)
            while n < BATCH_MAX:
                line = f.readline()
                if not line.endswith(b"\n"):
                    break
                pos += len(line)
                n += 1
                try:
                    rec = json.loads(line)
                    items.append((rec["flow"], rec.get("bodies") or {}))
                    ends.append((pos, n))
                except Exception:
                    self._spool_errors += 1
        return items, ends, pos, n

    def _ship_backlog(self):
        today = self._spool_path(_bj(self._clock(), "%Y%m%d"), "jsonl")
        for path in sorted(self._pending):
            while True:
                off = self._pending[path]
                try:
                    items, ends, pos, n = self._read_chunk(path, off)
                except OSError:
                    self._spool_errors += 1
                    self._pending.pop(path, None)
                    break
                if n == 0:
                    if path != today:
                        self._pending.pop(path, None)
                    break
                rejected = 0
                if items:
                    where, fails = self._stuck
                    done, rejected, err = self._ship_chunk(items, split=where == (path, off) and fails >= SHIP_TRIES)
                    if err is not None:
                        if done:                    # keep what went through; the rest waits
                            self._advance(path, off, *ends[done - 1], done - rejected)
                        here = self._pending[path]
                        self._stuck = ((path, here), fails + 1 if where == (path, here) else 1)
                        self._failed(err)
                        return
                self._stuck = (None, 0)
                self._advance(path, off, pos, n, len(items) - rejected)
                self._fails = 0
                self._between()

    def _advance(self, path, off, pos, n, shipped):
        """Lines up to pos (n of them after off) are in Mongo or rejected."""
        self._write_offset(path, pos)
        self._pending[path] = pos
        self._backlog = max(0, self._backlog - n)
        self._shipped += shipped

    def _ship_chunk(self, items, split=False):
        """Ship items in order. Returns (done, rejected, err): the first `done` items are in Mongo or
        rejected (`rejected` of them); err is set when shipping stopped and items[done:] must wait.
        The chunk is shipped whole first (SHIP_TRIES tries) unless split=True (it already failed that
        often at this spool position). Then record by record (SHIP_TRIES tries each). A record is
        rejected only with proof that the database is taking writes and only this record is refused:
        another write of the same pass went through, or the record could not be encoded on our side
        and a `ping` answers right after. Otherwise (an unreachable database, a refusal of everything,
        an error we cannot place) nothing is rejected and everything waits; the pass also stops early
        when the first REFUSE_ALL records all fail alike and none goes through."""
        if not split:
            for attempt in range(SHIP_TRIES):
                try:
                    return len(items), self._ship(items), None
                except Exception as exc:
                    if _unreachable(exc):
                        return 0, 0, exc
        ok, bad, refused = 0, [], 0
        for i, item in enumerate(items):
            err = None
            for attempt in range(SHIP_TRIES):
                try:
                    refused += self._ship([item])
                    err = None
                    break
                except Exception as exc:
                    err = exc
                    if _unreachable(exc):
                        break
            if err is None:
                ok += 1
            elif _unreachable(err):
                return self._settle(i, bad, refused, err, ok)
            else:
                bad.append((i, item, err))
                if ok == 0 and len(bad) == REFUSE_ALL and len({type(e) for _, _, e in bad}) == 1:
                    return self._settle(i + 1, bad, refused, err, ok)
        return self._settle(len(items), bad, refused, None, ok)

    def _settle(self, done, bad, refused, err, ok):
        """Reject the failed records there is proof against (see _ship_chunk); the first one without
        proof ends `done` (it and everything after it wait). With ok == 0 nothing before it went in."""
        alive = None
        for n, (i, (flow, _), exc) in enumerate(bad):
            if ok == 0:
                if alive is None and _record_fault(exc):
                    alive = self._ping()
                if not (_record_fault(exc) and alive):
                    for _, (f, _), e in bad[:n]:
                        self._reject(f, e)
                    return i, n + refused, exc
            self._reject(flow, exc)
        return done, len(bad) + refused, err

    def _reject(self, flow, exc):
        self._rejected += 1
        log.info("hello_log: rejected %s after %d tries: %r", flow.get("_id"), SHIP_TRIES, exc)

    def _ping(self):
        """True when the database answers a ping right now (no write, bounded by the client's timeouts)."""
        try:
            db = self._get_db()
            command = getattr(db, "command", None)
            if command is None:
                return False
            command("ping")
            return True
        except Exception:
            return False

    def _failed(self, exc):
        self._db_errors += 1
        self._fails += 1
        self._retry_at = time.monotonic() + min(30, 2 ** (self._fails - 1))
        if self._fails == 1:
            log.info("hello_log: Mongo write failed, keeping records in the spool: %r", exc)

    def _get_db(self):
        """The database; any failure to build the client (bad URI, missing pymongo …) is _NoDatabase:
        it is never a reason to reject a record."""
        if self._db is None:
            if not self._uri:
                raise _NoDatabase("no database configured")
            try:
                from pymongo import MongoClient
                self._db = MongoClient(self._uri, serverSelectionTimeoutMS=5000, socketTimeoutMS=5000,
                                       connectTimeoutMS=5000)[DB_NAME]
            except Exception as exc:
                raise _NoDatabase(f"connect: {type(exc).__name__}") from exc     # no URI text (secrets)
        return self._db

    def _ensure_indexes(self, db, month):
        if month in self._indexed:
            return
        flows = db["log_" + month]
        specs = [([("at", 1)], {}), ([("kind", 1), ("at", 1)], {}), ([("trace", 1)], {}),
                 # descending so it never clashes with at_1 above; range scans work either way
                 ([("at", -1)], {"name": "at_cost", "partialFilterExpression": {"cost": {"$exists": True}}})]
        for keys, kw in specs:
            self._between()
            try:
                flows.create_index(keys, **kw)
            except Exception as exc:
                if getattr(exc, "code", None) in _INDEX_EXISTS_CODES:
                    continue
                raise _NoDatabase(f"create_index: {exc!r}"[:300]) from exc
        self._indexed.add(month)

    def _ship(self, items):
        """Write (flow, bodies) pairs to Mongo; returns how many flows the server refused (counted as
        rejected). Raises when it should be retried later."""
        db = self._get_db()
        refused = 0
        by_month = {}
        for flow, bodies in items:
            by_month.setdefault(month_of(flow["at"]), []).append((flow, bodies))
        for month, group in by_month.items():
            self._ensure_indexes(db, month)
            new = {}
            for _, bodies in group:
                for digest, text in bodies.items():
                    if (month, digest) in self._cache:
                        self._cache.move_to_end((month, digest))
                    else:
                        new[digest] = text
            if new:
                self._between()
                try:
                    db["logbody_" + month].bulk_write([_upsert_op(d, t) for d, t in new.items()], ordered=False)
                except Exception as exc:
                    if not _is_dup(exc):
                        raise
            self._between()
            flows = [flow for flow, _ in group]
            try:
                db["log_" + month].insert_many(flows, ordered=False)
            except Exception as exc:
                errs = _write_errors(exc)
                if errs is None or _unreachable(exc):
                    raise
                for e in errs:
                    if e.get("code") != 11000:      # the server refused this document; retrying won't help
                        self._rejected += 1
                        refused += 1
                        i = e.get("index")
                        rid = flows[i].get("_id") if isinstance(i, int) and 0 <= i < len(flows) else "?"
                        log.info("hello_log: rejected %s: %s", rid, e.get("errmsg"))
            for digest in new:
                self._cache[(month, digest)] = True
            while len(self._cache) > BODY_CACHE:
                self._cache.popitem(last=False)
        return refused

    def _maybe_prune(self):
        """Delete spool files fully shipped more than SPOOL_KEEP_DAYS ago (by the offset file's mtime,
        i.e. when shipping last advanced; checked once a day). Today's file and lagging files stay."""
        now = self._clock()
        today = _bj(now, "%Y%m%d")
        if self._prune_day == today:
            return
        self._prune_day = today
        cutoff_s = (now - SPOOL_KEEP_DAYS * 86400000) / 1000
        try:
            names = os.listdir(self.spool_dir)
        except OSError:
            return
        for name in names:
            m = self._name_re.match(name)
            path = self.spool_dir / name
            if not m or m.group(1) >= today or path in self._pending:
                continue
            marker = path.with_suffix(".offset")
            try:
                done_s = (marker if marker.exists() else path).stat().st_mtime
                if self._read_offset(path) >= path.stat().st_size and done_s <= cutoff_s:
                    path.unlink()
                    marker.unlink(missing_ok=True)
            except OSError:
                self._spool_errors += 1


def init(svc, *, db=None, db_uri=None, spool_dir, code_version="", args=None, clock_ms=None, lifecycle=True,
         install_signal=True):
    """Start this process's logger (call once). `db` is a ready database object (tests inject a fake);
    otherwise `db_uri` is connected lazily. A previous logger in this process is stopped.
    lifecycle: svc.start now, svc.beat every minute, svc.stop at exit (False = a bare writer, for tests).
    install_signal: also record svc.stop on SIGTERM (main thread only); pass False when the caller
    handles SIGTERM itself (e.g. an asyncio loop) and calls get().on_stop("sigterm") on shutdown."""
    global _current, _atexit_hooked
    prev = _current
    if prev is not None:
        prev.close(timeout_s=2)
    _current = Logger(svc, db=db, db_uri=db_uri, spool_dir=spool_dir, code_version=code_version, args=args,
                      clock_ms=clock_ms, lifecycle=lifecycle)
    if lifecycle:
        if not _atexit_hooked:
            atexit.register(_atexit)
            _atexit_hooked = True
        if install_signal:
            _install_sigterm()
    return _current


def get():
    """This process's logger, or None before init()."""
    return _current


def new_trace():
    """A fresh trace id: 12 hex digits."""
    return secrets.token_hex(6)


@contextlib.contextmanager
def scope(kind=None, *, trace=None, **fields):
    """Within the block (this thread / asyncio task only), emit() fills kind (when None), trace and these
    fields where the call leaves them out. Nested scopes: the inner one overrides the outer per key."""
    sc = dict(_scope.get() or {})
    if kind is not None:
        sc["kind"] = kind
    if trace is not None:
        sc["trace"] = trace
    sc.update((k, v) for k, v in fields.items() if v is not None)
    token = _scope.set(sc)
    try:
        yield sc
    finally:
        _scope.reset(token)


def current():
    """The fields of the current scope ({} outside any)."""
    return dict(_scope.get() or {})


def _install_sigterm():
    """Our SIGTERM handler in front of the present one; only from the main thread, never twice in a row,
    and not over a handler installed outside Python (it could not be chained)."""
    global _sigterm_prev
    try:
        if threading.current_thread() is not threading.main_thread():
            return
        prev = signal.getsignal(signal.SIGTERM)
        if prev is None or prev is _on_sigterm:
            return
        _sigterm_prev = prev                 # before installing: a signal may arrive right after
        signal.signal(signal.SIGTERM, _on_sigterm)
    except Exception:
        pass


def _on_sigterm(signum, frame):
    """Record svc.stop, then do what the previous handler would: call it, keep ignoring, or exit(0)."""
    try:
        lg = _current
        if lg is not None and lg._lifecycle:
            lg._stop_from_signal()
    except Exception:
        pass
    prev = _sigterm_prev
    if prev is signal.SIG_IGN:
        return None
    if callable(prev):
        return prev(signum, frame)
    sys.exit(0)


def _atexit():
    try:
        lg = _current
        if lg is not None and lg._lifecycle:
            lg._on_stop("exit")
    except Exception:
        pass


def emit(kind, /, **fields):
    """Record one event on this process's logger; does nothing before init(). Never raises, never blocks."""
    lg = _current
    if lg is not None:
        lg.emit(kind, **fields)


def error(exc, *, where=None, **fields):
    """svc.error for an unhandled exception on this process's logger (see Logger.error); throttled to one
    per distinct message per minute; does nothing before init(). Never raises, never blocks."""
    lg = _current
    if lg is not None:
        lg.error(exc, where=where, **fields)
