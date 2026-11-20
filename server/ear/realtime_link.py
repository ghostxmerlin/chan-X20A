"""围观模式 realtime voice session (spike, 2026-11-14): hello talks with the people around her through DashScope's
end-to-end voice model qwen-audio-3.1-realtime-flash, in a cloned voice, with mind's scene / notes / people as context.

Owner (verbatim): "我们在围观模式，试试 qwen-audio-3.1-realtime-flash 的连续对话模式…先看一下，它是否可以在围观模式下，使用我们
的视觉，和情景 纪要 与我们聊天。" and "围观模式中，如果m12视觉，认出了主人，要喊主人并积极回应".

One RealtimeHub per ear process (like mouth_link.ModeRelay), on the ear's event loop, with its own tasks and bounded
queues; nothing it does is awaited by the audio path (feed() and on_vision() are plain non-blocking calls).

When a session runs (want()): the switch hello.switches {_id: "realtime"} is on (default off; billing 9 / 0), the key
and the voice are there, hello's relayed mode is `crowd`, and the /mouth link that reported it is up. A session closes
at once when any of these goes away (the supervisor checks every STEP_S; a closed robot link closes it immediately).

Input (feed, from Ear.on_audio): the ear's 16 kHz mic audio, denoised when the robot source has GTCRN. Nothing is sent
while hello is sounding (the robot's self flag, our own stream until the robot reports it done, a soul-tts stream, a
response being generated): no echo, and no barge-in in this spike. Cost gate (gate="vad", default): audio goes up only
from PREROLL_S before the ear's own VAD onset until TAIL_S after it ends, so silence is never billed; the server VAD
(server_vad, silence SILENCE_MS) still decides the turn. gate="all" streams everything.

Output: the session's response.audio.delta (24 kHz PCM16) -> Opus 24 kHz mono 60 ms frames (32 kbps, as soul-tts) ->
the robot over the same /mouth link with soul-tts's downlink protocol: {"type":"tts.start","stream":N,"codec":"opus",
"sample_rate":24000,"frame_ms":60} / binary "SPK1"|u32 stream|u32 seq|u16 60|u16 0 + one Opus packet / {"type":
"tts.end","stream":N,"frames":..,"duration_ms":..}; {"type":"tts.stop"} when it must stop. Stream ids start at
mouth_link.RT_STREAM_BASE so they never collide with soul-tts's (which count from 1); the robot's reports for them come
back here, not to soul-tts. Flow control as soul-tts: sent - played <= MAX_LEAD_MS (robot ring is 10 s), a stream with
no progress for STALL_S is stopped; the next stream starts only after the robot reported the previous one done.

soul-tts and realtime never interleave: REALTIME YIELDS. soul-tts in crowd should be idle, so a soul-tts tts.start there
is a deliberate action (the page's say / stop); MouthLink calls yield_to_tts() before it forwards that tts.start: our
stream is dropped at once (the robot's tts.start replaces it), the response is cancelled upstream, and no new realtime
stream or greeting starts while soul-tts has a stream the robot has not reported finished.

Context (persona + mind's GET /realtime/context block) goes in with session.update instructions at open and every
CONTEXT_S. Place (2026-11-15): the block says where hello is now (or unknown) and, at most once per 10 min, lets hello ask;
a heard sentence with 在 / 到 / 这里 goes to mind POST /place/told (mind's local rule decides; no model), and a changed
place re-sends the instructions at once. Owner greeting: VisionLink hands every M12 event to on_vision(); FaceView tracks which faces are in view.
When the owner (mind's owner.face_names) appears after AWAY_S unseen while in crowd, or is in view when a session opens,
a "[事件] 主人（<call>）出现在你面前了" user text item + response.create goes in; queued while hello sounds, dropped after
GREET_MAX_AGE_S, never twice within AWAY_S. The call name comes from mind at run time and is never logged.

Crowd touch since 2026-11-19 (collector-0.5.15): see TOUCH_S, touched() and touch_quiet(); MouthLink no longer calls tap().
The old crowd tap (owner 2026-11-14 "围观模式，单击，不要停了，改成检查m12视觉，看有没有认识的人在…"): MouthLink handed a
mode_report {mode: crowd, input: tap} to tap(); mind does not run that report's stop_speech (runner.on_change). tap()
looks at the M12 faces of the last face_recent_s (FaceView, from the robot's own face_found / face_named / face_attrs
events: box, name, gender, age_group): a known face (largest first) is greeted by name (the owner as 主人（call）,
others by mind's names map), else the largest face by its apparent gender + age group (美女 / 帅哥 / 小朋友 / 爷爷 /
奶奶, else a neutral hello), and with no face hello says she does not see anyone. No photo, no model call; attributes
stay in memory, the log says only known / unknown / none. TAP_COOLDOWN_S between taps. Firmware note: hello sends
a crowd tap only while she is speaking (her rules turn it into stop_speech); a tap while she is quiet is answered on
the robot (not_implemented) and never reaches the server until the rules are aligned.
Turn-taking v0 (owner: "可能很多人说话，有很多打断。所以我们不能被轻易打断"): DashScope documents no interrupt option for
this model (interrupt_response / create_response are dropped from the echoed session, measured 2026-11-14), so the hub
never lets speech cut a reply: no audio goes up from response.created until the robot reported the reply done plus
GUARD_S, it never sends response.cancel for speech, greetings wait for quiet; server VAD threshold 0.6 and silence
1000 ms (defaults 0.5 / 800).
Addressing v0 (2026-11-15, a TV drama answered ~100 times): the persona says to answer only 「嗯」 when nobody is talking
to hello (TV, others chatting, fragments); the reply gate (reply_verdict) holds a speech reply's audio until its
transcript shows it is not 「嗯」, not a didn't-hear question and not a word-for-word repeat, else the reply never plays
(realtime_turns state muted_quiet / muted_unclear / muted_repeat; it is still billed, ~0.004 CNY). A spoken 闭嘴 (is_hush)
turns the 围观 switch off through mind's POST /switches; hello's "一" turns it on again.
视觉爆发 (2026-11-15, vision_burst.py): every input transcription also goes to burst.heard() ("注意看" starts a 20 s
photo-by-photo look, "别看了" stops it). Each look comes back through add_look(); the last LOOKS_KEEP within LOOKS_S are
in 【你的眼睛】 as 注视（HH:MM:SS，N 秒前）：… (newest first) and the instructions are re-sent on a new look, at most once
per LOOKS_PUSH_S. Without looks of its own (an ear restart), mind's 注视： line from the context block is used.
Caps: one session at most MAX_SESSION_S, closed after IDLE_S without speech, and at most HOUR_CAP_S of open sessions in
any hour; after a cap / idle close the next session opens only on the next speech onset (or an owner greeting).
Records: one realtime_turns document per response (timings, transcript lengths only, token usage, cost estimate); the
console log has timings and ids only, never text, names, the key or the voice id. The run log (ear_log / hello_log,
2026-11-16) has the full instructions (call.realtime.session / .context), every gate verdict (gate.realtime), rescue
(gate.rescue), hush (trigger.hush), greeting (trigger.greet), filler (filler.play), web search (call.realtime.search)
and turn (call.realtime.turn: usage, cost, heard / ear's own words / reply). Each turn has its own trace, made when its
segment starts (R17); the ear's own sentences around it are listed in extra.heard as candidates by time, not as its
owner. Never the key (only its first 7 characters) or the voice id.
"""

log = print                                         # tests silence it
_LOG_FAILED = set()                                 # run-log helpers that have failed once (said once on the console)

import rt_base as _rt_base  # noqa: E402
# every name of rt_base (also the underscore ones), so the moved code resolves exactly as before; never log /
# _LOG_FAILED: rt_base's are shims that look up ours, so copying them made log call itself (2026-11-20: every Docking
# task died in a RecursionError on the Mac stack; the tests patch log, so they never saw it)
globals().update({_k: _v for _k, _v in vars(_rt_base).items() if not _k.startswith("__") and _k not in ("log", "_LOG_FAILED")})
from rt_gate import GateMixin  # noqa: E402
from rt_cards import CardsMixin  # noqa: E402
from rt_looks import LooksMixin  # noqa: E402
from rt_tools import ToolsMixin  # noqa: E402
from rt_skills import SkillsMixin  # noqa: E402
from rt_interrupt import InterruptMixin  # noqa: E402
from rt_events import EventsMixin  # noqa: E402
from rt_manner import MannerMixin  # noqa: E402


class RealtimeHub(MannerMixin, EventsMixin, InterruptMixin, SkillsMixin, ToolsMixin, LooksMixin, GateMixin, CardsMixin):
    def __init__(self, *, key, voice, modes, call, switch, db=None, connect=None, encoder_factory=None,
                 cfg=None, clock=time.monotonic, wall_ms=None, rng=None, fillers=None):
        if connect is None:
            connect = _direct_connect
        self.key, self.voice, self.modes, self.call, self.switch, self.db = key, voice, modes, call, switch, db
        self.connect, self.encoder_factory = connect, encoder_factory or OpusFrames
        self.rng = rng or random.Random()
        self._beeps = itertools.count(1)
        self.cfg, self.clock = cfg or Config(), clock
        self.wall_ms = wall_ms or (lambda: int(time.time() * 1000))
        self.url = URL.format(model=self.cfg.model)
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="rt-mind")
        self.state = "closed"                          # closed / connecting / open
        self._task = None
        self._ws = None
        self._ready = asyncio.Event()
        self._sid = itertools.count(RT_STREAM_BASE)
        self._sessions = itertools.count(1)
        self.session_no = 0
        self.opened_at = None
        self._spans = collections.deque()              # (start, end) of closed sessions, for the hour cap
        self._retry_at = 0.0
        self._need_speech = False
        self._last_speech = None
        self._was_speaking = False
        self._sounding = False
        self._send_until = 0.0
        self._preroll = collections.deque()
        self._preroll_bytes = 0
        self._in = asyncio.Queue(maxsize=self.cfg.in_queue)
        self._out = asyncio.Queue(maxsize=4000)
        self._progress = asyncio.Event()
        self._stream = None                            # our robot stream: dict, see _start_stream
        self._dropped = set()                          # response ids whose audio must not play (yielded / failed)
        self._resp = None                              # the response being generated (id)
        self._live_instr = None                        # the instructions last sent (run log: each turn's system input)
        self._turn = None                              # the turn the next response.created belongs to
        self._turns = {}                               # response id -> turn
        self._next_heard = None                        # the sentence a tool chain answers (see _run_tool)
        self._chain_calls = collections.OrderedDict()  # trace -> real tool calls (not open_skill) for that sentence
        self._preload_trace = {}                       # skill id -> trace of the sentence that preloaded it
        self._speech_end = None
        self._next_kind = "speech"
        self._inject_id = None                         # the server id of the item that goes with _next_kind
        self._pending_vision = None                    # a burst / look the ear's ASR heard, waiting for the model's verdict
        self._scene = None                             # (at_ms, text): the newest instantly recognised camera photo
        self._pending_search = None                    # a search the ear's ASR heard, waiting for the model's verdict
        self._length = None                            # (mode, at): the owner's 详细点 / 简单点 (see LENGTH_S)
        self._quiet_mode = None                        # until (clock): answer nothing unless called (set_quiet)
        self._low = None                               # until (clock): 少插话 (see LOW_S)
        self._role = None                              # (text, until): a temporary role on the persona (see ROLE_S)
        self.look_waiters = []                         # [(mark, future)]: look_now tool waits for a fresh look
        self._called_until = 0.0                       # inside a call window (hello / Dynames): see CALLED_S
        self._called_at = None                         # the last call (merges sources that heard the same call)
        self._called_text = None                       # {text, at, trace}: a called sentence still waiting for an answer
        self._body_at = None                           # when a shaken / patted line was sent (want(): said to the end)
        self._turn_changed = False                     # 智能断句 / 全答 switched while a session was open: close it when idle
        self._smart_wanted = None                      # the 智能断句 switch's last value (None: not read yet)
        self._chat_at = None                           # 点屏幕聊天: the tap that opened the chat (None: no chat)
        self._chat_word_at = None                      # when 在听 was last put under the eyes for the chat
        self._called_trace = None                      # the call's run-log trace: the next sentence in the window joins it
        self._order_at = None                          # the last direct order (Unit ASR command word): see ORDER_S
        self._qtask = None                             # the open search task {topic, at, tries, results, answered, ...}
        self._redo = None                              # a refused answer to send again {kind, prompt, question, at, tries}
        self.ctx = {}                                  # last /realtime/context answer
        self._ctx_at = None
        self._seen_sent, self._seen_at = None, None    # last 【此刻看到】 line sent, and when
        self.watch = FaceView(self.cfg.away_s, recent_s=self.cfg.face_recent_s)
        self._greet = None                             # pending greeting: {at, kind: owner / tap, pick}
        self._last_tap = None
        self._last_greet = None                        # any owner greeting (long, short, tap on the owner)
        self._last_long = None                         # the last long named owner greeting
        self._said = collections.deque(maxlen=self.cfg.greet_said_keep)   # its texts: memory only, never logged
        self._recent = collections.deque(maxlen=self.cfg.recent_keep)      # (at, heard, reply): memory only
        self._quiet_until = 0.0                        # no input / greeting right after hello finished speaking
        self._done_at = None                           # when the robot last reported one of our streams finished
        self._appear = None                            # (name, at): appeared before mind's owner names were known
        self._bg = set()
        self._status_at = -1e9
        self._cap_logged = False
        self.stats = collections.Counter()
        self.on_transcript = None                      # offline tests only: (who, text without emoji); never logged
        self._local_end = None                         # when the ear's own VAD last said speech ended
        self._heard_turn = None                        # the last speech turn (its transcript may come late)
        self._told_busy = False                        # a /place/told post in flight
        self._hush_busy = False                        # a 闭嘴 switch-off post in flight
        self._local_texts = collections.deque(maxlen=6)    # (clock, text) of the ear's own ASR: memory only
        self._heard_log = collections.deque(maxlen=HEARD_KEEP)   # (clock, {trace, text, at wall ms}): run log candidates
        self._rescue = None                            # a muted 「嗯」 that may still be asked again
        self._rescue_heard = None
        self._cmd_last = {}                            # burst word -> when it was last handled
        self._look_wait = None                         # a look question waiting for a fresh look
        self._search_wait = None                       # a search question waiting for the ear's web search
        self._search_question = None
        self.searcher = None                           # web_search.dashscope_search(key) (ear_server wires it)
        self.search_fillers = []                       # [(text, pcm)]: "我查查哈" (load_fillers, <voice>-search)
        self.tool_fillers = {}                         # {group: [(text, pcm)]} (load_tool_fillers), by FILLER_GROUP
        self.filler_base = None                        # ~/soul-spike/fillers: set_voice reloads tool_fillers from it
        self._filler_last_by = {}                      # group -> the clip played last (never twice in a row)
        self.tools_switch = None                       # 千问用工具 (realtime_tools): .on; None = off
        self.persona = None                            # set_voice: the persona that goes with the voice (None = PERSONA)
        self.screen_word = None                        # fn(text, hold_s): a word under the eyes (robot command line)
        self._touch_word = False                       # 在听 is under the eyes (touched)
        self._tool_wait = None                         # {trace, name, since}: a tool is running (see TOOL_WORD)
        self._last_shaken_say = None                   # SHAKEN_SAY_S
        self._quiet_sticky = False                     # 安静 with a said length: a call does not end it
        self._debug = None                             # 调试模式 until (clock, mode_span.OPEN) or None
        self._debug_since_ms = None                    # wall ms it started (the debug log's start)
        self._last_debug_alert = None
        self._mode_word_at = None                      # when the mode word went under the eyes (refreshed)
        self._tool_cancelled = set()                   # traces whose running tool was interrupted
        self.voice_key = None
        self._tool_specs = []                          # this session's tools (realtime format)
        self.tools_mode_fn = lambda: "all"             # 千问用工具: "all" (every live tool) / "skills" (open_skill first)
        self._tools_mode = "all"
        self._all_specs = {}                           # skills mode: name -> spec of every live tool
        self._skills = {}                              # skills mode: id -> skill (skills.json)
        self._skill_cfg = {}
        self._open_skills = {}                         # skills mode: id -> opened at (clock)
        self._filler_traces = collections.deque(maxlen=16)
        self._tool_ok_traces = collections.deque(maxlen=16)   # sentences whose tool calls passed the tool gate
        self._speakers = collections.deque(maxlen=16)  # (at, band, voice name) of the ear's recent sentences
        self._cards = collections.deque(maxlen=64)     # sentence cards waiting for their outcome (rt_cards, 2026-11-19)
        self._card_events = collections.deque(maxlen=256)   # (at, turn / ambient, state) for the cards
        self._called_source = None                     # the last call's source (asr / name / text / touch)
        self._speaker_sent = None
        self.intent = None                             # intent.Auto (自动档): decides before the model answers
        self.intent_on = False                         # 工具分类器 switch (realtime_intent): off = it judges nothing; off by default (owner 2026-11-20)
        self._preloaded = {}                           # auto: skill id -> preloaded at (clock), not used yet
        self._next_depth = 0
        self._tool_pool = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="rt-tool")
        self.fillers = list(fillers or [])             # [(text, pcm 24 kHz)]: "我看看哈" while waiting (load_fillers)
        self._filler_last = None
        self._search_filler_last = None
        self._look_question = None
        self.burst = None                              # vision_burst.VisionBurst (ear_server wires it)
        self.body = None                               # body_sense.BodySense (ear_server wires it; collector-0.5.12)
        self._looks = collections.deque(maxlen=LOOKS_KEEP)    # (at wall ms, text): memory only, never logged
        self._looks_dirty, self._looks_sent_at = False, None
        self._seg = None                                 # the segment in progress {start, trace} (speech_started .. stopped)
        self._cold = None                                # {start, trace}: the onset that opened the session (one segment)
        self._next_trace = self._next_prompt = None      # the trace / text item of the next non-speech response

    # ---- conditions ----
    def link(self):
        return self.modes._links.get(self.modes._mode_conn) if self.modes is not None else None

    def in_crowd(self):
        return self.modes is not None and self.modes.robot_mode == "crowd"

    def armed(self):
        """Everything but the robot link: the switch, the key, the voice, crowd."""
        return bool(self.key and self.voice) and self.switch.on and self.in_crowd()

    def want(self):
        # 点屏幕聊天: no session outside a chat a tap opened (collect only)
        # shaken / patted: said anyway, and the session stays until the line is said (review 2026-11-20: it closed 0.2 s
        # after the line went out): 3 s for the reply to start, then while it plays, 30 s at most
        b, now = self._body_at, self.clock()
        body_line = (self._greet is not None and self._greet.get("kind") == "body") or \
            (b is not None and now - b < 30 and (now - b < 3 or self.output_busy()))
        return self.armed() and self.link() is not None and not (self.cfg.answer_all and not self.chat_on() and not body_line)

    def chat_on(self, now=None):
        """点屏幕聊天: a tap opened a chat; it lasts while someone talks or hello speaks / runs a tool, CHAT_IDLE_S after
        the last of these, CHAT_MAX_S after the tap at most."""
        if self._chat_at is None:
            return False
        now = self.clock() if now is None else now
        if now - self._chat_at >= CHAT_MAX_S:
            return False
        if self._was_speaking or self.output_busy() or self._tool_wait is not None:
            return True
        last = max(self._chat_at, self._last_speech if self._last_speech is not None else -1e9,
                   self._done_at if self._done_at is not None else -1e9)
        return now - last < CHAT_IDLE_S

    def start_chat(self, trace=None, why="tap"):
        now = self.clock()
        fresh = not self.chat_on(now)
        self._chat_at = now
        if fresh:
            ear_log.emit("trigger.touch", trace=trace or ear_log.new_trace(), by="touch", sum="点屏幕：开始聊天",
                         ref={"session": self.session_no}, extra={"action": "chat_start", "why": why})
        self._greet = None                             # nothing queued from before the tap is said now
        self._chat_word_at = None

    def end_chat(self, why, trace=None):
        if self._chat_at is None:
            return
        self._chat_at = None
        ear_log.emit("trigger.touch", trace=trace or ear_log.new_trace(), by="touch" if why == "double_tap" else "auto",
                     sum={"idle": "聊天结束：30 秒没人说话", "max": "聊天结束：十分钟到了，再点一下接着聊",
                          "double_tap": "聊天结束：双击", "quiet": "聊天结束：说了安静"}.get(why, f"聊天结束：{why}"),
                     ref={"session": self.session_no}, extra={"action": "chat_end", "why": why})
        self._word("", 0)

    def active(self):
        return self.state != "closed"

    def output_busy(self):
        link = self.link()
        st = self._stream
        return bool((st and not st["reported"]) or self._resp is not None or (link is not None and link.tts_active))

    def busy(self):
        return self._sounding or self._was_speaking or self.output_busy() or self._tool_wait is not None or \
            self.clock() < self._quiet_until

    def hour_used(self, now):
        while self._spans and now - self._spans[0][1] > 3600:
            self._spans.popleft()
        used = sum(e - max(s, now - 3600) for s, e in self._spans)
        if self.opened_at is not None:
            used += now - max(self.opened_at, now - 3600)
        return used

    # ---- the ear's hooks (non-blocking) ----
    def feed(self, pcm, speaking, sounding):
        """16 kHz PCM16 from Ear.on_audio. Never blocks: a full queue drops its oldest packet."""
        self._sounding = sounding
        if not self.armed():
            self._preroll.clear()
            self._preroll_bytes, self._was_speaking = 0, False
            return
        now = self.clock()
        if speaking and not self._was_speaking and not sounding:
            self._onset(now)
        if self._was_speaking and not speaking:
            self._local_end = now
            h = self._heard_turn                       # the server VAD often ends the turn first (measured)
            if h is not None and h.get("local_end") is None and now - h["speech_end"] < 3:
                h["local_end"] = now
        self._was_speaking = speaking and not sounding
        called = now < self._called_until
        tail = sounding and self._robot_tail(now)      # only the robot's silent hold + tail after our reply
        if (sounding and not tail) or ((self.output_busy() or self._tool_wait is not None) and not called) or \
                now < self._quiet_until:
            self._preroll.clear()
            self._preroll_bytes, self._send_until = 0, 0.0
            return
        if tail:
            self.stats["robot_tail_in"] += 1
        if speaking or tail:                           # the tail must outlast the server VAD's silence
            self._send_until = now + max(self.cfg.tail_s, self.cfg.silence_ms / 1000 + 0.6)
        if self.cfg.gate == "all" or now < self._send_until or called:
            if self.state == "closed":
                return
            while self._preroll:
                self._put(self._preroll.popleft())
            self._preroll_bytes = 0
            self._put(pcm)
            return
        self._preroll.append(pcm)
        self._preroll_bytes += len(pcm)
        while self._preroll_bytes > self.cfg.preroll_s * SR_IN * 2 and self._preroll:
            self._preroll_bytes -= len(self._preroll.popleft())

    def _robot_tail(self, now):
        """True within robot_tail_s after the robot reported our stream finished, while nothing new is playing: its
        `sounding` flag is then only the speaker's silent hold + self tail, not hello's voice (guard_s still applies)."""
        return self._done_at is not None and now - self._done_at < self.cfg.robot_tail_s and not self.output_busy()

    def _put(self, pcm):
        if self._in.full():
            self._in.get_nowait()
            self.stats["in_dropped"] += 1
        self._in.put_nowait(pcm)

    def _onset(self, now):
        self._last_speech = now
        self._need_speech = False
        if self.state == "closed" and self.want():
            self._open(now, "speech")
            if self.state == "connecting":             # run log: the first segment starts at this onset (R17)
                self._cold = {"start": now, "trace": ear_log.new_trace()}


    def yield_to_tts(self, link):
        """MouthLink: soul-tts is about to start a stream on `link`. Realtime yields: our stream is dropped (the robot's
        tts.start replaces it) and the response is cancelled. Synchronous, before the tts.start is forwarded."""
        st = self._stream
        if st is not None and not st["reported"] and st["link"] is link:
            st["closed"] = st["reported"] = True
            self._dropped.add(st["rid"])
            self._finish_turn(st["rid"], "yielded")
            self.stats["yielded"] += 1
            log(f"realtime: stream {st['sid']} yielded to soul-tts", flush=True)
            self._progress.set()
        if self._resp is not None:
            self._dropped.add(self._resp)
            self._send_soon({"type": "response.cancel"})

    def on_report(self, link, m):
        """The robot's tts.playing / progress / done / stopped / error for one of our streams."""
        st = self._stream
        if st is None or m.get("stream") != st["sid"]:
            return
        v = m.get("played_ms")
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
            st["played_ms"], st["progress_at"] = v, self.clock()
        kind = m.get("type")
        turn = self._turns.get(st["rid"])
        if kind == "tts.playing" and turn is not None and turn.get("sound_at") is None:
            turn["sound_at"] = self.clock()
        elif kind in ("tts.done", "tts.stopped", "tts.error"):
            st["reported"] = True
            self._quiet_until = self.clock() + self.cfg.guard_s
            self._done_at = self.clock()
            self._finish_turn(st["rid"], kind.split(".", 1)[1])
        self._progress.set()

    def link_closed(self, link):
        """MouthLink.run() ended: the robot left. The session closes now."""
        st = self._stream
        if st is not None and st["link"] is link:
            st["closed"] = st["reported"] = True
            self._progress.set()
        if self.state != "closed" and self.link() in (None, link):
            self.close("robot_gone")

    # ---- supervisor ----
    def start(self):
        return self.spawn(self._supervise())

    async def _supervise(self):
        while True:
            try:
                self.step()
            except Exception:                          # noqa: BLE001 - logged, the next step tries again
                log(f"realtime: step failed\n{traceback.format_exc().rstrip()}", flush=True)
            await asyncio.sleep(STEP_S)

    def step(self):
        now = self.clock()
        if self._open_skills and self._ws is not None and self._ready.is_set() and self._prune_skills(now):
            self._push_skill_tools()                   # a preloaded skill nobody used goes again
        r = self._redo
        if r is not None:
            if now - r["at"] > REDO_S or r["tries"] > 4:
                self._redo = None
                self.stats["redo_dropped"] += 1
            elif self._ws is not None and self._ready.is_set() and self._resp is None and not self.output_busy() \
                    and self._seg is None and self._next_kind == "speech":
                self._redo = None
                self.stats["redo_sent"] += 1
                if r["kind"] == "search_answer":
                    self._search_question = r["question"]
                elif r["kind"] == "look_answer":
                    self._look_question = r["question"]
                else:
                    self._rescue_heard = r["question"]
                self._next_kind, self._next_trace, self._next_prompt = r["kind"], r["trace"], r["prompt"]
                self._send_soon({"type": "conversation.item.create", "item": {"type": "message", "role": "user",
                                                                              "content": [{"type": "input_text",
                                                                                           "text": r["prompt"]}]}})
                self._send_soon({"type": "response.create"})
        c = self._called_text
        if c is not None and now - c["at"] > CALLED_ANSWER_WAIT_S and self._resp is None and not self.output_busy():
            self._answer_called(c["text"], now, c["trace"], "千问没有回答")
        p = self._pending_vision or self._pending_search
        if p is not None and now - p["at"] > VISION_GATE_S:
            self._release_vision(now, None)            # no verdict came (no session, model silent): no burst
        if self._chat_at is not None and not self.chat_on(now):
            self.end_chat("max" if now - self._chat_at >= CHAT_MAX_S else "idle")
        elif self._chat_at is not None and self.screen_word is not None and \
                (self._chat_word_at is None or now - self._chat_word_at >= 10):
            self._chat_word_at = now
            self._word("在听", int(CHAT_IDLE_S))       # the robot shows the chat while it lasts (owner: 屏幕给出指示)
        if self._turn_changed and self.state == "open" and self._resp is None and not self.output_busy() \
                and self._tool_wait is None and not self._sounding:
            self._turn_changed = False
            self.close("turn_changed")                 # reopens at once below (in crowd) with the new turn detection
        elif self._turn_changed and self.state == "closed":
            self._turn_changed = False
        if not self.in_crowd():
            self._need_speech, self._greet, self._appear = False, None, None
        if not self.want():
            if self.state != "closed":
                # paused: mind/pause.py (nobody there / today's cap), the switch itself is still on (presence.Gated)
                self.close("paused" if getattr(self.switch, "paused", False) else "switch_off" if not self.switch.on
                           else "chat_over" if self.cfg.answer_all and self.in_crowd() and self.link() is not None
                           else "left_crowd" if not self.in_crowd() else "robot_gone")
        elif self.state == "closed":
            if now >= self._retry_at and not self._need_speech:
                self._open(now, "crowd")
        elif self.state == "open":
            if self.hour_used(now) >= self.cfg.hour_cap_s and not self.output_busy():
                self.close("hour_cap", need_speech=True)
            elif now - self.opened_at >= self.cfg.max_session_s and not self.output_busy():
                self.close("session_cap", need_speech=True)
            elif now - max(self._last_speech or 0, self.opened_at) >= self.cfg.idle_s and not self.busy() \
                    and self._greet is None:
                self.close("idle", need_speech=True)
            else:
                if self._ready.is_set() and self._ctx_at is not None and now - self._ctx_at >= self.cfg.context_s:
                    self._ctx_at = now
                    self.spawn(self._refresh_context())
                self._push_seen(now)
                self._push_looks(now)
                self._try_rescue(now)
                self._try_look_answer(now)
                self._try_search_answer(now)
                self._deliver_greet(now)
        if self._debug is not None and now >= self._debug:
            self.set_debug(False, by="timeout")       # the said length ran out
        elif self.debug_on(now) and "debug" in self._skills and self._ws is not None and self._ready.is_set():
            if "debug" in self._open_skills:
                self._open_skills["debug"] = now       # kept open for the whole debug session
            else:
                self._open_skill("debug", now, via="auto_open")
        try:
            self._cards_settle(now)                    # 先看见: cards whose outcome is in (gate.addressed)
        except Exception as e:                         # noqa: BLE001
            _log_failed("cards", e)
        if self._mode_word_at is not None and now - self._mode_word_at > 1800:
            self._show_mode_word()                     # a long / open-ended mode: the robot's word holds an hour at most
        if self._greet is not None and now - self._greet["at"] > self.cfg.greet_max_age_s:
            log("realtime: owner greeting dropped (too old)", flush=True)
            self._greet = None
            self.stats["greet_dropped"] += 1
        if now - self._status_at >= self.cfg.status_s:
            self._write_status(now)

    def _open(self, now, why):
        if now < self._retry_at:                       # a failed / refused session: not before retry_s
            return
        if self.hour_used(now) >= self.cfg.hour_cap_s:
            if not self._cap_logged:
                log("realtime: hourly cap reached, no new session", flush=True)
                self._cap_logged = True
            return
        self._cap_logged = False
        self.state, self.opened_at = "connecting", now
        self.session_no = next(self._sessions)
        self._ready.clear()
        self._task = self.spawn(self._session(self.session_no, why), clean=True)   # no caller's trace in it
        self._write_status(now)

    def close(self, why, need_speech=False):
        if self.state == "closed":
            return
        now = self.clock()
        log(f"realtime: session {self.session_no} closing ({why}) after {now - self.opened_at:.0f}s", flush=True)
        ear_log.emit("call.realtime.session", ok=why not in ("error", "refused", "connect_failed"), model=self.cfg.model,
                     sum=f"Docking 会话关闭 {why}", ms=int((now - self.opened_at) * 1000), ref={"session": self.session_no},
                     extra={"state": "close", "why": why})
        self._spans.append((self.opened_at, now))
        self.state, self.opened_at = "closed", None
        self._need_speech = need_speech
        self._seg = self._cold = None
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
        self._stop_stream()
        self._ready.clear()
        self._resp, self._turn = None, None
        for rid in list(self._turns):
            self._finish_turn(rid, "closed")
        for q in (self._in, self._out):
            while not q.empty():
                q.get_nowait()
        self._write_status(now)

    def _stop_stream(self):
        st = self._stream
        if st is not None and not st["reported"]:
            st["closed"] = st["reported"] = True
            self.spawn(st["link"].send_from_realtime(json.dumps({"type": "tts.stop", "stream": st["sid"]})))
            self._progress.set()

    # ---- the session ----
    async def _session(self, n, why):
        loop = asyncio.get_running_loop()
        ctx_fut = loop.run_in_executor(self._pool, self.call, "GET", "/realtime/context", None, self.cfg.context_timeout_s)
        ws = None
        try:
            try:
                ws = await asyncio.wait_for(self.connect(self.url, additional_headers={"Authorization": f"Bearer {self.key}"},
                                                         max_size=None, open_timeout=self.cfg.open_timeout_s),
                                            self.cfg.open_timeout_s + 1)
            except Exception as e:                     # noqa: BLE001 - offline, refused: try again later
                log(f"realtime: session {n} could not connect ({type(e).__name__}: {str(e)[:80]})", flush=True)
                ear_log.emit("call.realtime.session", ok=False, err=f"{type(e).__name__}: {str(e)[:120]}",
                             model=self.cfg.model, key=self.key, ref={"session": n},
                             extra={"state": "connect_failed", "why": why})
                self._retry_at = self.clock() + self.cfg.retry_s
                self.close("connect_failed")
                return
            self._ws = ws
            await self._take_context(ctx_fut)
            instr = self._instructions()
            self._reset_skills()                       # nothing carries over from the last session (security review)
            self._tool_specs = await self._load_tools() if self.tools_on() else []
            await ws.send(json.dumps({"type": "session.update", "session": session_config(
                self.voice, instr, self.cfg.silence_ms, self.cfg.vad_threshold, self.cfg.search, self.cfg.turn,
                self._tool_specs)}))
            self._live_instr = instr
            ear_log.emit("call.realtime.session", ok=True, model=self.cfg.model, key=self.key, sum=f"Docking 会话打开 {why}",
                         body={"system": instr}, ref={"session": n},
                         extra={"state": "open", "why": why, "search": bool(self.cfg.search),
                                "silence_ms": self.cfg.silence_ms, "vad_threshold": self.cfg.vad_threshold,
                                "turn": self.cfg.turn})
            self._ctx_at = self.clock()
            log(f"realtime: session {n} open ({why}), context {len(self.ctx.get('text') or '')} chars", flush=True)
            self.stats["sessions"] += 1
            tasks = [asyncio.create_task(self._reader(ws, n)), asyncio.create_task(self._sender(ws)),
                     asyncio.create_task(self._output())]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            if self._task is asyncio.current_task():   # the upstream ended it, not close()
                self._retry_at = self.clock() + self.cfg.retry_s
                self.close("upstream_closed")
        except asyncio.CancelledError:
            raise
        except Exception:                              # noqa: BLE001
            log(f"realtime: session {n} failed\n{traceback.format_exc().rstrip()}", flush=True)
            if self._task is asyncio.current_task():
                self._retry_at = self.clock() + self.cfg.retry_s
                self.close("error")
        finally:
            if self._ws is ws:
                self._ws = None
            if ws is not None:
                try:
                    await ws.close()
                except Exception:                      # noqa: BLE001
                    pass

    async def _take_context(self, fut):
        try:
            status, reply = await asyncio.wait_for(asyncio.wrap_future(fut) if isinstance(fut, concurrent.futures.Future)
                                                   else fut, self.cfg.context_timeout_s + 0.5)
        except Exception:                              # noqa: BLE001 - mind slow / down: persona only
            status, reply = None, {}
        if status == 200 and isinstance(reply, dict) and isinstance(reply.get("text"), str):
            self.ctx = reply
            if self._appear is not None:
                name, at = self._appear
                self._appear = None
                if name in ((reply.get("owner") or {}).get("face_names") or []):
                    self._queue_greet(at)
        return status

    async def _refresh_context(self):
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(self._pool, self.call, "GET", "/realtime/context", None, self.cfg.context_timeout_s)
        if await self._take_context(fut) == 200:
            self._send_instructions("context")

    def _told(self, text):
        """A heard sentence that may say where we are ("我们现在在饭店") goes to mind /place/told (a local rule there, no
        model); when the place changed, the context is fetched again at once so the next reply knows it. At most one
        post at a time; the text is never logged."""
        if not text or self._told_busy or not TOLD_HINT.search(text):
            return
        self._told_busy = True
        self.spawn(self._post_told(text[:300]))

    async def _post_told(self, text):
        loop = asyncio.get_running_loop()
        try:
            fut = loop.run_in_executor(self._pool, self.call, "POST", "/place/told", {"text": text, "source": "crowd"},
                                       self.cfg.context_timeout_s)
            status, reply = await asyncio.wait_for(fut, self.cfg.context_timeout_s + 0.5)
        except Exception:                              # noqa: BLE001 - mind slow / down: the place stays
            status, reply = None, {}
        finally:
            self._told_busy = False
        if status == 200 and isinstance(reply, dict) and reply.get("changed"):
            log("realtime: place set by voice", flush=True)
            self.stats["place_told"] += 1
            if self._ws is not None and self._ready.is_set():
                await self._refresh_context()


    def _instructions(self):
        seen = self._seen()
        self._seen_sent, self._seen_at = seen, self.clock()
        self._looks_dirty, self._looks_sent_at = False, self.clock()
        return instructions(self.ctx.get("text"), seen, self._recent_pairs(), look_lines(self._looks, self.wall_ms()),
                            self._scene_line(), self._length_line(), self._role_line(), self._manner_line(),
                            answer_all_persona(self.persona) if self.cfg.answer_all else self.persona,
                            self._skills_block(), self._speaker_text(), self._body_line())


    def set_voice(self, key, voice_id, persona, fillers=None, search_fillers=None, trace=None, by="tool"):
        """Switch Docking's voice and the persona that goes with it (owner 2026-11-18). The voice is set when a session
        opens: an open session is closed and reopens with the new voice on the next speech."""
        if not voice_id:
            raise ValueError("no voice id")
        self.voice, self.voice_key, self.persona = voice_id, key, persona
        self.fillers = list(fillers or [])
        self.search_fillers = list(search_fillers or [])
        if self.filler_base:
            self.tool_fillers = load_tool_fillers(self.filler_base, key)
        ear_log.emit("trigger.voice", trace=trace or ear_log.new_trace(), by=by, sum=f"换声音：{key}",
                     ref={"session": self.session_no}, extra={"voice": key})
        if self.state != "closed":
            self.close("voice")
        self._write_status(self.clock())

    async def test_turn(self, text, speaker=None, called=False, wait_s=8.0):
        """A test sentence through the real path (the long-run check, 2026-11-18): the ear's own ASR text (preload,
        tool gate, who said it) and the same words as the user's turn of the live session. -> "sent" / why not."""
        if called:
            self.called("text", text=text)
        elif self.state == "closed" and self.want():
            self._open(self.clock(), "test")
        t0 = self.clock()
        while not (self._ws is not None and self._ready.is_set()):
            if self.clock() - t0 > wait_s:
                return "not_ready"
            await asyncio.sleep(0.2)
        if self.busy():
            return "busy"
        self.local_heard(text, speaker=speaker)
        self._send_soon({"type": "conversation.item.create", "item": {"type": "message", "role": "user",
                                                                      "content": [{"type": "input_text", "text": str(text)[:200]}]}})
        self._send_soon({"type": "response.create"})
        return "sent"


    def _send_instructions(self, why, trace=None):
        """session.update with fresh instructions; in the run log a call.realtime.context with the full text (the
        body table keeps each distinct text once a month, so the same instructions again cost one small record)."""
        instr = self._instructions()
        try:
            ear_log.emit("call.realtime.context", trace=trace, model=self.cfg.model, body={"system": instr},
                         ref={"session": self.session_no}, extra={"why": why, "chars": len(instr)})
        except Exception as e:                      # noqa: BLE001 - the session.update below must still go out
            _log_failed("context", e)
        self._send_soon({"type": "session.update", "session": {"instructions": instr}})
        self._live_instr = instr

    def _send_soon(self, obj):
        ws = self._ws
        if ws is not None:
            self.spawn(self._safe_send(ws, json.dumps(obj, ensure_ascii=False)))

    @staticmethod
    async def _safe_send(ws, text):
        try:
            await ws.send(text)
        except Exception:                              # noqa: BLE001 - the reader sees the drop
            pass

    async def _sender(self, ws):
        await self._ready.wait()
        while True:
            pcm = await self._in.get()
            while len(pcm) < 3200 and not self._in.empty():
                pcm += self._in.get_nowait()
            await ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm).decode()}))
            self.stats["in_bytes"] += len(pcm)

    async def _reader(self, ws, n):
        async for message in ws:
            try:
                e = json.loads(message)
            except ValueError:
                continue
            if isinstance(e, dict):
                self._event(e, n)

    def _event(self, e, n):
        t, now = e.get("type"), self.clock()
        self.stats["ev:" + str(t)[:48]] += 1
        if "search" in str(t):                         # a web search event (none documented yet): note it on the turn
            turn = self._turns.get(self._resp)
            if turn is not None:
                turn["search_events"] = turn.get("search_events", 0) + 1
        self._cold_live(now)                           # R18: an onset no segment used does not outlive COLD_MAX_S
        if t == "session.updated":
            if not self._ready.is_set():
                self._ready.set()
                self.state = "open"
                # (no greeting just because the owner is in view when a session opens: owner 2026-11-18, only an
                # appearance after away_s greets; one queued by that appearance is delivered by step())
        elif t == "conversation.item.created":
            item = e.get("item") or {}
            if self._next_kind != "speech" and item.get("role") == "user" and item.get("id"):
                self._inject_id = item["id"]           # our [事件] / question item, deleted again if refused
        elif t == "error":
            err = e.get("error") or {}
            msg = str(err.get("message") or "")[:160].replace(self.voice or "\0", "<voice>")
            log(f"realtime: session {n} error {str(err.get('code') or err.get('type') or '')[:40]}: {msg}", flush=True)
            self.stats["errors"] += 1
            if "user is speaking" in msg and self._next_kind != "speech":
                # 2026-11-18 13:08:40: a greeting's response.create was refused while the owner talked; its [事件] item
                # stayed in the conversation and the next answer (89 s later) greeted instead of answering. Drop it.
                kind, trace, prompt = self._next_kind, self._next_trace, self._next_prompt
                if kind in ("search_answer", "look_answer", "rescue") and prompt:
                    # an answer someone waits for is sent again when the room is quiet (2026-11-18: three BTC / gold
                    # search answers were refused and lost); a greeting is just dropped
                    r = self._redo if self._redo is not None and self._redo["prompt"] == prompt else None
                    self._redo = {"kind": kind, "prompt": prompt, "trace": trace, "at": (r or {}).get("at", now),
                                  "tries": (r or {}).get("tries", 0) + 1,
                                  "question": {"search_answer": self._search_question, "look_answer": self._look_question,
                                               "rescue": self._rescue_heard}.get(kind)}
                if self._inject_id:
                    self._send_soon({"type": "conversation.item.delete", "item_id": self._inject_id})
                self._next_kind, self._next_trace, self._next_prompt, self._inject_id = "speech", None, None, None
                self.stats["refused_busy"] += 1
                ear_log.emit("gate.realtime", trace=trace or ear_log.new_trace(), sum=f"没发出 {kind}：对方正在说话",
                             extra={"verdict": "refused_busy", "kind": kind,
                                    "reason": "千问在有人说话时拒绝了主动回复（招呼 / 补救 / 回答），已撤回"})
                self._stage("busy")                    # 0.5.05: the robot shows 等你说完 (older firmware ignores it)
            if not self._ready.is_set():               # refused at setup (bad voice, key): stop and retry later
                self._retry_at = now + self.cfg.retry_s
                self.close("refused")
        elif t == "input_audio_buffer.speech_started":
            self._last_speech = now
            if self._seg is None:                      # the turn's own trace starts with its segment (R17)
                cold = self._take_cold(now)
                self._seg = cold or {"start": now, "trace": self._take_called_trace(now) or ear_log.new_trace()}
        elif t == "input_audio_buffer.speech_stopped":
            self._speech_end = now
            recent = self._local_end if self._local_end is not None and now - self._local_end < 3 else None
            seg, self._seg = self._seg, None
            if seg is None:                            # no speech_started seen: the segment starts here
                cold = self._take_cold(now)
                seg = cold or {"start": now, "trace": ear_log.new_trace()}
            self._turn = self._heard_turn = {"kind": "speech", "speech_end": now, "heard_chars": None,
                                             "local_end": recent, "start": seg["start"], "trace": seg["trace"]}
            self._stage("think")
        elif t == "conversation.item.input_audio_transcription.completed":
            text = strip_emoji(e.get("transcript"))
            if self._heard_turn is not None:
                self._heard_turn["heard_chars"] = len(text)
                self._heard_turn["heard"] = text[:300]
            if self.on_transcript:
                self.on_transcript("heard", text)
            self._heard_text(text, now, told=True, trace=(self._heard_turn or {}).get("trace"))
        elif t == "conversation.item.ambient_audio_transcription.completed":
            # smart_turn_v2 judged this turn not meant for hello: no response comes. Logged (gate.realtime ambient),
            # the waiting turn is dropped and the dots go back; not in the anti-repeat or rescue paths.
            text = strip_emoji(e.get("transcript"))
            turn, self._heard_turn = self._heard_turn, None
            if self._turn is turn:
                self._turn = None
            self.stats["ambient"] += 1
            self._card_note("ambient")
            called = self.in_called(now) or self._called_text is not None
            if self.on_transcript:
                self.on_transcript("ambient", text)
            ear_log.emit("gate.realtime", trace=(turn or {}).get("trace") or ear_log.new_trace(),
                         sum=f"千问判：不是跟我说 {text[:40]}".strip(),
                         extra={"verdict": "ambient", "reason": "smart_turn_v2 判断不是跟 hello 说的，没有回答",
                                "smart_turn": "rejected", "heard": text[:300], "turn": self.cfg.turn,
                                **({"called": True} if called else {})})
            if called:                                 # being called wins over smart_turn: answer it anyway
                self._release_vision(now, True)
                self._answer_called((self._called_text or {}).get("text") or text, now,
                                    (turn or {}).get("trace"), "千问判不是跟我说")
            else:
                self._release_vision(now, False)
                self._stage("aside")                   # 0.5.04: the stage blocks flash blue (older firmware ignores it)
        elif t == "response.created":
            rid = str((e.get("response") or {}).get("id") or f"r{now}")
            self._resp = rid
            turn = self._turn if self._turn is not None and self._next_kind == "speech" else \
                {"kind": self._next_kind, "speech_end": None, "heard_chars": None,
                 "trace": self._next_trace if self._next_kind != "speech" else None,
                 "prompt": self._next_prompt if self._next_kind != "speech" else None}
            if turn.get("kind") == "speech":
                self._called_text = None
            turn["tool_depth"] = self._next_depth if turn.get("kind") == "tool_answer" else 0
            if turn.get("kind") in ("tool_answer", "tool_refused") and self._next_heard:
                turn["chain_heard"] = self._next_heard
            self._next_heard = None
            self._next_kind, self._turn, self._next_depth = "speech", None, 0
            self._next_trace = self._next_prompt = self._inject_id = None
            turn.update({"rid": rid, "session": n, "response_at": now, "first_delta": None, "sound_at": None,
                         "reply_chars": 0, "audio_ms": 0, "usage": None, "at": self.wall_ms(),
                         "gate": None if turn.get("kind") in ("speech", "rescue", "look_answer", "search_answer",
                                                              "tool_refused") else "play",
                         "held": [], "tx": "", "system": self._live_instr})
            if turn.get("kind") == "rescue":
                turn["heard"] = self._rescue_heard
            elif turn.get("kind") == "look_answer":
                turn["heard"] = self._look_question
            elif turn.get("kind") == "search_answer":
                turn["heard"] = self._search_question
            self._turns[rid] = turn
            if self.link() is None or self.link().tts_active:     # soul-tts is speaking: realtime yields
                self._dropped.add(rid)
                self._send_soon({"type": "response.cancel"})
        elif t == "response.audio.delta":
            rid = self._resp
            turn = self._turns.get(rid)
            if turn is not None and turn["first_delta"] is None:
                turn["first_delta"] = now
            try:
                pcm = base64.b64decode(e.get("delta") or "")
            except ValueError:
                return
            if turn is not None:
                turn["audio_ms"] += len(pcm) * 1000 // (SR_OUT * 2)
            if rid in self._dropped:
                return
            if turn is not None and turn.get("gate") is None:
                turn["held"].append(pcm)               # the reply gate has not decided yet
            else:
                self._out_put(("pcm", rid, pcm))
        elif t == "response.audio_transcript.delta":
            turn = self._turns.get(self._resp)
            if turn is not None:
                turn["tx"] += str(e.get("delta") or "")
                self._judge(self._resp, turn, False)
        elif t == "response.audio_transcript.done":
            turn = self._turns.get(self._resp)
            text = strip_emoji(e.get("transcript"))
            if turn is not None:
                turn["reply_chars"] = len(text)
                turn["reply"] = text[:400]
                turn["tx"] = text
                self._judge(self._resp, turn, True)
                if turn.get("kind") == "greet" and text:
                    self._said.append(text[:80])
                if text and turn.get("gate") == "play" and turn.get("kind") in ("speech", "rescue") and PROMISE_RE.search(text):
                    self._task_promise(text, turn, self.clock())
                if text and turn.get("gate") == "play":
                    self._recent.append((self.clock(), turn, text[:80]))   # heard is read later (it may come after)
                    if self._ws is not None and self._ready.is_set():     # the next reply already knows it
                        self._send_instructions("reply", turn.get("trace"))
            if self.on_transcript:
                self.on_transcript("reply", text)
        elif t == "response.function_call_arguments.done":
            turn = self._turns.get(str(e.get("response_id") or self._resp))
            if turn is not None and turn.get("fc") is None:
                turn["fc"] = {"call_id": e.get("call_id"), "name": str(e.get("name") or "")[:60],
                              "arguments": str(e.get("arguments") or "")[:4000]}
                if turn.get("gate") is None:
                    turn["gate"], turn["held"] = "tool", []
                    if self._pending_vision is not None or self._pending_search is not None:
                        self._release_vision(now, True)   # the model took the sentence (it called a tool for it)
        elif t == "response.done":
            resp = e.get("response") or {}
            rid = str(resp.get("id") or self._resp)
            turn = self._turns.get(rid)
            self._log_search(rid, turn, resp.get("usage"))
            if turn is not None and turn.get("fc") and not turn["audio_ms"]:
                turn["usage"], turn["status"] = resp.get("usage"), resp.get("status")
                self._resp = None
                self._finish_turn(rid, "tool_call")
                self.spawn(self._run_tool(turn))
                return
            if turn is not None:
                turn["usage"], turn["status"] = resp.get("usage"), resp.get("status")
                self._judge(rid, turn, True)            # no transcript came: decided on what there is
            self._resp = None
            if rid in self._dropped or turn is None or not turn["audio_ms"]:
                muted = turn.get("muted") if turn is not None else None
                self._finish_turn(rid, muted or ("no_audio" if rid not in self._dropped else "yielded"))
                self._try_rescue(now)                  # a muted 「嗯」 may be asked again right away
                self._try_look_answer(now)
                self._try_search_answer(now)
                if self._look_wait is None and self._search_wait is None and self._next_kind == "speech":
                    self._stage("skip")                # (waiting for a look / an answer on its way: the dots stay)
            else:
                self._out_put(("end", rid, b""))


    def _heard_in(self, turn, now):
        """The run log's candidates for what the ear heard in this turn: its own sentences that arrived within
        [turn start - HEARD_EPS_S, now] (by time; a neighbouring sentence can appear)."""
        start = turn.get("start")
        if start is None:
            return []
        return [dict(h) for t, h in self._heard_log if start - HEARD_EPS_S <= t <= now]

    def _take_called_trace(self, now):
        """The call's trace for the first sentence after it (2026-11-18: the owner could not see the hello that led to
        a search in its chain)."""
        t, self._called_trace = self._called_trace, None
        return t if t is not None and self.in_called(now) else None

    def _trace_now(self, own=None):
        """The trace a local sentence's consequences (look wait, burst, hush) go on: the turn it falls into (the open
        segment, the session being opened for it, or the last turn while its record is not written yet), else the
        sentence's own heard.asr trace."""
        if self._seg is not None:
            return self._seg["trace"]
        h = self._heard_turn
        if h is not None and (h.get("rid") is None or h.get("rid") in self._turns) and \
                not (self._last_speech is not None and self._last_speech > h["speech_end"]):
            return h.get("trace")
        cold = self._cold_live(self.clock())
        if cold is not None:
            return cold["trace"]
        return self._take_called_trace(self.clock()) or own

    def _cold_live(self, now):
        """The onset that opened the session, while it is at most COLD_MAX_S old (R18); an older one is dropped."""
        if self._cold is not None and now - self._cold["start"] > COLD_MAX_S:
            self._cold = None
        return self._cold

    def _take_cold(self, now):
        """The live cold onset for the segment starting now; it serves one segment only."""
        cold, self._cold = self._cold_live(now), None
        return cold


    # ---- what was heard: the session's transcript and the ear's own ASR ----
    def local_heard(self, text, trace=None, speaker=None):
        """Ear: a sentence of its own ASR (SenseVoice) at the speech end, on the event loop. Live, the session's own
        transcript was often missing; this one feeds the 「嗯」 rescue, the burst words and 闭嘴 too. Not in the console
        log. trace: its heard.asr trace (run log): listed in extra.heard of the turn whose window it falls into (R17); what it
        sets off (look wait, burst, hush) goes on that turn's trace, else on this one."""
        if not text or not self.active() or not self.in_crowd():
            return
        now = self.clock()
        self._local_texts.append((now, str(text)[:300]))
        if isinstance(speaker, dict):                  # who said it (the ear's voiceprint): 亲口 / 认识的人
            band, name = speaker.get("band"), speaker.get("name")
            if band == "unsure":                       # a top match only with a clear lead (see owner_voice_lead)
                ru = speaker.get("runner_up") or {}
                sc, rs = speaker.get("score"), ru.get("score")
                same = self._owner_voice(name) and self._owner_voice(ru.get("name"))   # both the owner's prints
                lead = isinstance(sc, (int, float)) and isinstance(rs, (int, float)) and sc - rs >= UNSURE_LEAD
                if not (lead or same and isinstance(sc, (int, float))):   # security review 2026-11-19: fail closed
                    name = None
            self._speakers.append((now, band, name))
            said = self._speaker_text(now)
            if said != self._speaker_sent and self._ws is not None and self._ready.is_set():
                self._speaker_sent = said              # the model learns the speaker changed (owner vs a stranger)
                self._send_instructions("speaker", self._trace_now(trace))
        self._heard_log.append((now, {"trace": trace, "text": str(text)[:300], "at": self.wall_ms()}))
        try:                                           # 先看见 (2026-11-19): the sentence's card, logged later
            self._card_new(str(text)[:300], trace, speaker, now)
        except Exception as e:                         # noqa: BLE001 - a card never costs the sentence
            _log_failed("card", e)
        self._preload(str(text)[:300], now, trace=self._trace_now(trace))   # 自动档: before _heard_text's gates
        self._heard_text(str(text)[:300], now, trace=self._trace_now(trace))

    def _heard_text(self, text, now, told=False, trace=None):
        self._length_from(str(text), now, trace)
        self._debug_from(str(text), now, trace)
        self._low_from(str(text), now, trace)
        self._role_from(str(text), now, trace)
        # (2026-11-20: no more "direct orders": the factory voice commands are off, so a command word after the call
        # word is an ordinary called sentence; review fix, the early return here had swallowed the call)
        rest = call_rest(text)
        if rest is not None:
            self.called("text", text=text, trace=trace)
            if rest and not told:                      # the ear's ASR: make sure this sentence gets an answer
                self._called_text = {"text": rest, "at": now, "trace": trace}
        look, key = is_look_ask(text), self._burst_key(text)
        if (look or key == "burst_start") and self.cfg.turn != "server_vad" and not self.in_called(now):
            # owner 2026-11-18 "决定视觉爆发的前提是过了qwen的拒识闸门": no burst / look wait until the realtime model
            # answered the sentence with a real reply (_judge: not 「嗯」); ambient, 「嗯」 or nothing in 6 s = none.
            # Both sources wait: the model transcribes sentences it then ignores (15:09 / 15:14 2026-11-18: talk with
            # a colleague, "你看这把…", "还得看一下表", each started a burst from the transcript alone). A call skips it.
            if self._pending_vision is None or now - self._pending_vision["at"] > 1.0 or not told:
                self._pending_vision = {"text": text, "at": now, "trace": trace}
        else:
            self._vision_from(text, now, trace, look, key)
        if not look and not self.tools_on() and not self._task_follow(text, now, trace) and is_search_ask(text):
            if self.cfg.turn != "server_vad" and not self.in_called(now):
                # 2026-11-18 18:12: "别扔了就行，那只能去网上搜搜看有没有" (to a colleague) started a search that hello then
                # read out. Same gate as the burst: the search waits for the model's real reply to this sentence.
                if self._pending_search is None or now - self._pending_search["at"] > 1.0 or not told:
                    self._pending_search = {"text": text, "at": now, "trace": trace}
            else:
                self._task_open(text, now, trace)
                self._start_search_wait(text, now, trace, task=self._qtask)
        r = self._rescue
        if r is not None and not r["sent"] and now - r["at"] <= RESCUE_S:
            r["texts"].append(text)
            self._try_rescue(now)
        if told:
            self._told(text)
        if QUIET_RE.search(norm_text(text)) and not HUSH_ANY.search(norm_text(text)):
            # owner 2026-11-18 "设定静默（临时-直到唤醒）": 安静一会儿 = quiet until called (闭嘴 still = Docking off)
            span = mode_span.parse(text)               # 2026-11-19: 安静 30 分钟 / 直到我说退出 / 直到我叫你
            if not self.quiet_on(now) or span is not None:
                self.set_quiet("quiet", trace=trace, by="heard", heard=text, span=span)
        elif is_hush(text):
            self._hush(text, trace)


    def _stage(self, stage):
        """Crowd's stage dots on the robot (via the mode relay); never costs the session."""
        if self._touch_word and stage != "listen":     # she is on it (or it was not for her): 在听 goes
            self._word("", 0)
        try:
            if self.modes is not None and hasattr(self.modes, "crowd_stage"):
                self.modes.crowd_stage(stage)
        except Exception as error:                     # noqa: BLE001
            log(f"realtime: stage {stage} failed: {type(error).__name__}", flush=True)

    def _out_put(self, item):
        try:
            self._out.put_nowait(item)
        except asyncio.QueueFull:
            self.stats["out_dropped"] += 1
            self._dropped.add(item[1])

    # ---- output to the robot ----
    async def _output(self):
        enc = None
        while True:
            kind, rid, pcm = await self._out.get()
            st = self._stream
            if rid in self._dropped:
                continue
            if kind == "pcm" and (st is None or st["rid"] != rid):
                await self._await_previous()
                link = self.link()
                if link is None or link.tts_active or rid in self._dropped:
                    self._dropped.add(rid)
                    continue
                st = await self._start_stream(link, rid)
                enc = self.encoder_factory()
                if st is None:
                    continue
            if st is None or st["rid"] != rid or st["closed"]:
                continue
            frames = enc.feed(pcm) if kind == "pcm" else enc.flush()
            for payload in frames:
                if not await self._send_frame(st, payload):
                    break
            if kind == "end" and not st["closed"]:
                st["ended"], st["ended_at"] = True, self.clock()
                await st["link"].send_from_realtime(json.dumps({"type": "tts.end", "stream": st["sid"],
                                                                "frames": st["seq"], "duration_ms": st["sent_ms"]}))

    async def _start_stream(self, link, rid):
        sid = next(self._sid)
        now = self.clock()
        self._stream = st = {"sid": sid, "rid": rid, "link": link, "seq": 0, "sent_ms": 0, "played_ms": 0,
                             "started_at": now, "progress_at": now, "ended": False, "ended_at": None,
                             "reported": False, "closed": False}
        ok = await link.send_from_realtime(json.dumps({"type": "tts.start", "stream": sid, "codec": "opus",
                                                       "sample_rate": SR_OUT, "frame_ms": FRAME_MS,
                                                       "utterance": f"rt{self.session_no}"}))
        if not ok:
            st["closed"] = st["reported"] = True
            self._finish_turn(rid, "robot_gone")
            return None
        return st

    async def _wait(self, timeout):
        self._progress.clear()
        try:
            await asyncio.wait_for(self._progress.wait(), max(0.01, timeout))
        except asyncio.TimeoutError:
            pass

    async def _await_previous(self):
        st = self._stream
        while st is not None and st["ended"] and not st["reported"]:
            base = max(st["ended_at"], st["progress_at"])
            left = base + max(0, st["sent_ms"] - st["played_ms"]) / 1000 + self.cfg.done_grace_s - self.clock()
            if left <= 0:
                self._give_up(st, "no_done")
                return
            await self._wait(min(1.0, left))

    def _give_up(self, st, why):
        st["closed"] = st["reported"] = True
        self.spawn(st["link"].send_from_realtime(json.dumps({"type": "tts.stop", "stream": st["sid"]})))
        self._finish_turn(st["rid"], why)
        self._progress.set()

    async def _send_frame(self, st, payload):
        while st["sent_ms"] - st["played_ms"] > self.cfg.max_lead_ms and not st["closed"]:
            quiet = self.clock() - st["progress_at"]
            if quiet > self.cfg.stall_s:
                self._give_up(st, "stalled")
                return False
            await self._wait(min(2.0, self.cfg.stall_s - quiet))
        if st["closed"]:
            return False
        if not await st["link"].send_from_realtime(pack(st["sid"], st["seq"], payload)):
            st["closed"] = st["reported"] = True
            self._finish_turn(st["rid"], "robot_gone")
            return False
        st["seq"] += 1
        st["sent_ms"] += FRAME_MS
        self.stats["frames"] += 1
        return True


    # ---- records ----
    def _finish_turn(self, rid, state):
        turn = self._turns.pop(rid, None)
        if turn is None:
            return
        ms = lambda a, b: None if a is None or b is None else int((b - a) * 1000)     # noqa: E731
        timings = {"ear_speech_end_to_first_audio_ms": ms(turn.get("local_end"), turn.get("first_delta")),
                   "ear_speech_end_to_robot_sound_ms": ms(turn.get("local_end"), turn.get("sound_at")),
                   "speech_end_to_first_audio_ms": ms(turn.get("speech_end"), turn.get("first_delta")),
                   "response_to_first_audio_ms": ms(turn.get("response_at"), turn.get("first_delta")),
                   "first_audio_to_robot_sound_ms": ms(turn.get("first_delta"), turn.get("sound_at")),
                   "speech_end_to_robot_sound_ms": ms(turn.get("speech_end"), turn.get("sound_at"))}
        usage = usage_tokens(turn.get("usage"))
        doc = {"tid": f"{self.wall_ms()}-{rid}"[:96], "at": turn.get("at"), "session": turn.get("session"),
               "kind": turn.get("kind"), "state": state, "model": self.cfg.model, "timings": timings,
               "heard_chars": turn.get("heard_chars"), "reply_chars": turn.get("reply_chars"),
               "heard": turn.get("heard"), "reply": turn.get("reply"),     # DB only (as chat_turns), never logged
               "audio_ms": turn.get("audio_ms"), "usage": usage, "cost_cny": cost_cny(turn.get("usage"))}
        self.stats["turns"] += 1
        self.stats["cost_micro_cny"] += int(doc["cost_cny"] * 1e6)
        if turn.get("kind") not in LOCAL_KINDS:
            self._card_note("turn", state)             # the sentence cards (rt_cards) read what became of their turn
        log(f"realtime: turn {turn.get('session')}/{turn.get('kind')} {state}: ear speech end->audio "
            f"{timings['ear_speech_end_to_first_audio_ms']} ms (server VAD end->audio "
            f"{timings['speech_end_to_first_audio_ms']} ms), ->robot sound {timings['ear_speech_end_to_robot_sound_ms']} ms, "
            f"out {turn.get('audio_ms')} ms, {doc['cost_cny']:.4f} CNY", flush=True)
        if self.db is not None:
            try:
                self.db.realtime_turn(doc)
            except Exception:                          # noqa: BLE001 - a record never costs the session
                pass
        if turn.get("kind") not in LOCAL_KINDS:
            self._log_turn(turn, doc, state)

    @_run_log
    def _log_turn(self, turn, doc, state):
        """call.realtime.turn: usage as DashScope sent it, the cost estimate, what was heard (the model's transcript and
        the ear's own words) and the reply; ref.tid = the realtime_turns row."""
        heard = self._heard_in(turn, self.clock())
        inp = {k: v for k, v in (("heard", turn.get("heard")), ("ear", [h["text"] for h in heard] or None),
                                 ("prompt", turn.get("prompt"))) if v}
        reply = turn.get("tx") or turn.get("reply")
        ear_log.emit("call.realtime.turn", trace=turn.get("trace") or ear_log.new_trace(), model=self.cfg.model,
                     key=self.key, ok=state not in FAILED_STATES, err=state if state in FAILED_STATES else None,
                     ms=doc["timings"].get("response_to_first_audio_ms"), usage=turn.get("usage"),
                     cost=ear_log.cost(doc["cost_cny"], PRICE_BASIS), sum=(reply or f"[{state}]")[:60],
                     body={"system": turn.get("system") or None, "input": inp or None, "output": reply or None},
                     ref={"tid": doc["tid"], "session": turn.get("session"), "rid": turn.get("rid")},
                     extra={"kind": turn.get("kind"), "state": state, "heard": heard, "timings": doc["timings"],
                            "audio_ms": turn.get("audio_ms"), "heard_chars": turn.get("heard_chars"),
                            "reply_chars": turn.get("reply_chars"), "tokens": doc["usage"],
                            # reply length data (see LENGTH_S): what was asked, the length mode, how long the answer was
                            "ask_kind": ask_kind(turn.get("heard") or next((h["text"] for h in heard), "")),
                            "length_mode": self._length_mode()})

    def set_answer_all(self, on):
        """全答 switch (owner 2026-11-19 night): on = answer every sentence, no judgement (server_vad, no 「嗯」 rule, no
        reply / tool gate; 安静 = no session, a tap opens it). Off = as before (the 智能断句 switch's turn detection).
        An open session closes once idle and reopens with the new prompt and turn. Returns True when it changed."""
        on = bool(on)
        if on == self.cfg.answer_all:
            return False
        log(f"realtime: answer all {'on' if on else 'off'} (全答 switch)", flush=True)
        self.cfg.answer_all = on
        smart = self._smart_wanted if self._smart_wanted is not None else Config.turn != "server_vad"
        self.cfg.turn = "server_vad" if on else ("smart_turn_v2" if smart else "server_vad")
        self._turn_changed = self.state != "closed"
        self._status_at = -1e9
        return True

    def set_turn(self, smart):
        """智能断句 switch (owner 2026-11-19 night: "做一个开关，控制 smart turn on/off，然后给我们的网页前端用"):
        smart_turn_v2 (DashScope judges whether a sentence is over and meant for hello) or server_vad (a pause ends the
        sentence). The type is set when a session opens, so an open session closes once idle (no reply, no tool) and
        the next one opens with the new setting. Returns True when it changed."""
        self._smart_wanted = bool(smart)
        if self.cfg.answer_all:                        # 全答 runs server_vad; the choice applies when it is off again
            return False
        turn = "smart_turn_v2" if smart else "server_vad"
        if turn == self.cfg.turn:
            return False
        log(f"realtime: turn detection {self.cfg.turn} -> {turn} (智能断句 switch)", flush=True)
        self.cfg.turn = turn
        self._turn_changed = self.state != "closed"
        self._status_at = -1e9                         # the page sees the new value at the next step
        return True

    def set_intent(self, on):
        """工具分类器 switch (owner 2026-11-19 night: "如果关掉就不用它做任何判断"): off = no skill preload, and the tool
        gate does not use the rules or the head (Qwen's own tool call decides). Returns True when it changed."""
        on = bool(on)
        if on == self.intent_on:
            return False
        log(f"realtime: tool classifier {'on' if on else 'off'} (工具分类器 switch)", flush=True)
        self.intent_on = on
        self._status_at = -1e9
        return True

    def _write_status(self, now):
        self._status_at = now
        if self.db is None:
            return
        try:
            self.db.switch_status("realtime", {
                "running": [f"rt{self.session_no}"] if self.state != "closed" else [], "state": self.state,
                "available": bool(self.key and self.voice), "on": self.switch.on,
                "hour_used_s": int(self.hour_used(now)), "turns": self.stats["turns"],
                "cost_cny": round(self.stats["cost_micro_cny"] / 1e6, 4), "at": self.wall_ms(),
                "quiet": self.quiet_on(now), "low": self.low_on(now), "role": self.role_text(now),
                "debug": {"since_ms": self._debug_since_ms} if self.debug_on(now) else None,
                "voice_key": self.voice_key, "turn": self.cfg.turn, "intent": self.intent_on,
                "answer_all": self.cfg.answer_all, "chat": self.chat_on(now)})
        except Exception:                              # noqa: BLE001
            pass

    # ---- tasks ----
    def spawn(self, coro, clean=False):
        # clean: a fresh context, so a long-lived task never keeps the run-log scope (trace) of whoever opened it
        task = asyncio.create_task(coro, context=contextvars.Context()) if clean else asyncio.create_task(coro)
        self._bg.add(task)
        task.add_done_callback(self._done)
        return task

    def _done(self, task):
        self._bg.discard(task)
        if not task.cancelled() and task.exception() is not None:
            e = task.exception()
            log("realtime: task failed\n" + "".join(traceback.format_exception(type(e), e, e.__traceback__)).rstrip(),
                flush=True)
            ear_log.error(e, "realtime.task")

    async def shutdown(self):
        self.close("shutdown")
        for t in list(self._bg):
            t.cancel()
        await asyncio.gather(*list(self._bg), return_exceptions=True)
        self._pool.shutdown(wait=False, cancel_futures=True)
