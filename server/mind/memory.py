"""Evidence-backed, immutable memory revisions; derived from independent observation windows."""
import copy
import hashlib
import json
import math
import threading
import time
from collections import defaultdict

from . import settings

DAY = 86_400_000
WINDOW = 600_000
SOURCES = {'speech', 'tingwu', 'sounds', 'eye_minutes', 'vision'}
STATUSES = {'candidate', 'known', 'review', 'disabled'}
_LOCK = threading.RLock()


class MemoryConflict(Exception):
    pass


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _clean(doc):
    return {k: v for k, v in doc.items() if k != '_id'} if doc else None


def _page(offset, limit):
    if type(offset) is not int or type(limit) is not int or not 0 <= offset <= 100_000 or not 1 <= limit <= 100:
        raise ValueError('分页范围不合法')


class MemoryStore:
    def __init__(self, db, lane='live'):
        if lane not in ('live', 'replay'):
            raise ValueError('memory lane must be live/replay')
        self.db, self.lane = db, lane
        self.evidence = db['mind_memory_evidence']
        self.memories = db['mind_memories']

    def ensure_indexes(self):
        self.evidence.create_index([('lane', 1), ('scope_id', 1), ('window_ms', -1), ('version', -1)], unique=True)
        self.memories.create_index([('lane', 1), ('memory_id', 1), ('version', -1)], unique=True)
        self.memories.create_index([('lane', 1), ('scope_id', 1), ('changed_at', -1)])

    def _current(self, memory_id):
        return _clean(self.memories.find_one({'lane': self.lane, 'memory_id': memory_id}, {'_id': 0}, sort=[('version', -1)]))

    def _heads_pipeline(self, scope_id=None):
        match = {'lane': self.lane}
        if scope_id is not None:
            match['scope_id'] = scope_id
        return [{'$match': match}, {'$sort': {'version': -1}},
                {'$group': {'_id': '$memory_id', 'doc': {'$first': '$$ROOT'}}}, {'$replaceRoot': {'newRoot': '$doc'}}]

    def _windows(self, scope_id, start_ms, end_ms):
        pipe = [{'$match': {'lane': self.lane, 'scope_id': scope_id, 'window_ms': {'$gte': start_ms, '$lt': end_ms}}},
                {'$sort': {'window_ms': -1, 'version': -1}},
                {'$group': {'_id': '$window_ms', 'doc': {'$first': '$$ROOT'}}}, {'$replaceRoot': {'newRoot': '$doc'}},
                {'$sort': {'window_ms': -1}}, {'$limit': 8641}]
        return [_clean(x) for x in self.evidence.aggregate(pipe)]

    def observe(self, scope_id, window_ms, observations, coverage, source_version, now_ms, s):
        if scope_id not in ('office', 'home', 'friends') or type(window_ms) is not int:
            raise ValueError('invalid memory scope/window')
        if not isinstance(observations, list) or len(observations) > 32 or not isinstance(coverage, dict):
            raise ValueError('invalid memory observations/coverage')
        cov = {}
        for key in ('hearing_s', 'vision_s'):
            value = coverage.get(key, 0)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or not 0 <= value <= 600:
                raise ValueError('invalid observation coverage')
            cov[key] = value
        rows = []
        for obs in observations:
            if not isinstance(obs, dict):
                raise ValueError('invalid observation')
            for k, cap in (('key', 80), ('statement', 160), ('subject_id', 80)):
                if not isinstance(obs.get(k), str) or not 1 <= len(obs[k]) <= cap:
                    raise ValueError(f'invalid observation {k}')
            if obs.get('kind') not in ('habit', 'routine') or obs.get('modality') not in ('hearing', 'vision') or obs.get('relation', 'support') not in ('support', 'contradict'):
                raise ValueError('invalid observation kind/modality/relation')
            refs = obs.get('refs')
            if not isinstance(refs, list) or not 1 <= len(refs) <= 12 or any(not isinstance(r, dict) or r.get('c') not in SOURCES for r in refs):
                raise ValueError('memory requires raw observation references')
            if cov[obs['modality']+'_s'] <= 0:
                continue
            rows.append({k: copy.deepcopy(obs[k]) for k in ('key', 'statement', 'kind', 'subject_id', 'modality', 'refs')} |
                        {'relation': obs.get('relation', 'support')})
        payload = {'observations': rows, 'coverage': cov}
        digest = fingerprint(payload)
        flt = {'lane': self.lane, 'scope_id': scope_id, 'window_ms': window_ms}
        with _LOCK:
            previous = self.evidence.find_one(flt, {'_id': 0}, sort=[('version', -1)])
            if previous and previous['fingerprint'] == digest:
                return previous['version']
            version = previous['version'] + 1 if previous else 1
            try:
                self.evidence.insert_one({**flt, **payload, 'version': version, 'fingerprint': digest,
                                          'source_version': str(source_version)[:128], 'changed_at': now_ms,
                                          'supersedes': previous['version'] if previous else None})
            except Exception as e:
                if getattr(e, 'code', None) != 11000:
                    raise
                raise MemoryConflict('观察窗口已更新，稍后重试') from None
        return version

    def _rollup(self, scope_id, now_ms, s):
        windows = self._windows(scope_id, now_ms-60*DAY, now_ms+1)
        groups, coverage = {}, defaultdict(lambda: defaultdict(float))
        for w in windows:
            at = w['window_ms']
            t = settings.local_time(at, s['tz'])
            day, hour = t.date().isoformat(), t.hour
            day_type = 'workday' if t.isoweekday() in s['work']['days'] else 'restday'
            if at >= now_ms-28*DAY:
                # Ten-minute windows can straddle local hours in quarter-hour time zones.
                for offset in range(10):
                    mt = settings.local_time(at+offset*60_000, s['tz'])
                    md = mt.date().isoformat()
                    dt = 'workday' if mt.isoweekday() in s['work']['days'] else 'restday'
                    for modality in ('hearing', 'vision'):
                        coverage[(dt, mt.hour, modality)][md] += w['coverage'][modality+'_s']/10
            for o in w['observations']:
                if o['kind'] == 'routine' and at < now_ms-28*DAY:
                    continue
                key = (o['kind'], o['subject_id'], o['key'], o['modality'])
                if o['kind'] == 'routine':
                    key += (day_type, hour)
                mid = 'm'+fingerprint([scope_id, *key])[:24]
                if mid not in groups:
                    groups[mid] = {'memory_id': mid, 'kind': o['kind'], 'scope_id': scope_id, 'subject_id': o['subject_id'],
                                   'key': o['key'], 'modality': o['modality'], 'statement': o['statement'],
                                   'supports': {}, 'contradicts': {}, 'evidence_refs': [], 'evidence_snippets': [],
                                   **({'day_type': day_type, 'hour': hour} if o['kind']=='routine' else {})}
                g = groups[mid]
                g['supports' if o['relation']=='support' else 'contradicts'][at] = day
                if len(g['evidence_refs']) < 12:
                    g['evidence_refs'].append({'window_ms': at, 'version': w['version'], 'refs': o['refs'], 'relation': o['relation']})
                    g['evidence_snippets'].append(o['statement'])
        for g in groups.values():
            support, contradictions = g.pop('supports'), g.pop('contradicts')
            g.update(support_days=len(set(support.values())), observed_windows=len(support), conflict_windows=len(contradictions),
                     first_seen=min(support or contradictions), last_seen=max(support or contradictions),
                     status='known' if len(set(support.values())) >= 3 and len(support) >= 6 else 'candidate')
            if g['kind']=='routine':
                cov = coverage[(g['day_type'], g['hour'], g['modality'])]
                valid_days = {d for d, seconds in cov.items() if seconds >= 900}
                activity_days = valid_days & set(support.values())
                g.update(observed_days=len(valid_days), activity_days=len(activity_days),
                         frequency=len(activity_days)/len(valid_days) if valid_days else None,
                         coverage_s=round(sum(cov.values()), 1), status='known' if len(valid_days)>=5 else 'candidate')
            if not support or (len(contradictions)>=2 and max(contradictions)>max(support)):
                g['status'] = 'review'
            if now_ms-g['last_seen'] >= 30*DAY:
                g['status'] = 'review'
        return groups

    def _append(self, row, previous, actor, reason, now_ms):
        expected = previous['version'] if previous else 0
        rec = {**copy.deepcopy(row), 'lane': self.lane, 'version': expected+1, 'changed_at': now_ms,
               'actor': actor, 'reason': reason, 'supersedes': expected or None}
        rec.pop('_id', None)
        try:
            self.memories.insert_one(rec)
        except Exception as e:
            if getattr(e, 'code', None) != 11000:
                raise
            raise MemoryConflict('认识已更新，请刷新后重试') from None
        return rec

    def reconcile(self, scope_id, now_ms, s):
        with _LOCK:
            rollup = self._rollup(scope_id, now_ms, s)
            heads = {_clean(r)['memory_id']: _clean(r) for r in self.memories.aggregate(self._heads_pipeline(scope_id)+[{'$limit': 2000}])}
            changed = 0
            for mid in sorted(set(heads) | set(rollup)):
                old, new = heads.get(mid), rollup.get(mid)
                # Disposable statistics are not the authoritative memory statement/version.
                # Repeated support must refresh recency without flooding immutable history.
                metrics = {k: new[k] for k in ('last_seen', 'support_days', 'observed_windows', 'conflict_windows',
                                               'observed_days', 'activity_days', 'frequency', 'coverage_s') if new and k in new}
                self.db['mind_memory_jobs'].update_one({'_id': f'metrics:{self.lane}:{mid}'},
                    {'$set': {'metrics': metrics, 'updated_at': now_ms}}, upsert=True)
                if old and old.get('manual'):
                    continue
                if new is None:
                    if old['status'] == 'review':
                        continue
                    new = {**old, 'status': 'review'}
                elif old:
                    new['statement'] = old['statement']
                    new['first_seen'] = min(new['first_seen'], old['first_seen'])
                # A repeated hit isn't a semantic revision. Status, day evidence and distribution
                # changes are revisions; details remain traceable in the evidence ledger.
                keys = ('statement', 'status', 'support_days', 'conflict_windows', 'observed_days', 'activity_days', 'frequency')
                if old and all(old.get(k)==new.get(k) for k in keys):
                    continue
                self._append(new, old, 'worker', '证据修订' if old else '形成候选', now_ms)
                changed += 1
            return changed

    def _with_metrics(self, row):
        metrics = self.db['mind_memory_jobs'].find_one({'_id': f"metrics:{self.lane}:{row['memory_id']}"}, {'_id': 0})
        return {**row, 'latest_evidence': (metrics or {}).get('metrics') or {}}

    def list(self, scope_id, offset=0, limit=20, status=None):
        _page(offset, limit)
        if status is not None and status not in STATUSES:
            raise ValueError('unknown memory status')
        pipeline = self._heads_pipeline(scope_id)
        if status:
            pipeline += [{'$match': {'status': status}}]
        count = list(self.memories.aggregate(pipeline+[{'$count': 'total'}]))
        items = [_clean(x) for x in self.memories.aggregate(pipeline+[{'$sort': {'changed_at': -1, 'memory_id': 1}},
                                                                    {'$skip': offset}, {'$limit': limit}])]
        return {'items': [self._with_metrics(r) for r in items], 'total': count[0]['total'] if count else 0, 'offset': offset, 'limit': limit}

    def history(self, memory_id, offset=0, limit=20):
        _page(offset, limit)
        flt = {'lane': self.lane, 'memory_id': memory_id}
        return {'items': [_clean(x) for x in self.memories.find(flt, {'_id': 0}).sort('version', -1).skip(offset).limit(limit)],
                'total': self.memories.count_documents(flt), 'offset': offset, 'limit': limit}

    def edit(self, memory_id, expected_version, action, statement=None, restore_version=None, now_ms=None):
        if action not in ('correct', 'confirm', 'disable', 'restore'):
            raise ValueError('unknown memory action')
        now_ms = int(time.time()*1000) if now_ms is None else now_ms
        with _LOCK:
            old = self._current(memory_id)
            if not old:
                raise KeyError('没有这条认识')
            if type(expected_version) is not int or expected_version != old['version']:
                raise MemoryConflict('认识已更新，请刷新后重试')
            new = dict(old)
            if action=='correct':
                if not isinstance(statement, str) or not 1 <= len(statement.strip()) <= 160:
                    raise ValueError('认识内容需要 1–160 字')
                new.update(statement=statement.strip(), status='known')
            elif action=='confirm':
                new['status'] = 'known'
            elif action=='disable':
                new['status'] = 'disabled'
            elif action=='restore':
                if type(restore_version) is not int:
                    raise ValueError('恢复需要历史版本')
                saved = self.memories.find_one({'lane': self.lane, 'memory_id': memory_id, 'version': restore_version}, {'_id': 0})
                if not saved:
                    raise KeyError('没有这个历史版本')
                new = _clean(saved)
            new['manual'] = True
            return self._append(new, old, 'user', action, now_ms)

    def recall(self, scope_id, now_ms, limit=12):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('invalid recall limit')
        pipe = self._heads_pipeline(scope_id)+[{'$match': {'status': 'known'}},
            {'$sort': {'manual': -1, 'last_seen': -1}}, {'$limit': 2000}]
        result = []
        for item in self.memories.aggregate(pipe):
            row = self._with_metrics(_clean(item))
            seen = row['latest_evidence'].get('last_seen', row.get('last_seen', 0))
            if row.get('manual') or now_ms-seen < 30*DAY:
                result.append(row)
                if len(result)>=limit:
                    break
        return result
