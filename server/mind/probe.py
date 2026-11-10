"""aihubmix capability probe for mind (phase 0, run by hand; costs a few cents).

For each model × output mode, asks a tiny scene question about a few real minute packages, and once
about a photo; records whether the call worked, whether the answer parsed, latency and token use into
hello.mind_probe, and prints a table.
  python -m mind.probe --models gpt-6-luna,gpt-6.1-terra --minutes 5 --image photo.jpg
API_KEY / BASE_URL come from the hubmix env file on the server; never printed.
"""
import argparse
import base64
import json
import os
import re
import time
import urllib.error
import urllib.request

from . import settings

MODES = ["json_schema", "tools", "json_object", "plain"]
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["owner_present", "activity", "people_count"],
          "properties": {"owner_present": {"type": "string", "enum": ["yes", "no", "unknown"]},
                         "activity": {"type": "string"}, "people_count": {"type": "integer"}}}
PRICES = {"gpt-6-luna": (0.1, 0.5), "gpt-6.1-terra": (1.0, 5.0)}     # USD per million tokens, list price
SYSTEM = "你在帮一个桌面机器人整理它对周围的感知。只根据给出的内容回答，不确定就答 unknown。"
TASK = "根据下面这一分钟的感知内容，回答：主人在不在（owner_present: yes/no/unknown）、旁边的人在做什么（activity，一句中文）、看到或听到大约几个人（people_count）。"
IMAGE_TASK = "这是机器人摄像头拍的一张低分辨率照片。回答：画面里有没有人（owner_present 填 unknown，因为你不认识主人）、在做什么（activity）、几个人（people_count）。"


def request_body(model, mode, prompt, image_b64=None):
    text = prompt
    if mode == "json_object":
        text += "\n只输出一个 JSON 对象，字段 owner_present、activity、people_count。"
    elif mode == "plain":
        text += "\n把答案写成 JSON（字段 owner_present、activity、people_count），放在 <json>…</json> 里。"
    content = text if image_b64 is None else [
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + image_b64}}]
    body = {"model": model, "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}],
            "temperature": 0, "max_tokens": 300}
    if mode == "json_schema":
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "scene", "strict": True, "schema": SCHEMA}}
    elif mode == "tools":
        body["tools"] = [{"type": "function", "function": {"name": "scene", "description": "report the scene",
                                                           "parameters": SCHEMA}}]
        body["tool_choice"] = {"type": "function", "function": {"name": "scene"}}
    elif mode == "json_object":
        body["response_format"] = {"type": "json_object"}
    return body


def _valid(obj):
    return (isinstance(obj, dict) and obj.get("owner_present") in ("yes", "no", "unknown")
            and isinstance(obj.get("activity"), str)
            and isinstance(obj.get("people_count"), int) and not isinstance(obj.get("people_count"), bool))


def parse(mode, response_json):
    """The answer as a dict when it matches SCHEMA, else None."""
    try:
        msg = response_json["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return None
    if mode == "tools":
        calls = msg.get("tool_calls") or []
        raw = calls[0].get("function", {}).get("arguments") if calls else None
    else:
        raw = msg.get("content")
        if isinstance(raw, str) and mode == "plain":
            m = re.search(r"<json>(.*?)</json>", raw, re.S)
            raw = m.group(1) if m else None
    if not isinstance(raw, str):
        return None
    raw = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", raw.strip())
    try:
        obj = json.loads(raw)
    except ValueError:
        return None
    return obj if _valid(obj) else None


def cost_usd(model, usage):
    if model not in PRICES or not usage:
        return None
    pin, pout = PRICES[model]
    return (usage.get("prompt_tokens", 0) * pin + usage.get("completion_tokens", 0) * pout) / 1_000_000


def prompt_from(pkg, strong):
    lines = [TASK, f"时间：{strong.get('time', {}).get('label', '')}；规则判断的主人状态：{strong.get('owner', {}).get('state', '')}"]
    for x in pkg.get("speech", [])[:20]:
        who = x["who"].get("name") or x["who"].get("kind")
        lines.append(f"说话（{who}）：{x['text']}")
    for f in pkg.get("faces", []):
        lines.append(f"画面里的脸：{f.get('name') or '未认出'}")
    for g in pkg.get("sounds", []):
        lines.append(f"声音：{g.get('label') or g.get('kind')} × {g.get('count')}")
    return "\n".join(lines)


def call(base_url, key, body, timeout=60):
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", method="POST",
                                 data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read()), int((time.monotonic() - t0) * 1000), None
    except urllib.error.HTTPError as e:
        with e:
            return None, int((time.monotonic() - t0) * 1000), f"HTTP {e.code}: {e.read()[:200].decode('utf-8', 'replace')}"
    except Exception as e:                       # noqa: BLE001 - recorded, not retried
        return None, int((time.monotonic() - t0) * 1000), f"{type(e).__name__}: {str(e)[:200]}"


def main():
    ap = argparse.ArgumentParser(description="probe aihubmix models for mind")
    ap.add_argument("--hubmix-env", default=os.path.expanduser("~/aidea/agent/.env"))
    ap.add_argument("--models", default="gpt-6-luna,gpt-6.1-terra")
    ap.add_argument("--minutes", type=int, default=5)
    ap.add_argument("--image")
    a = ap.parse_args()
    env = settings.load_env(a.hubmix_env)
    key, base = env.get("API_KEY"), env.get("BASE_URL", "https://aihubmix.com/v1")
    if not key or not env.get("MONGO_URI"):
        raise SystemExit("API_KEY or MONGO_URI missing in the hubmix env file")
    from pymongo import MongoClient
    db = MongoClient(env["MONGO_URI"], serverSelectionTimeoutMS=5000)["hello"]
    docs = list(db.mind_packages.find({"lane": "live", "package.speech.0": {"$exists": True}}, {"_id": 0})
                .sort("minute", -1).limit(a.minutes))
    image = base64.b64encode(open(a.image, "rb").read()).decode() if a.image else None
    rows = []
    for model in a.models.split(","):
        for mode in MODES:
            jobs = [("text", prompt_from(d["package"], d["strong"]), None, d["minute"]) for d in docs]
            if image:
                jobs.append(("image", IMAGE_TASK, image, None))
            for kind, prompt, img, minute in jobs:
                resp, ms, err = call(base, key, request_body(model, mode, prompt, img))
                parsed = parse(mode, resp) if resp else None
                usage = (resp or {}).get("usage") or {}
                row = {"model": model, "mode": mode, "kind": kind, "minute": minute, "ok": resp is not None,
                       "parsed": parsed, "latency_ms": ms, "usage": usage, "cost_usd": cost_usd(model, usage),
                       "error": err, "at": int(time.time() * 1000)}
                db.mind_probe.insert_one(dict(row))
                rows.append(row)
    print(f"{'model':16} {'mode':12} {'kind':6} {'ok':>5} {'parsed':>7} {'ms':>6} {'in':>6} {'out':>5} {'$':>9}  error")
    groups = {}
    for r in rows:
        groups.setdefault((r["model"], r["mode"], r["kind"]), []).append(r)
    for (model, mode, kind), g in groups.items():
        n = len(g)
        avg = lambda f: sum(f(r) for r in g) / n          # noqa: E731
        cost = sum(r["cost_usd"] or 0 for r in g)
        err = next((r["error"] for r in g if r["error"]), "")
        print(f"{model:16} {mode:12} {kind:6} {sum(r['ok'] for r in g):>2}/{n:<2} {sum(bool(r['parsed']) for r in g):>4}/{n:<2}"
              f" {avg(lambda r: r['latency_ms']):>6.0f} {avg(lambda r: r['usage'].get('prompt_tokens', 0)):>6.0f}"
              f" {avg(lambda r: r['usage'].get('completion_tokens', 0)):>5.0f} {cost:>9.5f}  {err[:80]}")


if __name__ == "__main__":
    main()
