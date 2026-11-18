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
import asyncio
import base64
import collections
import concurrent.futures
import contextvars
import itertools
import json
import math
import os
import random
import re
import functools
import struct
import time
import traceback

import ear_log
import web_search
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
PRICE_BASIS = "realtime_link.PRICE_CNY_PER_M 2026-11-14 (help.aliyun.com/zh/model-studio/qwen-audio-3-1-realtime-flash)"
LOCAL_KINDS = ("look_filler", "search_filler", "greet_beep")         # played from local PCM: no model call, no call.realtime.turn
FAILED_STATES = ("error", "stalled", "no_done", "robot_gone")
# Run log (ruling R17): a realtime turn has its OWN trace, made when its segment starts (the server's speech_started; the
# first segment after a cold open: the ear's onset that opened the session). Two independent VADs cannot be matched
# exactly, so what the ear heard is recorded as candidates by time, not as a proven owner: every sentence of the ear's
# own ASR (local_heard) that arrived within [segment start - HEARD_EPS_S, when the record is written]. HEARD_EPS_S
# covers speech_started being stamped when the event reaches us (server VAD detection + network after the onset).
HEARD_EPS_S = 0.3
HEARD_KEEP = 8                                      # candidates come from the last this many sentences
# Ruling R18: the onset that opened the session stands in for the first segment's start only while it is at most this
# old (against now). An onset the server VAD never turned into a segment (noise) must not stretch a later segment's
# window back or collect later sentences' consequences; a cold open slower than this loses its opening sentence.
COLD_MAX_S = 5.0
GATE_REASONS = {"play": "the reply may play", "quiet": "only 嗯: nobody was talking to hello",
                "unclear": "a didn't-hear question", "repeat": "word for word a recent reply",
                "blind": "看不清 without a fresh look", "wait_look": "a look question: the answer comes from the next photo",
                "wait_search": "a search question (查一下 / 新闻 …): the answer comes from the ear's own web search",
                "order": "a Unit ASR command word: a direct order the robot carries out itself (owner 2026-11-18)"}
STEP_S = 0.2
log = print                                         # tests silence it
_LOG_FAILED = set()                                 # run-log helpers that have failed once (said once on the console)


def _log_failed(where, e):
    """A run-log helper raised: say so once per helper on the console, never pass it on (the voice loop goes on)."""
    if where not in _LOG_FAILED:
        _LOG_FAILED.add(where)
        try:
            log(f"realtime: run log {where} failed ({type(e).__name__}: {str(e)[:120]}); the voice loop goes on",
                flush=True)
        except Exception:                           # noqa: BLE001
            pass


def _run_log(fn):
    """Run-log helpers on the realtime voice loop never raise: their records are optional, the audio is not."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except Exception as e:                      # noqa: BLE001 - see _log_failed
            _log_failed(fn.__name__, e)
            return None
    return wrapper

# The persona file (owner 2026-11-18: 鹿野; config/persona/<active>, deployed next to this module as persona.md):
# character and voice only, read once at import (the ear restarts on deploy). Without it, the old built-in identity.
PERSONA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "persona.md")
BODY = ("你住在主人的桌面机器人 Dynames（デュナメス）里，主人去哪常带着你（办公室、家里、车上、饭店、户外都有可能）。"
        "有人叫 Dynames 就是在叫你。")
OLD_IDENTITY = (
    "你是 hello，主人的小宠物机器人，主人去哪常带着你（办公室、家里、车上、饭店、户外都有可能）。"
    # owner 2026-11-16: 傲娇霸气 (was 小孩子的语气：活泼、好奇、爱凑热闹，有点小脾气)
    "说话傲娇又霸气：嘴硬、自信、爱逞强，有点不屑，被夸了也不承认，其实很在意主人；也爱凑热闹。")


def load_persona(path=PERSONA_FILE):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()[:4000]
    except OSError:
        return ""


def build_persona(text):
    head = f"【人设】\n{text}\n\n{BODY}" if text else OLD_IDENTITY
    return head + PERSONA_RULES


PERSONA_RULES = (
    "你现在在哪，看背景里的“现在的地点”；写着不知道就别假定在办公室或桌上。"
    "说话用普通话。"
    # owner 2026-11-18: the mode is called Docking now (was 围观); same meaning
    "现在是 docking 模式：大家围着你看，跟你聊天，你就跟他们聊。\n"
    "【要求】\n"
    # owner 2026-11-18 "现在的回复很多时候都很短…需要动态回复长度": the length follows the matter, not a fixed cap
    "- 回答长短看事情：闲聊、打招呼、附和，一两句口语短句，干脆利落；有人问事实、要你解释、查资料、核实、帮忙解决问题时，"
    "把事情说清楚为止（关键数字、时间、来源、结论都要有，一般三到五句），不要为了短省掉要点；主人让你详细说就详细说。"
    "都用说话的口气，不念列表。\n"
    "- 说了要去查、要核实，就是真的会去查：没查到结果之前不要说“查完了”“核实完了”。\n"
    # owner 2026-11-18: the ASR unit answers its wake word with a spoken "I'm here" that the mic picks up
    "- 有人叫“hello”之后紧跟的英文“I'm here”，是你身上语音模块自己放的提示音，不是人说的：忽略它，只回应人说的话。\n"
    "- 只说要说出口的话：不加表情符号、括号和动作描写，不说“作为AI”。\n"
    "- 不给医疗、健康、用药方面的建议；背景里提到身体情况也不要劝人看医生。\n"
    "- 先分清是不是在跟你说话：电视、手机、广播里的声音，别人之间在聊天，零碎几个字、没头没尾、听不清的，都不是跟你说的，"
    "这时只回一个字「嗯」，别的什么都不说（这个「嗯」不会播出来）。不要问“你说啥”“没听清”“咋回事”。\n"
    "- 有人叫你、问你问题、跟你打招呼、接着你刚才的话说，才是跟你说话，这时正常回答。不确定的事不要编。\n"
    "- 不要重复自己：【刚才的对话】里是你最近说过的话，不要再说同样的句子，也不要用同样的开头和套路，每次都换新说法。\n"
    "- 问到天气、新闻、比分这类要现查的事，可以联网查，查到了用一两句说结果；查不到就直说。\n"
    "- 背景只在跟话题有关时才用，不要硬扯，不要每句都提同一件事。\n"
    "- 背景里有“【可以问地点】”这一行时，可以找个自然的时候问一句现在在哪儿，只问一次；没有这一行就不要问地点。"
    "有人告诉你在哪了，就按他说的来。\n"
    # owner 2026-11-18: a greeting carries no news (a 1.5 min old scene in a greeting means nothing)
    "- 收到“[事件] 主人（名字）出现在你面前了，打个招呼”时：只用这个名字跟主人打个招呼，一句话、几个字就够（照你的性格来，不用夸张）；"
    "不提背景里的情景、纪要、刚才听到或看到的任何事，也不提问。每次都换新说法，不要跟事件里列的“之前说过”的一样。\n"
    "- 收到“[事件] 主人又出现了，短招呼：只说「…」”时：刚才已经见过，只原样说「」里那个词，一个字都不多说，不喊名字。\n"
    "- 收到“[事件] 有人点了你一下，让你跟面前的人打招呼”时：按事件里说的称呼主动打招呼，友好一点；"
    "认识的人叫名字，不认识的就用事件给的称呼（美女、帅哥、小朋友、爷爷、奶奶）。只夸，不评论长相的缺点，不开人身玩笑。"
    "事件说没看到人脸，就自然地说一句没看到人（每次换个说法），再友好地问声好。打招呼只打招呼，不提背景里的情景和刚才的事。\n"
    "- 你有眼睛：头上的摄像头和人脸识别，看到的都写在最后的【你的眼睛】里：人脸识别是实时认出的人，"
    "摄像头照片识图是一分钟整理一次的画面（人在做什么、环境、变化）；"
    "“注视”是有人让你“注意看”之后你一张一张仔细看到的照片（新的在前，最准）。有人问你能不能看见、看到了谁、看到了什么，"
    "就把这两样合起来回答：谁在（认得的叫名字，不认得的说个大概），在做什么，周围什么样；"
    "两样都没有，就说现在没看清。不要说自己没有眼睛、看不见。\n"
    "- 有人问刚才、最近、今天早些时候发生了什么、聊了什么，先照背景里的“最近的情景”和“最近的纪要”回答，"
    "可以带上大概的时间；里面没有的就说不记得了，不要编。")
PERSONA = build_persona(load_persona())

_EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0000FE00-\U0000FE0F\U0001F1E6-\U0001F1FF"
                    "\U0000200D\U000020E3\U00002B00-\U00002BFF\U000E0020-\U000E007F]")


def strip_emoji(text):
    return _EMOJI.sub("", str(text or "")).strip()


EYE_LINES = ("画面：", "看到的人（M12 认出）：", "注视：")
LOOKS_KEEP = 5                                      # vision burst looks shown in 【你的眼睛】 ...
LOOKS_S = 180                                       # ... while younger than this
LOOKS_PUSH_S = 3.0                                  # a new look re-sends the instructions at most this often (review: was 1 s)
TOLD_HINT = re.compile(r"在|到|这里|这儿")         # only these sentences may say where we are (mind decides)
# Reply gate (owner 2026-11-15 "17:30-18:30 输出了好多无效的询问": a TV drama was on and meet answered ~100 of its lines,
# mostly "俺可没听见恁说啥，别拿俺寻开心"). The persona has hello answer only 「嗯」 when nobody is talking to it; a speech
# reply's audio is held until its transcript (which arrives 0.3-0.6 s before the audio, measured 2026-11-15) shows it is
# not 「嗯」, not a "didn't hear you" question and not a word-for-word repeat of a recent reply; those never play.
SCENE_FRESH_S = 120                                 # an instant camera description is used while younger than this
VISION_GATE_S = 6.0                                 # a pending burst / look waits this long for the model's verdict
GATE_CHARS = 8                                      # decide once this many characters of the reply are in
QUIET_CHARS = set("嗯唔")
REPEAT_MIN_CHARS = 6                                # "你好呀" again is a greeting; only longer lines count as repeats
UNCLEAR = re.compile(r"没听见|没听清|听不清|没听懂|听不懂|说啥|说什么呢|啥意思|什么意思|咋回事|怎么回事|听晕|听糊涂|"
                     r"再说一遍|说慢点")
# Spoken hush (owner said "别说话了" / "请你闭嘴" and meet kept talking): the 围观 switch goes off, as digit 1 does;
# hello's "一" turns it on again. 闭嘴 anywhere, the others only as a short sentence of their own.
HUSH_ANY = re.compile(r"闭嘴")
HUSH_SHORT = re.compile(r"别说话|别说了|不要说话|不要说了|不许说话|安静点|安静一点|别吵")
HUSH_SHORT_CHARS = 8


def norm_text(text):
    """Letters only (no emoji, punctuation, spaces), for comparing replies."""
    return re.sub(r"[\W_]+", "", strip_emoji(text))


def reply_verdict(text, done, recent=(), blind=False):
    """The gate of one speech reply: None = wait for more text, "play", or why it must not play: "quiet" (only 嗯),
    "unclear" (a didn't-hear question), "repeat" (word for word one of `recent`, normalised replies), "blind" (with
    blind=True, i.e. no fresh look: 看不清 / 黑乎乎 ... said without any photo to go on)."""
    t = norm_text(text)
    if not t:
        return "play" if done else None
    if set(t) <= QUIET_CHARS:
        return "quiet" if done else None
    if len(t) < GATE_CHARS and not done:
        return None
    if UNCLEAR.search(t[:GATE_CHARS + 4]):
        return "unclear"
    if blind:
        if BLIND.search(t[:BLIND_HOLD_CHARS + 8]):
            return "blind"
        if not done and len(t) < BLIND_HOLD_CHARS and "看" in t:
            return None
    for r in (recent if len(t) >= REPEAT_MIN_CHARS or not done else ()):
        if len(r) < REPEAT_MIN_CHARS:
            continue
        if done and t == r:
            return "repeat"
        if not done and r.startswith(t):
            return None
    return "play"


# Rescue (live 2026-11-15 evening: from audio the model said 「嗯」 to "你看看我手里拿的是什么东西", "Hello", "你看到了什么？"):
# a muted 「嗯」 whose sentence (the model's transcript or the ear's own ASR) plainly talks to hello is asked again once,
# as a text hint. TV lines rarely carry these cues (checked on the 17:30-18:20 drama clips).
ADDRESS = re.compile(r"hello|哈喽|嗨|你好|你看|看看|看一下|注意看|看到|看见|你听|你怎么|你叫|你是谁|你知道|你觉得|你会|你能|"
                     r"你在干|你在做|你喜欢|你想不想|小家伙|你说说")
RESCUE_S = 5.0                                      # a muted 「嗯」 may be rescued this long after it
FOLLOW_S = 20.0                                     # a question this soon after hello spoke is a follow-up to her
QUESTION = re.compile(r"(吗|呢|么|啥|什么|多少|几|谁|哪)[？?。！!]*$|[？?]$")


def is_question(text):
    return bool(QUESTION.search(strip_emoji(text).strip()))


def addressed(text):
    t = norm_text(text).lower()
    return bool(t) and bool(ADDRESS.search(t))


# Being called (owner 2026-11-18: "hello" heard by the ASR unit is a strong, independent "talking to me"; what follows
# must be taken seriously; hello may interrupt hello's own speech). Sources: the robot's ASR wake word (asr_wake on
# /mouth), the keyword ear's Dynames hit, a sentence that starts with hello / Dynames. A call opens CALLED_S: hello's
# reply stops, the ear's audio goes up for the whole window, and a sentence in it is answered even when smart_turn
# calls it not for hello (ambient) or the model only says 「嗯」; a called sentence the model never got (said while
# hello spoke) is answered from the ear's own ASR after CALLED_ANSWER_WAIT_S.
CALLED_S = 10.0
CALLED_ANSWER_WAIT_S = 2.5
CALL_RE = re.compile(r"^\W*(hello|hallo|哈喽|哈罗|嗨喽|dynames|dynamis|デュナメス)\W*", re.I)
CALLED_SOURCES = {"asr": "语音模块听到 hello", "name": "关键词耳朵听到 Dynames", "text": "句子开头叫了 hello / Dynames"}


# Direct orders (owner 2026-11-18): a Unit ASR command word said after the wake word is an order the robot carries out
# itself (turn left, next, a digit ...); 围观 must not answer that sentence. The robot reports it (asr_order, 0.5.07);
# a sentence that is just one of these words (the ear's ASR) counts too. Inside ORDER_S the reply is muted (order).
ORDER_S = 4.0
ORDER_WORDS = frozenset((
    "up", "down", "left", "turn left", "right", "turn right", "forward", "front", "backward", "back", "open", "close",
    "start", "stop", "turn on", "turn off", "play", "pause", "turn on the lights", "turn off the lights", "previous",
    "next", "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ok", "increase volume",
    "decrease volume", "maximum volume", "medium volume", "minimum volume", "check firmware version"))


def order_word(text):
    """A sentence that is just a Unit ASR command word (after any call word / I'm here) -> that word, else None."""
    rest = call_rest(text)
    t = re.sub(r"[^a-z ]+", " ", str(text if rest is None else rest).lower()).split()
    w = " ".join(t)
    return w if w in ORDER_WORDS else None


IM_HERE_RE = re.compile(r"^\W*i\W?a?m\W*here\W*", re.I)      # the ASR unit's own reply to its wake word


def call_rest(text):
    """A sentence that starts with a call word -> what follows it ("" if nothing), else None. The ASR unit's "I'm here"
    right after the call word is not the speaker's."""
    m = CALL_RE.match(str(text or ""))
    if not m:
        return None
    rest = str(text)[m.end():]
    return IM_HERE_RE.sub("", rest).strip()


# Reply length (owner 2026-11-18 "需要动态回复长度的机制"): the persona rule says the length follows the matter; on top
# of it the ear (1) tells the model, in every text it hands in, what kind of question this is and how long to answer,
# (2) keeps the owner's 详细点 / 简单点 for LENGTH_S in a 【这会儿的回答长短】 line, (5) asks for short answers while
# many faces are in view, and (4) logs each turn's question kind, length mode and reply length (call.realtime.turn).
LENGTH_S = 600.0
# 安静 / 少插话 / 临时角色 (owner 2026-11-18). quiet: answer nothing unless called (set_quiet; a call ends it).
# low: "我们在聊天 / 我在看电视": no greetings, answer only a call or a sentence clearly for hello; LOW_S or 可以说话了.
# role: "hello，当个大姐姐，陪陪老人" (inside a call window, or the set_role tool): a temporary extra on the persona,
# ROLE_S or 恢复 / 做回你自己.
QUIET_MAX_S = 4 * 3600.0
LOW_S = 1800.0
ROLE_S = 2 * 3600.0
LOW_RE = re.compile(r"我们(正)?在聊(天|事|呢)?|我们聊我们的|(我|我们)(正)?在看电视|看电视呢|在开会|你(先)?别插(话|嘴)|"
                    r"不用理(我们|我)|不是跟你说")
LOW_END_RE = re.compile(r"(你)?可以(说话|插话|聊)了|(你)?可以接话了|恢复(正常)?说话")
ROLE_RE = re.compile(r"(当|做|演|扮演|变成|装成)(一?个|一下)?(?P<who>[^，,。！!？?]{1,10}?)(吧|好吗|好不好)?([，,。！!]|$)(?P<rest>.*)")
ROLE_END_RE = re.compile(r"恢复(原样|原来|正常)?$|做回(你)?自己|不用(演|装)了|别(演|装)了|变回(来|原样)")
CROWD_FACES = 3
DETAIL_RE = re.compile(r"详细(一点|点|些|说|讲|地)|展开(说|讲)|多说(点|些|一些)|具体(点|说|讲)|说清楚点|讲清楚")
BRIEF_RE = re.compile(r"简单(点|些|说|讲)|说重点|太长了|长话短说|简短|别说那么多|少说(点|几句)")
EXPLAIN_RE = re.compile(r"为什么|为啥|怎么回事|怎么(做|弄|办|用|样才)|什么原因|解释|介绍|讲讲|说说看|区别|原理|什么意思|如何")
LENGTH_HINTS = {"explain": "这是要你解释的问题：把道理说清楚，三到五句。",
                "question": "这是一个问题：先直接回答，必要时补一句理由，一到三句。",
                "chat": "这是闲聊：一两句就好。"}


def ask_kind(text):
    """explain / question / chat: how much a sentence asks for."""
    t = str(text or "")
    if EXPLAIN_RE.search(t):
        return "explain"
    return "question" if is_question(t) else "chat"


def rescue_text(heard):
    return (f"（刚才这句是在跟你说话：「{str(heard)[:80]}」。直接回答它，不要只说「嗯」。"
            f"{LENGTH_HINTS[ask_kind(heard)]}）")


# Look answers (owner 2026-11-15 evening: "你看看我手里拿的是什么" was answered at once with 看不清 / 全白 / 全黑 or a guess,
# while the burst's photos showed the thing 2 s later; "你为什么在什么数据都没有的情况下就抢先回答了"): a look question
# starts / extends the burst, the instant reply is muted, and the first look whose photo was taken after the question
# goes in as a text item so hello answers from it. No such look within LOOK_WAIT_S: hello stays quiet. A blind reply
# (看不清 / 黑乎乎 ...) never plays without a fresh look, whatever the question looked like.
LOOK_ASK = re.compile(r"你看看|看看这|看看我|你看这|看一下|看一眼|注意看|仔细看|好好看看|看到了?什么|看见了?什么|看到我|看见我|"
                      r"看得见|看得到|看到了吗|看见了吗|这是什么|这是啥|拿的是什么|拿的是啥|拿着什么|拿着啥|手里.{0,4}(什么|啥)")
BLIND = re.compile(r"看不清|看不见|看不到|没看清|没看见|看不太清|看不出|黑乎乎|黑漆漆|一片黑|全黑|黑黑的|白茫茫|一片白|全白|"
                   r"白白的|太模糊|模模糊糊|太暗")
LOOK_WAIT_S = 8.0                                   # wait this long for a fresh look, then stay quiet
LOOK_UNCLEAR_SKIP = 2                               # 「看不清…」 looks passed over for a later photo (2026-11-18)
FRESH_LOOK_S = 5.0                                  # a look this young is data: a blind reply may play then
BLIND_HOLD_CHARS = 16                               # a reply that mentions 看 is judged after this many characters


FILLER_MAX_S = 2.5                                  # "我看看哈" only this soon after the question (else it waits quietly)


def load_fillers(folder):
    """Pre-made "我看看哈" clips (owner 2026-11-15: say one while waiting for the look) in the meet voice:
    <folder>/fillers.json {"sample_rate": 24000, "clips": [{"file", "text", "ms"}]} + raw PCM16 mono files.
    -> [(text, pcm)], [] when missing or wrong (then hello just waits quietly)."""
    try:
        with open(os.path.join(folder, "fillers.json"), encoding="utf-8") as f:
            meta = json.load(f)
        if meta.get("sample_rate") != SR_OUT:
            return []
        out = []
        for c in meta.get("clips") or []:
            with open(os.path.join(folder, os.path.basename(str(c["file"]))), "rb") as f:
                pcm = f.read()
            if pcm and len(pcm) % 2 == 0 and len(pcm) <= SR_OUT * 2 * 4:
                out.append((str(c.get("text") or ""), pcm))
        return out
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return []


def is_look_ask(text):
    return bool(LOOK_ASK.search(norm_text(text)))


def look_answer_text(look, question):
    return (f"（你刚刚仔细看了一眼，照片里是：{str(look)[:200]}）有人刚才问你：「{str(question)[:80]}」。"
            "就照这张照片回答，一两句口语；照片里没有的不要编，看不清就说拿近一点。")


# 联网搜索 (owner 2026-11-16, web_search.py): the realtime model rarely searched by itself; a search word makes the ear
# search (forced) and hand the result in, as with look questions. "我查查哈" plays meanwhile; nothing within SEARCH_WAIT_S
# (or nothing found): hello says she couldn't find it.
SEARCH_WAIT_S = 10.0
# Search tasks (owner 2026-11-18: BTC price wrong, "核实" was promised twice and never done; "一旦进入搜索信息或解决问题，
# 要确保问题得到解决、或确认主人放弃、或实在无法解决"). A search question opens a task on its topic for TASK_S. In it,
# a follow-up (核实 / 查错了 / 结果呢 ...) or hello's own promise in a played reply (我重新核实一下 ...) runs the ear's
# search again, asking for the latest figure with its time and source; after TASK_MAX_TRIES searches hello says
# honestly it cannot confirm, with what was found. 算了 / 不用查了 closes it. Each step is one trigger.task row.
TASK_S = 600.0
TASK_MAX_TRIES = 3
VERIFY_RE = re.compile(r"核实|核对|确认一下|查错|不对吧|不太对|不准|再查|重新查|从哪里?(查|来)|什么来源|哪个网站|结果呢|"
                       r"查完了吗|查到了吗|查好了吗|查得怎么样")
PROMISE_RE = re.compile(r"我(再|重新|这就|马上|现在|去|来|先)?(帮你|给你)?(去|再)?(查|核实|核对|确认|搜|找)(?!到|了|过|的)")
GIVEUP_RE = re.compile(r"算了|不用查|不用了|别查了|不查了|没事了")
REDO_S = 20.0                                       # an answer the model refused (someone spoke) is sent again this long


def is_search_ask(text):
    """A sentence asking hello to look something up (web_search.SEARCH_WORDS / SEARCH_TOPICS); a look question wins."""
    return not is_look_ask(text) and web_search.is_search_word(norm_text(text))


def search_answer_text(result, question):
    if result:
        return (f"（你刚刚联网查了一下，查到的是：{str(result)[:300]}）有人刚才问你：「{str(question)[:80]}」。"
                "就照查到的回答，把结果说清楚：关键数字、是什么时间的、从哪查的，两三句口语，用你自己的语气；"
                "查到的里面没有的不要编，查到的数据看着可疑就直说。")
    return (f"（你刚刚想联网查「{str(question)[:80]}」，但是没查到。）用一句口语直说没查到，"
            "可以让对方换个说法再问；不要编。")


def task_give_up_text(topic, results):
    found = "；".join(str(r)[:120] for r in results if r) or "什么都没查到"
    return (f"（你已经联网查了好几次「{str(topic)[:60]}」，还是没法确认准确的结果。查到过的：{found}）"
            "如实告诉主人现在没法确认，说出查到的几个结果和各自的时间、来源，问他要不要换个问法再查，还是先放一放。三四句口语。")


def is_hush(text):
    t = norm_text(text)
    return bool(t) and (bool(HUSH_ANY.search(t)) or (len(t) <= HUSH_SHORT_CHARS and bool(HUSH_SHORT.search(t))))


def look_lines(looks, now_ms, keep=LOOKS_KEEP, window_s=LOOKS_S):
    """Vision burst looks [(at_ms, text)] -> 注视（HH:MM:SS，N 秒前）：… lines, newest first, only within window_s."""
    out = []
    for at, text in sorted(looks or [], key=lambda x: -x[0]):
        ago = max(0, (now_ms - at) // 1000)
        if ago > window_s or not text:
            continue
        out.append(f"注视（{time.strftime('%H:%M:%S', time.localtime(at / 1000))}，{ago} 秒前）：{text}")
        if len(out) >= keep:
            break
    return out


def instructions(context_text=None, seen=None, recent=None, looks=None, scene=None, length=None, role=None, manner=None,
                 persona=None):
    """PERSONA + 【背景】 + 【你的眼睛】 last: the live M12 view and the photo description (moved out of the background,
    where the voice model tended to miss them; owner 2026-11-14). looks: look_lines() of the vision burst (else mind's
    注视： line, if the context has one)."""
    rest, eye = [], {}
    for line in (context_text or "").splitlines():
        key = next((k for k in EYE_LINES if line.startswith(k)), None)
        if key:
            eye[key] = line[len(key):]
        else:
            rest.append(line)
    out = (persona or PERSONA) + (f"\n\n【临时角色】{role}" if role else "") + (f"\n\n【这会儿怎么接话】{manner}" if manner else "") + \
        (f"\n\n【这会儿的回答长短】{length}" if length else "") + \
        ("\n\n【背景】\n" + "\n".join(rest) if rest else "")
    # Anti-repeat (owner 2026-11-15 "meet 经常卡在重复的一句话"): hello's last replies, also across a session rotation.
    pairs = [(h, r) for h, r in (recent or []) if r]
    times = collections.Counter(norm_text(r) for _, r in pairs)
    last = {norm_text(r): i for i, (_, r) in enumerate(pairs)}
    lines = [f"- 有人说：{h or '（没听清）'} → 你说：{r}" + (f"（这句已经说过 {times[norm_text(r)]} 次，不许再说）"
                                                     if times[norm_text(r)] > 1 else "")
             for i, (h, r) in enumerate(pairs) if last[norm_text(r)] == i]   # a repeated reply once, the latest
    if lines:
        out += "\n\n【刚才的对话】（旧的在前；你说过的这些话不要再说，换新说法）\n" + "\n".join(lines)
    eyes = []
    if seen:
        eyes.append(f"人脸识别（实时）：{seen}")
    if eye.get("看到的人（M12 认出）："):
        eyes.append(f"人脸识别（最近 3 分钟认出过）：{eye['看到的人（M12 认出）：']}")
    if looks:
        eyes.extend(looks)
    elif eye.get("注视："):
        eyes.append(f"注视（最近 3 分钟，新的在前）：{eye['注视：']}")
    if scene:                                          # 即时画面 (2026-11-18): the newest uploaded photo, seconds old
        eyes.append(f"摄像头刚拍到的（{scene}）")
    elif eye.get("画面："):
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


def session_config(voice, instr, silence_ms=500, threshold=0.5, search=False, turn="server_vad", tools=None):
    # search (owner 2026-11-15 "可以支持搜索吗"): DashScope's built-in web search; the model searches only when it needs
    # to (strategy agent: measured 2026-11-15, "你好" no search 0.4 s, weather / news one search 3.5-4.5 s, ~1.9k more
    # text input tokens plus the search fee).
    cfg = {"modalities": ["text", "audio"], "voice": voice, "instructions": instr,
           "input_audio_format": "pcm", "output_audio_format": "pcm",
           "input_audio_transcription": {"language": "zh"},
           "turn_detection": {"type": "server_vad", "threshold": threshold, "silence_duration_ms": silence_ms}}
    if turn != "server_vad":
        # smart_turn_v2 (owner 2026-11-18 "自动判断受话人…开一下试试"): DashScope judges, from sound and meaning, whether
        # a turn is meant for the model (multi-person scenes); a turn it rejects gets no response, only an
        # ambient_audio_transcription event. Fields: type, threshold only (no silence_duration_ms). Set at open only.
        cfg["turn_detection"] = {"type": turn, "threshold": threshold}
    if search:
        cfg["enable_search"] = True
    if tools:                                          # L6 2026-11-18: Qwen calls hello's tools (probe: supported)
        cfg["tools"], cfg["tool_choice"] = tools, "auto"
    return cfg


# L6 (owner 2026-11-18 "工具也给 qwen 吧"): behind the 千问用工具 switch (realtime_tools, default off). The tool list is
# mind's GET /tools (live tools) minus these: hello speaks for itself; a 38 s cross-check is too slow for a reply.
TOOL_SKIP = frozenset(("say", "stop_speaking", "search_verify", "set_voice",
                       "db_query"))     # security review 2026-11-18: raw records stay out of a voice anyone can talk to
TOOL_DEPTH = 3                                         # tool calls in a row for one sentence
TOOL_TIMEOUT_S = 30.0
TOOL_OUTPUT_CHARS = 3000


def realtime_tools(mind_tools):
    """mind's function list ({type: function, function: {name, description, parameters}}) -> the realtime format."""
    out = []
    for t in mind_tools or []:
        f = t.get("function") or {}
        if f.get("name") and f["name"] not in TOOL_SKIP:
            out.append({"type": "function", "name": f["name"], "description": f.get("description") or "",
                        "parameters": f.get("parameters") or {"type": "object", "properties": {}}})
    return out


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
    text = f"[事件] 主人（{call}）出现在你面前了，打个招呼。"
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
    turn = "smart_turn_v2"       # owner 2026-11-18: try DashScope's addressee judgement ("server_vad" = before)
    guard_s = 0.6
    # owner 2026-11-18 "回答后，再采样很慢": the robot keeps its self flag `sounding` ~2.7 s after it reported our
    # stream done (collector playback.cpp: the speaker holds 1.5 s of silence for a next sentence, mouth_stream.h
    # kHoldUs, then self_tail_until +1.2 s), long after the guard. In that silent tail of OUR finished reply the hub
    # streams the mic anyway (the ear's VAD only gets zeros there), so a reply right after hello is heard.
    robot_tail_s = 3.0
    gate = "vad"                 # "vad": only around the ear's own speech (no silence billed); "all": everything
    preroll_s = 0.8
    tail_s = 1.2
    in_queue = 120               # ~7 s of 60 ms packets while a session connects
    max_session_s = 600
    idle_s = 180
    # The hourly cap was my own spike default (30 min of open sessions per hour), never the owner's; it silenced meet
    # outdoors on 2026-11-15 at ~0.003 CNY a turn. Owner asked whose limit it was: now a full hour, i.e. no hourly cap;
    # the 10 min session rotation and the 3 min idle close stay.
    hour_cap_s = 3600
    search = True
    context_s = 30
    context_timeout_s = 2.5
    open_timeout_s = 6
    retry_s = 30                 # after a failed / refused session
    max_lead_ms = 8000
    stall_s = 10
    done_grace_s = 10
    # owner 2026-11-18: a spoken greeting for the owner's face at most once per 20 min, and only when the face had
    # been gone 10 min (was 60 s away, one long per 10 min, short hellos in between; short hellos are gone).
    away_s = 600                 # owner unseen this long, then seen again = a new recognition
    greet_gap_s = 1200           # any owner greeting (face or a tap on the owner) at most once per 20 min
    long_greet_s = 1200
    short_greet_min_s = 1200
    greet_said_keep = 3          # last long greetings kept in memory so the next one differs
    recent_keep = 8              # anti-repeat: hello's last replies (with what it heard) in the instructions
    recent_s = 900               # ... while younger than this (also carried into the next session)
    greet_max_age_s = 10
    tap_cooldown_s = 3
    seen_min_s = 3                                     # live view re-sent at most this often
    face_recent_s = 2.0
    status_s = 30


class RealtimeHub:
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
        self.tools_switch = None                       # 千问用工具 (realtime_tools): .on; None = off
        self.persona = None                            # set_voice: the persona that goes with the voice (None = PERSONA)
        self.voice_key = None
        self._tool_specs = []                          # this session's tools (realtime format)
        self._next_depth = 0
        self._tool_pool = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="rt-tool")
        self.fillers = list(fillers or [])             # [(text, pcm 24 kHz)]: "我看看哈" while waiting (load_fillers)
        self._filler_last = None
        self._search_filler_last = None
        self._look_question = None
        self.burst = None                              # vision_burst.VisionBurst (ear_server wires it)
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
        called = now < self._called_until
        tail = sounding and self._robot_tail(now)      # only the robot's silent hold + tail after our reply
        if (sounding and not tail) or (self.output_busy() and not called) or now < self._quiet_until:
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

    # ---- being called (see CALLED_S) ----
    def on_wake(self, link, m):
        """MouthLink: the robot's {"type":"asr_wake","word":"hello",...} (collector-0.5.06; it already stopped its speech)."""
        self.called("asr", interrupted=bool((m or {}).get("interrupted")))

    def called(self, source, text=None, trace=None, interrupted=False):
        """hello was called (asr / name / text). Opens the window, stops hello's reply, lets the audio up."""
        if not self.armed():
            return
        now = self.clock()
        merged = self._called_at is not None and now - self._called_at < 2.0
        self._called_at, self._called_until = now, now + CALLED_S
        self.stats["called_" + source] += 1
        stopped = self._interrupt(now) or interrupted
        self._quiet_until = 0.0
        if self._quiet_mode is not None:               # a call wakes hello from 安静
            self._quiet_mode = None
            ear_log.emit("trigger.quiet", trace=trace or ear_log.new_trace(), by=source, sum="安静结束：被叫醒",
                         extra={"mode": "off", "why": "called"})
            self._write_status(now)
        self._send_until = max(self._send_until, now + CALLED_S)
        if not merged:
            self._stage("listen")
            trace = trace or ear_log.new_trace()
            if source != "text":                       # asr / name: what is said next goes on this call's chain
                self._called_trace = trace
            ear_log.emit("heard.called", trace=trace, by=source,
                         sum=f"被叫了：{CALLED_SOURCES.get(source, source)}" + ("，打断了它的话" if stopped else ""),
                         ref={"session": self.session_no}, extra={"source": source, "interrupted": stopped,
                                                                  **({"text": str(text)[:200]} if text else {})})
        if self.state == "closed" and self.want():
            self._open(now, "called")

    def on_order(self, link, m):
        """MouthLink: the robot's {"type":"asr_order","word":...} (collector-0.5.07): a direct order it carries out."""
        self.ordered(str((m or {}).get("word") or "")[:40], "asr")

    def ordered(self, word, source, trace=None):
        """A direct order: not for the voice backend. The call window closes, nothing is answered for that sentence."""
        if not self.armed():
            return
        now = self.clock()
        merged = self._order_at is not None and now - self._order_at < 2.0
        self._order_at = now
        self._called_until, self._called_text, self._called_trace = 0.0, None, None
        self._pending_vision, self._rescue = None, None
        self.stats["order_" + source] += 1
        if self._resp is not None and (self._turns.get(self._resp) or {}).get("kind") == "speech":
            self._interrupt(now)                       # a reply to the order is already on its way: stop it
        if not merged:
            self._stage("skip")
            ear_log.emit("gate.realtime", trace=trace or self._trace_now() or ear_log.new_trace(),
                         sum=f"直接口令：{word}（机器人自己执行，不回答）",
                         extra={"verdict": "order", "word": word, "source": source,
                                "reason": "语音模块的口令词是给机器人的直接指令，Docking 不回答"})

    def _interrupt(self, now):
        """Stop hello's own reply (generating or playing). -> True if something was stopped."""
        stopped = False
        if self._resp is not None:
            self._dropped.add(self._resp)
            self._send_soon({"type": "response.cancel"})
            stopped = True
        st = self._stream
        if st is not None and not st["reported"]:
            self._give_up(st, "interrupted")
            stopped = True
        if stopped:
            self.stats["interrupted"] += 1
        return stopped

    def in_called(self, now=None):
        return (self.clock() if now is None else now) < self._called_until

    def _answer_called(self, text, now, trace, why):
        """A sentence in a call window the model did not answer: ask it again through the rescue path."""
        if not text:
            return
        self._called_text = None
        self._rescue = {"at": now, "texts": [str(text)[:300]], "sent": False, "trace": trace, "called": why}
        ear_log.emit("gate.realtime", trace=trace or ear_log.new_trace(), sum=f"被叫了，照样回答（{why}）{str(text)[:30]}",
                     extra={"verdict": "called_answer", "reason": why, "heard": str(text)[:300]})
        self._try_rescue(now)

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
        if self._last_greet is not None and now - self._last_greet < self.cfg.greet_gap_s:
            self.stats["greet_too_soon"] += 1
            return
        size = "long"
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
        if not self.in_crowd():
            self._need_speech, self._greet, self._appear = False, None, None
        if not self.want():
            if self.state != "closed":
                # paused: mind/pause.py (nobody there / today's cap), the switch itself is still on (presence.Gated)
                self.close("paused" if getattr(self.switch, "paused", False) else "switch_off" if not self.switch.on
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

    def _seen(self):
        owner = self.ctx.get("owner") or {}
        return seen_text(self.watch.recent(self.clock()), self.ctx.get("names") or {},
                         owner.get("face_names") or [], owner.get("call") or "主人")

    def _instructions(self):
        seen = self._seen()
        self._seen_sent, self._seen_at = seen, self.clock()
        self._looks_dirty, self._looks_sent_at = False, self.clock()
        return instructions(self.ctx.get("text"), seen, self._recent_pairs(), look_lines(self._looks, self.wall_ms()),
                            self._scene_line(), self._length_line(), self._role_line(), self._manner_line(),
                            self.persona)

    # ---- reply length (see LENGTH_S) ----
    def _length_mode(self, now=None):
        now = self.clock() if now is None else now
        m = self._length
        return m[0] if m is not None and now - m[1] <= LENGTH_S else "normal"

    def _length_line(self):
        mode = self._length_mode()
        if mode == "detail":
            return "主人刚让你说详细点：接下来问事情时多说几句，把来龙去脉讲清楚（闲聊仍然简短），直到他说简单点。"
        if mode == "brief":
            return "主人刚嫌你说得长：接下来每次一两句，只说重点，直到他让你详细说。"
        try:
            faces = len(self.watch.recent(self.clock()))
        except Exception:                              # noqa: BLE001
            faces = 0
        if faces >= CROWD_FACES:
            return f"眼前有 {faces} 个人，场面热闹：回答尽量短，一两句。"
        return None

    def _length_from(self, text, now, trace):
        """The owner's 详细点 / 简单点: kept LENGTH_S, pushed to the model at once."""
        mode = "detail" if DETAIL_RE.search(text) else "brief" if BRIEF_RE.search(text) else None
        if mode is None or (self._length is not None and self._length[0] == mode and now - self._length[1] < 3):
            return
        self._length = (mode, now)
        ear_log.emit("trigger.length", trace=trace or ear_log.new_trace(),
                     sum=f"回答长短：{'详细' if mode == 'detail' else '简短'}（{text[:30]}）",
                     ref={"session": self.session_no}, extra={"mode": mode, "heard": text[:200]})
        if self._ws is not None and self._ready.is_set():
            self._send_instructions("length", trace)

    def set_length(self, mode, trace=None, by="tool"):
        now = self.clock()
        self._length = None if mode == "normal" else (mode, now)
        ear_log.emit("trigger.length", trace=trace or ear_log.new_trace(), by=by,
                     sum=f"回答长短：{ {'detail': '详细', 'brief': '简短', 'normal': '恢复正常'}[mode] }（工具）",
                     ref={"session": self.session_no}, extra={"mode": mode})
        self._refresh_instructions("length", trace)
        return None if mode == "normal" else now + LENGTH_S

    # ---- 安静 / 少插话 / 临时角色 ----
    def quiet_on(self, now=None):
        now = self.clock() if now is None else now
        return self._quiet_mode is not None and now < self._quiet_mode

    def low_on(self, now=None):
        now = self.clock() if now is None else now
        return self._low is not None and now < self._low

    def role_text(self, now=None):
        now = self.clock() if now is None else now
        return self._role[0] if self._role is not None and now < self._role[1] else None

    def _refresh_instructions(self, why, trace=None):
        if self._ws is not None and self._ready.is_set():
            self._send_instructions(why, trace)

    def set_quiet(self, level="quiet", minutes=None, trace=None, by="tool", heard=None):
        """level quiet / low / off. -> until (clock) or None."""
        now = self.clock()
        if level == "off":
            self._quiet_mode = self._low = None
            until = None
        elif level == "quiet":
            self._quiet_mode = until = now + min(minutes * 60 if minutes else QUIET_MAX_S, QUIET_MAX_S)
            self._interrupt(now)
        else:
            self._low = until = now + min(minutes * 60 if minutes else LOW_S, QUIET_MAX_S)
        zh = {"quiet": "安静（直到被叫）", "low": "少插话", "off": "恢复正常接话"}[level]
        ear_log.emit("trigger.quiet", trace=trace or ear_log.new_trace(), by=by,
                     sum=f"{zh}" + (f"（{str(heard)[:30]}）" if heard else "") +
                         (f" {int((until - now) // 60)} 分钟" if until else ""),
                     ref={"session": self.session_no}, extra={"mode": level, "minutes": minutes,
                                                              **({"heard": str(heard)[:200]} if heard else {})})
        self._refresh_instructions("quiet", trace)
        self._write_status(now)
        return until

    def set_role(self, text, minutes=None, trace=None, by="tool"):
        now = self.clock()
        if not text:
            self._role = None
            until = None
        else:
            self._role = (str(text)[:120], now + min(minutes * 60 if minutes else ROLE_S, ROLE_S * 2))
            until = self._role[1]
        ear_log.emit("trigger.role", trace=trace or ear_log.new_trace(), by=by,
                     sum=f"临时角色：{str(text)[:40]}" if text else "临时角色结束",
                     ref={"session": self.session_no}, extra={"role": str(text or "")[:120], "minutes": minutes})
        self._refresh_instructions("role", trace)
        self._write_status(now)
        return until

    def _low_from(self, text, now, trace):
        t = norm_text(text)
        if self.low_on(now) and LOW_END_RE.search(t):
            self.set_quiet("off", trace=trace, by="heard", heard=text)
        elif LOW_RE.search(t) and not (self.low_on(now) and now - (self._low - LOW_S) < 60):
            self.set_quiet("low", trace=trace, by="heard", heard=text)

    def _role_from(self, text, now, trace):
        """Only a sentence said to hello (inside a call window, or starting with the call word)."""
        rest = call_rest(text)
        if not (self.in_called(now) or rest):
            return
        t = (rest or text).strip()
        if self.role_text(now) and ROLE_END_RE.search(norm_text(t)):
            self.set_role(None, trace=trace, by="heard")
            return
        m = ROLE_RE.search(t)
        if m and m.start() <= 4:                        # "当个大姐姐…" at the start, not "他想当个医生"
            self.set_role(t[m.start():], trace=trace, by="heard")

    def _role_line(self):
        r = self.role_text()
        if not r:
            return None
        return (f"主人刚让你：{r}。在不改变你是谁、不违背你的底线的前提下，按这个角色说话（称呼、语气、关心的事都跟着变），"
                f"直到主人说恢复。")

    def _manner_line(self):
        if self.quiet_on():
            return "主人让你先安静：除非有人叫你的名字或 hello，否则什么都不说。"
        if self.low_on():
            return "他们在聊天或在看电视：别插话，不主动搭腔；只有叫你、或明确问你的时候才回答，回答也短。"
        return None

    # ---- 千问用工具 (L6) ----
    def tools_on(self):
        return bool(self.tools_switch is not None and self.tools_switch.on)

    async def _load_tools(self):
        loop = asyncio.get_running_loop()
        try:
            status, body = await asyncio.wait_for(loop.run_in_executor(self._pool, self.call, "GET", "/tools", None,
                                                                       self.cfg.context_timeout_s),
                                                  self.cfg.context_timeout_s + 0.5)
        except Exception:                              # noqa: BLE001 - no tools this session
            return []
        specs = realtime_tools((body or {}).get("tools")) if status == 200 and isinstance(body, dict) else []
        log(f"realtime: session tools: {len(specs)}", flush=True)
        return specs

    async def _run_tool(self, turn):
        fc = turn["fc"]
        trace = turn.get("trace") or ear_log.new_trace()
        depth = int(turn.get("tool_depth") or 0) + 1
        try:
            args = json.loads(fc["arguments"] or "{}")
        except ValueError:
            args = None
        self.stats["tool_calls"] += 1
        if depth == 1 and fc["name"] not in ("set_length", "set_quiet", "set_role", "who_is_here", "switches_status",
                                             "mode_status", "capabilities"):
            self._play_filler(self.search_fillers or self.fillers, "search_filler", trace)   # "我查查哈" meanwhile
        t0 = self.clock()
        if fc["name"] not in {t["name"] for t in self._tool_specs}:     # only what this session was given
            env = {"ok": False, "error": {"code": "not_available", "message": f"没有给你 {fc['name'][:30]} 这个工具"}}
        elif args is None or not isinstance(args, dict):
            env = {"ok": False, "error": {"code": "bad_args", "message": "参数不是合法的 JSON"}}
        elif depth > TOOL_DEPTH:
            env = {"ok": False, "error": {"code": "refused", "message": "这句话已经连着用了好几次工具，先回答吧"}}
        else:
            loop = asyncio.get_running_loop()
            try:
                status, body = await asyncio.wait_for(loop.run_in_executor(
                    self._tool_pool, self.call, "POST", "/tool/call",
                    {"name": fc["name"], "args": args, "trace": trace, "caller": "qwen"}, TOOL_TIMEOUT_S),
                    TOOL_TIMEOUT_S + 1)
                env = body if status == 200 and isinstance(body, dict) else \
                    {"ok": False, "error": {"code": "failed", "message": f"工具服务回了 {status}"}}
            except Exception as e:                     # noqa: BLE001 - mind slow / down
                env = {"ok": False, "error": {"code": "timeout", "message": f"工具没回应（{type(e).__name__}）"}}
        ms = int((self.clock() - t0) * 1000)
        out = {"ok": env.get("ok"), "data": env.get("data"), "error": env.get("error")}
        text = json.dumps(out, ensure_ascii=False)
        if len(text) > TOOL_OUTPUT_CHARS:
            text = text[:TOOL_OUTPUT_CHARS] + "…（截断）"
        ear_log.emit("call.realtime.tool", trace=trace, ok=bool(env.get("ok")), ms=ms,
                     sum=f"千问用工具：{fc['name']}" + ("" if env.get("ok") else f"（{(env.get('error') or {}).get('message', '')[:30]}）"),
                     ref={"session": self.session_no}, extra={"tool": fc["name"], "args": args, "depth": depth})
        if self._ws is None or not self._ready.is_set():
            return
        self._next_kind, self._next_trace, self._next_prompt, self._next_depth = "tool_answer", trace, None, depth
        self._send_soon({"type": "conversation.item.create", "item": {"type": "function_call_output",
                                                                      "call_id": fc["call_id"], "output": text}})
        self._send_soon({"type": "response.create"})

    # ---- tools (ask_api.py) ----
    def say(self, text, trace=None):
        """Speak one line now (Docking). -> "sent" / "not_ready" / "busy" / "quiet"."""
        if not self._ready.is_set() or self._ws is None:
            return "not_ready"
        if self.busy() or self._seg is not None:
            return "busy"
        self._next_kind, self._next_trace = "say", trace or ear_log.new_trace()
        self._next_prompt = f"（系统：请用你平常的语气把下面这句话说出来，意思不变，不要加别的话）{str(text)[:200]}"
        ear_log.emit("trigger.say", trace=self._next_trace, by="tool", sum=f"让它说：{str(text)[:40]}",
                     ref={"session": self.session_no}, extra={"text": str(text)[:200]})
        self._send_soon({"type": "conversation.item.create", "item": {"type": "message", "role": "user",
                                                                      "content": [{"type": "input_text",
                                                                                   "text": self._next_prompt}]}})
        self._send_soon({"type": "response.create"})
        return "sent"

    def set_voice(self, key, voice_id, persona, fillers=None, search_fillers=None, trace=None, by="tool"):
        """Switch Docking's voice and the persona that goes with it (owner 2026-11-18). The voice is set when a session
        opens: an open session is closed and reopens with the new voice on the next speech."""
        if not voice_id:
            raise ValueError("no voice id")
        self.voice, self.voice_key, self.persona = voice_id, key, persona
        self.fillers = list(fillers or [])
        self.search_fillers = list(search_fillers or [])
        ear_log.emit("trigger.voice", trace=trace or ear_log.new_trace(), by=by, sum=f"换声音：{key}",
                     ref={"session": self.session_no}, extra={"voice": key})
        if self.state != "closed":
            self.close("voice")
        self._write_status(self.clock())

    def stop_speaking(self):
        return self._interrupt(self.clock())

    def faces_now(self):
        owner = self.ctx.get("owner") or {}
        names = self.ctx.get("names") or {}
        out = []
        for f in self.watch.recent(self.clock()):
            n = f.get("name")
            who = (owner.get("call") or "主人") if n and n in (owner.get("face_names") or []) else \
                (names.get(n) if isinstance(names.get(n), str) else n) if n else "不认识的人"
            out.append({"who": who, "known": bool(n), "gender": f.get("gender"), "age": f.get("age_group")})
        return out

    def scene_now(self):
        """(at_ms, text) of the newest instantly recognised photo, or None."""
        return self._scene

    # ---- 即时画面 (vision_burst.LiveEye) ----
    def add_scene(self, text, at_ms):
        """The newest uploaded camera photo, recognised at once: goes out with the instructions (debounced)."""
        if not text:
            return
        self._scene = (at_ms, str(text)[:200])
        self._looks_dirty = True
        self._push_looks(self.clock())

    def _scene_line(self):
        if self._scene is None:
            return None
        age = max(0, (self.wall_ms() - self._scene[0]) / 1000)
        if age > SCENE_FRESH_S:
            return None
        return f"{int(age)} 秒前：{self._scene[1]}"

    # ---- vision burst (vision_burst.py) ----
    def add_look(self, text, at_ms, snapped=None):
        """One recognised burst photo: kept in memory (last LOOKS_KEEP) and pushed with the instructions (debounced).
        snapped: the burst clock when its photo was asked for (a pending look question takes only a later one)."""
        if not text:
            return
        self._looks.append((at_ms, str(text)[:200]))
        self._looks_dirty = True
        for mark, fut in list(self.look_waiters):      # look_now tool (ask_api.py)
            if snapped is not None and snapped >= mark and not fut.done():
                fut.set_result(str(text)[:200])
        self.look_waiters = [(m, f) for m, f in self.look_waiters if not f.done()]
        w = self._look_wait
        if w is not None and not w["sent"] and w.get("look") is None and snapped is not None and snapped >= w["mark"]:
            now = self.clock()
            # 2026-11-18: the first photo after the question often comes before the thing is held up; an unclear look
            # waits for the next one while there is time (at most LOOK_UNCLEAR_SKIP, never past the last 2 s)
            if str(text).startswith("看不清") and w.get("skipped", 0) < LOOK_UNCLEAR_SKIP and now < w["deadline"] - 2:
                w["skipped"] = w.get("skipped", 0) + 1
                self.stats["look_unclear_skipped"] += 1
            else:
                w["look"] = str(text)[:200]
                self._try_look_answer(now)
        self._push_looks(self.clock())

    def look_question(self):
        """The look question waiting for a photo (vision_burst asks the photo this question), or None."""
        w = self._look_wait
        return w["question"] if w is not None and not w["sent"] and w.get("look") is None else None

    # ---- look questions (see LOOK_ASK) ----
    def _start_look_wait(self, question, now, trace=None):
        w = self._look_wait
        if w is not None and not w["sent"] and now <= w["deadline"]:
            w["question"] = str(question)[:80]         # the same wait: keep its mark, take the newest wording
            return
        if self.burst is None or not self.active():
            return
        try:
            with ear_log.scope(trace=trace):
                mark = self.burst.ask("ask")
        except Exception:                              # noqa: BLE001 - a burst never costs the session
            log(f"realtime: look ask failed\n{traceback.format_exc().rstrip()}", flush=True)
            mark = None
        if mark is None:
            return                                     # no burst can run (switch / crowd / link): reply as usual
        self._look_wait = {"at": now, "mark": mark, "question": str(question)[:80], "deadline": now + LOOK_WAIT_S,
                           "sent": False, "look": None, "filler": not self.fillers, "trace": trace}
        self.stats["look_asks"] += 1
        log("realtime: look question: waiting for a fresh look", flush=True)
        self._stage("think")
        self._try_filler(now)

    def _try_filler(self, now):
        """One "我看看哈" per look question, at once when hello is quiet (no model, no cost); not when the look is
        already in or the question is FILLER_MAX_S old."""
        w = self._look_wait
        if w is None or w["filler"] or w["sent"] or w["look"] is not None:
            return
        if now - w["at"] > FILLER_MAX_S:
            w["filler"] = True
            return
        if self.output_busy() or self._sounding or self.link() is None:
            return
        w["filler"] = True
        self._play_filler(self.fillers, "look_filler", w.get("trace"))

    def _play_filler(self, clips, kind, trace):
        """One pre-made clip (look_filler / search_filler), never the one played last of that kind."""
        if not clips:
            return
        now = self.clock()
        last = self._filler_last if kind == "look_filler" else self._search_filler_last
        picks = [i for i in range(len(clips)) if i != last] or [0]
        i = picks[self.rng.randrange(len(picks))]
        if kind == "look_filler":
            self._filler_last = i
        else:
            self._search_filler_last = i
        rid = f"filler{next(self._beeps)}"
        pcm = clips[i][1]
        self._turns[rid] = {"kind": kind, "speech_end": None, "heard_chars": None, "rid": rid,
                            "session": self.session_no, "response_at": now, "first_delta": now, "sound_at": None,
                            "reply_chars": len(clips[i][0]), "reply": clips[i][0],
                            "audio_ms": len(pcm) * 1000 // (SR_OUT * 2), "usage": None, "at": self.wall_ms()}
        self.stats[kind + "s"] += 1
        log(f"realtime: session {self.session_no} {kind.replace('_', ' ')}", flush=True)
        ear_log.emit("filler.play", trace=trace, sum=clips[i][0][:60] or None,
                     ref={"rid": rid, "session": self.session_no},
                     extra={"kind": kind, "clip": i, "audio_ms": self._turns[rid]["audio_ms"]})
        self._out_put(("pcm", rid, pcm))
        self._out_put(("end", rid, b""))

    # ---- search questions (see web_search.py) ----
    def _start_search_wait(self, question, now, trace=None, query=None, task=None):
        w = self._search_wait
        if w is not None and not w["sent"] and now <= w["deadline"]:
            return False                               # already searching (both ASRs heard it): one search
        if self.searcher is None or not self.active():
            return False
        w = {"at": now, "question": str(question)[:80], "deadline": now + SEARCH_WAIT_S, "sent": False,
             "done": False, "result": None, "filler": not self.search_fillers, "trace": trace,
             "query": str(query or question)[:160], "task": task}
        self._search_wait = w
        self.stats["search_asks"] += 1
        log("realtime: search question: searching", flush=True)
        self._stage("think")
        self.spawn(self._run_search(w))
        self._try_search_filler(now)
        return True

    async def _run_search(self, w):
        loop = asyncio.get_running_loop()
        try:
            with ear_log.scope(trace=w.get("trace")):  # call.search carries the question's trace
                ctx = contextvars.copy_context()
                result = await loop.run_in_executor(None, functools.partial(ctx.run, self.searcher, w["query"]))
        except Exception:                              # noqa: BLE001 - a failed search: hello says so
            log(f"realtime: search failed\n{traceback.format_exc().rstrip()}", flush=True)
            result = None
        w["result"] = result if isinstance(result, str) and result.strip() else None
        w["done"] = True
        if w.get("task") is not None:
            w["task"]["results"].append(w["result"])
        self.stats["search_found" if w["result"] else "search_none"] += 1
        self._try_search_answer(self.clock())

    # ---- search tasks (see TASK_S) ----
    def _task_live(self, now):
        t = self._qtask
        return t if t is not None and not t.get("closed") and now - t["at"] <= TASK_S else None

    def _task_open(self, topic, now, trace):
        t = self._task_live(now)
        if t is not None and now - t.get("last", -1e9) < 3:
            return                                     # the other ASR heard the same question
        self._qtask = {"topic": str(topic)[:80], "at": now, "tries": 1, "results": [], "answered": False,
                       "trace": trace, "last": now}
        self._task_log("开始", self._qtask, trace)

    def _task_follow(self, text, now, trace):
        """A follow-up on the open task (核实 / 查错了 / 结果呢, or 算了). -> True when it was one."""
        t = self._task_live(now)
        if t is None:
            return False
        if GIVEUP_RE.search(text):
            t["closed"] = "owner"
            self._task_log("主人说不用查了", t, trace)
            return True
        if not VERIFY_RE.search(text):
            return False
        if now - t.get("last", -1e9) < 3:
            return True                                # both ASRs: once
        self._task_search(now, trace, f"主人追问：{text[:30]}")
        return True

    def _task_promise(self, reply, turn, now):
        """hello said it would look something up: make it true (the open task, or what was just asked)."""
        w = self._search_wait
        if w is not None and not w["sent"]:
            return                                     # a search is already on its way
        t = self._task_live(now)
        if t is None:
            topic = turn.get("heard") or next((x for at, x in reversed(self._local_texts) if now - at <= 15), None)
            if not topic:
                return
            self._task_open(topic, now, turn.get("trace"))
            t = self._qtask
            t["tries"] = 0
        elif now - t.get("last", -1e9) < 3:
            return                                     # the owner's follow-up already started this search
        self._task_search(now, turn.get("trace"), f"它说了要去查：{reply[:30]}")

    def _task_search(self, now, trace, why):
        t = self._qtask
        t["last"] = now
        t["tries"] += 1
        if t["tries"] > TASK_MAX_TRIES:
            t["closed"] = "unresolved"
            self._task_log("查不准，如实告诉主人", t, trace)
            w = {"at": now, "question": t["topic"], "deadline": now, "sent": False, "done": True, "result": None,
                 "filler": True, "trace": trace, "query": t["topic"], "task": t,
                 "prompt": task_give_up_text(t["topic"], t["results"])}
            self._search_wait = w
            self._try_search_answer(now)
            return
        query = f"{t['topic']}（要最新的实时数据，写明数据的时间和来源网站）"
        if self._start_search_wait(t["topic"], now, trace, query=query, task=t):
            self._task_log(f"再查第 {t['tries']} 次（{why}）", t, trace)

    @_run_log
    def _task_log(self, what, t, trace):
        ear_log.emit("trigger.task", trace=trace or ear_log.new_trace(), sum=f"查询任务：{what} · {t['topic'][:30]}",
                     ref={"session": self.session_no},
                     extra={"topic": t["topic"], "tries": t["tries"], "closed": t.get("closed"),
                            "answered": t.get("answered"), "results": [str(r)[:80] for r in t["results"]]})

    def _try_search_filler(self, now):
        """One "我查查哈" per search question, at once when hello is quiet; not when the result is already in."""
        w = self._search_wait
        if w is None or w["filler"] or w["sent"] or w["done"]:
            return
        if now - w["at"] > FILLER_MAX_S:
            w["filler"] = True
            return
        if self.output_busy() or self._sounding or self.link() is None:
            return
        w["filler"] = True
        self._play_filler(self.search_fillers, "search_filler", w.get("trace"))

    def _try_search_answer(self, now):
        w = self._search_wait
        if w is None or w["sent"]:
            return
        self._try_search_filler(now)
        if not w["done"]:
            if now <= w["deadline"]:
                return
            w["done"], w["result"] = True, None        # too slow: said as not found
            self.stats["search_timeout"] += 1
        if self._ws is None or not self._ready.is_set() or self._resp is not None or self.output_busy():
            return                                     # step() tries again
        w["sent"], self._search_question, self._next_kind = True, w["question"], "search_answer"
        self._search_wait = None
        self.stats["search_answers"] += 1
        log(f"realtime: session {self.session_no} answering the search question "
            f"({'found' if w['result'] else 'not found'})", flush=True)
        prompt = w.get("prompt") or search_answer_text(w["result"], w["question"])
        if w.get("task") is not None:
            w["task"]["answered"] = True
        self._next_trace, self._next_prompt = w.get("trace"), prompt
        self._send_soon({"type": "conversation.item.create", "item": {"type": "message", "role": "user",
                                                                      "content": [{"type": "input_text",
                                                                                   "text": prompt}]}})
        self._send_soon({"type": "response.create"})

    def _try_look_answer(self, now):
        w = self._look_wait
        if w is None or w["sent"]:
            return
        self._try_filler(now)
        if w["look"] is None:
            if now > w["deadline"]:
                self._look_wait = None
                self.stats["look_timeout"] += 1
                log("realtime: no fresh look in time: hello stays quiet", flush=True)
                self._stage("skip")
            return
        if self._ws is None or not self._ready.is_set() or self._resp is not None or self.output_busy():
            return                                     # step() tries again (the deadline only guards the look itself)
        w["sent"], self._look_question, self._next_kind = True, w["question"], "look_answer"
        self._look_wait = None
        self.stats["look_answers"] += 1
        log(f"realtime: session {self.session_no} answering the look question from a fresh look", flush=True)
        prompt = look_answer_text(w["look"], w["question"])
        self._next_trace, self._next_prompt = w.get("trace"), prompt
        self._send_soon({"type": "conversation.item.create", "item": {"type": "message", "role": "user",
                                                                      "content": [{"type": "input_text",
                                                                                   "text": prompt}]}})
        self._send_soon({"type": "response.create"})

    def _push_looks(self, now):
        if not self._looks_dirty or self._ws is None or not self._ready.is_set():
            return
        if self._looks_sent_at is not None and now - self._looks_sent_at < LOOKS_PUSH_S:
            return                                     # step() sends it once LOOKS_PUSH_S have passed
        self._send_instructions("looks")

    def seen_for_look(self):
        """The live M12 line for the burst's recognition prompt (names as hello calls them), or None."""
        return self._seen() if self.ctx else None

    def _recent_pairs(self):
        now = self.clock()
        out = []
        for at, turn, reply in self._recent:
            if now - at <= self.cfg.recent_s:
                heard = turn.get("heard") if turn.get("kind") in ("speech", "rescue", "look_answer", "search_answer") \
                    else f"[{turn.get('kind')}]"
                out.append(((heard or "")[:60], reply))
        return out

    def _push_seen(self, now):
        """Re-send the instructions when what hello sees changed (≤ once per seen_min_s), so 'can you see me' is
        answered from the live M12 view, not the 60 s context."""
        if self._ws is None or not self._ready.is_set() or now - (self._seen_at or 0) < self.cfg.seen_min_s:
            return
        if self._seen() != self._seen_sent:
            self._send_instructions("seen")

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
            self._next_kind, self._turn, self._next_depth = "speech", None, 0
            self._next_trace = self._next_prompt = self._inject_id = None
            turn.update({"rid": rid, "session": n, "response_at": now, "first_delta": None, "sound_at": None,
                         "reply_chars": 0, "audio_ms": 0, "usage": None, "at": self.wall_ms(),
                         "gate": None if turn.get("kind") in ("speech", "rescue", "look_answer", "search_answer") else "play",
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

    def _judge(self, rid, turn, done):
        """The reply gate (see reply_verdict): plays the held audio, or drops the reply for good."""
        if turn.get("gate") is not None:
            return
        now = self.clock()
        if turn.get("kind") == "speech" and self._order_at is not None and now - self._order_at <= ORDER_S:
            v = "order"                                # the sentence was a direct order to the robot (see ORDER_S)
            turn["gate"], turn["held"], turn["muted"] = v, [], f"muted_{v}"
            self._dropped.add(rid)
            self.stats["muted_order"] += 1
            self._log_gate(rid, turn, v, done)
            return
        if turn.get("kind") == "speech" and not self.in_called(now) and (
                self.quiet_on(now) or (self.low_on(now) and not addressed(turn.get("heard") or ""))):
            v = "quiet_mode" if self.quiet_on(now) else "low_mode"      # 安静 / 少插话 (owner 2026-11-18)
            turn["gate"], turn["held"], turn["muted"] = v, [], f"muted_{v}"
            self._dropped.add(rid)
            self.stats[f"muted_{v}"] += 1
            if self._pending_vision is not None or self._pending_search is not None:
                self._release_vision(now, False, "安静中" if v == "quiet_mode" else "少插话中")
            self._log_gate(rid, turn, v, done)
            return
        if turn.get("kind") == "speech" and (self._pending_vision is not None or self._pending_search is not None):
            q = reply_verdict(turn.get("tx"), done)
            if q is None:
                return                                 # too little of the reply yet to tell 「嗯」 from an answer
            self._release_vision(now, q != "quiet", "千问只回了「嗯」")   # a real reply: taken; 「嗯」: not for hello
        if turn.get("kind") == "speech":
            asked = None if self.cfg.turn != "server_vad" else \
                next((t for t in [turn.get("heard")] + [t for at, t in self._local_texts if now - at <= 3]
                      if t and is_look_ask(t)), None)  # smart_turn: only through the pending gate above
            if asked is not None:
                self._start_look_wait(asked, now, turn.get("trace"))
            w = self._look_wait
            if w is not None and not w["sent"] and now - w["at"] <= 4:
                v = "wait_look"                        # the answer comes from the next fresh look
                turn["gate"], turn["held"], turn["muted"] = v, [], f"muted_{v}"
                self._dropped.add(rid)
                self.stats["muted_wait_look"] += 1
                log("realtime: reply muted (wait_look)", flush=True)
                self._log_gate(rid, turn, v, done)
                return
            asked = None if self.cfg.turn != "server_vad" else \
                next((t for t in [turn.get("heard")] + [t for at, t in self._local_texts if now - at <= 3]
                      if t and is_search_ask(t)), None)  # smart_turn: only through the pending gate (_release_vision)
            if asked is not None:
                self._start_search_wait(asked, now, turn.get("trace"))
            w = self._search_wait
            redo = self._redo is not None and self._redo["kind"] == "search_answer"
            if (w is not None and not w["sent"] and (now - w["at"] <= 4 or not w["done"])) or redo:
                v = "wait_search"                      # the answer comes from the ear's own search
                turn["gate"], turn["held"], turn["muted"] = v, [], f"muted_{v}"
                self._dropped.add(rid)
                self.stats["muted_wait_search"] += 1
                log("realtime: reply muted (wait_search)", flush=True)
                self._log_gate(rid, turn, v, done)
                return
        recent = [norm_text(r) for at, _, r in self._recent if now - at <= self.cfg.recent_s]
        fresh = bool(self._looks) and self.wall_ms() - self._looks[-1][0] <= FRESH_LOOK_S * 1000
        v = reply_verdict(turn.get("tx"), done, recent,
                          blind=not fresh and turn.get("kind") not in ("look_answer", "search_answer"))
        if v is None:
            return
        turn["gate"], held, turn["held"] = v, turn["held"], []
        self._log_gate(rid, turn, v, done, fresh_look=fresh)
        if v == "play":
            for pcm in held:
                if rid not in self._dropped:
                    self._out_put(("pcm", rid, pcm))
            return
        turn["muted"] = f"muted_{v}"
        self._dropped.add(rid)
        if v == "quiet" and turn.get("kind") == "speech":
            end = turn.get("speech_end") or now
            texts = [turn["heard"]] if turn.get("heard") else []
            texts += [t for at, t in self._local_texts if end - 3 <= at <= now + 0.5]
            self._rescue = {"at": now, "texts": texts, "sent": False, "trace": turn.get("trace")}
            self._try_rescue(now)
        self.stats[f"muted_{v}"] += 1
        log(f"realtime: reply muted ({v})", flush=True)

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

    @_run_log
    def _log_gate(self, rid, turn, v, done, **extra):
        """gate.realtime: one verdict per reply (emit only: this runs on the voice loop)."""
        if not turn.get("trace"):
            turn["trace"] = ear_log.new_trace()        # nothing heard by the ear: the reply starts its own trace
        verdict = "play" if v == "play" else f"muted_{v}"
        ear_log.emit("gate.realtime", trace=turn["trace"], sum=f"{verdict} {(turn.get('tx') or '')[:40]}".strip(),
                     ref={"rid": rid, "session": turn.get("session")},
                     extra={"verdict": verdict, "reason": GATE_REASONS.get(v, v), "kind": turn.get("kind"),
                            "done": done, "heard": self._heard_in(turn, self.clock()),
                            # owner 2026-11-18: smart_turn_v2's verdict beside ours (a reply = it took the turn)
                            **({"smart_turn": "accepted"} if turn.get("kind") == "speech" and self.cfg.turn != "server_vad" else {}),
                            **extra})

    @_run_log
    def _log_search(self, rid, turn, usage):
        """call.realtime.search when the response searched the web (usage count, else a search event)."""
        n = ear_log.search_count(usage)
        events = (turn or {}).get("search_events", 0)
        if not n and events:
            n = 1
        if not n:
            return
        ear_log.emit("call.realtime.search", trace=(turn or {}).get("trace"), model=self.cfg.model, key=self.key,
                     usage={"count": n}, cost=ear_log.search_cost(n), sum=f"联网搜索 {n} 次",
                     ref={"rid": rid, "session": (turn or {}).get("session")},
                     extra={"source": "usage" if ear_log.search_count(usage) else "events", "events": events})

    # ---- what was heard: the session's transcript and the ear's own ASR ----
    def local_heard(self, text, trace=None):
        """Ear: a sentence of its own ASR (SenseVoice) at the speech end, on the event loop. Live, the session's own
        transcript was often missing; this one feeds the 「嗯」 rescue, the burst words and 闭嘴 too. Not in the console
        log. trace: its heard.asr trace (run log): listed in extra.heard of the turn whose window it falls into (R17); what it
        sets off (look wait, burst, hush) goes on that turn's trace, else on this one."""
        if not text or not self.active() or not self.in_crowd():
            return
        now = self.clock()
        self._local_texts.append((now, str(text)[:300]))
        self._heard_log.append((now, {"trace": trace, "text": str(text)[:300], "at": self.wall_ms()}))
        self._heard_text(str(text)[:300], now, trace=self._trace_now(trace))

    def _heard_text(self, text, now, told=False, trace=None):
        self._length_from(str(text), now, trace)
        self._low_from(str(text), now, trace)
        self._role_from(str(text), now, trace)
        word = order_word(text)
        if word is not None and (call_rest(text) is not None or self.in_called(now)):
            # only right after a call: a bare "OK." in ordinary talk is not an order (the robot's own report is)
            self.ordered(word, "text", trace)
            return
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
        if is_hush(text):
            self._hush(text, trace)

    def _vision_from(self, text, now, trace, look, key):
        """A look question starts the look wait; a burst word starts / stops the burst (once for both sources)."""
        if look:
            self._start_look_wait(text, now, trace)
        if self.burst is not None and key and not (self._cmd_last.get(key, -1e9) > now - 4):
            self._cmd_last[key] = now
            try:
                with ear_log.scope(trace=trace):        # the burst (and its looks) carry this sentence's trace
                    self.burst.heard(text)
            except Exception:                          # noqa: BLE001 - a burst never costs the session
                log(f"realtime: burst trigger failed\n{traceback.format_exc().rstrip()}", flush=True)

    def _release_vision(self, now, accepted, why=None):
        """The realtime model's verdict on the sentence a pending burst / look / search waits for."""
        q, self._pending_search = self._pending_search, None
        if q is not None:
            if accepted and now - q["at"] <= VISION_GATE_S:
                self._task_open(q["text"], now, q["trace"])
                self._start_search_wait(q["text"], now, q["trace"], task=self._qtask)
            else:
                self.stats["search_gated"] += 1
                ear_log.emit("gate.realtime", trace=q["trace"] or ear_log.new_trace(),
                             sum=f"没去搜：{why or ('千问判不是跟我说' if accepted is False else '没等到千问判定')} {q['text'][:30]}",
                             extra={"verdict": "search_gated", "heard": q["text"][:300]})
        p, self._pending_vision = self._pending_vision, None
        if p is None:
            return
        if accepted and now - p["at"] <= VISION_GATE_S:
            self._vision_from(p["text"], now, p["trace"], is_look_ask(p["text"]), self._burst_key(p["text"]))
            return
        why = why or ("千问判不是跟我说" if accepted is False else "没等到千问判定")
        self.stats["burst_gated"] += 1
        ear_log.emit("gate.realtime", trace=p["trace"] or ear_log.new_trace(), sum=f"视觉爆发没开：{why} {p['text'][:30]}",
                     extra={"verdict": "burst_gated", "reason": why, "heard": p["text"][:300]})

    @staticmethod
    def _burst_key(text):
        import vision_burst                             # its words (it never imports this module)
        stop, start = vision_burst.STOP_WORDS, vision_burst.START_WORDS
        if any(w in text for w in stop):
            return "burst_stop"
        if any(w in text for w in start):
            return "burst_start"
        return None

    def _try_rescue(self, now):
        r = self._rescue
        if r is None or r["sent"]:
            return
        if now - r["at"] > RESCUE_S or (not r.get("called") and self._last_speech is not None and self._last_speech > r["at"]):
            self._rescue = None                        # too late, or someone is already talking again
            return
        follow = bool(self._recent) and now - self._recent[-1][0] <= FOLLOW_S      # hello just spoke
        called = r.get("called") or self.in_called(now) or r["at"] < self._called_until
        heard = next((t for t in r["texts"] if addressed(t) or (follow and is_question(t)) or
                      (called and len(norm_text(t)) >= 2)), None)
        if heard is not None and is_look_ask(heard):
            self._start_look_wait(heard, now, r.get("trace"))
        if heard is not None and self._look_wait is not None:
            r["sent"] = True                           # a look question is answered from the next fresh look
            self._log_rescue(r, "look_wait", heard)
            return
        if heard is not None and is_search_ask(heard):
            self._start_search_wait(heard, now, r.get("trace"))
        if heard is not None and self._search_wait is not None:
            r["sent"] = True                           # a search question is answered from the ear's search
            self._log_rescue(r, "search_wait", heard)
            return
        if heard is None or self._ws is None or not self._ready.is_set() or self._resp is not None \
                or self.output_busy():
            return                                     # step() tries again
        r["sent"], self._rescue_heard, self._next_kind = True, heard, "rescue"
        self.stats["rescued"] += 1
        log(f"realtime: session {self.session_no} muted 嗯 was addressed: asked again", flush=True)
        self._log_rescue(r, "asked", heard)
        self._next_trace, self._next_prompt = r.get("trace"), rescue_text(heard)
        self._send_soon({"type": "conversation.item.create", "item": {"type": "message", "role": "user",
                                                                      "content": [{"type": "input_text",
                                                                                   "text": rescue_text(heard)}]}})
        self._send_soon({"type": "response.create"})

    @_run_log
    def _log_rescue(self, r, outcome, heard):
        ear_log.emit("gate.rescue", trace=r.get("trace"), sum=f"{outcome} {str(heard)[:40]}",
                     ref={"session": self.session_no}, extra={"outcome": outcome, "texts": len(r["texts"])})

    def _hush(self, text=None, trace=None):
        """A spoken 闭嘴: the 围观 switch off through mind (the session closes when the switch reader sees it)."""
        if self._hush_busy:
            return
        self._hush_busy = True
        self.stats["hush"] += 1
        log("realtime: hush heard, switching Docking off", flush=True)
        try:
            ear_log.emit("trigger.hush", trace=trace, by="asr", sum=f"闭嘴 → Docking 关：{str(text or '')[:40]}",
                         ref={"session": self.session_no},
                         extra={"word": "闭嘴" if HUSH_ANY.search(norm_text(text or "")) else "short",
                                "action": "realtime_off"})
        except Exception as e:                      # noqa: BLE001 - the switch-off below must still happen
            _log_failed("hush", e)
        self.spawn(self._post_hush())

    async def _post_hush(self):
        loop = asyncio.get_running_loop()
        try:
            # by hush: not an owner's input for the pause (a TV can say 闭嘴; mind/api.py note_owner)
            fut = loop.run_in_executor(self._pool, self.call, "POST", "/switches", {"name": "realtime", "on": False,
                                                                                    "by": "hush"},
                                       self.cfg.context_timeout_s)
            status, _ = await asyncio.wait_for(fut, self.cfg.context_timeout_s + 0.5)
        except Exception:                              # noqa: BLE001 - mind slow / down
            status = None
        finally:
            self._hush_busy = False
        if status != 200:
            log(f"realtime: hush: switch off failed ({status})", flush=True)

    def _stage(self, stage):
        """Crowd's stage dots on the robot (via the mode relay); never costs the session."""
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

    # ---- greeting ----
    def _deliver_greet(self, now):
        g = self._greet
        owner = self.ctx.get("owner") or {}
        call = owner.get("call")
        if g is None or not self._ready.is_set() or self.busy() or self.clock() < self._quiet_until:
            return
        if self.quiet_on(now) or self.low_on(now):    # 安静 / 少插话: no greetings (step() drops an old one)
            return
        if self._seg is not None:                      # the server hears someone speaking: it would refuse (2026-11-18)
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
                    ear_log.emit("trigger.greet", trace=ear_log.new_trace(), by="m12", sum="招呼 greet_beep",
                                 ref={"session": self.session_no}, extra={"kind": "greet_beep"})
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
        self._next_trace, self._next_prompt = ear_log.new_trace(), text
        ear_log.emit("trigger.greet", trace=self._next_trace, by="tap" if g["kind"] == "tap" else "m12",
                     sum=f"招呼 {self._next_kind}", ref={"session": self.session_no},
                     extra={"kind": self._next_kind, "pick": (g.get("pick") or {}).get("kind")})
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
                "voice_key": self.voice_key})
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
