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
Chat v0 (2026-11-14): the relay remembers hello's mode from the reports as they arrive (robot_mode; cleared when the link
that reported it closes). While it is `chat`, the ear's end_speech calls chat_heard(ev, session) for every sentence
with text: a non-blocking POST mind /chat/heard {text, lang, emotion, event, speaker, voice, loud_db (above the ear's
floor), self, session, id, pos, t, dur, debug, sent_at} on its own small pool (chat_max_inflight threads, so a hung
mind can never take the mode pool's threads). At most chat_max_inflight posts at a time: a slot frees only when its call
has really ended (more: dropped and logged); the ear stops waiting after chat_timeout_s; no retry (a late sentence is
no use). The text is never logged.
Stage indicator (2026-11-14, protocol from the coordinator; the robot draws dots): while the relayed mode is chat the
ear sends the robot {"type":"chat_stage","stage":"listen"|"think"|"skip","at_ms":<epoch ms>} on the /mouth link that
reported the mode: listen at a speech onset (chat_listen: at most one per STAGE_DEBOUNCE_S, the ear never calls it
while hello sounds; mind gets a small /chat/stage note too, only when no chat post is in flight), think / skip from
mind's /chat/heard answer (accepted / skip), skip for a sentence without text (chat_nothing). Mind slow or down:
nothing (the robot times out). speak is not sent: the robot sees its own playback. Sends are tasks on the loop,
at most STAGE_INFLIGHT at a time (more: dropped), never awaited by the audio path.
mind is asked on the relay's own small thread pool (never the default executor, which tingwu and switches use).
围观 realtime (spike 2026-11-14, realtime=RealtimeHub, realtime_link.py): its streams use ids from RT_STREAM_BASE up and go
to the robot with send_from_realtime(); the robot's tts.* reports for those ids go to the hub, never to soul-tts.
tts_active holds the soul-tts streams the robot has not finished (start .. robot done / stopped / error, or soul-tts's
stop); a soul-tts tts.start first calls realtime.yield_to_tts(link) (realtime yields), so the two never interleave.
A mode_report with mode crowd and input tap is also handed to realtime.tap() (crowd: a tap = look and greet).
Place (2026-11-15, spec docs/superpowers/specs/2026-11-15-place-contract.md; ModeRelay(place_poll_s=..)): after each
/mouth handshake (links with a boot_id) the ear GETs mind /place and sends the robot {"type":"place","current":..,"by":..,
"choices":[..],"at_ms":..} (UTF-8 strings, <= 1600 B: at most 8 choices of <= 24 B, current <= 48 B); while a robot is
linked it asks mind again every place_poll_s (loopback, free) and pushes a frame only when it changed. The robot's
{"type":"place_select","seq":N,"choice":<text|null = unknown>} goes to mind POST /place {label, source: robot} (never to
soul-tts, one at a time per link, never awaited by the reader) and is answered {"type":"place_result","seq":N,"code":
ok|bad_choice|busy,"toast":ASCII<=32}; on ok every link gets the new place frame. Older firmware ignores type place.
视觉爆发 (2026-11-15, spec docs/superpowers/specs/2026-11-15-vision-burst-snap.md; MouthLink(burst=VisionBurst)): the
burst sends {"type":"snap","id":N} with send_from_realtime(); the robot's {"type":"snap_result",...} goes to
burst.on_snap_result(link, m) and never to soul-tts (dropped when there is no burst).
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

import ear_log

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
OWNER_BY = ("asr", "touch", "select", "quick")         # a report from one of these with an input = an owner's input
STATE_FIELDS = ("mode", "dir", "host", "return_to")           # hello's mode state in a report (run log robot.state)
STAGE_DEBOUNCE_S = 0.3
STAGE_INFLIGHT = 4
RT_STREAM_BASE = 0x40000000                         # realtime's /mouth stream ids (soul-tts counts from 1)
# Place (2026-11-15, spec docs/superpowers/specs/2026-11-15-place-contract.md): the robot's left-swipe info page.
PLACE_MAX_CHOICES = 8
PLACE_CHOICE_BYTES = 24                             # UTF-8 bytes per choice (8 CJK characters)
PLACE_CURRENT_BYTES = 48                            # the current place in the frame (mind allows 40 characters)
PLACE_FRAME_MAX = 1600                              # the robot's /mouth text limit (kMaxMessage)
PLACE_BY = ("page", "robot", "voice")
log = print                                         # tests silence it


def _trace(what):
    """Log an unexpected exception with its traceback (call inside an except block)."""
    log(f"{what}: unexpected error\n{traceback.format_exc().rstrip()}", flush=True)


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _ascii(text, n=32):
    return "".join(c for c in str(text or "") if 32 <= ord(c) < 127)[:n]


def _utf8(text, n):
    """Text without control characters, cut to at most n UTF-8 bytes on a character boundary."""
    t = "".join(c for c in str(text) if ord(c) >= 32 and ord(c) != 127).strip()
    return t.encode()[:n].decode("utf-8", "ignore")


def place_frame(st):
    """mind's GET /place (or a POST /place answer) -> the robot's place frame text, or None when it is not one.
    {"type":"place","current":<text|null>,"by":<page|robot|voice|null>,"choices":[<text>...],"at_ms":<int|null>}:
    compact JSON, UTF-8 strings (not \\u escapes), at most PLACE_FRAME_MAX bytes by construction."""
    if not isinstance(st, dict) or not isinstance(st.get("choices"), list):
        return None
    choices = []
    for c in st["choices"]:
        c = _utf8(c, PLACE_CHOICE_BYTES) if isinstance(c, str) else ""
        if c and c not in choices:
            choices.append(c)
    cur = _utf8(st["current"], PLACE_CURRENT_BYTES) if isinstance(st.get("current"), str) else ""
    frame = {"type": "place", "current": cur or None, "by": st.get("by") if st.get("by") in PLACE_BY else None,
             "choices": choices[:PLACE_MAX_CHOICES], "at_ms": st["at"] if _is_int(st.get("at")) else None}
    text = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
    return text if len(text.encode()) <= PLACE_FRAME_MAX else None


def _place_result(status, reply):
    """mind's answer to the robot's pick -> {code, toast} (ASCII toast <= 32, like mode_result)."""
    if status == 200:
        return {"code": "ok", "toast": "PLACE SET"}
    if status == 400:
        return {"code": "bad_choice", "toast": "NOT IN LIST"}
    return {"code": "busy", "toast": "MIND SLOW" if status is None else "MIND OFFLINE"}


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
                 report_retry_s=2.0, post_timeout_s=10.0, chat_max_inflight=2, chat_timeout_s=3.0,
                 place_poll_s=None, place_timeout_s=3.0):
        self.call, self.input_timeout_s, self.input_queue = call, input_timeout_s, input_queue
        self.report_queue, self.report_timeout_s, self.report_retry_s = report_queue, report_timeout_s, report_retry_s
        self.post_timeout_s = post_timeout_s
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="mode-mind")
        self._bg = set()
        self.robot_mode, self._mode_conn = None, None      # hello's mode as last reported, and which link said it
        self._links = {}                                    # conn -> MouthLink (stage frames go to the reporting one)
        self._last_listen = -1e9
        self._stage_inflight = 0
        self.chat_inflight, self.chat_max_inflight, self.chat_timeout_s = 0, chat_max_inflight, chat_timeout_s
        self._chat_pool = concurrent.futures.ThreadPoolExecutor(max_workers=chat_max_inflight, thread_name_prefix="chat-mind")
        self.chat_sent = self.chat_dropped = 0
        self.place_poll_s, self.place_timeout_s = place_poll_s, place_timeout_s   # None: no place frames (tests)
        self._place_frame = None                            # the last place frame text built from mind's answer
        self._place_task = None

    # ---- chat v0 ----
    def note_report(self, m, conn):
        mode = m.get("mode")
        if isinstance(mode, str):
            self.robot_mode, self._mode_conn = _ascii(mode, 16), conn
            # run log: robot.state when hello's reported mode state changed (a repeated report records nothing)
            ear_log.robot_state("mode", {k: m.get(k) for k in STATE_FIELDS}, by=_ascii(m.get("by"), 16) or "robot",
                                conn=f"m{conn}", seq=m.get("seq"), input=_ascii(m.get("input"), 16) or None)

    def link_closed(self, conn):
        self._links.pop(conn, None)
        if self._mode_conn == conn:
            self.robot_mode, self._mode_conn = None, None

    def attach(self, link):
        self._links[link.conn] = link
        if self.place_on():
            self.spawn(self._place_hello(link))
            if self._place_task is None or self._place_task.done():
                self._place_task = self.spawn(self._place_poll())

    # ---- place (2026-11-15): the robot's info page shows and sets where hello is ----
    def place_on(self):
        return self.place_poll_s is not None

    async def _place_fetch(self):
        status, reply = await self.ask("GET", "/place", None, timeout=self.place_timeout_s)
        frame = place_frame(reply) if status == 200 else None
        if frame is not None:
            self._place_frame = frame
        return frame

    async def _place_send(self, link, frame):
        if frame is None or getattr(link, "place_sent", None) == frame:
            return
        try:
            await link.client.send(frame)
            link.place_sent = frame
        except Exception:                            # noqa: BLE001 - the robot left: its link ends by itself
            pass

    async def _place_push(self, frame):
        for link in list(self._links.values()):
            await self._place_send(link, frame)

    async def _place_hello(self, link):
        """Right after the /mouth handshake: the current place and the choices (mind away: the last known frame)."""
        frame = await self._place_fetch()
        await self._place_send(link, frame or self._place_frame)

    async def _place_poll(self):
        """While a robot is linked: ask mind every place_poll_s (loopback, free) and push a changed frame to every link."""
        while self._links:
            await asyncio.sleep(self.place_poll_s)
            if not self._links:
                break
            frame = await self._place_fetch()
            if frame is not None:
                await self._place_push(frame)

    async def place_select(self, link, m):
        """The robot's {"type":"place_select","seq":N,"choice":<text|null>} -> POST mind /place {label, source:
        robot}; answers {"type":"place_result","seq":N,"code","toast"} and, when it took, pushes the new place."""
        seq, choice = m.get("seq"), m.get("choice")
        if not _is_int(seq) or seq < 0:
            log(f"mouth m{link.conn}: place_select without an integer seq, dropped", flush=True)
            return
        if choice is not None and not (isinstance(choice, str) and choice.strip()):
            res, status, reply = {"code": "bad_choice", "toast": "BAD CHOICE"}, 400, {}
        else:
            status, reply = await self.ask("POST", "/place", {"label": choice, "source": "robot"},
                                           timeout=self.place_timeout_s)
            res = _place_result(status, reply)
        try:
            await link.client.send(json.dumps({"type": "place_result", "seq": seq, **res}))
        except Exception:                            # noqa: BLE001 - the robot left
            return
        frame = place_frame(reply) if status == 200 else None
        if frame is not None:
            self._place_frame = frame
            await self._place_push(frame)
        elif status != 200 and self._place_frame is not None:
            await self._place_send(link, self._place_frame)     # the robot shows what is really set

    # ---- stage indicator ----
    def send_stage(self, stage):
        """Queue one chat_stage frame to the robot (non-blocking). -> True if queued."""
        link = self._links.get(self._mode_conn)
        if not self.in_stage_mode() or link is None or self._stage_inflight >= STAGE_INFLIGHT:
            return False
        msg = json.dumps({"type": "chat_stage", "stage": stage, "at_ms": int(time.time() * 1000)})
        self._stage_inflight += 1
        self.spawn(self._send_stage(link, msg))
        return True

    async def _send_stage(self, link, msg):
        try:
            await link.client.send(msg)
        except Exception:                            # noqa: BLE001 - the robot left: its link ends by itself
            pass
        finally:
            self._stage_inflight -= 1

    def chat_listen(self):
        """A speech onset in chat (the ear never calls this while hello sounds) -> True if listen was sent."""
        now = time.monotonic()
        if not self.in_stage_mode() or now - self._last_listen < STAGE_DEBOUNCE_S:
            return False
        if not self.send_stage("listen"):
            return False
        self._last_listen = now
        if self.in_chat() and self.chat_inflight == 0:                  # the page's dots; never takes a slot a sentence may need
            self.chat_inflight += 1
            self.spawn(self._post_stage({"stage": "listen", "at_ms": int(time.time() * 1000)}))
        return True

    async def _post_stage(self, body):
        loop = asyncio.get_running_loop()
        try:
            fut = loop.run_in_executor(self._chat_pool, self.call, "POST", "/chat/stage", body, self.chat_timeout_s + 1)
        except RuntimeError:
            self.chat_inflight -= 1
            return
        fut.add_done_callback(self._chat_done)
        try:
            await asyncio.wait_for(asyncio.shield(fut), self.chat_timeout_s)
        except asyncio.TimeoutError:
            pass

    def chat_nothing(self):
        """A speech end without text in chat: nothing to answer, the robot leaves listen. (In crowd the realtime
        session decides, so it sends think / skip itself: crowd_stage.)"""
        return self.in_chat() and self.send_stage("skip")

    def crowd_stage(self, stage):
        """Crowd's dots (owner 2026-11-15: 围观 gets chat's stage dots): realtime_link sends think when its server VAD
        ends a sentence and skip when a response brings no sound. Nothing outside crowd. -> True if queued."""
        return self.robot_mode == "crowd" and self.send_stage(stage)

    def in_chat(self):
        return self.robot_mode == "chat"

    def in_stage_mode(self):
        """The modes that show the stage dots on the robot: chat, and crowd since collector-0.4.97."""
        return self.robot_mode in ("chat", "crowd")

    def chat_heard(self, ev, session):
        """Call on the event loop with a speech end event: in chat, hand it to mind without waiting. -> True if sent."""
        if not self.in_chat() or not isinstance(ev, dict) or not str(ev.get("text") or "").strip():
            return False
        if self.chat_inflight >= self.chat_max_inflight:
            self.chat_dropped += 1
            log(f"chat: mind busy, sentence {_ascii(ev.get('id'), 16)} dropped", flush=True)
            return False
        body = chat_body(ev, session)
        self.chat_inflight += 1
        self.chat_sent += 1
        self.spawn(self._post_heard(body))
        return True

    async def _post_heard(self, body):
        loop = asyncio.get_running_loop()
        try:
            fut = loop.run_in_executor(self._chat_pool, self.call, "POST", "/chat/heard", body, self.chat_timeout_s + 1)
        except RuntimeError:                         # pool shut down (ear stopping)
            self.chat_inflight -= 1
            return
        fut.add_done_callback(self._chat_done)      # the slot frees when the call has really ended (review M1)
        try:
            status, reply = await asyncio.wait_for(asyncio.shield(fut), self.chat_timeout_s)
        except asyncio.TimeoutError:
            status, reply = None, {}
        if status == 200 and isinstance(reply, dict):              # the robot's dots: think / skip
            self.send_stage("think" if reply.get("accepted") else "skip")
        if status != 200:
            log(f"chat: sentence {_ascii(body.get('id'), 16)} not taken by mind ({status} "
                f"{_ascii(reply.get('error'), 60)})", flush=True)

    def _chat_done(self, fut):
        self.chat_inflight -= 1
        if not fut.cancelled():
            fut.exception()                          # retrieved: a failed call is already answered as 503

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
        self._chat_pool.shutdown(wait=False, cancel_futures=True)

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
            ear_log.error(e, "mode.task")

    async def drain(self):
        """Wait for background posts (closed events) to finish; tests and shutdown."""
        while self._bg:
            await asyncio.gather(*list(self._bg), return_exceptions=True)


def chat_body(ev, session):
    """The /chat/heard body from an ear speech end event (copies: the tick adds to ev's debug later)."""
    sp, v, me = ev.get("speaker") or {}, ev.get("voice") or {}, ev.get("self") or {}
    return {"session": session, "id": ev.get("id"), "pos": list(ev.get("pos") or []) or None, "t": ev.get("t"),
            "dur": ev.get("dur"), "text": ev.get("text"), "lang": ev.get("lang"), "emotion": ev.get("emotion"),
            "event": ev.get("event"), "speaker": {k: sp.get(k) for k in ("band", "name", "score")},
            "voice": {k: v.get(k) for k in ("p", "band", "acoustic_band", "loud_db")}, "loud_db": v.get("loud_db"),
            "self": {k: me.get(k) for k in ("sounding", "moving")}, "debug": dict(ev.get("debug") or {}),
            "speech_start_at": ev.get("t"),
            "trace": ev.get("trace"),                 # run log: mind's chat turn joins this sentence's trace
            "sent_at": int(time.time() * 1000)}


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
    def __init__(self, client, *, upstream_url, token, connect=None, retry_s=5, conn=0, modes=None, realtime=None,
                 burst=None, presence=None):
        if connect is None:
            from websockets.asyncio.client import connect
        self.client, self.upstream_url, self.token, self.connect = client, upstream_url, token, connect
        self.retry_s, self.conn, self.modes = retry_s, conn, modes
        self.realtime = realtime
        self.burst = burst                  # vision_burst.VisionBurst: the robot's snap_result goes there
        self.presence = presence            # presence.Presence: hello's reports of an owner's input (the pause)
        self.tts_active = set()             # soul-tts streams the robot has not reported finished
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
        self.place_sent = None              # the last place frame this robot got (unchanged frames are not re-sent)
        self.place_busy = False             # one place_select at a time per link
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
            self.modes.attach(self)
            aux = [asyncio.create_task(self._input_worker()), asyncio.create_task(self._report_worker())]
        try:
            await asyncio.wait({reader, upper}, return_when=asyncio.FIRST_COMPLETED)
        finally:                                        # also when the ear cancels this handler
            first_upper = upper.done() and not reader.done()
            for task in (reader, upper, *aux):
                task.cancel()
            await asyncio.gather(reader, upper, *aux, return_exceptions=True)   # a dropped link ends one with ConnectionClosed
            if self._modes_on:
                self.modes.link_closed(self.conn)       # chat: this link's mode is no longer hello's word
            if self.realtime is not None:
                try:
                    self.realtime.link_closed(self)
                except Exception:                       # noqa: BLE001
                    _trace(f"mouth m{self.conn} realtime close")
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
            if m is not None and m.get("type") == "snap_result":
                if self.burst is not None:              # vision burst (2026-11-15): never for soul-tts
                    try:
                        self.burst.on_snap_result(self, m)
                    except Exception:                   # noqa: BLE001 - a burst bug never ends /mouth
                        _trace(f"mouth m{self.conn} snap_result")
                continue
            if m is not None and m.get("type") == "place_select":
                if self._modes_on and self.modes.place_on():  # for mind, never for soul-tts; never awaited here
                    if self.place_busy:
                        await self._send_place_busy(m)
                    else:
                        self.place_busy = True
                        self.modes.spawn(self._place_select(m))
                continue
            if m is not None and m.get("type") in ("mode_input", "mode_report", "mode_applied"):
                if self._modes_on:                      # for mind, never for soul-tts
                    try:
                        await self._mode_message(m)
                    except ConnectionClosed:
                        raise
                    except Exception:               # noqa: BLE001 - a mode bug never ends /mouth
                        _trace(f"mouth m{self.conn} {m.get('type')}")
                continue
            sid = _stream(m) if m is not None else None
            if sid is not None and sid >= RT_STREAM_BASE and str(m.get("type", "")).startswith("tts."):
                if self.realtime is not None:           # realtime's own stream: its report, not soul-tts's
                    try:
                        self.realtime.on_report(self, m)
                    except Exception:                   # noqa: BLE001
                        _trace(f"mouth m{self.conn} realtime report")
                continue
            if m is not None and m.get("type") in ("tts.done", "tts.stopped", "tts.error"):
                self.playing.discard(_stream(m))
                self.tts_active.discard(_stream(m))
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

    async def _place_select(self, m):
        try:
            await self.modes.place_select(self, m)
        finally:
            self.place_busy = False

    async def _send_place_busy(self, m):
        if _is_int(m.get("seq")):
            try:
                await self.client.send(json.dumps({"type": "place_result", "seq": m["seq"], "code": "busy", "toast": "BUSY"}))
            except ConnectionClosed:
                raise
            except Exception:                           # noqa: BLE001
                pass

    async def send_from_realtime(self, message):
        """One whole message from the realtime hub to the robot -> False when the robot is gone."""
        try:
            await self.client.send(message)
            return True
        except Exception:                               # noqa: BLE001 - robot gone: run() ends via the reader
            return False

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
            self.tts_active.clear()
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
            ear_log.emit("link.upstream", sum="soul-tts up", extra={"to": "soul-tts", "state": "up", "conn": f"m{self.conn}"})
            async for message in up:
                m = _parse(message)
                if m is not None:
                    kind, sid = m.get("type"), _stream(m)
                    if kind == "hello":
                        continue                        # the robot already has the ear's hello
                    if kind == "tts.start" and sid is not None:
                        self.playing.add(sid)
                        self.tts_active.add(sid)
                        if self.realtime is not None:   # realtime yields before the robot hears this start
                            try:
                                self.realtime.yield_to_tts(self)
                            except Exception:           # noqa: BLE001
                                _trace(f"mouth m{self.conn} realtime yield")
                    elif kind in ("tts.end", "tts.stop"):
                        self.playing.discard(sid)
                        if kind == "tts.stop":
                            self.tts_active.discard(sid)
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
        ear_log.emit("link.upstream", sum="soul-tts down", extra={"to": "soul-tts", "state": "down", "conn": f"m{self.conn}",
                                                                  "code": up.close_code, "drops": self.upstream_drops})
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
            self.modes.note_report(m, self.conn)
            inp = m.get("input")
            if self.presence is not None and m.get("by") in OWNER_BY and isinstance(inp, str) and inp not in ("boot", "report"):
                try:                                    # an owner's input = someone is here (before the tap greets)
                    self.presence.owner(f"{m.get('by')}:{inp}")
                except Exception:                       # noqa: BLE001
                    _trace(f"mouth m{self.conn} presence")
            if self.realtime is not None and m.get("mode") == "crowd" and m.get("input") == "tap":
                try:
                    self.realtime.tap()
                except Exception:                       # noqa: BLE001 - a greeting never costs /mouth
                    _trace(f"mouth m{self.conn} realtime tap")
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
