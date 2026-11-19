"""How long a mode lasts, from what the owner said (owner 2026-11-19: "安静一下之类的模式，或者即将要做的调试模式，都要持续一
段时间…改成可以通过语言设定…可以预约一段时间，比如 30 分钟，也可以是直到我通知你退出").

parse(text) -> seconds (float), OPEN (until the owner ends it), CALLED (until hello is called: 直到我叫你), or None
(nothing said: the mode's own default).
  durations: 30 分钟 / 三十分钟 / 半小时 / 半个小时 / 一个半小时 / 两个钟头 / 1.5 小时 / 一刻钟 / 十分钟后 ...
  open:      直到我说退出 / 等我通知 / 我说好了再 / 一直安静 / 不限时 / 没我允许 ...
  called:    直到我叫你 / 等我叫你 / 我叫你再说
ended(text, loose) -> True for "退出 / 结束 / 取消 + 安静 / 少插话 / 角色 / 调试 (模式)", 恢复正常, 可以说话了 ...; loose (said
  to hello: a call window or the call word) also takes a bare 退出 / 结束吧 / 取消.
MAX_S caps a duration (a day); OPEN is float('inf') so `now < until` keeps working.
"""
import math
import re

OPEN = math.inf
CALLED = "called"
MAX_S = 24 * 3600.0
_DIG = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_NUM = r"[0-9]+(?:\.[0-9]+)?|[零一二两三四五六七八九十百]+"
DUR_RE = re.compile(rf"(?P<n>{_NUM})?(?P<half>半)?(?:个)?(?P<half2>半)?(?P<u>小时|钟头|分钟|分(?!之)|刻钟)")
CALLED_RE = re.compile(r"(直到|等)我(再)?(叫|喊)你|我(再)?(叫|喊)你(再|才|的时候)")
OPEN_RE = re.compile(r"直到我(再)?(让|说|通知|告诉)|等我(再)?(通知|说|让)|我说(好了|可以了|结束|退出|停|行了)(再|才|为止)?|"
                     r"一直(安静|这样|保持|到我|不说话)|不限时|没有?我(的)?(允许|同意)|别自己(结束|退出|恢复)|直到我(说)?(退出|结束)")
END_RE = re.compile(r"(退出|结束|取消|关掉|关闭|停止|解除)(一下)?(安静|少插话|角色|调试)(模式)?(吧|了)?|"
                    r"不用(再)?安静了|恢复(正常)?(说话|模式)?$|可以(说话|插话|接话)了|(安静|调试)(模式)?结束")
END_LOOSE_RE = re.compile(r"^(那)?(就)?(退出|结束|取消|停|好了)(吧|了|这个模式|模式)?$")


def cn_number(s):
    """'三十五' -> 35, '两' -> 2, '十' -> 10, '一百二十' -> 120, '1.5' -> 1.5; None when it is not a number."""
    if not s:
        return None
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", s):
        return float(s)
    total, cur = 0, 0
    for ch in s:
        if ch in _DIG:
            cur = _DIG[ch]
        elif ch == "十":
            total += (cur or 1) * 10
            cur = 0
        elif ch == "百":
            total += (cur or 1) * 100
            cur = 0
        else:
            return None
    return float(total + cur)


def parse(text):
    t = re.sub(r"\s+", "", str(text or ""))
    if not t:
        return None
    if CALLED_RE.search(t):
        return CALLED
    if OPEN_RE.search(t):
        return OPEN
    m = DUR_RE.search(t)
    if not m:
        return None
    unit = m.group("u")
    n = cn_number(m.group("n"))
    half = bool(m.group("half") or m.group("half2"))
    if n is None:
        if not half:
            return None if unit != "刻钟" else 15 * 60.0
        n = 0.0                                       # 半小时 / 半个钟头
    if half:
        n += 0.5
    sec = {"小时": 3600, "钟头": 3600, "分钟": 60, "分": 60, "刻钟": 900}[unit] * n
    return min(sec, MAX_S) if sec > 0 else None


def ended(text, loose=False):
    t = re.sub(r"[\s，,。.！!？?]+", "", str(text or ""))
    return bool(END_RE.search(t) or (loose and END_LOOSE_RE.search(t)))


def describe(until, now, wall_ms, tz_offset_min=None):
    """'直到你说退出' / '到 15:30' / '' (no until)."""
    if until is None:
        return ""
    if until == OPEN:
        return "直到主人说退出"
    import time as _t
    end_ms = wall_ms + (until - now) * 1000
    return "到 " + _t.strftime("%H:%M", _t.localtime(end_ms / 1000))
