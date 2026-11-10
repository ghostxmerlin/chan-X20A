// 情景 page: minute timeline from /api/mind/* (rules only in phase 0; see server/mind/README.md).
const COLOURS={seen:'#2e9d4f',just_left:'#8fd19e',likely_present:'#3b7dd8',unknown:'#9aa0a6',likely_absent:'#e8912d',sensors_off:'#555555'};
const LABELS={seen:'看到',just_left:'刚离开',likely_present:'很可能在',unknown:'不知道',likely_absent:'很可能不在',sensors_off:'感官全关'};
export const stateColour=s=>COLOURS[s]||COLOURS.unknown;
export const stateLabel=s=>LABELS[s]||s;
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
export function start(doc){
 const el=id=>doc.getElementById(id);
 const today=new Date(Date.now()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,10);
 el('mind-date').value=today;
 el('mind-legend').innerHTML=Object.keys(LABELS).map(k=>`<span><i class="dot" data-c="${COLOURS[k]}"></i>${LABELS[k]}</span>`).join('')+'<span>三条小条：听觉 / 视觉 / M12 这一分钟工作的秒数</span>';
 paint(el('mind-legend'));
 let open=null;
 async function refresh(){
  const day=el('mind-date').value||today,hours=Number(el('mind-range').value);
  const dayStart=new Date(day+'T00:00:00').getTime(),now=Date.now();
  const to=Math.min(dayStart+86400000,day===today?now:dayStart+86400000);
  const from=hours===24||day!==today?dayStart:Math.max(dayStart,to-hours*3600000);
  try{
   const [state,rows]=await Promise.all([getJson('/state'),getJson(`/timeline?from=${from}&to=${to}`)]);
   const lag=state.cursor_minute?Math.round((state.now_ms-state.cursor_minute)/60000):null;
   el('mind-status').textContent=state.last_error?`出错：${state.last_error}`:`已处理到 ${state.cursor_minute?hhmm(state.cursor_minute):'—'}（落后 ${lag??'—'} 分钟）`;
   el('mind-status').className=state.last_error?'error':'';
   el('mind-timeline').innerHTML=rows.slice().reverse().map(rowHtml).join('')||'<p class="alt">这段时间没有记录</p>';
   paint(el('mind-timeline'));
   if(open)await toggle(open,true);
  }catch(e){el('mind-status').textContent='情景服务不可用：'+e.message;el('mind-status').className='error';}
 }
 async function toggle(minute,keep=false){
  const row=doc.querySelector(`.row[data-minute="${minute}"]`);if(!row)return;
  const next=row.nextElementSibling;
  if(next&&next.classList.contains('detail')){if(!keep){next.remove();open=null;}return;}
  doc.querySelectorAll('.detail').forEach(d=>d.remove());
  open=minute;
  try{row.insertAdjacentHTML('afterend',detailHtml(await getJson(`/minute?t=${minute}`)));paint(row.nextElementSibling);}
  catch(e){row.insertAdjacentHTML('afterend',`<div class="detail">读取失败：${escapeHtml(e.message)}</div>`);}
 }
 el('mind-timeline').addEventListener('click',e=>{const row=e.target.closest('.row');if(row)toggle(Number(row.dataset.minute));});
 el('mind-date').addEventListener('change',()=>{open=null;refresh();});
 el('mind-range').addEventListener('change',refresh);
 refresh();setInterval(refresh,10000);
}
if(typeof document!=='undefined'&&document.getElementById('mind-timeline'))start(document);
