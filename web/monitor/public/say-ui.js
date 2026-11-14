// 让 hello 说一句 (2026-11-11; on the main page's 台词 panel since the same day): a button per role
// (config/roles), 别说了, 试听音色 (one fixed sentence per soul-tts voice) and today's last lines.
// Everything goes through /api/mind (mind /say, /say/stop, /voice/try, /roles, /voices, /utterances).
const escapeHtml=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const hhmmss=ms=>new Date(ms).toLocaleTimeString('zh-CN',{hour12:false});

// 台词 follows the mode (2026-11-14, hello modes step 1): say actions only work in the talk show. Billing gates nothing
// (owner 2026-11-14 evening: it is only the last whole on / off). gate = {talk}; unknown / absent mode info (a mind
// without /mode yet) is the open gate, so today's panel is unchanged.
const OPEN={talk:true};
export function gateOfView(v){return v&&v.known&&typeof v.mode==='string'?{talk:v.mode==='talk'}:{...OPEN};}
export function gateNote(g){return g?.talk===false?'只在脱口秀模式，台词按钮暂停':'';}
export function conflictCode(e){
 try{const c=JSON.parse(String(e?.message||e))?.code;return c==='only_talk'?c:null;}catch{return null;}
}
export function gateAfterConflict(code,g){return code==='only_talk'?{...g,talk:false}:g;}
const noteHtml=g=>`<span class="say-gate-note muted" data-gate-note="1">${escapeHtml(gateNote(g))}</span>`;
const dis=off=>off?' disabled':'';
export function roundControlsHtml(prefetchOn,familyOn,autoOn,gate=OPEN){
 const g={...OPEN,...gate},off=!g.talk;
 return `<button data-say-round="1" class="say-round-button"${dis(off)}>说一轮</button><button data-say-stop="1" class="stop-button">别说了</button>`+noteHtml(g)+
  `<label class="prefetch-switch"><input type="checkbox" data-prefetch="1"${prefetchOn?' checked':''}${dis(off)}> 提前写（讨论激烈时开）</label>`+
  `<label class="prefetch-switch"><input type="checkbox" data-auto="1"${autoOn?' checked':''}${dis(off)}> 连续说（每 2–3 分钟，播高分的提前写）</label>`+
  `<label class="prefetch-switch"><input type="checkbox" data-family="1"${familyOn?' checked':''}> 家人声音（主人和孩子们，普通话）</label>`;
}
// 连续说 plays what 提前写 wrote (2026-11-14): turning it on turns 提前写 on; turning 提前写 off turns it off.
export function linkedSwitches(which,on){return which==='auto'&&on?{prefetch:true}:which==='prefetch'&&!on?{auto:false}:{};}
// 家人声音 (2026-11-14): one boolean in the speech settings (speech.round.family); saved like the settings page does.
export function familySettingsBody(cfg,on){
 const speech=structuredClone(cfg.speech);speech.round={...(speech.round||{}),family:!!on};
 return {speech,memory:cfg.memory,expected_version:cfg.version,note:on?'家人声音 开':'家人声音 关'};
}
export function roleButtonsHtml(roles,gate=OPEN){
 const off=gate.talk===false;
 return (roles||[]).map(r=>`<button data-role="${escapeHtml(r.id)}" title="${escapeHtml(r.name)}"${dis(off)}>${escapeHtml(r.button)}</button>`).join('')+
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
// Chat v0: the last chat turn (mind /chat/turns), heard -> reply, why it was skipped, and how long it took.
const CHAT_SKIPS={not_chat:'不在对话模式',self:'hello 自己在出声',hello_voice:'是 hello 自己的声音',hello_speaking:'hello 正在说话',
 empty:'没听清',filler:'只有语气词',not_voice:'不像人声，声音也小',far_quiet:'太远（声音太小）',mouth_not_tts:'嘴不是 TTS',
 no_model:'没配模型',no_tts:'没配 TTS',replaced:'被下一句顶掉',stale:'等太久，过期了',superseded:'想的时候又来了新的一句，回新的',
 empty_reply:'模型没写出话',llm_error:'模型出错',left_chat:'已离开对话',cancelled:'被叫停',no_mouth:'嘴没连上',tts_error:'合成出错',
 budget:'今天的模型预算用完了',error:'出错了'};
const CHAT_STATES={queued:'排队中',thinking:'正在想',answered:'回了'};
const secs=ms=>Number.isFinite(ms)?`${(ms/1000).toFixed(1)} 秒`:null;
export function chatTurnHtml(t){
 if(!t||typeof t!=='object')return '';
 const tm=t.timings&&typeof t.timings==='object'?t.timings:{};
 const sound=Number.isFinite(tm.end_to_submit_ms)&&Number.isFinite(tm.tts_first_sound_ms)?tm.end_to_submit_ms+tm.tts_first_sound_ms:null;
 const facts=[secs(tm.llm_ms)&&`模型 ${secs(tm.llm_ms)}`,secs(tm.end_to_submit_ms)&&`说完到送出 ${secs(tm.end_to_submit_ms)}`,
  secs(sound)&&`说完到开口 ${secs(sound)}`,t.model?escapeHtml(t.model):null,
  Number.isFinite(t.cost_usd)?`$${t.cost_usd.toFixed(5)}`:null].filter(Boolean).join(' · ');
 const reply=t.state==='answered'&&t.reply?escapeHtml(t.reply):
  `<span class="muted">（${escapeHtml(t.skip?CHAT_SKIPS[t.skip]||t.skip:CHAT_STATES[t.state]||t.state||'—')}）</span>`;
 return `<p class="chat-turn"><span class="muted">${Number.isFinite(t.at)?hhmmss(t.at):'—'} 对话</span> `+
  `${t.who?`<b>${escapeHtml(t.who)}</b>：`:''}${escapeHtml(t.text||'')} → ${reply}${facts?` <span class="muted">${facts}</span>`:''}</p>`;
}
export function sayErrorMessage(e){
 let m=String(e?.message||e),code;
 try{const body=JSON.parse(m);if(typeof body?.error==='string'){m=body.error;code=body.code;}}catch{}
 if(code==='only_talk'||code==='no_situation'||/^暂时没有(可靠|值得说)的新情况/.test(m))return m;
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
// The card shows what hello actually said: the latest triggered round (pre-writes stay hidden until used).
export function pickRound(rows){return (rows||[]).filter(r=>r.triggered).at(-1)||null;}
export function roundKey(r){return r?JSON.stringify([r.rid,r.plays,r.marks,r.thin_moment]):'';}
export function earlierRoundsHtml(rows){
 const done=(rows||[]).filter(r=>r.triggered).slice(0,-1).reverse();
 if(!done.length)return '';
 return `<details class="say-earlier"><summary>更早的轮次（${done.length}）</summary><ul class="utterances">${done.map(r=>{
  const said=(r.plays||[]).map(p=>(r.lines||[]).find(l=>l.key===p.key)?.text).filter(Boolean);
  return `<li>${said.length?said.map(escapeHtml).join(' / '):'<span class="muted">没出声</span>'}</li>`;}).join('')}</ul></details>`;
}
const PREFETCH_WHY={off:'条件不满足（嘴不在 TTS 或不是一轮模式）',no_speech:'最近没有听清的话',waiting:'等这段话说完（静 5 秒）',
 nothing_new:'说完后没有新话',spacing:'离上一轮不到 30 秒',same_moment:'还是同一时刻，不重写',budget:'本小时已到 0.6 美元上限',wrote:'刚写完一轮'};
const AUTO_WHY={no_stock:'没有可用的提前写',no_high_score:'没有高分句',checked_none:'核对新话后都不合适',thin:'最近一分钟没人说话',
 off:'条件不满足（嘴不在 TTS 或不是一轮模式）',no_mouth:'hello 的嘴没连上',busy:'正在说别的或正在提前写，跳过',stopped:'被叫停，没说',error:'出错'};
export function autoStatusHtml(st){
 if(!st?.on)return '<p class="note say-auto-state">连续说关着</p>';
 const l=st.last,what=!l?'还没醒过':`${hhmmss(l.at)} ${l.reason==='played'?`播了 ${Number(l.n)||0} 句${l.stopped?'（中途停了）':''}`:AUTO_WHY[l.reason]||escapeHtml(l.reason)}`;
 return `<p class="note say-auto-state">连续说开着 · ${st.next_at?`下次 ${hhmmss(st.next_at)} · `:''}上次 ${what}</p>`;
}
// 提前写 (2026-11-13): the rounds written ahead, newest first, with whether each was used, still waiting, or expired (5 min).
// With 连续说 (2026-11-14) its state line follows the list.
export function prefetchRoundsHtml(rows,status,now=Date.now(),auto=null){
 const mine=(rows||[]).filter(r=>r.source==='prefetch').reverse();
 const head=!status?.on?'提前写关着':`提前写开着 · ${status.last?`${hhmmss(status.last.at)} ${PREFETCH_WHY[status.last.reason]||escapeHtml(status.last.reason)}`:'还没跑'}`;
 return `<details class="say-prefetch"><summary>提前写（${mine.length} 轮）· ${head}</summary>${mine.length?'<ul class="utterances">'+mine.map(r=>{
  const st=r.triggered?'已用':now-r.written_at>300000?'过期':'待用';
  const byKey=Object.fromEntries((r.lines||[]).map(l=>[l.key,l]));
  return `<li><span class="muted">${hhmmss(r.written_at)} · ${st}</span> ${(r.order||[]).map((k,i)=>byKey[k]?.text?`${i===0?'<b>':''}${escapeHtml(byKey[k].text)}${i===0?'</b>':''}`:'').filter(Boolean).join(' / ')}</li>`;}).join('')+'</ul>':'<p class="note">最近一小时还没有提前写的轮次</p>'}</details>${auto?autoStatusHtml(auto):''}`;
}
const DIR_ZH={up:'上',down:'下',left:'左',right:'右'};
export function signalsHtml(rows){
 if(!rows||!rows.length)return '<p class="note">还没有遥控信号</p>';
 return '<ul class="utterances">'+[...rows].reverse().slice(0,30).map(s=>{
  const v=s.value||{};
  const what=s.kind==='rating'?`${v.polarity>0?'好':'坏'} ×${v.count}${s.refers_to?.uid?' · 评刚说的那句':''}`:
   s.kind==='mode'?`摇杆 ${DIR_ZH[v.direction]||v.direction}`:s.kind==='click'?`点击${v.type&&v.type!=='single'?`（${v.type}）`:''}`:
   s.kind==='round_none'?'这一轮都不行':escapeHtml(s.kind);
  return `<li><span class="muted">${hhmmss(s.at)} ${escapeHtml(s.source?.device||'')}</span> ${what}</li>`;}).join('')+'</ul>';
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
  this.round=null;this.voices=[];this.mode='roles';this.gate={...OPEN};
  doc.addEventListener('modechange',ev=>this.setGate(gateOfView(ev.detail)));
  this.ratings=new RatingBuffer(body=>postJson('/signal',body).then(()=>this.refreshRound()).catch(e=>{this.fail(e);}));
  Promise.all([getJson('/roles'),getJson('/speech/settings').catch(()=>null),getJson('/say/stats?days=1').catch(()=>null)]).then(([r,cfg,st])=>{
   this.roles=r;this.mode=cfg?.speech?.pipeline==='round'?'round':'roles';
   // With the one-button talk show the five role buttons move into a folded section (spec 13).
   $('say-buttons').innerHTML=this.mode==='round'?roundControlsHtml(st?.prefetch?.on,cfg?.speech?.round?.family,st?.auto?.on,this.gate)+`<details class="say-roles"><summary>单个角色</summary>${roleButtonsHtml(r,this.gate)}</details>`:roleButtonsHtml(r,this.gate)+noteHtml(this.gate);
   this.refresh();
  },()=>{$('say-buttons').innerHTML='<span class="muted">角色读不到（情景服务未连接）</span>';});
  getJson('/voices').then(v=>{this.voices=v;$('say-voices').innerHTML=voiceButtonsHtml(v);},()=>{$('say-voices').innerHTML='<span class="muted">音色读不到</span>';});
  $('say-buttons').addEventListener('change',async ev=>{
   if(ev.target.dataset.family){
    const on=ev.target.checked;
    try{const cfg=await getJson('/speech/settings');await postJson('/speech/settings',familySettingsBody(cfg,on));
     $('say-msg').textContent=on?'家人声音已打开：下一轮用主人和孩子们的声音':'家人声音已关闭';}
    catch(e){ev.target.checked=!on;this.fail(e);}
    return;
   }
   const which=ev.target.dataset.auto?'auto':ev.target.dataset.prefetch?'prefetch':null;
   if(!which)return;
   const on=ev.target.checked;
   try{const r=await postJson(which==='auto'?'/say/auto':'/say/prefetch',{enabled:on});
    for(const [k,v] of Object.entries(linkedSwitches(which,r.enabled))){const box=$('say-buttons').querySelector(`input[data-${k}]`);if(box)box.checked=v;}
    $('say-msg').textContent=which==='auto'?(r.enabled?'连续说已打开（提前写也开了）：每 2–3 分钟播一次高分的提前写':'连续说已关闭'):r.enabled?'提前写已打开':'提前写已关闭';
    this.prefetchSeen=null;this.refreshRound();}
   catch(e){ev.target.checked=!on;this.fail(e);}
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
    }catch(e){this.fail(e);}
   });
   card.addEventListener('change',async ev=>{
    const sel=ev.target.closest('select[data-recast]');if(!sel||!sel.value)return;
    const art=sel.closest('.line'),[voice,dialect]=sel.value.split('|');
    try{await postJson('/say/play',{rid:art.dataset.rid,line:art.dataset.line,voice,dialect});$('say-msg').textContent=`换嗓子：${dialect}`;}
    catch(e){this.fail(e);}
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
   }catch(e){if(seq===this.seq)this.fail(e);}
   this.refresh();
  });
  $('say-voices').addEventListener('click',async ev=>{
   const b=ev.target.closest('button');if(!b)return;
   const seq=++this.seq;
   try{$('say-msg').textContent='试听中…';const r=await postJson('/voice/try',{voice:b.dataset.voice});if(seq===this.seq)$('say-msg').textContent=`试听：${r.text}`;}
   catch(e){if(seq===this.seq)this.fail(e);}
   this.refresh();
  });
  setInterval(()=>this.refresh(),5000);
 }
 // Flip disabled in place (a rebuild would reset the checkboxes and close the role list); the note text follows too.
 setGate(g){
  this.gate=g;
  const root=this.doc.getElementById('say-buttons');
  if(!root)return;
  const off=!g.talk;
  for(const el of root.querySelectorAll('[data-say-round],[data-role],[data-prefetch],[data-auto]'))el.disabled=off;
  for(const el of root.querySelectorAll('[data-gate-note]'))el.textContent=gateNote(g);
 }
 // mind's 409 on a say action: show its reason, take the gate it names at once, then read the real mode.
 fail(e){
  this.doc.getElementById('say-msg').textContent=sayErrorMessage(e);
  const code=conflictCode(e);if(!code)return;
  this.setGate(gateAfterConflict(code,this.gate));
  getJson('/mode').then(st=>{const s=st?.snapshot;if(s&&typeof s.mode==='string')this.setGate(gateOfView({known:true,mode:s.mode}));},()=>{});
 }
 async sayRound(forceNew){
  const $=id=>this.doc.getElementById(id);
  $('say-msg').textContent='正在想…';
  const r=await postJson('/say/round',forceNew?{force_new:true}:{});
  $('say-msg').textContent=sayRoundMessage(r);
  this.round=r.round;this.renderRound({ratings:{},none:false});
  this.refreshRound();
 }
 renderRound(marks,earlier=''){
  const card=this.doc.getElementById('say-round');
  if(card)card.innerHTML=roundCardHtml(this.round,marks,this.voices)+earlier;
 }
 async refreshRound(){
  if(this.mode!=='round')return;
  try{
   const rows=await getJson(`/say/rounds?from=${Date.now()-3600000}&to=${Date.now()+60000}`);
   const last=pickRound(rows);
   // Re-render only on change, so an open recast list is not closed by the 5 s refresh.
   const key=roundKey(last)+'|'+rows.filter(r=>r.triggered).length;
   if(last&&key!==this.roundKeySeen){this.roundKeySeen=key;this.round=last;this.renderRound(last.marks,earlierRoundsHtml(rows));}
   const pf=this.doc.getElementById('say-prefetch');
   if(pf){const stats=await getJson('/say/stats?days=1');const html=prefetchRoundsHtml(rows,stats.prefetch,Date.now(),stats.auto||{on:false});
    if(html!==this.prefetchSeen){const open=pf.querySelector('details')?.open;this.prefetchSeen=html;pf.innerHTML=html;if(open)pf.querySelector('details').open=true;}}
  }catch{}
 }
 async refresh(){
  this.refreshRound();
  const sig=this.doc.getElementById('say-signals');
  if(sig){try{sig.innerHTML=signalsHtml(await getJson(`/signals?from=${Date.now()-3600000}&to=${Date.now()+60000}`));}catch{}}
  const chat=this.doc.getElementById('say-chat');
  if(chat){try{const rows=await getJson('/chat/turns?limit=1');chat.innerHTML=chatTurnHtml(rows?.[0]);}catch{chat.innerHTML='';}}
  const d0=new Date();d0.setHours(0,0,0,0);
  try{const list=await getJson(`/utterances?from=${d0.getTime()}&to=${Date.now()+60000}`);
   this.doc.getElementById('say-utterances').innerHTML=utterancesHtml(list,this.roles);}catch{}
 }
}
