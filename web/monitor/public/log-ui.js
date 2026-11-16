// 日志 page: hello's run/audit log through mind's read-only /api/mind/log* (server/mind/api.py over
// server/common/hello_log_query.py; record format docs/hello-log.md). Filters on top, the newest records first with
// paging by `before`, a record's envelope and its bodies (each body fetched when opened, never kept), a trace's whole
// chain, the day's ledger, each service's last record (red when over 2 minutes old while looking at now) and gaps
// (missing heartbeats, seq jumps) as red bars. The pure functions are exported for log-ui.test.mjs.
export const SVCS=['ear','mind','eye','notes','memory','tts'];
export const KIND_PREFIXES=['call','gate','trigger','switch','mode','link','svc','heard','robot'];
export const STALE_MS=120000;
const escText=t=>String(t??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
const esc=t=>escText(t).replace(/"/g,'&quot;');
const two=n=>String(n).padStart(2,'0');
// Beijing time (+08:00, no DST) everywhere, like the server's months and days, whatever zone the Mac is in.
const BJ_MS=8*3600000;
const bj=ms=>new Date(Number(ms)+BJ_MS);                                      // read with the getUTC* getters
export const hms=ms=>{const d=bj(ms);return [d.getUTCHours(),d.getUTCMinutes(),d.getUTCSeconds()].map(two).join(':');};
export const today=(ms=Date.now())=>{const d=bj(ms);return `${d.getUTCFullYear()}-${two(d.getUTCMonth()+1)}-${two(d.getUTCDate())}`;};
const full=ms=>`${today(ms)} ${hms(ms)}`;
// <input type="datetime-local"> values read and written as Beijing wall time.
export const bjInput=ms=>{const d=bj(ms);return `${today(ms)}T${two(d.getUTCHours())}:${two(d.getUTCMinutes())}`;};
export const parseBj=v=>{const m=/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(v||'');
 return m?Date.UTC(+m[1],m[2]-1,+m[3],+m[4],+m[5])-BJ_MS:NaN;};

// {from, to, kind ("call." = prefix), svc, trace, cost (true: only with a cost), before, limit} -> the query string.
export function buildQuery({from,to,kind,svc,trace,cost,before,limit=200}={}){
 const q=[];
 const add=(k,v)=>{if(v!==undefined&&v!==null&&v!=='')q.push(k+'='+encodeURIComponent(v));};
 add('from',from);add('to',to);add('kind',kind);add('svc',svc);add('trace',trace);
 if(cost)q.push('cost=1');
 add('before',before);add('limit',limit);
 return q.join('&');
}

const SIGNS={CNY:'¥',USD:'$'};
export function fmtCost(cost){
 if(!cost)return '';
 if(cost.amount===null||cost.amount===undefined)return '未知';
 const a=Number(cost.amount);
 const n=(a===0||a>=0.0001?a.toFixed(4):a.toFixed(8)).replace(/\.?0+$/,'');
 return (SIGNS[cost.cur]??((cost.cur||'?')+' '))+n;
}

function ago(ms){
 const s=Math.max(0,Math.round(ms/1000));
 if(s<60)return `${s} 秒前`;
 if(s<3600)return `${Math.floor(s/60)} 分钟前`;
 if(s<86400)return `${Math.floor(s/3600)} 小时前`;
 return `${Math.floor(s/86400)} 天前`;
}
// {svc: {at, kind, run} | null} from /log/last -> the services whose last record is over 2 minutes old (or none).
export const staleSvcs=(last,now)=>SVCS.filter(s=>!last?.[s]||now-last[s].at>STALE_MS);
export function lastSeenHtml(last,now,live){
 const stale=new Set(live?staleSvcs(last,now):[]);
 return SVCS.map(s=>{
  const e=last?.[s];
  const text=e?`${s} ${ago(now-e.at)}`:`${s} 30 天内没有记录`;
  const title=e?`最后一条：${full(e.at)} ${e.kind}${e.run?` · run ${e.run}`:''}`:'30 天内没有这个服务的记录';
  return `<span class="svc-last${stale.has(s)?' stale':''}" title="${esc(title)}">${escText(text)}</span>`;
 }).join('');
}

const WHY={no_beat:'没有心跳',seq_jump:'序号跳号'};
const round3=x=>Math.round(x*1000)/1000;
// Gaps placed on [from, to): left / width in percent, clipped to the window.
export function gapSegments(gaps,from,to){
 const span=Math.max(1,to-from),pct=ms=>Math.min(100,Math.max(0,(ms-from)/span*100));
 return (gaps||[]).map(g=>{const l=pct(g.from_ms),r=pct(g.to_ms);
  return {svc:g.svc,why:g.why,run:g.run,from_ms:g.from_ms,to_ms:g.to_ms,left:round3(l),width:round3(Math.max(0,r-l)),
   label:`${g.svc} ${WHY[g.why]||g.why} ${hms(g.from_ms)}–${hms(g.to_ms)}`};});
}
export function gapsHtml(gaps,from,to){
 const seg=gapSegments(gaps,from,to);
 if(!seg.length)return '';
 const rows=SVCS.concat([...new Set(seg.map(s=>s.svc))].filter(s=>!SVCS.includes(s))).filter(s=>seg.some(g=>g.svc===s)).map(s=>
  `<div class="gaprow"><span class="svc">${escText(s)}</span><div class="gapbar">`+
  seg.filter(g=>g.svc===s).map(g=>`<i class="gap ${esc(g.why)}" data-l="${g.left}" data-w="${g.width}" title="${esc(g.label)}"></i>`).join('')+
  '</div></div>');
 return rows.join('')+'<ul class="gaplist">'+seg.map(g=>`<li class="gap-item">${escText(g.label)}${g.run?` <span class="alt">run ${escText(g.run)}</span>`:''}</li>`).join('')+'</ul>';
}

export function rowHtml(r){
 const sum=(r.sum||'')+(r.ok===false&&r.err?` · 错：${r.err}`:'');
 return `<div class="logrow${r.ok===false?' bad':''}" data-id="${esc(r._id)}" title="${esc(full(r.at))}">`+
  `<span class="t">${hms(r.at)}</span><span class="svc">${escText(r.svc)}</span><span class="kind">${escText(r.kind)}</span>`+
  `<span class="summary">${escText(sum)}</span><span class="cost">${escText(fmtCost(r.cost))}</span></div>`;
}

const traceButton=(trace,at)=>`<button data-trace="${esc(trace)}" data-at="${Number(at)||0}">链路 ${escText(trace)}</button>`;
const HEARD_KINDS=new Set(['call.realtime.turn','gate.realtime']);
// A record opened: its trace, the ear's heard candidates (realtime turns / gate: by time, not by trace), the envelope,
// and one slot per body section (fetched when its button is pressed).
export function detailHtml(r){
 const heard=HEARD_KINDS.has(r.kind)&&Array.isArray(r.extra?.heard)?r.extra.heard:null;
 const parts=Object.entries(r.body||{}).filter(([,id])=>typeof id==='string');
 return '<div class="detail">'+
  (r.trace?`<p>${traceButton(r.trace,r.at)}</p>`:'<p class="alt">没有链路编号</p>')+
  (heard?`<h3>ear 听到的候选（按时间找的，不是确定的归属）</h3>`+(heard.length?'<ul class="heard">'+heard.map(h=>
   `<li>${h.trace?traceButton(h.trace,h.at):''} ${h.at?hms(h.at):''} 「${escText(h.text)}」${h.trace?'':'（没有链路）'}</li>`).join('')+'</ul>':'<p class="alt">没有候选</p>'):'')+
  `<h3>信封</h3><pre class="envelope">${escText(JSON.stringify(r,null,2))}</pre>`+
  (parts.length?'<h3>正文</h3>'+parts.map(([name,id])=>`<div class="bodypart"><button data-body="${esc(id)}" data-at="${Number(r.at)||0}" data-name="${esc(name)}">${escText(name)}</button><pre hidden></pre></div>`).join(''):'')+
  '</div>';
}

export function chainHtml(trace,rows){
 return `<div class="chainhead"><h2>链路 ${escText(trace)}（${rows.length} 条）</h2><button data-close-chain>关闭</button></div>`+
  (rows.length?rows.map(rowHtml).join(''):'<p class="alt">这条链路前后一天内没有记录</p>');
}

export function ledgerHtml(l){
 if(!l)return '<p class="alt">读不到总账</p>';
 const totals=Object.entries(l.total||{}).map(([cur,amount])=>fmtCost({amount,cur}));
 return `<div class="ledger-total"><b>${escText(l.day)} 总账</b> ${escText(totals.join(' · ')||'没有花费')}`+
  `${l.unknown_n?` <span class="warn">含未知 ${l.unknown_n} 笔</span>`:''}</div>`+
  ((l.by||[]).length?'<table><tr><th>服务</th><th>模型</th><th>金额</th><th>笔数</th></tr>'+l.by.map(b=>
   `<tr><td>${escText(b.svc)}</td><td>${escText(b.model||'—')}</td><td>${escText(fmtCost({amount:b.amount,cur:b.cur}))}${b.unknown_n?` <span class="warn">+${b.unknown_n} 笔未知</span>`:''}</td><td>${b.n}</td></tr>`).join('')+'</table>':'');
}

function bodyText(t){
 const s=String(t??'');
 if(/^\s*[[{]/.test(s)){try{return JSON.stringify(JSON.parse(s),null,2);}catch{/* plain text */}}
 return s;
}

const RANGES={'15m':15*60000,'1h':3600000,'6h':6*3600000,'24h':86400000};
export const LAST_POLL_MS=60000;                 // mind caches /log/last for 60 s
export function mountLog(doc,{fetchJson=async p=>{const r=await fetch('/api/mind'+p,{cache:'no-store',signal:AbortSignal.timeout(15000)});if(!r.ok)throw new Error(`${r.status} ${await r.text()}`);return r.json();},now=Date.now,
 every=(f,ms)=>setInterval(f,ms),stopEvery=t=>clearInterval(t)}={}){
 const $=id=>doc.getElementById(id);
 const rows=new Map();let win=null,lastId=null,timer=null;
 const status=(t,bad=false)=>{const s=$('log-status');s.textContent=t;s.classList.toggle('error',bad);};
 const live=()=>$('log-range').value!=='custom';
 function place(root){root.querySelectorAll('[data-l]').forEach(e=>{e.style.left=e.dataset.l+'%';e.style.width=e.dataset.w+'%';});}
 function filters(){
  let from,to;
  if(live()){to=now();from=to-RANGES[$('log-range').value];}
  else{from=parseBj($('log-from').value);to=parseBj($('log-to').value);}
  return {from,to,kind:$('log-kind').value,svc:$('log-svc').value,trace:$('log-trace').value.trim(),cost:$('log-cost').checked};
 }
 async function last(){
  try{const d=await fetchJson('/log/last');$('log-svcs').innerHTML=lastSeenHtml(d.svcs,d.now,live());}
  catch(e){$('log-svcs').innerHTML=`<span class="error">读不到各服务的最后记录：${escText(e.message)}</span>`;}
 }
 async function gaps(){
  try{const d=await fetchJson(`/log/gaps?from=${win.from}&to=${win.to}`);
   const html=gapsHtml(d.gaps,d.from,d.to);$('log-gaps').innerHTML=html||'<p class="alt">这段时间没有断档</p>';place($('log-gaps'));}
  catch(e){$('log-gaps').innerHTML=`<p class="error">读不到断档：${escText(e.message)}</p>`;}
 }
 async function ledger(){
  try{$('log-ledger-box').innerHTML=ledgerHtml(await fetchJson('/log/ledger?day='+encodeURIComponent($('log-day').value||today(now()))));}
  catch(e){$('log-ledger-box').innerHTML=`<p class="error">读不到总账：${escText(e.message)}</p>`;}
 }
 async function page(more){
  const list=$('log-list');
  if(!more){const f=filters();if(!(f.from<f.to)){status('时间范围不对',true);return;}win=f;lastId=null;rows.clear();list.innerHTML='';}
  status('读取中…');
  try{
   const d=await fetchJson('/log?'+buildQuery({...win,before:more?lastId:undefined}));
   if(!more)win={...win,from:d.from,to:d.to};
   d.rows.forEach(r=>rows.set(r._id,r));
   list.insertAdjacentHTML('beforeend',d.rows.map(rowHtml).join('')||(more?'':'<p class="alt">这段时间没有记录</p>'));
   lastId=d.rows.length?d.rows.at(-1)._id:lastId;
   $('log-more').hidden=!d.more;
   status(`${rows.size} 条 · 北京时间 ${full(win.from)} – ${full(win.to)}`);
  }catch(e){status('读取失败：'+e.message,true);}
 }
 async function refresh(){await page(false);gaps();last();ledger();}
 async function chain(trace,at){
  const box=$('log-chain');box.hidden=false;box.innerHTML='<p class="alt">读取链路…</p>';
  try{const d=await fetchJson(`/log/chain?trace=${encodeURIComponent(trace)}&at=${at}`);d.rows.forEach(r=>rows.set(r._id,r));box.innerHTML=chainHtml(trace,d.rows);}
  catch(e){box.innerHTML=`<p class="error">读不到链路：${escText(e.message)}</p>`;}
  box.scrollIntoView({block:'start'});
 }
 async function bodyPart(btn){
  const pre=btn.nextElementSibling;
  if(!pre.hidden){pre.hidden=true;pre.textContent='';return;}        // closed: nothing kept
  pre.hidden=false;pre.textContent='读取中…';
  try{const d=await fetchJson(`/log/body?at=${btn.dataset.at}&id=${btn.dataset.body}`);pre.textContent=bodyText(d.text);}
  catch(e){pre.textContent='读不到正文：'+e.message;}
 }
 doc.addEventListener('click',e=>{
  const t=e.target.closest?.('[data-trace],[data-body],[data-close-chain],.logrow');
  if(!t)return;
  if(t.dataset.trace){chain(t.dataset.trace,t.dataset.at);return;}
  if(t.dataset.body){bodyPart(t);return;}
  if(t.hasAttribute('data-close-chain')){$('log-chain').hidden=true;$('log-chain').innerHTML='';return;}
  const next=t.nextElementSibling;
  if(next?.classList.contains('detail')){next.remove();return;}
  const r=rows.get(t.dataset.id);if(r)t.insertAdjacentHTML('afterend',detailHtml(r));
 });
 $('log-range').addEventListener('change',()=>{$('log-custom').hidden=live();if(live())refresh();});
 for(const id of ['log-kind','log-svc','log-cost'])$(id).addEventListener('change',()=>page(false));
 $('log-trace').addEventListener('keydown',e=>{if(e.key==='Enter')page(false);});
 $('log-refresh').addEventListener('click',refresh);
 $('log-more').addEventListener('click',()=>page(true));
 $('log-day').value=today(now());$('log-day').addEventListener('change',ledger);
 $('log-to').value=bjInput(now());$('log-from').value=bjInput(now()-3600000);
 refresh();
 if(timer!==null)stopEvery(timer);
 timer=every(()=>{if(live())last();},LAST_POLL_MS);
 return {refresh};
}

if(typeof document!=='undefined'&&document.getElementById('log-list'))mountLog(document);
