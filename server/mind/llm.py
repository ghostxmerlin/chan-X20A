"""aihubmix (OpenAI-compatible) chat call and list-price cost, shared by fusion, segments and probe."""
import json
import time
import urllib.error
import urllib.request

from .runlog import hello_log

PRICES = {"gpt-6-luna": (0.1, 0.5), "gpt-6.1-terra": (1.0, 5.0),   # USD per million tokens, list price
          "claude-haiku-5-5": (0.1, 0.5),                     # eye until 2026-11-15 (aihubmix list, 2026-11-11)
          "qwen3.8-flash": (0.1126, 0.380025)}                # eye since 2026-11-15 (aihubmix list price; DashScope direct)
PRICE_BASIS = "llm.PRICES 2026-11-15"                         # the run log's cost.basis: change with PRICES


def cost_usd(model, usage):
    if model not in PRICES or not usage:
        return None
    pin, pout = PRICES[model]
    return (usage.get("prompt_tokens", 0) * pin + usage.get("completion_tokens", 0) * pout) / 1_000_000


def chat(base_url, key, body, timeout=90):
    """(response json or None, latency ms, error text or None). Never raises. Every call is one run-log record
    (kind from the caller's hello_log.scope, else call.llm; see _record)."""
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", method="POST",
                                 data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read()), int((time.monotonic() - t0) * 1000), None
    except urllib.error.HTTPError as e:
        with e:
            out = None, int((time.monotonic() - t0) * 1000), f"HTTP {e.code}: {e.read()[:200].decode('utf-8', 'replace')}"
    except Exception as e:                       # noqa: BLE001 - reported, the caller decides
        out = None, int((time.monotonic() - t0) * 1000), f"{type(e).__name__}: {str(e)[:200]}"
    _record(key, body, *out)
    return out


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):                # parts: text + image_url (eye, look)
        return " ".join(p.get("text") or "" for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


def _record(key, body, resp, ms, err):
    """One run-log record of this call (docs/hello-log.md): model, ms, ok/err, usage, list-price cost, key
    fingerprint, the last user message's last 60 characters as sum, and the body: system texts, the other
    messages as they were sent (hello_log replaces data: images) and the reply text or tool_calls JSON."""
    try:
        if hello_log.get() is None:
            return
        messages = (body.get("messages") or []) if isinstance(body, dict) else []
        is_system = lambda m: isinstance(m, dict) and m.get("role") == "system"
        user = next((m for m in reversed(messages) if isinstance(m, dict) and m.get("role") == "user"), None)
        usage = (resp.get("usage") if isinstance(resp, dict) else None) or {}
        output = None
        try:
            msg = resp["choices"][0]["message"]
            output = json.dumps(msg["tool_calls"], ensure_ascii=False) if msg.get("tool_calls") else msg.get("content")
        except (KeyError, IndexError, TypeError):
            pass
        model = body.get("model")
        hello_log.emit(None if hello_log.current().get("kind") else "call.llm",
                       model=model, ms=ms, ok=err is None, err=err, usage=usage or None,
                       cost={"amount": cost_usd(model, usage), "cur": "USD", "est": True, "basis": PRICE_BASIS},
                       key=hello_log.key_fp(key), sum=_text(user.get("content"))[-60:] if user else None,
                       body={"system": [m.get("content") for m in messages if is_system(m)],
                             "input": [m for m in messages if not is_system(m)], "output": output})
    except Exception:                            # noqa: BLE001 - logging never touches the call
        pass
