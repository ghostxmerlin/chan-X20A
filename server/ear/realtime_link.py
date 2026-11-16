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
视觉爆发 (2026-11-15, vision_burst.py): every input transcription also goes to burst.heard() ("注意看" starts a 2 min
photo-by-photo look, "别看了" stops it). Each look comes back through add_look(); the last LOOKS_KEEP within LOOKS_S are
in 【你的眼睛】 as 注视（HH:MM:SS，N 秒前）：… (newest first) and the instructions are re-sent on a new look, at most once
per LOOKS_PUSH_S. Without looks of its own (an ear restart), mind's 注视： line from the context block is used.
Caps: one session at most MAX_SESSION_S, closed after IDLE_S without speech, and at most HOUR_CAP_S of open sessions in
any hour; after a cap / idle close the next session opens only on the next speech onset (or an owner greeting).
Records: one realtime_turns document per response (timings, transcript lengths only, token usage, cost estimate); the
console log has timings and ids only, never text, names, the key or the voice id. The run log (ear_log / hello_log,
2026-11-16) has the full instructions (call.realtime.session / .context), every gate verdict (gate.realtime), rescue
(gate.rescue), hush (trigger.hush), greeting (trigger.greet), filler (filler.play), web search (call.realtime.search)
and turn (call.realtime.turn: usage, cost, heard / ear's own words / reply), on the trace of the heard sentence that
started it; never the key (only its first 7 characters) or the voice id.
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
import struct
import time
import traceback

import ear_log
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
LOCAL_KINDS = ("look_filler", "greet_beep")         # played from local PCM: no model call, no call.realtime.turn
FAILED_STATES = ("error", "stalled", "no_done", "robot_gone")
# Which of the ear's own sentences belong to a server VAD turn (run log trace + the turn's "ear" words): those whose ASR
# came in after the turn's start, less SEG_EPS_S; the start is the earlier of the server's speech_started and the ear's
# own onset of the speech whose audio the turn carries (_seg_onset). A sentence's ASR always arrives after its own speech began;
# the margin covers speech_started being stamped when the event reaches us (server VAD detection + network lag behind
# the real onset). The previous sentence's ASR comes ~0.4 s or more before the next turn's speech_started (server VAD
# silence 1.0 s vs the ear's 0.4 s + ASR ~0.2 s; closer sentences are one server turn), so 0.3 s keeps them apart.
SEG_EPS_S = 0.3
GATE_REASONS = {"play": "the reply may play", "quiet": "only 嗯: nobody was talking to hello",
                "unclear": "a didn't-hear question", "repeat": "word for word a recent reply",
                "blind": "看不清 without a fresh look", "wait_look": "a look question: the answer comes from the next photo"}
STEP_S = 0.2
log = print                                         # tests silence it

PERSONA = (
    "你是 hello，主人的小宠物机器人，主人去哪常带着你（办公室、家里、车上、饭店、户外都有可能）。"
    "你现在在哪，看背景里的“现在的地点”；写着不知道就别假定在办公室或桌上。"
    "用小孩子的语气说话：活泼、好奇、爱凑热闹，有点小脾气。"
    "说话用普通话。"
    "现在是围观模式：大家围着你看，跟你聊天，你就跟他们聊。\n"
    "【要求】\n"
    "- 每次用一两句口语短句回答，越短越好，像小朋友说话。\n"
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
    "- 收到“[事件] 主人（名字）出现在你面前了，长招呼”时：用这个名字大声、热情地喊主人，开心地打招呼，"
    "再顺口说一句你看到的情景或想到的事，两三句。每次都换新说法，不要跟事件里列的“之前说过”的一样。\n"
    "- 收到“[事件] 主人又出现了，短招呼：只说「…」”时：刚才已经见过，只原样说「」里那个词，一个字都不多说，不喊名字。\n"
    "- 收到“[事件] 有人点了你一下，让你跟面前的人打招呼”时：按事件里说的称呼主动打招呼，嘴甜一点、热情一点；"
    "认识的人叫名字，不认识的就用事件给的称呼（美女、帅哥、小朋友、爷爷、奶奶）。只夸，不评论长相的缺点，不开人身玩笑。"
    "事件说没看到人脸，就自然地说一句没看到人（每次换个说法），再友好地问声好。\n"
    "- 你有眼睛：头上的摄像头和人脸识别，看到的都写在最后的【你的眼睛】里：人脸识别是实时认出的人，"
    "摄像头照片识图是一分钟整理一次的画面（人在做什么、环境、变化）；"
    "“注视”是有人让你“注意看”之后你一张一张仔细看到的照片（新的在前，最准）。有人问你能不能看见、看到了谁、看到了什么，"
    "就把这两样合起来回答：谁在（认得的叫名字，不认得的说个大概），在做什么，周围什么样；"
    "两样都没有，就说现在没看清。不要说自己没有眼睛、看不见。\n"
    "- 有人问刚才、最近、今天早些时候发生了什么、聊了什么，先照背景里的“最近的情景”和“最近的纪要”回答，"
    "可以带上大概的时间；里面没有的就说不记得了，不要编。")

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


def rescue_text(heard):
    return f"（刚才这句是在跟你说话：「{str(heard)[:80]}」。直接回答它，不要只说「嗯」。）"


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


def instructions(context_text=None, seen=None, recent=None, looks=None):
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
    out = PERSONA + ("\n\n【背景】\n" + "\n".join(rest) if rest else "")
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


def session_config(voice, instr, silence_ms=500, threshold=0.5, search=False):
    # search (owner 2026-11-15 "可以支持搜索吗"): DashScope's built-in web search; the model searches only when it needs
    # to (strategy agent: measured 2026-11-15, "你好" no search 0.4 s, weather / news one search 3.5-4.5 s, ~1.9k more
    # text input tokens plus the search fee).
    cfg = {"modalities": ["text", "audio"], "voice": voice, "instructions": instr,
           "input_audio_format": "pcm", "output_audio_format": "pcm",
           "input_audio_transcription": {"language": "zh"},
           "turn_detection": {"type": "server_vad", "threshold": threshold, "silence_duration_ms": silence_ms}}
    if search:
        cfg["enable_search"] = True
    return cfg


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
    away_s = 60                  # owner unseen this long, then seen again = a new recognition
    long_greet_s = 600           # at most one long named greeting per 10 min (owner); inside it: a short hello
    short_greet_min_s = 60       # short hellos at most this often
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
        self._recent = collections.deque(maxlen=self.cfg.recent_keep)      # (at, heard, reply): memory only
        self._quiet_until = 0.0                        # no input / greeting right after hello finished speaking
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
        self._claimed = collections.deque(maxlen=12)       # traces of the ear's sentences a turn already carries
        self._rescue = None                            # a muted 「嗯」 that may still be asked again
        self._rescue_heard = None
        self._cmd_last = {}                            # burst word -> when it was last handled
        self._look_wait = None                         # a look question waiting for a fresh look
        self.fillers = list(fillers or [])             # [(text, pcm 24 kHz)]: "我看看哈" while waiting (load_fillers)
        self._filler_last = None
        self._look_question = None
        self.burst = None                              # vision_burst.VisionBurst (ear_server wires it)
        self._looks = collections.deque(maxlen=LOOKS_KEEP)    # (at wall ms, text): memory only, never logged
        self._looks_dirty, self._looks_sent_at = False, None
        self._local_traces = collections.deque(maxlen=6)  # (clock, trace) of the ear's own sentences (run log)
        self._seg_start = None                           # the server VAD's last speech_started (clock)
        self._seg_onset = None                           # the ear's own onset of the speech the next server turn carries
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
        onset = speaking and not self._was_speaking and not sounding
        if onset:
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
            if onset and self._seg_onset is None:      # this speech's audio goes up (or waits in _in while connecting)
                self._seg_onset = now
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
                self._push_looks(now)
                self._try_rescue(now)
                self._try_look_answer(now)
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
                     sum=f"围观会话关闭 {why}", ms=int((now - self.opened_at) * 1000), ref={"session": self.session_no},
                     extra={"state": "close", "why": why})
        self._spans.append((self.opened_at, now))
        self.state, self.opened_at = "closed", None
        self._need_speech = need_speech
        self._seg_start = self._seg_onset = None
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
            await ws.send(json.dumps({"type": "session.update", "session": session_config(
                self.voice, instr, self.cfg.silence_ms, self.cfg.vad_threshold, self.cfg.search)}))
            ear_log.emit("call.realtime.session", ok=True, model=self.cfg.model, key=self.key, sum=f"围观会话打开 {why}",
                         body={"system": instr}, ref={"session": n},
                         extra={"state": "open", "why": why, "search": bool(self.cfg.search),
                                "silence_ms": self.cfg.silence_ms, "vad_threshold": self.cfg.vad_threshold})
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
        return instructions(self.ctx.get("text"), seen, self._recent_pairs(), look_lines(self._looks, self.wall_ms()))

    # ---- vision burst (vision_burst.py) ----
    def add_look(self, text, at_ms, snapped=None):
        """One recognised burst photo: kept in memory (last LOOKS_KEEP) and pushed with the instructions (debounced).
        snapped: the burst clock when its photo was asked for (a pending look question takes only a later one)."""
        if not text:
            return
        self._looks.append((at_ms, str(text)[:200]))
        self._looks_dirty = True
        w = self._look_wait
        if w is not None and not w["sent"] and w.get("look") is None and snapped is not None and snapped >= w["mark"]:
            w["look"] = str(text)[:200]
            self._try_look_answer(self.clock())
        self._push_looks(self.clock())

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
        picks = [i for i in range(len(self.fillers)) if i != self._filler_last] or [0]
        i = picks[self.rng.randrange(len(picks))]
        self._filler_last = i
        rid = f"filler{next(self._beeps)}"
        pcm = self.fillers[i][1]
        self._turns[rid] = {"kind": "look_filler", "speech_end": None, "heard_chars": None, "rid": rid,
                            "session": self.session_no, "response_at": now, "first_delta": now, "sound_at": None,
                            "reply_chars": len(self.fillers[i][0]), "reply": self.fillers[i][0],
                            "audio_ms": len(pcm) * 1000 // (SR_OUT * 2), "usage": None, "at": self.wall_ms()}
        self.stats["look_fillers"] += 1
        log(f"realtime: session {self.session_no} look filler", flush=True)
        ear_log.emit("filler.play", trace=w.get("trace"), sum=self.fillers[i][0][:60] or None,
                     ref={"rid": rid, "session": self.session_no},
                     extra={"kind": "look_filler", "clip": i, "audio_ms": self._turns[rid]["audio_ms"]})
        self._out_put(("pcm", rid, pcm))
        self._out_put(("end", rid, b""))

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
                heard = turn.get("heard") if turn.get("kind") in ("speech", "rescue", "look_answer") else f"[{turn.get('kind')}]"
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
        ear_log.emit("call.realtime.context", trace=trace, model=self.cfg.model, body={"system": instr},
                     ref={"session": self.session_no}, extra={"why": why, "chars": len(instr)})
        self._send_soon({"type": "session.update", "session": {"instructions": instr}})

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
            self._last_speech = self._seg_start = now
        elif t == "input_audio_buffer.speech_stopped":
            self._speech_end = now
            recent = self._local_end if self._local_end is not None and now - self._local_end < 3 else None
            # the segment starts at the earlier of the server's speech_started and the ear's own onset of the speech whose
            # audio it carries: right after a (re)connect the queued audio reaches the server in a burst, so its
            # speech_started can come after the ear has already recognised a short first sentence ("你好")
            marks = [m for m in (self._seg_start, self._seg_onset) if m is not None and m <= now]
            start = min(marks) if marks else None
            heard = [(at, text, tr) for (at, text), (_, tr) in zip(self._local_texts, self._local_traces)]
            mine = [h for h in heard if (h[0] >= start - SEG_EPS_S if start is not None else now - h[0] < 3)]
            if not mine:                               # no onset seen (it came while hello was busy): the last 3 s,
                mine = [h for h in heard if now - h[0] < 3 and h[2] not in self._claimed]   # minus sentences
            pick = next(((at, tr) for at, _, tr in mine if tr), (None, None))                # another turn took
            self._claim(pick[1])
            self._turn = self._heard_turn = {"kind": "speech", "speech_end": now, "heard_chars": None,
                                             "local_end": recent, "start": start,
                                             "trace": pick[1], "trace_at": pick[0],
                                             "ear": [text for _, text, _ in mine]}
            self._seg_start = self._seg_onset = None
            self._stage("think")
        elif t == "conversation.item.input_audio_transcription.completed":
            text = strip_emoji(e.get("transcript"))
            if self._heard_turn is not None:
                self._heard_turn["heard_chars"] = len(text)
                self._heard_turn["heard"] = text[:300]
            if self.on_transcript:
                self.on_transcript("heard", text)
            self._heard_text(text, now, told=True, trace=(self._heard_turn or {}).get("trace"))
        elif t == "response.created":
            rid = str((e.get("response") or {}).get("id") or f"r{now}")
            self._resp = rid
            turn = self._turn if self._turn is not None and self._next_kind == "speech" else \
                {"kind": self._next_kind, "speech_end": None, "heard_chars": None,
                 "trace": self._next_trace if self._next_kind != "speech" else None,
                 "prompt": self._next_prompt if self._next_kind != "speech" else None}
            self._next_kind, self._turn = "speech", None
            self._next_trace = self._next_prompt = None
            turn.update({"rid": rid, "session": n, "response_at": now, "first_delta": None, "sound_at": None,
                         "reply_chars": 0, "audio_ms": 0, "usage": None, "at": self.wall_ms(),
                         "gate": None if turn.get("kind") in ("speech", "rescue", "look_answer") else "play",
                         "held": [], "tx": ""})
            if turn.get("kind") == "rescue":
                turn["heard"] = self._rescue_heard
            elif turn.get("kind") == "look_answer":
                turn["heard"] = self._look_question
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
                if text and turn.get("gate") == "play":
                    self._recent.append((self.clock(), turn, text[:80]))   # heard is read later (it may come after)
                    if self._ws is not None and self._ready.is_set():     # the next reply already knows it
                        self._send_instructions("reply", turn.get("trace"))
            if self.on_transcript:
                self.on_transcript("reply", text)
        elif t == "response.done":
            resp = e.get("response") or {}
            rid = str(resp.get("id") or self._resp)
            turn = self._turns.get(rid)
            self._log_search(rid, turn, resp.get("usage"))
            if turn is not None:
                turn["usage"], turn["status"] = resp.get("usage"), resp.get("status")
                self._judge(rid, turn, True)            # no transcript came: decided on what there is
            self._resp = None
            if rid in self._dropped or turn is None or not turn["audio_ms"]:
                muted = turn.get("muted") if turn is not None else None
                self._finish_turn(rid, muted or ("no_audio" if rid not in self._dropped else "yielded"))
                self._try_rescue(now)                  # a muted 「嗯」 may be asked again right away
                self._try_look_answer(now)
                if self._look_wait is None and self._next_kind == "speech":
                    self._stage("skip")                # (waiting for a look / an answer on its way: the dots stay)
            else:
                self._out_put(("end", rid, b""))

    def _judge(self, rid, turn, done):
        """The reply gate (see reply_verdict): plays the held audio, or drops the reply for good."""
        if turn.get("gate") is not None:
            return
        now = self.clock()
        if turn.get("kind") == "speech":
            asked = next((t for t in [turn.get("heard")] + [t for at, t in self._local_texts if now - at <= 3]
                          if t and is_look_ask(t)), None)
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
        recent = [norm_text(r) for at, _, r in self._recent if now - at <= self.cfg.recent_s]
        fresh = bool(self._looks) and self.wall_ms() - self._looks[-1][0] <= FRESH_LOOK_S * 1000
        v = reply_verdict(turn.get("tx"), done, recent, blind=not fresh and turn.get("kind") != "look_answer")
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

    def _claim(self, trace):
        if trace and trace not in self._claimed:
            self._claimed.append(trace)

    def _log_gate(self, rid, turn, v, done, **extra):
        """gate.realtime: one verdict per reply (emit only: this runs on the voice loop)."""
        if not turn.get("trace"):
            turn["trace"] = ear_log.new_trace()        # nothing heard by the ear: the reply starts its own trace
        verdict = "play" if v == "play" else f"muted_{v}"
        ear_log.emit("gate.realtime", trace=turn["trace"], sum=f"{verdict} {(turn.get('tx') or '')[:40]}".strip(),
                     ref={"rid": rid, "session": turn.get("session")},
                     extra={"verdict": verdict, "reason": GATE_REASONS.get(v, v), "kind": turn.get("kind"),
                            "done": done, **extra})

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
        log. trace: its heard.asr trace (run log), carried by the turn, gate, rescue and burst it leads to."""
        if not text or not self.active() or not self.in_crowd():
            return
        now = self.clock()
        self._local_texts.append((now, str(text)[:300]))
        self._local_traces.append((now, trace))
        h = self._heard_turn                           # the server VAD ended the turn first: it is this sentence's ...
        if h is not None and now - h["speech_end"] < 3 and h.get("kind") == "speech" \
                and not (self._last_speech is not None and self._last_speech > h["speech_end"]):   # ... unless new speech began
            h.setdefault("ear", []).append(str(text)[:300])
            stale = h.get("start") is not None and h.get("trace_at") is not None \
                and h["trace_at"] < h["start"] - SEG_EPS_S
            if trace and (not h.get("trace") or stale):
                h["trace"], h["trace_at"] = trace, now
            self._claim(trace)
        self._heard_text(str(text)[:300], now, trace=trace)

    def _heard_text(self, text, now, told=False, trace=None):
        if is_look_ask(text):
            self._start_look_wait(text, now, trace)
        r = self._rescue
        if r is not None and not r["sent"] and now - r["at"] <= RESCUE_S:
            r["texts"].append(text)
            self._try_rescue(now)
        if self.burst is not None:
            key = self._burst_key(text)
            if key and not (self._cmd_last.get(key, -1e9) > now - 4):    # both sources heard it: once
                self._cmd_last[key] = now
                try:
                    with ear_log.scope(trace=trace):    # the burst (and its looks) carry this sentence's trace
                        self.burst.heard(text)
                except Exception:                      # noqa: BLE001 - a burst never costs the session
                    log(f"realtime: burst trigger failed\n{traceback.format_exc().rstrip()}", flush=True)
        if told:
            self._told(text)
        if is_hush(text):
            self._hush(text, trace)

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
        if now - r["at"] > RESCUE_S or (self._last_speech is not None and self._last_speech > r["at"]):
            self._rescue = None                        # too late, or someone is already talking again
            return
        follow = bool(self._recent) and now - self._recent[-1][0] <= FOLLOW_S      # hello just spoke
        heard = next((t for t in r["texts"] if addressed(t) or (follow and is_question(t))), None)
        if heard is not None and is_look_ask(heard):
            self._start_look_wait(heard, now, r.get("trace"))
        if heard is not None and self._look_wait is not None:
            r["sent"] = True                           # a look question is answered from the next fresh look
            self._log_rescue(r, "look_wait", heard)
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

    def _log_rescue(self, r, outcome, heard):
        ear_log.emit("gate.rescue", trace=r.get("trace"), sum=f"{outcome} {str(heard)[:40]}",
                     ref={"session": self.session_no}, extra={"outcome": outcome, "texts": len(r["texts"])})

    def _hush(self, text=None, trace=None):
        """A spoken 闭嘴: the 围观 switch off through mind (the session closes when the switch reader sees it)."""
        if self._hush_busy:
            return
        self._hush_busy = True
        self.stats["hush"] += 1
        log("realtime: hush heard, switching 围观 off", flush=True)
        ear_log.emit("trigger.hush", trace=trace, by="asr", sum=f"闭嘴 → 围观 关：{str(text or '')[:40]}",
                     ref={"session": self.session_no}, extra={"word": "闭嘴" if HUSH_ANY.search(norm_text(text or ""))
                                                             else "short", "action": "realtime_off"})
        self.spawn(self._post_hush())

    async def _post_hush(self):
        loop = asyncio.get_running_loop()
        try:
            fut = loop.run_in_executor(self._pool, self.call, "POST", "/switches", {"name": "realtime", "on": False},
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

    def _log_turn(self, turn, doc, state):
        """call.realtime.turn: usage as DashScope sent it, the cost estimate, what was heard (the model's transcript and
        the ear's own words) and the reply; ref.tid = the realtime_turns row."""
        inp = {k: v for k, v in (("heard", turn.get("heard")), ("ear", turn.get("ear") or None),
                                 ("prompt", turn.get("prompt"))) if v}
        reply = turn.get("tx") or turn.get("reply")
        ear_log.emit("call.realtime.turn", trace=turn.get("trace") or ear_log.new_trace(), model=self.cfg.model,
                     key=self.key, ok=state not in FAILED_STATES, err=state if state in FAILED_STATES else None,
                     ms=doc["timings"].get("response_to_first_audio_ms"), usage=turn.get("usage"),
                     cost=ear_log.cost(doc["cost_cny"], PRICE_BASIS), sum=(reply or f"[{state}]")[:60],
                     body={"input": inp or None, "output": reply or None},
                     ref={"tid": doc["tid"], "session": turn.get("session"), "rid": turn.get("rid")},
                     extra={"kind": turn.get("kind"), "state": state, "timings": doc["timings"],
                            "audio_ms": turn.get("audio_ms"), "heard_chars": turn.get("heard_chars"),
                            "reply_chars": turn.get("reply_chars"), "tokens": doc["usage"]})

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

    async def shutdown(self):
        self.close("shutdown")
        for t in list(self._bg):
            t.cancel()
        await asyncio.gather(*list(self._bg), return_exceptions=True)
        self._pool.shutdown(wait=False, cancel_futures=True)
