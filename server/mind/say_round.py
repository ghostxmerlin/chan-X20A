"""One talk-show round (spec sections 6-9, 11): five lines written in parallel, one Shanghainese
fallback line, one judge call; at speaking time at most one check call against newer speech.
Rounds are stored in hello.say_rounds. Voice and dialect come from role_config, never from the model.
"""
import difflib
import random
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from . import say_prompts as P, speech_settings
from .model_calls import run_json
from .say_moment import signature
from .speech_context import norm

MODEL = 'gpt-6.1-terra'
KEYS = tuple(P.DIRECTIONS)                       # comfort deadpan roast sad worst
HUYU = 'huyu'
PROMPTS = {'line': 'say-line-v1', 'huyu': 'say-huyu-v1', 'judge': 'say-judge-v1', 'check': 'say-check-v2'}
CALL_TIMEOUT_S = 12
HISTORY_ROUNDS = 6
BAD_CHARS = re.compile(r'[\r\n\[\]【】（）()“”「」<>]')


def _call(env, store, s, step, prompt, schema, messages, now_ms, chat, temperature, max_tokens):
    return run_json(env, store, s, step, prompt, schema, messages, now_ms, chat=chat, max_tokens=max_tokens,
                    reasoning_effort='none', timeout=CALL_TIMEOUT_S, model=MODEL, temperature=temperature)


def history(db, scope_id, now_ms, rounds=HISTORY_ROUNDS):
    """{key: [{text, device, point}, ...]} oldest first, from the latest rounds of this scope (played or not)."""
    out = {k: [] for k in (*KEYS, HUYU)}
    rows = list(db['say_rounds'].find({'scope_id': scope_id, 'at': {'$lt': now_ms}}, {'_id': 0, 'lines': 1})
                .sort('at', -1).limit(rounds))
    for row in reversed(rows):
        for line in row.get('lines', []):
            if line.get('text') and line.get('key') in out:
                out[line['key']].append({k: line.get(k) for k in ('text', 'device', 'point')})
    return out


def _casting(roles, s, key):
    if key == HUYU:
        role = speech_settings.role_config(roles['comfort'], s)
        return {'voice': role['voice'], 'dialect': '上海话', 'rate': role.get('rate'),
                'instruction': speech_settings.DIALECTS['上海话'] + '用地道的上海话，慢一点，像自言自语地嘀咕。'}
    role = speech_settings.role_config(roles[key], s)
    return {'voice': role['voice'], 'dialect': role.get('dialect', '普通话'), 'rate': role.get('rate'),
            'instruction': role['instruction']}


def _problem(text, ctx, max_chars):
    if not isinstance(text, str) or not 2 <= len(text.strip()) <= max_chars or BAD_CHARS.search(text):
        return 'format'
    if any(difflib.SequenceMatcher(None, norm(text), norm(r.get('text'))).ratio() >= 0.78 for r in ctx.get('recent', [])):
        return 'repeat'
    return None


def write_round(env, store, s, roles, ctx, now_ms, *, chat=None, trigger=None, moment_id=None, rng=None):
    rng = rng or random.Random()
    max_chars = s['speech']['max_chars']
    scope_id = ctx['scope']['id']
    hist = history(store.db, scope_id, now_ms)
    cast = {k: _casting(roles, s, k) for k in (*KEYS, HUYU)}
    started = time.monotonic()

    def write(key):
        if key == HUYU:
            msgs = P.huyu_messages(ctx, now_ms, hist, max_chars)
            return key, _call(env, store, s, 'say_huyu', PROMPTS['huyu'], P.HUYU_SCHEMA, msgs, now_ms, chat, 0.65, 160)
        msgs = P.line_messages(key, {**roles[key], **cast[key]}, ctx, now_ms, hist, max_chars)
        return key, _call(env, store, s, 'say_line', PROMPTS['line'], P.LINE_SCHEMA, msgs, now_ms, chat, 0.65, 160)

    with ThreadPoolExecutor(len(KEYS) + 1) as pool:
        results = dict(pool.map(write, (*KEYS, HUYU)))
    write_ms = int((time.monotonic() - started) * 1000)
    run_ids = [r[1] for r in results.values() if r[1]]
    lines = []
    for key in (*KEYS, HUYU):
        out, _, err = results[key]
        line = {'key': key, **cast[key], 'score': None, 'shown_as': None, 'dropped': None}
        if err or not isinstance(out, dict):
            line['dropped'] = 'invalid'
        else:
            line.update({k: out.get(k) for k in ('point', 'device', 'text', 'gloss') if k in out})
            line['dropped'] = _problem(out.get('text'), ctx, max_chars)
        lines.append(line)
    usable = [x for x in lines if x['key'] != HUYU and not x['dropped']]
    if len(usable) < 2:
        raise RuntimeError('这一轮没写出足够的句子，请重试')
    order = list(usable)
    rng.shuffle(order)
    labels = [{'text': x['text'], 'voice_label': P.VOICE_LABELS.get(x['voice'], x['voice']), 'dialect': x['dialect']}
              for x in order]
    judge_started = time.monotonic()
    out, judge_run, err = _call(env, store, s, 'say_judge', PROMPTS['judge'], P.JUDGE_SCHEMA,
                                P.judge_messages(ctx, now_ms, labels), now_ms, chat, 0, 80)
    judge_ms = int((time.monotonic() - judge_started) * 1000)
    if judge_run:
        run_ids.append(judge_run)
    scores = out.get('scores') if isinstance(out, dict) and isinstance(out.get('scores'), list) else []
    for i, x in enumerate(order):
        x['shown_as'] = i + 1
        x['score'] = scores[i] if i < len(scores) and isinstance(scores[i], int) else None
    best = out.get('best') if isinstance(out, dict) else None
    ranked = sorted(order, key=lambda x: -(x['score'] or 0))
    if isinstance(best, int) and 1 <= best <= len(order):
        ranked.remove(order[best - 1]); ranked.insert(0, order[best - 1])
    rid = 'r' + uuid.uuid4().hex
    cost = sum(d.get('cost_usd') or 0 for d in store.db['mind_runs'].find({'run_id': {'$in': run_ids}}, {'cost_usd': 1}))
    doc = {'rid': rid, 'moment_id': moment_id or rid, 'at': now_ms, 'written_at': now_ms, 'scope_id': scope_id,
           'trigger': trigger or {'source': 'page'}, 'prompts': PROMPTS, 'model': MODEL,
           'signature': signature(ctx, now_ms), 'context': ctx, 'lines': lines,
           'judge': {'order': [x['key'] for x in ranked], 'best_key': ranked[0]['key'],
                     'weak': bool(isinstance(out, dict) and out.get('weak')),
                     'error': err if not scores else None, 'run_id': judge_run},
           'checks': [], 'plays': [], 'state': 'ready', 'run_ids': run_ids,
           'timings': {'write_ms': write_ms, 'judge_ms': judge_ms}, 'cost_usd': cost}
    store.db['say_rounds'].insert_one(dict(doc))
    return doc


def ranked_keys(round_):
    return list(round_['judge']['order'])


def line(round_, key):
    return next(x for x in round_['lines'] if x['key'] == key)


def is_thin(round_, ctx, now_ms, s):
    # Replay 2026-11-13: requiring 'usable' speech (both recognisers agreeing) or trusting the judge's weak
    # flag made 4 of 5 typical moments "thin". Thin = no speech at all in 60 s; the weak flag is opt-in
    # until the owner's "这一轮都不行" marks calibrate it.
    flags = (s.get('speech') or {}).get('round') or {}
    no_speech = not any(e.get('kind') == 'speech' and e.get('at', 0) >= now_ms - 60_000 for e in ctx.get('evidence', []))
    return (flags.get('thin_no_speech', True) and no_speech) or (flags.get('thin_judge_weak', False) and round_['judge']['weak'])


def check_round(env, store, s, round_, ctx_now, now_ms, *, chat=None, keys=None):
    """The best still-fitting key among `keys` (default: the judge order), or None."""
    keys = list(keys or ranked_keys(round_))
    ranked = [{'text': line(round_, k)['text']} for k in keys]
    started = time.monotonic()
    out, run_id, err = _call(env, store, s, 'say_check', PROMPTS['check'], P.CHECK_SCHEMA,
                             P.check_messages(ctx_now, now_ms, ranked, round_['written_at']), now_ms, chat, 0, 20)
    pick = out.get('pick') if isinstance(out, dict) else None
    key = keys[pick - 1] if isinstance(pick, int) and 1 <= pick <= len(keys) else None
    entry = {'at': now_ms, 'pick': key, 'run_id': run_id, 'ms': int((time.monotonic() - started) * 1000), 'error': err}
    round_['checks'].append(entry)
    store.db['say_rounds'].update_one({'rid': round_['rid']}, {'$set': {'checks': round_['checks']}})
    return key
