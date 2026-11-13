"""The library: people cards, people notes, memories and knowledge, plus voice labels (backend phase 1).

Every change is a new version document (append-only; old versions are never touched, nothing is deleted).
Only the owner's page writes here; model-written rows come in later phases. Entry text is never logged."""
import copy
import datetime
import re
import threading
import uuid

COLLECTIONS = {'people_cards': 'person_id', 'people_notes': 'note_id', 'memory_items': 'memory_id', 'knowledge_items': 'kid'}
PREFIX = {'people_cards': 'p', 'people_notes': 'n', 'memory_items': 'e', 'knowledge_items': 'k'}
ID_RE = r'^(owner|[pnek]_[a-f0-9]{12})$'
_LOCK = threading.RLock()
CONFLICT = '已被改过，请刷新后重试'


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
        for k in ('subjects', 'topics'):
            d['memory_items'].create_index([(k, 1)])
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
                'status': _enum(f.get('status', 'active'), '状态', ('active', 'retired', 'disabled'))}

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
            return {key: item_id, 'scope_id': scope_id, 'source': {'kind': 'owner', 'refs': []}}
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
            return rec

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
            heads = self._heads(c)
            by_status, by_origin = {}, {}
            for r in heads:
                by_status[r.get('status')] = by_status.get(r.get('status'), 0) + 1
                if 'origin' in r:
                    by_origin[r['origin']] = by_origin.get(r['origin'], 0) + 1
            cols[c] = {'total': len(heads), 'by_status': by_status, 'by_origin': by_origin}
        mem = list(self.db['mind_memories'].aggregate([{'$group': {'_id': '$memory_id', 'doc': {'$first': '$$ROOT'}}},
                                                       {'$count': 'total'}]))
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
        mems = sorted((m for m in self._heads('memory_items') if person_id in (m.get('subjects') or [])),
                      key=lambda m: -m['when']['start_ms'])
        know = sorted((k for k in self._heads('knowledge_items') if person_id in (k.get('subjects') or [])), key=by_time)
        return {'card': card, 'versions': versions, 'notes': notes, 'memories': mems, 'knowledge': know}

    def memories(self, kind=None, person=None, topic=None, q=None, from_ms=None, to_ms=None, offset=0, limit=20):
        _page(offset, limit)
        rows = self._heads('memory_items', **({'kind': kind} if kind else {}))
        rows = [r for r in rows
                if (not person or person in (r.get('subjects') or [])) and (not topic or topic in (r.get('topics') or []))
                and self._hit(r, q) and (from_ms is None or r['when']['start_ms'] >= from_ms)
                and (to_ms is None or r['when']['start_ms'] <= to_ms)]
        rows.sort(key=lambda r: (-r['when']['start_ms'], r['memory_id']))
        return self._slice(rows, offset, limit)

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
            children = sorted((m for m in self._heads('memory_items') if item_id in (m.get('topics') or [])),
                              key=lambda m: -m['when']['start_ms'])
        return {'versions': versions, 'owner_original': original, 'children': children}


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
