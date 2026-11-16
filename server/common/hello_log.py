"""hello run/audit log writer (2026-11-16), shared by ear, mind, eye, notes, memory and tts.

    import hello_log
    hello_log.init("mind", db_uri=MONGO_URI, spool_dir="~/soul-spike/log-spool")   # once per process
    hello_log.emit("call.eye", trace=t, ok=True, ms=812, model="...", sum="一行摘要",
                   body={"system": prompt, "input": messages, "output": text})

Two layers in the Mongo database "hello", one pair of tables per Beijing month:
  log_YYYYMM      flow: one small record per event. _id "{svc}:{run}:{seq}", at (ms), svc, run, seq,
                  kind, plus the caller's envelope fields (spec §7.3); `body` holds {name: sha256}.
  logbody_YYYYMM  bodies: {_id: sha256, text}; full prompts / inputs / outputs, deduplicated per month.

emit() only snapshots the fields and puts them on a bounded queue; it never blocks and never raises.
One daemon thread cleans each record (redaction, no images, size caps), appends it to the local spool
<spool>/<svc>-YYYYMMDD.jsonl (fsync), then ships the spool to Mongo and records how far it got in
<svc>-YYYYMMDD.offset. If Mongo is down or slow (5 s timeouts) the lines wait in the spool and are
replayed later, also after a restart; replay is idempotent (duplicate _id = already shipped). If the
spool folder is unwritable the batch goes straight to Mongo. Fully shipped spool files are deleted
after 7 days. stdlib only; pymongo is imported when a real connection is first needed.
"""
import hashlib
import itertools
import json
import logging
import os
import queue
import re
import secrets
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path

DB_NAME = "hello"
QUEUE_MAX = 20000
BATCH_MAX = 500
BATCH_WAIT_S = 0.5
BODY_MAX = 4 * 1024 * 1024           # bytes of one body text
FLOW_STR_MAX = 64 * 1024             # bytes of one string in a flow record (big content belongs in body)
BODY_CACHE = 5000                    # recently shipped (month, sha) pairs
SPOOL_KEEP_DAYS = 7
RESERVED = ("_id", "svc", "run", "seq")

log = logging.getLogger("hello_log")
_current = None
_EPOCH_BJ = datetime(1970, 1, 1) + timedelta(hours=8)        # naive Beijing wall clock at epoch (no DST)
_SENSITIVE = ("key", "token", "secret", "password", "authorization")
_URL_SECRET = re.compile(r"(?i)([?&;][^=&#\s\"']*(?:key|token|secret|password|authorization)[^=&#\s\"']*=)"
                         r"[^&#\s\"']*")
_DATA_URL_WHOLE = re.compile(r"data:([\w.+-]+)/[\w.+-]+[;,]")
_DATA_URL_INLINE = re.compile(r"data:([\w.+-]+)/[\w.+-]+(?:;[\w.+-]+=[\w.+-]+)*;base64,[A-Za-z0-9+/=\r\n]*")
_SPOOL_NAME = r"^{}-(\d{{8}})\.jsonl$"
_INT64 = 2 ** 63


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


def _omitted(match_text, mime_major):
    kind = mime_major if mime_major in ("image", "audio", "video") else "data"
    return f"[{kind} omitted, {len(match_text.encode('utf-8'))} bytes]"


def _cap(text, limit):
    raw = text.encode("utf-8")
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
        s = _URL_SECRET.sub(r"\1***", s)
    if limit and len(s) * 4 > limit:
        s = _cap(s, limit)
    return s


def _sensitive(name):
    low = name.lower()
    return any(word in low for word in _SENSITIVE)


def _clean(obj, limit=0, depth=0, masked=False):
    """JSON-safe copy: secrets masked, data: URLs and bytes replaced by a size note, odd objects as repr,
    strings capped at `limit` bytes (0 = no cap). Under a sensitive name everything but numbers is '***'."""
    if obj is None or isinstance(obj, (bool, float)):
        return obj
    if isinstance(obj, int):
        return obj if -_INT64 <= obj < _INT64 else str(obj)
    if masked and not isinstance(obj, (dict, list, tuple)):
        return "***"
    if isinstance(obj, str):
        return _clean_str(obj, limit)
    if depth > 40:
        return "[too deep]"
    if isinstance(obj, dict):
        return {(k if isinstance(k, str) else str(k)):
                _clean(v, limit, depth + 1, masked or _sensitive(k if isinstance(k, str) else str(k)))
                for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v, limit, depth + 1, masked) for v in obj]
    if isinstance(obj, (bytes, bytearray, memoryview)):
        return f"[bytes omitted, {len(bytes(obj))} bytes]"
    try:
        text = repr(obj)
    except Exception:
        text = f"<{type(obj).__name__} without repr>"
    return _clean_str(text, limit)


def redact(obj):
    """Copy of obj with values under key/token/secret/password/authorization names (any case) as '***'
    (numbers such as usage token counts stay), URL query secrets as '***', data: URLs omitted."""
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


# ---------------------------------------------------------------- the writer

class Logger:
    """One per process (see init()). Counters in stats(); everything else happens on the writer thread."""

    def __init__(self, svc, *, db=None, db_uri=None, spool_dir, code_version="", args=None, clock_ms=None):
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
        self._queued = self._shipped = self._dropped = self._spool_errors = self._db_errors = 0
        self._backlog = 0
        self._enqueued = self._taken = 0            # records put / taken (flush waits on these)
        self._loop_started = self._loop_done = 0
        self._pending = {}                          # spool path -> shipped offset, files that may lag
        self._indexed = set()                       # months whose indexes this process has created
        self._cache = OrderedDict()                 # (month, sha) shipped recently
        self._fails = 0
        self._retry_at = 0.0
        self._kick = False
        self._stop = False
        self._prune_day = None
        self._name_re = re.compile(_SPOOL_NAME.format(re.escape(svc)))
        self._thread = threading.Thread(target=self._run, name=f"hello-log-{svc}", daemon=True)
        self._thread.start()

    # ---- caller side

    def emit(self, kind, **fields):
        try:
            rec = {k: _snap(v) for k, v in fields.items() if v is not None and k not in RESERVED}
            at = rec.pop("at", None)
            if isinstance(at, bool) or not isinstance(at, (int, float)):
                at = self._clock()
            with self._lock:
                seq = next(self._seq)
            rec.update({"_id": f"{self.svc}:{self.run}:{seq}", "at": int(at), "svc": self.svc, "run": self.run,
                        "seq": seq})
            if kind is not None:
                rec["kind"] = kind
            try:
                self._q.put_nowait(rec)
            except queue.Full:
                with self._lock:
                    self._dropped += 1
                return
            with self._lock:
                self._queued += 1
                self._enqueued += 1
        except Exception:
            try:
                with self._lock:
                    self._dropped += 1
            except Exception:
                pass

    def stats(self):
        return {"queued": self._queued, "shipped": self._shipped, "dropped": self._dropped,
                "spool_errors": self._spool_errors, "db_errors": self._db_errors, "backlog_lines": self._backlog}

    def flush(self, timeout_s=5):
        """Wait until everything emitted so far has been through the writer and one ship attempt.
        True when it is all in Mongo (nothing left in the spool backlog, nothing lost)."""
        deadline = time.monotonic() + timeout_s
        with self._lock:
            target, dropped0 = self._enqueued, self._dropped
        with self._cv:
            started = self._loop_started
            self._kick = True
        self._wake()
        with self._cv:
            while self._taken < target or self._loop_done <= started:
                left = deadline - time.monotonic()
                if left <= 0 or not self._thread.is_alive():
                    return False
                self._cv.wait(min(left, 0.1))
        return self._backlog == 0 and self._dropped == dropped0

    def close(self, timeout_s=5):
        """Stop the writer after it has spooled what is queued (waits at most timeout_s)."""
        self._stop = True
        self._wake()
        if timeout_s > 0 and self._thread is not threading.current_thread():
            self._thread.join(timeout_s)

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
            with self._cv:
                self._loop_started += 1
                n, kick, self._kick = self._loop_started, self._kick, False
            try:
                batch = self._take()
                if batch:
                    self._handle(batch)
                if self._backlog and (kick or time.monotonic() >= self._retry_at):
                    self._ship_backlog()
                self._maybe_prune()
            except Exception:
                log.exception("hello_log writer")
            with self._cv:
                self._loop_done = n
                self._cv.notify_all()
            if self._stop and self._q.empty():
                return

    def _take(self):
        batch = []
        try:
            item = self._q.get(timeout=BATCH_WAIT_S)
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
                digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                refs[str(name)] = digest
                bodies[digest] = text
            flow["body"] = refs
        return flow, bodies

    def _handle(self, batch):
        items, lines = [], []
        for rec in batch:
            try:
                flow, bodies = self._prepare(rec)
                line = json.dumps({"flow": flow, "bodies": bodies}, ensure_ascii=False, separators=(",", ":"))
            except Exception:
                with self._lock:
                    self._dropped += 1
                continue
            items.append((flow, bodies))
            lines.append(line.encode("utf-8") + b"\n")
        if not items or self._append(lines):
            return
        try:                                        # spool unusable: straight to Mongo
            self._ship(items)
            self._shipped += len(items)
            self._fails = 0
        except Exception as exc:
            self._failed(exc)
            with self._lock:
                self._dropped += len(items)

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
        target = path.with_suffix(".offset")
        tmp = target.with_suffix(".offset.tmp")
        try:
            tmp.write_text(str(offset))
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
                self._kick = True
            except OSError:
                self._spool_errors += 1

    def _read_chunk(self, path, offset):
        items, pos, n = [], offset, 0
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
                except Exception:
                    self._spool_errors += 1
        return items, pos, n

    def _ship_backlog(self):
        t0 = time.monotonic()
        today = self._spool_path(_bj(self._clock(), "%Y%m%d"), "jsonl")
        for path in sorted(self._pending):
            while True:
                try:
                    items, pos, n = self._read_chunk(path, self._pending[path])
                except OSError:
                    self._spool_errors += 1
                    self._pending.pop(path, None)
                    break
                if n == 0:
                    if path != today:
                        self._pending.pop(path, None)
                    break
                if items:
                    try:
                        self._ship(items)
                    except Exception as exc:
                        self._failed(exc)
                        return
                self._write_offset(path, pos)
                self._pending[path] = pos
                self._backlog = max(0, self._backlog - n)
                self._shipped += len(items)
                self._fails = 0
                if time.monotonic() - t0 > 1.0 and not self._q.empty():
                    return                          # let new records reach the spool first

    def _failed(self, exc):
        self._db_errors += 1
        self._fails += 1
        self._retry_at = time.monotonic() + min(30, 2 ** (self._fails - 1))
        if self._fails == 1:
            log.info("hello_log: Mongo write failed, keeping records in the spool: %s", exc)

    def _get_db(self):
        if self._db is None:
            if not self._uri:
                raise RuntimeError("no database configured")
            from pymongo import MongoClient
            self._db = MongoClient(self._uri, serverSelectionTimeoutMS=5000, socketTimeoutMS=5000,
                                   connectTimeoutMS=5000)[DB_NAME]
        return self._db

    def _ensure_indexes(self, db, month):
        if month in self._indexed:
            return
        flows = db["log_" + month]
        flows.create_index([("at", 1)])
        flows.create_index([("kind", 1), ("at", 1)])
        flows.create_index([("trace", 1)])
        # descending so it never clashes with at_1 above; range scans work either way
        flows.create_index([("at", -1)], name="at_cost", partialFilterExpression={"cost": {"$exists": True}})
        self._indexed.add(month)

    def _ship(self, items):
        """Write (flow, bodies) pairs to Mongo; raises when it should be retried later."""
        db = self._get_db()
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
            body_col = db["logbody_" + month]
            for digest, text in new.items():
                try:
                    body_col.update_one({"_id": digest}, {"$setOnInsert": {"text": text}}, upsert=True)
                except Exception as exc:
                    if not _is_dup(exc):
                        raise
            try:
                db["log_" + month].insert_many([flow for flow, _ in group], ordered=False)
            except Exception as exc:
                errs = _write_errors(exc)
                if errs is None:
                    raise
                rejected = [e for e in errs if e.get("code") != 11000]
                if rejected:                        # the server refused those documents; retrying won't help
                    self._db_errors += len(rejected)
                    log.info("hello_log: %d records rejected: %s", len(rejected), rejected[0].get("errmsg"))
            for digest in new:
                self._cache[(month, digest)] = True
            while len(self._cache) > BODY_CACHE:
                self._cache.popitem(last=False)

    def _maybe_prune(self):
        """Delete spool files that are fully shipped and older than SPOOL_KEEP_DAYS (checked once a day)."""
        now = self._clock()
        today = _bj(now, "%Y%m%d")
        if self._prune_day == today:
            return
        self._prune_day = today
        cutoff = _bj(now - SPOOL_KEEP_DAYS * 86400000, "%Y%m%d")
        try:
            names = os.listdir(self.spool_dir)
        except OSError:
            return
        for name in names:
            m = self._name_re.match(name)
            path = self.spool_dir / name
            if not m or m.group(1) >= cutoff or path in self._pending:
                continue
            try:
                if self._read_offset(path) >= path.stat().st_size:
                    path.unlink()
                    path.with_suffix(".offset").unlink(missing_ok=True)
            except OSError:
                self._spool_errors += 1


def init(svc, *, db=None, db_uri=None, spool_dir, code_version="", args=None, clock_ms=None):
    """Start this process's logger (call once). `db` is a ready database object (tests inject a fake);
    otherwise `db_uri` is connected lazily. A previous logger in this process is stopped."""
    global _current
    prev = _current
    if prev is not None:
        prev.close(timeout_s=2)
    _current = Logger(svc, db=db, db_uri=db_uri, spool_dir=spool_dir, code_version=code_version, args=args,
                      clock_ms=clock_ms)
    return _current


def emit(kind, **fields):
    """Record one event on this process's logger; does nothing before init(). Never raises, never blocks."""
    lg = _current
    if lg is not None:
        lg.emit(kind, **fields)
