"""Same moment (spec section 6): rounds and marks inside one moment count once.

Two triggers are the same moment when they are at most 90 s apart and at least half of the sentences
heard in the 90 s before each (clear or not) are shared. With no sentences on either side, the scene
minute decides. 90 s: on the 2026-11-12 clicks, 60 s split moments the owner saw as one (talk went on,
words rolled out of the window); 90 s reproduces the 10 expected moments, 120 s does too.
"""
from .speech_context import norm

GAP_MS = 90_000
WINDOW_MS = 90_000


def signature(ctx, now_ms, window_ms=WINDOW_MS):
    texts = {norm(e.get('text')) for e in ctx.get('evidence', [])
             if e.get('kind') == 'speech' and e.get('at', 0) >= now_ms - window_ms}
    texts.discard('')
    scene = ctx.get('scene') or {}
    return {'at': now_ms, 'texts': sorted(texts), 'scene_minute': scene.get('minute')}


def same_moment(prev, cur, gap_ms=GAP_MS):
    if not prev or cur['at'] - prev['at'] > gap_ms:
        return False
    a, b = set(prev.get('texts') or []), set(cur.get('texts') or [])
    if not a and not b:
        return prev.get('scene_minute') is not None and prev.get('scene_minute') == cur.get('scene_minute')
    if not a or not b:
        return False
    return len(a & b) * 2 >= min(len(a), len(b))
