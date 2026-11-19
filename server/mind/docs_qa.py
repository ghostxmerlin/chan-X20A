"""资料问答 (owner 2026-11-19 "基于知识库的回答，不能只是搜索和输出，还要进行再推理，且推理模型应该用 gpt-sol"):
the doc_answer tool. Search the material (docs_kb.find: vectors + exact words), then let sol reason over the excerpts and
answer the question; if the excerpts are not enough it may ask for one more search (a follow-up query), once.

  1. find(question) -> up to K chunks (full text, each cut to EXCERPT chars, all of them to INPUT_MAX)
  2. sol (Responses API on aihubmix, research.responses_call; MODEL, reasoning effort EFFORT) gets the question and the
     numbered excerpts and returns JSON: {answer, used[n], confidence 高/中/低, caveats, missing, followup}
  3. confidence 低 with a followup query and rounds left: find(followup), the new chunks join the old, sol again
  4. -> {answer, confidence, caveats, missing, sources[{project, id, title, status, branch?}], rounds, model}

Money: every sol call is one run-log row call.docs_qa with its USD cost (research.cost_usd, prices.AIHUBMIX_*), so it
counts into the one daily cap; the search's embedding is its own call.embed row. The tool returns plain data (not
Paid: no second cost on the call.tool row). At the daily-cap pause the tool refuses (the search alone still works:
doc_search).
"""
import json
import os
import re
import time

from . import docs_kb, pause, research
from .runlog import hello_log, trace as log_trace

MODEL = os.environ.get("MIND_DOCS_QA_MODEL") or "gpt-6.2-sol"
EFFORT = os.environ.get("MIND_DOCS_QA_EFFORT") or "medium"
K = 8                         # chunks per search
EXCERPT = 1500                # characters of one chunk given to the model
INPUT_MAX = 16000             # characters of all excerpts together
MAX_OUT = 2000                # output tokens (reasoning included)
ROUNDS = 2                    # searches at most (the first + one follow-up)
TIMEOUT_S = 60
BASIS = "research.cost_usd (prices.AIHUBMIX_USD_PER_M)"

INSTRUCTIONS = (
    "你是主人的工程助手，回答他工作上的技术问题。下面给你主人自己项目的资料片段（编号 [1] [2] …，每段标了项目、条目号、"
    "验证状态）。先读懂问题，再从片段里找依据，把相关的几段合起来推理，得出结论；片段之间说法不一样就指出来，说哪个更新或"
    "更可信、为什么。只用片段里有的事实，不要用常识补细节；片段不够回答，就说缺什么。\n"
    "只输出一个 JSON 对象，不要别的文字：\n"
    '{"answer": "要说出口的回答：口语，最多 120 个字，两三句，先说结论，再说最关键的一个依据或数字和它的条目号（比如 HW-035）",'
    ' "detail": "详细的说明：步骤、条件、数字和各自的条目号，最多 600 个字；主人要详细时才说",'
    ' "used": [用到的片段编号], "confidence": "高" 或 "中" 或 "低",'
    ' "caveats": "要提醒的：推测 / 未解决 / 没实板验证的结论、片段之间的矛盾；没有就空字符串",'
    ' "missing": "片段里缺、回答不了的部分；没有就空字符串",'
    ' "followup": "片段不够时，再查一次用的检索词（带上型号、条目号、关键名词）；够了就空字符串"}')


def _excerpts(chunks):
    out, used = [], 0
    for i, c in enumerate(chunks, 1):
        head = f"[{i}] {c.get('project_title')} · {c.get('cid')} · {c.get('title')}" + \
               (f" · 分支 {c['branch']}" if c.get("branch") else "") + (f" · 状态 {c['status']}" if c.get("status") else "")
        text = (c.get("text") or "")[:EXCERPT]
        if used + len(text) > INPUT_MAX and out:
            break
        used += len(text)
        out.append(f"{head}\n{text}")
    return "\n\n".join(out), len(out)


def parse(text):
    """The model's JSON (the first {...} block, fenced or not) -> dict, or None."""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    return d if isinstance(d, dict) and isinstance(d.get("answer"), str) else None


def _call(env, question, chunks, send):
    ex, n = _excerpts(chunks)
    body = {"model": MODEL, "instructions": INSTRUCTIONS, "max_output_tokens": MAX_OUT, "reasoning": {"effort": EFFORT},
            "input": f"问题：{question}\n\n资料片段（共 {n} 段）：\n\n{ex}"}
    resp, ms, err = send(env, body, TIMEOUT_S)
    text, _, _ = research.parse_output(resp) if resp else ("", [], 0)
    usage = (resp or {}).get("usage") or {}
    usd = research.cost_usd(MODEL, usage) if resp else 0.0
    out = parse(text)
    if err is None and out is None:
        err = "答案不是 JSON"
    try:
        hello_log.emit("call.docs_qa", trace=log_trace(), model=MODEL, ms=ms, ok=err is None, err=err,
                       usage=usage or None, cost={"amount": usd, "cur": "USD", "est": True, "basis": BASIS},
                       key=hello_log.key_fp(env["api_key"]) if env.get("api_key") else None,
                       sum=f"资料问答：{question[:40]}", extra={"chunks": n, "effort": EFFORT},
                       body={"system": [INSTRUCTIONS], "input": [body["input"]], "output": (text or "")[:3000]})
    except Exception:                            # noqa: BLE001 - logging never touches the answer
        pass
    return out, err, n


def answer_tool(db, env, embed_fn, clock_ms=None, send=None):
    """The doc_answer tool: fn(args {question, project?}) -> data (see the module doc). send(env, body, timeout) ->
    (resp, ms, err) is research.responses_call (tests pass a fake)."""
    from .tools import ToolError
    now_ms = clock_ms or (lambda: int(time.time() * 1000))
    send = send or research.responses_call

    def doc_answer(args):
        q, proj = docs_kb.check_args(db, args)
        if env is None or not env.get("api_key"):
            raise ToolError("not_available", "推理模型没接上，先用 doc_search 查原文")
        if pause.budget_stop(db, now_ms()):
            raise ToolError("budget", "今天的钱用到上限了，先用 doc_search 查原文")
        chunks, how, _ = docs_kb.find(db, q, proj, K, embed_fn, now_ms())
        if not chunks:
            return {"question": q, "project": docs_kb.project_label(db, proj), "answer": None,
                    "note": "资料里没找到相关的内容，就说资料里没写，可以换个说法或说出型号再问"}
        out, err, rounds, queries = None, None, 0, [q]
        while rounds < ROUNDS:
            rounds += 1
            out, err, n = _call(env, q, chunks, send)
            if out is None:
                break
            follow = str(out.get("followup") or "").strip()
            if out.get("confidence") != "低" or not follow or rounds >= ROUNDS or follow in queries:
                break
            queries.append(follow)
            more, _, _ = docs_kb.find(db, follow, proj, K, embed_fn, now_ms())
            seen = {c["_id"] for c in chunks}
            new = [c for c in more if c["_id"] not in seen]
            if not new:
                break
            chunks = new[:K // 2] + chunks          # the follow-up's own finds first, then the earlier ones
        if out is None:
            raise ToolError("failed", f"推理没成功（{(err or '')[:40]}），先用 doc_search 查原文")
        used = [int(i) for i in out.get("used") or [] if isinstance(i, (int, float)) or str(i).isdigit()]
        src = [chunks[i - 1] for i in used if 1 <= i <= len(chunks)] or chunks[:3]
        return {"question": q, "project": docs_kb.project_label(db, proj), "answer": out["answer"][:300],
                "detail": str(out.get("detail") or "")[:900] or None,
                "confidence": out.get("confidence"), "caveats": str(out.get("caveats") or "")[:300] or None,
                "missing": str(out.get("missing") or "")[:200] or None,
                "sources": [{"project": c.get("project_title"), "id": c.get("cid"), "title": (c.get("title") or "")[:60],
                             "status": c.get("status"), **({"branch": c["branch"]} if c.get("branch") else {})}
                            for c in src[:5]],
                "rounds": rounds, "searched": queries, "model": MODEL,
                "say": "照 answer 说（数字、条目号不要改），不要把 detail 一起念；主人要详细再说 detail；caveats 有内容要提一句；资料外的不要补"}
    return doc_answer


def tools(db, llm_env=None, embed=None):
    return {"doc_answer": answer_tool(db, llm_env, embed)}
