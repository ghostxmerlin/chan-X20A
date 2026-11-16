"""Test helpers for the run/audit log (server/common/hello_log.py): an in-memory stand-in for the
pymongo database and capture_log(), which starts a logger on it with a throwaway spool folder.

    logger, db = capture_log("mind")
    hello_log.emit("call.eye", sum="x")
    logger.flush()
    db.flows()            # every flow record, oldest first
"""
import atexit
import copy
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server" / "common"))
import hello_log                     # noqa: E402

T0 = 1794300000000          # 2026-11-10 16:40:00 +08:00


class FakeBulkWriteError(Exception):
    """Same shape as pymongo.errors.BulkWriteError: .details["writeErrors"] = [{index, code, errmsg}]."""

    def __init__(self, details):
        super().__init__("batch op errors occurred")
        self.details = details


class FakeDuplicateKeyError(Exception):
    """Same shape as pymongo.errors.DuplicateKeyError (single-document write)."""
    code = 11000


class FakeCollection:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.docs = {}                   # _id -> doc, in insertion order

    def _write(self, op, *info, ids=()):
        self.db.calls.append((op, self.name) + info)
        if self.db.delay_s:
            time.sleep(self.db.delay_s)
        if self.db.fail is not None:
            raise self.db.fail
        for i in ids:
            if i in self.db.poison:
                raise self.db.poison[i]

    def insert_many(self, docs, ordered=True):
        docs = list(docs)
        self._write("insert_many", len(docs), ordered, ids=[d.get("_id") for d in docs])
        errors = []
        with self.db.lock:
            for i, doc in enumerate(docs):
                if doc["_id"] in self.docs:
                    errors.append({"index": i, "code": 11000, "errmsg": "E11000 duplicate key error"})
                    if ordered:
                        break
                else:
                    self.docs[doc["_id"]] = copy.deepcopy(doc)
        if errors:
            raise FakeBulkWriteError({"writeErrors": errors, "writeConcernErrors": [],
                                      "nInserted": len(docs) - len(errors)})

    def update_one(self, flt, update, upsert=False):
        self._write("update_one", flt, upsert, ids=[flt.get("_id")])
        with self.db.lock:
            self._apply_update(flt, update, upsert)

    def bulk_write(self, ops, ordered=True):
        """UpdateOne requests only: pymongo.UpdateOne (private _filter/_doc/_upsert) or anything with
        .filter/.update/.upsert (hello_log's stand-in when pymongo is missing)."""
        reqs = []
        for op in ops:
            flt = getattr(op, "_filter", None)
            reqs.append((op.filter, op.update, op.upsert) if flt is None else (flt, op._doc, op._upsert))
        ids = [flt.get("_id") for flt, _, _ in reqs]
        self._write("bulk_write", ids, ordered, ids=ids)
        with self.db.lock:
            for flt, update, upsert in reqs:
                self._apply_update(flt, update, upsert)

    def _apply_update(self, flt, update, upsert):
        doc = self.find_one(flt)
        if doc is None:
            if not upsert:
                return
            doc = dict(flt)
            doc.update(update.get("$setOnInsert", {}))
            self.docs[doc["_id"]] = doc
        doc.update(update.get("$set", {}))

    def create_index(self, keys, **kw):
        self._write("create_index", keys, kw)
        return kw.get("name") or "_".join(f"{k}_{d}" for k, d in keys)

    def find_one(self, flt):
        return next(iter(self.find(flt)), None)

    def find(self, flt=None):
        """Documents whose fields equal every value in `flt` (plain equality only)."""
        return [d for d in self.docs.values() if all(d.get(k) == v for k, v in (flt or {}).items())]


class FakeDB:
    """Collections by name. Set .fail to an exception to make every write raise it, .delay_s to
    make every write slow, .poison[_id] = exception to make any write touching that _id raise it.
    .calls lists (op, collection, ...) for every write and create_index."""

    def __init__(self):
        self.collections = {}
        self.calls = []
        self.fail = None
        self.delay_s = 0
        self.poison = {}
        self.lock = threading.RLock()

    def __getitem__(self, name):
        with self.lock:
            if name not in self.collections:
                self.collections[name] = FakeCollection(self, name)
            return self.collections[name]

    def list_collection_names(self):
        return list(self.collections)

    def flows(self, month=None):
        """Flow records (log_YYYYMM), oldest first; one month or all of them."""
        names = [n for n in self.collections if n.startswith("log_") and (month is None or n == "log_" + month)]
        docs = [d for n in names for d in self.collections[n].docs.values()]
        return sorted(docs, key=lambda d: (d.get("at", 0), d.get("seq", 0)))

    def bodies(self, month):
        """{sha: text} of logbody_YYYYMM."""
        col = self.collections.get("logbody_" + month)
        return {k: d["text"] for k, d in col.docs.items()} if col else {}

    def ops(self, op, collection=None):
        return [c for c in self.calls if c[0] == op and (collection is None or c[1] == collection)]


def temp_dir():
    """A temporary folder, removed when the interpreter exits."""
    path = Path(tempfile.mkdtemp(prefix="hello-log-test-"))
    atexit.register(shutil.rmtree, str(path), True)
    return path


def capture_log(svc="test", *, db=None, spool_dir=None, clock_ms=None, **kw):
    """hello_log.init() on a FakeDB (or the given db) with a temporary spool folder.
    Returns (logger, db). The logger becomes the module-level one, so hello_log.emit() lands in db."""
    db = FakeDB() if db is None else db
    spool = spool_dir if spool_dir is not None else temp_dir()
    logger = hello_log.init(svc, db=db, spool_dir=spool, clock_ms=clock_ms or (lambda: T0), **kw)
    return logger, db
