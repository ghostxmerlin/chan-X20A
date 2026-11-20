// Mode bar and quick settings under the switch bar on both pages. Since 2026-11-20 (owner: Providence) Docking is the
// only mode: the bar shows a fixed Docking badge (from hello's telemetry, else mind's last record) and, while a
// firmware older than collector-0.5.19 still reports one of the removed modes, a note naming it. Nothing here switches
// modes. The billing switch and presets 0 / 9 stay on mind: the switch = preset 9 (on) / preset 0 (off), the last whole
// action, gating nothing (owner 2026-11-14 evening); single switches always work; 1-8 are empty. A preset sends its
// server half first, then runs the robot half mind names with the switch bar's own tier requests (runLevel /
// runVision): no queue, no retry, each failed step is written out. <nav id="modebar"> starts it.
import { runLevel, runVision } from '/switches-ui.js';

const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const hhmmss=ms=>Number.isFinite(ms)?new Date(ms).toLocaleTimeString('zh-CN',{hour12:false}):'—';

// Corner label and colour: the same as server/mind/mode_table.py MODES[*].look (mode-ui.test.mjs checks it).
export const LOOK={crowd:['DOCK','30D060']};
// The modes removed on 2026-11-20, by their old names: only to say what an old firmware still reports.
const OLD_NAMES={talk:'脱口秀',chat:'对话',settings:'设置',event:'事件驱动',subtitle:'字幕',companion:'陪伴',idle:'IDLE'};
const SOURCES={asr:'口令',touch:'触摸',select:'上滑页',quick:'快捷设置',dualkey:'DualKey',page:'网页',boot:'开机',timeout:'超时'};
export const PRESETS={0:'花费全关',1:'空',2:'空',3:'空',4:'空',5:'空',6:'空',7:'空',8:'空',9:'花费全开'};
const MIND_PRESETS=new Set([0,9]);
const CODES={ok:'',already:'已经是 Docking 了',not_in_mode:'不在这个模式里',only_talk:'脱口秀已经没有了',
 busy:'正在忙，没执行',too_soon:'太快了，等一会儿再按',mouth_not_tts:'嘴没切到 TTS',not_implemented:'还没做',
 undefined_preset:'这个键还没定义',stale:'太旧，丢掉了',unknown_input:'没有这个输入',no_action:'这个输入没有动作',
 senses_on_page:'机器人那一半要在网页上按',on_hello:'模式在 hello 上切',removed_mode:'只有 Docking 模式',asr_off:'语音口令已关'};
const SWITCH_NAMES={tingwu:'听悟',eye:'识别',notes:'纪要',mind:'情景',scene:'情景',prefetch:'提前写',auto:'连续说',speech:'说话',family:'家人声音',talk:'提前写 / 连续说',cancel:'停说',mouth:'嘴'};
const modeName=mode=>mode==='crowd'?'Docking':OLD_NAMES[mode]||String(mode??'—');
export const NO_MODES='hello 固件还不支持模式（等 0.4.92）';

// /api/state -> hello's own mode report. ok: online, fresh telemetry with a known telemetry.mode ({mode, dir, host,
// return_to, seq, label, by, since_ms = how long ago}, or the id alone; an old firmware's removed mode counts);
// unsupported: online and fresh but no mode in it; offline: anything else.
export function robotMode(st){
 if(!st||st.online!==true||st.fresh?.telemetry!==true||!st.telemetry||typeof st.telemetry!=='object')return {status:'offline',boot:null,mode:null};
 const raw=st.telemetry.mode,o=typeof raw==='string'?{mode:raw}:raw;
 if(!o||typeof o!=='object'||!(o.mode==='crowd'||Object.hasOwn(OLD_NAMES,o.mode)))return {status:'unsupported',boot:st.boot||null,mode:null};
 const at=Number.isFinite(st.receivedAt)&&st.receivedAt>0?st.receivedAt:Number.isFinite(st.now)?st.now:null;
 const since=Number.isFinite(o.since)?o.since:Number.isFinite(o.since_ms)&&at!==null?at-o.since_ms:null;
 return {status:'ok',boot:st.boot||null,mode:{...o,since}};
}

// (GET /mode reply, robotMode()) -> what the bar shows: always Docking when anything is known. oldMode: the removed
// mode an old firmware still reports (shown as a note), else null. why (mind only): 'absent' = mind has no /mode
// yet (404); 'down' = not reached.
export function modeView(state,robot=null,why=null){
 const r=robot||{status:'offline',boot:null,mode:null};
 const s=state?.snapshot,mindOk=!!s&&s.mode==='crowd';
 const billing=mindOk&&typeof s.billing==='boolean'?s.billing:null;
 const o=r.status==='ok'?r.mode:mindOk?s:null;
 if(!o)return {known:false,why:why==='absent'?'absent':'down',robot:r.status,source:null,boot:r.boot,mode:null,
  name:'模式未知',label:'',color:null,since:null,by:null,billing:null,oldMode:null,seq:null};
 const [label,color]=LOOK.crowd;
 return {known:true,why:null,robot:r.status,source:r.status==='ok'?'hello':'mind',boot:r.boot,mode:'crowd',name:'Docking',
  label,color,since:Number.isFinite(o.since)?o.since:null,by:o.by??null,billing,oldMode:o.mode!=='crowd'?o.mode:null,
  seq:Number.isInteger(o.seq)?o.seq:null};
}

export function logLine(r){
 const what=r?.input==='billing'?`计费 ${r.value?'全开':'全关'}`:r?.input==='digit'?`预设 ${esc(r.value)}`:
  `${esc(modeName(r?.to))}（${esc(r?.input)}）`;
 const code=r?.code&&r.code!=='ok'?` · ${esc(CODES[r.code]||r.code)}`:'';
 return `${esc(hhmmss(r?.at))} ${esc(SOURCES[r?.source]||r?.source)} · ${what}${code}`;
}

export function modeBarHtml(view,log){
 const v=view||modeView(null);
 const head=!v.known?(v.robot==='unsupported'?`<span class="mode-name">${NO_MODES}</span><span class="mode-note">情景服务也没连上</span>`:
   v.why==='absent'?'<span class="mode-name">mind 还没有模式功能（等上线）</span>':
   '<span class="mode-name">模式未知（hello 和情景服务都没连上）</span>'):
  `<span class="mode-chip"${v.color?` data-color="${v.color}"`:''}>${esc(v.label)}</span>`+
  `<span class="mode-name">${esc(v.name)}</span>`+
  `<span class="mode-note">${esc(hhmmss(v.since))} · ${esc(SOURCES[v.by]||v.by||'—')}</span>`+
  (v.source==='hello'?'<span class="mode-sync mode-sync-yes">hello 上的模式</span>':
   `<span class="mode-sync mode-sync-offline">${v.robot==='unsupported'?NO_MODES:'hello 没连上：这是 mind 最后记下的'}</span>`)+
  (v.oldMode?`<span class="mode-warn" role="note">机器人还是旧固件：报的是「${esc(modeName(v.oldMode))}」，当作 Docking</span>`:'');
 const rows=(Array.isArray(log)?log:[]).slice(0,10);
 const list=rows.length?`<ol>${rows.map(r=>`<li>${logLine(r)}</li>`).join('')}</ol>`:'<p class="mode-note">还没有记录</p>';
 return `<div class="mode-now">${head}</div>`+
  `<details class="mode-log"><summary>最近 ${rows.length} 次</summary><div class="mode-pop">${list}</div></details>`;
}

export function quickHtml(view){
 const v=view||{},known=typeof v.billing==='boolean',on=v.billing===true;
 const sw=`<button class="mode-billing" data-billing aria-pressed="${on}"${known?'':' disabled'} title="点一下 = ${on?'预设 0 花费全关':'预设 9 花费全开'}；单个开关随时可以单独点">计费 ${!known?'未知':on?'全开':'全关'}</button>`;
 const note=`<span class="mode-note">${!known?'状态未知':on?'上次整体：全开':'上次整体：全关（单个开关照样能开）'}</span>`;
 const keys=Object.entries(PRESETS).map(([k,name])=>{
  const n=Number(k);
  if(MIND_PRESETS.has(n))return `<button data-preset="${n}" title="${esc(name)}"${known?'':' disabled'}>${n}</button>`;
  return `<button data-preset="${n}" title="空" disabled>${n}</button>`;
 }).join('');
 // the keys fold into a small box so the bar stays one compact row on wide screens (review I2)
 return `${sw}${note}<details class="mode-presets-box"><summary>预设 0–9</summary><div class="mode-pop">`+
  `<div class="mode-presets" role="group" aria-label="预设 0–9">${keys}</div></div></details>`;
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
export function effectsText(effects){
 const e=Array.isArray(effects)?effects:[];
 return e.includes('talk_leave')?'提前写、连续说关':'';      // talk is gone (2026-11-20): only its turn-off can still come
}
const failedText=f=>Array.isArray(f)&&f.length?`这几步没成：${f.map(n=>String(SWITCH_NAMES[n]||n)).join('、')}`:'';
const ROBOT_STEP={hearing:{switch:'听悟档位',control:'本体听觉'},vision:{switch:'识别开关',camera:'摄像头',m12:'M12'}};

// One preset from the page. post(path, body) -> parsed JSON or throws. robotState: /api/state (for boot), may be null.
export async function runPreset(n,{post,robotState}){
 const title=`预设 ${n} ${PRESETS[n]||''}`.trim();
 let reply;
 try{reply=await post('/api/mind/preset',{n,source:'page'});}
 catch(e){return {reply:null,failed:[],message:`${title}：${errorText(e)}`};}
 const parts=[],code=resultText(reply?.result);
 parts.push(code||'服务器那一半已做');
 const fx=effectsText(reply?.effects);if(fx)parts.push(fx);
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

// The billing switch: on -> preset 0, off (or after a 0) -> preset 9; the same as pressing 0 / 9.
export function billingPreset(on){return on?0:9;}

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
const lookOf=v=>v?.known?`${v.billing}|${v.oldMode}`:null;
const OWN_MS=6000;                 // a change this soon after the page's own click is that click's answer
const MIND_ABSENT_MS=60000;        // mind without /mode (404): ask it again only once a minute (review I1)

// The bar's logic without the DOM (tests drive it with a fake fetch). paint() after every change; emit(view) once per
// mode / dir / billing / switchability change (modechange for the say panel). Each read carries a number and only the
// newest answer counts, so a slow old reply can't repaint an old mode or fire modechange twice (review M1).
export class ModeModel{
 // robotSource() -> the newest /api/state snapshot the page already has (the bar's event stream), or null: fetch it
 constructor({fetchFn=globalThis.fetch,post=null,paint=()=>{},emit=()=>{},now=Date.now,robotSource=()=>null}={}){
  Object.assign(this,{fetchFn,paint,emit,now,robotSource});this.post=post||poster(fetchFn);
  this.state=null;this.robot=robotMode(null);this.robotState=null;this.why='down';this.view=modeView(null);this.msg='';this.pending=false;
  this.key=undefined;this.ownAt=-Infinity;this.pollSeq=0;this.mindAt=-Infinity;
 }
 delay(){return 2000;}
 async poll(){
  const my=++this.pollSeq,askMind=this.why!=='absent'||this.now()-this.mindAt>=MIND_ABSENT_MS;
  const have=this.robotSource();
  const [m,r]=await Promise.all([askMind?fetchJson(this.fetchFn,'/api/mind/mode'):null,have?{body:have}:fetchJson(this.fetchFn,'/api/state')]);
  if(my!==this.pollSeq)return false;
  if(m){this.mindAt=this.now();this.why=m.body?null:m.status===404?'absent':'down';this.state=m.body;}
  this.robotState=r.body;this.robot=robotMode(r.body);this.view=modeView(this.state,this.robot,this.why);
  const key=this.view.known?`${this.view.source}|${this.view.seq}|${lookOf(this.view)}`:`unknown|${this.view.why}|${this.view.robot}`;
  if(key!==this.key){
   const first=this.key===undefined;this.key=key;
   // changed from elsewhere (hello's own words or touch, timeout): the line about the page's last click is old now
   if(!first&&!this.pending&&this.now()-this.ownAt>OWN_MS)this.msg='';
   this.emit(this.view);
  }
  this.paint();return true;
 }
 // work() -> the message
 async act(work){
  if(this.pending)return;
  this.pending=true;this.msg='发送中…';this.paint();
  try{this.msg=await work();}catch(e){this.msg=errorText(e);}
  this.pending=false;this.ownAt=this.now();this.paint();
  await this.poll();
 }
 dismiss(){if(!this.pending){this.msg='';this.paint();}}
 preset(n){
  return this.act(async()=>{
   const robotState=(await fetchJson(this.fetchFn,'/api/state')).body;
   return (await runPreset(n,{post:this.post,robotState})).message;
  });
 }
 billing(){return this.preset(billingPreset(this.view.billing===true));}
}

export class ModeBar{
 constructor(doc){
  this.doc=doc;this.root=doc.getElementById('modebar');if(!this.root)return;
  this.seen={main:null,quick:null};
  this.root.innerHTML='<div class="mode-main"></div><div class="mode-quick" aria-label="快捷设置"></div><p class="mode-msg" role="status"><span class="mode-msg-text"></span><button class="mode-msg-close" aria-label="关掉这行" title="关掉这行">×</button></p>';
  // Its own event stream (camera off): the monitor polls hello only while someone watches, and mind.html has no other
  // stream; the snapshots it sends are the bar's view of hello (newest within 3 s, else /api/state is asked).
  this.live=null;
  if(typeof EventSource!=='undefined'){
   const es=new EventSource('/api/events?camera=0&servo=0');
   es.onmessage=ev=>{try{this.live={at:Date.now(),state:JSON.parse(ev.data)};}catch{}};
  }
  const robotSource=()=>this.live&&Date.now()-this.live.at<3000?this.live.state:null;
  this.model=new ModeModel({robotSource,paint:()=>this.paint(),emit:view=>doc.dispatchEvent(new CustomEvent('modechange',{detail:view}))});
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
  if(b.dataset.preset!==undefined)m.preset(Number(b.dataset.preset)).then(done);
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
  keepOpen(quick,quickHtml(m.view),'quick');
  if(m.pending)for(const b of this.root.querySelectorAll('.mode-main button,.mode-quick button'))b.disabled=true;   // click() rebuilds them after
  const msg=this.root.querySelector('.mode-msg');msg.querySelector('.mode-msg-text').textContent=m.msg;msg.title=m.msg;
  msg.classList.toggle('empty',!m.msg);
  this.root.classList.toggle('unknown',!m.view.known);
 }
}

if(typeof document!=='undefined'&&document.getElementById('modebar'))new ModeBar(document);
