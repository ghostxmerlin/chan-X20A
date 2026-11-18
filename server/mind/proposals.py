"""Proposals (L3, 2026-11-18; docs/hello-tools.md §2): a C-level tool asked by anyone but the owner in person does not
run; it becomes one pending proposal the owner approves or rejects on the page (待确认). Approve = the tool runs then,
as the owner. Mongo hello.proposals {_id, tool, title, args, by, trace, at, state pending/approved/rejected/expired/
failed, result, error, decided_at, decided_by}. A proposal left PROPOSAL_TTL_MS expires.
"""
import time
import uuid

from .runlog import hello_log

PROPOSAL_TTL_MS = 24 * 3600_000
MAX_PENDING = 50


class Proposals:
    def __init__(self, db, clock_ms=None, emit=None):
        self.c = db["proposals"]
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self.emit = emit or hello_log.emit
        try:
            self.c.create_index([("state", 1), ("at", -1)])
        except Exception:                               # noqa: BLE001
            pass

    def _expire(self, now):
        for d in list(self.c.find({"state": "pending", "at": {"$lt": now - PROPOSAL_TTL_MS}}, {"_id": 1})):
            self.c.update_one({"_id": d["_id"], "state": "pending"}, {"$set": {"state": "expired", "decided_at": now}})

    def create(self, tool, title, args, by=None, trace=None, summary=None):
        now = self.clock_ms()
        self._expire(now)
        same = self.c.find_one({"state": "pending", "tool": tool, "args": args})
        if same:                                        # the same ask twice: one proposal
            return {"proposal": same["_id"], "state": "pending", "again": True,
                    "say": "这个要主人确认，我已经提给主人了"}
        if len(list(self.c.find({"state": "pending"}, {"_id": 1}))) >= MAX_PENDING:
            return {"proposal": None, "state": "full", "say": "待确认的事太多了，先请主人处理一下"}
        pid = f"q_{uuid.uuid4().hex[:10]}"
        doc = {"_id": pid, "tool": tool, "title": title, "summary": summary or title, "args": args, "by": by,
               "trace": trace, "at": now, "state": "pending"}
        self.c.insert_one(doc)
        self.emit("call.proposal", trace=trace, by=by, sum=f"待确认：{(summary or title)[:50]}",
                  extra={"proposal": pid, "tool": tool, "action": "create"})
        return {"proposal": pid, "state": "pending", "say": "这个要主人确认，我已经提给主人了"}

    def list(self, state="pending", limit=30):
        now = self.clock_ms()
        self._expire(now)
        flt = {} if state in (None, "all") else {"state": state}
        return list(self.c.find(flt).sort("at", -1).limit(limit))

    def decide(self, pid, approve, run, by="owner:page"):
        """run(tool, args) -> envelope (Tools.call as the owner). -> the updated proposal."""
        now = self.clock_ms()
        self._expire(now)
        doc = self.c.find_one({"_id": pid})
        if doc is None:
            raise KeyError("没有这条提议")
        if doc["state"] != "pending":
            raise ValueError(f"这条已经是「{doc['state']}」了")
        if not approve:
            self.c.update_one({"_id": pid}, {"$set": {"state": "rejected", "decided_at": now, "decided_by": by}})
            self.emit("call.proposal", trace=doc.get("trace"), by=by, sum=f"不同意：{doc['summary'][:50]}",
                      extra={"proposal": pid, "tool": doc["tool"], "action": "reject"})
            return self.c.find_one({"_id": pid})
        env = run(doc["tool"], doc["args"])
        state = "approved" if env.get("ok") else "failed"
        self.c.update_one({"_id": pid}, {"$set": {"state": state, "decided_at": now, "decided_by": by,
                                                  "result": env.get("data"), "error": env.get("error")}})
        self.emit("call.proposal", trace=doc.get("trace"), by=by, ok=env.get("ok"),
                  sum=f"同意：{doc['summary'][:50]}" + ("" if env.get("ok") else f"（失败：{(env.get('error') or {}).get('message', '')[:30]}）"),
                  extra={"proposal": pid, "tool": doc["tool"], "action": "approve"})
        return self.c.find_one({"_id": pid})
