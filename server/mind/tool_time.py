"""Time words for tools (docs/hello-tools.md §4, B2 2026-11-18): what people say -> a [start, end) range in epoch ms.

Absolute: 2026-11-18 09:00 · 2026-11-18 · 11-18 · 09:00 / 9点 / 9点半 (today).
Relative: 现在 / 刚才 · N分钟前 / N小时前 / N天前 · 半小时前.
Days: 今天 / 昨天 / 前天 / 大前天 · 周三 / 星期三 / 上周三 / 本周三 / 这周 / 上周.
Parts of a day (after a day word, or alone = today): 凌晨 0–6, 早上 6–9, 上午 6–12, 中午 11–13, 下午 12–18,
傍晚 17–19, 晚上 18–24, 夜里 21–24.
parse(text) -> (start, end): a point (relative / clock time) has end = start; a day or part has its own end.
span(frm, to): a range from two such words; frm alone = from its start to its own end (a day / part) or to now.
"""
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .tools import ToolError

PARTS = {"凌晨": (0, 6), "早上": (6, 9), "早晨": (6, 9), "上午": (6, 12), "中午": (11, 13), "下午": (12, 18),
         "傍晚": (17, 19), "晚上": (18, 24), "夜里": (21, 24), "白天": (6, 18)}
DAYS = {"今天": 0, "今日": 0, "昨天": 1, "昨日": 1, "前天": 2, "大前天": 3}
WEEKDAY = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "半": 0.5}


def _num(s):
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return float(s)
    if s in CN_NUM:
        return CN_NUM[s]
    if len(s) == 2 and s[0] == "十" and s[1] in CN_NUM:
        return 10 + CN_NUM[s[1]]
    if len(s) == 2 and s[1] == "十" and s[0] in CN_NUM:
        return CN_NUM[s[0]] * 10
    raise ToolError("bad_args", f"看不懂数字「{s}」")


def _ms(dt):
    return int(dt.timestamp() * 1000)


def parse(text, now_ms, tz="Asia/Shanghai"):
    zone = ZoneInfo(tz)
    now = datetime.fromtimestamp(now_ms / 1000, zone)
    t = re.sub(r"\s+", " ", str(text or "").strip())
    if not t:
        raise ToolError("bad_args", "时间是空的")
    if t in ("现在", "此刻", "刚才", "刚刚"):
        return (now_ms - (5 * 60_000 if t in ("刚才", "刚刚") else 0), now_ms)
    m = re.fullmatch(r"(\d+(?:\.\d+)?|[一两二三四五六七八九十半]{1,2})个?(分钟|小时|钟头|天)前", t)
    if m:
        n = _num(m.group(1))
        unit = {"分钟": 60_000, "小时": 3600_000, "钟头": 3600_000, "天": 86400_000}[m.group(2)]
        start = now_ms - int(n * unit)
        return (start, start)
    if t == "半小时前":
        return (now_ms - 1800_000, now_ms - 1800_000)
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?: (\d{1,2}):(\d{2}))?", t) or \
        re.fullmatch(r"()(\d{1,2})-(\d{1,2})(?: (\d{1,2}):(\d{2}))?", t)
    if m:
        y = int(m.group(1)) if m.group(1) else now.year
        try:
            day = datetime(y, int(m.group(2)), int(m.group(3)), tzinfo=zone)
        except ValueError:
            raise ToolError("bad_args", f"没有这个日期：{t}") from None
        if m.group(4) is not None:
            p = day.replace(hour=int(m.group(4)), minute=int(m.group(5)))
            return (_ms(p), _ms(p))
        return (_ms(day), _ms(day + timedelta(days=1)))
    # a day word, then maybe a part of the day, then maybe a clock time
    day, rest = None, t
    for w, back in sorted(DAYS.items(), key=lambda kv: -len(kv[0])):
        if rest.startswith(w):
            day, rest = (now - timedelta(days=back)).replace(hour=0, minute=0, second=0, microsecond=0), rest[len(w):]
            break
    if day is None:
        m = re.match(r"(上上|上|本|这)?(?:周|星期|礼拜)([一二三四五六日天])?", rest)
        if m and (m.group(1) or m.group(2)):
            monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
            monday -= timedelta(weeks={"上": 1, "上上": 2}.get(m.group(1) or "", 0))
            if m.group(2) is None:                     # 这周 / 上周: the whole week (up to now for this week)
                end = min(monday + timedelta(weeks=1), now)
                return (_ms(monday), _ms(end))
            day, rest = monday + timedelta(days=WEEKDAY[m.group(2)]), rest[m.end():]
            if day > now and not m.group(1):           # a bare 周五 later this week = the last one
                day -= timedelta(weeks=1)
    base = day or now.replace(hour=0, minute=0, second=0, microsecond=0)
    part = next((p for p in sorted(PARTS, key=len, reverse=True) if rest.startswith(p)), None)
    if part:
        rest = rest[len(part):]
    m = re.fullmatch(r"(\d{1,2}|[一两二三四五六七八九十]{1,3})(?:点|:|：)(半|\d{1,2}|[一二三四五]十[一二三四五六七八九]?)?分?", rest) if rest else None
    if m:
        h = int(_num(m.group(1)))
        mi = 30 if m.group(2) == "半" else int(_num(m.group(2))) if m.group(2) else 0
        if part in ("下午", "晚上", "傍晚", "夜里") and h < 12:
            h += 12
        p = base.replace(hour=min(h, 23), minute=min(mi, 59))
        return (_ms(p), _ms(p))
    if rest:
        raise ToolError("bad_args", f"看不懂时间「{t}」，可以写成 2026-11-18 09:00、1小时前、昨天下午")
    if part:
        a, b = PARTS[part]
        return (_ms(base + timedelta(hours=a)), _ms(base + timedelta(hours=b)))
    if day is not None:
        return (_ms(day), _ms(day + timedelta(days=1)))
    raise ToolError("bad_args", f"看不懂时间「{t}」")


def span(frm, to, now_ms, tz="Asia/Shanghai", default_ms=24 * 3600_000, max_ms=31 * 86400_000):
    """(start, end) from two time words; none = the last default_ms; frm alone = its own end, else now."""
    if not frm and not to:
        return (now_ms - default_ms, now_ms)
    if frm:
        a, b = parse(frm, now_ms, tz)
        lo, hi = a, (b if b > a else now_ms)
    else:
        lo, hi = None, None
    if to:
        c, d = parse(to, now_ms, tz)
        hi = d if d > c else c
        if lo is None:
            lo = hi - default_ms
    hi = min(hi, now_ms)
    if hi <= lo:
        raise ToolError("bad_args", "结束时间要在开始时间之后")
    if hi - lo > max_ms:
        raise ToolError("bad_args", f"一次最多查 {max_ms // 86400_000} 天")
    return (lo, hi)


def label(ms, tz="Asia/Shanghai", with_day=True):
    dt = datetime.fromtimestamp(ms / 1000, ZoneInfo(tz))
    return dt.strftime("%m-%d %H:%M" if with_day else "%H:%M")


# ---- future times (L4 2026-11-18: reminders and schedule) ----
FUTURE_DAYS = {"今天": 0, "今日": 0, "今晚": 0, "明天": 1, "明日": 1, "明早": 1, "明晚": 1, "后天": 2, "大后天": 3}


def future(text, now_ms, tz="Asia/Shanghai"):
    """A moment from now on as people say it -> epoch ms. 10分钟后 / 半小时后 / 2小时后 / 3天后 · 明天早上8点 / 今晚8点 /
    后天下午3点半 / 下周三上午10点 / 周五 / 8点（today, or tomorrow when 8点 has passed） · 2026-11-19 09:00 / 11-19 09:00."""
    zone = ZoneInfo(tz)
    now = datetime.fromtimestamp(now_ms / 1000, zone)
    t = re.sub(r"\s+", " ", str(text or "").strip())
    if not t:
        raise ToolError("bad_args", "时间是空的")
    if t in ("马上", "现在", "立刻"):
        return now_ms + 5_000
    m = re.fullmatch(r"(\d+(?:\.\d+)?|[一两二三四五六七八九十半]{1,3})个?(分钟|分|小时|钟头|天)(以?后|之后)", t)
    if m:
        n = _num(m.group(1)) if m.group(1) not in ("半",) else 0.5
        unit = {"分钟": 60_000, "分": 60_000, "小时": 3600_000, "钟头": 3600_000, "天": 86400_000}[m.group(2)]
        return now_ms + int(n * unit)
    if t in ("半小时后", "半个小时后", "半小时之后"):
        return now_ms + 1800_000
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2}) (\d{1,2}):(\d{2})", t) or \
        re.fullmatch(r"()(\d{1,2})-(\d{1,2}) (\d{1,2}):(\d{2})", t)
    if m:
        y = int(m.group(1)) if m.group(1) else now.year
        try:
            p = datetime(y, int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5)), tzinfo=zone)
        except ValueError:
            raise ToolError("bad_args", f"没有这个时间：{t}") from None
        if not m.group(1) and p < now:
            p = p.replace(year=y + 1)
        return _ms(p)
    day, rest, explicit = None, t, False
    for w, ahead in sorted(FUTURE_DAYS.items(), key=lambda kv: -len(kv[0])):
        if rest.startswith(w):
            day = (now + timedelta(days=ahead)).replace(hour=0, minute=0, second=0, microsecond=0)
            rest, explicit = rest[len(w):], True
            if w in ("今晚", "明晚"):
                rest = "晚上" + rest
            elif w == "明早":
                rest = "早上" + rest
            break
    if day is None:
        m = re.match(r"(下下|下|本|这)?(?:周|星期|礼拜)([一二三四五六日天])", rest)
        if m:
            monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
            monday += timedelta(weeks={"下": 1, "下下": 2}.get(m.group(1) or "", 0))
            day, rest, explicit = monday + timedelta(days=WEEKDAY[m.group(2)]), rest[m.end():], True
            if not m.group(1) and day + timedelta(days=1) <= now:          # a bare 周三 already gone: next week's
                day += timedelta(weeks=1)
    base = day or now.replace(hour=0, minute=0, second=0, microsecond=0)
    part = next((p for p in sorted(PARTS, key=len, reverse=True) if rest.startswith(p)), None)
    if part:
        rest = rest[len(part):]
    m = re.fullmatch(r"(\d{1,2}|[一两二三四五六七八九十]{1,3})(?:点|:|：)(半|一刻|三刻|\d{1,2}|[一二三四五]十[一二三四五六七八九]?)?分?", rest) \
        if rest else None
    if m:
        h = int(_num(m.group(1)))
        g = m.group(2)
        mi = 30 if g == "半" else 15 if g == "一刻" else 45 if g == "三刻" else int(_num(g)) if g else 0
        if part in ("下午", "晚上", "傍晚", "夜里") and h < 12:
            h += 12
        if part == "中午" and h < 6:
            h += 12
        p = base.replace(hour=min(h, 23), minute=min(mi, 59))
        if p <= now and not explicit:
            if part is None and h < 12 and p + timedelta(hours=12) > now:   # "8点" said at 19:00 = 20:00
                p += timedelta(hours=12)
            else:
                p += timedelta(days=1)
        if p <= now:
            raise ToolError("bad_args", f"「{t}」已经过去了")
        return _ms(p)
    if rest:
        raise ToolError("bad_args", f"看不懂时间「{t}」，可以说 10分钟后、明天早上8点、下周三下午3点")
    if part:                                              # a part alone: its start (明天早上 = 8:00 for 早上)
        a = {"早上": 8, "早晨": 8, "上午": 9, "中午": 12, "下午": 15, "傍晚": 18, "晚上": 20, "夜里": 22, "凌晨": 6, "白天": 9}[part]
        p = base + timedelta(hours=a)
        if p <= now and not explicit:
            p += timedelta(days=1)
        if p <= now:
            raise ToolError("bad_args", f"「{t}」已经过去了")
        return _ms(p)
    if day is not None:
        p = day + timedelta(hours=9)                      # a day alone: 9:00 that day
        return _ms(p if p > now else now + timedelta(minutes=1))
    raise ToolError("bad_args", f"看不懂时间「{t}」")
