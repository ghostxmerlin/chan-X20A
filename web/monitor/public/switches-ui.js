// Main switches shown at the top of both pages: hearing in three tiers (关 / 本地 free / 听悟 paid),
// vision (M12 检测) and mind (情景 model calls), plus one line saying what costs money right now.
// Hearing on/off and vision go to the robot through /api/control; the 听悟 tier is the server switch
// (/api/mind/switches, spec 2026-11-11 §3); mind goes to /api/mind/switch. Unknown is never "off".
const HEARING={idle:'待机',connecting:'连接后端中',listening:'监听中',enrolling:'录入声纹中',retrying:'断线重连中'};
const VISION={standby:'待机',starting:'正在开启',stopping:'正在待机',detecting:'检测中',stalled:'检测已开 · 结果过期',
 offline:'M12 离线',serial_unavailable:'串口未就绪',hot:'M12 过热暂停',disconnected:'未连接'};
const VISION_ON=new Set(['detecting','starting','stalled','hot']);

export function switchView(robot,fresh,mind,switches=null){
 const t=robot?.telemetry||{},h=t.hearing||{};
 const supported=robot?.capabilities?.hearing==='supported';
 const hearingKnown=!!fresh&&supported;
 const swKnown=!!switches?.tingwu&&!!switches?.tingwu_status,st=switches?.tingwu_status||{};
 const tingwuOn=swKnown&&switches.tingwu.on===true;
 const tingwuAvailable=swKnown&&typeof st.available==='boolean'?st.available:null;
 const tingwuRunning=!swKnown||st.stale?null:(st.running||[]).length>0;
 const on=hearingKnown&&!!h.requested;
 const level=!hearingKnown?'unknown':!on?'off':!swKnown?'unknown':tingwuOn?'tingwu':'local';
 const base=HEARING[h.state]||'—';
 const hearing={known:hearingKnown,on,level,tingwuOn,tingwuRunning,tingwuAvailable,switchesKnown:swKnown,
  can:hearingKnown&&!!h.ready&&!!h.url&&!!h.token_configured&&!h.enrollment?.active,
  detail:!fresh?'状态未知（数据过期）':!supported?'固件不支持':
   level==='tingwu'?(tingwuAvailable===false?'服务器未配置听悟':tingwuRunning===null?`${base} · 听悟状态未知`:tingwuRunning?`${base} · 听悟在跑`:`${base} · 正在开启听悟…`):
   level==='local'?`${base} · 本地（免费）`:level==='unknown'?`${base} · 档位未知`:base};
 const status=robot?.m12?.status||'disconnected';
 const visionKnown=!!fresh&&status!=='disconnected';
 const vOn=visionKnown&&VISION_ON.has(status);
 const camKnown=typeof t.health?.camera_enabled==='boolean',camOn=t.health?.camera_enabled===true;
 const vLevel=!visionKnown||!camKnown?'unknown':camOn&&vOn?'upload':vOn?'m12_only':camOn?'camera_only':'off';
 const vLevelAny=vLevel==='unknown'&&!!fresh&&camKnown?(camOn?'camera_only':'off'):vLevel;   // M12 link down: the camera alone
 const eyeKnown=!!switches?.eye&&!!switches?.eye_status,es=switches?.eye_status||{};
 const eyeOn=eyeKnown&&switches.eye.on===true;
 const eyeAvailable=eyeKnown&&typeof es.available==='boolean'?es.available:null;
 const eyeRunning=!eyeKnown||es.stale?null:es.running===true;
 const vision={known:visionKnown&&camKnown,offKnown:!!fresh&&(camKnown||visionKnown),on:vOn,
  level:vLevelAny==='upload'&&eyeOn?'recognize':vLevelAny,camera:camOn,eyeKnown,eyeOn,eyeAvailable,eyeRunning,
  can:visionKnown&&(vOn||(!!t.m12?.valid&&t.m12?.online!==false)),
  detail:!fresh?'状态未知（数据过期）':`${VISION[status]||status} · 摄像头${!camKnown?'未知':camOn?'开（变化时上传）':'关'}`};
 const mindKnown=!!mind&&typeof mind.enabled==='boolean';
 const senses=hearing.on||vision.on;
 const m={known:mindKnown,on:mindKnown&&mind.enabled,can:mindKnown,
  detail:!mindKnown?'情景服务未连接':!mind.enabled?'不调用模型':senses?'每分钟最多一次模型调用':'感官都关 · 不调用模型'};
 let cost;
 if(!hearingKnown||!mindKnown)cost='花费状态未知';
 else{
  const paying=[];
  if(tingwuRunning===true)paying.push('听悟（约 0.9 元/小时）');
  const tingwuUnknown=tingwuRunning===null&&hearing.on&&(tingwuOn||!swKnown)&&tingwuAvailable!==false;
  if(m.on&&senses)paying.push('情景模型');
  const eyeSelected=eyeOn&&camOn&&eyeAvailable!==false;
  if(eyeSelected&&eyeRunning===true)paying.push('视觉识别（每分钟最多一次）');
  const unknown=[tingwuUnknown&&'听悟状态未知',eyeSelected&&eyeRunning===null&&'识别状态未知'].filter(Boolean).join('；');
  cost=paying.length?'正在花钱：'+paying.join(' + ')+(unknown?'；'+unknown:''):unknown||'现在不花钱';
 }
 return {hearing,vision,mind:m,cost};
}

// Hearing tiers: set the 听悟 switch first, so a connection that starts is already in the right tier.
export function levelRequests(level,robot){
 const listen=enabled=>({kind:'control',body:{action:'hearing_listen',enabled,boot:robot?.boot}});
 const tier=on=>({kind:'switch',body:{name:'tingwu',on}});
 if(level==='off')return [tier(false),listen(false)];    // the paid tier first: works even if the robot is unreachable
 return [tier(level==='tingwu'),listen(true)];
}

// Vision tiers (step 2): 上传 = body camera on (frames to the server on change) + M12 detection.
export function visionRequests(level,robot){
 const c=(action,enabled)=>({kind:'control',body:{action,enabled,boot:robot?.boot}});
 const eye=on=>({kind:'switch',body:{name:'eye',on}});         // the paid part first, so 关 stops it even if the robot is gone
 if(level==='recognize')return [eye(true),c('camera',true),c('m12',true)];
 if(level==='upload')return [eye(false),c('camera',true),c('m12',true)];
 return [eye(false),c('camera',false),c('m12',false)];
}
export function visionButtons(v,pending){
 const s=v.vision;
 return {off:!!pending||!(s.offKnown||(s.eyeKnown&&s.eyeOn)),upload:!!pending||!s.known||!s.can,
  recognize:!!pending||!s.known||!s.can||!s.eyeKnown||s.eyeAvailable===false};
}
async function runRequests(reqs,keepGoing,post){
 const errors=[];
 for(const req of reqs){
  try{await post(req);}
  catch(e){errors.push({kind:req.kind,action:req.body?.action,error:e});if(!keepGoing)break;}
 }
 return errors;
}

// Send a tier's requests in order. "off" tries every step; turning on stops at the first failure so
// hearing never starts in an unknown tier. Returns [{kind, error}] for the failed steps.
export async function runLevel(level,robot,post){
 return runRequests(levelRequests(level,robot),level==='off',post);
}
export async function runVision(level,robot,post){
 return runRequests(visionRequests(level,robot),level==='off',post);
}

// Disabled state of the three tier buttons. 关 needs only the server switch or the robot to be known.
export function levelButtons(v,pending){
 const h=v.hearing;
 return {off:!!pending||(!h.known&&!h.switchesKnown),
  local:!!pending||!h.known||!h.can||!h.switchesKnown,
  tingwu:!!pending||!h.known||!h.can||!h.switchesKnown||h.tingwuAvailable===false};
}

export function toggleRequest(which,robot,mindOrFresh){
 if(which==='mind')return {kind:'mind',body:{enabled:!(mindOrFresh?.enabled)}};
 const v=switchView(robot,mindOrFresh,null);
 if(which==='hearing')return {kind:'control',body:{action:'hearing_listen',enabled:!v.hearing.on,boot:robot?.boot}};
 return {kind:'control',body:{action:'m12',enabled:!v.vision.on,boot:robot?.boot}};
}

const NAMES={hearing:'听觉',vision:'视觉',mind:'情景'};
const LEVELS=[['off','关'],['local','本地'],['tingwu','听悟']];
const VLEVELS=[['off','关'],['upload','上传'],['recognize','识别']];
async function postJson(path,body){
 const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(8000)});
 if(!r.ok)throw new Error(await r.text());
 return r.json();
}
export class SwitchBar{
 // pollRobot: the page has no event stream of its own (mind.html), so read /api/state every 2 s.
 constructor(doc,{pollRobot=false}={}){
  this.doc=doc;this.robot=null;this.fresh=false;this.mind=null;this.switches=null;this.pending=null;this.msg='';
  const root=doc.getElementById('switches');
  const hearing='<div class="switch switch-group" id="sw-hearing" role="group" aria-label="听觉档位"><b>听觉</b><span id="sw-hearing-state">—</span>'+
   LEVELS.map(([k,label])=>`<button id="sw-hearing-${k}" aria-pressed="false" disabled>${label}</button>`).join('')+'<small id="sw-hearing-detail">—</small></div>';
  const vision='<div class="switch switch-group" id="sw-vision" role="group" aria-label="视觉档位"><b>视觉</b><span id="sw-vision-state">—</span>'+
   VLEVELS.map(([k,label])=>`<button id="sw-vision-${k}" aria-pressed="false" disabled${k==='recognize'?' title="每分钟用 haiku 看一次画面（约 2 元/天）"':''}>${label}</button>`).join('')+'<small id="sw-vision-detail">—</small></div>';
  root.innerHTML=hearing+vision+['mind'].map(k=>`<button class="switch" id="sw-${k}" aria-pressed="false" disabled><b>${NAMES[k]}</b><span id="sw-${k}-state">—</span><small id="sw-${k}-detail">—</small></button>`).join('')+
   '<span id="sw-cost" class="switch-cost" role="status">—</span>';
  for(const [k] of LEVELS)doc.getElementById('sw-hearing-'+k).addEventListener('click',()=>this.setLevel(k));
  for(const [k] of VLEVELS)doc.getElementById('sw-vision-'+k).addEventListener('click',()=>this.setVision(k));
  doc.getElementById('sw-mind').addEventListener('click',()=>this.toggle('mind'));
  this.pollMind();setInterval(()=>this.pollMind(),5000);
  if(pollRobot){this.pollRobot();setInterval(()=>this.pollRobot(),2000);}
 }
 async pollMind(){
  const get=async p=>{try{const r=await fetch(p,{signal:AbortSignal.timeout(8000)});return r.ok?await r.json():null;}catch{return null;}};
  [this.mind,this.switches]=await Promise.all([get('/api/mind/state'),get('/api/mind/switches')]);
  this.paint();
 }
 async pollRobot(){
  try{const r=await fetch('/api/state',{signal:AbortSignal.timeout(4000)});const s=r.ok?await r.json():null;this.render(s,!!s?.online&&!!s?.fresh?.telemetry);}
  catch{this.render(null,false);}
 }
 render(robot,fresh){this.robot=robot;this.fresh=fresh;this.paint();}
 async toggle(which){
  if(this.pending)return;
  const req=toggleRequest(which,this.robot,which==='mind'?this.mind:this.fresh);
  this.pending=which;this.msg='';this.paint();
  try{
   if(req.kind==='mind'){const r=await postJson('/api/mind/switch',req.body);this.mind={...(this.mind||{}),enabled:r.enabled};}
   else await postJson('/api/control',req.body);
  }catch(e){this.msg=`${NAMES[which]}：${e.name==='TimeoutError'?'结果未确认，请看状态':e.message}`;}
  this.pending=null;this.paint();
 }
 async setLevel(level){
  if(this.pending)return;
  this.pending='hearing';this.msg='';this.paint();
  const steps={switch:'听悟档位',control:'本体听觉'};
  const errors=await runLevel(level,this.robot,async req=>{
   if(req.kind==='switch')this.switches=await postJson('/api/mind/switches',req.body);
   else await postJson('/api/control',req.body);
  });
  this.msg=errors.map(({kind,error})=>`听觉（${steps[kind]}）：${error.name==='TimeoutError'?'结果未确认，请看状态':error.message}`).join('；');
  this.pending=null;this.paint();
 }
 async setVision(level){
  if(this.pending)return;
  this.pending='vision';this.msg='';this.paint();
  const errors=await runVision(level,this.robot,async req=>{
   if(req.kind==='switch')this.switches=await postJson('/api/mind/switches',req.body);
   else await postJson('/api/control',req.body);
  });
  const step=e=>e.kind==='switch'?'识别开关':e.action==='camera'?'摄像头':'M12';
  this.msg=errors.map(e=>`视觉（${step(e)}）：${e.error.name==='TimeoutError'?'结果未确认，请看状态':e.error.message}`).join('；');
  this.pending=null;this.paint();
 }
 paint(){
  const v=switchView(this.robot,this.fresh,this.mind,this.switches),$=id=>this.doc.getElementById(id);
  const hv=v.hearing,g=$('sw-hearing');
  g.classList.toggle('on',hv.on);g.classList.toggle('unknown',!hv.known||hv.level==='unknown');
  const dis=levelButtons(v,this.pending);
  for(const [k] of LEVELS){
   const b=$('sw-hearing-'+k);b.setAttribute('aria-pressed',String(hv.level===k));b.disabled=dis[k];
  }
  $('sw-hearing-state').textContent=this.pending==='hearing'?'发送中…':{off:'关',local:'本地',tingwu:'听悟',unknown:'未知'}[hv.level];
  $('sw-hearing-detail').textContent=hv.detail;
  const vv=v.vision,vg=$('sw-vision'),vdis=visionButtons(v,this.pending);
  vg.classList.toggle('on',vv.level==='upload'||vv.level==='recognize');vg.classList.toggle('unknown',!vv.known);
  for(const [k] of VLEVELS){const b=$('sw-vision-'+k);b.setAttribute('aria-pressed',String(vv.level===k));b.disabled=vdis[k];}
  $('sw-vision-state').textContent=this.pending==='vision'?'发送中…':{off:'关',upload:'上传',recognize:'识别',m12_only:'只有 M12',camera_only:'只有摄像头',unknown:'未知'}[vv.level];
  $('sw-vision-detail').textContent=vv.detail;
  for(const k of ['mind']){
   const s=v[k],b=$('sw-'+k);
   b.setAttribute('aria-pressed',String(s.on));b.classList.toggle('on',s.on);b.classList.toggle('unknown',!s.known);
   b.disabled=!!this.pending||!s.can;
   $('sw-'+k+'-state').textContent=this.pending===k?'发送中…':!s.known?'未知':s.on?'开':'关';
   $('sw-'+k+'-detail').textContent=s.detail;
  }
  const c=$('sw-cost');c.textContent=this.msg||v.cost;c.classList.toggle('paying',v.cost.startsWith('正在花钱'));
 }
}

// mind.html has no event stream: <div id="switches" data-poll="robot"> makes the bar read /api/state itself.
if(typeof document!=='undefined'){const el=document.getElementById('switches');if(el?.dataset.poll==='robot')new SwitchBar(document,{pollRobot:true});}
