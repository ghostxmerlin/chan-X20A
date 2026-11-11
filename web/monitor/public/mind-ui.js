// 情景 page: minute timeline from /api/mind/* (rules only in phase 0; see server/mind/README.md).
const COLOURS={seen:'#2e9d4f',just_left:'#8fd19e',likely_present:'#3b7dd8',unknown:'#9aa0a6',likely_absent:'#e8912d',sensors_off:'#555555'};
const LABELS={seen:'看到',just_left:'刚离开',likely_present:'很可能在',unknown:'不知道',likely_absent:'很可能不在',sensors_off:'感官全关'};
export const stateColour=s=>COLOURS[s]||COLOURS.unknown;
export const stateLabel=s=>LABELS[s]||escapeHtml(s);
export const escapeHtml=t=>String(t??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
export const coverageTitle=c=>`听觉 ${c.hearing_s} 秒 · 视觉 ${c.vision_s} 秒 · M12 ${c.m12_s} 秒${c.estimated?'（估计）':''}`;
export function whoLabel(w){
 if(w.kind==='known')return `认出：${w.name}`;
 if(w.kind==='unsure')return `不确定（最像 ${w.best} ${Number(w.score??0).toFixed(2)}）`;
 if(w.kind==='self')return 'hello 自己';
 return '不认识';
}
const hhmm=ms=>new Date(ms).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false});
const hhmmss=ms=>new Date(ms).toLocaleTimeString('zh-CN',{hour12:false});
async function getJson(path){
 const r=await fetch('/api/mind'+path,{signal:AbortSignal.timeout(10000)});
 if(!r.ok)throw new Error(await r.text());
 return r.json();
}
function bars(c){
 const w=s=>Math.min(100,Math.round(s/60*100));
 return `<div class="bars${c.estimated?' est':''}" title="${escapeHtml(coverageTitle(c))}"><div class="bar h"><i data-w="${w(c.hearing_s)}"></i></div><div class="bar v"><i data-w="${w(c.vision_s)}"></i></div><div class="bar m"><i data-w="${w(c.m12_s)}"></i></div></div>`;
}
function rowHtml(r){
 return `<div class="row" data-minute="${r.minute}"><span class="t">${hhmm(r.minute)}</span>${bars(r.coverage)}<span class="state" data-c="${stateColour(r.owner_state)}">${stateLabel(r.owner_state)}</span><span class="summary">${escapeHtml(r.summary)}</span></div>`;
}
const li=items=>items.length?`<ul>${items.join('')}</ul>`:'<p class="alt">无</p>';
export function detailHtml(d){
 const p=d.package,s=d.strong,code=c=>`<span class="code">${escapeHtml(c)}</span>`;
 const speech=p.speech.map(x=>`<li>${code(x.code)}${hhmmss(x.at)} 「${escapeHtml(x.text)}」 · ${escapeHtml(whoLabel(x.who))}${x.loud_db!=null?` · 响度 ${x.loud_db}`:''}${x.tingwu.map(t=>`<div class="alt">${code(t.code)}听悟：「${escapeHtml(t.text)}」</div>`).join('')}</li>`);
 const faces=p.faces.map(f=>`<li>${code(f.code)}脸 #${f.face} ${escapeHtml(f.name||'未认出')} · ${hhmmss(f.from)}–${f.open?'仍在':hhmmss(f.to)}${f.closed_by&&f.closed_by!=='lost'?`（${escapeHtml(f.closed_by)}）`:''}${f.gender?` · ${f.gender==='m'?'男':'女'}`:''}${f.age_group?` · ${escapeHtml(f.age_group)}`:''}</li>`);
 const o=s.owner,ov=s.owner_voice,of=s.owner_face;
 return `<div class="detail">
 <h3>主人：<span class="state" data-c="${stateColour(o.state)}">${stateLabel(o.state)}</span> ${escapeHtml(o.why)} ${o.codes.map(code).join('')}</h3>
 <p>${escapeHtml(coverageTitle(s.coverage))} · ${escapeHtml(s.time.label)}${s.time.work_hours?' · 工作时段':''} · 地点：${escapeHtml(s.place.label||'未知（还没有 Wi-Fi 数据）')}</p>
 <p>主人的脸最后：${of.last_ms?hhmmss(of.last_ms):'—'} · 主人的声音最后：认出 ${ov.known_last_ms?hhmmss(ov.known_last_ms):'—'}，很可能 ${ov.likely_last_ms?hhmmss(ov.likely_last_ms):'—'} · 旁人：${escapeHtml(s.others.known.join('、')||'—')}，未认出的脸 ${s.others.unknown_faces}，${s.others.unknown_voices?'有':'没有'}不认识的声音</p>
 <h3>说的话（${p.speech.length}）</h3>${li(speech)}
 ${p.tingwu_only.length?`<h3>只有听悟听到（${p.tingwu_only.length}）</h3>${li(p.tingwu_only.map(t=>`<li>${code(t.code)}${hhmmss(t.at)} 「${escapeHtml(t.text)}」</li>`))}`:''}
 ${p.self_speech.length?`<h3>hello 自己出的声（${p.self_speech.length}）</h3>${li(p.self_speech.map(x=>`<li class="self">${code(x.code)}「${escapeHtml(x.text)}」</li>`))}`:''}
 <h3>声音</h3>${li(p.sounds.map(g=>`<li>${escapeHtml(g.label||g.kind)} × ${g.count}${g.max_loud_db!=null?`（最响 ${g.max_loud_db}）`:''}</li>`))}
 <h3>脸（${p.faces.length}） · 人形 ${p.persons.seconds} 秒 / ${p.persons.ids} 个编号</h3>${li(faces)}
 ${s.contradictions.length?`<h3>矛盾</h3>${li(s.contradictions.map(c=>`<li>${escapeHtml(c.kind)} ${c.codes.map(code).join('')}</li>`))}`:''}
 ${p.warnings.length?`<h3>告警</h3>${li(p.warnings.map(w=>`<li>${escapeHtml(w.kind)} ${code(w.code)}${w.seconds} 秒</li>`))}`:''}
 <p class="alt">第 ${d.version} 版（共 ${d.versions.length} 版）· 指纹 ${escapeHtml(d.fingerprint)}</p></div>`;
}
// The monitor's CSP blocks style attributes in markup; set widths and colours through the DOM.
export function paint(root){
 root.querySelectorAll('[data-w]').forEach(e=>{e.style.width=e.dataset.w+'%';});
 root.querySelectorAll('[data-c]').forEach(e=>{e.style.background=e.dataset.c;});
}

export const DIM_ORDER=['people','activity','sound_scene','vision','self','time_place','topic','contradiction'];
const DIM_LABEL={people:'人',activity:'在做什么',sound_scene:'声音环境',vision:'画面',self:'hello 自己',time_place:'时间地点',topic:'话题',contradiction:'矛盾',other:'其他'};
const BASIS={seen:'看到',heard:'听到',inferred:'推断',assumed:'猜测'};
const CONF={high:'高',mid:'中',low:'低'};
const CLASS_COLOURS={present:'#2e9d4f',absent:'#e8912d',unknown:'#9aa0a6',off:'#555555',gap:'#333338'};
const CLASS_LABEL={present:'主人在',absent:'主人不在',unknown:'不确定',off:'感官关',gap:'mind 没在运行'};
// eye result for one minute (plan 2026-11-11-eye): who is in view, doing what.
export function eyeHtml(d){
 if(!d)return '<p class="alt">还没有识别结果</p>';
 const t=d.minute?`${hhmm(d.minute)} 的画面`:'';
 if(d.source==='failed')return `<p class="alt">${t} · 识别失败，退避中${d.error?`（${escapeHtml(String(d.error).slice(0,80))}）`:''}</p>`;
 if(d.source==='budget')return `<p class="alt">${t} · 今天的预算用完，没有识别</p>`;
 if(d.source==='backoff')return `<p class="alt">${t} · 上次失败，退避中</p>`;
 const r=d.result||{};
 const people=(r.people||[]).map(p=>`<li>${escapeHtml([p.who,p.where,p.doing].filter(Boolean).join(' · '))}</li>`).join('');
 return `<div class="eye"><p class="meta">${t} · ${Number(r.people_count??0)} 人 · 把握 ${escapeHtml(CONF[r.confidence]||r.confidence||'')} · ${(d.frames||[]).length} 张图</p>`+
  (people?`<ul>${people}</ul>`:'')+`<p>${escapeHtml(r.activity||'')}</p><p class="alt">环境：${escapeHtml(r.scene||'—')} · 变化：${escapeHtml(r.changes||'—')}</p></div>`;
}
export function frameCaption(f){
 if(!f)return '还没有收到画面';
 return `${hhmmss(f.at)} 收到 · 变化 ${f.diff===255?'首张':f.diff+' 格'} · 今天 ${f.today} 张${f.missing?' · 文件缺失':''}`;
}
export const placeLabel=p=>p?.label?`${p.label}（${p.source==='owner_report'?`你报告，${hhmm(p.at)}`:'来自 Wi-Fi'}）`:'未知（还没有报告）';
export const basisLabel=b=>BASIS[b]||escapeHtml(b||'');
export function groupEntries(entries){
 const out={};
 for(const d of [...DIM_ORDER,'other'])for(const e of entries||[]){const k=DIM_ORDER.includes(e.dimension)?e.dimension:'other';if(k===d)(out[d]??=[]).push(e);}
 return out;
}
export function sourceLabel(rec){
 if(!rec)return '';
 if(rec.source==='model')return rec.reason==='revised'?`模型 · 修订版（第 ${Number(rec.version)||'?'} 版）`:'模型';   // v3: late records revise a minute
 if(rec.source==='carried')return `沿用自 ${hhmm(rec.carried_from)}`;
 if(rec.source==='failed')return rec.carried_from?`调用失败，沿用自 ${hhmm(rec.carried_from)}`:'调用失败';
 return '仅规则';
}
const confLabel=c=>CONF[c]||escapeHtml(c||'');
const answer=(label,a)=>a?`<p class="answer"><b>${label}</b> ${escapeHtml(a.text)} <span class="conf">把握${confLabel(a.confidence)}</span></p>`:'';
function entriesHtml(entries){
 const g=groupEntries(entries);
 return Object.entries(g).map(([d,list])=>`<div class="dim"><h4>${DIM_LABEL[d]}</h4><ul>${list.map(e=>`<li${e.stale?' class="stale"':''}>${escapeHtml(e.text)}${e.stale?' <span class="alt">（陈旧）</span>':''} <span class="basis">${basisLabel(e.basis)}·${confLabel(e.confidence)}</span> ${(e.evidence||[]).map(c=>`<span class="code">${escapeHtml(c)}</span>`).join('')}</li>`).join('')}</ul></div>`).join('');
}
export function nowHtml(now){
 const rec=now?.scene,sc=rec?.scene,st=now?.strong;
 if(!rec)return '<p class="alt">还没有情景（模型还没跑过）</p>';
 const owner=st?`<span class="state" data-c="${stateColour(st.owner.state)}">${stateLabel(st.owner.state)}</span> <span class="alt">${escapeHtml(st.owner.why)}</span>`:'';
 const seg=now.segment?`<p class="alt">当前段：${hhmm(now.segment.start)} 起 · ${escapeHtml(now.segment.summary?.title||CLASS_LABEL[now.segment.cls]||'')}</p>`:'';
 if(!sc)return `<div class="now"><p class="meta">${hhmm(rec.minute)} · ${sourceLabel(rec)}</p><p>${owner}</p>${seg}<p class="alt">这一分钟只有规则结果</p></div>`;
 return `<div class="now"><p class="meta">${hhmm(rec.minute)} · ${escapeHtml(sourceLabel(rec))} · ${escapeHtml(sc.summary)}</p>
 ${answer('我是谁',sc.who_am_i)}${answer('我在哪',sc.where_am_i)}${answer('旁边的人在干什么',sc.people_doing)}
 <p>规则：${owner}${st?` · ${escapeHtml(coverageTitle(st.coverage))} · 地点：${escapeHtml(placeLabel(st.place))}`:''}</p>${seg}
 <div class="dims">${entriesHtml(sc.entries)}</div>
 ${sc.want_to_know?`<p class="alt">想确认：${escapeHtml(sc.want_to_know)}</p>`:''}</div>`;
}
export function segmentsHtml(segs){
 const list=(segs||[]).slice().reverse();
 if(!list.length)return '<p class="alt">今天还没有情景段</p>';
 return list.map(g=>`<div class="seg" data-start="${escapeHtml(g.start)}"><span class="dot" data-c="${CLASS_COLOURS[g.cls]||CLASS_COLOURS.unknown}"></span><span class="t">${escapeHtml(hhmm(g.start))}–${g.end?escapeHtml(hhmm(g.end)):'现在'}</span><b>${escapeHtml(g.summary?.title||CLASS_LABEL[g.cls]||g.cls)}</b><span class="alt">${escapeHtml(g.summary?.text||'')}</span></div>`).join('');
}
export function sceneHtml(rec,run){
 if(!rec)return '';
 const sc=rec.scene,c=rec.checks||{};
 const checks=rec.source==='model'?`<p class="alt">校验：无效代号 ${Number(c.invalid_refs)||0} · 降级 ${Number(c.downgraded)||0} · 拦下的提高 ${Number(c.raise_blocked)||0} · 规则冲突 ${c.rule_conflict?'有':'无'} · 只凭语言 ${Math.round((Number(c.language_only)||0)*100)}%</p>`:'';
 const num=v=>escapeHtml(v??'—');
 const r=run?`<p class="alt">${escapeHtml(run.model)} · ${num(run.latency_ms)} ms · 输入 ${num(run.usage?.prompt_tokens)} / 输出 ${num(run.usage?.completion_tokens)} token · $${Number(run.cost_usd||0).toFixed(5)}${run.error?` · 错误：${escapeHtml(run.error)}`:''}</p><details><summary>完整提示和回复</summary><pre>${escapeHtml(JSON.stringify(run.request?.messages,null,1))}</pre><pre>${escapeHtml(JSON.stringify(run.response,null,1))}</pre></details>`:'';
 const body=sc?`${answer('我是谁',sc.who_am_i)}${answer('我在哪',sc.where_am_i)}${answer('旁边的人在干什么',sc.people_doing)}<div class="dims">${entriesHtml(sc.entries)}</div>`:'<p class="alt">没有模型情景</p>';
 return `<div class="scene"><p class="meta">${escapeHtml(rec.lane==='rerun'?'重跑':'正式')} · ${escapeHtml(sourceLabel(rec))} · ${escapeHtml(rec.reason||'')} · ${escapeHtml(rec.prompt||'')} · 第 ${escapeHtml(rec.version)} 版</p>${body}${checks}${r}</div>`;
}
const list=v=>String(v||'').split(/[,，\s]+/).map(x=>x.trim()).filter(Boolean);
export function formFromSettings(s){
 return {self_facts:s.self_facts||'',place:s.place?.default||'',owner_display:s.owner?.display||'',owner_aliases:(s.owner?.aliases||[]).join(','),owner_voice:[...(s.owner?.voice_names||[])],owner_face:[...(s.owner?.face_names||[])],
  people:(s.people||[]).map(p=>({display:p.display,voice:(p.voice_names||[]).join(','),face:(p.face_names||[]).join(',')})),
  work_days:(s.work?.days||[]).join(','),work_start:s.work?.start||'09:00',work_end:s.work?.end||'19:00',
  speech_text:!!s.sources?.speech_text,tingwu:!!s.sources?.tingwu,sounds:!!s.sources?.sounds,vision:!!s.sources?.vision,
  fusion_enabled:!!s.fusion?.enabled,model:s.fusion?.model||'gpt-6-luna',budget:s.budget_usd_per_day??20,persona:s.persona||''};
}
export function settingsFromForm(v,base){
 const s=JSON.parse(JSON.stringify(base||{}));
 s.self_facts=v.self_facts;s.place={...(s.place||{}),default:v.place};s.persona=v.persona;
 s.owner={...(s.owner||{}),display:v.owner_display,aliases:list(v.owner_aliases),voice_names:[...v.owner_voice],face_names:[...v.owner_face]};
 s.people=v.people.filter(p=>p.display.trim()).map(p=>({display:p.display.trim(),voice_names:list(p.voice),face_names:list(p.face)}));
 s.work={...(s.work||{}),days:list(v.work_days).map(Number),start:v.work_start,end:v.work_end};
 s.sources={...(s.sources||{}),speech_text:v.speech_text,tingwu:v.tingwu,sounds:v.sounds,vision:v.vision};
 s.fusion={...(s.fusion||{}),enabled:v.fusion_enabled,model:v.model};
 s.budget_usd_per_day=Number(v.budget);
 return s;
}
const chk=(name,on,label)=>`<label class="chk"><input type="checkbox" name="${name}"${on?' checked':''}> ${escapeHtml(label)}</label>`;
export function settingsHtml(v,names){
 const pick=(field,current,pool)=>[...new Set([...current,...pool])].map(n=>`<label class="chk"><input type="checkbox" name="${field}" value="${escapeHtml(n)}"${current.includes(n)?' checked':''}> ${escapeHtml(n)}</label>`).join('')||'<span class="alt">（库里还没有名字）</span>';
 const people=[...v.people,{display:'',voice:'',face:''}].map((p,i)=>`<div class="person" data-i="${i}"><input name="p_display" placeholder="显示名" value="${escapeHtml(p.display)}"><input name="p_voice" placeholder="声纹名，逗号分隔" value="${escapeHtml(p.voice)}"><input name="p_face" placeholder="人脸名，逗号分隔" value="${escapeHtml(p.face)}"></div>`).join('');
 return `<label>关于 hello（自我事实）<textarea name="self_facts" rows="3">${escapeHtml(v.self_facts)}</textarea></label>
 <label>默认地点<input name="place" value="${escapeHtml(v.place)}"></label>
 <fieldset><legend>主人</legend><label>显示名（没有称呼时用）<input name="owner_display" value="${escapeHtml(v.owner_display)}"></label>
 <label>称呼（逗号分隔，每天随机用一个）<input name="owner_aliases" value="${escapeHtml(v.owner_aliases??'')}"></label>
 <div>声纹名：${pick('owner_voice',v.owner_voice,names?.voice||[])}</div><div>人脸名：${pick('owner_face',v.owner_face,names?.face||[])}</div></fieldset>
 <fieldset><legend>认识的人（留空的行不保存）</legend>${people}</fieldset>
 <fieldset><legend>工作时段</legend><label>工作日（1=周一）<input name="work_days" value="${escapeHtml(v.work_days)}"></label><label>从<input name="work_start" value="${escapeHtml(v.work_start)}"></label><label>到<input name="work_end" value="${escapeHtml(v.work_end)}"></label></fieldset>
 <fieldset><legend>交给模型的信息</legend>${chk('speech_text',v.speech_text,'话的内容')}${chk('tingwu',v.tingwu,'听悟')}${chk('sounds',v.sounds,'声音事件')}${chk('vision',v.vision,'视觉')}</fieldset>
 <fieldset><legend>模型</legend>${chk('fusion_enabled',v.fusion_enabled,'每分钟融合')}<label>模型<select name="model">${['gpt-6-luna','gpt-6.1-terra'].map(m=>`<option${m===v.model?' selected':''}>${m}</option>`).join('')}</select></label><label>每日花费上限（美元，防失控）<input name="budget" value="${escapeHtml(v.budget)}"></label></fieldset>
 <label>性格与语气（现在只存不用）<textarea name="persona" rows="2">${escapeHtml(v.persona)}</textarea></label>
 <label>修改说明<input name="note" placeholder="比如：补上同事A"></label>`;
}
function readForm(root){
 const val=n=>root.querySelector(`[name="${n}"]`)?.value??'',on=n=>!!root.querySelector(`[name="${n}"]`)?.checked;
 const checked=n=>[...root.querySelectorAll(`[name="${n}"]`)].filter(e=>e.checked).map(e=>e.value);
 return {self_facts:val('self_facts'),place:val('place'),owner_display:val('owner_display'),owner_aliases:val('owner_aliases'),owner_voice:checked('owner_voice'),owner_face:checked('owner_face'),
  people:[...root.querySelectorAll('.person')].map(p=>({display:p.querySelector('[name=p_display]').value,voice:p.querySelector('[name=p_voice]').value,face:p.querySelector('[name=p_face]').value})),
  work_days:val('work_days'),work_start:val('work_start'),work_end:val('work_end'),speech_text:on('speech_text'),tingwu:on('tingwu'),sounds:on('sounds'),vision:on('vision'),
  fusion_enabled:on('fusion_enabled'),model:val('model'),budget:val('budget'),persona:val('persona'),note:val('note')};
}
async function postJson(path,obj){
 const r=await fetch('/api/mind'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(obj),signal:AbortSignal.timeout(210000)});
 const text=await r.text();if(!r.ok)throw new Error(text);return JSON.parse(text);
}
function statsHtml(st){
 if(!st?.rows?.length)return '<p class="alt">还没有模型调用</p>';
 return `<table><tr><th>日期</th><th>步骤</th><th>模型</th><th>泳道</th><th>调用</th><th>失败</th><th>输入 token</th><th>输出 token</th><th>花费</th></tr>${st.rows.map(r=>`<tr><td>${escapeHtml(r.day)}</td><td>${escapeHtml(r.step)}</td><td>${escapeHtml(r.model)}</td><td>${escapeHtml(r.lane)}</td><td>${Number(r.calls)}</td><td>${Number(r.failures)}</td><td>${Number(r.prompt_tokens)}</td><td>${Number(r.completion_tokens)}</td><td>$${Number(r.cost_usd).toFixed(4)}</td></tr>`).join('')}</table>`;
}
// While a minute is open (labels being typed, a rerun running), the timer must not rebuild the list.
export const refreshPlan=open=>({timeline:open==null,detail:false});
export function start(doc){
 const el=id=>doc.getElementById(id);
 const today=new Date(Date.now()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,10);
 el('mind-date').value=today;
 el('mind-legend').innerHTML=Object.keys(LABELS).map(k=>`<span><i class="dot" data-c="${COLOURS[k]}"></i>${LABELS[k]}</span>`).join('')+'<span>三条小条：听觉 / 视觉 / M12 这一分钟工作的秒数</span>';
 paint(el('mind-legend'));
 let open=null,settingsBase=null,prompts={versions:['v3'],models:['gpt-6-luna','gpt-6.1-terra']};
 const range=()=>{const day=el('mind-date').value||today,hours=Number(el('mind-range').value),dayStart=new Date(day+'T00:00:00').getTime(),now=Date.now();
  const to=Math.min(dayStart+86400000,day===today?now:dayStart+86400000);return {day,from:hours===24||day!==today?dayStart:Math.max(dayStart,to-hours*3600000),to,dayStart};};
 async function refresh(){
  const r=range();
  try{
   const [state,now,segs,rows,stats]=await Promise.all([getJson('/state'),getJson('/now'),getJson(`/segments?from=${r.dayStart}&to=${r.to}`),getJson(`/timeline?from=${r.from}&to=${r.to}`),getJson('/stats?days=7')]);
   const lag=state.cursor_minute?Math.round((state.now_ms-state.cursor_minute)/60000):null;
   const err=state.last_error||state.fusion_last_error&&state.fusion_failures?(state.last_error||`模型：${state.fusion_last_error}`):'';
   el('mind-status').textContent=err?`出错：${err}`:`已处理到 ${state.cursor_minute?hhmm(state.cursor_minute):'—'}（落后 ${lag??'—'} 分钟）· 设置第 ${state.settings_version??0} 版`;
   el('mind-status').className=err?'error':'';
   el('mind-stats-line').textContent=`今天模型调用 ${stats.today_calls} 次 · $${(stats.today_cost_usd||0).toFixed(4)}`;
   el('mind-stats').innerHTML=statsHtml(stats);
   if(r.day===today){el('mind-now').innerHTML=nowHtml(now);paint(el('mind-now'));}
   else el('mind-now').innerHTML='<p class="alt">看的是历史日期；“此刻”只显示今天</p>';
   el('mind-segments').innerHTML=segmentsHtml(segs);paint(el('mind-segments'));
   if(refreshPlan(open).timeline){
    el('mind-timeline').innerHTML=rows.slice().reverse().map(rowHtml).join('')||'<p class="alt">这段时间没有记录</p>';
    paint(el('mind-timeline'));
   }
  }catch(e){el('mind-status').textContent='情景服务不可用：'+e.message;el('mind-status').className='error';}
 }
 async function toggle(minute,keep=false){
  const row=doc.querySelector(`.row[data-minute="${minute}"]`);if(!row)return;
  const next=row.nextElementSibling;
  if(next&&next.classList.contains('detail')){if(!keep){next.remove();open=null;}return;}
  doc.querySelectorAll('.detail').forEach(d=>d.remove());
  open=minute;
  try{
   let scene='',pkgVersion=null;
   try{const sc=await getJson(`/scene?t=${minute}`);const rec=sc.versions.at(-1);pkgVersion=rec.package_version;scene=sceneHtml(rec,sc.runs.find(r=>r.run_id===rec.run_id));}catch{scene='<p class="alt">这一分钟没有模型情景</p>';}
   // the package version the scene was built from, so evidence codes point at the right sentences
   const pkg=await getJson(`/minute?t=${minute}${pkgVersion?`&version=${pkgVersion}`:''}`);
   const tools=`<div class="tools" data-minute="${minute}">
    <span>这一分钟：</span>${['right','wrong','unsure'].map(v=>`<button data-verdict="${v}">${{right:'对',wrong:'错',unsure:'说不清'}[v]}</button>`).join('')}
    <select name="field">${[['owner','主人在不在'],['people','人'],['activity','在做什么'],['topic','话题'],['summary','摘要'],['other','其他']].map(([k,t])=>`<option value="${k}">${t}</option>`).join('')}</select>
    <input name="correct" placeholder="正确的是"><input name="note" placeholder="备注"><span class="label-msg alt"></span>
    <span class="sep">｜重跑：</span><select name="prompt">${prompts.versions.map(v=>`<option>${escapeHtml(v)}</option>`).join('')}</select><select name="model">${prompts.models.map(v=>`<option>${escapeHtml(v)}</option>`).join('')}</select><button data-rerun="1">重跑这一分钟</button></div>
    <div class="compare"><div>${scene}</div><div class="rerun-out"></div></div>`;
   row.insertAdjacentHTML('afterend',`<div class="detail">${tools}${detailHtml(pkg)}</div>`);paint(row.nextElementSibling);
  }catch(e){row.insertAdjacentHTML('afterend',`<div class="detail">读取失败：${escapeHtml(e.message)}</div>`);}
 }
 el('mind-timeline').addEventListener('click',async e=>{
  const tools=e.target.closest('.tools');
  if(tools){
   const minute=Number(tools.dataset.minute),q=n=>tools.querySelector(`[name="${n}"]`).value;
   if(e.target.dataset.verdict){
    try{await postJson('/label',{minute,verdict:e.target.dataset.verdict,field:q('field'),correct:q('correct'),note:q('note')});tools.querySelector('.label-msg').textContent='已记下';}
    catch(err){tools.querySelector('.label-msg').textContent='没记上：'+err.message;}
   }
   if(e.target.dataset.rerun){
    const out=tools.parentElement.querySelector('.rerun-out');out.innerHTML='<p class="alt">重跑中，最多一两分钟…</p>';
    try{const rec=await postJson('/rerun',{minute,prompt:q('prompt'),model:q('model')});const sc=await getJson(`/scene?t=${minute}&lane=rerun`);out.innerHTML=sceneHtml(rec,sc.runs.find(r=>r.run_id===rec.run_id));}
    catch(err){out.innerHTML=`<p class="error">重跑失败：${escapeHtml(err.message)}</p>`;}
   }
   return;
  }
  const row=e.target.closest('.row');if(row)toggle(Number(row.dataset.minute));
 });
 el('mind-marks').addEventListener('click',async e=>{
  const kind=e.target.dataset?.mark;if(!kind)return;
  try{const r=await postJson('/mark',{kind});el('mind-mark-msg').textContent=`已记 ${hhmm(r.at)} ${e.target.textContent}`;}
  catch(err){el('mind-mark-msg').textContent='没记上：'+err.message;}
 });
 async function reportPlace(label){
  if(!label.trim())return;
  try{const r=await postJson('/place',{label});el('mind-place-msg').textContent=`已报告 ${hhmm(r.at)}：${label}（下一分钟起生效）`;}
  catch(err){el('mind-place-msg').textContent='没报上：'+err.message;}
 }
 el('mind-place').addEventListener('click',e=>{
  if(e.target.dataset?.place)reportPlace(e.target.dataset.place);
  if(e.target.id==='mind-place-send')reportPlace(el('mind-place-input').value);
 });
 el('mind-open-settings').addEventListener('click',async()=>{
  try{
   const [cur,names,p]=await Promise.all([getJson('/settings'),getJson('/names'),getJson('/prompts')]);
   settingsBase=cur.settings;prompts=p;
   el('settings-form').innerHTML=settingsHtml(formFromSettings(cur.settings),names);
   el('settings-msg').textContent=`当前第 ${cur.version} 版`;el('settings-dialog').showModal();
  }catch(e){el('mind-status').textContent='读取设置失败：'+e.message;}
 });
 el('settings-cancel').addEventListener('click',()=>el('settings-dialog').close());
 el('settings-save').addEventListener('click',async()=>{
  const v=readForm(el('settings-form'));
  try{const r=await postJson('/settings',{settings:settingsFromForm(v,settingsBase),note:v.note});el('settings-msg').textContent=`已保存为第 ${r.version} 版`;}
  catch(e){el('settings-msg').textContent='没保存：'+e.message;}
 });
 el('mind-date').addEventListener('change',()=>{open=null;refresh();});
 el('mind-range').addEventListener('change',refresh);
 getJson('/prompts').then(p=>{prompts=p;},()=>{});
 // 服务器收到的最新画面（第 2 步）：JPEG arrives as base64 JSON; shown through a blob: URL (CSP has no data:).
 let frameURL=null;
 async function refreshFrame(){
  let f=null;
  try{const r=await fetch('/api/mind/frames/latest',{signal:AbortSignal.timeout(10000)});f=r.ok?await r.json():null;}catch{f=null;}
  el('mind-frame-cap').textContent=frameCaption(f);
  let e=null;
  try{const r=await fetch('/api/mind/eye/latest',{signal:AbortSignal.timeout(10000)});e=r.ok?await r.json():null;}catch{e=null;}
  el('mind-eye').innerHTML=eyeHtml(e);
  const img=el('mind-frame-img');
  if(f?.jpeg_b64){
   const bytes=Uint8Array.from(atob(f.jpeg_b64),c=>c.charCodeAt(0));
   const url=URL.createObjectURL(new Blob([bytes],{type:'image/jpeg'}));
   img.src=url;img.hidden=false;
   if(frameURL)URL.revokeObjectURL(frameURL);
   frameURL=url;
  }
 }
 refreshFrame();setInterval(refreshFrame,15000);
 refresh();setInterval(refresh,10000);
}
if(typeof document!=='undefined'&&document.getElementById('mind-timeline'))start(document);
