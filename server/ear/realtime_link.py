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
CONTEXT_S. Owner greeting: VisionLink hands every M12 event to on_vision(); FaceView tracks which faces are in view.
When the owner (mind's owner.face_names) appears after AWAY_S unseen while in crowd, or is in view when a session opens,
a "[事件] 主人（<call>）出现在你面前了" user text item + response.create goes in; queued while hello sounds, dropped after
GREET_MAX_AGE_S, never twice within AWAY_S. The call name comes from mind at run time and is never logged.

Crowd tap (owner 2026-11-14 "围观模式，单击，不要停了，改成检查m12视觉，看有没有认识的人在…"): MouthLink hands a
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
1000 ms (defaults 0.5 / 800). Who is talking to hello (addressing) comes later.
Caps: one session at most MAX_SESSION_S, closed after IDLE_S without speech, and at most HOUR_CAP_S of open sessions in
any hour; after a cap / idle close the next session opens only on the next speech onset (or an owner greeting).
Records: one realtime_turns document per response (timings, transcript lengths only, token usage, cost estimate); the
log has timings and ids only, never text, names, the key or the voice id.
"""
import asyncio
import base64
import collections
import concurrent.futures
import itertools
import json
import math
import random
import re
import struct
import time
import traceback

from mouth_link import RT_STREAM_BASE

MODEL = "qwen-audio-3.1-realtime-flash"
URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model={model}"
SR_IN, SR_OUT, FRAME_MS = 16000, 24000, 60
FRAME_SAMPLES = SR_OUT * FRAME_MS // 1000
FRAME_BYTES = FRAME_SAMPLES * 2
MAGIC = b"SPK1"
HDR = struct.Struct("<4sIIHH")
# Official price, 华北2（北京）, 元 per million tokens (help.aliyun.com/zh/model-studio/qwen-audio-3-1-realtime-flash,
# read 2026-11-14). With audio output the accompanying text output is not billed.
PRICE_CNY_PER_M = {"text_in": 1.5, "audio_in": 6.0, "text_out": 4.5, "audio_out": 12.0}
STEP_S = 0.2
log = print                                         # tests silence it

PERSONA = (
    "你是 hello，住在主人办公桌上的桌面宠物机器人，用小孩子的声音说话：活泼、好奇、爱凑热闹，有点小脾气。"
    "现在是围观模式：大家围着你看，跟你聊天，你就跟他们聊。\n"
    "【要求】\n"
    "- 每次用一两句口语短句回答，越短越好，像小朋友说话。\n"
    "- 只说要说出口的话：不加表情符号、括号和动作描写，不说“作为AI”。\n"
    "- 不给医疗、健康、用药方面的建议；背景里提到身体情况也不要劝人看医生。\n"
    "- 听不清、没头没尾，就简短反问一句；不确定的事不要编。\n"
    "- 背景只在跟话题有关时才用，不要硬扯，不要每句都提同一件事。\n"
    "- 收到“[事件] 主人（名字）出现在你面前了，长招呼”时：用这个名字大声、热情地喊主人，开心地打招呼，"
    "再顺口说一句你看到的情景或想到的事，两三句。每次都换新说法，不要跟事件里列的“之前说过”的一样。\n"
    "- 收到“[事件] 主人又出现了，短招呼：只说「…」”时：刚才已经见过，只原样说「」里那个词，一个字都不多说，不喊名字。\n"
    "- 收到“[事件] 有人点了你一下，让你跟面前的人打招呼”时：按事件里说的称呼主动打招呼，嘴甜一点、热情一点；"
    "认识的人叫名字，不认识的就用事件给的称呼（美女、帅哥、小朋友、爷爷、奶奶）。只夸，不评论长相的缺点，不开人身玩笑。"
    "事件说没看到人脸，就说“我看看……咦，没看到人呀”这样的话，再友好地问声好。\n"
    "- 你有眼睛：头上的摄像头和人脸识别，看到的都写在最后的【你的眼睛】里：人脸识别是实时认出的人，"
    "摄像头照片识图是一分钟整理一次的画面（人在做什么、环境、变化）。有人问你能不能看见、看到了谁、看到了什么，"
    "就把这两样合起来回答：谁在（认得的叫名字，不认得的说个大概），在做什么，周围什么样；"
    "两样都没有，就说现在没看清。不要说自己没有眼睛、看不见。\n"
    "- 有人问刚才、最近、今天早些时候发生了什么、聊了什么，先照背景里的“最近的情景”和“最近的纪要”回答，"
    "可以带上大概的时间；里面没有的就说不记得了，不要编。")

_EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0000FE00-\U0000FE0F\U0001F1E6-\U0001F1FF"
                    "\U0000200D\U000020E3\U00002B00-\U00002BFF\U000E0020-\U000E007F]")


def strip_emoji(text):
    return _EMOJI.sub("", str(text or "")).strip()


EYE_LINES = ("画面：", "看到的人（M12 认出）：")


def instructions(context_text=None, seen=None):
    """PERSONA + 【背景】 + 【你的眼睛】 last: the live M12 view and the photo description (moved out of the background,
    where the voice model tended to miss them; owner 2026-11-14)."""
    rest, eye = [], {}
    for line in (context_text or "").splitlines():
        key = next((k for k in EYE_LINES if line.startswith(k)), None)
        if key:
            eye[key] = line[len(key):]
        else:
            rest.append(line)
    out = PERSONA + ("\n\n【背景】\n" + "\n".join(rest) if rest else "")
    eyes = []
    if seen:
        eyes.append(f"人脸识别（实时）：{seen}")
    if eye.get("看到的人（M12 认出）："):
        eyes.append(f"人脸识别（最近 3 分钟认出过）：{eye['看到的人（M12 认出）：']}")
    if eye.get("画面："):
        eyes.append(f"摄像头照片识图（大约一分钟前）：{eye['画面：']}")
    if eyes:
        out += "\n\n【你的眼睛】\n" + "\n".join(eyes)
    return out


def seen_text(faces, names, owner_faces, owner_call):
    """What hello sees right now, from FaceView.recent() (largest first): one line, never logged or stored."""
    if not faces:
        return "眼前没有人脸。"
    parts = []
    for f in faces[:5]:
        n = f.get("name")
        if n:
            parts.append(f"主人（{owner_call}）" if n in owner_faces else (names.get(n) or n))
        else:
            parts.append(address(f.get("gender"), f.get("age_group"))[1] or "一个认不出的人")
    more = f"，还有 {len(faces) - 5} 个人" if len(faces) > 5 else ""
    return f"眼前有 {len(faces)} 个人：" + "、".join(parts) + more + "。"


def session_config(voice, instr, silence_ms=500, threshold=0.5):
    return {"modalities": ["text", "audio"], "voice": voice, "instructions": instr,
            "input_audio_format": "pcm", "output_audio_format": "pcm",
            "input_audio_transcription": {"language": "zh"},
            "turn_detection": {"type": "server_vad", "threshold": threshold, "silence_duration_ms": silence_ms}}


SHORT_WORDS = ("hi", "你好", "嗯哼？", "嘿")     # owner 2026-11-14: a short hello is one of these, or two beeps


def beep_pcm(rng=random):
    """Two R2D2-style chirps (24 kHz PCM16 mono, ~0.33 s), a little different every time; made here, no model."""
    out = bytearray()

    def chirp(ms, f0, f1, warble):
        n = SR_OUT * ms // 1000
        ph = 0.0
        for i in range(n):
            x = i / n
            f = f0 + (f1 - f0) * x + warble * math.sin(2 * math.pi * 30 * i / SR_OUT)
            ph += 2 * math.pi * f / SR_OUT
            env = min(1.0, i / (SR_OUT * 0.006), (n - i) / (SR_OUT * 0.012))
            out.extend(struct.pack("<h", int(9000 * env * math.sin(ph))))

    chirp(rng.randint(90, 130), rng.randint(1400, 1900), rng.randint(2600, 3300), rng.randint(80, 220))
    out.extend(bytes(2 * SR_OUT * rng.randint(50, 80) // 1000))
    chirp(rng.randint(70, 110), rng.randint(2500, 3100), rng.randint(1500, 2100), rng.randint(60, 200))
    return bytes(out)


BEEP = beep_pcm(random.Random(7))                   # = the owner's sample beep1.wav


def greet_text(call, kind="long", said=(), word=None):
    """Owner greeting event (owner 2026-11-14): one long, named, always-new greeting per long_greet_s; a re-recognition
    inside that window gets a short hello. said: hello's last long greetings (memory only), so it does not repeat."""
    if kind == "short":
        return f"[事件] 主人又出现了，短招呼：只说「{word or SHORT_WORDS[0]}」。"
    text = f"[事件] 主人（{call}）出现在你面前了，长招呼。"
    if said:
        text += "之前说过：" + "；".join(f"「{t}」" for t in said) + "。这次换个完全不同的说法。"
    return text


def usage_tokens(usage):
    u = usage or {}
    i, o = u.get("input_tokens_details") or {}, u.get("output_tokens_details") or {}
    n = lambda v: v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0   # noqa: E731
    return {"in_text": n(i.get("text_tokens")), "in_audio": n(i.get("audio_tokens")),
            "out_text": n(o.get("text_tokens")), "out_audio": n(o.get("audio_tokens"))}


def cost_cny(usage):
    t = usage_tokens(usage)
    out_text = 0 if t["out_audio"] else t["out_text"] * PRICE_CNY_PER_M["text_out"]
    return round((t["in_text"] * PRICE_CNY_PER_M["text_in"] + t["in_audio"] * PRICE_CNY_PER_M["audio_in"] +
                  t["out_audio"] * PRICE_CNY_PER_M["audio_out"] + out_text) / 1e6, 6)


def load_voice(path, key):
    """The voice id under `key` in a soul-tts voices.json ({"voices": {key: {"voice": id}}}), or None. Never logged."""
    try:
        with open(path, encoding="utf-8") as f:
            v = ((json.load(f).get("voices") or {}).get(key) or {}).get("voice")
    except (OSError, ValueError, AttributeError):
        return None
    return v if isinstance(v, str) and v else None


def pack(stream, seq, payload):
    return HDR.pack(MAGIC, stream, seq, FRAME_MS, 0) + payload


class OpusFrames:
    """24 kHz PCM16 in any chunk size -> whole 60 ms Opus packets (same settings as soul-tts's Codec)."""

    def __init__(self, encoder=None):
        if encoder is None:
            import opuslib_next
            encoder = opuslib_next.Encoder(SR_OUT, 1, opuslib_next.APPLICATION_AUDIO)
            encoder.bitrate = 32000
            try:
                encoder.complexity = 5
            except Exception:                    # noqa: BLE001
                pass
        self.enc, self.carry = encoder, b""

    def feed(self, pcm):
        buf = self.carry + pcm
        n = len(buf) // FRAME_BYTES * FRAME_BYTES
        self.carry = buf[n:]
        return [self.enc.encode(buf[i:i + FRAME_BYTES], FRAME_SAMPLES) for i in range(0, n, FRAME_BYTES)]

    def flush(self):
        """The last partial frame, padded with silence (soul-tts never cuts a sentence's tail either)."""
        if not self.carry:
            return []
        buf, self.carry = self.carry + bytes(FRAME_BYTES - len(self.carry)), b""
        return [self.enc.encode(buf, FRAME_SAMPLES)]


AGE_GROUPS = ("child", "young", "adult", "elder")


def address(gender, age_group):
    """How hello calls an unknown face from the M12's apparent attributes (owner 2026-11-14): (call, description)."""
    g = gender if gender in ("m", "f") else None
    a = age_group if age_group in AGE_GROUPS else None
    if a == "child":
        return "小朋友", "一个小朋友"
    if a == "elder" and g:
        return ("爷爷", "一位爷爷") if g == "m" else ("奶奶", "一位奶奶")
    if a in ("adult", "young") and g:
        return ("帅哥", "一位帅哥") if g == "m" else ("美女", "一位美女")
    return None, None


class FaceView:
    """The robot's M12 faces in view, from its vision change events (in memory only; never stored or logged).
    event() -> the name that has just appeared after away_s unseen (or never seen), else None. A face with no event
    for face_ttl_s counts as gone (a lost link); a lost face still counts as seen for recent_s (the robot reports a
    loss 1.5 s after the face left)."""

    def __init__(self, away_s=300.0, face_ttl_s=600.0, recent_s=2.0):
        self.away_s, self.face_ttl_s, self.recent_s = away_s, face_ttl_s, recent_s
        self.faces = {}                       # face id -> {name, area, gender, age_group, at}
        self.lost = {}                        # face id -> (face, lost at), kept recent_s
        self.last_seen = {}                   # name -> when it was last in view

    def _expire(self, now):
        for fid, f in list(self.faces.items()):
            if now - f["at"] > self.face_ttl_s:
                del self.faces[fid]
                if f["name"]:
                    self.last_seen[f["name"]] = max(self.last_seen.get(f["name"], f["at"]), f["at"])
        for fid, (_, at) in list(self.lost.items()):
            if now - at > self.recent_s:
                del self.lost[fid]

    def _lose(self, fid, now):
        f = self.faces.pop(fid, None)
        if f:
            self.lost[fid] = (f, now)
            if f["name"]:
                self.last_seen[f["name"]] = now

    def seen_at(self, name, now):
        if any(f["name"] == name for f in self.faces.values()):
            return now
        return self.last_seen.get(name)

    def present(self, names, now):
        self._expire(now)
        return any(f["name"] in names for f in self.faces.values() if f["name"])

    def recent(self, now):
        """Faces in view or lost within recent_s, largest first."""
        self._expire(now)
        got = list(self.faces.values()) + [f for f, _ in self.lost.values()]
        return sorted(got, key=lambda f: -f["area"])

    def event(self, msg, now):
        self._expire(now)
        ev, fid = msg.get("event"), msg.get("face")
        if ev in ("face_found", "face_named", "face_attrs", "face_unknown"):
            f = self.faces.get(fid)
            if f is None:
                f = self.faces[fid] = {"name": None, "area": 0, "gender": None, "age_group": None, "at": now}
                self.lost.pop(fid, None)
            f["at"] = now
            box = msg.get("box")
            if isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box):
                f["area"] = max(0, box[2]) * max(0, box[3])
            if ev == "face_attrs" or msg.get("gender") is not None:
                f["gender"], f["age_group"] = msg.get("gender"), msg.get("age_group")
            if ev == "face_named" and isinstance(msg.get("name"), str) and msg["name"]:
                name = msg["name"][:32]
                before = self.seen_at(name, now) if f["name"] != name else now
                f["name"] = name
                self.last_seen[name] = now
                return name if before is None or now - before >= self.away_s else None
        elif ev == "face_lost":
            self._lose(fid, now)
        elif ev == "vision_off":
            for k in list(self.faces):
                self._lose(k, now)
        return None


def tap_pick(faces):
    """What a crowd tap greets, from FaceView.recent() (largest first): {kind: known/unknown/none, face, others}."""
    known = [f for f in faces if f["name"]]
    if known:
        return {"kind": "known", "face": known[0], "others": [f["name"] for f in known[1:4]]}
    if faces:
        return {"kind": "unknown", "face": faces[0], "others": []}
    return {"kind": "none", "face": None, "others": []}


def tap_text(pick, names, owner_faces, owner_call):
    """The event text for a tap greeting. names: M12 name -> how to call that person (mind's context)."""
    def call_of(name):
        if name in owner_faces:
            return f"主人（{owner_call}）"
        return names.get(name) or name
    head = "[事件] 有人点了你一下，让你跟面前的人打招呼："
    if pick["kind"] == "known":
        text = head + f"面前是{call_of(pick['face']['name'])}"
        others = [call_of(n) for n in pick["others"] if n != pick["face"]["name"]]
        if others:
            text += "，旁边还有" + "、".join(dict.fromkeys(others))
        return text + "。"
    if pick["kind"] == "unknown":
        call, desc = address(pick["face"].get("gender"), pick["face"].get("age_group"))
        if call:
            return head + f"面前是{desc}，你不认识，叫TA“{call}”，嘴甜一点。"
        return head + "面前有一个人，你不认识，看不出是谁，就友好地问声好。"
    return "[事件] 有人点了你一下，让你打招呼，可是你现在没看到人脸。"


def _direct_connect(url, **kw):
    """websockets connect without the system proxy (websockets 15+ would use one, and then needs python-socks)."""
    from websockets.asyncio.client import connect
    try:
        return connect(url, proxy=None, **kw)
    except TypeError:                                  # websockets < 15: no proxy support at all
        return connect(url, **kw)


class Config:
    model = MODEL
    # Crowd turn-taking v0 (owner 2026-11-14: many people talk, hello must not be cut off easily): less twitchy than
    # DashScope's defaults (800 ms, 0.5). No audio goes up while a response is generated or played, so new speech
    # can never cancel a reply (no interrupt option is documented for this model); guard_s after it ends.
    silence_ms = 1000
    vad_threshold = 0.6
    guard_s = 0.6
    gate = "vad"                 # "vad": only around the ear's own speech (no silence billed); "all": everything
    preroll_s = 0.8
    tail_s = 1.2
    in_queue = 120               # ~7 s of 60 ms packets while a session connects
    max_session_s = 600
    idle_s = 180
    hour_cap_s = 1800
    context_s = 30
    context_timeout_s = 2.5
    open_timeout_s = 6
    retry_s = 30                 # after a failed / refused session
    max_lead_ms = 8000
    stall_s = 10
    done_grace_s = 10
    away_s = 60                  # owner unseen this long, then seen again = a new recognition
    long_greet_s = 600           # at most one long named greeting per 10 min (owner); inside it: a short hello
    short_greet_min_s = 60       # short hellos at most this often
    greet_said_keep = 3          # last long greetings kept in memory so the next one differs
    greet_max_age_s = 10
    tap_cooldown_s = 3
    seen_min_s = 3                                     # live view re-sent at most this often
    face_recent_s = 2.0
    status_s = 30


class RealtimeHub:
    def __init__(self, *, key, voice, modes, call, switch, db=None, connect=None, encoder_factory=None,
                 cfg=None, clock=time.monotonic, wall_ms=None, rng=None):
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
        self._turn = None                              # the turn the next response.created belongs to
        self._turns = {}                               # response id -> turn
        self._speech_end = None
        self._next_kind = "speech"
        self.ctx = {}                                  # last /realtime/context answer
        self._ctx_at = None
        self._seen_sent, self._seen_at = None, None    # last 【此刻看到】 line sent, and when
        self.watch = FaceView(self.cfg.away_s, recent_s=self.cfg.face_recent_s)
        self._greet = None                             # pending greeting: {at, kind: owner / tap, pick}
        self._last_tap = None
        self._last_greet = None                        # any owner greeting (long, short, tap on the owner)
        self._last_long = None                         # the last long named owner greeting
        self._said = collections.deque(maxlen=self.cfg.greet_said_keep)   # its texts: memory only, never logged
        self._quiet_until = 0.0                        # no input / greeting right after hello finished speaking
        self._appear = None                            # (name, at): appeared before mind's owner names were known
        self._bg = set()
        self._status_at = -1e9
        self._cap_logged = False
        self.stats = collections.Counter()
        self.on_transcript = None                      # offline tests only: (who, text without emoji); never logged
        self._local_end = None                         # when the ear's own VAD last said speech ended
        self._heard_turn = None                        # the last speech turn (its transcript may come late)

    # ---- conditions ----
    def link(self):
        return self.modes._links.get(self.modes._mode_conn) if self.modes is not None else None

    def in_crowd(self):
        return self.modes is not None and self.modes.robot_mode == "crowd"

    def armed(self):
        """Everything but the robot link: the switch, the key, the voice, crowd."""
        return bool(self.key and self.voice) and self.switch.on and self.in_crowd()

    def want(self):
        return self.armed() and self.link() is not None

    def active(self):
        return self.state != "closed"

    def output_busy(self):
        link = self.link()
        st = self._stream
        return bool((st and not st["reported"]) or self._resp is not None or (link is not None and link.tts_active))

    def busy(self):
        return self._sounding or self._was_speaking or self.output_busy() or self.clock() < self._quiet_until

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
        if sounding or self.output_busy() or now < self._quiet_until:
            self._preroll.clear()
            self._preroll_bytes, self._send_until = 0, 0.0
            return
        if speaking:                                   # the tail must outlast the server VAD's silence
            self._send_until = now + max(self.cfg.tail_s, self.cfg.silence_ms / 1000 + 0.6)
        if self.cfg.gate == "all" or now < self._send_until:
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

    def on_vision(self, msg):
        """Every robot M12 event (VisionLink). Owner appears after away_s unseen in crowd -> a greeting."""
        if not isinstance(msg, dict):
            return
        now = self.clock()
        name = self.watch.event(msg, now)
        if name is None or not self.armed():
            return
        owners = (self.ctx.get("owner") or {}).get("face_names")
        if owners is None:
            self._appear = (name, now)                 # decided when mind's owner names arrive
        elif name in owners:
            self._queue_greet(now)

    def _queue_greet(self, now):
        if self._last_long is None or now - self._last_long >= self.cfg.long_greet_s:
            size = "long"
        elif self._last_greet is None or now - self._last_greet >= self.cfg.short_greet_min_s:
            size = "short"
        else:
            return
        if self._greet is None:
            self._greet = {"at": now, "kind": "owner", "pick": None, "size": size}
            self.stats["greet_queued"] += 1
        if self.state == "closed" and self.want():
            self._open(now, "owner")

    def tap(self):
        """A tap on hello's home screen in crowd (MouthLink sees the robot's mode_report with input tap): look at the
        M12 faces of the last face_recent_s and greet: a known person by name, else the largest face by its apparent
        gender / age group, else say nobody is in view. Never interrupts: it waits until hello is quiet. -> True if
        queued."""
        now = self.clock()
        if not self.armed() or (self._last_tap is not None and now - self._last_tap < self.cfg.tap_cooldown_s):
            return False
        self._last_tap = now
        pick = tap_pick(self.watch.recent(now))
        self._greet = {"at": now, "kind": "tap", "pick": pick}          # a tap replaces a waiting owner greeting
        self.stats["taps"] += 1
        log(f"realtime: tap greet: {pick['kind']}", flush=True)
        if self.state == "closed" and self.want():
            self._open(now, "tap")
        return True

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

    def streaming(self, link):
        """Our stream is on `link` and the robot has not finished it (asset pushes wait for it)."""
        st = self._stream
        return st is not None and st["link"] is link and not st["reported"] and not st["closed"]

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
        if not self.in_crowd():
            self._need_speech, self._greet, self._appear = False, None, None
        if not self.want():
            if self.state != "closed":
                self.close("switch_off" if not self.switch.on else "left_crowd" if not self.in_crowd() else "robot_gone")
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
                self._deliver_greet(now)
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
        self._task = self.spawn(self._session(self.session_no, why))
        self._write_status(now)

    def close(self, why, need_speech=False):
        if self.state == "closed":
            return
        now = self.clock()
        log(f"realtime: session {self.session_no} closing ({why}) after {now - self.opened_at:.0f}s", flush=True)
        self._spans.append((self.opened_at, now))
        self.state, self.opened_at = "closed", None
        self._need_speech = need_speech
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
                self._retry_at = self.clock() + self.cfg.retry_s
                self.close("connect_failed")
                return
            self._ws = ws
            await self._take_context(ctx_fut)
            await ws.send(json.dumps({"type": "session.update", "session": session_config(
                self.voice, self._instructions(), self.cfg.silence_ms, self.cfg.vad_threshold)}))
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
            self._send_soon({"type": "session.update", "session": {"instructions": self._instructions()}})

    def _seen(self):
        owner = self.ctx.get("owner") or {}
        return seen_text(self.watch.recent(self.clock()), self.ctx.get("names") or {},
                         owner.get("face_names") or [], owner.get("call") or "主人")

    def _instructions(self):
        seen = self._seen()
        self._seen_sent, self._seen_at = seen, self.clock()
        return instructions(self.ctx.get("text"), seen)

    def _push_seen(self, now):
        """Re-send the instructions when what hello sees changed (≤ once per seen_min_s), so 'can you see me' is
        answered from the live M12 view, not the 60 s context."""
        if self._ws is None or not self._ready.is_set() or now - (self._seen_at or 0) < self.cfg.seen_min_s:
            return
        if self._seen() != self._seen_sent:
            self._send_soon({"type": "session.update", "session": {"instructions": self._instructions()}})

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
        if t == "session.updated":
            if not self._ready.is_set():
                self._ready.set()
                self.state = "open"
                if self.watch.present((self.ctx.get("owner") or {}).get("face_names") or [], now):
                    self._queue_greet(now)
        elif t == "error":
            err = e.get("error") or {}
            msg = str(err.get("message") or "")[:160].replace(self.voice or "\0", "<voice>")
            log(f"realtime: session {n} error {str(err.get('code') or err.get('type') or '')[:40]}: {msg}", flush=True)
            self.stats["errors"] += 1
            if not self._ready.is_set():               # refused at setup (bad voice, key): stop and retry later
                self._retry_at = now + self.cfg.retry_s
                self.close("refused")
        elif t == "input_audio_buffer.speech_started":
            self._last_speech = now
        elif t == "input_audio_buffer.speech_stopped":
            self._speech_end = now
            recent = self._local_end if self._local_end is not None and now - self._local_end < 3 else None
            self._turn = self._heard_turn = {"kind": "speech", "speech_end": now, "heard_chars": None,
                                             "local_end": recent}
        elif t == "conversation.item.input_audio_transcription.completed":
            text = strip_emoji(e.get("transcript"))
            if self._heard_turn is not None:
                self._heard_turn["heard_chars"] = len(text)
                self._heard_turn["heard"] = text[:300]
            if self.on_transcript:
                self.on_transcript("heard", text)
        elif t == "response.created":
            rid = str((e.get("response") or {}).get("id") or f"r{now}")
            self._resp = rid
            turn = self._turn if self._turn is not None and self._next_kind == "speech" else \
                {"kind": self._next_kind, "speech_end": None, "heard_chars": None}
            self._next_kind, self._turn = "speech", None
            turn.update({"rid": rid, "session": n, "response_at": now, "first_delta": None, "sound_at": None,
                         "reply_chars": 0, "audio_ms": 0, "usage": None, "at": self.wall_ms()})
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
            if rid not in self._dropped:
                self._out_put(("pcm", rid, pcm))
        elif t == "response.audio_transcript.done":
            turn = self._turns.get(self._resp)
            text = strip_emoji(e.get("transcript"))
            if turn is not None:
                turn["reply_chars"] = len(text)
                turn["reply"] = text[:400]
                if turn.get("kind") == "greet" and text:
                    self._said.append(text[:80])
            if self.on_transcript:
                self.on_transcript("reply", text)
        elif t == "response.done":
            resp = e.get("response") or {}
            rid = str(resp.get("id") or self._resp)
            turn = self._turns.get(rid)
            if turn is not None:
                turn["usage"], turn["status"] = resp.get("usage"), resp.get("status")
            self._resp = None
            if rid in self._dropped or turn is None or not turn["audio_ms"]:
                self._finish_turn(rid, "no_audio" if rid not in self._dropped else "yielded")
            else:
                self._out_put(("end", rid, b""))

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

    # ---- greeting ----
    def _deliver_greet(self, now):
        g = self._greet
        owner = self.ctx.get("owner") or {}
        call = owner.get("call")
        if g is None or not self._ready.is_set() or self.busy() or self.clock() < self._quiet_until:
            return
        if now - g["at"] > self.cfg.greet_max_age_s:
            return                                     # step() drops it
        if g["kind"] == "owner":
            if not call:
                return
            if g.get("size") == "short":
                pick = self.rng.randrange(len(SHORT_WORDS) + 1)       # a word, or (last slot) two beeps
                if pick == len(SHORT_WORDS):
                    self._greet = None
                    self._last_greet = now
                    self.stats["greeted"] += 1
                    self._beep(now)
                    return
                text = greet_text(call, "short", word=SHORT_WORDS[pick])
            else:
                text = greet_text(call, "long", tuple(self._said))
        else:
            text = tap_text(g["pick"], self.ctx.get("names") or {}, owner.get("face_names") or [], call or "主人")
        self._greet = None
        if g["kind"] == "owner" or (g["pick"]["kind"] == "known" and g["pick"]["face"]["name"] in
                                    (owner.get("face_names") or [])):
            self._last_greet = now
        if g["kind"] == "owner" and g.get("size", "long") == "long":
            self._last_long = now
        self._next_kind = ("greet" if g.get("size", "long") == "long" else "greet_short") if g["kind"] == "owner" \
            else "tap"
        self.stats["greeted"] += 1
        log(f"realtime: session {self.session_no} {self._next_kind} greeting", flush=True)
        self._send_soon({"type": "conversation.item.create", "item": {"type": "message", "role": "user",
                                                                      "content": [{"type": "input_text", "text": text}]}})
        self._send_soon({"type": "response.create"})

    def _beep(self, now):
        """A short hello as two local chirps: played like a reply (same /mouth stream path), no model, no cost."""
        rid = f"beep{next(self._beeps)}"
        pcm = BEEP                                     # owner: "都差不多啊，随便用一个吧" — one fixed beep (sample 1)
        self._turns[rid] = {"kind": "greet_beep", "speech_end": None, "heard_chars": None, "rid": rid,
                            "session": self.session_no, "response_at": now, "first_delta": now, "sound_at": None,
                            "reply_chars": 0, "audio_ms": len(pcm) * 1000 // (SR_OUT * 2), "usage": None,
                            "at": self.wall_ms()}
        log(f"realtime: session {self.session_no} greet_short beep", flush=True)
        self._out_put(("pcm", rid, pcm))
        self._out_put(("end", rid, b""))

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
        log(f"realtime: turn {turn.get('session')}/{turn.get('kind')} {state}: ear speech end->audio "
            f"{timings['ear_speech_end_to_first_audio_ms']} ms (server VAD end->audio "
            f"{timings['speech_end_to_first_audio_ms']} ms), ->robot sound {timings['ear_speech_end_to_robot_sound_ms']} ms, "
            f"out {turn.get('audio_ms')} ms, {doc['cost_cny']:.4f} CNY", flush=True)
        if self.db is not None:
            try:
                self.db.realtime_turn(doc)
            except Exception:                          # noqa: BLE001 - a record never costs the session
                pass

    def _write_status(self, now):
        self._status_at = now
        if self.db is None:
            return
        try:
            self.db.switch_status("realtime", {
                "running": [f"rt{self.session_no}"] if self.state != "closed" else [], "state": self.state,
                "available": bool(self.key and self.voice), "on": self.switch.on,
                "hour_used_s": int(self.hour_used(now)), "turns": self.stats["turns"],
                "cost_cny": round(self.stats["cost_micro_cny"] / 1e6, 4), "at": self.wall_ms()})
        except Exception:                              # noqa: BLE001
            pass

    # ---- tasks ----
    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self._bg.add(task)
        task.add_done_callback(self._done)
        return task

    def _done(self, task):
        self._bg.discard(task)
        if not task.cancelled() and task.exception() is not None:
            e = task.exception()
            log("realtime: task failed\n" + "".join(traceback.format_exception(type(e), e, e.__traceback__)).rstrip(),
                flush=True)

    async def shutdown(self):
        self.close("shutdown")
        for t in list(self._bg):
            t.cancel()
        await asyncio.gather(*list(self._bg), return_exceptions=True)
        self._pool.shutdown(wait=False, cancel_futures=True)
