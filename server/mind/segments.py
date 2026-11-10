"""Scene segments (spec 6.2): cut by rules from the owner state, summarized by the model.

A segment is a stretch with the same owner class (present / absent / unknown / off); a new class
must hold for 3 minutes before it cuts, so flicker stays inside the segment. Minutes mind never
processed make a "gap" segment. The cut is recomputed from the minute packages every time (cheap,
deterministic); only the summaries are stored (mind_segments, versioned).
"""
import json

from . import fusion, llm
from . import settings as settings_mod

MIN_MS = 60_000
CLASS = {"seen": "present", "just_left": "present", "likely_present": "present", "likely_absent": "absent",
         "unknown": "unknown", "sensors_off": "off"}
REFRESH_MS = 5 * MIN_MS
SUMMARY_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["title", "text", "confidence"],
                  "properties": {"title": {"type": "string"}, "text": {"type": "string"},
                                 "confidence": {"type": "string", "enum": ["high", "mid", "low"]}}}
SYSTEM = ("你在整理桌面宠物机器人 hello 一段连续时间里的情景。给出这一段的标题（不超过 20 个字）和一两句总结："
          "谁在、在做什么、环境怎样、有什么变化。只依据给出的每分钟情景，不编造；把握不足就说得保守一些。")


def owner_class(state):
    return CLASS.get(state, "unknown")


def cut(rows, hold_min=3):
    segs, cur, pending, last = [], None, None, None
    for r in rows:
        m, c = r["minute"], owner_class(r["owner_state"])
        if cur is None:
            cur = {"start": m, "end": None, "cls": c, "reason": "start"}
        elif m != last + MIN_MS:
            cur["end"] = last + MIN_MS
            segs.append(cur)
            segs.append({"start": last + MIN_MS, "end": m, "cls": "gap", "reason": "gap"})
            cur, pending = {"start": m, "end": None, "cls": c, "reason": "gap"}, None
        elif c == cur["cls"]:
            pending = None
        else:
            pending = pending if pending and pending[0] == c else (c, m, 0)
            pending = (pending[0], pending[1], pending[2] + 1)
            if pending[2] >= hold_min:
                cur["end"] = pending[1]
                segs.append(cur)
                cur = {"start": pending[1], "end": None, "cls": c, "reason": f"owner:{cur['cls']}→{c}"}
                pending = None
        last = m
    if cur:
        segs.append(cur)
    return segs


def summarize(env, store, s, seg, scenes, lane, now_ms):
    lines = []
    for d in scenes:
        sc = d.get("scene") or {}
        hm = settings_mod.local_time(d["minute"], s["tz"]).strftime("%H:%M")
        bits = "；".join(f"{e.get('dimension')}:{e.get('text')}" for e in sc.get("entries", [])[:6])
        lines.append(f"{hm} {sc.get('summary', '')}｜{bits}")
    model = s["fusion"]["model"]
    body = {"model": model, "temperature": 0, "max_tokens": 2000,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n".join(lines[-90:])}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "segment", "strict": True, "schema": SUMMARY_SCHEMA}}}
    resp, ms, err = fusion.CHAT(env["base_url"], env["api_key"], body)
    out = None
    if not err:
        try:
            out = json.loads(resp["choices"][0]["message"]["content"])
            out = {"title": str(out["title"])[:20], "text": str(out["text"])[:300], "confidence": out["confidence"]}
        except (KeyError, IndexError, TypeError, ValueError):
            err = "unparsable segment summary"
    usage = (resp or {}).get("usage") or {}
    run_id = store.put_run({"lane": lane, "minute": seg["start"], "step": "segment", "prompt": "segment-v1", "model": model,
                            "request": body, "response": resp, "parsed": out, "error": err, "latency_ms": ms, "usage": usage,
                            "cost_usd": llm.cost_usd(model, usage), "retries": 0, "at": now_ms})
    if out is None:
        return None
    store.put_segment_summary(lane, seg["start"], seg["end"], out, run_id, len(scenes), now_ms)
    return out


def update(env, store, s, day_start_ms, now_ms, lane="live", summarize_now=True):
    rows = [{"minute": r["minute"], "owner_state": r["owner_state"], "hearing_s": r["coverage"]["hearing_s"],
             "vision_s": r["coverage"]["vision_s"]} for r in store.timeline(lane, day_start_ms, now_ms)]
    segs = cut(rows)
    for seg in segs:
        existing = store.segment_summary(lane, seg["start"])
        seg["summary"] = existing["summary"] if existing else None
        if seg["cls"] == "gap" or not summarize_now or env is None or not s["fusion"]["enabled"]:
            continue
        scenes = [d for d in store.scenes_range(lane, seg["start"], seg["end"] or now_ms) if d.get("scene")]
        if not scenes:
            continue
        if seg["end"] is not None:
            need = existing is None or existing["scenes_count"] < len(scenes) or existing.get("end") != seg["end"]
        else:
            need = existing is None or (now_ms - existing["at"] >= REFRESH_MS and existing["scenes_count"] < len(scenes))
        if need:
            got = summarize(env, store, s, seg, scenes, lane, now_ms)
            if got:
                seg["summary"] = got
    return segs
