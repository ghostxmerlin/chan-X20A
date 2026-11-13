// 让 hello 说一句 (2026-11-11; on the main page's 台词 panel since the same day): a button per role
// (config/roles), 别说了, 试听音色 (one fixed sentence per soul-tts voice) and today's last lines.
// Everything goes through /api/mind (mind /say, /say/stop, /voice/try, /roles, /voices, /utterances).
const escapeHtml=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const hhmmss=ms=>new Date(ms).toLocaleTimeString('zh-CN',{hour12:false});

export function roundControlsHtml(prefetchOn){
 return `<button data-say-round="1" class="say-round-button">说一轮</button><button data-say-stop="1" class="stop-button">别说了</button>`+
  `<label class="prefetch-switch"><input type="checkbox" data-prefetch="1"${prefetchOn?' checked':''}> 提前写（讨论激烈时开）</label>`;
}
export function roleButtonsHtml(roles){
 return (roles||[]).map(r=>`<button data-role="${escapeHtml(r.id)}" title="${escapeHtml(r.name)}">${escapeHtml(r.button)}</button>`).join('')+
  '<button data-say-stop="1" class="stop-button">别说了</button>';
}
export function voiceButtonsHtml(voices){
 if(!voices||!voices.length)return '<span class="muted">没有音色</span>';
 return voices.map(v=>`<button data-voice="${escapeHtml(v.id)}">${escapeHtml(v.label)}${v.rate&&v.rate!==1?` · ${escapeHtml(String(v.rate))}×`:''}</button>`).join('');
}
const SAY_STATE={sent:'合成中',synthesizing:'合成中',playing:'正在说',done:'说完了',failed:'失败',cancelled:'被打断',unknown:'状态未知'};
export function utterancesHtml(list,roles){
 if(!list||!list.length)return '<p class="note">还没说过话</p>';
 const name={try:'试听',...Object.fromEntries((roles||[]).map(r=>[r.id,r.button]))};
 return '<ul class="utterances">'+list.slice(-5).reverse().map(u=>{
  const extra=[SAY_STATE[u.state]||escapeHtml(u.state||''),u.server_ms!=null?`服务端 ${(Number(u.server_ms)/1000).toFixed(1)} 秒`:'',u.timings_s?.first_accepted!=null?`TTS 起播 ${Number(u.timings_s.first_accepted).toFixed(1)} 秒`:'',
   u.tts_cost_cny!=null?`¥${Number(u.tts_cost_cny).toFixed(4)}`:''].filter(Boolean).join(' · ');
  return `<li><span class="muted">${hhmmss(u.at)}</span> <b>${escapeHtml(name[u.role]||u.role||'')}</b> ${escapeHtml(u.text||'')} <span class="muted">${extra}</span>${u.uid&&u.role!=='try'?` <button data-say-explain="${escapeHtml(u.uid)}" class="inline-button">为什么说这句</button>`:''}</li>`;
 }).join('')+'</ul>';
}
export function sayErrorMessage(e){
 let m=String(e?.message||e),code;
 try{const body=JSON.parse(m);if(typeof body?.error==='string'){m=body.error;code=body.code;}}catch{}
 if(code==='no_situation'||/^暂时没有(可靠|值得说)的新情况/.test(m))return m;
 return m.includes('TTS 模式')?'先把顶上的「嘴」切到 TTS':m.includes('嘴没连上')?'hello 的嘴还没连上（本体 /mouth 未连接）':`没说成：${m}`;
}
async function getJson(path){const r=await fetch('/api/mind'+path,{signal:AbortSignal.timeout(10000)});if(!r.ok)throw new Error(await r.text());return r.json();}
async function postJson(path,obj){
 const r=await fetch('/api/mind'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(obj),signal:AbortSignal.timeout(path==='/say'||path==='/say/round'?180000:30000)});
 const text=await r.text();if(!r.ok)throw new Error(text);return JSON.parse(text);
}
// ---- one-button talk show (spec 2026-11-13-unified-say-button-design.md, section 13) ----
const DIRECTION={comfort:'鼓励',deadpan:'呆呆',roast:'吐槽',sad:'伤心',worst:'最坏',huyu:'上海话'};
const DIALECTS=['普通话','上海话','四川话','武汉话'];
function lineHtml(r,l,marks,played,voices){
 const v=(marks?.ratings||{})[l.key]||0;
 const recast=voices&&voices.length?`<select data-recast="${escapeHtml(l.key)}" aria-label="换副嗓子"><option value="">换嗓子…</option>${
  voices.flatMap(vo=>DIALECTS.map(d=>`<option value="${escapeHtml(vo.id)}|${d}">${escapeHtml(vo.label)} · ${d}</option>`)).join('')}</select>`:'';
 return `<article class="line${played?' played':''}" data-rid="${escapeHtml(r.rid)}" data-line="${escapeHtml(l.key)}">
<p class="line-tag"><b>${escapeHtml(DIRECTION[l.key]||l.key)}</b> <span class="muted">${l.score!=null?`评委 ${escapeHtml(String(l.score))} 分 · `:''}${escapeHtml(l.voice_label||'')} · ${escapeHtml(l.dialect||'')}</span></p>
<p class="line-text">${escapeHtml(l.text)}</p>${l.gloss?`<p class="line-gloss muted">意思：${escapeHtml(l.gloss)}</p>`:''}
<p class="line-actions"><button data-replay="1">重播</button><button data-rate="1" class="${v>0?'on':''}">好${v>0?` ×${v}`:''}</button><button data-rate="-1" class="${v<0?'on bad':'bad'}">坏${v<0?` ×${-v}`:''}</button>${recast}</p></article>`;
}
export function roundCardHtml(r,marks,voices){
 if(!r)return '';
 const usable=(r.lines||[]).filter(l=>l.text&&!l.dropped);
 const byKey=Object.fromEntries(usable.map(l=>[l.key,l]));
 const playedKey=(r.plays||[])[0]?.key;
 const order=[...(playedKey&&byKey[playedKey]?[playedKey]:[]),...(r.order||[]).filter(k=>k!==playedKey&&byKey[k])];
 const huyu=byKey.huyu&&playedKey!=='huyu'?lineHtml(r,byKey.huyu,marks,false,voices):'';
 const t=r.timings||{};
 return `<div class="round-card" data-rid="${escapeHtml(r.rid)}">${order.map((k,i)=>lineHtml(r,byKey[k],marks,i===0&&k===playedKey,voices)).join('')}${
  huyu?`<p class="round-sub muted">素材差时说的上海话</p>${huyu}`:''}
<p class="round-foot"><button data-round-none="1" class="${marks?.none?'on bad':''}">这一轮都不行</button><button data-round-new="1">重新写五句</button>
<span class="muted">${t.write_ms!=null?`写 ${(t.write_ms/1000).toFixed(1)} 秒`:''}${t.judge_ms!=null?` · 评委 ${(t.judge_ms/1000).toFixed(1)} 秒`:''}${r.weak?' · 评委觉得多半冷场':''}</span></p></div>`;
}
export class RatingBuffer{
 constructor(send,{settleMs=600,max=5}={}){this.send=send;this.settleMs=settleMs;this.max=max;this.cur=null;this.timer=null;}
 press(rid,line,polarity){
  const same=this.cur&&this.cur.rid===rid&&this.cur.line===line&&this.cur.polarity===polarity;
  if(!same){this.flush();this.cur={rid,line,polarity,count:0};}
  this.cur.count=Math.min(this.max,this.cur.count+1);
  clearTimeout(this.timer);this.timer=setTimeout(()=>this.flush(),this.settleMs);
 }
 flush(){
  clearTimeout(this.timer);this.timer=null;
  if(!this.cur)return;
  const c=this.cur;this.cur=null;
  this.send({kind:'rating',value:{polarity:c.polarity,count:c.count},refers_to:{rid:c.rid,line:c.line}});
 }
}
export function sayRoundMessage(res){
 if(res.why==='silent')return '这一刻先不说了';
 const key=res.played??(res.round?.plays||[]).at(-1)?.key;
 const line=(res.round?.lines||[]).find(l=>l.key===key);
 const secs=res.server_ms!=null?`（服务端 ${(res.server_ms/1000).toFixed(1)} 秒${res.why==='fresh'?'，现写':''}）`:'';
 return `${res.why==='fresh'?'现写：':''}${line?`说：${line.text}`:'说完了'}${secs}`;
}

export class SayPanel{
 constructor(doc){
  this.doc=doc;this.roles=[];this.seq=0;
  const $=id=>doc.getElementById(id);
  if(!$('say-buttons'))return;
  this.round=null;this.voices=[];this.mode='roles';
  this.ratings=new RatingBuffer(body=>postJson('/signal',body).then(()=>this.refreshRound()).catch(e=>{$('say-msg').textContent=sayErrorMessage(e);}));
  Promise.all([getJson('/roles'),getJson('/speech/settings').catch(()=>null),getJson('/say/stats?days=1').catch(()=>null)]).then(([r,cfg,st])=>{
   this.roles=r;this.mode=cfg?.speech?.pipeline==='round'?'round':'roles';
   // With the one-button talk show the five role buttons move into a folded section (spec 13).
   $('say-buttons').innerHTML=this.mode==='round'?roundControlsHtml(st?.prefetch?.on)+`<details class="say-roles"><summary>单个角色</summary>${roleButtonsHtml(r)}</details>`:roleButtonsHtml(r);
   this.refresh();
  },()=>{$('say-buttons').innerHTML='<span class="muted">角色读不到（情景服务未连接）</span>';});
  getJson('/voices').then(v=>{this.voices=v;$('say-voices').innerHTML=voiceButtonsHtml(v);},()=>{$('say-voices').innerHTML='<span class="muted">音色读不到</span>';});
  $('say-buttons').addEventListener('change',async ev=>{
   if(!ev.target.dataset.prefetch)return;
   try{const r=await postJson('/say/prefetch',{enabled:ev.target.checked});$('say-msg').textContent=r.enabled?'提前写已打开':'提前写已关闭';}
   catch(e){ev.target.checked=!ev.target.checked;$('say-msg').textContent=sayErrorMessage(e);}
  });
  const card=$('say-round');
  if(card){
   card.addEventListener('click',async ev=>{
    const b=ev.target.closest('button');if(!b)return;
    const art=b.closest('.line'),rid=card.querySelector('.round-card')?.dataset.rid;
    try{
     if(b.dataset.rate&&art)this.ratings.press(art.dataset.rid,art.dataset.line,Number(b.dataset.rate));
     else if(b.dataset.replay&&art){await postJson('/say/play',{rid:art.dataset.rid,line:art.dataset.line});$('say-msg').textContent='重播';}
     else if(b.dataset.roundNone&&rid){await postJson('/signal',{kind:'round_none',refers_to:{rid}});this.refreshRound();}
     else if(b.dataset.roundNew)await this.sayRound(true);
    }catch(e){$('say-msg').textContent=sayErrorMessage(e);}
   });
   card.addEventListener('change',async ev=>{
    const sel=ev.target.closest('select[data-recast]');if(!sel||!sel.value)return;
    const art=sel.closest('.line'),[voice,dialect]=sel.value.split('|');
    try{await postJson('/say/play',{rid:art.dataset.rid,line:art.dataset.line,voice,dialect});$('say-msg').textContent=`换嗓子：${dialect}`;}
    catch(e){$('say-msg').textContent=sayErrorMessage(e);}
    sel.value='';
   });
  }
  $('say-buttons').addEventListener('click',async ev=>{
   const b=ev.target.closest('button');if(!b)return;
   const seq=++this.seq;
   try{
    if(b.dataset.sayStop){await postJson('/say/stop',{});if(seq===this.seq)$('say-msg').textContent='已叫停';}
    else if(b.dataset.sayRound){await this.sayRound(false);}
    else{$('say-msg').textContent='正在想…';const r=await postJson('/say',{role:b.dataset.role});if(seq===this.seq)$('say-msg').textContent=`说：${r.text}`;}
   }catch(e){if(seq===this.seq)$('say-msg').textContent=sayErrorMessage(e);}
   this.refresh();
  });
  $('say-voices').addEventListener('click',async ev=>{
   const b=ev.target.closest('button');if(!b)return;
   const seq=++this.seq;
   try{$('say-msg').textContent='试听中…';const r=await postJson('/voice/try',{voice:b.dataset.voice});if(seq===this.seq)$('say-msg').textContent=`试听：${r.text}`;}
   catch(e){if(seq===this.seq)$('say-msg').textContent=sayErrorMessage(e);}
   this.refresh();
  });
  setInterval(()=>this.refresh(),5000);
 }
 async sayRound(forceNew){
  const $=id=>this.doc.getElementById(id);
  $('say-msg').textContent='正在想…';
  const r=await postJson('/say/round',forceNew?{force_new:true}:{});
  $('say-msg').textContent=sayRoundMessage(r);
  this.round=r.round;this.renderRound({ratings:{},none:false});
  this.refreshRound();
 }
 renderRound(marks){
  const card=this.doc.getElementById('say-round');
  if(card)card.innerHTML=roundCardHtml(this.round,marks,this.voices);
 }
 async refreshRound(){
  if(this.mode!=='round')return;
  try{
   const rows=await getJson(`/say/rounds?from=${Date.now()-3600000}&to=${Date.now()+60000}`);
   const last=rows.at(-1);
   if(last){this.round=last;this.renderRound(last.marks);}
  }catch{}
 }
 async refresh(){
  this.refreshRound();
  const d0=new Date();d0.setHours(0,0,0,0);
  try{const list=await getJson(`/utterances?from=${d0.getTime()}&to=${Date.now()+60000}`);
   this.doc.getElementById('say-utterances').innerHTML=utterancesHtml(list,this.roles);}catch{}
 }
}
