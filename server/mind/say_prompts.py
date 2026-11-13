"""Prompts for the one-button talk show (spec docs/superpowers/specs/2026-11-13-unified-say-button-design.md).

One call per direction (written in parallel), one call for the Shanghainese fallback line, one judge
call, and at speaking time at most one check call. Ported from the 2026-11-13 spike, third version.
Voice and dialect are decided by the server (role_config); the model only writes in that dialect.
"""
import json

from . import scene_speech as S

DIRECTIONS = {
    'comfort': '鼓励（安慰性讽刺）：站在干活的人这边。先让他觉得你懂他的难处，再顺手讽刺造成麻烦的那一方：乱提需求的、白嫖的、瞎催的。不灌鸡汤，不夸天才，不责怪正在难受的人。',
    'deadpan': '呆呆：一本正经，把眼前的话或规矩当真，顺着走一步，让荒唐自己露出来。越像在陈述普通事实越好，自己不觉得好笑。',
    'roast': '吐槽：旁边那个会接话的熟人。先顺着对方半句，再翻过来补一刀；短反问、装认同都行。蔫坏，有分寸，不做人身评判。',
    'sad': '伤心（丧气）：丧。越想越觉得自己亏了：先有一个实在吃亏的地方，再短短地诉苦或自嘲，丧得让人想笑。不煽情，不写悼词。',
    'worst': '最坏（直面现实）：主人自己的损嘴。别人还在绕，你把难看的实话直接说破：这事现在到底是个什么局面。说的是眼前的现实，不是以后的预言；直、急、不绕弯，像「这不完犊子了吗」那种口气，每次换个说法开头。牢骚听得出是牢骚，不捏造没发生的事。',
}
VOICE_LABELS = {'tang': '汤师爷', 'wuxian': '无限', 'anhuan': '龙安欢', 'xiaohei': '小黑', 'me01': '主人自己的声音'}

_BASE = '''你是 hello，主人工位桌上的小机器人，像一只有主见的宠物：一直在旁边听着看着，忍不住插一句嘴。你不是同事：不回答问题，不出主意，不追问细节，不总结大家在聊什么。
【抓点】实时输入按新到旧排列。优先抓30秒内清楚的原话，其次对话纪要，再次当前情景和画面。只抓一个具体的点：某人刚说的一个词、一个要求、一个动作、一个反差。残缺难懂的转写别猜意思，换一个点。长期认识只当背景。
【底线】谁说了什么、发生了什么只能来自输入；夸张、预言、歪理可以放开说，但要一听就知道是玩笑。场景偏好只影响口气，不凭空编出客户、销售、加班。
别用这些老套路：「……是一回事，……又是一笔账」「这笔账」「还得另算」「……还没……，……先……了」「看来……也……」。笑点在人身上，不在东西身上：别给故障、现象、板子、库存、桌椅这些东西安脾气（躲猫猫、给面子、挑座位、闹情绪）；只有 hello 自己这台小机器可以拿来说事。
【格式】一句口语，尽量不超过25字，最多{max_chars}字；可以是两个短分句或一个反问。不报幕、不解释、不加动作、引号或括号。只输出 JSON。'''

LINE_RULES = _BASE + '''
【本次只写一句】只写下面这个方向的一句：
{direction}
大家刚听过原话，不用交代背景，直接说包袱。先写 point（这句抓住的那个点，十个字以内），再从六种笑法里选一种写进 device，「这个方向上一句的笑法」这次换一种：
{devices}
最出人意料的词放在句尾。「这个方向最近写过」里的句式和意思这次别再用，「这个方向用滥的词」一个都别用。
【谁来说】这句由{voice}用{dialect}说出来。按{dialect}的说法写，一两处地道的词就够，说普通话的人也要能听懂；普通话的句子别带方言词。'''

HUYU_RULES = _BASE + '''
【本次写一句地道的上海话】现在素材不多，可能冷场。写一句地道的上海话碎碎念，用沪语的用字和说法（侬、阿拉、伐、老、交关、勿等），不必让说普通话的人听懂，靠腔调逗人。内容是对眼前这一刻或对 hello 自己的嘀咕，同样不能编造事实。text 写上海话原句，gloss 写它的普通话意思。「最近写过」里的句子别重复。'''

JUDGE_RULES = '''你替桌上的小机器人 hello 把关。它刚听到下面这些话，准备插一句嘴，写了几句候选，每句配了嗓子和方言。
给每句打分，1 到 10 的整数。看这几条：是不是接住了刚才的话，越新越好；有没有意外；短不短；像不像熟人顺嘴说的；这副嗓子和方言说这句合不合适。硬编的、复述原话的、不知所云的给低分。
scores 按候选的编号顺序给出。best 填现场效果最好的那句的编号。如果连最好的一句说出来也多半冷场，weak 填 true。只输出 JSON。'''

CHECK_RULES = '''桌上的小机器人 hello 事先写好了几句插嘴，按好坏排好了序。写完之后屋里又说了新的话。
判断现在说哪一句还接得上：优先排在前面的；如果新的话已经换了话题，或者某句现在说出来会显得答非所问，就跳过它。pick 填要说的那句的编号；一句都不合适就填 0。只输出 JSON。'''

LINE_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['point', 'device', 'text'],
               'properties': {'point': {'type': 'string'}, 'device': {'type': 'string', 'enum': list(S.DEVICES)},
                              'text': {'type': 'string'}}}
HUYU_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['text', 'gloss'],
               'properties': {'text': {'type': 'string'}, 'gloss': {'type': 'string'}}}
JUDGE_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['scores', 'best', 'weak'],
                'properties': {'scores': {'type': 'array', 'items': {'type': 'integer'}}, 'best': {'type': 'integer'},
                               'weak': {'type': 'boolean'}}}
CHECK_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['pick'],
                'properties': {'pick': {'type': 'integer'}}}


def _payload(ctx, now_ms, extra):
    return {'现在': ctx['time'], '当前情景': (ctx.get('scene') or {}).get('text'),
            '实时输入': S.evidence(ctx, now_ms)[::-1], '长期认识': [m['statement'] for m in ctx.get('memories', [])],
            '场景偏好': ctx['profile'].get('topics', []), '避免话题': ctx['profile'].get('avoid', []), **extra}


def _messages(system, ctx, payload):
    return [{'role': 'system', 'content': system + '\n【环境】' + (ctx.get('environment') or '')},
            {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}]


def line_messages(direction, role, ctx, now_ms, history, max_chars=40):
    """history: {direction: [{text, device, point}, ...]} including lines that were written but not played."""
    mine = history.get(direction, [])
    system = LINE_RULES.format(max_chars=max_chars, direction=DIRECTIONS[direction],
                               devices='\n'.join(f'- {k}：{v}' for k, v in S.DEVICES.items()),
                               voice=VOICE_LABELS.get(role.get('voice'), role.get('voice') or 'hello'),
                               dialect=role.get('dialect', '普通话'))
    extra = {'这个方向最近写过': [x['text'] for x in mine[-4:]],
             '这个方向上一句的笑法': mine[-1].get('device') if mine else None,
             '这个方向用滥的词': S.overused({'recent': [{'text': x['text']} for x in mine[-8:]], 'evidence': ctx['evidence']})}
    return _messages(system, ctx, _payload(ctx, now_ms, extra))


def huyu_messages(ctx, now_ms, history, max_chars=40):
    mine = history.get('huyu', [])
    return _messages(HUYU_RULES.format(max_chars=max_chars), ctx,
                     _payload(ctx, now_ms, {'最近写过': [x['text'] for x in mine[-6:]]}))


def _heard(ctx, since_ms=None):
    rows = []
    for e in ctx['evidence']:
        if e['kind'] != 'speech' or (since_ms is not None and e['at'] <= since_ms):
            continue
        rows.append('- ' + e['text'][:200] + ('' if e.get('quality') == 'usable' else '（听不太清）'))
    return rows[-5:]


def judge_messages(ctx, now_ms, lines):
    """lines: already shuffled, each {text, voice_label, dialect}; numbering follows this order."""
    heard = '\n'.join(_heard(ctx)) or '（没有听到清楚的话）'
    cands = '\n'.join(f"{i + 1}. {x['text']}（{x['voice_label']}，{x['dialect']}）" for i, x in enumerate(lines))
    scene = (ctx.get('scene') or {}).get('text') or '不明'
    return [{'role': 'system', 'content': JUDGE_RULES},
            {'role': 'user', 'content': f'当前情景：{scene}\n刚听到（旧到新）：\n{heard}\n\n候选：\n{cands}'}]


def check_messages(ctx_now, now_ms, ranked, since_ms):
    """ranked: best first, each {text}; only speech heard after since_ms (the round's written_at) is shown."""
    heard = '\n'.join(_heard(ctx_now, since_ms)) or '（没有新的话）'
    cands = '\n'.join(f"{i + 1}. {x['text']}" for i, x in enumerate(ranked))
    return [{'role': 'system', 'content': CHECK_RULES},
            {'role': 'user', 'content': f'写完之后新听到的话（旧到新）：\n{heard}\n\n事先写好的句子（好的在前）：\n{cands}'}]
