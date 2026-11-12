#!/usr/bin/env python3
"""Bounded synthetic-case speech replay: no MongoDB writes and no TTS/player imports.

python tools/speech_replay.py --live --env /private/model.env --output /private/replay.json
Default is an offline case preview. --old-roles enables a same-input v1 comparison.
"""
import argparse
import copy
import json
from pathlib import Path
import sys
import time
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
from mind import attention, settings, speech_generation, speak
from mind.memory import fingerprint
from mind.model_calls import run_json


class ReplayStore:
    def __init__(self): self.runs=[]
    def cost_today(self,*_): return sum(r.get('cost_usd') or 0 for r in self.runs)
    def put_run(self,row):
        uid=uuid.uuid4().hex
        self.runs.append({**copy.deepcopy(row),'run_id':uid})
        return uid


def cases(s,now):
    def evidence(i,kind,text,**kw):
        return dict(id=i,kind=kind,text=text,at=now-3000,quality='usable',priority='highest' if kind=='speech' else 'support',refs=[],**kw)
    speech=evidence('s1','speech','客户又加了三个功能，销售还答应明天交，钱也不加。')
    routine=evidence('v1','vision','主人靠着办公椅坐着。',changes='没有变化',confidence='high')
    memory={'memory_id':'m1','kind':'habit','statement':'主人常靠着办公椅坐着','status':'known'}
    rows=[
        ('office_agree','office',[speech,evidence('n1','notes','客户增加三个功能，销售承诺明天交付，不加钱。'),routine],[memory],list(speak.load_roles(Path(__file__).resolve().parents[1]/'config/roles'))),
        ('new_topic','office',[evidence('s1','speech','刚才那个项目先不聊了，中午谁去拿外卖？'),evidence('n1','notes','大家讨论客户改需求和明天交付。')],[],['roast']),
        ('routine_only','office',[routine],[memory],['deadpan']),
        ('visual_change','office',[evidence('v1','vision','水杯倒了，水正流向电脑键盘。',changes='水杯刚翻倒，桌面出现积水',confidence='high')],[],['worst']),
        ('home','home',[evidence('s1','speech','说好你洗碗，吃完你怎么又躺下了？')],[],['comfort']),
        ('uncertain','office',[evidence('s1','speech','客户火箭椅子咕噜',quality_override='uncertain')],[],['worst']),
    ]
    out=[]
    for name,profile,evidences,memories,roles in rows:
        for e in evidences:
            if 'quality_override' in e:e['quality']=e.pop('quality_override')
        ctx={'scope':{'id':profile,'profile':profile,'since':0},'environment':'软件办公室' if profile=='office' else '家里',
             'time':settings.local_label(now,s['tz']),'profile':s['speech']['profiles'][profile],
             'evidence':evidences,'memories':memories,'recent':[],'warnings':[]}
        ctx['fingerprint']=fingerprint(ctx)
        out.append({'case':name,'context':ctx,'roles':roles})
    return out


def run(env,s,roles,old_roles=None):
    store=ReplayStore(); now=int(time.time()*1000); result=[]
    for case in cases(s,now):
        ctx=case['context'];row={**case,'outputs':[]};result.append(row)
        try:
            focus=attention.choose(env,store,s,ctx,now)
            row['attention']=focus
            for role in case['roles']:
                output=speech_generation.generate(env,store,s,roles[role],ctx,focus,now)
                row['outputs'].append({'role':role,**output})
            if old_roles and case['case'] in ('office_agree','home'):
                oldctx={'environment':ctx['environment'],'time':ctx['time'],'doing':None,'summary':None,
                        'eye':' / '.join(e['text'] for e in ctx['evidence'] if e['kind']=='vision'),
                        'conversation':' / '.join(e['text'] for e in ctx['evidence'] if e['kind']=='notes'),
                        'heard':[e['text'] for e in ctx['evidence'] if e['kind']=='speech'],'mine':[]}
                out,_,err=run_json(env,store,s,'say','say-v1-replay',speak.SAY_SCHEMA,
                    speak._messages(old_roles['comfort'],oldctx),now,lane='replay')
                row['old_comfort']={'output':out,'error':err}
        except attention.NoSituation as e:row['skipped']=str(e)
        except Exception as e:row['error']=type(e).__name__+': '+str(e)
    checks={r['case']:not r.get('error') for r in result}
    by={r['case']:r for r in result}
    checks['routine_only']=bool(by['routine_only'].get('skipped'))
    checks['uncertain']=bool(by['uncertain'].get('skipped'))
    checks['new_topic']=by['new_topic'].get('attention',{}).get('evidence_ids')==['s1']
    checks['office_agree']=set(by['office_agree'].get('attention',{}).get('evidence_ids',[]))=={'s1','n1'}
    return {'cases':result,'checks':checks,'runs':store.runs,'cost_usd':store.cost_today(),'no_database_or_playback':True}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--live',action='store_true');p.add_argument('--env',type=Path);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--old-roles',type=Path);a=p.parse_args()
    s=settings.load(None);s['budget_usd_per_day']=0.15
    roles=speak.load_roles(Path(__file__).resolve().parents[1]/'config/roles')
    if a.live:
        if not a.env:p.error('--live requires --env')
        hub=settings.load_env(a.env)
        data=run({'base_url':hub.get('BASE_URL','https://aihubmix.com/v1'),'api_key':hub['API_KEY']},s,roles,speak.load_roles(a.old_roles) if a.old_roles else None)
    else:data={'cases':cases(s,int(time.time()*1000)),'live':False}
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(data,ensure_ascii=False,indent=2));a.output.chmod(0o600)
    print(json.dumps({k:data[k] for k in ('checks','cost_usd','no_database_or_playback','live') if k in data},ensure_ascii=False))
    if a.live and not all(data['checks'].values()):raise SystemExit(1)


if __name__=='__main__':main()
