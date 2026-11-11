"""Conversation notes (spec docs/superpowers/specs/2026-11-11-conversation-notes-design.md).

People's sentences (SenseVoice and 听悟, hello's own clips removed) are cut into conversations by rule:
a gap of more than 60 s ends one. luna writes a ~10-character title and ~50-character notes for the
running conversation, and on its first update says whether it continues one from earlier today.
"""
import json

from . import llm, package, prompts, selfvoice, settings as settings_mod

GAP_MS = 60_000                  # no one spoke for a minute: the conversation is over (user 2026-11-11)
CONTINUE_WINDOW_MS = 10 * 60_000  # the previous conversation is always a candidate within 10 minutes
CANDIDATES = 4
UNKNOWN = {"kind": "unknown", "name": None, "best": None, "score": None}


def _sounding(d):
    return bool((d.get("self") or {}).get("sounding"))


def new_sentences(db, records, since_ms, until_ms, seen):
    """People's sentences with wall time in [since_ms, until_ms), oldest first, minus `seen` refs."""
    w = records.window(since_ms, until_ms)
    out = []
    for d in w.get("speech", []):
        ref = ("speech", d.get("session"), d.get("id"))
        if ref in seen or not (d.get("text") or "").strip() or selfvoice.is_hello(d.get("text"), _sounding(d)):
            continue
        out.append({"ref": ref, "at": d["_wall"], "text": d["text"], "who": package._who(d)})
    for d in w.get("tingwu", []):
        if d.get("kind", "sentence") != "sentence":
            continue
        ref = ("tingwu", d.get("session"), d.get("n"))
        if ref in seen or not (d.get("text") or "").strip() or selfvoice.is_hello(d.get("text"), _sounding(d)):
            continue
        out.append({"ref": ref, "at": d["_wall"], "text": d["text"], "who": dict(UNKNOWN), "engine": "tingwu"})
    return sorted(out, key=lambda x: x["at"])


def segment(open_conv, sentences, now_ms, close_after=GAP_MS):
    """{"close": the open conversation is over, "append": its new sentences, "new": [[sentences], ...]}.
    Every group in "new" but the last is already over (a gap followed it)."""
    last = open_conv["last_ms"] if open_conv else None
    close, append, new = False, [], []
    for s in sentences:
        if last is not None and s["at"] - last <= GAP_MS:
            (new[-1] if new else append).append(s)
        else:
            if open_conv and not new and not close:
                close = True             # the open conversation ended before this sentence
            new.append([s])
        last = s["at"]
    if open_conv and not new and not append and now_ms - open_conv["last_ms"] > close_after:
        close = True                     # silence for a minute: over
    return {"close": close, "append": append, "new": new}


# ---------------------------------------------------------------- one update (luna) ----
CHAT = llm.chat                    # injectable in tests
NOTES_MODEL = "gpt-6-luna"
NOTES_PROMPT = "notes-v1"
BACKOFF_MIN = [1, 2, 5, 10]
MIN_MS = 60_000
RECENT_MAX = 20

NOTES_SCHEMA = {"type": "object", "additionalProperties": False,
                "required": ["continues", "title", "notes", "confidence"],
                "properties": {"continues": {"type": "string"}, "title": {"type": "string"}, "notes": {"type": "string"},
                               "confidence": {"type": "string", "enum": ["high", "mid", "low"]}}}

NOTES_SYSTEM = """你是桌面宠物机器人 hello 的听觉笔记员。hello 放在办公桌上，一直听着周围的人说话（hello 自己的声音已经去掉了）。你为“一段对话”写纪要：一段对话是一串相隔不超过一分钟的话。

【写什么】
- title：约 10 字的标题，说清是哪件事。
- notes：约 50 字的纪要：谁在谈（认得出就写，认不出写“两人”“几位同事”）、谈的是什么、说到的要点或结论。要点多了就压缩旧的，保持约 50 字。
- confidence：high / mid / low，按听到的内容有多清楚、多连贯来定。
- 归纳不出主题（零散寒暄、只有一两个词、听不清）时，title 和 notes 都留空，不要硬凑。
- 两个转写（SenseVoice 和听悟）常给出不同的字，可能是同一句话的两个版本；一致的部分才可信。远处的话常被听错，结合环境说明里的工作内容和常用词去理解。

【接续】
- 新对话开始时会给你今天之前的几段对话（候选）。如果新的话明显是在接着其中某一段谈同一件事，continues 写那一段的编号，纪要接着那一段往下写（说明“又谈到……”）；不是接续就让 continues 留空。只能写候选里给出的编号。
- 正在进行的对话不需要判断接续，continues 照抄给出的值。

只描述，不提建议，不编故事。"""


def _hms(ms, tz):
    return settings_mod.local_time(ms, tz).strftime("%H:%M:%S")


def _hm(ms, tz):
    return settings_mod.local_time(ms, tz).strftime("%H:%M")


def _bigrams(text):
    t = "".join(ch for ch in str(text or "") if ch.isalnum())
    return {t[i:i + 2] for i in range(len(t) - 1)}


def day_start(ms, tz):
    t = settings_mod.local_time(ms, tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(t.timestamp() * 1000)


def candidates(db, day_start_ms, before_ms, text):
    """Closed conversations of today to offer for a continuation (the previous one within 10 minutes always)."""
    closed = [c for c in db["conversations"].find({"start_ms": {"$gte": day_start_ms, "$lt": before_ms}}, {"_id": 0})
              if c.get("end_ms") is not None]
    closed.sort(key=lambda c: c["end_ms"])
    out = []
    if closed and before_ms - closed[-1]["end_ms"] <= CONTINUE_WINDOW_MS:
        out.append(closed[-1])
    words = _bigrams(text)
    scored = sorted(((len(words & _bigrams((c.get("title") or "") + (c.get("notes") or ""))), c) for c in closed
                     if c not in out and (c.get("notes") or c.get("title"))), key=lambda x: -x[0])
    out += [c for score, c in scored if score > 0][:CANDIDATES - len(out)]
    return out


def _line(x, s, owner):
    who = prompts._who(x["who"], owner, s.get("people", []))
    return f"{_hms(x['at'], s['tz'])} [{who}{'，听悟' if x.get('engine') == 'tingwu' else ''}]：{x['text']}"


def messages_for(s, conv, sentences, recent, cands, now_ms):
    tz = s["tz"]
    owner = {**s["owner"], "display": settings_mod.owner_name(s, now_ms)}
    L = []
    if (s.get("environment") or "").strip():
        L.append(f"环境：{s['environment'].strip()}")
    L.append(f"主人的称呼：{owner['display']}；现在：{settings_mod.local_label(now_ms, tz)}")
    if conv is None:
        L.append("【今天之前的对话（候选）】")
        L += [f"{c['cid']} {_hm(c['start_ms'], tz)}–{_hm(c['end_ms'], tz)} 标题：{c.get('title') or '（无）'}；纪要：{c.get('notes') or '（无）'}"
              for c in cands] or ["（没有）"]
        L.append("【新对话的话】")
    else:
        L.append(f"【这段对话目前的纪要】（{_hm(conv['start_ms'], tz)} 开始；continues：{conv.get('continues') or '无'}）")
        L.append(f"标题：{conv.get('title') or '（无）'}；纪要：{conv.get('notes') or '（无）'}")
        if recent:
            L.append("【最近的原话】")
            L += [_line(x, s, owner) for x in recent[-RECENT_MAX:]]
        L.append("【新的话】")
    L += [_line(x, s, owner) for x in sentences]
    return [{"role": "system", "content": NOTES_SYSTEM}, {"role": "user", "content": "\n".join(L)}]


def _parse(resp):
    if not resp:
        return None, "no response"
    try:
        out = json.loads(resp["choices"][0]["message"].get("content") or "")
    except (KeyError, IndexError, TypeError, ValueError) as e:
        return None, f"not valid JSON: {type(e).__name__}"
    missing = [k for k in NOTES_SCHEMA["required"] if k not in out] if isinstance(out, dict) else ["object"]
    return (None, f"JSON missing fields: {', '.join(missing)}") if missing else (out, None)


def _state(db):
    return db["notes_state"].find_one({"_id": "live"}) or {}


def _set_state(db, **fields):
    db["notes_state"].update_one({"_id": "live"}, {"$set": fields}, upsert=True)


def _save(db, conv, run_id, now_ms, changed, until_ms=None):
    db["conversations"].replace_one({"cid": conv["cid"]}, dict(conv), upsert=True)
    if changed:
        # until_ms: the minute boundary these notes cover (mind picks the version covering its minute)
        db["conversation_versions"].insert_one({k: conv.get(k) for k in ("cid", "version", "title", "notes", "confidence",
                                                                         "continues")} | {"run_id": run_id, "at": now_ms,
                                                                                          "until_ms": until_ms,
                                                                                          "refs": len(conv["refs"])})


def update(env, store, s, conv, sentences, now_ms, recent=None, until_ms=None, call=True):
    """Add `sentences` to `conv` (None: a new conversation) and rewrite its notes. The sentences always
    count as handled; a skipped or failed call leaves the notes as they were (the next update also gets
    the recent raw speech)."""
    db = store.db
    new = conv is None
    if new:
        first = sentences[0]["at"]
        conv = {"cid": f"c{first}", "start_ms": first, "last_ms": first, "end_ms": None, "refs": [], "continues": "",
                "title": "", "notes": "", "confidence": "low", "version": 0, "prompt": NOTES_PROMPT}
    conv = dict(conv, refs=list(conv["refs"]) + [list(x["ref"]) for x in sentences],
                last_ms=max([conv["last_ms"]] + [x["at"] for x in sentences]))
    if not call or store.cost_today(s["tz"], now_ms) >= s["budget_usd_per_day"] or (_state(db).get("backoff_until") or 0) > now_ms:
        _save(db, conv, None, now_ms, False)
        return conv
    cands = candidates(db, day_start(conv["start_ms"], s["tz"]), conv["start_ms"],
                       " ".join(x["text"] for x in sentences)) if new else []
    messages = messages_for(s, None if new else conv, sentences, recent or [], cands, now_ms)
    body = {"model": NOTES_MODEL, "messages": messages, "temperature": 0, "max_tokens": 4000, "reasoning_effort": "low",
            "response_format": {"type": "json_schema", "json_schema": {"name": "notes", "strict": True, "schema": NOTES_SCHEMA}}}
    resp, ms, err = CHAT(env["base_url"], env["api_key"], body)
    out, perr = _parse(resp) if not err else (None, err)
    usage, total_ms, retries = dict((resp or {}).get("usage") or {}), ms, 0
    if out is None:
        retries = 1
        body = dict(body, messages=messages + [{"role": "user", "content": f"上一次输出不合格（{perr}）。请严格按要求的 JSON 结构重新输出。"}])
        resp, ms, err = CHAT(env["base_url"], env["api_key"], body)
        total_ms += ms
        out, perr = _parse(resp) if not err else (None, err)
        for k, v in ((resp or {}).get("usage") or {}).items():
            if isinstance(v, (int, float)):
                usage[k] = usage.get(k, 0) + v
    run_id = store.put_run({"lane": "live", "minute": settings_mod.minute_floor(now_ms), "step": "notes", "prompt": NOTES_PROMPT,
                            "model": NOTES_MODEL, "request": body, "response": resp, "parsed": out, "error": None if out else perr,
                            "latency_ms": total_ms, "usage": usage, "cost_usd": llm.cost_usd(NOTES_MODEL, usage),
                            "retries": retries, "at": now_ms, "cid": conv["cid"]})
    if out is None:
        failures = (_state(db).get("failures") or 0) + 1
        _set_state(db, failures=failures, backoff_until=now_ms + BACKOFF_MIN[min(failures - 1, 3)] * MIN_MS,
                   last_error=str(perr)[:200])
        _save(db, conv, run_id, now_ms, False)
        return conv
    _set_state(db, failures=0, backoff_until=0)
    cont = out.get("continues") or ""
    if new:
        cont = cont if cont in {c["cid"] for c in cands} else ""
    else:
        cont = conv.get("continues") or ""
    conv = dict(conv, title=str(out.get("title") or "")[:40], notes=str(out.get("notes") or "")[:200],
                confidence=out.get("confidence") if out.get("confidence") in ("high", "mid", "low") else "low",
                continues=cont, version=conv["version"] + 1, updated_at=now_ms, run_id=run_id)
    _save(db, conv, run_id, now_ms, True, until_ms)
    return conv
