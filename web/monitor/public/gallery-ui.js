// 图库 page: the body camera's photos by day / hour, with what was read from them, and a guarded delete.
// Left: days and hours. Middle: the hour's thumbnails grouped by minute (loaded lazily, <= 60 per call). Right: the
// photo large with its 看一眼 / 分钟描述 / 情景. Deleting only works while the server says 维护中 (mind/gallery.py).
// The pure functions are exported for gallery-ui.test.mjs; the DOM part lives in mountGallery.
const MINUTE=60000, BJ_MS=8*3600000, BATCH=500, PER_CALL=60;
const two=n=>String(n).padStart(2,'0');
const escText=t=>String(t??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
const bj=ms=>new Date(Number(ms)+BJ_MS);
export const hms=ms=>{const d=bj(ms);return [d.getUTCHours(),d.getUTCMinutes(),d.getUTCSeconds()].map(two).join(':');};
export const hm=ms=>hms(ms).slice(0,5);
export const dayOf=ms=>{const d=bj(ms);return `${d.getUTCFullYear()}-${two(d.getUTCMonth()+1)}-${two(d.getUTCDate())}`;};
export const hourStart=(day,hour)=>{const [y,m,d]=day.split('-').map(Number);return Date.UTC(y,m-1,d,hour)-BJ_MS;};

export function groupByMinute(frames){
 const out=[];
 for(const f of frames){
  const minute=Math.floor(f.at/MINUTE)*MINUTE, last=out[out.length-1];
  if(last&&last.minute===minute)last.items.push(f);else out.push({minute,items:[f]});
 }
 return out;
}
// Every id from one photo to the other (either order), in the list's order. An unknown end gives just the known one.
export function selectRange(frames,fromId,toId){
 const ids=frames.map(f=>f.id), a=ids.indexOf(fromId), b=ids.indexOf(toId);
 if(a<0&&b<0)return new Set();
 if(a<0||b<0)return new Set([ids[Math.max(a,b)]]);
 return new Set(ids.slice(Math.min(a,b),Math.max(a,b)+1));
}
export function batches(ids,size=BATCH){
 const out=[];
 for(let i=0;i<ids.length;i+=size)out.push(ids.slice(i,i+size));
 return out;
}
// The plans of every batch -> one confirmation text. A minute with a 分钟描述 and its 情景 counts once.
export function planSummary(plans){
 const sum=k=>plans.reduce((n,p)=>n+(p[k]||0),0);
 const minutes=plans.reduce((n,p)=>n+Math.max(p.eye_minutes||0,p.scenes||0),0);
 const seen=new Map();
 for(const p of plans)for(const s of p.segments||[])seen.set(`${s.start}-${s.end}`,s.title||'未命名');
 const parts=[`${sum('frames')} 张照片`];
 if(sum('looks'))parts.push(`${sum('looks')} 条看一眼`);
 if(minutes)parts.push(`${minutes} 分钟的分钟描述和情景`);
 if(seen.size)parts.push(`${seen.size} 段段落总结（${[...seen.values()].join('、')}）`);
 return `将删除 ${parts.join('、')}，删除后无法恢复。`;
}
// The delete orchestration, free of the DOM. post(path, body) -> {status, json}; confirm(text) -> Promise<bool>.
// Plans every batch, asks once, deletes batch by batch with each batch's fp. A stale 409 re-plans the batches not yet
// done and asks again; any other failure stops. -> {deleted, counts, cancelled, stopped: null | {code, error, remaining}}.
export async function deleteFlow(ids,{post:rawPost,confirm,onProgress=()=>{}}){
 const post=async(p,b)=>{try{return await rawPost(p,b);}catch(e){return {status:0,json:{error:'连接出错：'+(e?.message||e)}};}};   // never throws
 const out={deleted:[],counts:[],cancelled:false,stopped:null};
 let pending=batches(ids),note='';
 while(pending.length){
  const plans=[];
  for(const b of pending){
   const r=await post('/gallery/plan',{ids:b});
   if(r.status<200||r.status>=300){out.stopped={code:r.json?.code||null,error:r.json?.error||('状态 '+r.status),remaining:pending.flat(),phase:'plan'};return out;}
   plans.push(r.json);
  }
  if(!await confirm((note?note+'\n':'')+planSummary(plans),pending.reduce((n,b)=>n+b.length,0))){out.cancelled=true;return out;}
  let stale=false;
  for(let i=0;i<pending.length;i++){
   const r=await post('/gallery/delete',{ids:pending[i],fp:plans[i].fp});
   if(r.status>=200&&r.status<300){out.deleted.push(...pending[i]);out.counts.push(r.json);onProgress(out);continue;}
   if(r.status===409&&r.json?.code==='stale'){pending=pending.slice(i);stale=true;note='清单在你确认之后有了变化，已重新生成：';break;}
   out.stopped={code:r.json?.code||null,error:r.json?.error||('状态 '+r.status),remaining:pending.slice(i).flat()};return out;
  }
  if(!stale)break;
 }
 return out;
}
// The 维护 note at the top: not in it / in it but the robot is still connected / in it and ready.
export function maintenanceHtml(m){
 if(!m)return '<span class="dot off"></span><b>读不到维护状态</b>';
 if(!m.on)return '<span class="dot off"></span><b>未在维护</b><span class="muted">可以翻看；要删除请先开始维护</span>';
 if(m.robot_connected)return `<span class="dot warn"></span><b>维护中</b><span class="muted">${escText(m.why_not||'机器人还连着，请先关机')}</span>`;
 return '<span class="dot on"></span><b>维护中</b><span class="muted">机器人已断开，可以删除</span>';
}
export function resultText(done,total){
 const s=k=>done.reduce((n,o)=>n+(o[k]||0),0);
 const bits=[`${s('frames')} 张照片`];
 if(s('looks'))bits.push(`${s('looks')} 条看一眼`);
 if(s('eye_minutes'))bits.push(`${s('eye_minutes')} 条分钟描述`);
 if(s('scenes'))bits.push(`${s('scenes')} 条情景`);
 if(s('segments'))bits.push(`${s('segments')} 段段落总结`);
 return `已删除 ${bits.join('、')}${total!=null&&s('frames')<total?`（共选了 ${total} 张）`:''}`;
}

// ---- page ----
const el=(doc,tag,cls,text)=>{const e=doc.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;return e;};

export function mountGallery(doc,{fetchImpl=(...a)=>fetch(...a),URLImpl=URL,Observer=globalThis.IntersectionObserver}={}){
 const $=id=>doc.getElementById(id);
 const S={days:[],day:null,hours:[],hour:null,frames:[],sel:new Set(),anchor:null,cur:null,maint:null,urls:new Map(),
  queue:new Set(),asked:new Set(),busy:false,token:0};
 const status=t=>{$('g-status').textContent=t||'';};
 const api=async(p,body)=>{
  const r=await fetchImpl('/api/mind'+p,body===undefined?{cache:'no-store',signal:AbortSignal.timeout(30000)}
   :{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(120000)});
  const text=await r.text();let json=null;try{json=JSON.parse(text);}catch{}
  return {ok:r.ok,status:r.status,json,text};
 };
 const need=async p=>{const r=await api(p);if(!r.ok)throw new Error(`${r.status} ${r.json?.error||r.text}`.slice(0,120));return r.json;};

 // -- maintenance
 function renderMaint(){
  const m=S.maint;$('g-maint').innerHTML=maintenanceHtml(m);
  const b=$('g-maint-btn');b.hidden=!m;if(m){b.textContent=m.on?'结束维护':'开始维护';}
  renderBar();
 }
 async function loadMaint(){try{S.maint=await need('/gallery/maintenance');}catch{S.maint=null;}renderMaint();}
 async function toggleMaint(){
  const on=!S.maint?.on;$('g-maint-btn').disabled=true;
  const r=await api('/gallery/maintenance',{on});
  $('g-maint-btn').disabled=false;
  if(r.ok)S.maint=r.json;else status('维护状态没改成：'+(r.json?.error||r.status));
  renderMaint();
 }

 // -- days / hours
 function renderDays(){
  const box=$('g-days');box.textContent='';
  if(!S.days.length)box.append(el(doc,'p','empty','还没有照片'));
  for(const d of S.days){
   const b=el(doc,'button','row');b.setAttribute('aria-pressed',String(d.day===S.day));
   b.append(el(doc,'span','row-name',d.day.slice(5)),el(doc,'span','count',String(d.n)));
   b.addEventListener('click',()=>pickDay(d.day));box.append(b);
  }
 }
 function renderHours(){
  const box=$('g-hours');box.textContent='';
  $('g-hours-title').hidden=!S.day;
  for(const h of S.hours){
   const b=el(doc,'button','row');b.setAttribute('aria-pressed',String(h.hour===S.hour));
   b.append(el(doc,'span','row-name',`${two(h.hour)}:00`),el(doc,'span','count',String(h.n)));
   b.addEventListener('click',()=>pickHour(h.hour));box.append(b);
  }
 }
 async function loadDays(){
  try{S.days=(await need('/gallery/days')).days||[];}catch(e){status('读不到日期：'+e.message);S.days=[];}
  renderDays();
 }
 async function pickDay(day,keepHour){
  S.day=day;renderDays();
  try{S.hours=(await need('/gallery/hours?day='+day)).hours||[];}catch(e){status('读不到小时：'+e.message);S.hours=[];}
  renderHours();
  if(!keepHour){const last=S.hours[S.hours.length-1];if(last)pickHour(last.hour);else clearHour();}
 }
 function revokeAll(keep){
  for(const [id,u] of S.urls)if(!keep||!keep.has(id)){URLImpl.revokeObjectURL(u);S.urls.delete(id);}
 }
 function clearHour(){S.hour=null;S.frames=[];S.sel=new Set();revokeAll();renderGrid();renderDetail(null);}
 async function pickHour(hour){
  const token=++S.token;S.hour=hour;renderHours();
  S.sel=new Set();S.anchor=null;revokeAll();S.queue.clear();S.asked.clear();S.frames=[];S.cur=null;
  $('g-grid').textContent='';$('g-hour-title').textContent=`${S.day.slice(5)}  ${two(hour)}:00–${two((hour+1)%24)}:00`;status('读取中…');
  const lo=hourStart(S.day,hour);
  try{const r=await need(`/gallery/frames?from=${lo}&to=${lo+3600000}`);if(token!==S.token)return;S.frames=r.frames||[];}
  catch(e){status('读不到照片：'+e.message);return;}
  status('');renderGrid();renderDetail(null);
 }

 // -- grid
 let io=null;
 function observe(img){
  if(!Observer){queueImage(img.dataset.id);return;}
  if(!io)io=new Observer(es=>{for(const e of es)if(e.isIntersecting){io.unobserve(e.target);queueImage(e.target.dataset.id);}},{rootMargin:'300px'});
  io.observe(img);
 }
 let flushTimer=null;
 function queueImage(id){
  if(S.urls.has(id)||S.asked.has(id))return;
  S.asked.add(id);S.queue.add(id);
  if(!flushTimer)flushTimer=setTimeout(flush,60);
 }
 async function flush(){
  flushTimer=null;const token=S.token;
  const ids=[...S.queue].slice(0,PER_CALL);ids.forEach(i=>S.queue.delete(i));
  if(!ids.length)return;
  if(S.queue.size)flushTimer=setTimeout(flush,0);
  try{
   const r=await need('/gallery/images?ids='+ids.join(','));
   if(token!==S.token)return;
   for(const id of ids){
    const b64=r.images?.[id];if(!b64)continue;
    const bytes=Uint8Array.from(atob(b64),c=>c.charCodeAt(0));
    const url=URLImpl.createObjectURL(new Blob([bytes],{type:'image/jpeg'}));
    S.urls.set(id,url);
    const img=doc.querySelector(`img[data-id="${id}"]`);if(img){img.src=url;img.classList.add('ready');}
   }
  }catch{ids.forEach(i=>S.asked.delete(i));}   // retried when scrolled into view again
 }
 const BADGES=[['look','看','看一眼'],['eye','描','分钟描述'],['scene','景','情景'],['snap','快','快拍']];
 function cell(f){
  const b=el(doc,'button','cell');b.dataset.id=f.id;b.title=`${hms(f.at)}`;
  b.setAttribute('aria-pressed',String(S.sel.has(f.id)));
  if(S.cur===f.id)b.classList.add('cur');
  const img=el(doc,'img');img.alt=hms(f.at);img.dataset.id=f.id;img.width=160;img.height=120;
  if(S.urls.has(f.id)){img.src=S.urls.get(f.id);img.classList.add('ready');}
  const tags=el(doc,'span','tags');
  for(const [k,g,name] of BADGES)if(f[k]){const t=el(doc,'span','tag tag-'+k,g);t.title=name;tags.append(t);}
  b.append(img,tags);
  b.addEventListener('click',ev=>onCell(f,ev));
  return b;
 }
 function renderGrid(){
  const box=$('g-grid');box.textContent='';
  $('g-hour-title').hidden=S.hour==null;
  $('g-hour-select').hidden=!S.frames.length;
  if(S.hour!=null&&!S.frames.length)box.append(el(doc,'p','empty','这一小时没有照片'));
  for(const g of groupByMinute(S.frames)){
   const sec=el(doc,'section','minute');
   const head=el(doc,'button','minute-head',`${hm(g.minute)}`);head.append(el(doc,'span','count',`${g.items.length} 张`));
   head.title='选中这一分钟';head.addEventListener('click',()=>{S.sel=new Set(g.items.map(i=>i.id));S.anchor=g.items[0].id;refreshSel();});
   const row=el(doc,'div','cells');for(const f of g.items)row.append(cell(f));
   sec.append(head,row);box.append(sec);
  }
  for(const img of box.querySelectorAll('img'))if(!img.classList.contains('ready'))observe(img);
  renderBar();
 }
 function refreshSel(){
  for(const b of $('g-grid').querySelectorAll('.cell')){b.setAttribute('aria-pressed',String(S.sel.has(b.dataset.id)));b.classList.toggle('cur',S.cur===b.dataset.id);}
  renderBar();
 }
 function onCell(f,ev){
  if(ev.shiftKey&&S.anchor)S.sel=selectRange(S.frames,S.anchor,f.id);
  else if(ev.metaKey||ev.ctrlKey){const s=new Set(S.sel);s.has(f.id)?s.delete(f.id):s.add(f.id);S.sel=s;S.anchor=f.id;}
  else{S.sel=new Set([f.id]);S.anchor=f.id;}
  S.cur=f.id;refreshSel();showDetail(f.id);
 }

 // -- selection bar and delete buttons
 function canDelete(){return !!S.maint&&S.maint.can_delete&&!S.busy;}
 function why(){return S.busy?'正在删除…':S.maint?(S.maint.can_delete?'':S.maint.why_not||'现在不能删除'):'读不到维护状态';}
 function renderBar(){
  const n=S.sel.size;
  $('g-count').textContent=n?`已选 ${n} 张`:'点选照片；Shift 选一段，⌘/Ctrl 加选';
  const b=$('g-delete');b.disabled=!n||!canDelete();b.textContent=n?`删除所选 ${n} 张`:'删除所选';
  $('g-why').textContent=n&&!canDelete()?why():'';
  const one=$('g-delete-one');if(one){one.disabled=!canDelete();$('g-why-one').textContent=canDelete()?'':why();}
 }

 // -- detail
 async function showDetail(id){
  const box=$('g-detail');box.textContent='';box.append(el(doc,'p','empty','读取中…'));
  let d;try{d=await need('/gallery/frame?id='+id);}catch(e){if(S.cur===id){box.textContent='';box.append(el(doc,'p','empty','读不到这张：'+e.message));}return;}
  if(S.cur!==id)return;renderDetail(d);
 }
 function renderDetail(d){
  const box=$('g-detail');box.textContent='';
  if(!d){box.append(el(doc,'p','empty','点一张照片看详情'));return;}
  if(d.jpeg_b64){
   const bytes=Uint8Array.from(atob(d.jpeg_b64),c=>c.charCodeAt(0));
   const url=URLImpl.createObjectURL(new Blob([bytes],{type:'image/jpeg'}));
   const img=el(doc,'img','big');img.alt='大图';img.addEventListener('load',()=>URLImpl.revokeObjectURL(url),{once:true});img.src=url;box.append(img);
  }else box.append(el(doc,'p','empty','照片文件已经不在了'));
  const meta=el(doc,'p','meta');meta.append(el(doc,'b',null,`${dayOf(d.at).slice(5)} ${hms(d.at)}`));
  if(d.snap){const t=el(doc,'span','tag tag-snap','快拍');t.title='拍照快拍';meta.append(t);}
  box.append(meta,el(doc,'p','small',`连接 ${d.session||'—'}`));
  const sect=(title,small,rows)=>{
   const s=el(doc,'section','note');const h=el(doc,'h3',null,title);h.append(el(doc,'small',null,small));s.append(h);
   if(!rows.length)s.append(el(doc,'p','muted','没有'));for(const t of rows)s.append(el(doc,'p','text',t));box.append(s);
  };
  sect('看一眼','vision_looks',(d.looks||[]).map(l=>`${l.at?hms(l.at)+'  ':''}${l.text||''}`));
  sect('分钟描述','eye_minutes',d.eye?[d.eye.text]:[]);
  sect('情景','mind_scenes',d.scene?[d.scene.text]:[]);
  const act=el(doc,'div','actions');
  const del=el(doc,'button','btn danger','删除这张');del.id='g-delete-one';
  del.addEventListener('click',()=>runDelete([d.id]));
  const why1=el(doc,'span','small');why1.id='g-why-one';act.append(del,why1);box.append(act);
  renderBar();
 }

 // -- modal (browser dialogs are not used)
 function ask({title,text,ok,danger=true}){
  return new Promise(resolve=>{
   $('g-modal-title').textContent=title;$('g-modal-text').textContent=text;
   const okb=$('g-modal-ok'),no=$('g-modal-no');
   okb.hidden=!ok;okb.textContent=ok||'';okb.classList.toggle('danger',danger);no.textContent=ok?'取消':'知道了';
   const close=v=>{$('g-modal').hidden=true;okb.onclick=no.onclick=null;resolve(v);};
   okb.onclick=()=>close(true);no.onclick=()=>close(false);
   $('g-modal').hidden=false;no.focus();
  });
 }
 async function runDelete(ids){
  if(!ids.length||!canDelete())return;
  S.busy=true;renderBar();
  const total=ids.length;let r={deleted:[],counts:[]};
  try{
   r=await deleteFlow(ids,{post:async(p,b)=>{const x=await api(p,b);return {status:x.status,json:x.json};},
    confirm:(t,n)=>ask({title:'确认删除',text:t,ok:`删除 ${n} 张`})});
   if(r.stopped){
    const sofar=r.deleted.length?resultText(r.counts,total)+'；其余没有删。':'一张也没有删。';
    await ask({title:r.stopped.phase==='plan'&&!r.deleted.length?'无法删除':'删除中断',text:`${r.stopped.error}。${sofar}`,ok:null});
   }else if(!r.cancelled)await ask({title:'删除完成',text:resultText(r.counts,total)+'。',ok:null});
  }catch(e){await ask({title:'删除中断',text:'出错：'+e.message,ok:null});}
  finally{
   S.busy=false;const removed=new Set(r.deleted);
   if(removed.size){
    for(const id of removed)if(S.urls.has(id)){URLImpl.revokeObjectURL(S.urls.get(id));S.urls.delete(id);}
    S.frames=S.frames.filter(f=>!removed.has(f.id));
    S.sel=new Set([...S.sel].filter(x=>!removed.has(x)));
    if(removed.has(S.cur)){S.cur=null;renderDetail(null);}
    renderGrid();
    await loadDays();await pickDay(S.day,true);
   }
   await loadMaint();
  }
 }

 $('g-maint-btn').addEventListener('click',toggleMaint);
 $('g-delete').addEventListener('click',()=>runDelete([...S.sel]));
 for(const id of ['g-hour-select','g-hour-title'])$(id).addEventListener('click',()=>{S.sel=new Set(S.frames.map(f=>f.id));S.anchor=S.frames[0]?.id||null;refreshSel();});
 $('g-refresh').addEventListener('click',async()=>{await Promise.all([loadMaint(),loadDays()]);if(S.day)await pickDay(S.day,true);if(S.hour!=null)pickHour(S.hour);});
 (async()=>{
  await Promise.all([loadMaint(),loadDays()]);
  if(S.days[0])await pickDay(S.days[0].day);
  renderDetail(null);
 })();
 return S;
}
if(typeof document!=='undefined'&&document.getElementById('g-grid'))mountGallery(document);
