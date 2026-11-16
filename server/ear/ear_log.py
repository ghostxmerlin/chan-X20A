"""The ear's side of the hello run/audit log (2026-11-16; writer: server/common/hello_log.py, format: docs/hello-log.md).

On the server the ear runs as flat files in ~/soul-spike, where hello_log.py and prices.py sit next to them, so
`import hello_log` finds them; in the repository they are in server/common (found through the fallback below). When
hello_log cannot be imported at all, `hello_log` here is a stand-in that records nothing: logging never stops the ear.

  start(secrets, args)        hello_log.init("ear", install_signal=False) once, inside the running event loop; never raises
  watch(fn)                   the heartbeat's state (switches, links)
  Stopper(loop)               SIGTERM on the ear's own loop: stop serving, clean up, then svc.stop off the loop
  emit / scope / trace()      as hello_log; trace() = the current scope's trace, else a fresh one
  link_open / link_close      link.open / link.close of one robot link
  robot_state(part, after)    robot.state {part, before, after} only when what the robot reported changed
  cost(amount, basis, cur)    the cost envelope; tingwu_cost(audio_s), search_count(usage)
Everything here is cheap and runs on the event loop; heavy cleaning happens on the writer thread.
"""
import asyncio
import contextlib
import os
import secrets as _secrets
import signal
import sys
import time
from pathlib import Path

try:
    import hello_log
except ImportError:                              # repository layout: server/common next to server/ear
    sys.path.append(str(Path(__file__).resolve().parents[1] / "common"))
    try:
        import hello_log
    except ImportError:
        hello_log = None
try:
    import prices
except ImportError:
    prices = None

SPOOL_DIR = "~/soul-spike/log-spool"
CODE_VERSION_FILE = "~/soul-spike/CODE_VERSION"      # written by the deploy (plan Task 8)
STOP_GRACE_S = 10.0                                  # SIGTERM: clean shutdown at most this long, then exit anyway
SVC = "ear"


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
    @contextlib.contextmanager
    def scope(kind=None, *, trace=None, **fields):
        yield {}

    @staticmethod
    def current():
        return {}

    @staticmethod
    def new_trace():
        return _secrets.token_hex(6)


if hello_log is None:
    hello_log = _Off()


def code_version(path=None):
    try:
        return Path(os.path.expanduser(path or CODE_VERSION_FILE)).read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return ""


def start(secrets=None, args=None, **kw):
    """hello_log.init("ear") with hello.env's MONGO_URI (none: the spool keeps everything until there is one). Call it
    inside the running loop, after Stopper (rulings R5 / R12: the ear owns SIGTERM). -> the logger or None; never raises."""
    if isinstance(hello_log, _Off):
        print("ear: hello_log is not deployed, nothing goes to the run log", flush=True)
        return None
    kw.setdefault("spool_dir", os.path.expanduser(SPOOL_DIR))
    kw.setdefault("code_version", code_version())
    try:
        return hello_log.init(SVC, db_uri=(secrets or {}).get("MONGO_URI") or None, args=args, install_signal=False, **kw)
    except Exception as e:                       # noqa: BLE001 - logging never stops a service
        print(f"ear: run log off ({type(e).__name__}: {str(e)[:120]})", flush=True)
        return None


def watch(fn):
    """fn() -> dict for every svc.beat's state (called on the beat thread; an error there is recorded, not raised)."""
    try:
        lg = hello_log.get()
        if lg is not None:
            lg.set_beat_state(fn)
    except Exception:                            # noqa: BLE001
        pass


def emit(kind, /, **fields):
    try:
        hello_log.emit(kind, **fields)
    except Exception:                            # noqa: BLE001 - hello_log never raises; a stand-in might
        pass


def scope(kind=None, *, trace=None, **fields):
    try:
        return hello_log.scope(kind, trace=trace, **fields)
    except Exception:                            # noqa: BLE001
        return contextlib.nullcontext({})


def new_trace():
    try:
        return hello_log.new_trace()
    except Exception:                            # noqa: BLE001
        return _secrets.token_hex(6)


def trace():
    """The current scope's trace, else a fresh one."""
    try:
        return hello_log.current().get("trace") or new_trace()
    except Exception:                            # noqa: BLE001
        return new_trace()


def scoped_trace():
    """The current scope's trace, or None outside any."""
    try:
        return hello_log.current().get("trace")
    except Exception:                            # noqa: BLE001
        return None


# ---- money

def cost(amount, basis, cur="CNY"):
    """The cost envelope (spec §7.3): amount None when the price is unknown (usage is still recorded)."""
    return {"amount": None if amount is None else round(float(amount), 8), "cur": cur, "est": True, "basis": basis}


def _price(name):
    return getattr(prices, name, None) if prices is not None else None


def _basis(name):
    b = (getattr(prices, "BASIS", None) or {}).get(name) if prices is not None else None
    return f"prices.{name}" + (f" ({b})" if b else " (prices.py missing)")


def tingwu_cost(audio_s):
    """听悟 realtime is billed per hour of audio sent (one input: all of it, silence included)."""
    p = _price("TINGWU_CNY_PER_HOUR")
    amount = None if p is None or audio_s is None else p * float(audio_s) / 3600
    return cost(amount, _basis("TINGWU_CNY_PER_HOUR"))


def search_cost(n):
    p = _price("REALTIME_SEARCH_CNY_PER_CALL")
    return cost(None if p is None else p * n, _basis("REALTIME_SEARCH_CNY_PER_CALL"))


def search_count(usage):
    """Web searches in a response's usage: DashScope reports them as usage.plugins.search.count (web-search docs,
    read 2026-11-16); a top-level search_count / web_search_requests is taken too. 0 when there is none."""
    if not isinstance(usage, dict):
        return 0

    def num(v):
        return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0
    plugins = usage.get("plugins") if isinstance(usage.get("plugins"), dict) else {}
    search = plugins.get("search")
    if isinstance(search, dict) and num(search.get("count")):
        return num(search.get("count"))
    tool = usage.get("server_tool_use") if isinstance(usage.get("server_tool_use"), dict) else {}
    return num(usage.get("search_count")) or num(tool.get("web_search_requests"))


# ---- robot links and reported state

def peer_text(peer):
    if isinstance(peer, (tuple, list)) and len(peer) >= 2:
        return f"{peer[0]}:{peer[1]}"
    return None if peer is None else str(peer)[:64]


def link_open(link, *, conn=None, peer=None, ok=True, why=None):
    """link.open of one robot link (mouth / signal / vision / hearing) -> its start (monotonic) for link_close."""
    emit("link.open", ok=ok, err=None if ok else why, sum=f"{link} {conn or ''} +".strip(),
         extra={"link": link, "conn": conn, "peer": peer_text(peer), "why": why})
    return time.monotonic()


def link_close(link, t0, *, conn=None, boot=None, peer=None, why=None, counts=None):
    emit("link.close", ok=True, ms=None if t0 is None else int((time.monotonic() - t0) * 1000),
         sum=f"{link} {conn or ''} - {why or ''}".strip(),
         extra={"link": link, "conn": conn, "boot": boot, "peer": peer_text(peer), "why": why, "counts": counts})


_robot = {}                                      # (part, device) -> what the robot last reported
_live = {}                                       # (part, device) -> live links that report the part on


def robot_state(part, after, *, device=None, by="robot", **extra):
    """robot.state {part, device, before, after} when the reported `part` (mode, hearing, vision, m12) of one robot
    (device: its source, None = the one relayed mode) changed. Kept per device, so two robots never mix."""
    key = (part, device)
    before = _robot.get(key)
    if key in _robot and before == after:
        return False
    _robot[key] = after
    emit("robot.state", by=by, sum=f"{part} {before}→{after}",
         extra={"part": part, "device": device, "before": before, "after": after, **extra})
    return True


def robot_link(part, device, conn, up, **extra):
    """A state the robot shows by holding a link open (hearing): on while ANY live link of that device carries it, off
    only when its last one closes. A reconnect overlaps the old half-open socket (new c2 opens, then old c1 closes):
    c1's close must not record off while c2 is up, so the state is derived from the set of live links, not set by
    each link's own open / close."""
    live = _live.setdefault((part, device), set())
    if up:
        live.add(conn)
    elif conn in live:
        live.discard(conn)
    else:
        return False                             # never reported on: nothing changes
    return robot_state(part, "on" if live else "off", device=device, conn=conn, links=len(live), **extra)


def reset_robot_state():
    _robot.clear()
    _live.clear()


# ---- stop

class Stopper:
    """SIGTERM on the ear's asyncio loop (the ear had none: the default action killed it mid-write). The first
    SIGTERM resolves `stopped` (the server's finally blocks then run); finish() records svc.stop off the loop. If the
    clean shutdown takes longer than grace_s (or a second SIGTERM comes), svc.stop is written and the process exits."""

    def __init__(self, loop, grace_s=STOP_GRACE_S, exit_fn=os._exit):
        self.loop, self.grace_s, self._exit = loop, grace_s, exit_fn
        self.stopped = loop.create_future()
        self.installed = False
        try:
            loop.add_signal_handler(signal.SIGTERM, self._on_sigterm)
            self.installed = True
        except (NotImplementedError, RuntimeError, ValueError):     # not the main thread / no signals here
            pass

    def _on_sigterm(self):
        if self.stopped.done():
            self._force()
            return
        print("ear: SIGTERM, shutting down", flush=True)
        self.stopped.set_result("sigterm")
        self.loop.call_later(self.grace_s, self._force)

    def _force(self):
        print(f"ear: shutdown took over {self.grace_s:.0f} s, exiting", flush=True)
        _record_stop("sigterm")
        self._exit(0)

    async def wait(self):
        return await asyncio.shield(self.stopped)

    async def finish(self, reason="sigterm"):
        """svc.stop + flush (at most 3 s) on a worker thread, so the loop is never blocked."""
        await self.loop.run_in_executor(None, _record_stop, reason)


def _record_stop(reason):
    try:
        lg = hello_log.get()
        if lg is not None:
            lg.on_stop(reason)
    except Exception:                            # noqa: BLE001
        pass
