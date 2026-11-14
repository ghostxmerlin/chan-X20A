"""/mouth link (2026-11-11, ear terminates it since 2026-11-14): the robot's speaker downlink.

The robot connects to the ear's public port at /mouth (like /vision); the ear has already checked the
device token. The ear now ends the robot's link itself and keeps its own link to soul-tts's mouth
server on the loopback (127.0.0.1:52751) behind it, so later steps can add mode messages on /mouth:

- The robot's first message must be its hello (device_id, boot_id, codecs, ...). The ear answers it
  itself with {"type":"hello","codec":"opus","frame_ms":60,"sample_rate":24000} (the robot checks only
  those three), then sends the robot's hello verbatim to soul-tts and drops soul-tts's own hello.
- After that, frames pass both ways unchanged: binary audio packets and tts.* text down, the robot's
  tts.playing / progress / done / stopped / error reports up.
- soul-tts drops or is not running: every stream it had started and not ended or stopped gets a
  {"type":"tts.stop","stream":N} to the robot (it clears the stream and hearing un-mutes); the robot's
  side stays open; the ear dials soul-tts again every retry_s seconds and replays the robot's hello.
  Robot reports meant for soul-tts while it is away are dropped (it has forgotten those streams).
- soul-tts closes with 4000 (it keeps one device and a newer link replaced this one): this robot side
  is stale, so it is closed and run() returns "upstream_replaced" (no reconnect, or the two links
  would take soul-tts from each other every retry_s).
- The robot leaves: the soul-tts link is closed at once (soul-tts never speaks to an absent robot) and
  run() returns "device_closed".

Modes (step 1b, with modes=ModeRelay): hello owns the mode (owner 2026-11-14); the ear only carries its messages to
mind. None of this goes to soul-tts, and nothing about the mode is pushed down any more (no snapshots).
- Robot {"type":"mode_report",seq,mode,dir,host,return_to,by,input,effects} (one per change, and the current state
  after every /mouth connect) -> a bounded queue (report_queue; full: the oldest is dropped, logged) -> one worker
  POSTs mind /mode/report {boot, conn, age_ms (time queued), seq, mode, dir, host, return_to, by, input, effects}, in
  order. mind away or slow: the same report is retried every report_retry_s while the robot is here (mind
  deduplicates on boot + seq, and drops the stop / paid effects of a report older than 3 s); 400 / 404 / 409:
  logged and dropped. No answer goes back to the robot.
- Robot {"type":"mode_input",seq,age_ms,source,input,value} (digits 0 / 9: the master switch) -> a bounded queue
  (input_queue) -> one worker POSTs /mode/input {device:"robot", boot, seq, age_ms + time queued, source, input,
  value} and answers {"type":"mode_result","seq":<robot seq>,"code":..,"toast":..}. Never on the audio path: queue
  full -> busy "BUSY" at once; no answer in input_timeout_s -> busy "MIND SLOW"; mind down -> busy "MIND OFFLINE";
  400 -> unknown_input "BAD INPUT".
- The robot leaves after mind took at least one report from this link: POST /mode/robot {boot, conn, event:"closed"}
  (conn is this link's number, so mind ignores a late close of an old link).
- {"type":"mode_applied"} (0.4.92 test builds before step 1b) is dropped. A robot that sends none of these (0.4.91)
  just has no modes; audio and the link are unchanged.
mind is asked on the relay's own small thread pool (never the default executor, which tingwu and switches use).
Writers: several tasks send() to the robot socket (the upstream loop, input results). websockets writes each whole
message atomically, so that is safe; never use fragmented sends (async iterators) on the robot ws.
An unexpected error is logged with its traceback: in the mode path it is caught there and /mouth goes on; a task
that dies anyway is logged before run() ends.
"""
import asyncio
import collections
import concurrent.futures
import json
import time
import traceback
import urllib.error
import urllib.request

try:
    from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI
except ImportError:                                 # tests without websockets only use mind_client
    class ConnectionClosed(Exception):
        pass
    InvalidHandshake = InvalidURI = ConnectionClosed

UPSTREAM = "ws://127.0.0.1:52751/mouth"
EAR_HELLO = json.dumps({"type": "hello", "codec": "opus", "frame_ms": 60, "sample_rate": 24000})
HELLO_TIMEOUT_S = 10
REPLACED = 4000                                     # soul-tts: "replaced by new connection"
MIND = "http://127.0.0.1:52740"
REPORT_FIELDS = ("seq", "mode", "dir", "host", "return_to", "by", "input", "effects")
log = print                                         # tests silence it


def _trace(what):
    """Log an unexpected exception with its traceback (call inside an except block)."""
    log(f"{what}: unexpected error\n{traceback.format_exc().rstrip()}", flush=True)


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _ascii(text, n=32):
    return "".join(c for c in str(text or "") if 32 <= ord(c) < 127)[:n]


def mind_client(token, base=MIND):
    """call(method, path, body=None, timeout=5) -> (status, reply dict); never raises (mind down -> 503).
    Same shape as signal_link.mind_poster; blocking, run it off the event loop."""
    def call(method, path, body=None, timeout=5):
        headers = {"Authorization": f"Bearer {token}"}
        data = None
        if body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        req = urllib.request.Request(base + path, method=method, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                reply = json.loads(r.read() or b"{}")
                return r.status, reply if isinstance(reply, dict) else {}
        except urllib.error.HTTPError as e:
            with e:
                try:
                    reply = json.loads(e.read() or b"{}")
                    return e.code, reply if isinstance(reply, dict) else {}
                except ValueError:
                    return e.code, {}
        except Exception as e:                       # noqa: BLE001 - mind down or slow: the caller answers busy
            return 503, {"error": type(e).__name__}
    return call


def _result(status, reply):
    """mind's answer to /mode/input -> the robot's {code, toast} (codes from the plan's list; ASCII toast <= 32)."""
    if status is None:
        return {"code": "busy", "toast": "MIND SLOW"}
    res = reply.get("result") if status == 200 else None
    if isinstance(res, dict) and isinstance(res.get("code"), str):
        return {"code": _ascii(res["code"]), "toast": _ascii(res.get("toast"))}
    if status == 400:
        return {"code": "unknown_input", "toast": "BAD INPUT"}
    if status == 409 and isinstance(reply.get("code"), str):
        code = _ascii(reply["code"])
        return {"code": code, "toast": code.upper().replace("_", " ")}
    return {"code": "busy", "toast": "MIND OFFLINE"}


class ModeRelay:
    """The ear's link to mind for hello's modes (step 1b): reports and digit inputs go up, nothing is pushed down.
    call is mind_client(...) (or a test fake)."""

    def __init__(self, call, *, input_timeout_s=5.0, input_queue=8, report_queue=16, report_timeout_s=5.0,
                 report_retry_s=2.0, post_timeout_s=10.0):
        self.call, self.input_timeout_s, self.input_queue = call, input_timeout_s, input_queue
        self.report_queue, self.report_timeout_s, self.report_retry_s = report_queue, report_timeout_s, report_retry_s
        self.post_timeout_s = post_timeout_s
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="mode-mind")
        self._bg = set()

    async def ask(self, method, path, body=None, timeout=None):
        """(status, reply) from mind, or (None, {}) when it does not answer within timeout. Runs on the relay's own
        pool; a timed-out call's thread ends by itself within the urllib timeout (timeout + 1 s)."""
        if timeout is None:
            timeout = self.post_timeout_s
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(self._pool, self.call, method, path, body, timeout + 1)
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return None, {}

    def close(self):
        """Let the pool go (shutdown / tests); calls still running finish on their own."""
        self._pool.shutdown(wait=False, cancel_futures=True)

    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self._bg.add(task)
        task.add_done_callback(self._done)
        return task

    def _done(self, task):
        self._bg.discard(task)
        if not task.cancelled() and task.exception() is not None:
            e = task.exception()
            text = "".join(traceback.format_exception(type(e), e, e.__traceback__)).rstrip()
            log(f"mode: background task failed\n{text}", flush=True)

    async def drain(self):
        """Wait for background posts (closed events) to finish; tests and shutdown."""
        while self._bg:
            await asyncio.gather(*list(self._bg), return_exceptions=True)


def _parse(message):
    """A text frame's JSON object, else None (binary, not JSON, not an object)."""
    if not isinstance(message, str):
        return None
    try:
        m = json.loads(message)
    except ValueError:
        return None
    return m if isinstance(m, dict) else None


def _stream(m):
    sid = m.get("stream")
    return sid if isinstance(sid, int) and not isinstance(sid, bool) else None


class _RobotGone(Exception):
    pass


class MouthLink:
    def __init__(self, client, *, upstream_url, token, connect=None, retry_s=5, conn=0, modes=None):
        if connect is None:
            from websockets.asyncio.client import connect
        self.client, self.upstream_url, self.token, self.connect = client, upstream_url, token, connect
        self.retry_s, self.conn, self.modes = retry_s, conn, modes
        self._modes_on = False              # mode messages are relayed (needs a boot_id)
        self._inputs = asyncio.Queue(maxsize=modes.input_queue) if modes is not None else None
        self._reports = collections.deque()  # (message, queued at), oldest first
        self._report_new = asyncio.Event()
        self.reported = False               # mind took a report from this link: it hears the close too
        self.boot = None                    # the robot's boot_id from its hello
        self.device = None
        self.hello_text = None              # the robot's hello as received, replayed on every upstream dial
        self.playing = set()                # streams soul-tts started that are neither ended nor stopped
        self.upstream_drops = 0
        self._up = None
        self._lock = asyncio.Lock()         # robot reports wait while a fresh upstream is being greeted

    @property
    def upstream_up(self):
        return self._up is not None

    async def run(self):
        """Serve the robot until it leaves; returns why ("device_closed", "upstream_replaced", "no_hello", "bad_hello")."""
        why = await self._hello()
        if why:
            return why
        reader = asyncio.create_task(self._from_robot())
        upper = asyncio.create_task(self._upstream_loop())
        aux = []
        if self.modes is not None and not (isinstance(self.boot, str) and self.boot):
            log(f"mouth m{self.conn}: robot hello has no boot_id, no modes", flush=True)
        elif self.modes is not None:
            self._modes_on = True
            aux = [asyncio.create_task(self._input_worker()), asyncio.create_task(self._report_worker())]
        try:
            await asyncio.wait({reader, upper}, return_when=asyncio.FIRST_COMPLETED)
        finally:                                        # also when the ear cancels this handler
            first_upper = upper.done() and not reader.done()
            for task in (reader, upper, *aux):
                task.cancel()
            await asyncio.gather(reader, upper, *aux, return_exceptions=True)   # a dropped link ends one with ConnectionClosed
            if self._modes_on and self.reported:        # outlives run(): mind marks this link closed
                self.modes.spawn(self.modes.ask("POST", "/mode/robot",
                                                {"boot": self.boot, "conn": self.conn, "event": "closed"}))
            for name, task in (("reader", reader), ("soul-tts loop", upper)):
                e = None if task.cancelled() else task.exception()
                if e is not None and not isinstance(e, (ConnectionClosed, _RobotGone)):
                    text = "".join(traceback.format_exception(type(e), e, e.__traceback__)).rstrip()
                    log(f"mouth m{self.conn} {name}: unexpected error\n{text}", flush=True)
            up, self._up = self._up, None
            if up is not None:
                await up.close()
        why = "device_closed"
        if first_upper and not upper.cancelled() and upper.exception() is None:
            why = upper.result()
        if why == "upstream_replaced":
            await self.client.close(code=REPLACED, reason="replaced by new connection")
        else:
            await self.client.close()
        return why

    async def _hello(self):
        try:
            first = await asyncio.wait_for(self.client.recv(), HELLO_TIMEOUT_S)
        except asyncio.TimeoutError:
            await self.client.close(code=4408, reason="no hello")
            return "no_hello"
        except ConnectionClosed:                        # robot left before its hello
            return "device_closed"
        except Exception:                               # noqa: BLE001 - anything else: logged, the link ends
            _trace(f"mouth m{self.conn} hello")
            return "device_closed"
        m = _parse(first)
        if m is None or m.get("type") != "hello":
            await self.client.close(code=4400, reason="first message must be hello")
            return "bad_hello"
        self.hello_text, self.boot, self.device = first, m.get("boot_id"), m.get("device_id")
        try:
            await self.client.send(EAR_HELLO)
        except ConnectionClosed:                        # robot left right after its hello
            return "device_closed"
        except Exception:                               # noqa: BLE001
            _trace(f"mouth m{self.conn} hello reply")
            return "device_closed"
        return None

    async def _from_robot(self):
        async for message in self.client:
            m = _parse(message)
            if m is not None and m.get("type") in ("mode_input", "mode_report", "mode_applied"):
                if self._modes_on:                      # for mind, never for soul-tts
                    try:
                        await self._mode_message(m)
                    except ConnectionClosed:
                        raise
                    except Exception:               # noqa: BLE001 - a mode bug never ends /mouth
                        _trace(f"mouth m{self.conn} {m.get('type')}")
                continue
            if m is not None and m.get("type") in ("tts.done", "tts.stopped", "tts.error"):
                self.playing.discard(_stream(m))
            async with self._lock:
                up = self._up
                if up is None:
                    continue                            # soul-tts away: it has forgotten these streams
                try:
                    await up.send(message)
                except ConnectionClosed:                # the upstream loop sees the drop
                    pass
                except Exception:                       # noqa: BLE001
                    _trace(f"mouth m{self.conn} report to soul-tts")

    async def _to_robot(self, message):
        try:
            await self.client.send(message)
        except ConnectionClosed as e:                   # robot gone: run() ends via the reader
            raise _RobotGone() from e
        except Exception as e:                          # noqa: BLE001
            _trace(f"mouth m{self.conn} send to robot")
            raise _RobotGone() from e

    async def _upstream_loop(self):
        """Keep a soul-tts link while the robot is here; returns "upstream_replaced" or "device_closed"."""
        while True:
            code = await self._one_upstream()
            for sid in sorted(self.playing):
                try:
                    await self._to_robot(json.dumps({"type": "tts.stop", "stream": sid}))
                except _RobotGone:
                    return "device_closed"
            self.playing.clear()
            if code == REPLACED:
                return "upstream_replaced"
            await asyncio.sleep(self.retry_s)

    async def _one_upstream(self):
        """One dial and session; returns soul-tts's close code (None: could not connect)."""
        try:
            up = await self.connect(self.upstream_url, additional_headers={"Authorization": f"Bearer {self.token}"},
                                    max_size=64 * 1024, open_timeout=5)
        except (OSError, asyncio.TimeoutError, InvalidHandshake, InvalidURI):   # soul-tts down or refusing
            return None
        except Exception:                               # noqa: BLE001
            _trace(f"mouth m{self.conn} dial soul-tts")
            return None
        try:
            async with self._lock:
                await up.send(self.hello_text)
                self._up = up
            log(f"mouth m{self.conn}: soul-tts up", flush=True)
            async for message in up:
                m = _parse(message)
                if m is not None:
                    kind, sid = m.get("type"), _stream(m)
                    if kind == "hello":
                        continue                        # the robot already has the ear's hello
                    if kind == "tts.start" and sid is not None:
                        self.playing.add(sid)
                    elif kind in ("tts.end", "tts.stop"):
                        self.playing.discard(sid)
                await self._to_robot(message)
        except _RobotGone:
            raise
        except ConnectionClosed:                        # soul-tts dropped
            pass
        except Exception:                               # noqa: BLE001 - logged; treated as a drop
            _trace(f"mouth m{self.conn} soul-tts link")
        finally:
            if self._up is up:
                self._up = None
            await up.close()
        self.upstream_drops += 1
        log(f"mouth m{self.conn}: soul-tts down ({up.close_code})", flush=True)
        return up.close_code

    # ---- modes (step 1b) ----
    async def _send_result(self, seq, res):
        try:
            await self.client.send(json.dumps({"type": "mode_result", "seq": seq, **res}))
            return True
        except ConnectionClosed:
            return False

    async def _mode_message(self, m):
        if m["type"] == "mode_applied":
            return                                      # pre-1b test firmware: nothing pushes snapshots any more
        seq = m.get("seq")
        if not _is_int(seq) or seq < 0:
            log(f"mouth m{self.conn}: {m.get('type')} without an integer seq, dropped", flush=True)
            return
        if m["type"] == "mode_report":
            if len(self._reports) >= self.modes.report_queue:
                old, _ = self._reports.popleft()
                log(f"mouth m{self.conn}: report queue full, report {old.get('seq')} dropped", flush=True)
            self._reports.append((m, time.monotonic()))
            self._report_new.set()
            return
        try:
            self._inputs.put_nowait((m, time.monotonic()))
        except asyncio.QueueFull:
            await self._send_result(seq, {"code": "busy", "toast": "BUSY"})

    async def _input_worker(self):
        hub = self.modes
        while True:
            m, queued = await self._inputs.get()
            try:
                age = m.get("age_ms")
                age = (age if _is_int(age) and age >= 0 else 0) + int((time.monotonic() - queued) * 1000)
                body = {"device": "robot", "boot": self.boot, "seq": m["seq"], "age_ms": age,
                        "source": m.get("source"), "input": m.get("input"), "value": m.get("value")}
                status, reply = await hub.ask("POST", "/mode/input", body, timeout=hub.input_timeout_s)
                if not await self._send_result(m["seq"], _result(status, reply)):
                    return
            except Exception:                           # noqa: BLE001 - one bad input must not stop the rest
                _trace(f"mouth m{self.conn} mode input")

    async def _report_worker(self):
        """hello's reports to mind, in order; one that mind could not take is retried (it never jumps the queue)."""
        hub = self.modes
        while True:
            while not self._reports:
                self._report_new.clear()
                await self._report_new.wait()
            m, queued = self._reports.popleft()
            try:
                body = {"boot": self.boot, "conn": self.conn, "age_ms": int((time.monotonic() - queued) * 1000),
                        **{k: m.get(k) for k in REPORT_FIELDS}}
                if not isinstance(body["effects"], list):
                    body["effects"] = []
                status, reply = await hub.ask("POST", "/mode/report", body, timeout=hub.report_timeout_s)
            except Exception:                           # noqa: BLE001 - one bad report must not stop the rest
                _trace(f"mouth m{self.conn} mode report")
                continue
            if status == 200:
                self.reported = True
                res = reply.get("result") or {}
                if res.get("code") not in (None, "ok"):
                    log(f"mouth m{self.conn}: report {m['seq']} {_ascii(m.get('mode'), 12)}: {_ascii(res.get('code'))}",
                        flush=True)
            elif status in (400, 404, 409):
                log(f"mouth m{self.conn}: report {m['seq']} refused by mind ({status} "
                    f"{_ascii(reply.get('error'), 80)}), dropped", flush=True)
            else:                                       # mind away or slow: the same report again, first in line
                if len(self._reports) < hub.report_queue:
                    self._reports.appendleft((m, queued))
                else:
                    log(f"mouth m{self.conn}: report queue full, report {m['seq']} dropped", flush=True)
                await asyncio.sleep(hub.report_retry_s)
