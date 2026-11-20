"""Docking hub, the module level: constants, the text and prompt helpers, small classes (moved out of
realtime_link.py unchanged on 2026-11-19, refactor 4.2; realtime_link re-exports every name).

log and _LOG_FAILED stay in realtime_link (tests patch them there): the shims below look them up at call time."""
import sys as _sys


def log(*a, **k):
    m = _sys.modules.get("realtime_link")
    return (m.log if m is not None else print)(*a, **k)


class _LateFailed:
    """realtime_link._LOG_FAILED, looked up at use (a test may swap the set)."""
    def _s(self):
        m = _sys.modules.get("realtime_link")
        return m._LOG_FAILED if m is not None else _FALLBACK

    def __contains__(self, x):
        return x in self._s()

    def add(self, x):
        self._s().add(x)


_FALLBACK = set()
_LOG_FAILED = _LateFailed()

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
import mode_span
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
GATE_REASONS = {"marker": "念出了事件 / 背景标记，不播", "body_gated": "不认识的人叫它动，没动", "tool_gated": "闲聊里想用工具，挡回去了（没执行、没放垫话）", "play": "the reply may play", "quiet": "only 嗯: nobody was talking to hello",
                "unclear": "a didn't-hear question", "repeat": "word for word a recent reply",
                "blind": "看不清 without a fresh look", "wait_look": "a look question: the answer comes from the next photo",
                "wait_search": "a search question (查一下 / 新闻 …): the answer comes from the ear's own web search",
                "order": "a Unit ASR command word: a direct order the robot carries out itself (owner 2026-11-18)"}
STEP_S = 0.2


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
BODY = ("你住在主人的桌面机器人 Providence（神意高达）里，主人去哪常带着你（办公室、家里、车上、饭店、户外都有可能）。"
        "有人叫 Providence 就是在叫你。")      # owner 2026-11-20: renamed from Dynames
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
    # 2026-11-18 skill test: a reply began with "[事件] 主人（hello）出现在你面前了，打个招呼" (the format below, echoed)
    "- 背景、事件、工具结果里的标记（[事件]、【…】、字段名）只是给你看的，绝不念出来；也不要自己编事件。\n"
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
    "- 收到“[事件] 你刚被人用力晃了几下”时：照事件说的，自然地说一句就够。\n"
    "- 你也有身体感觉：【你的眼睛】里的“身体感觉”来自机身的传感器（被拿起来、放下、晃了几下、有东西靠得很近）。"
    "刚发生的可以自然地带一句（比如被拿起来时问一句要带我去哪），不要每次都提；没有这一行就是身体好好放着。\n"
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
# 2026-11-19 13:53: "没叫你安静点儿" (I did NOT tell you to be quiet) turned Docking off; a hush word right after a
# negation / question word is not a hush ("没叫你闭嘴", "不是让你别说话", "谁让你安静了", "不用安静")
HUSH_NOT = re.compile(r"(没(?!事|关系)|不是|不用|不要你|谁|干嘛|为什么)[^，。！？,.!?\s]{0,3}(闭嘴|别说|不要说|不许说|安静|别吵)")


def norm_text(text):
    """Letters only (no emoji, punctuation, spaces), for comparing replies."""
    return re.sub(r"[\W_]+", "", strip_emoji(text))


MARKER_RE = re.compile(r"^\s*[\[【（(]\s*(事件|系统|背景|你的眼睛)|\[事件\]|^\s*\[[a-z_]+\]")   # [tool_refused]: a kind placeholder


def reply_verdict(text, done, recent=(), blind=False):
    """The gate of one speech reply: None = wait for more text, "play", or why it must not play: "quiet" (only 嗯),
    "unclear" (a didn't-hear question), "repeat" (word for word one of `recent`, normalised replies), "blind" (with
    blind=True, i.e. no fresh look: 看不清 / 黑乎乎 ... said without any photo to go on)."""
    if MARKER_RE.search(str(text or "")[:40]):
        return "marker"                                # an echoed event / context marker: never played (2026-11-19)
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


# Being called (owner 2026-11-18, renamed 2026-11-20: the robot is Providence; the StickS3 voice module's PROVIDENCE
# word is a strong, independent "talking to me"; what follows must be taken seriously; hello may interrupt hello's own
# speech). Sources: the robot's name word (asr_wake on /mouth), a sentence that starts with the name, a tap (the
# keyword ear is no longer used). A call opens CALLED_S: hello's
# reply stops, the ear's audio goes up for the whole window, and a sentence in it is answered even when smart_turn
# calls it not for hello (ambient) or the model only says 「嗯」; a called sentence the model never got (said while
# hello spoke) is answered from the ear's own ASR after CALLED_ANSWER_WAIT_S.
CALLED_S = 10.0
# owner 2026-11-19: a tap on the screen in Docking outranks being called and must be answered: the next sentence within
# TOUCH_S of the tap is answered whatever the gates say (it is a call window, only longer); hello says 在听 under the
# eyes until she starts on it. A tap while she speaks stops her; a double tap is 安静一下 (touch_quiet).
TOUCH_S = 20.0
# owner 2026-11-19 "我刚才使劲摇晃了机器人，没有任何反应": being shaken hard (body_sense shaken) is an event hello answers in
# one line of her own (queued like a greeting: waits for quiet, none in 安静 / 少插话), at most once per SHAKEN_SAY_S.
SHAKEN_SAY_S = 60.0
SHAKEN_TEXT = "[事件] 你刚被人用力晃了几下：照你的性格自然地说一句（抗议、喊晕、问他干嘛都行），一句话就够，不提传感器，每次换个说法。"
CALLED_ANSWER_WAIT_S = 2.5
CALL_RE = re.compile(r"^\W*(providence|普罗维登斯|神意)\W*", re.I)
CALLED_SOURCES = {"asr": "语音模块听到 Providence", "name": "关键词耳朵（已停用）", "text": "句子开头叫了 Providence",
                  "touch": "主人点了屏幕（必答）"}


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
# 2026-11-19 (owner "可以预约一段时间，比如 30 分钟，也可以是直到我通知你退出"): how long is said (mode_span.py) in the
# sentence or passed to the tool (minutes / until exit|called); these are the defaults when nothing is said. A mode with
# a said length (a duration or until exit) is "sticky": a call is answered but does not end it. Modes are kept in
# hello.switches {_id: rt_modes_status} and restored when the ear starts (restore_modes).
QUIET_MAX_S = 4 * 3600.0
LOW_S = 1800.0
ROLE_S = 2 * 3600.0
LOW_RE = re.compile(r"我们(正)?在聊(天|事|呢)?|我们聊我们的|(我|我们)(正)?在看电视|看电视呢|在开会|你(先)?别插(话|嘴)|"
                    r"不用理(我们|我)|不是跟你说")
QUIET_RE = re.compile(r"安静(一)?会(儿)?|先安静|你先别(说话|出声|说了)|先别(说话|出声)|安静一下|别出声了")
LOW_END_RE = re.compile(r"(你)?可以(说话|插话|聊)了|(你)?可以接话了|恢复(正常)?说话")
ROLE_RE = re.compile(r"(当|做|演|扮演|变成|装成)(一?个|一下)?(?P<who>[^，,。！!？?]{1,10}?)(吧|好吗|好不好)?([，,。！!]|$)(?P<rest>.*)")
# 调试模式 (owner 2026-11-19, today's task 3 "帮我调试"): hello listens and keeps a debug log (mind/debug_mode.py reads
# the speech since the start), stays out of the talk (as 安静, a call or a tap is answered), says a reminder when mind
# finds one (debug_alert), and asks at the end whether to sum up. No length said = until the owner ends it.
DEBUG_RE = re.compile(r"(进入|开始|开启|打开|切到|切换到)(一下)?调试(模式)?|帮我调试|陪我调试|调试模式(开始|开一下)")
DEBUG_ALERT_S = 120.0                                # reminders at most this often (mind waits longer; a guard here)
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


# owner 2026-11-19: 「我查查」 alone is too few - a filler picked by the situation (the tool), at random. look = the look
# fillers (<voice>/); the others <voice>-<group>/ (tools/voice/make_tool_fillers.py). Tools not listed: search.
FILLER_GROUP = {
    **{n: "look" for n in ("look_now", "scene_now")},
    **{n: "recall" for n in ("memory_search", "semantic_search", "speech_search", "hello_said", "knowledge_search",
                             "notes_search", "person_lookup", "people_list", "recent_speech", "recent_sounds",
                             "review_period", "why_last_turn", "cost_today")},
    **{n: "daily" for n in ("reminder_set", "reminder_list", "reminder_cancel", "schedule_add", "schedule_list",
                            "watch_start", "watch_list", "watch_stop", "memory_propose", "person_note_propose",
                            "switch_propose")},
}
TOOL_FILLER_GROUPS = ("search", "recall", "daily")


def load_tool_fillers(base, voice):
    """{group: [(text, pcm)]} from <base>/<voice>-<group>/ (groups that are missing are left out)."""
    out = {}
    for g in TOOL_FILLER_GROUPS:
        clips = load_fillers(os.path.join(base, f"{voice}-{g}"))
        if clips:
            out[g] = clips
    return out


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
    if not t or HUSH_NOT.search(strip_emoji(text or "")):      # on the text with its punctuation: "没事，你闭嘴" hushes
        return False
    return bool(HUSH_ANY.search(t)) or (len(t) <= HUSH_SHORT_CHARS and bool(HUSH_SHORT.search(t)))


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


# 全答: the persona's addressee rule (judge who is talked to, else only 「嗯」) gives way to one line
ADDRESSEE_RULES = PERSONA_RULES[PERSONA_RULES.index("- 先分清是不是在跟你说话"):PERSONA_RULES.index("- 不要重复自己")]
CHAT_IDLE_S = 30.0          # 点屏幕聊天: the chat ends this long after the last speech / hello's last reply / the tap
CHAT_MAX_S = 600.0          # ... and this long after the tap at most (a TV keeps the ear's VAD going): tap again
ANSWER_ALL_RULE = "- 听到的每句话都当作是在跟你说的，正常回答。不确定的事不要编。\n"


def answer_all_persona(persona):
    return str(persona or "").replace(ADDRESSEE_RULES, ANSWER_ALL_RULE)


def instructions(context_text=None, seen=None, recent=None, looks=None, scene=None, length=None, role=None, manner=None,
                 persona=None, skills=None, speaker=None, body=None):
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
    if speaker:                                        # 2026-11-18: who said the newest sentence (the ear's voiceprint)
        eyes.append(f"声音（刚才那句话）：{speaker}")
    if scene:                                          # 即时画面 (2026-11-18): the newest uploaded photo, seconds old
        eyes.append(f"摄像头刚拍到的（{scene}）")
    elif eye.get("画面："):
        eyes.append(f"摄像头照片识图（大约一分钟前）：{eye['画面：']}")
    if body:                                           # 身体感觉 (collector-0.5.12): IMU / proximity events, held now
        eyes.append(body)
    if eyes:
        out += "\n\n【你的眼睛】\n" + "\n".join(eyes)
    if skills:                                         # the open skills' full text (owner 2026-11-18): here, not in the
        out += "\n\n【此刻打开的技能】（用到时照着做）\n" + skills   # history, so a closed skill leaves no trace
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
    if search and not tools:
        cfg["enable_search"] = True
    if tools:                                          # L6 2026-11-18: Qwen calls hello's tools (probe: supported)
        # probe 2026-11-18 23:58: enable_search together with function tools makes every response fail (InternalError
        # "An internal error occurred while generating the response", status failed); with tools, searching is the
        # web_search tool's job (verify / realtime skills)
        cfg["tools"], cfg["tool_choice"] = tools, "auto"
    return cfg


# L6 (owner 2026-11-18 "工具也给 qwen 吧"): behind the 千问用工具 switch (realtime_tools, default off). The tool list is
# mind's GET /tools (live tools) minus these: hello speaks for itself; a 38 s cross-check is too slow for a reply.
TOOL_SKIP = frozenset(("say", "stop_speaking", "search_verify", "set_voice",
                       "db_query"))     # security review 2026-11-18: raw records stay out of a voice anyone can talk to
TOOL_DEPTH = 3                                         # tool calls in a row for one sentence
TOOL_TIMEOUT_S = 30.0
# owner 2026-11-19 "单独放宽": 查证 (two searches + a judge) takes ~38 s
SLOW_TOOL_TIMEOUT_S = {"search_verify": 75.0, "doc_answer": 75.0}
# owner 2026-11-19 "调用技能或工具时…机器上应该有ui…不再响应新的自然语言输入，等同于在说话，可以被触摸、asr hello 等打断":
# while a tool runs the robot's dots go amber (stage tool, collector-0.5.16; its touch rules count it as speaking) and a
# word under the eyes says what hello is doing; no new speech goes up (as while she speaks) unless she is called / tapped,
# which cancels the tool: its result is dropped (the call gets a cancelled output, no reply).
TOOL_WORD = {"doc_answer": "查资料中", "doc_search": "查资料中", "web_search": "上网查", "search_verify": "核实中",
             "live_data": "查行情", "deep_research": "交给研究", "look_now": "在看", "memory_search": "在回忆",
             "semantic_search": "在回忆", "speech_search": "在回忆", "notes_search": "翻纪要", "review_period": "在回顾"}
TOOL_WORD_GROUP = {"search": "在查", "recall": "在回忆", "daily": "在记", "look": "在看"}
TOOL_OUTPUT_CHARS = 3000


SKILLS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills.json")
TOOL_ENOUGH = "这句话已经查了好几次了：不要再调用工具，现在就用已经拿到的结果回答（没查到就直说）。"
SKILL_DEPTH = 5                                        # skills / auto: tool calls for one sentence (open_skill not counted)
PRELOAD_KEEP_S = 120.0
MAX_SKILL_TOOLS = 16                                   # tools of open skills at once
TOOL_GATE_TEXT_S = 8.0
UNSURE_LEAD = 0.1                                      # an unsure voiceprint names its top match only this far ahead
BODY_TOOLS = frozenset(("turn_head", "face_front", "turn_around", "tracking", "nod", "shake_head", "express_eyes",
                        "show_word", "play_clip", "set_volume"))      # move / express (robot_status only reads)                                 # the ear's own ASR texts this recent count for the tool gate                                 # auto: a skill preloaded for a sentence, never used, goes after this


SKILLS_REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "config", "skills", "skills.json")


def load_skills(path=None):
    """config/skills/skills.json (the ear's deploy copies it next to this file; the repository copy otherwise) -> dict,
    or {} when missing. A skill's full text (config/skills/skills/<id>.md, owner 2026-11-18: a skill is complete -
    when, steps, how to call each tool, the answer, examples) replaces its short guide when the file is there."""
    for p in ([path] if path else [SKILLS_FILE, SKILLS_REPO]):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError, AttributeError):
            continue
        if not isinstance(d.get("skills"), list):
            return {}
        folder = os.path.join(os.path.dirname(p), "skills")
        for sk in d["skills"]:
            try:
                with open(os.path.join(folder, f"{sk['id']}.md"), encoding="utf-8") as f:
                    sk["guide"] = f.read().strip()[:4000]
            except (OSError, KeyError):
                pass
        return d
    return {}


def open_skill_spec(skills):
    """The one tool of the first layer (owner 2026-11-18: the model finds a skill first, then its tools)."""
    ids = [s["id"] for s in skills]
    # owner 2026-11-18 "没有机会给全部列表，会不会错失模型发挥创造力": each skill lists its tools' names too (cheap),
    # so the model sees everything it can do; several skills may be opened for one task
    menu = "；".join(f"{s['id']}={s['title']}（{s['when']}；工具：{'全部' if '*' in (s.get('tools') or []) else '、'.join(s.get('tools') or [])}）"
                    for s in skills)
    return {"type": "function", "name": "open_skill",
            "description": f"要用工具时先打开对应的技能，拿到它的工具和做法，再调用那些工具；一件事要用到好几个技能就都打开。"
                           f"闲聊不用打开。技能：{menu}",
            "parameters": {"type": "object", "properties": {"skill": {"type": "string", "enum": ids}}, "required": ["skill"]}}


SKILL_TOOL_SKIP = frozenset(("say", "stop_speaking", "db_query"))   # inside a skill set_voice (a proposal) is fine


def realtime_tools(mind_tools, skip=TOOL_SKIP):
    """mind's function list ({type: function, function: {name, description, parameters}}) -> the realtime format."""
    out = []
    for t in mind_tools or []:
        f = t.get("function") or {}
        if f.get("name") and f["name"] not in skip:
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
    turn = "server_vad"          # 智能断句 off by default (owner 2026-11-20); the switch turns smart_turn_v2 on (2026-11-18 trial)
    # 点屏幕聊天 / 全手动 (owner 2026-11-19 night: "开了它就应答所有的话，关了它就只收集"; "调试模式，你看看之类的…也是要在
    # 我点击屏幕…以后才能启动"; "暂时退回全手动"): no session and no word of its own unless a tap opened a chat; in the
    # chat every sentence is answered, no judgement (server_vad, no 「嗯」 rule, no reply / tool gate). Live default: on
    # (ear_server, mind's switch); False here keeps the automatic behaviour for the tests written before it.
    answer_all = False
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
