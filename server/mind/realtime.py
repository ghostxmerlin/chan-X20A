"""围观模式 realtime spike (2026-11-14): the context block the ear's realtime voice session gets (GET /realtime/context).

The ear (server/ear/realtime_link.py) runs one end-to-end voice session (qwen-audio-3.1-realtime-flash) while hello
reports `crowd`. It fetches this block when the session opens and every ~60 s and puts it into the session's
instructions. Nothing here calls a model or costs money; it only reads what mind already has.

The block (plain Chinese text, at most MAX_CHARS):
  现在 / 环境         the local time, the environment text (as chat)
  此刻的情景 / 在聊 / 声音   the latest scene (chat._load_scene: summary, topic, sound; fresh within 4 min)
  对话纪要            the latest conversation notes (chat._load_notes)
  看到的人            who the M12 named in the last VISIBLE_MS (a later face_lost drops that face) and the latest eye
                      minute (people / activity, fresh within EYE_FRESH_MS)
  你记得              at most 3 remembered statements (MemoryStore.recall, best first)
  认识的人            library cards of the people seen or of the owner: display + active notes (+ "不要调侃他")
The answer also carries owner {face_names, call} and names {M12 face name: display} (library cards' links.face_names,
then settings people): the ear's owner greeting and tap greeting need them, and `call` is how hello calls the owner today (settings.owner_name, rotating daily among the aliases). Those are private settings
data on the loopback API; the ear never logs them.

Parts are cached (TTL_S) so the ear's 60 s refresh and the scene_now tool cost a few small reads.
"""
import collections
import time

from . import settings as settings_mod, speech_settings
from .chat import MAX_MEMORIES
from .library import Library

VISIBLE_MS = 3 * 60_000
EYE_FRESH_MS = 3 * 60_000
MAX_CHARS = 1500
TTL_S = 15


def visible_faces(db, now_ms, window_ms=VISIBLE_MS):
    """M12 names seen in the window, newest first: a face_named whose face id has no later face_lost / vision_off."""
    rows = list(db['vision'].find({'at': {'$gte': now_ms - window_ms, '$lte': now_ms}}, {'_id': 0}).sort('at', 1))
    faces = {}
    for e in rows:
        ev = e.get('event')
        if ev == 'face_named' and e.get('name'):
            faces[(e.get('session'), e.get('face'))] = (e['name'], e.get('at') or 0)
        elif ev == 'face_lost':
            faces.pop((e.get('session'), e.get('face')), None)
        elif ev == 'vision_off':
            faces = {k: v for k, v in faces.items() if k[0] != e.get('session')}
    out = []
    for name, _ in sorted(faces.values(), key=lambda v: -v[1]):
        if name not in out:
            out.append(name)
    return out


def latest_eye(db, now_ms, fresh_ms=EYE_FRESH_MS):
    doc = db['eye_minutes'].find_one({'source': 'model'}, {'_id': 0}, sort=[('minute', -1), ('version', -1)])
    if not doc or now_ms - (doc.get('minute') or 0) > fresh_ms + 60_000 or not isinstance(doc.get('result'), dict):
        return None
    return doc['result']


def face_cards(db):
    """face name -> {display, notes (active, up to 6), tease} from the library (links.face_names)."""
    lib = Library(db)
    notes = collections.defaultdict(list)
    for n in sorted(lib._heads('people_notes'), key=lambda r: -(r.get('changed_at') or 0)):
        if n.get('status') == 'active' and len(notes[n['person_id']]) < 6:
            notes[n['person_id']].append(n['text'])
    out = {}
    for c in lib._heads('people_cards'):
        if c.get('status') == 'retired':
            continue
        for name in (c.get('links') or {}).get('face_names') or []:
            out[name] = {'person_id': c['person_id'], 'display': c['display'], 'notes': notes[c['person_id']],
                         'tease': (c.get('treatment') or {}).get('tease')}
    return out


class RealtimeContext:
    """block() -> {text, owner: {face_names, call}, visible: [display], at}. chat is the live chat.Chat (its scene /
    notes / memories loaders are reused; their state is not touched)."""

    def __init__(self, db, chat, settings_fn, clock_ms=None, mono=time.monotonic):
        self.db, self.chat, self.settings_fn = db, chat, settings_fn
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self.mono = mono
        self._cache = {}

    def _part(self, name, fn):
        got = self._cache.get(name)
        if got and self.mono() - got[0] < TTL_S:
            return got[1]
        try:
            value = fn()
        except Exception:                     # noqa: BLE001 - a missing part is left out, never an error
            value = got[1] if got else None
        self._cache[name] = (self.mono(), value)
        return value

    def block(self, now_ms=None):
        now = self.clock_ms() if now_ms is None else now_ms
        s = self.settings_fn()
        owner = s.get('owner') or {}
        owner_faces = [n for n in owner.get('face_names') or [] if n]
        call = settings_mod.owner_name(s, now)
        chat = self.chat
        scene = self._part('scene', lambda: chat._load_scene(now)) if chat else None
        notes = self._part('notes', lambda: chat._load_notes(now)) if chat else None
        memories = (self._part('memories', lambda: chat._load_memories(now)) if chat else None) or []
        faces = self._part('faces', lambda: visible_faces(self.db, now)) or []
        eye = self._part('eye', lambda: latest_eye(self.db, now))
        cards = self._part('cards', lambda: face_cards(self.db)) or {}
        others = {n: p.get('display') for p in s.get('people') or [] for n in p.get('face_names') or [] if p.get('display')}

        L = [f'现在：{settings_mod.local_label(now, s["tz"])}']
        env = (speech_settings.effective(s).get('environment') or '').strip()
        if env:
            L.append(f'环境：{env[:300]}')
        if scene:
            if scene.get('doing') or scene.get('summary'):
                L.append(f'此刻的情景：{scene.get("doing") or ""}（{scene.get("summary") or ""}）')
            if scene.get('topic'):
                L.append('在聊：' + ' / '.join(scene['topic']))
            if scene.get('sound'):
                L.append(f'声音：{scene["sound"]}')
        if notes:
            L.append(f'对话纪要：{notes}')
        visible, people = [], []
        for name in faces:
            if name in owner_faces:
                visible.append(f'主人（{call}）')
            else:
                visible.append((cards.get(name) or {}).get('display') or others.get(name) or name)
            card = cards.get(name)
            if card and card not in people:
                people.append(card)
        if visible:
            L.append('看到的人（M12 认出）：' + '、'.join(dict.fromkeys(visible)))
        if eye:
            seen = '；'.join(f'{p.get("who")}{p.get("where") or ""}{p.get("doing") or ""}'
                            for p in (eye.get('people') or [])[:4] if isinstance(p, dict))
            if seen or eye.get('activity'):
                L.append(f'画面：{seen or eye.get("activity")}')
        if memories:
            L.append('你记得（只在相关时提）：' + '；'.join(memories[:MAX_MEMORIES]))
        for p in people[:4]:
            line = f'认识的人：{p["display"]}'
            if p.get('notes'):
                line += '（' + '；'.join(p['notes']) + '）'
            if p.get('tease') == 'never':
                line += '。不要调侃他。'
            L.append(line)
        text = '\n'.join(L)[:MAX_CHARS]
        names = {**others, **{n: c['display'] for n, c in cards.items() if c.get('display')}}
        return {'text': text, 'owner': {'face_names': owner_faces, 'call': call}, 'names': names,
                'visible': list(dict.fromkeys(visible)), 'at': now}
