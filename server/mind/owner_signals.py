"""Owner signals (spec section 10-11): ratings and "this round is no good", kept forever, append-only.

The page is the debugging judgement, a remote (any other device) the everyday one; every number is
shown in two columns and the everyday column is the measure of "getting better" (user, 2026-11-13).
Learning counts each moment once: per line, the last rating wins.
"""
import uuid

KINDS = ('rating', 'round_none')


def put_signal(db, body, now_ms):
    if not isinstance(body, dict) or body.get('kind') not in KINDS:
        raise ValueError('kind must be rating or round_none')
    refers = body.get('refers_to')
    doc = {'sid': 'g' + uuid.uuid4().hex, 'at': now_ms, 'who': 'owner', 'kind': body['kind'], 'state': 'applied',
           'source': {'device': ((body.get('source') or {}).get('device') or 'page'),
                      'seq': (body.get('source') or {}).get('seq')}}
    if body['kind'] == 'rating':
        v = body.get('value') or {}
        if v.get('polarity') not in (1, -1) or not isinstance(v.get('count'), int) or not 1 <= v['count'] <= 5:
            raise ValueError('rating needs polarity ±1 and count 1..5')
        if not (isinstance(refers, dict) and ((isinstance(refers.get('rid'), str) and isinstance(refers.get('line'), str))
                                              or isinstance(refers.get('uid'), str))):
            raise ValueError('rating refers to {rid, line} or {uid}')
        doc['value'] = {'polarity': v['polarity'], 'count': v['count']}
    elif not (isinstance(refers, dict) and isinstance(refers.get('rid'), str)):
        raise ValueError('round_none refers to {rid}')
    doc['refers_to'] = {k: refers[k] for k in ('rid', 'line', 'uid') if k in refers}
    db['owner_signals'].insert_one(dict(doc))
    return doc


def _column(device):
    return 'page' if device == 'page' else 'daily'


def _signals(db, from_ms):
    for sig in db['owner_signals'].find({'at': {'$gte': from_ms}}, {'_id': 0}).sort('at', 1):
        ref, line = sig.get('refers_to') or {}, None
        rid = ref.get('rid')
        if ref.get('uid'):
            u = db['utterances'].find_one({'uid': ref['uid']}, {'_id': 0, 'rid': 1, 'line': 1}) or {}
            rid, line = u.get('rid'), u.get('line')
        yield sig, rid, line or ref.get('line')


def fold_moments(db, scope_id, from_ms, to_ms):
    """One entry per moment. Ratings are keyed by (rid, line, column) — a line of round 2 is not the same
    sentence as round 1's line with the same direction (final review I5); the last rating wins."""
    rounds = list(db['say_rounds'].find({'scope_id': scope_id, 'at': {'$gte': from_ms, '$lt': to_ms}}, {'_id': 0}).sort('at', 1))
    by_rid = {r['rid']: r for r in rounds}
    moments = {}
    for r in rounds:
        m = moments.setdefault(r['moment_id'], {'moment_id': r['moment_id'], 'first_rid': r['rid'],
                                                'judge_first': r['judge'].get('best_key'), 'ratings': {},
                                                'none': {}, 'plays': []})
        m['plays'] += [{**p, 'rid': r['rid']} for p in r.get('plays', [])]
    if not by_rid:
        return []
    for sig, rid, line in _signals(db, from_ms):
        if rid not in by_rid:
            continue
        m = moments[by_rid[rid]['moment_id']]
        col = _column(sig['source']['device'])
        if sig['kind'] == 'round_none':
            m['none'][col] = True
        else:
            m['ratings'][(rid, line, col)] = sig['value']['polarity'] * sig['value']['count']
    return list(moments.values())


def round_marks(db, rid):
    """The page's marks for one round: {ratings: {line: ±strength}, none: bool}, last mark per line."""
    out = {'ratings': {}, 'none': False}
    for sig, srid, line in _signals(db, 0):
        if srid != rid:
            continue
        if sig['kind'] == 'round_none':
            out['none'] = True
        else:
            out['ratings'][line] = sig['value']['polarity'] * sig['value']['count']
    return out


def stats(db, scope_id, from_ms, to_ms):
    moments = fold_moments(db, scope_id, from_ms, to_ms)
    result = {}
    for col in ('page', 'daily'):
        good = bad = rated = hits = judged = none = marked = 0
        for m in moments:
            mine = {(rid, line): v for (rid, line, c), v in m['ratings'].items() if c == col}
            if mine or m['none'].get(col):
                marked += 1
            none += bool(m['none'].get(col))
            if not mine:
                continue
            rated += len(mine)
            good += sum(v for v in mine.values() if v > 0)
            bad += sum(-v for v in mine.values() if v < 0)
            judged += 1
            hits += mine.get((m['first_rid'], m['judge_first']), 0) > 0
        result[col] = {'good_rate': good / (good + bad) if good + bad else None,
                       'judge_hit_rate': hits / judged if judged else None,
                       'none_rate': none / marked if marked else None,
                       'rated_lines': rated, 'moments': marked}
    played = [(p['rid'], p['key']) for m in moments for p in m['plays']]
    rated_any = {(rid, line) for m in moments for (rid, line, _c) in m['ratings']}
    with_play = [m for m in moments if m['plays']]
    result['unrated_played_lines'] = len({p for p in played if p not in rated_any})
    result['huyu_rate'] = (sum(any(p.get('why') == 'huyu' for p in m['plays']) for m in with_play) / len(with_play)
                           if with_play else None)
    return result
