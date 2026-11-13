"""Scene-scoped speech preferences. Private environment/identity values stay in settings."""
import copy
import math

PROFILES = ('office', 'home', 'friends')
ROUND_DEFAULTS = {'thin_no_speech': True, 'thin_judge_weak': True, 'huyu': True, 'moment_gap_s': 90,
                  'moment_window_s': 90, 'prefetch_quiet_s': 5, 'prefetch_spacing_s': 30, 'prefetch_usd_per_hour': 0.6}
ROUND_LIMITS = {'moment_gap_s': (10, 600), 'moment_window_s': (10, 600), 'prefetch_quiet_s': (1, 60),
                'prefetch_spacing_s': (5, 600), 'prefetch_usd_per_hour': (0, 5)}
ROLES = ('comfort', 'deadpan', 'roast', 'sad', 'worst')
DIALECTS = {'普通话': '请讲普通话。', '上海话': '请讲上海话。',
            '四川话': '请讲四川话。', '武汉话': '请讲湖北武汉话。'}


def defaults():
    return {'pipeline': 'v2', 'profile': 'office', 'scope_since': 0, 'max_chars': 40, 'roles': {},
            # One-button talk show (spec 2026-11-13-unified-say-button-design.md, sections 6, 6A, 14).
            'round': dict(ROUND_DEFAULTS),
            'profiles': {
                'office': {'scope_id': 'office', 'environment': None,
                           'topics': ['销售空头承诺', '客户无理要求', '活多钱少', '抱怨忙', '项目要完蛋', '出馊主意', '发牢骚'], 'avoid': []},
                'home': {'scope_id': 'home', 'environment': '',
                         'topics': ['家务分配', '拖延', '花钱', '吃饭', '作息', '懒得动'], 'avoid': []},
                'friends': {'scope_id': 'friends', 'environment': '',
                            'topics': ['吹牛', '迟到', '抢单', '白嫖', '计划与行动的落差'], 'avoid': []}}}


def _number(v, lo, hi):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and lo <= v <= hi


def validate(speech, memory):
    errors = []
    if not isinstance(speech, dict):
        return ['speech: must be an object']
    if not isinstance(memory, dict):
        return ['memory: must be an object']
    if set(speech) - set(defaults()):
        errors.append('speech: unknown setting')
    if speech.get('pipeline') not in ('v1', 'v2', 'round'):
        errors.append('speech.pipeline: v1, v2 or round')
    rnd = speech.get('round', ROUND_DEFAULTS)
    if not isinstance(rnd, dict) or set(rnd) - set(ROUND_DEFAULTS):
        errors.append('speech.round: unknown setting')
    else:
        for key, value in rnd.items():
            limits = ROUND_LIMITS.get(key)
            if limits is None and not isinstance(value, bool):
                errors.append(f'speech.round.{key}: true or false')
            elif limits and not _number(value, *limits):
                errors.append(f'speech.round.{key}: {limits[0]}–{limits[1]}')
    if speech.get('profile') not in PROFILES:
        errors.append('speech.profile: office/home/friends')
    if not isinstance(speech.get('max_chars'), int) or not _number(speech.get('max_chars'), 8, 40):
        errors.append('speech.max_chars: integer 8–40')
    if not _number(speech.get('scope_since', 0), 0, 10**15):
        errors.append('speech.scope_since: timestamp')
    profiles = speech.get('profiles')
    if not isinstance(profiles, dict) or set(profiles) != set(PROFILES):
        errors.append('speech.profiles: office/home/friends objects required')
    else:
        for name, p in profiles.items():
            if not isinstance(p, dict):
                errors.append(f'speech.profiles.{name}: must be an object')
                continue
            if set(p) - {'scope_id', 'environment', 'topics', 'avoid'} or p.get('scope_id') != name:
                errors.append(f'speech.profiles.{name}: invalid or mutable scope')
            env = p.get('environment')
            if not (name == 'office' and env is None) and not (isinstance(env, str) and len(env) <= 3000):
                errors.append(f'speech.profiles.{name}.environment: text up to 3000 characters')
            for key in ('topics', 'avoid'):
                arr = p.get(key)
                if not isinstance(arr, list) or len(arr) > 16 or not all(isinstance(x, str) and 1 <= len(x) <= 80 for x in arr):
                    errors.append(f'speech.profiles.{name}.{key}: up to 16 short phrases')
    roles = speech.get('roles')
    if not isinstance(roles, dict) or set(roles) - set(ROLES):
        errors.append('speech.roles: unknown role')
    else:
        for name, r in roles.items():
            if not isinstance(r, dict) or set(r) - {'dialect', 'instruction', 'rate'}:
                errors.append(f'speech.roles.{name}: invalid override')
                continue
            if 'dialect' in r and (not isinstance(r['dialect'], str) or r['dialect'] not in DIALECTS):
                errors.append(f'speech.roles.{name}.dialect: unsupported')
            if 'instruction' in r and not (isinstance(r['instruction'], str) and len(r['instruction']) <= 400):
                errors.append(f'speech.roles.{name}.instruction: up to 400 characters')
            if 'rate' in r and not _number(r['rate'], 0.5, 2):
                errors.append(f'speech.roles.{name}.rate: finite number 0.5–2')
    if set(memory) - {'learning', 'apply'} or any(not isinstance(memory.get(k), bool) for k in ('learning', 'apply')):
        errors.append('memory: learning/apply must be booleans')
    return errors


def scope(s):
    speech = s.get('speech') or defaults()
    profile = speech.get('profile', 'office')
    return {'id': profile, 'profile': profile, 'since': int(speech.get('scope_since', 0))}


def effective(s):
    out = copy.deepcopy(s)
    speech = out.get('speech') or defaults()
    profile = speech.get('profile', 'office')
    p = speech['profiles'][profile]
    if p.get('environment') is not None:
        out['environment'] = p['environment']
    if profile != 'office':
        out['place'] = {**out.get('place', {}), 'default': {'home': '家里', 'friends': '朋友聚会'}[profile]}
    return out


def role_config(role, s):
    out = copy.deepcopy(role)
    override = ((s.get('speech') or {}).get('roles') or {}).get(role['id']) or {}
    dialect = override.get('dialect', role.get('dialect', '普通话'))
    delivery = override.get('instruction', role.get('delivery', role.get('instruction', '')))
    out['dialect'] = dialect
    out['instruction'] = DIALECTS[dialect] + delivery
    if 'rate' in override:
        out['rate'] = override['rate']
    return out
