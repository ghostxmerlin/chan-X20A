"""Per-minute fusion (spec 3.3, 3.6, 4.5): gate, model call with one retry, run log, validation,
scene record; failures back off and never break the timeline.

A scene record (mind_scenes) per minute and lane: source model | carried | rules_only | failed.
"""
import hashlib
import json

from . import llm, prompts, validate

CHAT = llm.chat                       # injectable in tests
MIN_MS = 60_000
BACKOFF_MIN = [1, 2, 5, 10]


def _bin(seconds):
    return 0 if seconds <= 0 else (2 if seconds >= 60 else 1)


def content_fp(pkg, strong, eye_bin=None):
    """What the model would see, in bins (spec 3.3): owner state, coverage, who is in view, which kinds
    of sound, whether a person shape was seen, and the new sentences themselves (new words are new
    evidence). Record ids and face end times are left out, so a quiet minute matches the last one."""
    c = strong["coverage"]
    raw = [strong["owner"]["state"], _bin(c["hearing_s"]), _bin(c["vision_s"]),
           sorted((f.get("name") or "?") for f in pkg.get("faces", [])),
           sorted({g.get("kind") for g in pkg.get("sounds", [])}),
           (pkg.get("persons") or {}).get("seconds", 0) > 0,
           sorted([x["ref"].get("session"), x["ref"].get("id")] for x in pkg.get("speech", [])),
           bool(pkg.get("tingwu_only"))]
    if eye_bin is not None:             # v3 only (the prompt that shows e1): people count, not the eye wording
        raw.append(["eye", eye_bin])
    return hashlib.sha1(json.dumps(raw, ensure_ascii=False).encode()).hexdigest()[:16]


def decide(prev, pkg, strong, s, minute_ms, review_level, fp=None):
    """("call" | "carry", reason). review_level: re-checks in a row that found nothing new."""
    if prev is None:
        return "call", "first"
    if prev.get("owner_state") != strong["owner"]["state"]:
        return "call", "owner_state"
    called_with = prev["last_call_prompt"] if "last_call_prompt" in prev else prev.get("prompt")   # older records: prompt
    if called_with is not None and called_with != s["fusion"]["prompt"]:
        return "call", "prompt"                  # a new prompt version never carries a scene the old one wrote
    cov = strong["coverage"]["hearing_s"] + strong["coverage"]["vision_s"]
    if prev.get("coverage_sum", 0) == 0 and cov > 0:
        return "call", "sensors_on"
    fp = fp if fp is not None else content_fp(pkg, strong)
    if prev.get("last_call_minute") is None:
        return "call", "changed"
    since = (minute_ms - prev["last_call_minute"]) / MIN_MS
    review = s["fusion"]["review_after_min"][min(review_level, 3)]
    if fp == prev.get("last_call_fp"):
        return ("carry", "unchanged") if since < review else ("call", "review")
    return "call", "changed"


def _parse(resp):
    if not resp:
        return None, "no response"
    try:
        content = resp["choices"][0]["message"].get("content")
    except (KeyError, IndexError, TypeError):
        return None, "no choices in the response"
    if not content:
        return None, "empty content"
    try:
        out = json.loads(content)
    except ValueError as e:
        return None, f"not valid JSON: {e}"
    missing = [k for k in prompts.SCENE_SCHEMA["required"] if k not in out]
    if missing:
        return None, f"JSON missing fields: {', '.join(missing)}"
    return out, None


def recent_speech(store, lane, minute_ms, minutes=5, limit=30):
    """What people said in the minutes before this one (oldest first), for the topic summary."""
    out = []
    for k in range(minutes, 0, -1):
        doc = store.latest(lane, minute_ms - k * MIN_MS)
        for x in ((doc or {}).get("package") or {}).get("speech", []):
            if x.get("text"):
                out.append({"at": x["at"], "who": x["who"], "text": x["text"]})
    return out[-limit:]


def run_minute(env, store, s, settings_version, minute_ms, pkg, strong, neighbours, lane="live",
               force_prompt=None, force_model=None, now_ms=None, segments=None, package_version=None, revise=False):
    """revise: late records rewrote this minute's package — call again only if the content changed
    (returns None, writing nothing, when the minute has no scene yet or its content is the same)."""
    now_ms = now_ms if now_ms is not None else minute_ms + 2 * MIN_MS
    forced = force_prompt is not None or force_model is not None
    prev = store.last_scene_before("live" if lane == "rerun" else lane, minute_ms)
    level = prev.get("review_level", 0) if prev else 0
    prompt = prompts.resolve(force_prompt or s["fusion"]["prompt"])
    model = force_model or s["fusion"]["model"]
    # e1 counts as content binned to the people count (not the eye wording); a minute without an eye record
    # (no new frames) keeps the last count, so a settled picture is not a change.
    eye_bin = None
    if s.get("sources", {}).get("vision", True):
        eye_bin = (pkg["eye"]["result"].get("people_count") if pkg.get("eye") else (prev or {}).get("eye_bin"))
    base = {"source": None, "reason": None, "prompt": prompt, "model": model, "settings_version": settings_version,
            "run_id": None, "carried_from": None, "owner_state": strong["owner"]["state"],
            "coverage_sum": strong["coverage"]["hearing_s"] + strong["coverage"]["vision_s"],
            "content_fp": content_fp(pkg, strong, eye_bin), "eye_bin": eye_bin, "last_call_minute": prev.get("last_call_minute") if prev else None,
            "last_call_fp": prev.get("last_call_fp") if prev else None, "review_level": level,
            "last_call_prompt": (prev["last_call_prompt"] if "last_call_prompt" in prev else prev.get("prompt")) if prev else None,
            "scene": None, "checks": None, "package_version": package_version, "package_fingerprint": pkg.get("fingerprint")}

    if revise:
        existing = store.latest_scene(lane, minute_ms)
        if not existing or existing.get("content_fp") == base["content_fp"]:
            return None

    def carry(source, reason):
        rec = dict(base, source=source, reason=reason)
        if prev and prev.get("scene") is not None:      # carry the last model scene; name the minute it came from
            rec.update(scene=prev["scene"], carried_from=prev["minute"] if prev.get("source") == "model" else (prev.get("carried_from") or prev["minute"]))
        else:
            rec.update(source="rules_only")
        version = store.put_scene(lane, minute_ms, rec)
        return {**rec, "lane": lane, "minute": minute_ms, "version": version}

    if not forced and base["coverage_sum"] == 0:     # both senses off: nothing new to see, never pay for a call
        rec = dict(base, source="rules_only", reason="sensors_off")
        version = store.put_scene(lane, minute_ms, rec)
        return {**rec, "lane": lane, "minute": minute_ms, "version": version}
    if forced:
        action, reason = "call", "rerun"
    else:
        action, reason = ("call", "revised") if revise else decide(prev, pkg, strong, s, minute_ms, level, base["content_fp"])
        state = store.state()
        if action == "call" and (state.get("fusion_backoff_until") or 0) >= now_ms:   # a 1-min backoff skips the next minute
            action, reason = "carry", "backoff"
        if action == "call" and store.cost_today(s["tz"], now_ms) >= s["budget_usd_per_day"] and minute_ms % (30 * MIN_MS):
            action, reason = "carry", "budget"
    if action == "carry":
        return carry("carried", reason)

    messages = prompts.render(prompt, s, pkg, strong, prev, segments or [], neighbours or {})
    body = {"model": model, "messages": messages, "temperature": 0, "max_tokens": prompts.MAX_TOKENS.get(prompt, 4000),
            "response_format": prompts.schema_format()}
    if prompts.REASONING_EFFORT.get(prompt):
        body["reasoning_effort"] = prompts.REASONING_EFFORT[prompt]
    resp, ms, err = CHAT(env["base_url"], env["api_key"], body)
    out, perr = _parse(resp) if not err else (None, err)
    retries, total_ms, usage_sum = 0, ms, dict((resp or {}).get("usage") or {})
    if out is None:
        retries = 1
        body = dict(body, messages=messages + [{"role": "user", "content": f"上一次输出不合格（{perr}）。请严格按要求的 JSON 结构重新输出。"}])
        resp, ms, err = CHAT(env["base_url"], env["api_key"], body)
        total_ms += ms
        out, perr = _parse(resp) if not err else (None, err)
        for k, v in ((resp or {}).get("usage") or {}).items():
            if isinstance(v, (int, float)):
                usage_sum[k] = usage_sum.get(k, 0) + v
    run_id = store.put_run({"lane": lane, "minute": minute_ms, "step": "fusion", "prompt": prompt, "model": model,
                            "request": body, "response": resp, "parsed": out, "error": None if out else perr,
                            "latency_ms": total_ms, "usage": usage_sum, "cost_usd": llm.cost_usd(model, usage_sum),
                            "retries": retries, "at": now_ms})
    if out is None:
        if lane == "live":
            failures = (store.state().get("fusion_failures") or 0) + 1
            store.set_state(fusion_failures=failures,
                            fusion_backoff_until=now_ms + BACKOFF_MIN[min(failures - 1, 3)] * MIN_MS,
                            fusion_last_error=str(perr)[:200])
        base["run_id"] = run_id
        return carry("failed", "error")
    if lane == "live":
        store.set_state(fusion_failures=0, fusion_backoff_until=0)
    scene, checks = validate.apply(out, (prev or {}).get("scene", {}).get("entries", []) if prev and prev.get("scene") else [],
                                   pkg, strong, minute_ms)
    new_level = level + 1 if reason == "review" else 0
    rec = dict(base, source="model", reason=reason, run_id=run_id, scene=scene, checks=checks,
               last_call_minute=minute_ms, last_call_fp=base["content_fp"], last_call_prompt=prompt, review_level=new_level)
    version = store.put_scene(lane, minute_ms, rec)
    return {**rec, "lane": lane, "minute": minute_ms, "version": version}
