// 情景摘要 (2026-11-13): the per-minute scene beside the talk-show card, so a line can be read against
// what mind thought was happening. Reads /api/mind/scenes (latest version of each minute).
const escapeHtml=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const hhmm=ms=>new Date(ms).toLocaleTimeString('zh-CN',{hour12:false,hour:'2-digit',minute:'2-digit'});
export function sceneMinutesHtml(rows){
 if(!rows||!rows.length)return '<p class="note">还没有情景（情景开关关着，或最近没有新分钟）</p>';
 return '<ol class="scene-minutes">'+[...rows].reverse().map(r=>`<li><span class="muted">${hhmm(r.minute)}${r.source==='carried'?' · 沿用':''}</span> ${escapeHtml(r.text||'—')}${
  r.summary?`<br><span class="scene-summary">${escapeHtml(r.summary)}</span>`:''}</li>`).join('')+'</ol>';
}
export class SceneMinutesPanel{
 constructor(doc){
  this.el=doc.getElementById('scene-minutes');
  if(!this.el)return;
  this.refresh();setInterval(()=>this.refresh(),15000);
 }
 async refresh(){
  const now=Date.now();
  try{const r=await fetch(`/api/mind/scenes?from=${now-20*60000}&to=${now+60000}`,{signal:AbortSignal.timeout(10000)});
   if(!r.ok)throw new Error(await r.text());this.el.innerHTML=sceneMinutesHtml(await r.json());}
  catch{this.el.innerHTML='<p class="note">情景服务未连接</p>';}
 }
}
