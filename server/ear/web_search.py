"""联网搜索 for 围观 (owner 2026-11-16): "让其搜索新闻，效果不好" — the realtime model decided by itself whether to search
and, asked "今天黄仁勋有什么新闻吗" / "联机搜索一下…", never did ("黄仁勋是啥呀"). Owner: "搜索需要触发……建立一组可能用于
搜索的关键字，比如你查查，查一下，帮我找之类的".

So the ear triggers it: a heard sentence with one of SEARCH_WORDS / SEARCH_TOPICS (is_search_ask, in realtime_link) makes
the hub play a "我查查哈" filler, mute the model's instant reply and call search(question) here: qwen3.8-flash on
DashScope with web search forced on (one search per call), thinking off, a one-or-two-sentence spoken answer. The hub
then hands that text to the realtime session as a user text item and hello says it in her own words; nothing found
(error, timeout, empty) -> hello says she couldn't find it.

Measured 2026-11-16 on the server: 1.7-2.4 s, 3.4k-5.7k prompt tokens (the search results), ~35 output tokens.
Every call is one call.search record in the run log: cost = the search fee (prices.REALTIME_SEARCH_CNY_PER_CALL, CNY);
the token cost (list price, USD, as vision_burst / mind llm.PRICES) is in extra.token_cost_usd.
"""
import json
import re
import time
import urllib.error
import urllib.request

import ear_log

BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL = "qwen3.8-flash"
TIMEOUT_S = 8.0
MAX_TOKENS = 200
PRICE_USD_PER_M = (0.1126, 0.380025)            # as vision_burst.PRICE_USD_PER_M / mind llm.PRICES (list price)

# Trigger words (owner's examples first). Asking to look (realtime_link.LOOK_ASK) wins over these.
SEARCH_WORDS = ("你查查", "查查", "查一下", "查一查", "帮我查", "给我查", "查个", "搜一下", "搜搜", "搜一搜", "搜索",
                "帮我找", "找一下", "找找", "上网", "联网", "联机", "网上")
SEARCH_TOPICS = ("新闻", "天气", "比分", "股价", "汇率", "热搜", "最新消息", "最新进展")   # not 下雨 / 气温: "下雨了" is talk
NOT_SEARCH = re.compile(r"检查|查房|查岗|找茬|找事|找麻烦|找你|找我|找他|找她")


def is_search_word(norm):
    """norm: letters only (realtime_link.norm_text). True when it asks hello to look something up."""
    if not norm or NOT_SEARCH.search(norm):
        return False
    return any(w in norm for w in SEARCH_WORDS) or any(w in norm for w in SEARCH_TOPICS)


def system_text(today):
    return (f"今天是{today}（北京时间）。你在帮一个小机器人联网查资料，它会把你的话说给主人听。根据搜索结果，用一两句口语"
            "中文回答问题，只说查到的事实，带上时间或数字，不超过70字；搜索结果里没有的不要编，查不到就只回答：没查到。")


def cost_usd(usage):
    if not usage:
        return None
    return (usage.get("prompt_tokens", 0) * PRICE_USD_PER_M[0] + usage.get("completion_tokens", 0) * PRICE_USD_PER_M[1]) \
        / 1_000_000


def dashscope_search(key, base_url=BASE_URL, model=MODEL, timeout=TIMEOUT_S, today=None):
    """search(question) -> answer text or None (nothing found / failed). Blocking (the hub runs it on a thread);
    never raises; the key is only in the header (the run log keeps its first 7 characters)."""
    params = {"model": model, "temperature": 0, "max_tokens": MAX_TOKENS, "enable_thinking": False,
              "enable_search": True, "search_options": {"forced_search": True, "search_strategy": "turbo"}}

    def search(question):
        day = today() if callable(today) else time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))
        messages = [{"role": "system", "content": system_text(day)}, {"role": "user", "content": str(question)[:200]}]
        text, usage, ms, err = _call(messages)
        if text is not None and re.sub(r"[\W_]+", "", text) in ("没查到", "查不到"):
            text = None
        ear_log.emit("call.search", ok=err is None, err=err, ms=ms, model=model, key=key, usage=usage or None,
                     cost=ear_log.search_cost(1), sum=f"查「{str(question)[:30]}」→ {(text or '没查到')[:50]}",
                     extra={"token_cost_usd": cost_usd(usage), "found": text is not None, "query": str(question)[:200]},
                     body={"system": messages[0]["content"], "input": messages[1:], "output": text, "params": params})
        return text

    def _call(messages):
        body = {**params, "messages": messages}
        req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", method="POST",
                                     data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        t0 = time.monotonic()
        ms = lambda: int((time.monotonic() - t0) * 1000)     # noqa: E731
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                return None, {}, ms(), f"HTTP {e.code}"
        except Exception as e:                   # noqa: BLE001 - offline, slow: hello says she couldn't find it
            return None, {}, ms(), type(e).__name__
        usage = resp.get("usage") if isinstance(resp, dict) and isinstance(resp.get("usage"), dict) else {}
        try:
            text = resp["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return None, usage, ms(), "no choices"
        text = text.strip() if isinstance(text, str) else ""
        return (text, usage, ms(), None) if len(text) >= 2 else (None, usage, ms(), "empty text")
    return search
