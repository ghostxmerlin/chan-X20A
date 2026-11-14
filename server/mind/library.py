"""The library: people cards, people notes, memories and knowledge, plus voice labels (backend phase 1).

Every change is a new version document (append-only; old versions are never touched, nothing is deleted).
The owner's page writes through put() / review(); the memory extraction job (memory_extract.py, long-term memory
phase A) writes memory_items through put_derived() only, never from the page. Entry text is never logged.

Long-term memory phase A (docs/superpowers/specs/2026-11-14-long-term-memory-assessment.md §2.7, §4):
  memory_items gain lane (live / replay), day, origin (owner / extracted), extract_key + extract_keys (idempotence),
  derived_from [{c, key, version?, pos?}], run {job_id, run_id, model, prompt_version, at}, people [{id, role,
  name_text?}], confidence, basis, salience 0..3, sensitivity [child / others_said / private], emotion, merged_into.
  A version written by the owner (actor owner: 对 / 错 / 改 / 合并 / 停用) locks the item: the job never writes it again.
  memory_recall is a derived, rebuildable copy of every memory head (one row per memory_id) for indexed reads; it is
  refreshed on every write and can be rebuilt from memory_items with rebuild_recall().
"""
import copy
import datetime
import hashlib
import json
import re
import threading
import uuid

from . import speech_settings

COLLECTIONS = {'people_cards': 'person_id', 'people_notes': 'note_id', 'memory_items': 'memory_id', 'knowledge_items': 'kid'}
PREFIX = {'people_cards': 'p', 'people_notes': 'n', 'memory_items': 'e', 'knowledge_items': 'k'}
ID_RE = r'^(owner|[pnek]_[a-f0-9]{12})$'
_LOCK = threading.RLock()
CONFLICT = '已被改过，请刷新后重试'
LANES = ('live', 'replay')
CONFIDENCE = ('high', 'mid', 'low')
BASIS = ('observed', 'reported', 'owner')
ROLES = ('about', 'speaker', 'present', 'mentioned')
SENSITIVITY = ('child', 'others_said', 'private')
EMOTIONS = ('开心', '难过', '生气', '担心', '疲惫', '兴奋')
PERSON_RE = r'^(owner|hello|room|unknown|p_[a-f0-9]{12})$'
# What a derived_from ref may point to, and the key fields of each (a minute, a segment, a conversation, a chat turn,
# an eye minute, a sentence, or a recording span = session + pos).
REF_KEYS = {'conversations': ('cid',), 'conversation_versions': ('cid',), 'speech': ('session', 'id'),
            'tingwu': ('session', 'n'), 'mind_segments': ('lane', 'start'), 'mind_scenes': ('lane', 'minute'),
            'mind_packages': ('lane', 'minute'), 'chat_turns': ('tid',), 'eye_minutes': ('minute',),
            'owner_signals': ('sid',), 'utterances': ('uid',), 'say_rounds': ('rid',), 'sessions': ('session',),
            'recording': ('session',), 'frames': ('session', 'seq')}
MAX_REFS = 60
# Carried from version to version of a memory (server-side fields; the page never sends them).
MEMORY_CARRY = ('lane', 'day', 'extract_key', 'extract_keys', 'derived_from', 'run', 'people', 'confidence', 'basis',
                'salience', 'sensitivity', 'emotion', 'merged_into', 'scope_id', 'source', 'owner_confirmed')


class LibraryConflict(Exception):
    pass


def _int(v, name, lo=0):
    if type(v) is not int or v < lo:
        raise ValueError(f'{name}需要不小于 {lo} 的整数')
    return v


def _text(v, name, lo, hi):
    if not isinstance(v, str) or not lo <= len(v.strip()) <= hi:
        raise ValueError(f'{name}需要 {lo}–{hi} 字')
    return v.strip()


def _enum(v, name, allowed):
    if v not in allowed:
        raise ValueError(f'{name}取值不对')
    return v


def _strings(v, name, hi=40, limit=10):
    if not isinstance(v, list) or len(v) > limit:
        raise ValueError(f'{name}最多 {limit} 项')
    return [_text(x, name, 1, hi) for x in v]


def _only(d, name, allowed):
    if not isinstance(d, dict):
        raise ValueError(f'{name}格式不对')
    extra = set(d) - set(allowed)
    if extra:
        raise ValueError(f'{name}不能包含：{"、".join(sorted(map(str, extra)))}')


def _page(offset, limit):
    if type(offset) is not int or type(limit) is not int or not 0 <= offset <= 100_000 or not 1 <= limit <= 100:
        raise ValueError('分页范围不合法')


def _ms(v, name):
    return None if v is None else _int(v, name)


def _fp(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def derived_id(lane, key):
    """The memory_id of an extracted memory: the same lane and extract_key always give the same id."""
    return 'e_' + _fp(['memory', lane, key])[:12]


def extract_key(kind, refs, title):
    """Idempotence key: kind + the primary source refs (order-free) + the normalised title."""
    norm = re.sub(r'[\W_]', '', str(title or '')).lower()
    return _fp([kind, sorted(_fp([r.get('c'), r.get('key')]) for r in refs), norm])[:24]


def clean_ref(r):
    """One derived_from ref {c, key: {...}, version?, pos?} -> a clean copy; ValueError when malformed."""
    _only(r, '出处', ('c', 'key', 'version', 'pos'))
    c = r.get('c')
    if c not in REF_KEYS:
        raise ValueError('出处集合不对')
    key = r.get('key')
    _only(key, '出处键', REF_KEYS[c])
    if set(key) != set(REF_KEYS[c]):
        raise ValueError('出处键不全')
    for k, v in key.items():
        if not (isinstance(v, str) and 1 <= len(v) <= 80) and not (type(v) is int and v >= 0):
            raise ValueError('出处键不对')
    out = {'c': c, 'key': dict(key)}
    if r.get('version') is not None:
        out['version'] = _int(r['version'], '出处版本')
    pos = r.get('pos')
    if pos is not None:
        if not isinstance(pos, list) or len(pos) != 2 or any(type(x) is not int or x < 0 for x in pos) or pos[1] < pos[0]:
            raise ValueError('录音位置需要两个递增的非负整数')
        out['pos'] = list(pos)
    elif c == 'recording':
        raise ValueError('录音片段需要位置')
    return out


def clean_people(v):
    if not isinstance(v, list) or len(v) > 12:
        raise ValueError('人物最多 12 个')
    out, seen = [], set()
    for p in v:
        _only(p, '人物', ('id', 'role', 'name_text'))
        pid = p.get('id')
        if not isinstance(pid, str) or not re.match(PERSON_RE, pid):
            raise ValueError('人物编号不对')
        role = _enum(p.get('role'), '人物角色', ROLES)
        name = p.get('name_text') or None
        if name is not None:
            name = _text(name, '听到的称呼', 1, 20)
        k = (pid, role, name)
        if k not in seen:
            seen.add(k)
            out.append({'id': pid, 'role': role, **({'name_text': name} if name else {})})
    return out


def _people_from_subjects(old_people, subjects):
    """An owner edit of subjects: role about follows the subjects; the other roles stay."""
    kept = [p for p in (old_people or []) if p['role'] != 'about' or p['id'] == 'unknown']
    return [*({'id': s, 'role': 'about'} for s in subjects), *kept]


def recall_row(rec):
    """The memory_recall row of one memory head (derived; rebuildable from memory_items)."""
    row = {k: copy.deepcopy(v) for k, v in rec.items() if k != '_id'}
    row['lane'] = rec.get('lane') or 'live'
    row['start_ms'] = (rec.get('when') or {}).get('start_ms')
    row['person_ids'] = sorted({*(rec.get('subjects') or []), *(p['id'] for p in rec.get('people') or [])})
    row['auto'] = bool(rec.get('extract_keys'))
    row['recallable'] = rec.get('status') == 'active' and rec.get('confidence', 'high') in ('high', 'mid')
    return row


class Library:
    def __init__(self, db):
        self.db = db

    def ensure_indexes(self):
        d = self.db
        for c, key in COLLECTIONS.items():
            d[c].create_index([(key, 1), ('version', 1)], unique=True)
        d['people_cards'].create_index([('status', 1), ('changed_at', 1)])
        for k in ('links.voice_names', 'links.face_names', 'aliases'):
            d['people_cards'].create_index([(k, 1)])
        d['people_notes'].create_index([('person_id', 1), ('kind', 1), ('changed_at', 1)])
        d['people_notes'].create_index([('origin', 1), ('changed_at', 1)])
        d['memory_items'].create_index([('scope_id', 1), ('kind', 1), ('when.start_ms', 1)])
        for k in ('subjects', 'topics', 'people.id'):
            d['memory_items'].create_index([(k, 1)])
        d['memory_items'].create_index([('lane', 1), ('extract_keys', 1)])
        d['memory_items'].create_index([('lane', 1), ('status', 1), ('day', 1)])
        d['memory_items'].create_index([('derived_from.c', 1), ('derived_from.key', 1)])
        d['memory_items'].create_index([('origin', 1), ('changed_at', 1)])
        r = d['memory_recall']
        r.create_index([('memory_id', 1)], unique=True)
        r.create_index([('lane', 1), ('recallable', 1), ('salience', -1), ('start_ms', -1)])
        r.create_index([('lane', 1), ('auto', 1), ('changed_at', -1)])
        r.create_index([('lane', 1), ('start_ms', -1)])
        r.create_index([('lane', 1), ('day', 1)])
        for k in ('person_ids', 'topics'):
            r.create_index([('lane', 1), (k, 1)])
        d['memory_uses'].create_index([('memory_id', 1), ('at', -1)])
        d['memory_uses'].create_index([('purpose', 1), ('at', -1)])
        d['library_jobs'].create_index([('step', 1), ('lane', 1), ('day', 1)])
        d['knowledge_items'].create_index([('kind', 1), ('changed_at', 1)])
        for k in ('name', 'aliases', 'heard_as', 'subjects'):
            d['knowledge_items'].create_index([(k, 1)])
        d['voice_labels'].create_index([('lid', 1)], unique=True)
        d['voice_labels'].create_index([('target.session', 1), ('target.speech_id', 1), ('at', 1)])
        d['voice_labels'].create_index([('person_id', 1), ('at', 1)])
        d['voice_labels'].create_index([('review_day', 1)])

    def head(self, c, item_id):
        doc = self.db[c].find_one({COLLECTIONS[c]: item_id}, {'_id': 0}, sort=[('version', -1)])
        if doc:
            doc.pop('_id', None)
        return doc

    def _subjects(self, v):
        out = _strings(v, '涉及对象')
        for s in out:
            if s not in ('owner', 'hello', 'room') and not self.head('people_cards', s):
                raise ValueError('涉及对象需要是已有人物卡')
        return out

    def _card(self, f, scope_id):
        _only(f, '人物卡', ('kind', 'display', 'aliases', 'links', 'treatment', 'scope_ids', 'status'))
        links, treat = f.get('links', {}), f.get('treatment', {})
        _only(links, 'links', ('voice_names', 'face_names'))
        _only(treat, 'treatment', ('tease', 'quote'))
        return {'kind': _enum(f.get('kind'), '人物类别', ('owner', 'regular', 'visitor', 'family', 'unknown')),
                'display': _text(f.get('display'), '称呼', 1, 40),
                'aliases': _strings(f.get('aliases', []), '别名'),
                'links': {'voice_names': _strings(links.get('voice_names', []), '声纹名', 32),
                          'face_names': _strings(links.get('face_names', []), '脸名', 32)},
                'treatment': {'tease': _enum(treat.get('tease', 'words_only'), '调侃', ('ok', 'words_only', 'never')),
                              'quote': _enum(treat.get('quote', 'gist'), '引用', ('ok', 'gist', 'no'))},
                'scope_ids': _strings(f.get('scope_ids', [scope_id]), '场所'),
                'status': _enum(f.get('status', 'confirmed'), '状态', ('confirmed', 'retired'))}

    def _note(self, f, scope_id):
        _only(f, '人物笔记', ('person_id', 'kind', 'text', 'status', 'observed_at'))
        if not isinstance(f.get('person_id'), str) or not self.head('people_cards', f['person_id']):
            raise ValueError('人物卡不存在')
        return {'person_id': f['person_id'],
                'kind': _enum(f.get('kind'), '笔记类别', ('trait', 'fact', 'preference', 'boundary', 'relation')),
                'text': _text(f.get('text'), '内容', 1, 200),
                'status': _enum(f.get('status', 'active'), '状态', ('active', 'disabled', 'retired')),
                'observed_at': _ms(f.get('observed_at'), '观察时间')}

    def _memory(self, f, scope_id):
        _only(f, '记忆', ('kind', 'title', 'statement', 'subjects', 'topics', 'when', 'status'))
        w = f.get('when')
        _only(w, 'when', ('type', 'start_ms', 'end_ms'))
        typ = _enum(w.get('type'), '时间类型', ('point', 'span'))
        start = _int(w.get('start_ms'), '开始时间')
        end = None
        if typ == 'span':
            end = _int(w.get('end_ms'), '结束时间')
            if end < start:
                raise ValueError('结束时间不能早于开始时间')
        topics = _strings(f.get('topics', []), '话题')
        for t in topics:
            if not re.match(r'^k_[a-f0-9]{12}$', t) or not self.head('knowledge_items', t):
                raise ValueError('话题需要是已有知识')
        return {'kind': _enum(f.get('kind'), '记忆类别', ('event', 'told', 'feeling')),
                'title': _text(f.get('title'), '标题', 1, 30), 'statement': _text(f.get('statement'), '内容', 1, 300),
                'subjects': self._subjects(f.get('subjects', [])), 'topics': topics,
                'when': {'type': typ, 'start_ms': start, 'end_ms': end},
                'status': _enum(f.get('status', 'active'), '状态', ('active', 'retired', 'disabled', 'merged'))}

    def _knowledge(self, f, scope_id):
        _only(f, '知识', ('kind', 'name', 'aliases', 'heard_as', 'statement', 'subjects', 'when', 'scope_ids', 'status'))
        w = f.get('when', {})
        _only(w, 'when', ('valid_from', 'valid_to'))
        lo, hi = _ms(w.get('valid_from'), '起始时间'), _ms(w.get('valid_to'), '截止时间')
        if lo is not None and hi is not None and hi < lo:
            raise ValueError('截止时间不能早于起始时间')
        return {'kind': _enum(f.get('kind'), '知识类别', ('place', 'work', 'commitment', 'life', 'term', 'self', 'other')),
                'name': _text(f.get('name'), '名称', 1, 40), 'aliases': _strings(f.get('aliases', []), '别名'),
                'heard_as': _strings(f.get('heard_as', []), '听成'),
                'statement': _text(f.get('statement', ''), '内容', 0, 400),
                'subjects': self._subjects(f.get('subjects', [])), 'when': {'valid_from': lo, 'valid_to': hi},
                'scope_ids': _strings(f.get('scope_ids', [scope_id]), '场所'),
                'status': _enum(f.get('status', 'active'), '状态', ('active', 'stale', 'disabled'))}

    def _server_fields(self, c, key, item_id, scope_id):
        provenance = {'origin': 'owner', 'owner_original': None, 'derived_from': [], 'fusion': None}
        if c == 'people_cards':
            return {key: item_id, 'owner_rotation': False, 'merged_into': None, 'source': {'kind': 'owner', 'ref': None}}
        if c == 'people_notes':
            return {key: item_id, 'scope_id': scope_id, **provenance}
        if c == 'memory_items':
            return {key: item_id, 'scope_id': scope_id, 'source': {'kind': 'owner', 'refs': []}, 'lane': 'live', 'day': None,
                    'origin': 'owner', 'extract_key': None, 'extract_keys': [], 'derived_from': [], 'run': None,
                    'people': [], 'confidence': 'high', 'basis': 'owner', 'salience': 2, 'sensitivity': [],
                    'emotion': None, 'merged_into': None}
        return {key: item_id, **provenance}

    def put(self, c, fields, *, item_id, expected_version, scope_id, now_ms, reason=''):
        if c not in COLLECTIONS:
            raise ValueError('没有这个集合')
        clean = getattr(self, {'people_cards': '_card', 'people_notes': '_note', 'memory_items': '_memory',
                               'knowledge_items': '_knowledge'}[c])(fields, scope_id)
        key = COLLECTIONS[c]
        with _LOCK:
            old = self.head(c, item_id) if item_id else None
            if item_id and not old:
                raise KeyError('没有这一条')
            if old:
                if type(expected_version) is not int or expected_version != old['version']:
                    raise LibraryConflict(CONFLICT)
                for k in ('kind', 'person_id'):
                    if k in clean and clean[k] != old[k]:
                        raise ValueError('类别和所属人物不能改')
            else:
                if expected_version not in (None, 0):
                    raise LibraryConflict(CONFLICT)
                if c == 'people_cards' and clean['kind'] == 'owner':
                    item_id = 'owner'
                    if self.head(c, 'owner'):
                        raise LibraryConflict(CONFLICT)
                else:
                    item_id = f'{PREFIX[c]}_{uuid.uuid4().hex[:12]}'
            server = self._server_fields(c, key, item_id, scope_id)   # owner writes re-stamp provenance
            if old:   # only truly immutable fields carry forward
                for k in ('owner_rotation', 'merged_into', 'scope_id'):
                    if k in old and k in server:
                        server[k] = copy.deepcopy(old[k])
            if c == 'memory_items':
                if old:   # an owner edit of an extracted memory keeps its sources and lane; it becomes the owner's
                    for k in MEMORY_CARRY:
                        if k in old:
                            server[k] = copy.deepcopy(old[k])
                    server['confidence'] = 'high'
                if clean['status'] == 'merged' and (not old or old.get('status') != 'merged'):
                    raise ValueError('合并请用“合并”')
                if clean['status'] != 'merged':
                    server['merged_into'] = None
                server['people'] = _people_from_subjects(server.get('people'), clean['subjects'])
            if c == 'people_cards':
                clean['links']['legacy_ids'] = copy.deepcopy(old['links'].get('legacy_ids', [])) if old else []
            version = old['version'] + 1 if old else 1
            rec = {**clean, **server, 'version': version, 'changed_at': now_ms, 'actor': 'owner',
                   'reason': reason, 'supersedes': old['version'] if old else None}
            try:
                self.db[c].insert_one(copy.deepcopy(rec))
            except Exception as e:
                if getattr(e, 'code', None) != 11000:
                    raise
                raise LibraryConflict(CONFLICT) from None
            if c == 'memory_items':
                self._sync_recall(rec)
            return rec

    # ---- long-term memory phase A: machine writes, owner review, recall copy ----

    def _sync_recall(self, rec):
        self.db['memory_recall'].replace_one({'memory_id': rec['memory_id']}, recall_row(rec), upsert=True)

    def rebuild_recall(self, lane=None):
        """Recompute memory_recall from memory_items (streams every version; no cut). -> rows written."""
        flt = {'lane': lane} if lane else {}
        last, n = None, 0
        for d in self.db['memory_items'].find(flt, {'_id': 0}).sort([('memory_id', 1), ('version', 1)]):
            if last is not None and d['memory_id'] != last['memory_id']:
                self._sync_recall(last)
                n += 1
            last = d
        if last is not None:
            self._sync_recall(last)
            n += 1
        return n

    def find_by_key(self, lane, key):
        """The head of the memory that an extract_key maps to in this lane, or None."""
        hit = self.db['memory_items'].find_one({'lane': lane, 'extract_keys': key}, {'_id': 0, 'memory_id': 1})
        return self.head('memory_items', hit['memory_id']) if hit else None

    def _append_memory(self, old, changes, *, actor, reason, now_ms, origin=None):
        rec = {k: copy.deepcopy(v) for k, v in old.items() if k != '_id'}
        rec.update(copy.deepcopy(changes))
        rec.update(version=old['version'] + 1, changed_at=now_ms, actor=actor, reason=reason, supersedes=old['version'])
        if origin:
            rec['origin'] = origin
        try:
            self.db['memory_items'].insert_one(copy.deepcopy(rec))
        except Exception as e:
            if getattr(e, 'code', None) != 11000:
                raise
            raise LibraryConflict(CONFLICT) from None
        self._sync_recall(rec)
        return rec

    @staticmethod
    def locked(head):
        """True when the extraction job may not write this memory: the owner touched it, or it is off."""
        return head.get('actor') == 'owner' or head.get('status') != 'active'

    def put_derived(self, fields, *, lane, key, run, now_ms, scope_id, day, target_id=None):
        """Machine write of one extracted memory (server-internal; never reachable from the page).

        fields: kind, title, statement, when, derived_from, people, confidence, basis, salience, sensitivity, emotion.
        target_id: an existing memory of the same lane this one is the same event as (cross-day merge); otherwise the
        memory is found by `key` (extract_key) or created with derived_id(lane, key).
        -> (action, rec): created / updated / unchanged / blocked (the owner touched or disabled it: never written)."""
        if lane not in LANES:
            raise ValueError('lane 不对')
        f = self._derived_fields(fields)
        with _LOCK:
            head = None
            if target_id:
                head = self.head('memory_items', target_id)
                if head and (head.get('lane') or 'live') != lane:
                    head = None
            if head is None:
                head = self.find_by_key(lane, key) or self.head('memory_items', derived_id(lane, key))
            hops = 0
            while head and head.get('status') == 'merged' and head.get('merged_into') and hops < 4:
                nxt = self.head('memory_items', head['merged_into'])
                if not nxt or (nxt.get('lane') or 'live') != lane:
                    break
                head, hops = nxt, hops + 1
            if head is None:
                mid = derived_id(lane, key)
                rec = {'memory_id': mid, **f, 'subjects': [p['id'] for p in f['people'] if p['role'] == 'about'
                                                         and p['id'] != 'unknown'],
                       'topics': [], 'status': 'active', 'scope_id': scope_id, 'source': {'kind': 'extracted', 'refs': []},
                       'lane': lane, 'day': day, 'origin': 'extracted', 'extract_key': key, 'extract_keys': [key],
                       'run': copy.deepcopy(run), 'merged_into': None, 'version': 1, 'changed_at': now_ms,
                       'actor': 'worker', 'reason': 'extract', 'supersedes': None}
                try:
                    self.db['memory_items'].insert_one(copy.deepcopy(rec))
                except Exception as e:
                    if getattr(e, 'code', None) != 11000:
                        raise
                    raise LibraryConflict(CONFLICT) from None
                self._sync_recall(rec)
                return 'created', rec
            if self.locked(head):
                return 'blocked', head
            merged = self._merge_derived(head, f)
            keys = list(dict.fromkeys([*(head.get('extract_keys') or []), key]))
            same = all(head.get(k) == merged[k] for k in merged) and keys == (head.get('extract_keys') or [])
            if same:
                return 'unchanged', head
            merged['extract_keys'] = keys
            merged['run'] = copy.deepcopy(run)
            merged['subjects'] = list(dict.fromkeys([*head.get('subjects', []), *(p['id'] for p in merged['people']
                                                     if p['role'] == 'about' and p['id'] != 'unknown')]))
            return 'updated', self._append_memory(head, merged, actor='worker', reason='extract', now_ms=now_ms,
                                                  origin='extracted')

    def _derived_fields(self, f):
        _only(f, '提取的记忆', ('kind', 'title', 'statement', 'when', 'derived_from', 'people', 'confidence', 'basis',
                            'salience', 'sensitivity', 'emotion'))
        w = f.get('when')
        _only(w, 'when', ('type', 'start_ms', 'end_ms'))
        typ = _enum(w.get('type'), '时间类型', ('point', 'span'))
        start = _int(w.get('start_ms'), '开始时间')
        end = _int(w.get('end_ms'), '结束时间') if typ == 'span' else None
        if end is not None and end < start:
            raise ValueError('结束时间不能早于开始时间')
        refs = f.get('derived_from')
        if not isinstance(refs, list) or not 1 <= len(refs) <= MAX_REFS:
            raise ValueError('提取的记忆需要出处')
        sal = f.get('salience')
        if type(sal) is not int or not 0 <= sal <= 3:
            raise ValueError('重要程度 0–3')
        sens = f.get('sensitivity')
        if not isinstance(sens, list) or any(x not in SENSITIVITY for x in sens):
            raise ValueError('敏感标记不对')
        emo = f.get('emotion')
        if emo is not None:
            _only(emo, '情绪', ('label', 'basis'))
            _enum(emo.get('label'), '情绪', EMOTIONS)
            _enum(emo.get('basis'), '情绪来源', ('said', 'voice'))
        return {'kind': _enum(f.get('kind'), '记忆类别', ('event', 'told', 'feeling')),
                'title': _text(f.get('title'), '标题', 1, 30), 'statement': _text(f.get('statement'), '内容', 1, 300),
                'when': {'type': typ, 'start_ms': start, 'end_ms': end},
                'derived_from': [clean_ref(r) for r in refs], 'people': clean_people(f.get('people', [])),
                'confidence': _enum(f.get('confidence'), '置信', CONFIDENCE),
                'basis': _enum(f.get('basis'), '依据', ('observed', 'reported')), 'salience': sal,
                'sensitivity': sorted(set(sens)), 'emotion': copy.deepcopy(emo)}

    @staticmethod
    def _merge_derived(head, f):
        """A new extraction of an existing memory: newest wording, union of sources / people / time / flags."""
        refs, seen = [], set()
        for r in [*(head.get('derived_from') or []), *f['derived_from']]:
            k = _fp(r)
            if k not in seen:
                seen.add(k)
                refs.append(r)
        people, pseen = [], set()
        for p in [*(head.get('people') or []), *f['people']]:
            k = (p['id'], p['role'], p.get('name_text'))
            if k not in pseen:
                pseen.add(k)
                people.append(p)
        ow, nw = head.get('when') or f['when'], f['when']
        lo = min(ow['start_ms'], nw['start_ms'])
        hi = max(ow.get('end_ms') or ow['start_ms'], nw.get('end_ms') or nw['start_ms'])
        when = {'type': 'span' if hi > lo else 'point', 'start_ms': lo, 'end_ms': hi if hi > lo else None}
        return {'kind': head['kind'], 'title': f['title'], 'statement': f['statement'], 'when': when,
                'derived_from': refs[-MAX_REFS:], 'people': people[:12], 'confidence': f['confidence'],
                'basis': f['basis'], 'salience': max(head.get('salience') or 0, f['salience']),
                'sensitivity': sorted({*(head.get('sensitivity') or []), *f['sensitivity']}),
                'emotion': f['emotion'] or head.get('emotion')}

    def review(self, item_id, expected_version, action, *, now_ms, into=None, reason=''):
        """The owner's 对 (confirm) / 错 (wrong: disabled, never deleted) / 合并 (merge into another memory).
        改 is put(). Every one of them is an owner version, so the extraction job never writes the item again."""
        if action not in ('confirm', 'wrong', 'merge'):
            raise ValueError('不认识的操作')
        reason = str(reason or '')[:200]
        with _LOCK:
            head = self.head('memory_items', item_id)
            if not head:
                raise KeyError('没有这一条')
            if type(expected_version) is not int or expected_version != head['version']:
                raise LibraryConflict(CONFLICT)
            if action == 'confirm':
                ch = {'status': 'active', 'owner_confirmed': True, 'confidence': 'high', 'merged_into': None}
            elif action == 'wrong':
                ch = {'status': 'disabled', 'merged_into': None}
            else:
                if not isinstance(into, str) or not re.match(r'^e_[a-f0-9]{12}$', into) or into == item_id:
                    raise ValueError('要并到另一条记忆')
                target = self.head('memory_items', into)
                if not target or (target.get('lane') or 'live') != (head.get('lane') or 'live'):
                    raise ValueError('要并到同一条道上已有的记忆')
                if target.get('status') == 'merged':
                    raise ValueError('那一条已经并到别处了')
                ch = {'status': 'merged', 'merged_into': into}
            return self._append_memory(head, ch, actor='owner', reason={'confirm': '对', 'wrong': '错', 'merge': '合并'}[action]
                                       + (('：' + reason) if reason else ''), now_ms=now_ms)

    def changes(self, lane='live', offset=0, limit=20):
        """Extracted memories (and the owner's later versions of them), latest change first."""
        _page(offset, limit)
        if lane not in LANES:
            raise ValueError('lane 不对')
        flt = {'lane': lane, 'auto': True}
        r = self.db['memory_recall']
        rows = [{k: v for k, v in x.items() if k != '_id'}
                for x in r.find(flt, {'_id': 0}).sort([('changed_at', -1), ('memory_id', 1)]).skip(offset).limit(limit)]
        return {'items': rows, 'total': r.count_documents(flt), 'offset': offset, 'limit': limit}

    def sources(self, item_id, limit=20):
        """The current version's sources, resolved to short readable rows for the page (read only)."""
        head = self.head('memory_items', item_id)
        if not head:
            raise KeyError('没有这一条')
        out = []
        for ref in (head.get('derived_from') or [])[:limit]:
            out.append({'ref': ref, **_resolve_ref(self.db, ref)})
        return {'memory_id': item_id, 'version': head['version'], 'sources': out,
                'more': max(0, len(head.get('derived_from') or []) - limit)}

    def restore(self, c, item_id, expected_version, restore_version, now_ms):
        if c not in COLLECTIONS:
            raise ValueError('没有这个集合')
        key = COLLECTIONS[c]
        with _LOCK:
            head = self.head(c, item_id)
            if not head:
                raise KeyError('没有这一条')
            if type(restore_version) is not int:
                raise ValueError('恢复需要历史版本')
            saved = self.db[c].find_one({key: item_id, 'version': restore_version}, {'_id': 0})
            if not saved:
                raise KeyError('没有这个历史版本')
            if 'origin' in saved and saved['origin'] != 'owner':
                raise ValueError('只能恢复主人写过的版本')
            names = {'people_cards': ('kind', 'display', 'aliases', 'links', 'treatment', 'scope_ids', 'status'),
                     'people_notes': ('person_id', 'kind', 'text', 'status', 'observed_at'),
                     'memory_items': ('kind', 'title', 'statement', 'subjects', 'topics', 'when', 'status'),
                     'knowledge_items': ('kind', 'name', 'aliases', 'heard_as', 'statement', 'subjects', 'when',
                                         'scope_ids', 'status')}[c]
            fields = {k: copy.deepcopy(saved[k]) for k in names if k in saved}
            if 'links' in fields:
                fields['links'].pop('legacy_ids', None)
            return self.put(c, fields, item_id=item_id, expected_version=expected_version,
                            scope_id=head.get('scope_id', ''), now_ms=now_ms, reason='restore')

    # ---- read views (never write) ----

    def _heads(self, c, **match):
        """Latest version of every id in `c`, scalar filters only, at most 5000."""
        pipe = [{'$match': dict(match)}, {'$sort': {'version': -1}},
                {'$group': {'_id': '$' + COLLECTIONS[c], 'doc': {'$first': '$$ROOT'}}}, {'$replaceRoot': {'newRoot': '$doc'}},
                {'$limit': 5000}]
        rows = self.db[c].aggregate(pipe)
        return [{k: v for k, v in r.items() if k != '_id'} for r in rows]

    @staticmethod
    def _hit(row, q):
        if not q:
            return True
        vals = [row.get(k) for k in ('title', 'statement', 'name', 'text')]
        vals += list(row.get('aliases') or [])
        return any(isinstance(v, str) and q in v for v in vals)

    @staticmethod
    def _slice(rows, offset, limit):
        return {'items': rows[offset:offset + limit], 'total': len(rows), 'offset': offset, 'limit': limit}

    def overview(self, s):
        cols = {}
        for c in COLLECTIONS:
            if c == 'memory_items':
                r = self.db['memory_recall']
                count = lambda **f: r.count_documents({'lane': 'live', **f})
                by_status = {k: n for k in ('active', 'retired', 'disabled', 'merged') if (n := count(status=k))}
                by_origin = {k: n for k in ('owner', 'extracted') if (n := count(origin=k))}
                cols[c] = {'total': count(), 'by_status': by_status, 'by_origin': by_origin,
                           'recallable': count(recallable=True), 'replay': r.count_documents({'lane': 'replay'})}
                continue
            heads = self._heads(c)
            by_status, by_origin = {}, {}
            for r in heads:
                by_status[r.get('status')] = by_status.get(r.get('status'), 0) + 1
                if 'origin' in r:
                    by_origin[r['origin']] = by_origin.get(r['origin'], 0) + 1
            cols[c] = {'total': len(heads), 'by_status': by_status, 'by_origin': by_origin}
        mem = list(self.db['mind_memories'].aggregate([{'$match': {'lane': 'live', 'scope_id': speech_settings.scope(s)['id']}},
                                                       {'$group': {'_id': '$memory_id'}}, {'$count': 'total'}]))
        owner = s.get('owner') or {}
        return {'collections': cols, 'voice_labels': {'total': self.db['voice_labels'].count_documents({})},
                'existing': {'owner_voice_names': len(owner.get('voice_names') or []),
                             'owner_face_names': len(owner.get('face_names') or []),
                             'settings_people': len(s.get('people') or []),
                             'mind_memories': mem[0]['total'] if mem else 0}}

    def people(self):
        counts = {}
        for n in self._heads('people_notes'):
            if n.get('status') == 'active':
                counts[n['person_id']] = counts.get(n['person_id'], 0) + 1
        rows = [{**c, 'notes': counts.get(c['person_id'], 0)} for c in self._heads('people_cards')]
        rows.sort(key=lambda r: -r['changed_at'])
        rows.sort(key=lambda r: r['person_id'] != 'owner')
        return rows

    def person(self, person_id):
        card = self.head('people_cards', person_id)
        if not card:
            raise KeyError('没有这个人物')
        versions = [{k: v for k, v in d.items() if k != '_id'}
                    for d in self.db['people_cards'].find({'person_id': person_id}, {'_id': 0}).sort('version', -1)]
        by_time = lambda r: -r['changed_at']
        notes = sorted(self._heads('people_notes', person_id=person_id), key=by_time)
        mems = self._recall_rows({'lane': 'live', 'person_ids': person_id}, limit=200)
        know = sorted((k for k in self._heads('knowledge_items') if person_id in (k.get('subjects') or [])), key=by_time)
        return {'card': card, 'versions': versions, 'notes': notes, 'memories': mems, 'knowledge': know}

    def _recall_rows(self, flt, limit=None, offset=0):
        cur = self.db['memory_recall'].find(flt, {'_id': 0}).sort([('start_ms', -1), ('memory_id', 1)])
        if offset:
            cur = cur.skip(offset)
        if limit:
            cur = cur.limit(limit)
        return [{k: v for k, v in r.items() if k != '_id'} for r in cur]

    def memories(self, kind=None, person=None, topic=None, q=None, from_ms=None, to_ms=None, offset=0, limit=20, lane='live'):
        """Indexed read of memory heads from memory_recall (no whole-collection aggregate, no silent cut)."""
        _page(offset, limit)
        if lane not in LANES:
            raise ValueError('lane 不对')
        flt = {'lane': lane}
        if kind:
            flt['kind'] = kind
        if person:
            flt['person_ids'] = person
        if topic:
            flt['topics'] = topic
        rng = {**({'$gte': from_ms} if from_ms is not None else {}), **({'$lte': to_ms} if to_ms is not None else {})}
        if rng:
            flt['start_ms'] = rng
        if q:   # text search: every matching head is scanned (the filters above are indexed), then paged
            rows = [r for r in self._recall_rows(flt) if self._hit(r, q)]
            return self._slice(rows, offset, limit)
        return {'items': self._recall_rows(flt, limit=limit, offset=offset),
                'total': self.db['memory_recall'].count_documents(flt), 'offset': offset, 'limit': limit}

    def knowledge(self, kind=None, person=None, q=None, offset=0, limit=20):
        _page(offset, limit)
        rows = self._heads('knowledge_items', **({'kind': kind} if kind else {}))
        rows = [r for r in rows if (not person or person in (r.get('subjects') or [])) and self._hit(r, q)]
        rows.sort(key=lambda r: (-r['changed_at'], r['kid']))
        return self._slice(rows, offset, limit)

    def item(self, c, item_id):
        if c not in COLLECTIONS:
            raise ValueError('没有这个集合')
        key = COLLECTIONS[c]
        versions = [{k: v for k, v in d.items() if k != '_id'}
                    for d in self.db[c].find({key: item_id}, {'_id': 0}).sort('version', -1).limit(50)]
        if not versions:
            raise KeyError('没有这一条')
        original = None
        if c in ('people_notes', 'knowledge_items'):
            original = next((v for v in versions if v.get('origin') == 'owner'), None)
        children = []
        if c == 'knowledge_items':
            children = self._recall_rows({'lane': 'live', 'topics': item_id}, limit=200)
        return {'versions': versions, 'owner_original': original, 'children': children}


def _short(v, n):
    return v[:n] if isinstance(v, str) else None


def _resolve_ref(db, ref):
    """{kind, at?, text?, ...} for one derived_from ref (read only; missing -> {missing: True})."""
    c, key = ref['c'], ref['key']
    try:
        if c in ('conversations', 'conversation_versions'):
            d = db['conversations'].find_one({'cid': key['cid']}, {'_id': 0, 'title': 1, 'notes': 1, 'start_ms': 1,
                                                                   'end_ms': 1, 'last_ms': 1, 'version': 1})
            return {'kind': '对话纪要', 'at': d.get('start_ms'), 'end': d.get('end_ms') or d.get('last_ms'),
                    'title': _short(d.get('title'), 40), 'text': _short(d.get('notes'), 300), 'now_version': d.get('version')} \
                if d else {'kind': '对话纪要', 'missing': True}
        if c == 'speech':
            d = db['speech'].find_one({'session': key['session'], 'id': key['id']}, {'_id': 0})
            return {'kind': '原话', 'at': d.get('t', d.get('at')), 'text': _short(d.get('text'), 300), 'session': key['session'],
                    'pos': d.get('pos'), 'speaker': (d.get('speaker') or {}).get('name')
                    if (d.get('speaker') or {}).get('band') == 'known' else None} if d else {'kind': '原话', 'missing': True}
        if c == 'mind_segments':
            d = db['mind_segments'].find_one({'lane': key['lane'], 'start': key['start']}, {'_id': 0}, sort=[('version', -1)])
            sm = (d or {}).get('summary') or {}
            return {'kind': '情景段', 'at': d.get('start'), 'end': d.get('end'), 'title': _short(sm.get('title'), 40),
                    'text': _short(sm.get('text'), 300)} if d else {'kind': '情景段', 'missing': True}
        if c == 'mind_scenes':
            d = db['mind_scenes'].find_one({'lane': key['lane'], 'minute': key['minute']}, {'_id': 0}, sort=[('version', -1)])
            return {'kind': '情景', 'at': key['minute'], 'text': _short(((d or {}).get('scene') or {}).get('summary'), 300)} \
                if d else {'kind': '情景', 'missing': True}
        if c == 'chat_turns':
            d = db['chat_turns'].find_one({'tid': key['tid']}, {'_id': 0})
            return {'kind': '对话模式', 'at': d.get('at'), 'text': _short(d.get('text'), 300), 'reply': _short(d.get('reply'), 300)} \
                if d else {'kind': '对话模式', 'missing': True}
        if c == 'eye_minutes':
            d = db['eye_minutes'].find_one({'minute': key['minute']}, {'_id': 0}, sort=[('version', -1)])
            return {'kind': '看图', 'at': key['minute'], 'text': _short(((d or {}).get('result') or {}).get('activity'), 300)} \
                if d else {'kind': '看图', 'missing': True}
        if c == 'owner_signals':
            d = db['owner_signals'].find_one({'sid': key['sid']}, {'_id': 0})
            v = (d or {}).get('value') or {}
            return {'kind': '主人打分', 'at': d.get('at'), 'text': ('好评' if v.get('polarity') == 1 else '差评' if v.get('polarity') == -1
                                                                else d.get('kind'))} if d else {'kind': '主人打分', 'missing': True}
        if c == 'recording':
            return {'kind': '录音片段', 'session': key['session'], 'pos': ref.get('pos')}
    except Exception as e:                 # noqa: BLE001 - one unreadable source never hides the rest
        return {'kind': c, 'error': type(e).__name__}
    return {'kind': c}


def voice_label(body, now_ms):
    """Build and validate one voice label document; the caller writes it (phase 4)."""
    _only(body, '声纹标注', ('target', 'person_id', 'verdict', 'via', 'group', 'review_day', 'page_version'))
    t = body.get('target')
    _only(t, 'target', ('session', 'speech_id', 'pos', 'tingwu_n'))
    pos = t.get('pos')
    if not isinstance(pos, list) or len(pos) != 2 or any(type(x) is not int or x < 0 for x in pos) or pos[1] <= pos[0]:
        raise ValueError('位置需要两个递增的非负整数')
    tn = t.get('tingwu_n')
    verdict = _enum(body.get('verdict'), '判断', ('is', 'not', 'several', 'hello_self', 'noise', 'unsure'))
    person = body.get('person_id')
    if verdict == 'is' and not person:
        raise ValueError('判断是某人时需要人物')
    if person is not None and (not isinstance(person, str) or not re.match(ID_RE, person)):
        raise ValueError('人物编号不对')
    group = body.get('group')
    if group is not None:
        _only(group, 'group', ('kind', 'key'))
        _enum(group.get('kind'), '分组类别', ('tingwu_speaker', 'run'))
        _text(group.get('key'), '分组键', 1, 80)
    day = body.get('review_day')
    try:
        if not isinstance(day, str) or not re.match(r'^\d{4}-\d{2}-\d{2}$', day):
            raise ValueError
        datetime.date.fromisoformat(day)
    except ValueError:
        raise ValueError('复核日期形如 2026-11-12') from None
    return {'lid': f'l_{uuid.uuid4().hex[:12]}', 'at': now_ms, 'actor': 'owner',
            'target': {'session': _text(t.get('session'), '会话', 1, 80), 'speech_id': _text(t.get('speech_id'), '语音', 1, 80),
                       'pos': pos, 'tingwu_n': None if tn is None else _int(tn, '听悟序号')},
            'person_id': person, 'verdict': verdict, 'via': _enum(body.get('via'), '方式', ('single', 'group')),
            'group': copy.deepcopy(group), 'review_day': day, 'page_version': _int(body.get('page_version'), '页面版本')}
