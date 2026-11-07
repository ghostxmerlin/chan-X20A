"""Read the voice catalog and propose visual mappings; never mutate voice data."""
from __future__ import annotations
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
SOURCE = Path('/Users/ghostxmerlin/soul/voice/outputs/20261106-stackchan-300/stackchan_candidates_300.json')
THEME_SOURCE = Path('/Users/ghostxmerlin/soul/voice/outputs/20261106-stackchan-catalog/_build/expand300.mjs')
STATES = {
    'idle':'待机', 'happy':'开心', 'hurt':'扎心', 'sad':'伤心', 'angry':'愤怒',
    'affectionate':'亲密', 'laughing':'嘲笑', 'goofy':'搞怪', 'sarcastic':'嘲讽', 'helpless':'无奈',
    'greeting':'问候', 'grateful':'感谢', 'apologetic':'歉意', 'comforting':'安慰',
    'encouraging':'鼓励', 'determined':'坚定／认真', 'refusing':'拒绝', 'pleading':'请求',
    'relieved':'释然', 'farewell':'告别', 'surprised':'惊讶', 'confused':'困惑',
    'thinking':'思考', 'expectant':'期待', 'curious':'好奇', 'shy':'害羞',
    'worried':'担忧', 'scared':'害怕', 'sleepy':'疲惫', 'proud':'得意',
}
THEMES = {
    '关心／提醒':['worried','comforting','determined'],
    '决心／行动':['determined','encouraging','proud'],
    '吐槽／玩笑':['sarcastic','goofy','laughing','helpless'],
    '告别／思念':['farewell','sad','affectionate'],
    '和解／接纳':['relieved','comforting','affectionate'],
    '喜爱／称赞':['affectionate','happy','proud'],
    '安慰／陪伴':['comforting','affectionate','encouraging'],
    '庆祝／祝福':['happy','encouraging','expectant'],
    '惊讶／困惑':['surprised','confused','curious'],
    '感慨／倾诉':['sad','thinking','helpless','hurt'],
    '愿望／计划':['expectant','thinking','determined'],
    '拒绝／制止':['refusing','determined','angry'],
    '确认／回应':['determined','proud','happy'],
    '解释／澄清':['thinking','determined','curious'],
    '请求／求助':['pleading','worried','shy'],
    '质疑／反对':['confused','refusing','sarcastic'],
    '道歉／感谢':['apologetic','grateful','shy'],
    '邀请／协作':['greeting','expectant','determined'],
    '问候／闲聊':['greeting','curious','goofy'],
    '鼓励／支持':['encouraging','determined','comforting'],
}
# Proposed aliases, not a claimed voice-database enum. Order resolves compound tags.
EMOTIONS = [
    ('grateful',r'感激|感谢'), ('apologetic',r'歉疚|歉意|抱歉'),
    ('hurt',r'委屈|挫败|崩溃|受伤|扎心'),
    ('sad',r'悲伤|悲痛|低落|失落|感伤|沉重|难过|怀念|怀旧|惦念|隐忍'),
    ('angry',r'愤怒|生气|恼火|不满|怨气|不服气|不服|挑衅|不甘|强硬|严厉'),
    ('scared',r'恐惧|害怕|惊恐|惊慌|慌乱'),
    ('worried',r'焦急|担忧|不安|紧张|顾虑|急迫|急切|紧迫|关切|警惕|警觉|谨慎'),
    ('shy',r'窘迫|拘谨|害羞|回避|尴尬'),
    ('sleepy',r'疲惫|懒散|虚弱|困倦|困乏|退缩'),
    ('helpless',r'无奈|自嘲'),
    ('sarcastic',r'讽刺|嫌弃|吐槽|挖苦|冷嘲|居高临下'),
    ('laughing',r'嘲笑|讥笑'),
    ('goofy',r'俏皮|调皮|调侃|戏谑|幽默|诙谐|好笑|夸张|装腔作势'),
    ('surprised',r'惊讶|难以置信|惊喜'),
    ('confused',r'困惑|迷茫|矛盾|质疑'),
    ('relieved',r'释然|如释重负|宽松'),
    ('farewell',r'不舍|惜别'),
    ('affectionate',r'亲近|依恋|温柔|温暖|亲切|撒娇|温厚'),
    ('comforting',r'体谅|照应|信任'),
    ('expectant',r'期待|跃跃欲试'),
    ('happy',r'兴奋|激动|满足|开心'),
    ('curious',r'好奇|试探'),
    ('proud',r'得意|自信|笃定|胸有成竹|理直气壮|豪气'),
    ('determined',r'坚定|坚决|果断|坚毅|认真|郑重|严肃|专注|干练|坚持|决绝|执着|稳重|干脆|公事公办|一本正经|自我约束'),
    ('thinking',r'沉思|神秘|意味深长|意味复杂|克制'),
    ('greeting',r'礼貌|客气|友好|随和|温和|恳切|真诚|诚恳'),
    ('idle',r'平静|冷静|从容|淡然|淡定|平常|自然|轻松|随意|沉静|云淡风轻'),
]
INTENTS = [
    ('apologetic',r'道歉|歉疚'), ('grateful',r'感谢'),
    ('farewell',r'告别|思念|感怀|纪念|想家|想起故人|惦念|被记得|告知离开'),
    ('relieved',r'和解|接纳|劝和|尊重选择|放人自由'),
    ('comforting',r'安慰|陪伴|共情|安抚|挽留|决定留下|珍惜生命'),
    ('happy',r'庆祝'), ('expectant',r'祝愿|祝福|愿望|计划|志向|等待|重逢'),
    ('encouraging',r'鼓励|支持|失败后|信任|救援承诺|启发'),
    ('pleading',r'请求|求助|托付|协商|问路|解决付款|寻求搭车|要求保证'),
    ('greeting',r'邀请|邀约|问候|闲聊|自我介绍|招呼|欢迎|认识同伴|初次交谈|起名|呼叫|恢复对话'),
    ('worried',r'关心|警告|提醒代价|协同避险'),
    ('refusing',r'拒绝|制止|劝阻|反对冒险|暂缓见面|反对|反驳'),
    ('confused',r'困惑|质疑|反问|追问|认人疑问'),
    ('curious',r'询问|了解'),
    ('thinking',r'解释|澄清|纠正|坦白|揭示|说明来意|掩饰|思考|领悟'),
    ('proud',r'识别成功|答对|自豪|认可'),
    ('affectionate',r'喜爱|喜好|爱意|感情承诺|维护感情选择|赞美|称赞|夸赞|表扬|偏好'),
    ('sarcastic',r'讽刺'), ('helpless',r'自嘲|抱怨|后悔|无奈'),
    ('goofy',r'调侃|玩笑|吐槽'), ('shy',r'尴尬'),
    ('sleepy',r'休息|想歇'), ('surprised',r'惊讶|惊喜|吸引注意'),
    ('sad',r'失望|遗憾|悲伤'),
    ('determined',r'决心|行动|任务|承诺|责任|担当|承担|原则|立场|决定|判断|出发|重启|坚持|公平|挑战|本领|指令|催促|改变|确认|回应|报告|通知|告知|报平安|提醒|指导'),
]
PLACEHOLDERS = {'依场景试听确定','待试听判断'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def matches(text, rules):
    return [{'animation':state,'matched_text':match.group(0)}
            for state,pattern in rules if (match:=re.search(pattern,text))]


def propose(candidate):
    raw_emotion=candidate['emotion']
    emotion_text=raw_emotion.replace('（待试听）','')
    emotion_matches=[] if raw_emotion in PLACEHOLDERS else matches(emotion_text,EMOTIONS)
    intent_matches=matches(candidate['intent'],INTENTS)
    sequence=[m['animation'] for m in emotion_matches+intent_matches]+THEMES[candidate['theme']]
    unique=list(dict.fromkeys(sequence))
    basis='emotion_keyword' if emotion_matches else ('intent_keyword' if intent_matches else 'theme_fallback')
    return {**{k:candidate[k] for k in ('id','theme','emotion','intent')},
            'primary_animation':unique[0], 'alternates':unique[1:],
            'mapping_basis':{'strategy':basis,'emotion_matches':emotion_matches,'intent_matches':intent_matches,
                             'theme_fallback':THEMES[candidate['theme']], 'status':'proposed_visual_mapping_not_voice_schema'},
            'emotion_unspecified':raw_emotion in PLACEHOLDERS,
            'needs_audio_review':True,
            'source_verification_status':candidate['verification_status']}


def build():
    source_bytes=SOURCE.read_bytes()
    data=json.loads(source_bytes)
    candidates=data['candidates']
    mappings=[propose(c) for c in candidates]
    expected_ids={c['id'] for c in candidates}
    assert len(mappings)==len(expected_ids)==300
    assert {m['id'] for m in mappings}==expected_ids
    assert set(THEMES)=={c['theme'] for c in candidates}
    assert len(STATES)==30 and len(THEMES)==20
    assert all(m['primary_animation'] in STATES and all(a in STATES for a in m['alternates']) for m in mappings)
    assert all(m['mapping_basis']['strategy'] and m['needs_audio_review'] for m in mappings)
    assert sum(m['emotion_unspecified'] for m in mappings)==72
    assert SOURCE.read_bytes()==source_bytes
    metadata={
        'source_path':str(SOURCE), 'source_sha256':hashlib.sha256(source_bytes).hexdigest(),
        'source_modified_utc':datetime.fromtimestamp(SOURCE.stat().st_mtime,timezone.utc).isoformat(),
        'snapshot_created_utc':datetime.now(timezone.utc).isoformat(),
        'schema_version':data['schema_version'], 'source_as_of':data['as_of'], 'edition':data['edition'],
        'theme_policy':data['theme_policy'], 'audio_acquired':data['audio_acquired'],
        'counts':{'candidates':len(candidates),'themes':len(THEMES),
                  'distinct_intent_strings':len({c['intent'] for c in candidates}),
                  'distinct_emotion_strings':len({c['emotion'] for c in candidates}),
                  'emotion_unspecified':sum(m['emotion_unspecified'] for m in mappings),
                  'named_emotion_with_pending_annotation':sum('待试听' in c['emotion'] and c['emotion']!='待试听判断' for c in candidates)},
        'theme_rule_source':{'path':str(THEME_SOURCE),'sha256':sha(THEME_SOURCE),'lines':[20,45]},
        'evidence_lines':{'source_counts':[2,70],'theme_policy':9873},
        'interpretation':'20 flat editorial expression themes, free-text intent and emotion. No formal emotion enum, alias table, numeric intensity or database file found.',
    }
    themes=[{'theme':theme,'candidate_count':sum(c['theme']==theme for c in candidates),
             'proposed_animations':animations, 'source_rule_line':next(i for i,line in enumerate(THEME_SOURCE.read_text().splitlines(),1) if "['"+theme+"'" in line)}
            for theme,animations in THEMES.items()]
    raw_emotions=Counter(c['emotion'] for c in candidates)
    output={'status':'proposed_animation_mapping','states':STATES,'source_metadata':'source-snapshot.json',
            'policy':'Raw source fields remain unchanged. Keyword and theme choices are editorial visual mappings. Every candidate requires audio review; placeholder labels are not verified emotions.',
            'candidates':mappings}
    report={'ok':True,'source_unchanged':True,'candidate_count':len(mappings),'exactly_once_ids':True,
            'allowed_animation_states':len(STATES),'theme_count':len(themes),'unknown_or_empty_mappings':0,
            'placeholder_emotion_count':72,'audio_review_required_count':sum(m['needs_audio_review'] for m in mappings),
            'primary_animation_counts':dict(Counter(m['primary_animation'] for m in mappings)),
            'states_not_primary_for_current_voice_candidates':sorted(set(STATES)-{m['primary_animation'] for m in mappings}),
            'note':'All 300 candidates have proposed lookup coverage; this is not a claim of audio-verified emotional accuracy.'}
    outputs={'source-snapshot.json':metadata,'theme-coverage.json':themes,'candidate-animation-map.json':output,
             'raw-emotion-inventory.json':dict(sorted(raw_emotions.items())), 'coverage-validation.json':report,
             'mapping-rules.json':{'emotion':EMOTIONS,'intent':INTENTS,'theme':THEMES}}
    for name,value in outputs.items():
        (ROOT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    build()
