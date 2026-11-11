// 让 hello 说一句 (2026-11-11; on the main page's 台词 panel since the same day): a button per role
// (config/roles), 别说了, 试听音色 (one fixed sentence per soul-tts voice) and today's last lines.
// Everything goes through /api/mind (mind /say, /say/stop, /voice/try, /roles, /voices, /utterances).
const escapeHtml=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const hhmmss=ms=>new Date(ms).toLocaleTimeString('zh-CN',{hour12:false});

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
  const extra=[SAY_STATE[u.state]||escapeHtml(u.state||''),u.timings_s?.first_accepted!=null?`${Number(u.timings_s.first_accepted).toFixed(1)} 秒开口`:'',
   u.tts_cost_cny!=null?`¥${Number(u.tts_cost_cny).toFixed(4)}`:''].filter(Boolean).join(' · ');
  return `<li><span class="muted">${hhmmss(u.at)}</span> <b>${escapeHtml(name[u.role]||u.role||'')}</b> ${escapeHtml(u.text||'')} <span class="muted">${extra}</span></li>`;
 }).join('')+'</ul>';
}
function why(e){
 const m=String(e?.message||e);
 return m.includes('TTS 模式')?'先把顶上的「嘴」切到 TTS':m.includes('嘴没连上')?'hello 的嘴还没连上（本体 /mouth 未连接）':`没说成：${m}`;
}
async function getJson(path){const r=await fetch('/api/mind'+path,{signal:AbortSignal.timeout(10000)});if(!r.ok)throw new Error(await r.text());return r.json();}
async function postJson(path,obj){
 const r=await fetch('/api/mind'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(obj),signal:AbortSignal.timeout(60000)});
 const text=await r.text();if(!r.ok)throw new Error(text);return JSON.parse(text);
}
export class SayPanel{
 constructor(doc){
  this.doc=doc;this.roles=[];
  const $=id=>doc.getElementById(id);
  if(!$('say-buttons'))return;
  getJson('/roles').then(r=>{this.roles=r;$('say-buttons').innerHTML=roleButtonsHtml(r);this.refresh();},()=>{$('say-buttons').innerHTML='<span class="muted">角色读不到（情景服务未连接）</span>';});
  getJson('/voices').then(v=>{$('say-voices').innerHTML=voiceButtonsHtml(v);},()=>{$('say-voices').innerHTML='<span class="muted">音色读不到</span>';});
  $('say-buttons').addEventListener('click',async ev=>{
   const b=ev.target.closest('button');if(!b)return;
   try{
    if(b.dataset.sayStop){await postJson('/say/stop',{});$('say-msg').textContent='已叫停';}
    else{$('say-msg').textContent='正在想…';const r=await postJson('/say',{role:b.dataset.role});$('say-msg').textContent=`说：${r.text}`;}
   }catch(e){$('say-msg').textContent=why(e);}
   this.refresh();
  });
  $('say-voices').addEventListener('click',async ev=>{
   const b=ev.target.closest('button');if(!b)return;
   try{$('say-msg').textContent='试听中…';const r=await postJson('/voice/try',{voice:b.dataset.voice});$('say-msg').textContent=`试听：${r.text}`;}
   catch(e){$('say-msg').textContent=why(e);}
   this.refresh();
  });
  setInterval(()=>this.refresh(),5000);
 }
 async refresh(){
  const d0=new Date();d0.setHours(0,0,0,0);
  try{const list=await getJson(`/utterances?from=${d0.getTime()}&to=${Date.now()+60000}`);
   this.doc.getElementById('say-utterances').innerHTML=utterancesHtml(list,this.roles);}catch{}
 }
}
