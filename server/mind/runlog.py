"""mind's side of the hello run/audit log (2026-11-16; writer: server/common/hello_log.py, record format: docs/hello-log.md).

On the server mind runs as `python -m mind.<x>` from ~/soul-mind, where hello_log.py sits next to the package, so
`import hello_log` finds it; in the repository it is server/common/hello_log.py (tests put that folder on sys.path).
When it cannot be imported at all, `hello_log` here is a stand-in that records nothing: logging never stops a
service from starting.

  start(svc, hub, args)      hello_log.init once per process (mind / eye / notes / memory); never raises
  watch(db, store)           the heartbeat's state: the switches table + the mind switch (store.enabled())
  error(exc, where, type_only)   svc.error from a top-level handler (throttled: 1 per message a minute; type_only:
                             the exception's type name only, for memory)
  trace()                    the current scope's trace, else a fresh one (a scope never drops an outer trace)
  current_trace()            the current scope's trace, else None (what mind sends soul-tts as /v1/say's trace)
  switch(name, before, after, by)   with-block around one switch write -> one switch.set {name, before, after}
  read_switch(db, name)      a switch's stored value (on, or mode for 嘴) before a write; None when unreadable
  value(fn), by_scope(default)      a 'before' read that never raises; the scope's by, else default
"""
import contextlib
import os
import secrets
import sys
from pathlib import Path

try:
    import hello_log
except ImportError:                              # repository layout: server/common next to server/mind
    sys.path.append(str(Path(__file__).resolve().parents[1] / "common"))
    try:
        import hello_log
    except ImportError:
        hello_log = None

SPOOL_DIR = "~/soul-spike/log-spool"
CODE_VERSION_FILE = "~/soul-mind/CODE_VERSION"      # written by the deploy (plan Task 8)
NAMES = {"tingwu": "听悟", "eye": "识别", "notes": "纪要", "realtime": "Docking", "mind": "情景", "mouth": "嘴",
         "memory_extract": "记忆整理", "say_prefetch": "提前写", "say_auto": "连续说"}


class _Off:
    """hello_log when it is not deployed: the same calls, nothing recorded."""
    _current = None

    @staticmethod
    def init(*a, **kw):
        return None

    @staticmethod
    def get():
        return None

    @staticmethod
    def emit(kind, /, **fields):
        return None

    @staticmethod
    def error(exc, *, where=None, **fields):
        return None

    @staticmethod
    @contextlib.contextmanager
    def scope(kind=None, *, trace=None, **fields):
        yield {}

    @staticmethod
    def current():
        return {}

    @staticmethod
    def new_trace():
        return secrets.token_hex(6)

    @staticmethod
    def key_fp(key):
        return str(key)[:7] if key else None


if hello_log is None:
    hello_log = _Off()


def code_version(path=None):
    try:
        return Path(os.path.expanduser(path or CODE_VERSION_FILE)).read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return ""


def start(svc, hub, args=None, **kw):
    """hello_log.init(svc) with hello.env's MONGO_URI (none: the spool keeps everything until there is one).
    -> the logger, or None when logging is off; never raises."""
    if isinstance(hello_log, _Off):
        print(f"{svc}: hello_log is not deployed, nothing goes to the run log", flush=True)
        return None
    try:
        return hello_log.init(svc, db_uri=(hub or {}).get("MONGO_URI") or None,
                              spool_dir=os.path.expanduser(SPOOL_DIR), code_version=code_version(), args=args, **kw)
    except Exception as e:                       # noqa: BLE001 - logging never stops a service
        print(f"{svc}: run log off ({type(e).__name__}: {str(e)[:120]})", flush=True)
        return None


def beat_state(db, store=None):
    """What svc.beat carries as state: {switch: on / mode, ..., mind: on}. Cheap, never raises."""
    out = {}
    try:
        for doc in db["switches"].find({}, {"_id": 1, "on": 1, "mode": 1}):
            name = str(doc.get("_id"))
            if not name.endswith("_status"):
                out[name] = doc.get("mode") if "mode" in doc else doc.get("on")
    except Exception as e:                       # noqa: BLE001
        out["error"] = f"switches: {type(e).__name__}"
    try:
        if store is not None:
            out["mind"] = store.enabled()
    except Exception as e:                       # noqa: BLE001
        out["error"] = f"mind: {type(e).__name__}"
    return out


def watch(db, store=None):
    try:
        lg = hello_log.get()
        if lg is not None:
            lg.set_beat_state(lambda: beat_state(db, store))
    except Exception:                            # noqa: BLE001
        pass


def error(exc, where, type_only=False):
    """svc.error from a runner's top-level exception handler: one line (type + message, redacted, ≤ 300 chars), at
    most one per distinct message a minute (hello_log throttles). type_only=True records only the exception's type
    name, no message (memory: its errors can carry private content). Never raises."""
    try:
        hello_log.error(type(exc).__name__ if type_only else exc, where=where)
    except Exception:                            # noqa: BLE001
        pass


def trace():
    try:
        return hello_log.current().get("trace") or hello_log.new_trace()
    except Exception:                            # noqa: BLE001
        return secrets.token_hex(6)


def current_trace():
    try:
        return hello_log.current().get("trace") or None
    except Exception:                            # noqa: BLE001
        return None


def value(fn):
    """fn() for a 'before' value; None when it raises (the write itself will say what is wrong)."""
    try:
        return fn()
    except Exception:                            # noqa: BLE001
        return None


def by_scope(default):
    """The current scope's by (e.g. page around a page request), else default."""
    try:
        return hello_log.current().get("by") or default
    except Exception:                            # noqa: BLE001
        return default


def read_switch(db, name):
    try:
        doc = db["switches"].find_one({"_id": name}) or {}
        return doc.get("mode") if "mode" in doc else doc.get("on")
    except Exception:                            # noqa: BLE001 - the write itself will say what is wrong
        return None


def _word(v):
    return "开" if v is True else "关" if v is False else "?" if v is None else str(v)


def emit_switch(name, before, after, by, err=None):
    hello_log.emit("switch.set", by=by, ok=err is None, err=err,
                   sum=f"{NAMES.get(name, name)} {_word(before)}→{_word(after)}",
                   extra={"name": name, "before": before, "after": after})


@contextlib.contextmanager
def switch(name, before, after, by):
    """Around one switch write: switch.set after it (ok), or with the error when the write raises (re-raised)."""
    try:
        yield
    except Exception as e:
        emit_switch(name, before, after, by, err=f"{type(e).__name__}: {str(e)[:160]}")
        raise
    emit_switch(name, before, after, by)
