"""深入研究 (deep research, 2026-11-18): hello's background research jobs (tools deep_research / research_status,
registry config/tools/registry.json, rules docs/hello-tools.md).

One job = plan -> rounds of web search + reading -> a final Chinese answer: a spoken summary (at most 3 sentences,
what hello says) and a written report, with sources. A job runs in the background (1 worker thread by default,
settings research.max_jobs 1–2) and is one document in Mongo hello.research:

  {_id, topic, depth, asked_by, at, state queued/running/done/failed/cancelled, steps[], answer{spoken, report},
   sources[{title, url}], cost_cny, cap_cny, model, trace, started_at, finished_at, stopped, error, reported,
   reported_at}

Model: aihubmix's sol (settings research.model, default gpt-6.2-sol) through the OpenAI Responses API
(POST {base_url}/responses). Web search: the Responses API's own web_search tool on the same model (aihubmix bills it
per search request, prices.AIHUBMIX_SEARCH_USD_PER_CALL); the backend is pluggable (ResponsesWebSearch, or
ChatSearchModel for aihubmix's *-search chat models such as gemini-3-flash-preview-search).

Money (owner 2026-11-18): every model call is priced from its token usage (+ search fee) with prices.AIHUBMIX_*,
added to the job's cost_cny and logged as one run-log record call.research.<step> with a cost, so it counts into the
one daily cap (mind/pause.py) like every other paid call. Per job at most research.cap_cny_per_task (default 2 CNY):
before each call the job checks the daily cap (the budget pause) and what is left of its own cap (keeping a reserve
for the final answer); a search that no longer fits ends the searching, an answer that no longer fits (or a spent
cap) ends the job with what was found so far, unsorted. stopped says why: task_cap / daily_cap.

When a job ends (done or failed, not cancelled) the runner calls report(job) once (default: nothing; mind wires it
to hello speaking + a screen word) and marks the job reported.
"""
import json
import queue
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

from . import pause
from .runlog import hello_log
from .tools import ToolError

try:
    import prices                                # ~/soul-mind/prices.py (mind's deploy ships it); server/common here
except ImportError:                              # noqa: no prices = no model with a price = research refuses to start
    prices = None

COLL = "research"
STATES = ("queued", "running", "done", "failed", "cancelled")
STATE_ZH = {"queued": "排队中", "running": "正在研究", "done": "做完了", "failed": "没做成", "cancelled": "取消了"}
STOP_ZH = {"task_cap": "这次研究的钱用到上限了", "daily_cap": "今天的花费到上限了", "final_failed": "最后整理没成功"}
DEFAULTS = {"model": "gpt-6.2-sol", "cap_cny_per_task": 2.0, "max_jobs": 1}
DEPTH = {"quick": {"questions": 2, "rounds": 1, "more": 0},
         "normal": {"questions": 3, "rounds": 2, "more": 2},
         "deep": {"questions": 4, "rounds": 3, "more": 3}}
MAX_QUEUED = 5
SEARCH_EST_CNY = 0.4        # a search call before the job has measured one (~8k in + 2k out + 2 searches at sol prices)
SEARCH_MAX_OUT = 2500
PLAN_MAX_OUT = 1200
FINAL_MAX_OUT = 4000
FINAL_MIN_OUT = 800
STEP_TEXT = 4000            # characters of one search's findings kept in the job
BASIS = "prices.AIHUBMIX_USD_PER_M + AIHUBMIX_SEARCH_USD_PER_CALL 2026-11-18"
BJ = timezone(timedelta(hours=8))


# ---- price ---------------------------------------------------------------------------------------------------------

def priced(model):
    return prices is not None and model in getattr(prices, "AIHUBMIX_USD_PER_M", {})


def cost_usd(model, usage, searches=0):
    """List-price USD of one call from its usage (Responses or chat form) and its search requests; None = no price."""
    if not priced(model):
        return None
    u = usage or {}
    tin = int(u.get("input_tokens", u.get("prompt_tokens", 0)) or 0)
    tout = int(u.get("output_tokens", u.get("completion_tokens", 0)) or 0)
    det = u.get("input_tokens_details") or u.get("prompt_tokens_details") or {}
    cached = min(int(det.get("cached_tokens") or 0), tin)
    long = getattr(prices, "AIHUBMIX_USD_PER_M_LONG", {}).get(model)
    pin, pcached, pout = (long if long and tin > getattr(prices, "AIHUBMIX_LONG_CONTEXT_TOKENS", 272_000)
                          else prices.AIHUBMIX_USD_PER_M[model])
    fee = getattr(prices, "AIHUBMIX_SEARCH_USD_PER_CALL", {}).get(model, 0.0)
    return ((tin - cached) * pin + cached * pcached + tout * pout) / 1_000_000 + searches * fee


def _usd_per_token(model):
    pin, _, pout = prices.AIHUBMIX_USD_PER_M[model]
    return pin / 1_000_000, pout / 1_000_000


# ---- the Responses API -----------------------------------------------------------------------------------------------

def post_json(env, path, body, timeout=240):
    """POST {base_url}{path} -> (response json or None, ms, error text or None). Never raises. No run-log record here:
    the job writes one per call with its cost (_Run._account)."""
    req = urllib.request.Request(env["base_url"].rstrip("/") + path, method="POST", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {env['api_key']}", "Content-Type": "application/json"})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read()), int((time.monotonic() - t0) * 1000), None
    except urllib.error.HTTPError as e:
        with e:
            return None, int((time.monotonic() - t0) * 1000), f"HTTP {e.code}: {e.read()[:200].decode('utf-8', 'replace')}"
    except Exception as e:                       # noqa: BLE001 - reported, the job decides
        return None, int((time.monotonic() - t0) * 1000), f"{type(e).__name__}: {str(e)[:200]}"


def responses_call(env, body, timeout=240):
    """The OpenAI Responses API on aihubmix: POST {base_url}/responses."""
    return post_json(env, "/responses", body, timeout)


def _dedup(sources):
    seen, out = set(), []
    for s in sources:
        if s.get("url") and s["url"] not in seen:
            seen.add(s["url"])
            out.append(s)
    return out


def parse_output(resp):
    """A Responses API reply -> (text, sources [{title, url}] from url_citation annotations, web search calls)."""
    text, sources, searches = [], [], 0
    for item in (resp or {}).get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "web_search_call":
            searches += 1                        # every search call is billed (open_page counted too: on the safe side)
        elif item.get("type") == "message":
            for c in item.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "output_text":
                    text.append(c.get("text") or "")
                    for a in c.get("annotations") or []:
                        if isinstance(a, dict) and a.get("type") == "url_citation" and a.get("url"):
                            sources.append({"title": str(a.get("title") or a["url"])[:120], "url": str(a["url"])[:500]})
    return "".join(text).strip(), _dedup(sources), searches


# ---- search backends (pluggable): search(topic, question, max_out) -> dict, never raises ------------------------------

SEARCH_INSTRUCTIONS = ("你是研究助手。用联网搜索查下面这个小问题，读几篇可靠的来源（官方、论文、权威媒体优先），然后用中文写 3–8 条"
                       "要点：具体的事实、数字、日期；来源之间有分歧就写出分歧。只写查到的，不要编；查不到就直说查不到。")


def _search_input(topic, question):
    return f"总问题：{topic}\n这次要查：{question}\n今天是 {datetime.now(BJ).strftime('%Y-%m-%d')}。"


class ResponsesWebSearch:
    """The model itself searches and reads: Responses API + the web_search tool (aihubmix: per-request fee). The tool's
    type name is tried in order (web_search, then web_search_preview, which aihubmix's docs show) when the first is
    refused with HTTP 400; the one that worked is kept."""
    def __init__(self, send, model, tool_types=("web_search", "web_search_preview"), effort="low"):
        self.send, self.model, self.tool_types, self.effort = send, model, list(tool_types), effort

    def search(self, topic, question, max_out):
        body = None
        for i, tool in enumerate(self.tool_types):
            body = {"model": self.model, "instructions": SEARCH_INSTRUCTIONS, "input": _search_input(topic, question),
                    "tools": [{"type": tool}], "max_output_tokens": max_out, "reasoning": {"effort": self.effort}}
            resp, ms, err = self.send(body)
            if err and err.startswith("HTTP 400") and i + 1 < len(self.tool_types):
                continue
            if i:
                self.tool_types = self.tool_types[i:] + self.tool_types[:i]
            text, sources, searches = parse_output(resp) if resp else ("", [], 0)
            return {"text": text, "sources": sources, "searches": searches, "usage": (resp or {}).get("usage") or {},
                    "model": self.model, "ms": ms, "err": err or (None if text else "没有查到内容"), "body": body}
        return {"text": "", "sources": [], "searches": 0, "usage": {}, "model": self.model, "ms": 0,
                "err": "no search tool", "body": body}


class ChatSearchModel:
    """An aihubmix *-search chat model (it always searches before answering): POST {base_url}/chat/completions.
    One search request is counted per call (aihubmix does not report them). post(path, body) -> (resp, ms, err)."""
    def __init__(self, env, model="gemini-3-flash-preview-search", post=None):
        self.model = model
        self.post = post or (lambda path, body: post_json(env, path, body, timeout=120))

    def search(self, topic, question, max_out):
        body = {"model": self.model, "max_tokens": max_out,
                "messages": [{"role": "system", "content": SEARCH_INSTRUCTIONS},
                             {"role": "user", "content": _search_input(topic, question)}]}
        resp, ms, err = self.post("/chat/completions", body)
        text, sources = "", []
        try:
            msg = resp["choices"][0]["message"]
            text = (msg.get("content") or "").strip()
            for a in msg.get("annotations") or []:
                c = a.get("url_citation") or a
                if c.get("url"):
                    sources.append({"title": str(c.get("title") or c["url"])[:120], "url": str(c["url"])[:500]})
        except (KeyError, IndexError, TypeError):
            pass
        return {"text": text, "sources": _dedup(sources), "searches": 1 if resp else 0,
                "usage": (resp or {}).get("usage") or {}, "model": self.model, "ms": ms,
                "err": err or (None if text else "没有查到内容"), "body": body}


# ---- one job ---------------------------------------------------------------------------------------------------------

PLAN_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["questions"],
               "properties": {"questions": {"type": "array", "items": {"type": "string"}}}}
REVIEW_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["enough", "questions"],
                 "properties": {"enough": {"type": "boolean"}, "questions": {"type": "array", "items": {"type": "string"}}}}
FINAL_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["spoken", "report", "sources"],
                "properties": {"spoken": {"type": "string"}, "report": {"type": "string"},
                               "sources": {"type": "array", "items": {"type": "integer"}}}}


def three_sentences(text, limit=200):
    parts = [p for p in re.split(r"(?<=[。！？!?])", (text or "").strip()) if p.strip()]
    return "".join(parts[:3]).strip()[:limit]


class Stop(Exception):
    def __init__(self, why):
        super().__init__(why)
        self.why = why


class _Run:
    def __init__(self, runner, doc):
        self.r, self.doc = runner, doc
        self.id, self.topic, self.model = doc["_id"], doc["topic"], doc["model"]
        self.cap = float(doc["cap_cny"])
        self.spent = 0.0
        self.steps, self.sources, self.findings = [], [], []      # findings: [(question, text)]
        self.search_costs = []
        self.stopped = None

    # -- money --
    def to_cny(self, usd):
        return usd * self.r.usd_cny()

    def hard_stop(self):
        """Raise Stop when the job may not spend anything more."""
        if self.r.cancelled(self.id):
            raise Stop("cancelled")
        if pause.budget_stop(self.r.db, self.r.now()):
            raise Stop("daily_cap")
        if self.r.day_left_fn is not None:
            left = self.r.day_left_fn()
            if left is not None and left <= 0:
                raise Stop("daily_cap")
        if self.spent >= self.cap:
            raise Stop("task_cap")

    def left(self):
        left = self.cap - self.spent
        if self.r.day_left_fn is not None:
            day = self.r.day_left_fn()
            if day is not None:
                left = min(left, day)
        return left

    def final_reserve(self, out_tokens=FINAL_MIN_OUT * 2):
        pin, pout = _usd_per_token(self.model)
        return self.to_cny((self._final_chars() + 1500) * pin + out_tokens * pout)

    def _final_chars(self):
        return sum(len(t) + len(q) for q, t in self.findings) + 300 * len(self.sources) + 1000

    # -- calls --
    def _account(self, step, model, usage, searches, ms, err, body, text, question):
        usd = cost_usd(model, usage, searches)
        cny = round(self.to_cny(usd or 0.0), 5)
        self.spent += cny
        try:
            hello_log.emit(f"call.research.{step}", trace=self.doc.get("trace"), model=model, ms=ms, ok=err is None,
                           err=err, usage=usage or None,
                           cost={"amount": usd, "cur": "USD", "est": True, "basis": BASIS},
                           key=hello_log.key_fp(self.r.env["api_key"]) if self.r.env else None,
                           sum=(question or self.topic)[:60],
                           extra={"job": self.id, "searches": searches},
                           body={"system": [body.get("instructions")] if body and body.get("instructions") else None,
                                 "input": [body.get("input") or body.get("messages")] if body else None,
                                 "output": (text or "")[:2000],
                                 "params": {k: v for k, v in (body or {}).items()
                                            if k not in ("instructions", "input", "messages")} or None})
        except Exception:                        # noqa: BLE001 - logging never touches the job
            pass
        return cny

    def _json(self, step, instructions, text_in, schema, max_out, effort="low"):
        body = {"model": self.model, "instructions": instructions, "input": text_in, "max_output_tokens": max_out,
                "reasoning": {"effort": effort},
                "text": {"format": {"type": "json_schema", "name": step, "strict": True, "schema": schema}}}
        resp, ms, err = self.r.send(body)
        text = parse_output(resp)[0] if resp else ""
        out = None
        if resp and not err:
            try:
                out = json.loads(text)
                if not isinstance(out, dict):
                    raise ValueError
            except ValueError:
                out, err = None, "invalid model JSON"
        cny = self._account(step, self.model, (resp or {}).get("usage") or {}, 0, ms, err, body, text, None)
        return out, cny, ms, err

    def step(self, kind, **fields):
        self.steps.append({"kind": kind, "at": self.r.now(), **fields})
        self.r._set(self.id, steps=self.steps, sources=self.sources, cost_cny=round(self.spent, 4))

    # -- the loop --
    def plan(self, n):
        self.hard_stop()
        out, cny, ms, err = self._json(
            "plan", f"你是机器人 hello 的研究助手。主人让 hello 研究一个问题。把它拆成最多 {n} 个具体的、可以上网查的小问题"
                    "（中文，每个一句话），按重要性排序。", f"主人的问题：{self.topic}", PLAN_SCHEMA, PLAN_MAX_OUT)
        qs = [str(q).strip()[:120] for q in ((out or {}).get("questions") or []) if str(q).strip()][:n]
        self.step("plan", questions=qs or [self.topic], cost_cny=cny, ms=ms, err=err)
        return qs or [self.topic]

    def search(self, question, rnd):
        self.hard_stop()
        est = (sum(self.search_costs) / len(self.search_costs)) if self.search_costs else SEARCH_EST_CNY
        if self.left() < est + self.final_reserve():
            raise Stop("task_cap_soft")
        res = self.r.search.search(self.topic, question, SEARCH_MAX_OUT)
        cny = self._account("search", res.get("model") or self.model, res.get("usage") or {}, res.get("searches") or 0,
                            res.get("ms"), res.get("err"), res.get("body"), res.get("text"), question)
        if cny > 0:                              # a failed call costs nothing and says nothing about the next one
            self.search_costs.append(cny)
        text = (res.get("text") or "")[:STEP_TEXT]
        new = [s for s in res.get("sources") or [] if s["url"] not in {x["url"] for x in self.sources}]
        self.sources.extend(new)
        if text and not res.get("err"):
            self.findings.append((question, text))
        self.step("search", round=rnd + 1, q=question, text=text, sources=len(res.get("sources") or []),
                  searches=res.get("searches") or 0, cost_cny=cny, ms=res.get("ms"), err=res.get("err"))

    def notes(self):
        return "\n\n".join(f"【{q}】\n{t}" for q, t in self.findings)

    def review(self, k):
        self.hard_stop()
        if self.left() < SEARCH_EST_CNY + self.final_reserve():
            raise Stop("task_cap_soft")
        asked = [s["q"] for s in self.steps if s["kind"] == "search"]
        out, cny, ms, err = self._json(
            "review", f"下面是到目前为止查到的要点。判断回答主人的问题是否已经够了；不够的话，给出最多 {k} 个还需要查的新小问题"
                      "（不要重复已经查过的）。够了就 enough=true、questions 为空。",
            f"主人的问题：{self.topic}\n已经查过：{'；'.join(asked)}\n\n{self.notes()}", REVIEW_SCHEMA, PLAN_MAX_OUT)
        qs = [] if out is None or out.get("enough") else \
            [str(q).strip()[:120] for q in out.get("questions") or [] if str(q).strip() and q not in asked][:k]
        self.step("review", enough=not qs, questions=qs, cost_cny=cny, ms=ms, err=err)
        return qs

    def final(self):
        """-> answer dict, or None when it no longer fits the money left (then the caller answers with the findings)."""
        try:
            self.hard_stop()
        except Stop as s:
            if s.why == "cancelled":
                raise
            self.stopped = s.why
            return None
        pin, pout = _usd_per_token(self.model)
        left_usd = self.left() / self.r.usd_cny() - self._final_chars() * pin
        out_tokens = min(FINAL_MAX_OUT, int(left_usd / pout)) if left_usd > 0 else 0
        if out_tokens < FINAL_MIN_OUT:
            self.stopped = self.stopped or "task_cap"
            return None
        listing = "\n".join(f"[{i + 1}] {s['title']} {s['url']}" for i, s in enumerate(self.sources))
        out, cny, ms, err = self._json(
            "final", "根据查到的要点，用中文回答主人的问题。spoken：hello 要说出来的口头总结，最多 3 句、一共不超过 120 字，"
                     "口语，不念网址；report：书面报告，先给结论，再分点讲依据、分歧和不确定的地方，600–1500 字；"
                     "sources：报告用到的来源编号（来源列表里的数字）。只根据要点，不要编。",
            f"主人的问题：{self.topic}\n\n要点：\n{self.notes()}\n\n来源列表：\n{listing or '（没有）'}",
            FINAL_SCHEMA, out_tokens, effort="medium")
        self.step("final", cost_cny=cny, ms=ms, err=err)
        if out is None or not str(out.get("spoken") or "").strip():
            self.stopped = self.stopped or "final_failed"
            return None
        used = [self.sources[i - 1] for i in out.get("sources") or [] if isinstance(i, int) and 0 < i <= len(self.sources)]
        return {"spoken": three_sentences(out["spoken"]), "report": str(out.get("report") or "").strip(),
                "sources": _dedup(used) or self.sources[:10]}

    def unsorted(self):
        """The answer when the money ran out before the final write-up: what was found, as it is."""
        why = STOP_ZH.get(self.stopped, "研究提前停了")
        first = three_sentences(self.findings[0][1], 120) if self.findings else ""
        return {"spoken": three_sentences(f"{why}，先停了。查到的第一点：{first}" if first else f"{why}，先停了。"),
                "report": f"（{why}，下面是已经查到的内容，没有整理）\n\n{self.notes()}", "sources": self.sources[:10]}

    def go(self):
        d = DEPTH.get(self.doc.get("depth"), DEPTH["normal"])
        try:
            pending = self.plan(d["questions"])
            for rnd in range(d["rounds"]):
                for q in pending:
                    self.search(q, rnd)
                if rnd + 1 >= d["rounds"] or not d["more"]:
                    break
                pending = self.review(d["more"])
                if not pending:
                    break
        except Stop as s:
            if s.why == "cancelled":
                raise
            self.stopped = "task_cap" if s.why == "task_cap_soft" else s.why
            self.step("stop", why=self.stopped)
        answer = self.final()
        if answer is None:
            if not self.findings:
                return "failed", None, STOP_ZH.get(self.stopped) or "没有查到内容"
            answer = self.unsorted()
        return "done", answer, None


# ---- the runner ------------------------------------------------------------------------------------------------------

class Runner:
    """Background research jobs. send(body) -> (resp, ms, err): the Responses API call (default: responses_call on env);
    search: a search backend (default ResponsesWebSearch on send and the research model); report(job): called once
    when a job ends (done / failed); day_left_fn() -> CNY left of today's cap or None (optional; the budget pause is
    always checked)."""
    def __init__(self, db, env=None, *, settings_fn=None, report=None, send=None, search=None, clock_ms=None,
                 day_left_fn=None, new_id=None):
        self.db, self.env = db, env
        self.settings_fn = settings_fn or (lambda: {})
        self.report = report or (lambda job: None)
        self.send = send or ((lambda body: responses_call(env, body)) if env else None)
        self._search = search
        self.now = clock_ms or (lambda: int(time.time() * 1000))
        self.day_left_fn = day_left_fn
        self.new_id = new_id or (lambda: "r" + uuid.uuid4().hex[:8])
        self._q = queue.Queue()
        self._cancel = set()
        self._lock = threading.Lock()
        self._threads = []

    # -- settings --
    def config(self):
        c = {**DEFAULTS, **((self.settings_fn() or {}).get("research") or {})}
        try:
            cap = float(c["cap_cny_per_task"])
        except (TypeError, ValueError):
            cap = DEFAULTS["cap_cny_per_task"]
        jobs = c.get("max_jobs") if isinstance(c.get("max_jobs"), int) else 1
        return {"model": str(c.get("model") or DEFAULTS["model"]), "cap_cny_per_task": max(0.0, cap),
                "max_jobs": min(2, max(1, jobs))}

    def usd_cny(self):
        try:
            return pause.config(self.settings_fn())["usd_cny"]
        except Exception:                        # noqa: BLE001
            return pause.DEFAULTS["usd_cny"]

    @property
    def search(self):
        if self._search is None:
            self._search = ResponsesWebSearch(self.send, self.config()["model"])
        return self._search

    # -- documents --
    def col(self):
        return self.db[COLL]

    def _set(self, job_id, **fields):
        self.col().update_one({"_id": job_id}, {"$set": fields})

    def get(self, job_id):
        return self.col().find_one({"_id": job_id})

    def recent(self, limit=5):
        return list(self.col().find({}).sort("at", -1).limit(limit))

    # -- jobs --
    def submit(self, topic, depth="normal", asked_by=None):
        topic = str(topic or "").strip()
        if not topic:
            raise ToolError("bad_args", "要研究什么还没说")
        cfg = self.config()
        if self.send is None:
            raise ToolError("not_available", "没有模型密钥，研究不了")
        if not priced(cfg["model"]):
            raise ToolError("not_available", f"{cfg['model']} 没有价格，没法封顶，先不研究")
        now = self.now()
        if pause.budget_stop(self.db, now):
            raise ToolError("budget", "今天的花费到上限了，先不研究")
        waiting = self.col().count_documents({"state": {"$in": ["queued", "running"]}})
        if waiting >= MAX_QUEUED:
            raise ToolError("refused", "已经排了好几个研究了，等做完再说")
        doc = {"_id": self.new_id(), "topic": topic[:200], "depth": depth if depth in DEPTH else "normal",
               "asked_by": asked_by, "at": now, "state": "queued", "steps": [], "answer": None, "sources": [],
               "cost_cny": 0.0, "cap_cny": cfg["cap_cny_per_task"], "model": cfg["model"], "trace": _trace(),
               "started_at": None, "finished_at": None, "stopped": None, "error": None, "reported": False}
        self.col().insert_one(doc)
        self._q.put(doc["_id"])
        doc["ahead"] = waiting
        return doc

    def cancel(self, job_id):
        """A queued job is cancelled at once; a running one stops before its next model call."""
        doc = self.get(job_id)
        if not doc or doc["state"] not in ("queued", "running"):
            return False
        if doc["state"] == "queued":
            self._set(job_id, state="cancelled", finished_at=self.now())
        else:
            with self._lock:
                self._cancel.add(job_id)
        return True

    def cancelled(self, job_id):
        with self._lock:
            return job_id in self._cancel

    def run_one(self, job_id):
        """Run one queued job to its end (the worker threads call this; tests call it directly). -> the final doc."""
        doc = self.col().find_one_and_update({"_id": job_id, "state": "queued"},
                                             {"$set": {"state": "running", "started_at": self.now()}},
                                             return_document=True)
        if not doc:
            return self.get(job_id)
        run = _Run(self, doc)
        with hello_log.scope(trace=doc.get("trace")):
            try:
                state, answer, error = run.go()
            except Stop:
                state, answer, error = "cancelled", None, None
            except Exception as e:               # noqa: BLE001 - a bug ends this job, never the worker
                state, answer, error = "failed", None, f"出错了（{type(e).__name__}）"
                try:
                    hello_log.error(e, where="mind.research")
                except Exception:                # noqa: BLE001
                    pass
        self._set(job_id, state=state, answer={"spoken": answer["spoken"], "report": answer["report"]} if answer else None,
                  sources=(answer or {}).get("sources") or run.sources[:10], steps=run.steps,
                  cost_cny=round(run.spent, 4), stopped=run.stopped, error=error, finished_at=self.now())
        with self._lock:
            self._cancel.discard(job_id)
        try:
            hello_log.emit("research.end", trace=doc.get("trace"), ok=state == "done",
                           sum=f"{STATE_ZH[state]}：{doc['topic'][:40]}",
                           extra={"job": job_id, "state": state, "cost_cny": round(run.spent, 4), "stopped": run.stopped})
        except Exception:                        # noqa: BLE001
            pass
        if state in ("done", "failed"):
            self._report(job_id)
        return self.get(job_id)

    def _report(self, job_id):
        doc = self.get(job_id)
        if not doc or doc.get("reported"):
            return
        self._set(job_id, reported=True, reported_at=self.now())       # once, even when the hook fails
        try:
            self.report(doc)
        except Exception as e:                   # noqa: BLE001 - the hook never breaks the runner
            self._set(job_id, report_error=f"{type(e).__name__}: {str(e)[:120]}")

    # -- threads --
    def recover(self):
        """At start: a job that was running when mind stopped has failed (and is reported); queued ones run again."""
        for d in list(self.col().find({"state": "running"})):
            self._set(d["_id"], state="failed", error="mind 重启了，研究中断", finished_at=self.now())
            self._report(d["_id"])
        for d in list(self.col().find({"state": "queued"}).sort("at", 1)):
            self._q.put(d["_id"])

    def start(self):
        self.recover()
        for _ in range(self.config()["max_jobs"]):
            t = threading.Thread(target=self._work, daemon=True, name="research")
            t.start()
            self._threads.append(t)
        return self

    def _work(self):
        while True:
            job_id = self._q.get()
            if job_id is None:
                return
            try:
                self.run_one(job_id)
            except Exception as e:               # noqa: BLE001 - the worker keeps running
                print(f"mind research: {type(e).__name__}: {str(e)[:200]}", flush=True)

    def stop(self):
        for _ in self._threads:
            self._q.put(None)


def _trace():
    try:
        return hello_log.new_trace()
    except Exception:                            # noqa: BLE001
        return uuid.uuid4().hex[:12]


# ---- the tools (tool_impls.mind_tools(research=runner)) -----------------------------------------------------------------

def _label(ms):
    return datetime.fromtimestamp(ms / 1000, BJ).strftime("%m-%d %H:%M") if isinstance(ms, int) else None


def tools(runner):
    def deep_research(args):
        job = runner.submit(args["question"], args.get("depth") or "normal", asked_by="tool")
        return {"job_id": job["_id"], "say": "开始研究", "state": job["state"], "ahead": job["ahead"],
                "cap_cny": job["cap_cny"], "depth": job["depth"]}

    def research_status(args):
        if args.get("id"):
            d = runner.get(args["id"])
            if not d:
                raise ToolError("bad_args", "没有这个研究")
            ans = d.get("answer") or {}
            report = ans.get("report") or ""
            last = (d.get("steps") or [{}])[-1]
            return {"id": d["_id"], "topic": d["topic"], "depth": d.get("depth"), "state": d["state"],
                    "state_zh": STATE_ZH.get(d["state"], d["state"]), "at": _label(d.get("at")),
                    "finished_at": _label(d.get("finished_at")), "steps": len(d.get("steps") or []),
                    "last_step": last.get("q") or last.get("kind"),
                    "spoken": ans.get("spoken"), "report": report[:1500], "report_chars": len(report),
                    "sources": (d.get("sources") or [])[:8], "cost_cny": d.get("cost_cny"), "cap_cny": d.get("cap_cny"),
                    "stopped": STOP_ZH.get(d.get("stopped"), d.get("stopped")), "error": d.get("error")}
        rows = runner.recent(5)
        return {"jobs": [{"id": d["_id"], "topic": d["topic"], "state": d["state"],
                          "state_zh": STATE_ZH.get(d["state"], d["state"]), "at": _label(d.get("at")),
                          "cost_cny": d.get("cost_cny"), "spoken": (d.get("answer") or {}).get("spoken")} for d in rows],
                "shown": len(rows), "total": runner.col().count_documents({}),
                "running": runner.col().count_documents({"state": "running"}),
                "queued": runner.col().count_documents({"state": "queued"})}

    return {"deep_research": deep_research, "research_status": research_status}
