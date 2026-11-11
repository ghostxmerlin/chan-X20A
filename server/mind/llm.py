"""aihubmix (OpenAI-compatible) chat call and list-price cost, shared by fusion, segments and probe."""
import json
import time
import urllib.error
import urllib.request

PRICES = {"gpt-6-luna": (0.1, 0.5), "gpt-6.1-terra": (1.0, 5.0),   # USD per million tokens, list price
          "claude-haiku-5-5": (0.1, 0.5)}                     # eye (aihubmix list, 2026-11-11)


def cost_usd(model, usage):
    if model not in PRICES or not usage:
        return None
    pin, pout = PRICES[model]
    return (usage.get("prompt_tokens", 0) * pin + usage.get("completion_tokens", 0) * pout) / 1_000_000


def chat(base_url, key, body, timeout=90):
    """(response json or None, latency ms, error text or None). Never raises."""
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
    except Exception as e:                       # noqa: BLE001 - reported, the caller decides
        return None, int((time.monotonic() - t0) * 1000), f"{type(e).__name__}: {str(e)[:200]}"
