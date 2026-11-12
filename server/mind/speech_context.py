"""Fresh, scoped evidence for speech. Preserve ASR alternatives instead of doubling votes."""
import math
import re
import statistics
from collections import defaultdict

from . import package, selfvoice, settings, speech_settings
from .memory import MemoryStore, fingerprint
from .records import Records


def norm(text):
    return re.sub(r'[^\w\u3400-\u9fff]', '', str(text or '')).lower()


def subject(name, s):
    owner = s.get('owner') or {}
    if name in [owner.get('display'), *owner.get('voice_names', []), *owner.get('face_names', []), *owner.get('aliases', [])] and name:
        return 'owner'
    for person in s.get('people') or []:
        if name and name in [person.get('display'), *person.get('voice_names', []), *person.get('face_names', [])]:
            return 'person:'+fingerprint(person.get('display'))[:16]
    return 'unknown'


def _num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def group_speech(window, s, now_ms, max_rows=240):
    """Connected overlap groups handle one-to-many ASR segmentation in both directions."""
    unique = {}
    for collection, enabled in (('speech', s['sources']['speech_text']), ('tingwu', s['sources']['tingwu'])):
        if not enabled:
            continue
        for d in window.get(collection, []):
            if collection=='tingwu' and d.get('kind', 'sentence')!='sentence':
                continue
            text = str(d.get('text') or '').strip()
            if not text or selfvoice.is_hello(text, bool((d.get('self') or {}).get('sounding'))):
                continue
            ref = {'c': collection, 'session': d.get('session'), ('id' if collection=='speech' else 'n'): d.get('id' if collection=='speech' else 'n')}
            unique[fingerprint(ref)] = {**d, 'text': text[:600], '_ref': ref, '_collection': collection}
    rows = sorted(unique.values(), key=lambda x:x['_wall'])[-max_rows:]
    parent = list(range(len(rows)))

    def root(i):
        while parent[i]!=i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(rows):
        for j in range(i):
            b = rows[j]
            if a.get('session')!=b.get('session') or a['_collection']==b['_collection']:
                continue
            overlap = package._overlap(a.get('pos'), b.get('pos'))
            same = abs(a['_wall']-b['_wall'])<=2000 and norm(a['text'])==norm(b['text'])
            if overlap>0 or same:
                parent[root(i)] = root(j)
    groups = defaultdict(list)
    for i, row in enumerate(rows):
        groups[root(i)].append(row)
    out = []
    for group in groups.values():
        variants = []
        for engine in ('speech', 'tingwu'):
            parts = list(dict.fromkeys(r['text'] for r in group if r['_collection']==engine))
            if parts:
                variants.append({'engine': engine, 'text': '，'.join(parts)[:600]})
        text = variants[-1]['text']
        names = {r.get('speaker', {}).get('name') for r in group if r.get('speaker', {}).get('band')=='known'}-{None}
        name = next(iter(names)) if len(names)==1 else None
        raw = [r for r in group if r['_collection']=='speech']
        refs = sorted([r['_ref'] for r in group], key=lambda x:json_key(x))
        valid_text = len(norm(text))>=2 and len(set(norm(text)))>=2
        # High character similarity cannot establish semantic agreement: a single 不, date,
        # or amount can reverse the meaning. Preserve both alternatives and stay conservative.
        mismatch = len(variants)>1 and norm(variants[0]['text'])!=norm(variants[1]['text'])
        low_voice = raw and all(_num((r.get('voice') or {}).get('p')) and r['voice']['p']<0.4 for r in raw) and len(variants)==1
        at = max(r['_wall'] for r in group)
        item = {'id': 's'+fingerprint(refs)[:16], 'kind': 'speech', 'at': at, 'text': text, 'alternatives': variants,
                'observed_at':min(r['_wall'] for r in (raw or group)),
                'earliest_at':min(r['_wall'] for r in group),
                'refs': refs, 'subject_id': subject(name,s), 'speaker': name, 'speaker_reliable': bool(name),
                'asr_conflict': mismatch, 'quality': 'usable' if valid_text and not mismatch and not low_voice else 'uncertain',
                'priority': 'highest' if now_ms-at<=30_000 else 'support', 'emotion_boost': False,
                'emotion_reliable': False, 'emotion_cues': [], '_raw': raw,
                '_speaker_group': subject(name,s) if name else 'unknown:'+str(group[0].get('session'))}
        out.append(item)
    _emotions(out, now_ms)
    for item in out:
        item.pop('_raw', None)
        item.pop('_speaker_group', None)
    return sorted(out, key=lambda x:x['at'])


def json_key(value):
    return fingerprint(value)


def _emotions(items, now_ms):
    for item in items:
        if item['at']<now_ms-30_000:
            continue
        emotions = {r.get('emotion') for r in item['_raw']}-{None, '', 'neutral', 'unknown'}
        for emotion in emotions:
            peers = [p for p in items if p['at']>=now_ms-30_000 and p['_speaker_group']==item['_speaker_group']
                     and any(r.get('emotion')==emotion for r in p['_raw'])]
            if len(peers)<2:
                continue
            baseline = [r for p in items if now_ms-120_000<=p['at']<now_ms-30_000 and p['_speaker_group']==item['_speaker_group'] for r in p['_raw']]
            loud = [(r.get('voice') or {}).get('loud_db') for r in baseline]
            rates = [(r.get('voice') or {}).get('rate') for r in baseline]
            loud, rates = [v for v in loud if _num(v)], [v for v in rates if _num(v) and v>0]
            evidence = []
            for r in item['_raw']:
                voice = r.get('voice') or {}
                if len(loud)>=3 and _num(voice.get('loud_db')) and voice['loud_db']-statistics.median(loud)>=6:
                    evidence.append('louder_than_baseline')
                if len(rates)>=3 and _num(voice.get('rate')) and voice['rate']/statistics.median(rates)>=1.5:
                    evidence.append('faster_than_baseline')
                if r.get('event') in ('cry', 'crying', 'laughter', 'laugh'):
                    evidence.append('vocal_event')
            if evidence:
                item['emotion_boost'] = True
                item['emotion_cues'] = sorted(set(evidence))
                item['emotion_label'] = emotion


def build(db, store, s, now_ms):
    scope = speech_settings.scope(s)
    effective = speech_settings.effective(s)
    start = max(now_ms-120_000, scope['since'])
    window = Records(db).window(start, now_ms+1)
    evidence = group_speech(window, s, now_ms)[-12:]
    if s['sources']['vision']:
        latest = {}
        for row in db['eye_minutes'].find({'minute': {'$gte': now_ms-180_000, '$lte': now_ms}, 'source':'model'}, {'_id':0}):
            if row['minute'] not in latest or row.get('version',0)>latest[row['minute']].get('version',0):
                latest[row['minute']] = row
        visual = []
        for row in sorted(latest.values(), key=lambda r:r['minute'], reverse=True):
            frames = [f.get('at') for f in row.get('frames', []) if _num(f.get('at'))]
            if not frames or min(frames)<scope['since'] or not start<=max(frames)<=now_ms:
                continue
            result = row.get('result') or {}
            if result.get('activity'):
                usable = result.get('confidence') in ('high','mid')
                if visual and not usable:
                    continue
                ref = {'c':'eye_minutes', 'minute':row['minute'], 'version':row.get('version',0)}
                visual.append({'id':'v'+fingerprint(ref)[:16], 'kind':'vision', 'at':max(frames),
                                 'text':result['activity'], 'changes':result.get('changes') or '',
                                 'confidence':result.get('confidence', 'low'), 'refs':[ref],
                                 'quality':'usable' if usable else 'uncertain', 'priority':'support'})
                # Keep the latest uncertainty visible, but do not let it hide a separate,
                # still-fresh clear observation. Never resurrect a superseded revision.
                if usable:
                    break
        evidence.extend(visual)
    if s['sources']['speech_text'] or s['sources']['tingwu']:
        conv = db['conversations'].find_one({'start_ms':{'$gte':scope['since'], '$lte':now_ms}}, {'_id':0}, sort=[('start_ms',-1)])
        if conv and (conv.get('notes') or conv.get('title')):
            version = db['conversation_versions'].find_one({'cid':conv['cid'], 'at':{'$lte':now_ms}}, {'_id':0}, sort=[('version',-1)])
            source = version or conv
            covered = source.get('covered_last_ms')
            at = covered if _num(covered) else min(conv.get('last_ms',0),source.get('at',conv.get('updated_at',conv.get('last_ms',0))))
            if start<=at<=now_ms:
                evidence.append({'id':'n'+fingerprint([conv['cid'],source.get('version',0)])[:16], 'kind':'notes', 'at':at,
                                 'text':f"{source.get('title') or ''}：{source.get('notes') or ''}",
                                 'refs':[{'c':'conversation_versions','cid':conv['cid'],'version':source.get('version',0)}],
                                 'quality':'usable' if source.get('confidence')!='low' and (not version or _num(covered)) else 'uncertain', 'priority':'support'})
    recent = []
    for u in db['utterances'].find({'at':{'$gte':now_ms-1_800_000, '$lte':now_ms}}, {'_id':0}).sort('at',-1).limit(100):
        if u.get('role')=='try' or u.get('scope_id','office')!=scope['id']:
            continue
        if u.get('state') not in ('done','playing','unknown') and not u.get('played'):
            continue
        recent.append({k:u.get(k) for k in ('uid','text','state','role','focus','joke_type')})
        if len(recent)>=20:
            break
    warnings, memories = [], []
    if s.get('memory',{}).get('apply',True):
        try:
            remembered = MemoryStore(db).recall(scope['id'], now_ms, limit=40)
            local = settings.local_time(now_ms, s['tz'])
            day_type = 'workday' if local.isoweekday() in s['work']['days'] else 'restday'
            memories = [m for m in remembered if m['kind']!='routine' or (m.get('hour')==local.hour and m.get('day_type')==day_type)][:12]
        except Exception as e:
            warnings.append('memory_unavailable:'+type(e).__name__)
    ctx = {'scope':scope, 'environment':effective.get('environment',''), 'time':settings.local_label(now_ms,s['tz']),
           'profile':s['speech']['profiles'][scope['id']], 'evidence':evidence, 'memories':memories, 'recent':recent, 'warnings':warnings}
    ctx['fingerprint'] = fingerprint({**ctx, 'settings':s})
    return ctx
