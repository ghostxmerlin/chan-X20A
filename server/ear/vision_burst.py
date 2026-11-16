"""视觉爆发 vision burst (2026-11-15): while hello is in crowd, "注意看" makes the ear look photo by photo for 2 minutes.

Owner (verbatim): "这叫视觉爆发。只是拍照，识别，然后增加进情景或者任何下次对话可以用到的地方。" and "我们在meet中，让hello注意看，
则……持续2分钟" and "爆发模式就是拍照，上传，识别……再拍照" (no fixed interval: strictly one at a time). hello does not
speak because of a burst; the looks only go into what the next reply can use.

Wire contract (robot side collector-0.4.99, docs/superpowers/specs/2026-11-15-vision-burst-snap.md):
  ear -> robot on /mouth   {"type":"snap","id":<uint32>}
  robot -> ear on /mouth   {"type":"snap_result","id","ok":true,"seq","bytes","ms"} or {"id","ok":false,"why":
                           busy|camera_off|no_uplink|timeout}; MouthLink hands it to on_snap_result (never to soul-tts)
  robot -> ear on /vision  the photo as an SVF1 frame whose header has "snap":<id>; VisionLink stores it as usual and
                           hands it to on_frame. Frame and result may arrive in either order. A bad request gets no
                           reply and old firmware ignores snap: our own SNAP_TIMEOUT_S.

Trigger (heard(), the 围观 realtime session's input transcription): while the robot reports crowd and the realtime
switch is on (a paid feature: owner's 0 / 9 rule), a sentence with 注意看 / 仔细看 / 好好看看 starts a burst of BURST_S
(one already running is extended to BURST_S from now); 别看了 / 不用看了 / 停止看 stops it. It also stops when BURST_S
are up, hello leaves crowd, the /mouth link that reported crowd drops (or is replaced), the realtime switch goes off,
after MAX_LOOKS recognitions, or after MAX_FAILURES failures in a row. Start / extend / stop are logged with the reason
(never the heard text).

Loop (one task; exactly one snap or one recognition in flight): snap(id) on the current mouth link -> wait for its
snap_result (SNAP_TIMEOUT_S) and, when ok, its frame (FRAME_WAIT_S more) -> recognise that one JPEG with qwen3.8-flash
direct on DashScope (thinking off, MAX_TOKENS, LOOK_TIMEOUT_S; on its own one-thread pool, never on the event loop) ->
store -> the next snap at once. A failure (no reply, busy, camera_off, no_uplink, timeout, no frame, recognition
error) waits RETRY_S, then goes on.

Each look: one hello.vision_looks document {kind: look, at, session, seq, snap_id, path, text, latency_ms, snap_ms,
usage, cost_usd, burst_id} and hub.add_look() (the realtime instructions' 【你的眼睛】 shows the latest few as 注视；mind
reads vision_looks for chat and the realtime context). At the end one {kind: burst} summary row. The console log has
ids, timings and counts only, never the description text. The run log (ear_log, 2026-11-16) has trigger.burst (start /
extend / refused / stop / end) and one call.look per recognition (prompt, the photo as its size only, the reply, usage,
cost; ref = frame path + lid), all on the trace of the sentence that started the burst. Frames stay where VisionLink
put them (recordings/frames/).
"""
import asyncio
import base64
import collections
import concurrent.futures
import contextvars
import itertools
import json
import time
import traceback
import urllib.error
import urllib.request

import ear_log

# Same model / endpoint / price as mind/eye.py (2026-11-15 bake-off, owner chose qwen3.8-flash on DashScope direct;
# the ear does not ship mind, so the constants are repeated here).
LOOK_MODEL = "qwen3.8-flash"
LOOK_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
LOOK_EXTRA = {"enable_thinking": False}
PRICE_USD_PER_M = (0.1126, 0.380025)           # (input, output) USD per million tokens, as mind/llm.PRICES
PRICE_BASIS = "vision_burst.PRICE_USD_PER_M = mind llm.PRICES qwen3.8-flash 2026-11-15"
LOOK_PROMPT = "look-v1"

# 2026-11-15 evening: the owner's own first try was "你看看我手里拿的是什么东西" (no burst); 你看看 / 看看这 / 你看这 / 看一下
# start one too (a burst costs ~0.001 USD a minute).
START_WORDS = ("注意看", "仔细看", "好好看看", "你看看", "看看这", "看看我", "你看这", "看一下")
STOP_WORDS = ("别看了", "不用看了", "停止看")
BURST_S = 120.0
MAX_LOOKS = 120
MAX_FAILURES = 3
SNAP_TIMEOUT_S = 3.0
FRAME_WAIT_S = 1.0
RETRY_S = 1.0
LOOK_TIMEOUT_S = 8.0
MAX_TOKENS = 200
POLL_S = 0.2
UINT32 = 1 << 32
log = print                                     # tests silence it

LOOK_SYSTEM = ("你是宠物机器人 hello 的眼睛。hello 个头很小，主人常带着它走，可能在任何地方；机身摄像头 320×240，"
               "画面只是 hello 正看着的方向。主人让 hello“注意看”，你一张一张看它刚拍下的照片。")
LOOK_USER = ("下面是 hello 刚刚拍下的一张照片。用中文写一两句短句（不超过 60 字），说画面里现在有谁、在哪、在做什么，周围什么样。"
             "人脸识别认出的名字可信；没认出的人说“一个人”“一个小朋友”等，不要猜名字（宁可叫不出名字，也不要叫错），"
             "不写年龄性别。只写看到的，看不清就直说。不要用“我看到”开头，不提建议。")


def cost_usd(usage):
    if not isinstance(usage, dict):
        return None
    pin, pout = PRICE_USD_PER_M
    n = lambda k: usage.get(k) if isinstance(usage.get(k), (int, float)) and not isinstance(usage.get(k), bool) else 0  # noqa: E731
    return round((n("prompt_tokens") * pin + n("completion_tokens") * pout) / 1e6, 12)


def look_messages(jpeg, seen=None):
    text = LOOK_USER + (f"\n人脸识别此刻：{seen}" if seen else "")
    return [{"role": "system", "content": LOOK_SYSTEM},
            {"role": "user", "content": [{"type": "text", "text": text},
                                         {"type": "image_url", "image_url": {
                                             "url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}]}]


def dashscope_look(key, base_url=LOOK_BASE_URL, model=LOOK_MODEL, timeout=LOOK_TIMEOUT_S):
    """recognise(jpeg, seen) -> (text or None, usage dict, latency ms, error or None). Blocking (run it on a pool);
    never raises; the key is only in the header (the run log keeps its first 7 characters). Every call is one call.look
    in the run log (trace and ref {path, lid} from the caller's scope; the photo is replaced by its size there)."""
    def recognise(jpeg, seen=None):
        messages = look_messages(jpeg, seen)
        out = _recognise(messages)
        text, usage, ms, err = out
        ear_log.emit("call.look", ok=err is None, err=err, ms=ms, model=model, key=key, usage=usage or None,
                     cost=ear_log.cost(cost_usd(usage) if usage else None, PRICE_BASIS, "USD"),
                     sum=(text or "")[:60] or None,
                     body={"system": LOOK_SYSTEM, "input": messages[1:], "output": text, "params": params})
        return out

    params = {"model": model, "temperature": 0, "max_tokens": MAX_TOKENS, **LOOK_EXTRA}   # sent and logged as one

    def _recognise(messages):
        body = {**params, "messages": messages}
        req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", method="POST",
                                     data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        t0 = time.monotonic()
        ms = lambda: int((time.monotonic() - t0) * 1000)     # noqa: E731
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                return None, {}, ms(), f"HTTP {e.code}"
        except Exception as e:                   # noqa: BLE001 - offline, slow: one failure
            return None, {}, ms(), type(e).__name__
        usage = resp.get("usage") if isinstance(resp, dict) and isinstance(resp.get("usage"), dict) else {}
        try:
            text = resp["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return None, usage, ms(), "no choices"
        text = text.strip() if isinstance(text, str) else ""
        return (text, usage, ms(), None) if len(text) >= 2 else (None, usage, ms(), "empty text")
    return recognise


def _is_id(v):
    return isinstance(v, int) and not isinstance(v, bool) and 0 <= v < UINT32


class Config:
    burst_s = BURST_S
    max_looks = MAX_LOOKS
    max_failures = MAX_FAILURES
    snap_timeout_s = SNAP_TIMEOUT_S
    frame_wait_s = FRAME_WAIT_S
    retry_s = RETRY_S
    look_timeout_s = LOOK_TIMEOUT_S
    poll_s = POLL_S


class VisionBurst:
    """One per ear process, on the ear's event loop. hub is the realtime_link.RealtimeHub (switch, in_crowd(), link(),
    add_look(), seen_for_look()); recognise(jpeg, seen) is dashscope_look(key) or a test fake; db is hello_db.HelloDB
    (vision_look) or None."""

    def __init__(self, hub, recognise, db=None, cfg=None, clock=time.monotonic, wall_ms=None, first_id=1):
        self.hub, self.recognise, self.db = hub, recognise, db
        self.cfg = cfg or Config()
        self.clock = clock
        self.wall_ms = wall_ms or (lambda: int(time.time() * 1000))
        self._ids = itertools.count(first_id)
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="vision-burst")
        self._task = None
        self._wake = asyncio.Event()
        self._pending = None                     # {id, link, result, frame}: the one snap in flight
        self._stop_why = None
        self._burst = None                       # the running burst's record
        self.until = 0.0
        self.stats = collections.Counter()

    # ---- state ----
    def running(self):
        return self._task is not None and not self._task.done()

    def allowed(self):
        """A burst may run: the realtime switch on (paid), hello in crowd, a /mouth link that reported it."""
        hub = self.hub
        return bool(hub is not None and hub.switch.on and hub.in_crowd() and hub.link() is not None)

    def _why_stop(self):
        if self._stop_why:
            return self._stop_why
        hub, b = self.hub, self._burst
        link = hub.link()
        if link is None or link is not b["link"]:
            return "mouth_dropped"
        if not hub.switch.on:
            return "switch_off"
        if not hub.in_crowd():
            return "left_crowd"
        if self.clock() >= self.until:
            return "elapsed"
        if b["looks"] >= self.cfg.max_looks:
            return "cap"
        if b["fails_in_row"] >= self.cfg.max_failures:
            return f"failures:{b['last_fail']}"
        return None

    # ---- the realtime session's heard text ----
    def heard(self, text):
        """One input transcription of the 围观 session (never logged). -> "start" / "extend" / "stop" / None."""
        if not isinstance(text, str) or not text:
            return None
        if any(w in text for w in STOP_WORDS):
            if self.running():
                self.stop("heard_stop", word=next(w for w in STOP_WORDS if w in text))
                return "stop"
            return None
        if not any(w in text for w in START_WORDS):
            return None
        word = next(w for w in START_WORDS if w in text)
        return self.start(word)

    def _emit(self, action, b=None, trace=None, **extra):
        """trigger.burst in the run log (start / extend / refused / stop / end); never the heard text."""
        ear_log.emit("trigger.burst", trace=trace or (b or {}).get("trace") or ear_log.trace(),
                     sum=f"视觉爆发 {action}", ref={"burst_id": b["id"]} if b else None,
                     extra={"action": action, **extra})

    def start(self, trigger="manual"):
        if not self.allowed():
            if self.hub is not None and not self.hub.switch.on:
                log("vision burst: not started (realtime switch off)", flush=True)
            self._emit("refused", word=trigger,
                       why="switch_off" if self.hub is not None and not self.hub.switch.on else "not_allowed")
            return None
        if self.running() and self._stop_why:
            log(f"vision burst {self._burst['id']}: stopping ({self._stop_why}), not extended", flush=True)
            return None
        self.until = self.clock() + self.cfg.burst_s
        if self.running():
            self.stats["extended"] += 1
            self._burst["extended"] += 1
            log(f"vision burst {self._burst['id']}: extended to {self.cfg.burst_s:.0f} s from now ({trigger})", flush=True)
            self._emit("extend", self._burst, ear_log.scoped_trace(), word=trigger)
            return "extend"
        now_ms = self.wall_ms()
        self._stop_why = None
        self._burst = {"id": f"b{now_ms}", "link": self.hub.link(), "started": self.clock(), "at": now_ms,
                       "trigger": trigger, "looks": 0, "snaps": 0, "fails": collections.Counter(), "fails_in_row": 0,
                       "last_fail": None, "cost_usd": 0.0, "latency_ms": [], "cycle_ms": [], "extended": 0,
                       "session": None, "trace": ear_log.trace()}      # the heard sentence's trace when in its scope
        self.stats["bursts"] += 1
        log(f"vision burst {self._burst['id']}: start ({trigger}) for {self.cfg.burst_s:.0f} s", flush=True)
        self._emit("start", self._burst, word=trigger, burst_s=self.cfg.burst_s)
        self._task = asyncio.create_task(self._run(self._burst), context=contextvars.Context())
        self._task.add_done_callback(self._done)
        return "start"

    def ask(self, trigger="ask"):
        """Someone asked hello to look ("这是什么", "你看到了什么"): a burst runs from now (started or extended). -> this
        burst clock's now (a look whose photo was sent for after it answers the question), or None (no burst can run)."""
        return self.clock() if self.start(trigger) is not None else None

    def stop(self, why, word=None):
        if self.running() and not self._stop_why:
            self._stop_why = why
            self._emit("stop", self._burst, ear_log.scoped_trace(), why=why, word=word)
            self._wake.set()

    # ---- the robot's answers ----
    def on_snap_result(self, link, m):
        """MouthLink: {"type":"snap_result",...} from the robot (non-blocking)."""
        p = self._pending
        sid = m.get("id")
        if p is None or not _is_id(sid) or sid != p["id"] or link is not p["link"]:
            self.stats["late_results"] += 1
            return
        if p["result"] is None:
            p["result"] = m
            self._wake.set()

    def on_frame(self, head, jpeg, path, session):
        """VisionLink: one stored body-camera frame (non-blocking). Only a frame tagged with the snap in flight counts;
        a frame for an id we already gave up on is just an ordinary frame."""
        sid = head.get("snap") if isinstance(head, dict) else None
        if not _is_id(sid):
            return
        p = self._pending
        if p is None or sid != p["id"]:
            self.stats["late_frames"] += 1
            return
        if p["frame"] is None:
            p["frame"] = {"seq": head.get("seq"), "jpeg": bytes(jpeg), "path": path, "session": session,
                          "bytes": len(jpeg)}
            self._wake.set()

    # ---- the loop ----
    async def _wait_for(self, ready, timeout):
        """Wait until ready() or timeout; False when a stop condition came first (its reason is then set)."""
        end = self.clock() + timeout
        while not ready():
            why = self._why_stop()
            if why:
                self._stop_why = why
                return False
            left = end - self.clock()
            if left <= 0:
                return True
            self._wake.clear()
            if ready():
                break
            try:
                await asyncio.wait_for(self._wake.wait(), min(self.cfg.poll_s, left))
            except asyncio.TimeoutError:
                pass
        return True

    async def _pause(self, seconds):
        await self._wait_for(lambda: False, seconds)

    def _fail(self, b, why):
        b["fails"][why] += 1
        b["fails_in_row"] += 1
        b["last_fail"] = why
        self.stats["fail:" + why] += 1
        log(f"vision burst {b['id']}: snap failed ({why}), {b['fails_in_row']} in a row", flush=True)

    async def _snap(self, b):
        """One snap -> the frame dict, or None (a failure was counted, or a stop condition came)."""
        link = b["link"]
        sid = next(self._ids) % UINT32
        p = self._pending = {"id": sid, "link": link, "result": None, "frame": None}
        b["snaps"] += 1
        snapped = self.clock()                   # the photo is taken after this (the hub's look answer needs it)
        try:                                     # review 2026-11-15: a stalled send must not hold the loop
            sent = await asyncio.wait_for(link.send_from_realtime(json.dumps({"type": "snap", "id": sid})),
                                          self.cfg.snap_timeout_s)
        except asyncio.TimeoutError:
            sent = False
        if not sent:
            self._stop_why = self._stop_why or "mouth_dropped"
            return None
        if not await self._wait_for(lambda: p["result"] is not None, self.cfg.snap_timeout_s):
            return None
        r = p["result"]
        if r is None:
            self._fail(b, "no_reply")
            return None
        if r.get("ok") is not True:
            why = r.get("why")
            self._fail(b, why if why in ("busy", "camera_off", "no_uplink", "timeout") else "refused")
            return None
        if not await self._wait_for(lambda: p["frame"] is not None, self.cfg.frame_wait_s):
            return None
        f = p["frame"]
        if f is None:
            self._fail(b, "no_frame")
            return None
        if r.get("seq") != f["seq"]:
            self._fail(b, "seq_mismatch")
            return None
        f["snap_id"], f["snap_ms"] = sid, r.get("ms") if isinstance(r.get("ms"), int) else None
        f["snapped"] = snapped
        return f

    async def _look(self, b, f):
        """Recognise one frame on the pool; stores and hands on the look. -> True when it worked."""
        loop = asyncio.get_running_loop()
        seen = None
        try:
            seen = self.hub.seen_for_look() if hasattr(self.hub, "seen_for_look") else None
        except Exception:                        # noqa: BLE001 - the look goes without the M12 line
            seen = None
        b["looks"] += 1
        self.stats["looks"] += 1
        lid = f"{b['id']}-{f['snap_id']}"
        with ear_log.scope("call.look", trace=b.get("trace"), ref={"path": f["path"], "lid": lid, "burst_id": b["id"]}):
            ctx = contextvars.copy_context()             # the pool thread logs call.look with this trace and ref
        try:
            fut = loop.run_in_executor(self._pool, ctx.run, self.recognise, f["jpeg"], seen)
            text, usage, latency_ms, err = await asyncio.wait_for(fut, self.cfg.look_timeout_s + 1)
        except asyncio.TimeoutError:
            text, usage, latency_ms, err = None, {}, int((self.cfg.look_timeout_s + 1) * 1000), "timeout"
        except Exception as e:                   # noqa: BLE001 - one failed look
            text, usage, latency_ms, err = None, {}, None, type(e).__name__
        usage = usage if isinstance(usage, dict) else {}
        cost = cost_usd(usage) if usage else None
        b["cost_usd"] += cost or 0.0
        at = self.wall_ms()
        doc = {"kind": "look", "lid": lid, "burst_id": b["id"], "at": at,
               "session": f["session"], "seq": f["seq"], "snap_id": f["snap_id"], "path": f["path"], "bytes": f["bytes"],
               "text": text, "error": err, "latency_ms": latency_ms, "snap_ms": f.get("snap_ms"),
               "usage": {k: v for k, v in usage.items() if isinstance(v, (int, float)) and not isinstance(v, bool)},
               "cost_usd": cost, "model": LOOK_MODEL, "prompt": LOOK_PROMPT}
        b["session"] = f["session"] or b["session"]
        self._store(doc)
        if err or not text:
            self._fail(b, "look_" + str(err or "empty")[:24].replace(" ", "_"))
            return False
        b["fails_in_row"] = 0
        if latency_ms is not None:
            b["latency_ms"].append(latency_ms)
        if self._stop_why:                       # review: a look that finished after the stop is stored, not shown
            return False
        try:
            self.hub.add_look(text, at, snapped=f.get("snapped"))
        except Exception:                        # noqa: BLE001 - the look is stored anyway
            log(f"vision burst {b['id']}: add_look failed\n{traceback.format_exc().rstrip()}", flush=True)
        log(f"vision burst {b['id']}: look {b['looks']} snap {f['snap_id']} seq {f['seq']} robot {f.get('snap_ms')} ms, "
            f"model {latency_ms} ms", flush=True)
        return True

    def _store(self, doc):
        if self.db is None:
            return
        try:
            self.db.vision_look(doc)
        except Exception:                        # noqa: BLE001 - a record never costs the burst
            pass

    async def _tell(self, b, on):
        """The robot's burst indicator (collector-0.5.00, docs/superpowers/specs/2026-11-15-burst-indicator.md):
        {"type":"burst","on":true,"left_s","looks"} at the start and after every look, {"on":false} at the end.
        Older firmware ignores it. Never costs the burst."""
        link = b.get("link")
        if link is None:
            return
        msg = {"type": "burst", "on": True, "left_s": max(0, int(round(self.until - self.clock()))), "looks": b["looks"]} \
            if on else {"type": "burst", "on": False, "looks": b["looks"]}
        try:
            await asyncio.wait_for(link.send_from_realtime(json.dumps(msg)), 1.0)
        except Exception:                        # noqa: BLE001 - a lost indicator frame times out on the robot
            self.stats["tell_failed"] += 1

    async def _run(self, b):
        try:
            await self._tell(b, True)
            while True:
                why = self._why_stop()
                if why:
                    self._stop_why = why
                    break
                t0 = self.clock()
                f = await self._snap(b)
                self._pending = None
                if f is not None:
                    ok = await self._look(b, f)
                    if ok:
                        b["cycle_ms"].append(int((self.clock() - t0) * 1000))
                        await self._tell(b, True)
                        continue
                if self._why_stop():
                    continue                     # the top of the loop ends it with the reason
                await self._pause(self.cfg.retry_s)
        finally:
            self._pending = None
            self._finish(b, self._stop_why or "cancelled")
            if self._stop_why != "shutdown":
                try:
                    await self._tell(b, False)
                except asyncio.CancelledError:       # cancelled while telling: the robot's own timeout clears it
                    pass

    def _finish(self, b, why):
        dur = self.clock() - b["started"]
        lat, cyc = sorted(b["latency_ms"]), b["cycle_ms"]
        doc = {"kind": "burst", "lid": f"{b['id']}-end", "burst_id": b["id"], "at": b["at"], "end_at": self.wall_ms(),
               "duration_s": round(dur, 1), "reason": why, "trigger": b["trigger"], "extended": b["extended"],
               "snaps": b["snaps"], "looks": b["looks"], "failures": dict(b["fails"]), "session": b["session"],
               "cost_usd": round(b["cost_usd"], 12), "model": LOOK_MODEL, "prompt": LOOK_PROMPT,
               "latency_p50_ms": lat[len(lat) // 2] if lat else None,
               "cycle_mean_ms": int(sum(cyc) / len(cyc)) if cyc else None}
        self._store(doc)
        self._emit("end", b, why=why, looks=b["looks"], snaps=b["snaps"], failures=dict(b["fails"]),
                   duration_s=round(dur, 1), extended=b["extended"])
        self.stats["stop:" + why.split(":")[0]] += 1
        log(f"vision burst {b['id']}: stop ({why}) after {dur:.0f} s, {b['looks']} looks / {b['snaps']} snaps, "
            f"{sum(b['fails'].values())} failures, {b['cost_usd']:.5f} USD", flush=True)

    def _done(self, task):
        if not task.cancelled() and task.exception() is not None:
            e = task.exception()
            log("vision burst: task failed\n" + "".join(traceback.format_exception(type(e), e, e.__traceback__)).rstrip(),
                flush=True)
            ear_log.error(e, "burst.task")

    async def shutdown(self):
        task = self._task
        if task is not None and not task.done():
            self._stop_why = self._stop_why or "shutdown"
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._pool.shutdown(wait=False, cancel_futures=True)
