// 视觉爆发（注意看）on the 情景 page: the ear's burst looks from /api/mind/looks (server/ear/vision_burst.py), each with
// the photo it saw (/api/mind/looks/frame, base64 JSON shown through a blob: URL; the CSP has no data:) and the model's
// text. Polls every 3 s while a burst runs, else every 15 s; photos are fetched once per look.
const esc=t=>String(t??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const hms=ms=>{const d=new Date(ms);return [d.getHours(),d.getMinutes(),d.getSeconds()].map(n=>String(n).padStart(2,'0')).join(':');};
const REASONS={elapsed:'2 分钟到了',heard_stop:'听到“别看了”',max_looks:'看满了',failures:'连续失败',left_crowd:'离开了围观',
 switch_off:'围观关了',mouth_dropped:'机器人断开',shutdown:'服务重启',cancelled:'中断'};
export const reasonText=r=>REASONS[String(r||'').split(':')[0]]||esc(r||'—');

export function burstStatusHtml(d,now=Date.now()){
 if(!d)return '<p class="alt">读不到视觉爆发记录</p>';
 const r=d.running;
 if(r)return `<p class="burst-on">● 正在看：已看 ${r.looks} 张，开始于 ${hms(r.since)}（${Math.max(0,Math.round((now-r.since)/1000))} 秒前）</p>`;
 const b=(d.bursts||[])[0];
 if(!b)return '<p class="alt">还没有视觉爆发。围观模式下说“注意看”就会开始。</p>';
 const fails=Object.values(b.failures||{}).reduce((a,n)=>a+n,0);
 return `<p>上一次：${hms(b.at)}，${esc(b.duration_s)} 秒，看了 ${esc(b.looks)} 张`+
  `${b.latency_p50_ms!=null?`，识图中位 ${(b.latency_p50_ms/1000).toFixed(1)} 秒`:''}`+
  `${b.cycle_mean_ms!=null?`，每张约 ${(b.cycle_mean_ms/1000).toFixed(1)} 秒`:''}，失败 ${fails} 次，`+
  `$${Number(b.cost_usd||0).toFixed(4)}；结束：${reasonText(b.reason)}</p>`;
}

export function looksHtml(looks){
 if(!looks?.length)return '';
 return '<ol class="looks">'+looks.map(l=>`<li data-burst="${esc(l.burst_id)}">`+
  (l.has_frame?`<img data-lid="${esc(l.lid)}" alt="${hms(l.at)} 这一张照片" width="160" height="120">`:'<div class="nophoto alt">没有照片</div>')+
  `<div><b>${hms(l.at)}</b>${l.latency_ms!=null?` <span class="alt">识图 ${(l.latency_ms/1000).toFixed(1)} 秒</span>`:''}`+
  `<p>${l.text?esc(l.text):`<span class="alt">没认出来（${esc(l.error||'空')}）</span>`}</p></div></li>`).join('')+'</ol>';
}

export function mountLooks(root,{fetchJson=async p=>{const r=await fetch('/api/mind'+p,{signal:AbortSignal.timeout(10000)});if(!r.ok)throw new Error(r.status);return r.json();}}={}){
 const urls=new Map();let timer=null,shown='';
 async function photo(img){
  const lid=img.dataset.lid;
  if(urls.has(lid)){img.src=urls.get(lid);return;}
  try{
   const f=await fetchJson('/looks/frame?lid='+encodeURIComponent(lid));
   if(!f.jpeg_b64){img.replaceWith(Object.assign(document.createElement('div'),{className:'nophoto alt',textContent:'照片不在了'}));return;}
   const url=URL.createObjectURL(new Blob([Uint8Array.from(atob(f.jpeg_b64),c=>c.charCodeAt(0))],{type:'image/jpeg'}));
   urls.set(lid,url);img.src=url;
  }catch{/* next refresh tries again */}
 }
 async function refresh(){
  let d=null;
  try{d=await fetchJson('/looks?limit=60');}catch{d=null;}
  root.querySelector('.burst-status').innerHTML=burstStatusHtml(d);
  const html=looksHtml(d?.looks);
  if(html!==shown){shown=html;root.querySelector('.burst-looks').innerHTML=html;root.querySelectorAll('img[data-lid]').forEach(photo);}
  clearTimeout(timer);timer=setTimeout(refresh,d?.running?3000:15000);
 }
 refresh();
 return {refresh};
}
