"""按意思找 (semantic search / RAG; owner design 2026-11-18, tool semantic_search in config/tools/registry.json).

What is embedded (never single speech sentences):
  memory     memory_recall (the library's memories, live lane, recallable): title + statement
  knowledge  knowledge_items heads with status active: name + aliases + statement
  notes      conversations (纪要) with a title or notes: title + notes
  segment    mind_segments (片段, live lane, newest version per start): summary title + text
  doc        doc_chunks (资料, 2026-11-19, mind/docs_kb.py: the owner's project material, imported by folder): project +
             title + text. Synced like the rest, but semantic_search never searches it (recall stays personal); the
             doc_search tool does (docs_kb.py, vectors + exact words).
Vectors live in Mongo hello.vectors, one per source:
  {_id: "<kind>:<source id>", kind, sid, ref {...how to open it}, text_hash, text (≤ 60-char preview), vec [floats,
   unit length], model, dim, when (the source's time, ms), at (written, ms)}
A source is embedded again only when its text_hash (model + dim + text) changes; vectors whose source is gone (or no
longer recallable / active) are removed by the next sync. Search embeds the query once and streams the vectors of the
wanted kinds through a dot product in Python, keeping only a small top-k heap (thousands of rows; no vector database;
the server once ran out of memory, so nothing holds all vectors at once).

Embedding model: Alibaba DashScope (百炼), OpenAI-compatible POST {URL} with {model, input[], dimensions,
encoding_format: float}; the key is DASHSCOPE_API_KEY (environment, or hello.env); it only goes into the request header
and the run log keeps its fingerprint. Every embed() call is one run-log row call.embed with usage and a CNY cost
estimate (the ¥/day cap in mind/pause.py counts it). urllib only.

    python3 -m mind.semantic status                         # counts per kind, what a full sync would cost (no call)
    python3 -m mind.semantic backfill --budget 5 --dry-run  # what would be embedded, estimated cost (no call)
    python3 -m mind.semantic backfill --budget 5            # embed what is missing / changed, stop before ¥5
    python3 -m mind.semantic search "上次说的那个板子问题"     # one query (≈ ¥0.00001)
(run from ~/soul-mind on the server; MONGO_URI and DASHSCOPE_API_KEY from ~/soul-spike/hello.env)
"""
import hashlib
import heapq
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request

from . import pause
from .runlog import hello_log, trace as log_trace

URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"   # env DASHSCOPE_EMBED_URL overrides
MODEL = "text-embedding-v4"                                            # env MIND_SEMANTIC_MODEL overrides
DIM = 512                                                              # env MIND_SEMANTIC_DIM overrides
# Prices, 元 per million input tokens, 中国内地 (华北2 北京), list price without free quota. Looked up 2026-11-18 on
# https://help.aliyun.com/zh/model-studio/embedding : text-embedding-v4 0.0005 元/千 Token (= 0.5 元/百万), dimensions
# 2048/1536/1024 (default)/768/512/256/128/64, at most 10 texts per request, 8192 tokens per text; the newer
# qwen3.7-text-embedding 0.5 元/百万 (20 per request) and qwen3.7-text-embedding-flash 0.125 元/百万 (dims 1024/768/512/256,
# 20 per request). The API reports usage.total_tokens (only the flash model also prompt_tokens):
# https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api . That page now documents a per-workspace
# base URL (https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1); the dashscope.aliyuncs.com one is
# what eye already uses (DASHSCOPE_EMBED_URL switches it).
PRICE_CNY_PER_M = {"text-embedding-v4": 0.5, "qwen3.7-text-embedding": 0.5, "qwen3.7-text-embedding-flash": 0.125}
BATCH = {"text-embedding-v4": 10, "qwen3.7-text-embedding": 20, "qwen3.7-text-embedding-flash": 20}
PRICE_BASIS = "semantic.PRICE_CNY_PER_M (help.aliyun.com/zh/model-studio/embedding, 2026-11-18)"
TEXT_MAX = 2000                 # characters embedded per source (notes ~50, segments ≤ 320; doc chunks ≤ ~2800, 2026-11-19)
PREVIEW = 60
CHUNK = 50                      # texts per embed() call in a sync: the budget is checked between chunks
TIMEOUT_S = 30
KINDS = ("memory", "knowledge", "notes", "segment")    # semantic_search (recall): personal kinds only
DOC_KINDS = ("doc",)                                    # doc_search (docs_kb.py)
ALL_KINDS = KINDS + DOC_KINDS                           # what a sync keeps up to date
KIND_ZH = {"memory": "记忆", "knowledge": "知识", "notes": "纪要", "segment": "片段", "doc": "资料"}
COLL = "vectors"
SYNC_EVERY_S = 600              # the periodic incremental sync (runner, MIND_SEMANTIC_SYNC=1)
SYNC_BUDGET_CNY = 0.5           # at most this much per periodic sync (new items are a few cents a day)


def model():
    return os.environ.get("MIND_SEMANTIC_MODEL") or MODEL


def dim():
    try:
        return int(os.environ.get("MIND_SEMANTIC_DIM") or DIM)
    except ValueError:
        return DIM


class EmbedError(Exception):
    """The embedding call failed; .info = what the batches that did go through cost ({tokens, cny})."""
    def __init__(self, message, info=None):
        super().__init__(message)
        self.info = info or {"tokens": 0, "cny": 0.0}


def est_tokens(texts):
    """Tokens ≈ characters for Chinese (an English word is fewer tokens than letters: an over-estimate, safe)."""
    return sum(len(t) for t in texts)


def cost_cny(tokens, mdl=None):
    p = PRICE_CNY_PER_M.get(mdl or model())
    return None if p is None or tokens is None else tokens * p / 1_000_000


def cost(info, mdl=None):
    """The run log's cost envelope for an embed() result."""
    amt = info.get("cny") if isinstance(info, dict) else None
    return {"amount": None if amt is None else round(float(amt), 8), "cur": "CNY", "est": True, "basis": PRICE_BASIS}


def unit(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n > 0 else list(v)


def text_hash(text, mdl=None, d=None):
    return hashlib.sha256(f"{mdl or model()}|{d or dim()}|{text}".encode("utf-8")).hexdigest()[:20]


# ---- the model call ------------------------------------------------------------------------------------------------

def embed(texts, *, key=None, mdl=None, d=None, url=None, timeout=TIMEOUT_S, purpose="sync"):
    """texts -> (unit vectors in order, {tokens, cny, est}). Batches of BATCH[model]; tokens from the API's usage
    (estimated from characters when it is missing). One run-log row call.embed per call. Raises EmbedError."""
    key = key or os.environ.get("DASHSCOPE_API_KEY")
    mdl, d = mdl or model(), d or dim()
    url = url or os.environ.get("DASHSCOPE_EMBED_URL") or URL
    texts = [str(t)[:TEXT_MAX] or " " for t in texts]
    t0 = time.monotonic()
    vecs, tokens, est, err = [], 0, False, None
    try:
        if not key:
            raise EmbedError("no DASHSCOPE_API_KEY")
        step = BATCH.get(mdl, 10)
        for i in range(0, len(texts), step):
            batch = texts[i:i + step]
            body = {"model": mdl, "input": batch, "dimensions": d, "encoding_format": "float"}
            req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
                                         headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    out = json.loads(r.read())
            except urllib.error.HTTPError as e:
                with e:
                    raise EmbedError(f"HTTP {e.code}: {e.read()[:200].decode('utf-8', 'replace')}") from None
            except (urllib.error.URLError, OSError, ValueError) as e:
                raise EmbedError(f"{type(e).__name__}: {str(e)[:160]}") from None
            rows = sorted(out.get("data") or [], key=lambda r: r.get("index", 0))
            if len(rows) != len(batch) or any(len(r.get("embedding") or []) != d for r in rows):
                raise EmbedError(f"unexpected answer: {len(rows)} vectors for {len(batch)} texts")
            vecs += [unit([float(x) for x in r["embedding"]]) for r in rows]
            used = (out.get("usage") or {}).get("total_tokens")
            if isinstance(used, int) and not isinstance(used, bool) and used >= 0:
                tokens += used
            else:
                tokens += est_tokens(batch)
                est = True
    except EmbedError as e:
        err = str(e)
    info = {"tokens": tokens, "cny": cost_cny(tokens, mdl) or 0.0, "est": est}
    try:
        hello_log.emit("call.embed", trace=log_trace(), model=mdl, ms=int((time.monotonic() - t0) * 1000),
                       ok=err is None, err=err, usage={"total_tokens": tokens, "texts": len(texts)},
                       cost=cost(info, mdl), key=hello_log.key_fp(key) if key else None,
                       sum=f"向量化 {len(texts)} 条（{purpose}）", extra={"purpose": purpose, "dim": d})
    except Exception:                            # noqa: BLE001 - logging never touches the call
        pass
    if err is not None:
        raise EmbedError(err, info)
    return vecs, info


def embedder(key, **kw):
    """embed bound to a key: fn(texts, purpose) -> (vectors, info), what mind passes to the tool and the periodic
    sync (tests pass a fake with the same shape). None when there is no key."""
    if not key:
        return None
    return lambda texts, purpose="sync": embed(texts, key=key, purpose=purpose, **kw)


# ---- the sources ---------------------------------------------------------------------------------------------------

def _join(*parts):
    return "：".join(p.strip() for p in parts if isinstance(p, str) and p.strip())


def sources(db, kind):
    """[{sid, text, when, ref}] of one kind as it is now."""
    out = []
    if kind == "memory":
        for r in db["memory_recall"].find({"lane": "live", "recallable": True},
                                          {"_id": 0, "memory_id": 1, "title": 1, "statement": 1, "start_ms": 1}):
            text = _join(r.get("title"), r.get("statement"))
            if r.get("memory_id") and r.get("statement"):
                out.append({"sid": r["memory_id"], "text": text, "when": r.get("start_ms"),
                            "ref": {"memory_id": r["memory_id"]}})
    elif kind == "knowledge":
        from .library import Library
        for k in Library(db)._heads("knowledge_items"):
            if k.get("status", "active") != "active" or not k.get("kid"):
                continue
            aliases = "、".join(str(a) for a in k.get("aliases") or [])
            text = _join(k.get("name"), f"也叫{aliases}" if aliases else "", k.get("statement"))
            out.append({"sid": k["kid"], "text": text, "when": k.get("changed_at"), "ref": {"kid": k["kid"]}})
    elif kind == "notes":
        for c in db["conversations"].find({}, {"_id": 0, "cid": 1, "title": 1, "notes": 1, "start_ms": 1, "end_ms": 1}):
            text = _join(c.get("title"), c.get("notes"))
            if c.get("cid") and text:
                out.append({"sid": c["cid"], "text": text, "when": c.get("start_ms"),
                            "ref": {"cid": c["cid"], "start_ms": c.get("start_ms"), "end_ms": c.get("end_ms")}})
    elif kind == "segment":
        latest = {}
        for g in db["mind_segments"].find({"lane": "live"}, {"_id": 0, "start": 1, "end": 1, "version": 1, "summary": 1}):
            if g.get("start") is not None and (g["start"] not in latest or g.get("version", 0) > latest[g["start"]].get("version", 0)):
                latest[g["start"]] = g
        for start, g in latest.items():
            sm = g.get("summary") or {}
            text = _join(sm.get("title"), sm.get("text"))
            if text:
                out.append({"sid": str(start), "text": text, "when": start, "ref": {"start": start, "end": g.get("end")}})
    elif kind == "doc":
        for c in db["doc_chunks"].find({}, {"_id": 1, "project": 1, "project_title": 1, "title": 1, "text": 1, "at": 1}):
            text = _join(c.get("project_title"), c.get("title")) + "\n" + str(c.get("text") or "")
            if c.get("text"):
                out.append({"sid": c["_id"], "text": text, "when": c.get("at"),
                            "ref": {"chunk": c["_id"], "project": c.get("project")}})
    else:
        raise ValueError(f"unknown kind {kind}")
    return out


def _kinds(kinds, default=KINDS):
    kinds = list(kinds or default)
    bad = [k for k in kinds if k not in ALL_KINDS]
    if bad:
        raise ValueError(f"unknown kinds {bad}")
    return kinds


def plan(db, kinds=None):
    """What a sync would do: (todo [{_id, kind, sid, text, hash, when, ref}] newest first, stale ids, counts)."""
    mdl, d = model(), dim()
    todo, stale, counts = [], [], {}
    for kind in _kinds(kinds, ALL_KINDS):
        have = {r["_id"]: (r.get("text_hash"), r.get("model"), r.get("dim"))
                for r in db[COLL].find({"kind": kind}, {"_id": 1, "text_hash": 1, "model": 1, "dim": 1})}
        now_ids, n_todo = set(), 0
        for s in sources(db, kind):
            vid = f"{kind}:{s['sid']}"
            now_ids.add(vid)
            h = text_hash(s["text"], mdl, d)
            if have.get(vid) != (h, mdl, d):
                todo.append({**s, "_id": vid, "kind": kind, "hash": h})
                n_todo += 1
        gone = [v for v in have if v not in now_ids]
        stale += gone
        counts[kind] = {"sources": len(now_ids), "fresh": len(now_ids) - n_todo, "todo": n_todo, "stale": len(gone)}
    todo.sort(key=lambda t: -(t["when"] if isinstance(t.get("when"), (int, float)) else 0))
    return todo, stale, counts


def sync(db, kinds=None, budget_cny=5.0, embed_fn=None, now_ms=None, chunk=CHUNK, dry_run=False):
    """Backfill / update the vectors of `kinds`: embed what is missing or changed (newest first), remove vectors whose
    source is gone. Stops before spending more than budget_cny (estimated per chunk from characters), and at the
    daily-cap pause. -> {kinds: {kind: {sources, fresh, todo, stale, embedded}}, embedded, removed, left, spent_cny,
    est_cny (dry run: the estimate of all todo), tokens, stopped (None | budget | daily_cap | error: ...)}."""
    mdl, d = model(), dim()
    todo, stale, counts = plan(db, kinds)
    res = {"kinds": counts, "model": mdl, "dim": d, "embedded": 0, "removed": 0, "left": len(todo), "spent_cny": 0.0,
           "tokens": 0, "stopped": None, "est_cny": round(cost_cny(est_tokens([t["text"][:TEXT_MAX] for t in todo]), mdl) or 0, 6)}
    for c in counts.values():
        c["embedded"] = 0
    if dry_run:
        return res
    if stale:
        db[COLL].delete_many({"_id": {"$in": stale}})
        res["removed"] = len(stale)
    if todo and embed_fn is None:
        res["stopped"] = "error: no embedder (DASHSCOPE_API_KEY missing)"
        return res
    for i in range(0, len(todo), chunk):
        part = todo[i:i + chunk]
        texts = [t["text"][:TEXT_MAX] for t in part]
        est = cost_cny(est_tokens(texts), mdl) or 0.0
        if res["spent_cny"] + est > budget_cny:
            res["stopped"] = "budget"
            break
        if pause.budget_stop(db, now_ms):
            res["stopped"] = "daily_cap"
            break
        try:
            vecs, info = embed_fn(texts, "sync")
        except EmbedError as e:
            res["spent_cny"] += e.info.get("cny") or 0.0
            res["tokens"] += e.info.get("tokens") or 0
            res["stopped"] = f"error: {str(e)[:120]}"
            break
        res["spent_cny"] += info.get("cny") if info.get("cny") is not None else est
        res["tokens"] += info.get("tokens") or 0
        at = now_ms if now_ms is not None else int(time.time() * 1000)
        for t, v in zip(part, vecs):
            db[COLL].replace_one({"_id": t["_id"]}, {"_id": t["_id"], "kind": t["kind"], "sid": t["sid"], "ref": t["ref"],
                                                     "text_hash": t["hash"], "text": t["text"][:PREVIEW], "vec": unit(v),
                                                     "model": mdl, "dim": d, "when": t.get("when"), "at": at}, upsert=True)
            res["embedded"] += 1
            res["kinds"][t["kind"]]["embedded"] += 1
        res["left"] -= len(part)
    res["spent_cny"] = round(res["spent_cny"], 8)
    return res


def ensure_indexes(db):
    try:
        db[COLL].create_index([("kind", 1), ("model", 1), ("dim", 1)])
    except Exception:                            # noqa: BLE001 - slower, never wrong
        pass


# ---- search --------------------------------------------------------------------------------------------------------

def search(db, query, kinds=None, k=5, embed_fn=None, pool=3):
    """-> (best k*pool candidates [{_id, kind, sid, ref, text, when, score}] best first, the query's embed info).
    Streams the vectors (only one row's vector in memory at a time besides the heap). Raises EmbedError."""
    mdl, d = model(), dim()
    kinds = _kinds(kinds)
    (qv,), info = embed_fn([query], "search")
    qv = unit(qv)
    heap, n = [], 0
    cur = db[COLL].find({"kind": {"$in": kinds}, "model": mdl, "dim": d},
                        {"_id": 1, "kind": 1, "sid": 1, "ref": 1, "text": 1, "when": 1, "vec": 1})
    for r in cur.batch_size(500):
        v = r.get("vec")
        if not v or len(v) != len(qv):
            continue
        s = sum(a * b for a, b in zip(qv, v))
        n += 1
        item = (s, n, r["_id"])
        r.pop("vec", None)
        if len(heap) < k * pool:
            heapq.heappush(heap, (item, r))
        elif item > heap[0][0]:
            heapq.heapreplace(heap, (item, r))
    out = [{**r, "score": round(item[0], 4)} for item, r in sorted(heap, key=lambda x: x[0], reverse=True)]
    return out, {**info, "scanned": n}


# ---- the tool ------------------------------------------------------------------------------------------------------

def _abs(ms, tz):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.fromtimestamp(ms / 1000, ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M")


def _describe(db, hit, tz, names_of):
    """A candidate -> what the model gets (read fresh from its source), or None when the source is gone."""
    from . import tool_time
    from .library import Library
    kind, sid = hit["kind"], hit["sid"]
    row = {"kind": kind, "kind_title": KIND_ZH[kind], "id": sid, "score": hit["score"]}
    if kind == "memory":
        m = db["memory_recall"].find_one({"memory_id": sid, "lane": "live"}, {"_id": 0})
        if not m or not m.get("recallable") or not m.get("statement"):
            return None
        sens = m.get("sensitivity") or []
        row.update(title=m.get("title") or "", text=m["statement"][:160],
                   when=tool_time.label(m["start_ms"], tz) if isinstance(m.get("start_ms"), int) else None,
                   people=names_of(m.get("person_ids")), only_for_owner="private" in sens or "child" in sens,
                   open={"tool": "memory_search", "args": {"query": (m.get("title") or m["statement"])[:30]}})
    elif kind == "knowledge":
        k = Library(db).head("knowledge_items", sid)
        if not k or k.get("status", "active") != "active":
            return None
        row.update(title=k.get("name") or "", text=(k.get("statement") or "")[:160], aliases=k.get("aliases") or [],
                   about=names_of(k.get("subjects")), open={"tool": "knowledge_search", "args": {"query": (k.get("name") or "")[:30]}})
    elif kind == "notes":
        c = db["conversations"].find_one({"cid": sid}, {"_id": 0})
        if not c or not (c.get("title") or c.get("notes")):
            return None
        lo = c.get("start_ms")
        hi = c.get("end_ms") or c.get("last_ms") or lo
        row.update(title=c.get("title") or "", text=(c.get("notes") or "")[:160],
                   when=tool_time.label(lo, tz) if isinstance(lo, int) else None,
                   open={"tool": "notes_search", "args": {"from": _abs(lo, tz), "to": _abs(hi + 60_000, tz)}}
                   if isinstance(lo, int) else None)
    elif kind == "segment":
        start = int(sid)
        g = db["mind_segments"].find_one({"lane": "live", "start": start}, {"_id": 0}, sort=[("version", -1)])
        sm = (g or {}).get("summary") or {}
        if not (sm.get("title") or sm.get("text")):
            return None
        end = g.get("end") or start + 60_000
        row.update(title=sm.get("title") or "", text=(sm.get("text") or "")[:160], when=tool_time.label(start, tz),
                   until=tool_time.label(end, tz, with_day=False),
                   open={"tool": "review_period", "args": {"from": _abs(start, tz), "to": _abs(end, tz), "detail": "full"}})
    return row


def search_tool(db, embed_fn, settings_fn, names_of, clock_ms=None):
    """The semantic_search tool: fn(args) -> Paid({query, kinds, hits[...], scanned}, cost)."""
    from .tools import Paid, ToolError
    now_ms = clock_ms or (lambda: int(time.time() * 1000))

    def semantic_search(args):
        if embed_fn is None:
            raise ToolError("not_available", "按意思找还没接上向量模型")
        if pause.budget_stop(db, now_ms()):
            raise ToolError("budget", "今天的钱用到上限了，先用关键词查吧")
        q = (args.get("query") or "").strip()
        if not q:
            raise ToolError("bad_args", "要说找什么（query）")
        kinds = [k for k in KINDS if k in (args.get("kinds") or KINDS)]
        if not kinds:
            raise ToolError("bad_args", f"kinds 只能是 {' / '.join(KINDS)}")
        limit = args.get("limit") or 5
        try:
            cands, info = search(db, q, kinds, limit, embed_fn)
        except EmbedError as e:
            raise ToolError("failed", "向量模型没回应，换关键词查吧") from e
        tz = settings_fn()["tz"]
        hits = []
        for c in cands:
            row = _describe(db, c, tz, names_of)
            if row is not None:
                hits.append(row)
            if len(hits) >= limit:
                break
        data = {"query": q, "kinds": [KIND_ZH[k] for k in kinds], "scanned": info.get("scanned", 0), "hits": hits}
        if not info.get("scanned"):
            data["note"] = "向量表是空的（还没回填），先用关键词查"
        return data              # not Paid: the call.embed row already carries the cost (2026-11-19: no double count)
    return semantic_search


# ---- the periodic incremental sync (runner, behind MIND_SEMANTIC_SYNC=1) -------------------------------------------

def sync_enabled():
    """On by default since the owner approved RAG (2026-11-18; the first backfill: 518 items, 0.014 CNY);
    MIND_SEMANTIC_SYNC=0 turns it off. Each round is capped (SYNC_BUDGET_CNY), skipped while paused."""
    return os.environ.get("MIND_SEMANTIC_SYNC", "1").strip().lower() not in ("0", "false", "off", "no")


def periodic(db, embed_fn, paused_fn=None, every_s=SYNC_EVERY_S, budget_cny=SYNC_BUDGET_CNY, sleep=time.sleep, rounds=None):
    """Every every_s: sync all kinds (new / changed items only, ≤ budget_cny a round). Skips a round while mind is
    paused (away or the daily cap; nothing paid runs then). Never raises. rounds: stop after n (tests)."""
    n = 0
    while rounds is None or n < rounds:
        n += 1
        try:
            if not (paused_fn and paused_fn()):
                r = sync(db, budget_cny=budget_cny, embed_fn=embed_fn)
                if r["embedded"] or r["removed"] or r["stopped"]:
                    print(f"semantic sync: +{r['embedded']} -{r['removed']} left {r['left']} ¥{r['spent_cny']:.4f}"
                          f"{' stopped ' + r['stopped'] if r['stopped'] else ''}", flush=True)
        except Exception as e:                   # noqa: BLE001 - one bad round, the next one tries again
            print(f"semantic sync error: {type(e).__name__}: {str(e)[:160]}", flush=True)
        if rounds is None or n < rounds:
            sleep(every_s)


# ---- CLI -----------------------------------------------------------------------------------------------------------

def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="python3 -m mind.semantic", description="hello 按意思找: vectors backfill / search")
    ap.add_argument("--hubmix-env", default=os.path.expanduser("~/soul-spike/hello.env"),
                    help="MONGO_URI and DASHSCOPE_API_KEY (the environment wins)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backfill", help="embed what is missing or changed, stop before --budget CNY")
    b.add_argument("--budget", type=float, default=5.0)
    b.add_argument("--kinds", default=",".join(ALL_KINDS))
    b.add_argument("--dry-run", action="store_true", help="count and estimate only, no model call, nothing written")
    sub.add_parser("status", help="counts per kind and what a full sync would cost (no model call)")
    s = sub.add_parser("search", help="one query (one small paid call)")
    s.add_argument("query")
    s.add_argument("--kinds", default=",".join(KINDS))
    s.add_argument("-k", type=int, default=5)
    a = ap.parse_args(argv)
    from . import settings as settings_mod
    from .runlog import start
    hub = settings_mod.load_env(a.hubmix_env)
    uri = os.environ.get("MONGO_URI") or hub.get("MONGO_URI")
    if not uri:
        raise SystemExit("MONGO_URI missing (environment or --hubmix-env)")
    key = os.environ.get("DASHSCOPE_API_KEY") or hub.get("DASHSCOPE_API_KEY")
    from pymongo import MongoClient
    db = MongoClient(uri, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000)[os.environ.get("SOUL_DB") or "hello"]
    kinds = [k for k in getattr(a, "kinds", ",".join(ALL_KINDS)).split(",") if k]
    if a.cmd in ("status",) or (a.cmd == "backfill" and a.dry_run):
        r = sync(db, kinds, budget_cny=getattr(a, "budget", 0.0), dry_run=True)
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return
    start("semantic", hub, a)                    # run log: call.embed rows carry the cost (the daily cap counts them)
    ensure_indexes(db)
    fn = embedder(key)
    if fn is None:
        raise SystemExit("DASHSCOPE_API_KEY missing (environment or --hubmix-env)")
    if a.cmd == "backfill":
        r = sync(db, kinds, budget_cny=a.budget, embed_fn=fn)
        print(json.dumps(r, ensure_ascii=False, indent=1))
    else:
        cands, info = search(db, a.query, kinds, a.k, fn)
        for c in cands[:a.k]:
            print(f"{c['score']:.3f}  {KIND_ZH[c['kind']]}  {c['sid']}  {c['text']}")
        print(f"(scanned {info.get('scanned')}, {info.get('tokens')} tokens, ¥{info.get('cny', 0):.6f})")
    lg = hello_log.get()
    if lg is not None:
        lg.flush(5)                              # the call.embed rows reach Mongo before the process ends


if __name__ == "__main__":
    sys.exit(main())
