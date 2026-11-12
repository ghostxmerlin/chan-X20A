"""Test helpers for server/mind: an in-memory stand-in for the pymongo database and record
factories whose fields match what server/ear/hello_db.py writes."""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

T0 = 1794300000000          # 2026-11-10 16:40:00 +08:00, a minute boundary


def _get(doc, key):
    cur = doc
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


_MISSING = object()


class FakeDuplicateKey(Exception):
    code = 11000


def _match(doc, flt):
    for key, cond in (flt or {}).items():
        if key == "$or":
            if not any(_match(doc, f) for f in cond):
                return False
            continue
        value = _get(doc, key)
        if isinstance(cond, dict) and any(k.startswith("$") for k in cond):
            for op, arg in cond.items():
                if op == "$exists":
                    if (value is not _MISSING) != bool(arg):
                        return False
                elif value is _MISSING:
                    return False
                elif op == "$gte" and not value >= arg: return False
                elif op == "$gt" and not value > arg: return False
                elif op == "$lt" and not value < arg: return False
                elif op == "$lte" and not value <= arg: return False
                elif op == "$in" and value not in arg: return False
                elif op == "$ne" and value == arg: return False
        elif value is _MISSING or value != cond:
            return False
    return True


class FakeCursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, key, direction=1):
        keys = key if isinstance(key, list) else [(key, direction)]
        for k, d in reversed(keys):
            self.docs.sort(key=lambda x: (_get(x, k) is _MISSING, _get(x, k) if _get(x, k) is not _MISSING else 0),
                           reverse=d < 0)
        return self

    def limit(self, n):
        if n:
            self.docs = self.docs[:n]
        return self

    def skip(self, n):
        self.docs = self.docs[n:]
        return self

    def __iter__(self):
        return iter(copy.deepcopy(self.docs))


class FakeCollection:
    def __init__(self):
        self.docs = []
        self.fail = None

    def _check(self):
        if self.fail:
            raise self.fail

    def find(self, flt=None, projection=None):
        self._check()
        return FakeCursor([d for d in self.docs if _match(d, flt)])

    def find_one(self, flt=None, projection=None, sort=None):
        self._check()
        cur = self.find(flt)
        if sort:
            cur.sort(sort)
        for d in cur:
            return d
        return None

    def count_documents(self, flt):
        self._check()
        return sum(1 for d in self.docs if _match(d, flt))

    def aggregate(self, pipeline):
        """The small Mongo aggregation subset used by memory; unknown stages fail loudly."""
        self._check()
        rows = copy.deepcopy(self.docs)
        for stage in pipeline:
            op, value = next(iter(stage.items()))
            if op == '$match':
                rows = [r for r in rows if _match(r, value)]
            elif op == '$sort':
                rows = FakeCursor(rows).sort(list(value.items())).docs
            elif op == '$group':
                grouped = {}
                for row in rows:
                    key = _get(row, value['_id'][1:])
                    if key not in grouped:
                        grouped[key] = {'_id': key}
                        for k, expr in value.items():
                            if k != '_id':
                                assert expr == {'$first': '$$ROOT'}
                                grouped[key][k] = row
                rows = list(grouped.values())
            elif op == '$replaceRoot':
                rows = [_get(r, value['newRoot'][1:]) for r in rows]
            elif op == '$skip':
                rows = rows[value:]
            elif op == '$limit':
                rows = rows[:value]
            elif op == '$count':
                rows = [{value: len(rows)}] if rows else []
            else:
                raise NotImplementedError(op)
        return rows

    def insert_one(self, doc):
        self._check()
        if '_id' in doc and any(d.get('_id', _MISSING) == doc['_id'] for d in self.docs):
            raise FakeDuplicateKey('duplicate _id')
        self.docs.append(copy.deepcopy(doc))

    def find_one_and_update(self, flt, update, upsert=False, return_document=False):
        self._check()
        for row in self.docs:
            if _match(row, flt):
                old = copy.deepcopy(row)
                row.update(copy.deepcopy(update.get('$set', {})))
                return copy.deepcopy(row) if return_document else old
        if upsert:
            row = {k:v for k,v in flt.items() if not k.startswith('$') and not isinstance(v,dict)}
            row.update(copy.deepcopy(update.get('$set', {})))
            self.insert_one(row)
            return copy.deepcopy(row) if return_document else None
        return None

    def replace_one(self, flt, doc, upsert=False):
        self._check()
        for i, d in enumerate(self.docs):
            if _match(d, flt):
                self.docs[i] = copy.deepcopy(doc)
                return
        if upsert:
            self.docs.append(copy.deepcopy(doc))

    def update_one(self, flt, update, upsert=False):
        self._check()
        for d in self.docs:
            if _match(d, flt):
                d.update(copy.deepcopy(update.get("$set", {})))
                return
        if upsert:
            new = {k: v for k, v in flt.items() if not k.startswith("$")}
            new.update(copy.deepcopy(update.get("$set", {})))
            self.docs.append(new)

    def create_index(self, keys, unique=False, **k):
        self.__dict__.setdefault("indexes", []).append((list(keys), unique))
        return "ok"


class FakeDB:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection())

    __getattr__ = __getitem__

    def add(self, collection, *docs):
        for d in docs:
            self[collection].docs.append(copy.deepcopy(d))
        return self

    def count(self, collection):
        return len(self[collection].docs)


def session(key, start_ms, end_ms=None, source="stackchan-dev", kind=None):
    d = {"session": key, "source": source, "start_ms": start_ms}
    if end_ms is not None:
        d["end_ms"] = end_ms
    if kind:
        d["kind"] = kind
    return d


def speech(sess, id, at, text="话", band="unknown", name=None, best=None, score=None, loud=12.0,
           self_sounding=False, pos=None, dur=1.0, emotion="neutral"):
    return {"session": sess, "id": id, "t": at, "at": at, "pos": pos, "dur": dur, "text": text, "lang": "zh",
            "emotion": emotion, "event": "speech",
            "speaker": {"band": band, "name": name if band == "known" else None, "best": best or name, "score": score},
            "voice": {"p": 0.8, "band": "voice", "loud_db": loud, "rate": 4.0},
            "self": {"sounding": self_sounding, "moving": False}, "engine": "sensevoice"}


def tingwu(sess, n, at, text, pos=None):
    return {"session": sess, "kind": "sentence", "n": n, "at": at, "text": text, "pos": pos, "engine": "tingwu",
            "self": {"sounding": False, "moving": False}}


def sound(sess, id, at, kind="typing", label="键盘打字", loud=10.0, pos=None):
    return {"session": sess, "id": id, "t": at, "kind": kind, "label": label, "pos": pos, "dur": 1.0, "peak": 0.3,
            "loud_db": loud, "self": {"sounding": False, "moving": False}}


def vision(sess, event, at, face=None, person=None, name=None, t_us=None, gender=None, age_group=None):
    d = {"session": sess, "event": event, "at": at, "received": at, "t_us": t_us if t_us is not None else at * 1000}
    for k, v in (("face", face), ("person", person), ("name", name), ("gender", gender), ("age_group", age_group)):
        if v is not None:
            d[k] = v
    return d
