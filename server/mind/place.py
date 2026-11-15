"""Where hello is now (owner 2026-11-15: "地点要搞对，删除所有可能钉死的地方，hello可以主动询问或我们自然语言输入。
在左滑的信息页中显示并可以设置（选择常用的或未知）。"). Spec: docs/superpowers/specs/2026-11-15-place-contract.md.

One source of truth, built on the owner's place report that already existed (POST /place, 2026-11-14 "地点以你点的为准"):
- The current place is the latest row {kind: "place"} in mind_labels (Store.place_at). label is free text (1-40
  characters) or None = unknown. Nothing reported yet = unknown. There is no default any more: settings place.default
  (it said 办公室) is no longer read anywhere, and the scene profile (office / home / friends) no longer implies a place.
- Who set it: by = page (the monitor page; rows without `by` are page reports), robot (picked on hello's left-swipe info
  page, via the ear's /mouth link), voice (someone told hello in chat or crowd: parse_told, a local rule, no model).
- The common choices hello's info page offers live in db.place {_id: "choices"} (DEFAULT_CHOICES until the owner edits
  them): at most MAX_CHOICES, each at most CHOICE_MAX_BYTES of UTF-8.
- Asking (owner: hello may ask): ask_line() gives the chat / crowd prompt one line that allows a natural question when
  the place is unknown, or the latest eye minute says the picture looks like another kind of place; at most once per
  ASK_GAP_MS (db.place {_id: "ask"}), so not every turn.
Prompts read the place through line(); with unknown they say so and never assume an office or a desk. The eye prompt
gets no place at all (it describes the picture; its place_seen field is what the picture looks like).
Nothing here calls a model or costs money.
"""
import re
import time

from . import settings as settings_mod

MAX_LABEL = 40                       # characters, as the 2026-11-14 /place
MAX_CHOICES = 8
CHOICE_MAX_BYTES = 24                # UTF-8 bytes (8 CJK characters)
DEFAULT_CHOICES = ['办公室', '家里', '车上', '饭店', '户外']
BY = ('page', 'robot', 'voice')
BY_TEXT = {'page': '主人在网页上设的', 'robot': '主人在 hello 身上选的', 'voice': '有人跟 hello 说的'}
ASK_GAP_MS = 10 * 60_000
EYE_FRESH_MS = 3 * 60_000
UNKNOWN_TEXT = '现在的地点：不知道（hello 常被主人带着走，可能在任何地方；不要假定在办公室或桌上）'
# The owner's 环境说明 describes the usual life (the office, the people, the words); it is background, not the place.
ENV_LABEL = '背景资料（主人平时的情况和常用词，只作背景，不代表 hello 现在在那里）'
SELF_FACTS_FIX = (('是主人放在办公桌上的', '是主人的'), ('主人放在办公桌上的', '主人的'), ('放在办公桌上的', ''),
                  ('住在主人办公桌上的', ''))


def _ctrl(text):
    return any(ord(c) < 32 or ord(c) == 127 for c in text)


def clean_label(label):
    """A place label -> stripped text, or None for unknown; ValueError for a bad one."""
    if label is None:
        return None
    if not isinstance(label, str) or _ctrl(label) or not 1 <= len(label.strip()) <= MAX_LABEL:
        raise ValueError(f'label: 1–{MAX_LABEL} characters, or null for unknown')
    return label.strip()


def clean_choice(text):
    if not isinstance(text, str) or _ctrl(text) or not text.strip() or len(text.strip().encode()) > CHOICE_MAX_BYTES:
        raise ValueError(f'choice: 1–{CHOICE_MAX_BYTES} bytes of UTF-8 text')
    return text.strip()


def self_facts(s):
    """settings self_facts without the old 'on the owner's office desk' wording (the stored value may still have it)."""
    text = (s.get('self_facts') or '').strip()
    for old, new in SELF_FACTS_FIX:
        text = text.replace(old, new)
    return text


# ---- the current place ----
def current(db, at_ms=None):
    """{label (None = unknown), by, at, heard?} in force at at_ms (default now)."""
    at_ms = int(time.time() * 1000) if at_ms is None else at_ms
    for d in db['mind_labels'].find({'kind': 'place', 'at': {'$lte': at_ms}}, {'_id': 0}).sort('at', -1).limit(1):
        return {'label': d.get('label'), 'by': d.get('by') or 'page', 'at': d.get('at')}
    return {'label': None, 'by': None, 'at': None}


def choices(db):
    doc = db['place'].find_one({'_id': 'choices'}) or {}
    items = doc.get('items')
    return list(items) if isinstance(items, list) else list(DEFAULT_CHOICES)


def state(db, now_ms=None):
    """GET /place: the current place, the common choices and rev (changes whenever either changes)."""
    now = int(time.time() * 1000) if now_ms is None else now_ms
    cur = current(db, now)
    doc = db['place'].find_one({'_id': 'choices'}) or {}
    return {'current': cur['label'], 'by': cur['by'], 'at': cur['at'], 'choices': choices(db),
            'rev': max(cur['at'] or 0, doc.get('at') or 0)}


def set_current(db, label, by, now_ms, heard=None, lane='live'):
    """Write a new current place (label None = unknown). A robot pick must be one of the choices."""
    label = clean_label(label)
    if by not in BY:
        raise ValueError(f'source: one of {"/".join(BY)}')
    if by == 'robot' and label is not None and label not in choices(db):
        raise ValueError('choice: not one of the common places')
    # Two sets in the same millisecond must still read back in order (current() takes the latest at): keep at strictly
    # increasing (found as a flaky test, 2026-11-15).
    for d in db['mind_labels'].find({'kind': 'place', 'at': {'$gte': now_ms}}, {'_id': 0, 'at': 1}).sort('at', -1).limit(1):
        now_ms = d['at'] + 1
    row = {'lane': lane, 'kind': 'place', 'label': label, 'at': now_ms}
    if by != 'page':                         # page rows stay as before (no by), so old strong elements do not change
        row['by'] = by
    if heard:
        row['heard'] = str(heard)[:80]       # the sentence that set it (DB only, never logged)
    db['mind_labels'].insert_one(row)
    return state(db, now_ms)


def set_choices(db, items, now_ms):
    if not isinstance(items, list) or len(items) > MAX_CHOICES:
        raise ValueError(f'choices: a list of at most {MAX_CHOICES}')
    out = []
    for x in items:
        x = clean_choice(x)
        if x not in out:
            out.append(x)
    db['place'].update_one({'_id': 'choices'}, {'$set': {'items': out, 'at': now_ms}}, upsert=True)
    return state(db, now_ms)


def edit_choices(db, body, now_ms):
    """POST /place/choices: {choices: [...]} replaces the list; {add} / {remove} edit it."""
    if set(body) - {'choices', 'add', 'remove'} or not body:
        raise ValueError('choices, add or remove required')
    if 'choices' in body:
        return set_choices(db, body['choices'], now_ms)
    items = choices(db)
    if 'remove' in body:
        items = [x for x in items if x != clean_choice(body['remove'])]
    if 'add' in body:
        add = clean_choice(body['add'])
        if add not in items:
            if len(items) >= MAX_CHOICES:
                raise ValueError(f'choices: at most {MAX_CHOICES}; remove one first')
            items.append(add)
    return set_choices(db, items, now_ms)


# ---- natural language: "我们现在在饭店" ----
_NOT_PLACE = ('这里', '那里', '哪里', '这儿', '那儿', '哪儿', '这边', '那边', '哪边', '心里', '手里', '嘴里', '梦里',
              '网上', '线上', '身上', '脸上', '头上', '马上', '早上', '晚上', '世上', '床上')
_PLACE_WORDS = ('公司', '学校', '医院', '商场', '超市', '公园', '外面', '外头', '户外', '室外', '地铁', '飞机', '机场',
                '高铁', '火车', '酒店', '宾馆', '会议室', '办公室', '工位', '老家', '食堂', '车站')
_PLACE_ENDS = '店馆厅室家里上边旁园场站路街楼院湖河海山房车区村镇城门口岸桥'
_TIME_WORDS = ('昨天', '前天', '明天', '后天', '上次', '下次', '那天', '去年', '以前', '待会', '等会', '一会儿', '上周', '下周',
               '刚才', '本来', '要是', '如果', '想去', '打算')
_IN_CAR = ('开车', '坐车', '打车', '车里')
_VERBS = '吃喝看玩做写想说聊忙搞弄听学等逛找修拍买洗睡开坐打散走跑'      # "我在看海" is an activity, not a place
_TOLD = re.compile(r'(?:(?P<sub>我们|咱们|咱|我|你|hello|哈喽)[，, ]*)?(?P<now>现在|这会儿|已经|正|今天|这下)?'
                   r'(?:在|到了|到)(?P<p>[一-鿿A-Za-z0-9]{1,10}?)(?:了|呢|啦|呀|哦|啊|吧|哈|$)')
_HERE = re.compile(r'(?:这里|这儿|这边)(?:是|叫)(?P<p>[一-鿿A-Za-z0-9]{1,10}?)(?:了|呢|啦|呀|哦|啊|吧|哈|$)')


def _placeish(p):
    return p not in _NOT_PLACE and (p.endswith(_PLACE_WORDS) or p[-1] in _PLACE_ENDS)


def parse_told(text, choice_list=()):
    """A sentence that says where we are now -> the place label, else None. Local rule (no model): a subject (我们 /
    咱们 / 我 / 你 / hello) or 现在 before 在 / 到, or 这里是; the place must look like one (a place word or ending);
    questions, past / future and 'if' sentences are ignored. A label close to a common choice becomes that choice."""
    if not isinstance(text, str):
        return None
    t = re.sub(r'\s+', '', text.lower())
    if not t or len(t) > 60 or any(q in t for q in ('吗', '?', '？', '哪', '么')) or any(w in t for w in _TIME_WORDS):
        return None
    for part in re.split(r'[。！!，,；;、]', t):
        p = None
        m = _HERE.search(part)
        if m:
            p = m.group('p')
        else:
            for m in _TOLD.finditer(part):
                if m.group('sub') or m.group('now'):
                    p = m.group('p')
                    break
        if not p:
            continue
        p = re.sub(r'^(这个|一个|个|这家|一家)', '', p)
        if p in _IN_CAR:
            p = '车上'
        if p and p[0] not in _VERBS:     # "在饭店吃饭" / "在机场等飞机": the place, then what we do there
            p = next((p[:k] for k in range(1, len(p)) if p[k] in _VERBS and _placeish(p[:k])), p)
        if not p or p[0] in _VERBS or not _placeish(p):
            continue
        for c in choice_list:
            if c == p or (len(c) >= 2 and c in p) or (len(p) >= 1 and p in c and len(c) - len(p) <= 1):
                return c
        return p[:MAX_LABEL]
    return None


def told(db, text, source, now_ms):
    """A sentence heard in chat / crowd: when it says where we are and that differs from the current place, it becomes
    the current place (by voice). -> {changed, label, state}."""
    label = parse_told(text, choices(db))
    if label is None or label == current(db, now_ms)['label']:
        return {'changed': False, 'label': label, 'state': state(db, now_ms)}
    return {'changed': True, 'label': label, 'state': set_current(db, label, 'voice', now_ms, heard=text)}


# ---- prompts ----
_KINDS = {'car': ('车',), 'outdoor': ('户外', '室外', '外面', '街', '路边', '湖', '河边', '海边', '海滩', '公园', '山上', '广场', '草地', '树'),
          'food': ('饭店', '餐厅', '饭馆', '餐馆', '食堂', '咖啡', '吃饭', '火锅', '面馆'),
          'home': ('家', '客厅', '卧室', '厨房', '阳台'), 'office': ('办公', '工位', '公司', '会议室')}


def kinds(text):
    t = str(text or '')
    return {k for k, words in _KINDS.items() if any(w in t for w in words)}


def looks_changed(label, seen):
    """The eye's place_seen and the current place name different kinds of place (both must be recognisable)."""
    a, b = kinds(label), kinds(seen)
    return bool(a and b and not a & b)


def line(cur, tz):
    """The one place line every prompt that needs a place uses."""
    if not cur or not cur.get('label'):
        if cur and cur.get('at'):
            return UNKNOWN_TEXT.replace('不知道（', f'不知道（{_hhmm(cur["at"], tz)} 设成了未知；', 1)
        return UNKNOWN_TEXT
    by = BY_TEXT.get(cur.get('by') or 'page', BY_TEXT['page'])
    return f'现在的地点：{cur["label"]}（{by}，{_hhmm(cur["at"], tz)} 起，以此为准）'


def _hhmm(ms, tz):
    return settings_mod.local_time(ms, tz).strftime('%H:%M')


def latest_seen(db, now_ms, fresh_ms=EYE_FRESH_MS):
    """What the latest fresh eye minute says the picture looks like ('' when none or 看不出)."""
    doc = db['eye_minutes'].find_one({'source': 'model'}, {'_id': 0}, sort=[('minute', -1), ('version', -1)])
    if not doc or now_ms - (doc.get('minute') or 0) > fresh_ms + 60_000:
        return ''
    seen = str((doc.get('result') or {}).get('place_seen') or '').strip()
    return '' if not seen or '看不出' in seen else seen[:20]


def ask_line(db, cur, seen, now_ms, consume=True, gap_ms=ASK_GAP_MS):
    """One line that lets hello ask where it is, or None: only when the place is unknown or the picture looks like another
    kind of place, and at most once per gap_ms (consume=True records that it was offered)."""
    unknown = not cur.get('label')
    if not unknown and not looks_changed(cur['label'], seen):
        return None
    last = (db['place'].find_one({'_id': 'ask'}) or {}).get('at') or 0
    if 0 <= now_ms - last < gap_ms:
        return None
    if consume:
        db['place'].update_one({'_id': 'ask'}, {'$set': {'at': now_ms}}, upsert=True)
    if unknown:
        hint = f'（画面看着像{seen}）' if seen else ''
        return f'可以顺口自然地问一句现在在哪儿{hint}，只问这一次；有人回答了就记住，不要再问。'
    return (f'画面看着像{seen}，跟现在的地点（{cur["label"]}）不太一样：可以顺口问一句是不是换地方了，只问这一次；'
            '没人接话就算了。')


def prompt_lines(db, s, now_ms, ask=False):
    """[place line] (+ the ask line when ask and allowed) for the chat / crowd context."""
    cur = current(db, now_ms)
    out = [line(cur, s['tz'])]
    if ask:
        a = ask_line(db, cur, latest_seen(db, now_ms), now_ms)
        if a:
            out.append('【可以问地点】' + a)
    return out


def day_lines(db, lo_ms, hi_ms, tz):
    """'HH:MM label' for each place change in [lo, hi) and the one in force at lo (memory extraction)."""
    start = current(db, lo_ms)
    rows = [{'label': start['label'], 'at': lo_ms}] if start['at'] else []
    rows += [d for d in db['mind_labels'].find({'kind': 'place', 'at': {'$gt': lo_ms, '$lt': hi_ms}}, {'_id': 0}).sort('at', 1)]
    return [f'{_hhmm(r["at"], tz)} {r.get("label") or "不知道"}' for r in rows]
