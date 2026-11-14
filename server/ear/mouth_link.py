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

Modes (Task A5, with modes=ModeHub): mind owns the mode; the ear only carries it. None of this goes to soul-tts.
- After the robot's hello: POST mind /mode/robot {boot, conn, event:"hello"} (retried every hello_retry_s while
  mind is away), then the current snapshot goes down as {"type":"mode", ...snapshot}. The robot leaves:
  /mode/robot {boot, conn, event:"closed"}; conn is this link's number, so mind ignores a late close of an old link.
- One ModeHub task asks mind GET /mode/seq every poll_s while a robot is here; when the seq differs (!=, it can go
  back after a mind restart) it fetches GET /mode and pushes it to every link whose last seq differs; every full_s
  it pushes to all regardless. Fetches and pushes are serialised, so a link never gets an older fetch after a newer.
  mind away: nothing is pushed, the robot stays. A snapshot over 1 KB is not sent (logged).
- Robot {"type":"mode_input",seq,age_ms,source,input,value} -> a bounded queue (input_queue) -> one worker POSTs
  /mode/input {device:"robot", boot, seq, age_ms + time queued, source, input, value} and answers
  {"type":"mode_result","seq":<robot seq>,"code":..,"toast":..}, then the hub pushes the new snapshot. Never on the
  audio path: queue full -> busy "BUSY" at once; no answer in input_timeout_s -> busy "MIND SLOW" (mind may still
  apply it; the next snapshot shows it); mind down -> busy "MIND OFFLINE"; 400 -> unknown_input "BAD INPUT".
- Robot {"type":"mode_applied","seq":S} -> /mode/robot {boot, conn, event:"applied", applied_seq:S} (latest only).
A robot that ignores these types (0.4.91) just drops the mode messages; audio and the link are unchanged.
mode_result and the new snapshot can reach the robot in either order (the 1 s poll may see the change first).
mind is asked on the hub's own small thread pool (never the default executor, which tingwu and switches use).
Writers: several tasks send() to the robot socket (the upstream loop, hub pushes, input results). websockets writes
each whole message atomically, so that is safe; never use fragmented sends (async iterators) on the robot ws.
An unexpected error is logged with its traceback: in the mode path it is caught there and /mouth goes on; a task
that dies anyway is logged before run() ends.
"""
import asyncio
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
MAX_MODE_BYTES = 1024                               # per /mouth mode message (robot's receive cap is 1600 B)
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


class ModeHub:
    """The ear's one view of mind's mode: polls the seq, fetches and pushes snapshots to the joined links.
    call is mind_client(...) (or a test fake); links are MouthLinks (A6 adds /signal links the same way)."""

    def __init__(self, call, *, poll_s=1.0, full_s=60.0, input_timeout_s=5.0, input_queue=8, hello_retry_s=5.0,
                 get_timeout_s=3.0, post_timeout_s=10.0):
        self.call, self.poll_s, self.full_s = call, poll_s, full_s
        self.input_timeout_s, self.input_queue, self.hello_retry_s = input_timeout_s, input_queue, hello_retry_s
        self.get_timeout_s, self.post_timeout_s = get_timeout_s, post_timeout_s
        self.links = set()
        self.seq = None                      # the seq of the last snapshot fetched
        self.mind_up = None                  # decided by /mode/seq only
        self.fetch_failing = False           # GET /mode failing while /mode/seq answers (logged once per streak)
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="mode-mind")
        self._last_full = time.monotonic()
        self._lock = None                    # one fetch-and-push at a time: links never get an older fetch later
        self._bg = set()                     # (made on first use, inside the running loop)

    async def ask(self, method, path, body=None, timeout=None):
        """(status, reply) from mind, or (None, {}) when it does not answer within timeout. Runs on the hub's own
        pool; a timed-out call's thread ends by itself within the urllib timeout (timeout + 1 s)."""
        if timeout is None:
            timeout = self.get_timeout_s if method == "GET" else self.post_timeout_s
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(self._pool, self.call, method, path, body, timeout + 1)
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return None, {}

    def close(self):
        """Let the pool go (shutdown / tests); calls still running finish on their own."""
        self._pool.shutdown(wait=False, cancel_futures=True)

    def join(self, link):
        self.links.add(link)

    def leave(self, link):
        self.links.discard(link)

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

    def _mark(self, up):
        if up == self.mind_up:
            return
        was, self.mind_up = self.mind_up, up
        log(f"mode: mind {'up' if up else 'away'}", flush=True)
        if up and was is False:              # mind came back (maybe restarted): tell it again who is connected
            for link in list(self.links):
                rehello = getattr(link, "request_rehello", None)
                if rehello is not None:
                    rehello()

    async def run(self):
        while True:
            try:
                await self.tick()
            except Exception:                            # noqa: BLE001 - keep polling
                _trace("mode poll")
            await asyncio.sleep(self.poll_s)

    async def tick(self):
        if not self.links:
            return                                       # no robot here: no polling
        status, reply = await self.ask("GET", "/mode/seq")
        if status != 200 or not _is_int(reply.get("seq")):
            self._mark(False)
            return
        self._mark(True)
        full = time.monotonic() - self._last_full >= self.full_s
        if full or reply["seq"] != self.seq or any(link.mode_wants_first for link in self.links):
            await self.refresh(force=full)

    async def refresh(self, force=False):
        """GET /mode and push it: to links whose last seq differs, or to all with force. -> snapshot or None."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            status, reply = await self.ask("GET", "/mode")
            snap = reply.get("snapshot") if status == 200 else None
            if not (isinstance(snap, dict) and _is_int(snap.get("seq"))):
                if not self.fetch_failing:        # up/away is /mode/seq's call; this is logged once per streak
                    self.fetch_failing = True
                    log(f"mode: GET /mode failing ({status if status is not None else 'timeout'}), no pushes", flush=True)
                return None
            if self.fetch_failing:
                self.fetch_failing = False
                log("mode: GET /mode ok again", flush=True)
            if snap["seq"] != self.seq:
                log(f"mode: seq {snap['seq']} {_ascii(snap.get('mode'), 12)} to {len(self.links)} link(s)", flush=True)
            self.seq = snap["seq"]
            if force:
                self._last_full = time.monotonic()
            for link in list(self.links):
                await link.push_mode(snap, force=force)
            return snap


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
        self.mode_seq = None                # seq of the last snapshot sent to this robot
        self.mode_hello_ok = asyncio.Event()
        self._mode_hello_tried = False
        self._mode_closing = asyncio.Event()
        self._mode_wake = asyncio.Event()   # closing or a re-hello request wakes the session
        self._modes_on = False              # joined to the hub (needs a boot_id)
        self._rehello = False
        self._mode_too_large = None
        self._inputs = asyncio.Queue(maxsize=modes.input_queue) if modes is not None else None
        self._applied, self._applied_new = None, asyncio.Event()
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
            self.modes.join(self)
            aux = [asyncio.create_task(self._input_worker()), asyncio.create_task(self._ack_worker())]
            self.modes.spawn(self._mode_session())      # outlives run(): it posts the closed event
        try:
            await asyncio.wait({reader, upper}, return_when=asyncio.FIRST_COMPLETED)
        finally:                                        # also when the ear cancels this handler
            first_upper = upper.done() and not reader.done()
            if self.modes is not None:
                self.modes.leave(self)
                self._mode_closing.set()
                self._mode_wake.set()
            for task in (reader, upper, *aux):
                task.cancel()
            await asyncio.gather(reader, upper, *aux, return_exceptions=True)   # a dropped link ends one with ConnectionClosed
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
            if m is not None and m.get("type") in ("mode_input", "mode_applied"):
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

    # ---- modes (Task A5) ----
    @property
    def mode_wants_first(self):
        """No snapshot sent yet (and none was skipped as too large): the hub fetches for this link."""
        return self.mode_seq is None and self._mode_too_large is None

    async def push_mode(self, snap, force=False):
        """Send a snapshot as {"type":"mode",...} unless this robot already has that seq (force: send anyway)."""
        if not force and snap["seq"] == self.mode_seq:
            return
        text = json.dumps({"type": "mode", **{k: v for k, v in snap.items() if k != "type"}}, separators=(",", ":"))
        if len(text.encode()) > MAX_MODE_BYTES:
            if self._mode_too_large != snap["seq"]:
                self._mode_too_large = snap["seq"]
                log(f"mouth m{self.conn}: mode seq {snap['seq']} too large ({len(text.encode())} B), not sent", flush=True)
            return
        try:
            await self.client.send(text)
        except ConnectionClosed:                        # robot gone: run() ends via the reader
            return
        except Exception:                               # noqa: BLE001
            _trace(f"mouth m{self.conn} send mode")
            return
        self.mode_seq = snap["seq"]

    async def _send_result(self, seq, res):
        try:
            await self.client.send(json.dumps({"type": "mode_result", "seq": seq, **res}))
            return True
        except ConnectionClosed:
            return False

    async def _mode_message(self, m):
        seq = m.get("seq")
        if not _is_int(seq) or seq < 0:
            log(f"mouth m{self.conn}: {m.get('type')} without an integer seq, dropped", flush=True)
            return
        if m["type"] == "mode_applied":
            self._applied = seq
            self._applied_new.set()
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
                if status == 200:
                    await hub.refresh()
            except Exception:                           # noqa: BLE001 - one bad input must not stop the rest
                _trace(f"mouth m{self.conn} mode input")

    async def _ack_worker(self):
        hub = self.modes
        await self.mode_hello_ok.wait()                 # mind learns this conn before its acks
        while True:
            await self._applied_new.wait()
            self._applied_new.clear()
            try:
                await hub.ask("POST", "/mode/robot", {"boot": self.boot, "conn": self.conn, "event": "applied",
                                                      "applied_seq": self._applied})
            except Exception:                           # noqa: BLE001
                _trace(f"mouth m{self.conn} mode applied")

    async def _hello_mind(self):
        self._mode_hello_tried = True
        status, _ = await self.modes.ask("POST", "/mode/robot", {"boot": self.boot, "conn": self.conn, "event": "hello"})
        return status == 200

    def request_rehello(self):
        """mind came back after being away: the session says hello again (same boot: not a reboot for mind),
        ordered before its closed post, and then re-posts the last ack (a hello resets mind's applied_seq)."""
        if self.mode_hello_ok.is_set() and not self._mode_closing.is_set():
            self._rehello = True
            self._mode_wake.set()

    async def _mode_session(self):
        """hello (retried while mind is away), first snapshot, then wait for the robot to leave and post closed."""
        hub = self.modes
        while not self._mode_closing.is_set():
            if await self._hello_mind():
                self.mode_hello_ok.set()
                await hub.refresh()                     # newest snapshot, through the hub so order holds
                break
            try:
                await asyncio.wait_for(self._mode_closing.wait(), hub.hello_retry_s)
            except asyncio.TimeoutError:
                pass
        while not self._mode_closing.is_set():
            await self._mode_wake.wait()
            self._mode_wake.clear()
            if self._rehello and not self._mode_closing.is_set():
                self._rehello = False
                if await self._hello_mind() and self._applied is not None:
                    self._applied_new.set()         # the ack worker re-posts the last applied seq
        if self._mode_hello_tried:
            await hub.ask("POST", "/mode/robot", {"boot": self.boot, "conn": self.conn, "event": "closed"})
