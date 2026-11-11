// Three main switches shown at the top of both pages: hearing (连续听觉), vision (M12 检测) and mind
// (情景 model calls), plus one line saying what costs money right now. Hearing and vision go to the
// robot through /api/control; mind goes to /api/mind/switch. Unknown state is never shown as "off".
const HEARING={idle:'待机',connecting:'连接后端中',listening:'监听中',enrolling:'录入声纹中',retrying:'断线重连中'};
const VISION={standby:'待机',starting:'正在开启',stopping:'正在待机',detecting:'检测中',stalled:'检测已开 · 结果过期',
 offline:'M12 离线',serial_unavailable:'串口未就绪',hot:'M12 过热暂停',disconnected:'未连接'};
const VISION_ON=new Set(['detecting','starting','stalled','hot']);

export function switchView(robot,fresh,mind){
 const t=robot?.telemetry||{},h=t.hearing||{};
 const supported=robot?.capabilities?.hearing==='supported';
 const hearingKnown=!!fresh&&supported;
 const hearing={known:hearingKnown,on:hearingKnown&&!!h.requested,
  can:hearingKnown&&!!h.ready&&!!h.url&&!!h.token_configured&&!h.enrollment?.active,
  detail:!fresh?'状态未知（数据过期）':!supported?'固件不支持':HEARING[h.state]||'—'};
 const status=robot?.m12?.status||'disconnected';
 const visionKnown=!!fresh&&status!=='disconnected';
 const vOn=visionKnown&&VISION_ON.has(status);
 const vision={known:visionKnown,on:vOn,can:visionKnown&&(vOn||(!!t.m12?.valid&&t.m12?.online!==false)),
  detail:!fresh?'状态未知（数据过期）':VISION[status]||status};
 const mindKnown=!!mind&&typeof mind.enabled==='boolean';
 const senses=hearing.on||vision.on;
 const m={known:mindKnown,on:mindKnown&&mind.enabled,can:mindKnown,
  detail:!mindKnown?'情景服务未连接':!mind.enabled?'不调用模型':senses?'每分钟最多一次模型调用':'感官都关 · 不调用模型'};
 let cost;
 if(!hearingKnown||!mindKnown)cost='花费状态未知';
 else{
  const paying=[];
  if(hearing.on)paying.push('听悟（约 0.9 元/小时）');
  if(m.on&&senses)paying.push('情景模型');
  cost=paying.length?'正在花钱：'+paying.join(' + '):'现在不花钱';
 }
 return {hearing,vision,mind:m,cost};
}

export function toggleRequest(which,robot,mindOrFresh){
 if(which==='mind')return {kind:'mind',body:{enabled:!(mindOrFresh?.enabled)}};
 const v=switchView(robot,mindOrFresh,null);
 if(which==='hearing')return {kind:'control',body:{action:'hearing_listen',enabled:!v.hearing.on,boot:robot?.boot}};
 return {kind:'control',body:{action:'m12',enabled:!v.vision.on,boot:robot?.boot}};
}

const NAMES={hearing:'听觉',vision:'视觉',mind:'情景'};
async function postJson(path,body){
 const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(8000)});
 if(!r.ok)throw new Error(await r.text());
 return r.json();
}
export class SwitchBar{
 // pollRobot: the page has no event stream of its own (mind.html), so read /api/state every 2 s.
 constructor(doc,{pollRobot=false}={}){
  this.doc=doc;this.robot=null;this.fresh=false;this.mind=null;this.pending=null;this.msg='';
  const root=doc.getElementById('switches');
  root.innerHTML=['hearing','vision','mind'].map(k=>`<button class="switch" id="sw-${k}" aria-pressed="false" disabled><b>${NAMES[k]}</b><span id="sw-${k}-state">—</span><small id="sw-${k}-detail">—</small></button>`).join('')+
   '<span id="sw-cost" class="switch-cost" role="status">—</span>';
  for(const k of ['hearing','vision','mind'])doc.getElementById('sw-'+k).addEventListener('click',()=>this.toggle(k));
  this.pollMind();setInterval(()=>this.pollMind(),5000);
  if(pollRobot){this.pollRobot();setInterval(()=>this.pollRobot(),2000);}
 }
 async pollMind(){
  try{const r=await fetch('/api/mind/state',{signal:AbortSignal.timeout(8000)});this.mind=r.ok?await r.json():null;}catch{this.mind=null;}
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
 paint(){
  const v=switchView(this.robot,this.fresh,this.mind),$=id=>this.doc.getElementById(id);
  for(const k of ['hearing','vision','mind']){
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
