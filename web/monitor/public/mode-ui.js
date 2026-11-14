// Mode bar and quick settings (hello modes step 1, spec 2026-11-14 v2 §5.5, §7) under the switch bar on both pages.
// mind is the only place that changes the mode: the page reads GET /api/mind/mode every 2 s and sends inputs.
// Picking the current mode leaves it (mind decides; owner 2026-11-14). A preset sends its server half first, then
// runs the robot half mind names with the switch bar's own tier requests (runLevel / runVision): no queue, no retry,
// each failed step is written out (spec §3.8). <nav id="modebar"> starts it; modechange events go to the say panel.
import { runLevel, runVision } from '/switches-ui.js';

const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const hhmmss=ms=>Number.isFinite(ms)?new Date(ms).toLocaleTimeString('zh-CN',{hour12:false}):'—';

const NAMES={talk:'脱口秀',chat:'对话',settings:'设置',event:'事件驱动',subtitle:'字幕',crowd:'围观',companion:'陪伴',idle:'IDLE'};
const DIRS={zh_en:'中→英',en_zh:'英→中'};
// The nine buttons, in the robot's swipe-up page order (spec §5.4): subtitle twice, one per direction.
export const MODE_BUTTONS=[['talk'],['chat'],['settings'],['event'],['subtitle','zh_en'],['subtitle','en_zh'],['crowd'],['companion'],['idle']];
const SOURCES={asr:'口令',touch:'触摸',select:'上滑页',quick:'快捷设置',dualkey:'DualKey',page:'网页',boot:'开机',timeout:'超时'};
export const PRESETS={0:'花费全关',1:'数据收集',2:'听悟反转',3:'提前写反转',4:'IDLE',5:'空',6:'空',7:'空',8:'空',9:'花费全开'};
const UNDEFINED_PRESETS=new Set([5,6,7,8]);
const CODES={ok:'',already:'已经是这个模式了',not_in_mode:'不在这个模式里',only_talk:'只在脱口秀里有用',billing_off:'计费关着',
 busy:'正在忙，没执行',too_soon:'太快了，等一会儿再按',mouth_not_tts:'嘴没切到 TTS',not_implemented:'这个模式里还没做',
 undefined_preset:'这个键还没定义',stale:'输入太旧，丢掉了',unknown_input:'没有这个输入',no_action:'这个模式里这个输入没有动作',
 senses_on_page:'机器人那一半要在网页上按'};
const SWITCH_NAMES={tingwu:'听悟',eye:'识别',notes:'纪要',mind:'情景',scene:'情景',prefetch:'提前写',auto:'连续说',speech:'说话',family:'家人声音'};
const modeName=(mode,dir)=>NAMES[mode]?(mode==='subtitle'&&DIRS[dir]?`字幕 ${DIRS[dir]}`:NAMES[mode]):String(mode??'—');

// GET /mode reply -> what the bar shows. Unknown (mind not reached, bad reply) is never shown as a mode.
// why (unknown only): 'absent' = mind answers but has no /mode yet (404, before the modes deploy); else not reached.
export function modeView(state,why=null){
 const s=state?.snapshot;
 if(!s||typeof s.mode!=='string')return {known:false,why:why==='absent'?'absent':'down',mode:null,name:'模式未知',label:'',color:null,since:null,by:null,
  synced:'offline',billing:null,event:null,dir:null,returnTo:null,seq:null};
 const robot=state.robot||{};
 const synced=!robot.connected?'offline':robot.applied_seq===s.seq?'yes':'waiting';
 const color=typeof s.look?.color==='string'&&/^[0-9A-Fa-f]{6}$/.test(s.look.color)?s.look.color.toUpperCase():null;
 return {known:true,mode:s.mode,name:modeName(s.mode,s.dir),label:String(s.look?.label??''),color,since:s.since??null,by:s.by??null,
  synced,billing:typeof s.billing==='boolean'?s.billing:null,event:s.host==='event'?'hosting':s.mode==='event'?'waiting':null,
  dir:s.dir??null,returnTo:s.mode==='settings'?(s.return_to||'idle'):null,seq:s.seq??null};
}

export function selectRequest(mode,dir){return {input:'select',value:dir?{mode,dir}:{mode}};}

export function logLine(r){
 const what=r?.input==='billing'?`计费总闸 ${r.value?'开':'关'}`:r?.input==='digit'?`预设 ${esc(r.value)}`:
  r?.from===r?.to?`${esc(modeName(r?.to))}（${esc(r?.input)}）`:`${esc(modeName(r?.from))} → ${esc(modeName(r?.to))}`;
 const code=r?.code&&r.code!=='ok'?` · ${esc(CODES[r.code]||r.code)}`:'';
 return `${esc(hhmmss(r?.at))} ${esc(SOURCES[r?.source]||r?.source)} · ${what}${code}`;
}

const SYNC={yes:'机器人已显示',waiting:'等机器人',offline:'机器人离线（连上后照这个显示）'};
export function modeBarHtml(view,log){
 const v=view||modeView(null),dis=v.known?'':' disabled';
 const head=!v.known?(v.why==='absent'?'<span class="mode-name">mind 还没有模式功能（等上线）</span><span class="mode-note">情景服务连着，只是还没有模式；按钮先不能用</span>':
   '<span class="mode-name">模式未知（情景服务没连上）</span><span class="mode-note">按钮先不能用</span>'):
  `<span class="mode-chip"${v.color?` data-color="${v.color}"`:''}>${esc(v.label)}</span>`+
  `<span class="mode-name">${esc(v.name)}</span>`+
  (v.event?`<span class="mode-auto" title="${v.event==='hosting'?'事件驱动托管中':'事件驱动等待中'}">${v.event==='hosting'?'A':'AUTO'}</span>`:'')+
  (v.returnTo?`<span class="mode-note">关了回到 ${esc(modeName(v.returnTo,v.dir))}（没动作会自己回去）</span>`:'')+
  `<span class="mode-note">${esc(hhmmss(v.since))} · ${esc(SOURCES[v.by]||v.by||'—')}</span>`+
  `<span class="mode-sync mode-sync-${v.synced}">${SYNC[v.synced]}</span>`;
 const buttons=MODE_BUTTONS.map(([m,d])=>{
  const cur=v.known&&v.mode===m&&(m!=='subtitle'||v.dir===d);
  return `<button data-mode="${m}"${d?` data-dir="${d}"`:''} aria-pressed="${cur}" title="${cur?'当前模式，再点 = 离开':'进入'}"${dis}>${esc(modeName(m,d))}</button>`;
 }).join('');
 const rows=(Array.isArray(log)?log:[]).slice(0,10);
 const list=rows.length?`<ol>${rows.map(r=>`<li>${logLine(r)}</li>`).join('')}</ol>`:'<p class="mode-note">还没有切换记录</p>';
 return `<div class="mode-now">${head}</div><div class="mode-buttons" role="group" aria-label="切换模式">${buttons}</div>`+
  `<details class="mode-log"><summary>最近 ${rows.length} 次</summary><div class="mode-pop">${list}</div></details>`;
}

// talk: optional {prefetch, auto, family} booleans (null = unknown) from the say stats / speech settings.
export function quickHtml(view,talk=null){
 const v=view||{},known=v.known!==false&&typeof v.billing==='boolean',on=v.billing===true;
 const sw=`<button class="mode-billing" data-billing aria-pressed="${on}"${known?'':' disabled'} title="关 = 付费开关全关，关着时不能再打开">计费总闸 ${!known?'未知':on?'开':'关'}</button>`;
 const note=`<span class="mode-note">${!known?'状态未知':on?'付费开关可以打开':'现在不花钱'}</span>`;
 const keys=Object.entries(PRESETS).map(([n,name])=>
  `<button data-preset="${n}" title="${esc(name)}"${!known||UNDEFINED_PRESETS.has(Number(n))?' disabled':''}>${n}</button>`).join('');
 const word=b=>b===true?'开':b===false?'关':'未知';
 const t=talk?`<p class="mode-note mode-talk">提前写 ${word(talk.prefetch)} · 连续说 ${word(talk.auto)} · 家人声音 ${word(talk.family)}</p>`:'';
 // the keys fold into a small box so the bar stays one compact row on wide screens (review I2)
 return `${sw}${note}<details class="mode-presets-box"><summary>预设 0–9</summary><div class="mode-pop">`+
  `<div class="mode-presets" role="group" aria-label="预设 0–9">${keys}</div>${t}</div></details>`;
}

// The robot half mind names for a preset, in the switch bar's tier names. Anything else is dropped.
const TIERS={hearing:['local','tingwu'],vision:['upload','recognize']};
export function presetRobotSteps(robot){
 if(!robot||typeof robot!=='object')return [];
 return ['hearing','vision'].filter(k=>TIERS[k].includes(robot[k])).map(k=>[k,robot[k]]);
}

export function errorText(e){
 if(e?.name==='TimeoutError')return '结果未确认，请看状态';
 try{const j=JSON.parse(e?.message);if(j&&typeof j.error==='string')return j.error;}catch{}
 return String(e?.message||e||'出错了');
}
// Messages below are plain text: the bar writes them with textContent.
export function resultText(result){const c=result?.code;return c==null||c==='ok'?'':String(CODES[c]||c);}
export function effectsText(effects,billing){
 const e=Array.isArray(effects)?effects:[],out=[];
 if(e.includes('talk_enter'))out.push(billing===false?'计费关着：提前写、连续说没开':'提前写、连续说打开，家人声音关');
 if(e.includes('talk_leave'))out.push('提前写、连续说关');
 if(e.includes('prefetch_on'))out.push('提前写开');
 if(e.includes('prefetch_off'))out.push('提前写关');
 return out.join('；');
}
const failedText=f=>Array.isArray(f)&&f.length?`没关成：${f.map(n=>String(SWITCH_NAMES[n]||n)).join('、')}`:'';
const ROBOT_STEP={hearing:{switch:'听悟档位',control:'本体听觉'},vision:{switch:'识别开关',camera:'摄像头',m12:'M12'}};

// One preset from the page. post(path, body) -> parsed JSON or throws. robotState: /api/state (for boot), may be null.
export async function runPreset(n,{post,robotState}){
 const title=`预设 ${n} ${PRESETS[n]||''}`.trim();
 let reply;
 try{reply=await post('/api/mind/preset',{n,source:'page'});}
 catch(e){return {reply:null,failed:[],message:`${title}：${errorText(e)}`};}
 const parts=[],code=resultText(reply?.result);
 parts.push(code||'服务器那一半已做');
 const fx=effectsText(reply?.effects,reply?.snapshot?.billing);if(fx)parts.push(fx);
 const fl=failedText(reply?.failed);if(fl)parts.push(fl);
 const steps=presetRobotSteps(reply?.robot),failed=[];
 for(const [k,tier] of steps){
  const req=async r=>post(r.kind==='switch'?'/api/mind/switches':'/api/control',r.body);
  const errs=k==='hearing'?await runLevel(tier,robotState,req):await runVision(tier,robotState,req);
  for(const e of errs){
   const step=ROBOT_STEP[k][e.kind==='switch'?'switch':k==='hearing'?'control':e.action]||e.action||e.kind;
   failed.push({sense:k,step,error:e.error});
   parts.push(`${k==='hearing'?'听觉':'视觉'}（${step}）没成：${errorText(e.error)}`);
  }
 }
 if(steps.length&&!failed.length)parts.push('机器人那一半已发');
 return {reply,failed,message:`${title}：${parts.join('；')}`};
}

export function billingRequest(on){return {name:'billing',on:!on};}
export function billingMessage(r){
 const on=r?.billing?.on;
 return [on===true?'计费总闸已开':on===false?'计费总闸已关':'计费总闸状态未知',resultText(r?.result),failedText(r?.failed)].filter(Boolean).join('；');
}

async function fetchJson(fetchFn,path,ms=4000){
 try{const r=await fetchFn(path,{signal:AbortSignal.timeout(ms)});return {status:r.status,body:r.ok?await r.json():null};}
 catch{return {status:0,body:null};}
}
function poster(fetchFn){
 return async(path,body)=>{
  const r=await fetchFn(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(8000)});
  if(!r.ok)throw new Error(await r.text());
  return r.json();
 };
}
const lookOf=v=>v?.known?`${v.mode}|${v.dir}|${v.billing}`:null;

// The bar's logic without the DOM (tests drive it with a fake fetch). paint() after every change; emit(view) once per
// mode / dir / billing / seq change (modechange for the say panel). Each read carries a number and only the newest
// answer counts, so a slow old reply can't repaint an old mode or fire modechange twice (review M1).
export class ModeModel{
 constructor({fetchFn=globalThis.fetch,post=null,paint=()=>{},emit=()=>{}}={}){
  Object.assign(this,{fetchFn,paint,emit});this.post=post||poster(fetchFn);
  this.state=null;this.why='down';this.view=modeView(null);this.talk=null;this.msg='';this.pending=false;
  this.key=undefined;this.ownLook=null;this.pollSeq=0;this.talkSeq=0;
 }
 // 404 = this mind has no /mode yet: ask again in a minute instead of every 2 s (review I1)
 delay(){return this.why==='absent'?60000:2000;}
 async poll(){
  const my=++this.pollSeq;
  const r=await fetchJson(this.fetchFn,'/api/mind/mode');
  if(my!==this.pollSeq)return false;
  this.why=r.body?null:r.status===404?'absent':'down';
  this.state=r.body;this.view=modeView(r.body,this.why);
  const key=this.view.known?`${this.view.seq}|${lookOf(this.view)}`:`unknown|${this.view.why}`;
  if(key!==this.key){
   const first=this.key===undefined;this.key=key;
   // changed from elsewhere (DualKey, robot, timeout): the line about the page's last click is old now (review M2)
   if(!first&&!this.pending&&lookOf(this.view)!==this.ownLook)this.msg='';
   this.emit(this.view);
   if(this.view.known)void this.readTalk();
  }
  this.paint();return true;
 }
 async readTalk(){
  const my=++this.talkSeq;
  const [st,cfg]=await Promise.all([fetchJson(this.fetchFn,'/api/mind/say/stats?days=1'),fetchJson(this.fetchFn,'/api/mind/speech/settings')]);
  if(my!==this.talkSeq)return;
  const b=x=>typeof x==='boolean'?x:null;
  this.talk=st.body||cfg.body?{prefetch:b(st.body?.prefetch?.on),auto:b(st.body?.auto?.on),family:b(cfg.body?.speech?.round?.family)}:null;
  this.paint();
 }
 // work() -> [message, look the page itself produced (or null)]
 async act(work){
  if(this.pending)return;
  this.pending=true;this.msg='发送中…';this.paint();
  try{[this.msg,this.ownLook]=await work();}catch(e){this.msg=errorText(e);this.ownLook=null;}
  this.pending=false;this.paint();
  await this.poll();
  void this.readTalk();          // a preset may flip 提前写 / 连续说 without changing the mode or the master switch
 }
 dismiss(){if(!this.pending){this.msg='';this.paint();}}
 select(mode,dir){
  return this.act(async()=>{
   const r=await this.post('/api/mind/mode',selectRequest(mode,dir));
   const v=modeView({snapshot:r.snapshot,robot:this.state?.robot});
   return [[resultText(r.result)||`现在是 ${v.name}`,effectsText(r.effects,r.snapshot?.billing)].filter(Boolean).join('；'),lookOf(v)];
  });
 }
 preset(n){
  return this.act(async()=>{
   const robotState=(await fetchJson(this.fetchFn,'/api/state')).body;
   const out=await runPreset(n,{post:this.post,robotState});
   return [out.message,out.reply?.snapshot?lookOf(modeView({snapshot:out.reply.snapshot})):null];
  });
 }
 billing(){
  return this.act(async()=>{
   const r=await this.post('/api/mind/switches',billingRequest(this.view.billing));
   const v=this.view.known&&typeof r?.billing?.on==='boolean'?{...this.view,billing:r.billing.on}:null;
   return [billingMessage(r),lookOf(v)];
  });
 }
}

export class ModeBar{
 constructor(doc){
  this.doc=doc;this.root=doc.getElementById('modebar');if(!this.root)return;
  this.seen={main:null,quick:null};
  this.root.innerHTML='<div class="mode-main"></div><div class="mode-quick" aria-label="快捷设置"></div><p class="mode-msg" role="status"><span class="mode-msg-text"></span><button class="mode-msg-close" aria-label="关掉这行" title="关掉这行">×</button></p>';
  this.model=new ModeModel({paint:()=>this.paint(),emit:view=>doc.dispatchEvent(new CustomEvent('modechange',{detail:view}))});
  this.root.addEventListener('click',ev=>this.click(ev));
  // a click outside closes an open 预设 / 最近 box
  doc.addEventListener('click',ev=>{if(!this.root.contains(ev.target))for(const d of this.root.querySelectorAll('details[open]'))d.open=false;});
  const loop=async()=>{await this.model.poll();setTimeout(loop,this.model.delay());};
  void loop();
 }
 click(ev){
  const b=ev.target.closest('button');if(!b||b.disabled||!this.root.contains(b))return;
  if(b.classList.contains('mode-msg-close')){this.model.dismiss();return;}
  const m=this.model,done=()=>{this.seen={main:null,quick:null};this.paint();};   // rebuild: unlock the buttons
  if(b.dataset.mode)m.select(b.dataset.mode,b.dataset.dir).then(done);
  else if(b.dataset.preset!==undefined)m.preset(Number(b.dataset.preset)).then(done);
  else if(b.hasAttribute('data-billing'))m.billing().then(done);
 }
 paint(){
  const m=this.model,main=this.root.querySelector('.mode-main'),quick=this.root.querySelector('.mode-quick');
  const keepOpen=(el,html,key)=>{
   if(html===this.seen[key])return;
   const open=[...el.querySelectorAll('details')].filter(d=>d.open).map(d=>d.className);
   el.innerHTML=html;this.seen[key]=html;
   for(const d of el.querySelectorAll('details'))if(open.includes(d.className))d.open=true;
  };
  keepOpen(main,modeBarHtml(m.view,m.state?.log),'main');
  const chip=main.querySelector('.mode-chip[data-color]');
  if(chip)chip.style.setProperty('--mode-color','#'+chip.dataset.color);    // CSSOM, allowed by the CSP
  keepOpen(quick,quickHtml(m.view,m.talk),'quick');
  if(m.pending)for(const b of this.root.querySelectorAll('.mode-main button,.mode-quick button'))b.disabled=true;   // click() rebuilds them after
  const msg=this.root.querySelector('.mode-msg');msg.querySelector('.mode-msg-text').textContent=m.msg;msg.title=m.msg;
  msg.classList.toggle('empty',!m.msg);
  this.root.classList.toggle('unknown',!m.view.known);
 }
}

if(typeof document!=='undefined'&&document.getElementById('modebar'))new ModeBar(document);
