"""资料库 (owner 2026-11-19 "知识库…Tab5 里的数据，模组、传感器、指纹算法、USB"): the owner's project material, by folder.

The material lives outside the repo (company documents): a root folder with one sub-folder per project, e.g.
  knowledge-base/fingerprint moudle/  data/knowledge.jsonl (pre-chunked, schema v1: id, title, text, branch, commit,
                                      scope, document, source_refs, verification_status, hardware_verified, tags)
  knowledge-base/tab5/                Markdown, one entry per "### [HW-001] title" (fields 标签 / 状态 / 来源)
  knowledge-base/aidea phone/         Markdown, cut at its ## / ### headings
A project with data/knowledge.jsonl is imported from it alone (its own guide: never also the full Markdown and the
chapters, or every answer is found three times). Otherwise every *.md: "### [ID]" entries when the file has them,
else heading sections, a long section split on paragraphs (CHUNK_MAX). HTML, logs and raw data files are not imported.

Chunks go to Mongo hello.doc_chunks {_id "<project>:<chunk id>", project, project_title, cid, title, text, doc,
tags[], status, source, branch?, scope?, hash, at}; an import replaces the project's chunks (removed ones go, unchanged
ones keep their time). Vectors: semantic.py kind "doc" (the periodic sync embeds new / changed chunks).

doc_search (the tool): the query's vector against the doc vectors plus an exact-word search for the identifier-like
parts of the query (ICN7332, GPIO17, HW-035, 0x82, F1 ...; and the whole query as a phrase), merged by reciprocal
rank; at most `limit` chunks, each cut to SNIPPET chars so the answer stays under the 3000 the hub passes on.

    python3 -m mind.docs_kb import ~/soul-kb --dry-run    # what would be imported (no write)
    python3 -m mind.docs_kb import ~/soul-kb              # write doc_chunks (then: python3 -m mind.semantic backfill --kinds doc)
    python3 -m mind.docs_kb search "ICN7332 录入次数"       # one query (one small paid call)
"""
import hashlib
import json
import os
import re
import sys
import time

COLL = "doc_chunks"
CHUNK_MAX = 1800                 # a heading section longer than this is split on blank lines
CHUNK_MIN = 8                    # a section with less text under its heading than this is dropped
SNIPPET = 650                    # characters of a chunk the tool returns
RRF_K = 60
PROJECT_TITLES = {"fingerprint moudle": "指纹模组", "fingerprint module": "指纹模组", "tab5": "Tab5（X10A）",
                  "aidea phone": "AIdea 手机"}
STATUS_ZH = {"已验证": "verified", "推测": "guess", "未解决": "open"}
ENTRY_RE = re.compile(r"^### \[([A-Za-z]+-\d+)\]\s*(.*)$", re.M)
HEAD_RE = re.compile(r"^(#{1,3})\s+(.*)$", re.M)
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_./-]*\d[A-Za-z0-9_./-]*|0x[0-9A-Fa-f]+|[A-Z]{2,}[A-Za-z0-9_-]*|[A-Z]+\d*/\d+|(?<![\w.])\d{3,}(?![\w.])")
FENCE_RE = re.compile(r"^```.*?^```[^\n]*$", re.M | re.S)   # code blocks: a format example is not an entry


def _hash(*parts):
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


def project_key(name):
    return re.sub(r"\s+", "-", name.strip().lower())


def project_title(name):
    return PROJECT_TITLES.get(name.strip().lower(), name.strip())


def _field(body, label):
    m = re.search(rf"^- {label}[:：]\s*(.+)$", body, re.M)
    return m.group(1).strip() if m else None


def _split_long(text, limit=CHUNK_MAX):
    """Paragraph-aligned pieces of at most ~limit characters (a single longer paragraph is cut hard)."""
    if len(text) <= limit:
        return [text]
    out, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        while len(para) > limit:
            if cur:
                out.append(cur)
                cur = ""
            out.append(para[:limit])
            para = para[limit:]
        if cur and len(cur) + len(para) + 2 > limit:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        out.append(cur)
    return out


def chunks_from_entries(text, doc):
    """'### [HW-001] title' entries (the Tab5 pack): one chunk each, with its 标签 / 状态 / 来源 fields."""
    marks = list(ENTRY_RE.finditer(text))
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        nxt = HEAD_RE.search(text, m.end())          # an entry also ends at the next ## / # heading
        if nxt and nxt.start() < end and len(nxt.group(1)) <= 2:
            end = nxt.start()
        body = text[m.start():end].strip()
        status = _field(body, "状态") or ""
        tags = [t.strip() for t in re.split(r"[,，、]", _field(body, "标签") or "") if t.strip()]
        out.append({"cid": m.group(1), "title": m.group(2).strip(), "text": body, "doc": doc, "tags": tags,
                    "status": next((v for k, v in STATUS_ZH.items() if status.startswith(k)), status[:40] or None),
                    "source": _field(body, "来源")})
    return out


def chunks_from_headings(text, doc):
    """A Markdown file cut at its ## / ### headings (the title path kept), long sections split on paragraphs."""
    out, path = [], {}
    masked = FENCE_RE.sub(lambda m: " " * len(m.group(0)), text)   # a "### " inside a code block is not a heading
    marks = list(HEAD_RE.finditer(masked))
    pre = text[:marks[0].start()] if marks else text
    pieces = [(None, pre)] + [(m, text[m.start():(marks[i + 1].start() if i + 1 < len(marks) else len(text))])
                              for i, m in enumerate(marks)]
    stem = os.path.splitext(doc)[0]
    n = 0
    for m, body in pieces:
        if m is not None:
            level, title = len(m.group(1)), text[m.start(2):m.end(2)].strip()
            path = {k: v for k, v in path.items() if k < level}
            path[level] = title
        body = body.strip()
        content = body.split("\n", 1)[1].strip() if m is not None and "\n" in body else ("" if m is not None else body)
        if len(content) < CHUNK_MIN:                 # a lone heading (its text is in the sections under it)
            continue
        title = " / ".join(path[k] for k in sorted(path) if k > 1) or (path.get(1) or stem)
        for j, part in enumerate(_split_long(body)):
            n += 1
            out.append({"cid": f"{stem}#{n}", "title": title + (f"（{j + 1}）" if j else ""), "text": part, "doc": doc,
                        "tags": [], "status": None, "source": doc})
    return out


def chunks_from_jsonl(path, doc):
    """A pre-chunked knowledge.jsonl (the fingerprint pack's schema v1)."""
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if not r.get("id") or not r.get("text"):
                continue
            out.append({"cid": str(r["id"]), "title": str(r.get("title") or ""), "text": str(r["text"]),
                        "doc": r.get("document") or doc, "tags": [str(t) for t in r.get("tags") or []],
                        "status": r.get("verification_status"), "hardware_verified": r.get("hardware_verified"),
                        "source": ", ".join(r.get("source_refs") or []) or r.get("document"),
                        "branch": r.get("branch"), "commit": r.get("commit"), "scope": r.get("scope")})
    return out


def load_project(folder):
    """-> (project name, [chunk]) for one project folder."""
    name = os.path.basename(os.path.normpath(folder))
    pre = os.path.join(folder, "data", "knowledge.jsonl")
    if os.path.isfile(pre):
        return name, chunks_from_jsonl(pre, "data/knowledge.jsonl")
    chunks = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for fn in sorted(files):
            if not fn.lower().endswith(".md"):
                continue
            rel = os.path.relpath(os.path.join(root, fn), folder)
            with open(os.path.join(root, fn), encoding="utf-8") as f:
                text = f.read()
            bare = FENCE_RE.sub("", text)
            chunks += chunks_from_entries(bare, rel) if ENTRY_RE.search(bare) else chunks_from_headings(text, rel)
    return name, chunks


def load_root(root):
    """-> [(project name, [chunk])] for every project folder under root."""
    out = []
    for d in sorted(os.listdir(root)):
        p = os.path.join(root, d)
        if os.path.isdir(p) and not d.startswith("."):
            out.append(load_project(p))
    return out


def import_root(db, root, now_ms=None, dry_run=False):
    """Write every project's chunks; a project's chunks that are no longer there go. -> {project: {chunks, new,
    changed, same, removed}}."""
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    res = {}
    for name, chunks in load_root(root):
        key = project_key(name)
        have = {r["_id"]: r.get("hash") for r in db[COLL].find({"project": key}, {"_id": 1, "hash": 1})}
        seen, counts = set(), {"chunks": len(chunks), "new": 0, "changed": 0, "same": 0, "removed": 0}
        for c in chunks:
            _id = f"{key}:{c['cid']}"
            if _id in seen:                          # a duplicate id in the material: keep the first
                continue
            seen.add(_id)
            h = _hash(c["title"], c["text"], c.get("status"), ",".join(c.get("tags") or []))
            if have.get(_id) == h:
                counts["same"] += 1
                continue
            counts["changed" if _id in have else "new"] += 1
            if not dry_run:
                db[COLL].replace_one({"_id": _id}, {**{k: v for k, v in c.items() if v is not None}, "_id": _id,
                                                    "project": key, "project_title": project_title(name), "hash": h,
                                                    "at": now}, upsert=True)
        gone = [i for i in have if i not in seen]
        counts["removed"] = len(gone)
        if gone and not dry_run:
            db[COLL].delete_many({"_id": {"$in": gone}})
        res[key] = counts
    return res


def projects(db):
    return sorted({(r.get("project"), r.get("project_title")) for r in db[COLL].find({}, {"project": 1, "project_title": 1})})


# ---- search --------------------------------------------------------------------------------------------------------

def words(query):
    """The identifier-like parts of a query (part numbers, pins, entry ids, hex), each at least 2 characters."""
    return [w for w in dict.fromkeys(WORD_RE.findall(query or "")) if len(w) >= 2]


def keyword_hits(db, query, project=None, limit=20):
    """Chunks containing the query's identifier-like words (all of them ranked first), or the whole query as a phrase.
    -> [_id] best first."""
    ws = words(query)
    q = (query or "").strip()
    base = {"project": project} if project else {}
    scored = {}
    for w in ws:
        rx = {"$regex": re.escape(w), "$options": "i"}
        for r in db[COLL].find({**base, "$or": [{"title": rx}, {"text": rx}, {"tags": rx}, {"cid": rx}]}, {"_id": 1}).limit(200):
            scored[r["_id"]] = scored.get(r["_id"], 0) + 1
    if len(q) >= 4 and not ws:
        rx = {"$regex": re.escape(q), "$options": "i"}
        for r in db[COLL].find({**base, "$or": [{"title": rx}, {"text": rx}]}, {"_id": 1}).limit(50):
            scored[r["_id"]] = scored.get(r["_id"], 0) + 1
    return [i for i, _ in sorted(scored.items(), key=lambda x: (-x[1], x[0]))][:limit]


def merge(vector_ids, keyword_ids, k=RRF_K):
    """Reciprocal-rank fusion of two best-first id lists."""
    score = {}
    for lst in (vector_ids, keyword_ids):
        for rank, i in enumerate(lst):
            score[i] = score.get(i, 0.0) + 1.0 / (k + rank + 1)
    return [i for i, _ in sorted(score.items(), key=lambda x: -x[1])]


def _resolve_project(db, name):
    """A project argument (key, folder name or title, any case) -> its key, or None."""
    if not name:
        return None
    n = str(name).strip().lower()
    for key, title in projects(db):
        if n in (str(key).lower(), str(title or "").lower()) or n in str(title or "").lower() or n in str(key).lower():
            return key
    return False


def search_tool(db, embed_fn, clock_ms=None):
    """The doc_search tool: fn(args) -> Paid({query, project, hits[...], how}, cost) (or a plain dict when only the
    exact-word search ran)."""
    from . import pause, semantic
    from .tools import Paid, ToolError
    now_ms = clock_ms or (lambda: int(time.time() * 1000))

    def doc_search(args):
        q = (args.get("query") or "").strip()
        if not q:
            raise ToolError("bad_args", "要说查什么（query）")
        limit = max(1, min(int(args.get("limit") or 4), 6))
        proj = _resolve_project(db, args.get("project"))
        if proj is False:
            raise ToolError("bad_args", "没有这个项目，可选：" + "、".join(t for _, t in projects(db)))
        if not db[COLL].find_one({}, {"_id": 1}):
            raise ToolError("not_available", "资料库还是空的，资料还没导入")
        vec_ids, info, how = [], None, []
        if embed_fn is not None and not pause.budget_stop(db, now_ms()):
            try:
                cands, info = semantic.search(db, q, ["doc"], limit * 3, embed_fn, pool=4 if proj else 2)
                vec_ids = [c["sid"] for c in cands if not proj or (c.get("ref") or {}).get("project") == proj]
                how.append("按意思")
            except semantic.EmbedError:
                pass
        kw_ids = keyword_hits(db, q, proj)
        # an entry asked for by its id (HW-035, kb-...-002) comes first, whatever the vectors say
        pinned = [r["_id"] for w in words(q) for r in db[COLL].find({**({"project": proj} if proj else {}), "cid": w}, {"_id": 1})]
        if kw_ids:
            how.append("按字面：" + "、".join(words(q)[:6]) if words(q) else "按字面")
        hits = []
        for _id in list(dict.fromkeys(pinned + merge(vec_ids, kw_ids))):
            c = db[COLL].find_one({"_id": _id}, {"_id": 0, "hash": 0})
            if not c:
                continue
            text = c.get("text") or ""
            hits.append({"project": c.get("project_title"), "id": c.get("cid"), "title": c.get("title"),
                         "status": c.get("status"), "source": (c.get("source") or c.get("doc") or "")[:120],
                         **({"branch": c["branch"]} if c.get("branch") else {}),
                         "text": text[:SNIPPET] + ("…" if len(text) > SNIPPET else "")})
            if len(hits) >= limit:
                break
        data = {"query": q, "project": next((t for k, t in projects(db) if k == proj), None) if proj else "全部",
                "how": "、".join(how) or "没找到", "hits": hits,
                "say": "答案只用这些片段里有的；说出处（项目和条目号）；状态是推测 / 未解决 / 没实板验证的要说明；片段里没有就说资料里没写"}
        if not hits:
            data["note"] = "资料里没找到，换个说法或说出型号 / 条目号再查"
        return Paid(data, semantic.cost(info)) if info else data
    return doc_search


# ---- CLI -----------------------------------------------------------------------------------------------------------

def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="python3 -m mind.docs_kb", description="hello 资料库: import / search")
    ap.add_argument("--hubmix-env", default=os.path.expanduser("~/soul-spike/hello.env"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("import", help="import every project folder under ROOT into doc_chunks")
    i.add_argument("root")
    i.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("search", help="one query (one small paid call)")
    s.add_argument("query")
    s.add_argument("--project")
    a = ap.parse_args(argv)
    if a.cmd == "import" and a.dry_run:
        for name, chunks in load_root(a.root):
            L = sorted(len(c["text"]) for c in chunks) or [0]
            print(f"{project_key(name)} ({project_title(name)}): {len(chunks)} chunks, chars median {L[len(L) // 2]}, "
                  f"max {L[-1]}, total {sum(L)}")
        return
    from . import settings as settings_mod
    from . import semantic
    hub = settings_mod.load_env(a.hubmix_env)
    uri = os.environ.get("MONGO_URI") or hub.get("MONGO_URI")
    if not uri:
        raise SystemExit("MONGO_URI missing")
    from pymongo import MongoClient
    db = MongoClient(uri, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000)["hello"]
    if a.cmd == "import":
        db[COLL].create_index([("project", 1)])
        print(json.dumps(import_root(db, a.root), ensure_ascii=False, indent=1))
        return
    from .runlog import start, hello_log
    start("docs_kb", hub, a)
    fn = semantic.embedder(os.environ.get("DASHSCOPE_API_KEY") or hub.get("DASHSCOPE_API_KEY"))
    out = search_tool(db, fn)({"query": a.query, "project": a.project})
    data = out.data if hasattr(out, "data") else out
    print(json.dumps(data, ensure_ascii=False, indent=1)[:4000])
    lg = hello_log.get()
    if lg is not None:
        lg.flush(5)


if __name__ == "__main__":
    sys.exit(main())
