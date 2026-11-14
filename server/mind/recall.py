"""One recall pool for chat, the talk show and realtime (long-term memory phase A, assessment §3.5).

The pool merges, at read time, the two memory engines (they stay separate on disk):
  habit    mind_memories (MemoryStore.recall: known habits / routines of this scene), up to `habit_limit`, first;
  library  memory_recall (library.py: the current version of every memory_items entry), live lane, active and not low
           confidence, best salience then newest first, up to POOL_MAX. Library memories are global (not per scene).
Each pool item: {id, source, text, title, kind, person_ids, sensitivity, salience, at, tease, quote, words}.
settings memory.apply false -> an empty pool (nothing paid either way: reading never calls a model).

Picking (no embeddings in phase A; relevance = two-character word overlap, the same rule chat used before):
  chat      related(): at most 3 per sentence that share a word with it; a memory about / said by the speaker ranks
            higher among those that share a word; then salience; then pool order.
  talk show pick_for_talk(): at most 1 library memory per round that shares a word with the recent speech, never a
            child / private memory, never one whose person card says tease never or quote no, and not one offered to
            the talk show in the last 3 days (memory_uses, purpose talk; written when a round is written).
  realtime  plain(crowd=True): habit memories and plain library memories only (no child / private / others_said).
Prompt annotations (prompt_text): child -> do not joke, do not repeat verbatim; others_said / quote gist -> gist only.
"""
import re

from . import speech_settings
from .memory import MemoryStore

POOL_MAX = 300
HABIT_LIMIT = 8
TALK_COOLDOWN_MS = 3 * 86_400_000
FIELDS = {'_id': 0, 'memory_id': 1, 'title': 1, 'statement': 1, 'kind': 1, 'person_ids': 1, 'people': 1,
          'sensitivity': 1, 'salience': 1, 'start_ms': 1, 'confidence': 1}
# Two-character words too common to make a memory "related" (chat review M4).
STOP_BIGRAMS = set('什么 一个 我们 你们 他们 今天 明天 昨天 这个 那个 怎么 没有 就是 不是 可以 现在 还是 知道 觉得 时候 '
                   '一下 一点 自己 然后 因为 所以 但是 如果 已经 还有 这样 那样 哪里 为什 真的 好的 是不 不要 要不'.split())
TEASE_ORDER = {'ok': 0, 'words_only': 1, 'never': 2}
QUOTE_ORDER = {'ok': 0, 'gist': 1, 'no': 2}


def _norm(text):
    return re.sub(r'[\W_]', '', str(text or '').lower())


def bigrams(text):
    n = _norm(text)
    return {n[i:i + 2] for i in range(len(n) - 1)} | ({n} if len(n) == 1 else set())


def _treatments(db):
    """person_id -> (tease, quote) from the library cards (small collection)."""
    from .library import Library
    out = {}
    for c in Library(db)._heads('people_cards'):
        t = c.get('treatment') or {}
        out[c['person_id']] = (t.get('tease') or 'words_only', t.get('quote') or 'gist')
    return out


def _strictest(row, cards):
    tease, quote = 'ok', 'ok'
    for p in row.get('people') or []:
        if p.get('id') in ('owner', 'hello', 'room'):
            continue
        if p.get('role') not in ('speaker', 'about'):
            continue
        t, q = cards.get(p.get('id'), ('words_only', 'gist'))     # no card: the card defaults
        tease = max(tease, t, key=TEASE_ORDER.get)
        quote = max(quote, q, key=QUOTE_ORDER.get)
    if 'others_said' in (row.get('sensitivity') or []):
        quote = max(quote, 'gist', key=QUOTE_ORDER.get)
    return tease, quote


def library_rows(db, lane='live', limit=POOL_MAX):
    cards = _treatments(db)
    rows = db['memory_recall'].find({'lane': lane, 'recallable': True}, FIELDS) \
        .sort([('salience', -1), ('start_ms', -1)]).limit(limit)
    out = []
    for r in rows:
        if not r.get('statement'):
            continue
        tease, quote = _strictest(r, cards)
        out.append({'id': r['memory_id'], 'source': 'library', 'text': r['statement'], 'title': r.get('title') or '',
                    'kind': r.get('kind'), 'person_ids': list(r.get('person_ids') or []),
                    'sensitivity': list(r.get('sensitivity') or []), 'salience': r.get('salience', 1),
                    'at': r.get('start_ms'), 'tease': tease, 'quote': quote,
                    'words': frozenset(bigrams((r.get('title') or '') + (r['statement'])) - STOP_BIGRAMS)})
    return out


def load_pool(db, s, now_ms, habit_limit=HABIT_LIMIT, limit=POOL_MAX, lane='live'):
    """habit memories (this scene) then library memories; [] when memory.apply is off. Raises on database trouble."""
    if not (s.get('memory') or {}).get('apply', True):
        return []
    scope = speech_settings.scope(s)['id']
    habit = []
    for m in MemoryStore(db).recall(scope, now_ms, limit=habit_limit):
        if m.get('statement'):
            habit.append({'id': m['memory_id'], 'source': 'habit', 'text': m['statement'], 'title': '', 'kind': m.get('kind'),
                          'person_ids': [m['subject_id']] if m.get('subject_id') else [], 'sensitivity': [],
                          'salience': 1, 'at': m.get('last_seen'), 'tease': 'ok', 'quote': 'ok',
                          'words': frozenset(bigrams(m['statement']) - STOP_BIGRAMS)})
    return habit + library_rows(db, lane, limit)


def _text(m):
    return m if isinstance(m, str) else m.get('text') or ''


def related(pool, text, limit=3, person_id=None):
    """At most `limit` pool items (or plain strings) sharing a two-character word with `text`, best first."""
    words = bigrams(text) - STOP_BIGRAMS
    scored = []
    for i, m in enumerate(pool):
        w = m.get('words') if isinstance(m, dict) and m.get('words') is not None else bigrams(_text(m))
        n = len(words & w)
        if n <= 0:
            continue
        bonus = 1 if person_id and isinstance(m, dict) and person_id in (m.get('person_ids') or []) else 0
        sal = m.get('salience', 1) if isinstance(m, dict) else 1
        scored.append((n + bonus, sal, -i, m))
    scored.sort(key=lambda x: x[:3], reverse=True)
    return [m for *_, m in scored][:limit]


def prompt_text(m):
    """One memory as it goes into a prompt, with how it may be used."""
    if isinstance(m, str):
        return m
    notes = []
    sens = m.get('sensitivity') or []
    if 'child' in sens:
        notes.append('孩子的事：别开玩笑，别照原话说')
    if 'others_said' in sens:
        notes.append('别人说的：只提意思')
    elif m.get('quote') == 'no':
        notes.append('只提意思')
    if m.get('tease') == 'never':
        notes.append('别调侃这个人')
    return m['text'] + (f'（{"；".join(notes)}）' if notes else '')


def plain(pool, crowd=False):
    """Prompt texts for a context that cannot pick per sentence (realtime); crowd: visitors may hear it."""
    out = []
    for m in pool:
        if isinstance(m, dict) and crowd and set(m.get('sensitivity') or []) & {'child', 'private', 'others_said'}:
            continue
        out.append(prompt_text(m))
    return out


def talk_ok(m):
    sens = set(m.get('sensitivity') or [])
    return not sens & {'child', 'private'} and m.get('tease') != 'never' and m.get('quote') != 'no'


def pick_for_talk(db, pool, texts, now_ms, limit=1):
    """<= limit library memories for one talk-show round, as ctx['memories'] rows."""
    cands = [m for m in pool if isinstance(m, dict) and m.get('source') == 'library' and talk_ok(m)]
    if not cands:
        return []
    used = {u.get('memory_id') for u in db['memory_uses'].find({'purpose': 'talk', 'at': {'$gte': now_ms - TALK_COOLDOWN_MS}},
                                                                {'_id': 0, 'memory_id': 1})}
    best = related([m for m in cands if m['id'] not in used], ' '.join(t for t in texts if t), limit=limit)
    return [{'memory_id': m['id'], 'statement': m['text'], 'kind': 'library', 'source': 'library',
             'sensitivity': m.get('sensitivity') or []} for m in best]


def note_uses(db, memories, purpose, at, ref=None):
    """Record that these library memories were offered (talk show: when a round is written)."""
    for m in memories or []:
        if isinstance(m, dict) and m.get('source') == 'library' and m.get('memory_id'):
            db['memory_uses'].insert_one({'memory_id': m['memory_id'], 'purpose': purpose, 'at': at, 'ref': ref})
