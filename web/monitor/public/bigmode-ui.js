// The three big modes (owner 2026-11-20, collector-0.5.21): 关 / 收集 / 智能, owned and stored by the robot
// (telemetry big_mode, /v1/control target big_mode). Choosing one: the robot saves it, switches its own senses and
// sends its digit (0 / 2 / 9) to mind, which sets the server's switches. The small switches under 详细设置 are
// temporary: every robot power-on applies the big mode again.
export const BIG_MODES=[
 {id:'off',name:'关',detail:'什么都不上传，不花钱'},
 {id:'collect',name:'收集',detail:'上传声音和有变化的画面，只做免费处理；她听，但不说话'},
 {id:'smart',name:'智能',detail:'全部打开，包括付费的识别和对话；她会说话'},
];
const IDS=BIG_MODES.map(m=>m.id);

export function bigModeView(robot,fresh){
 const sup=robot?.capabilities?.big_mode==='supported',mode=robot?.telemetry?.big_mode?.mode;
 const known=sup&&IDS.includes(mode);
 const m=BIG_MODES.find(x=>x.id===mode);
 return {known,mode:known?mode:null,can:known&&!!fresh,
  detail:!sup?'要 0.5.21 固件（机器人自己记住大模式）':!known?'机器人还没报上来':`${m.name}：${m.detail}`+(fresh?'':' · 机器人状态不是最新的')};
}

export function bigModeRequest(mode,robot){
 if(!IDS.includes(mode))throw new Error('未知大模式');
 return {action:'big_mode',mode,boot:robot?.boot};
}

export class BigModeBar{
 constructor(doc){
  this.doc=doc;this.root=doc.getElementById('bigmode');this.robot=null;this.fresh=false;this.pending=null;this.msg='';
  if(!this.root)return;
  this.root.innerHTML='<div class="bigmode-buttons" role="group" aria-label="大模式">'+
   BIG_MODES.map(m=>`<button id="bm-${m.id}" class="bigmode-${m.id}" aria-pressed="false" disabled><b>${m.name}</b><small>${m.detail}</small></button>`).join('')+
   '</div><p id="bm-detail" class="bigmode-detail" role="status">—</p>';
  for(const m of BIG_MODES)doc.getElementById('bm-'+m.id).addEventListener('click',()=>this.choose(m.id));
 }
 render(robot,fresh){this.robot=robot;this.fresh=fresh;this.paint();}
 async choose(mode){
  if(this.pending||!this.root)return;
  this.pending=mode;this.msg='';this.paint();
  try{
   const r=await fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(bigModeRequest(mode,this.robot)),signal:AbortSignal.timeout(8000)});
   if(!r.ok)throw new Error(await r.text());
  }catch(e){this.msg=`大模式：${e.name==='TimeoutError'?'结果未确认，请看状态':e.message}`;}
  this.pending=null;this.paint();
 }
 paint(){
  if(!this.root)return;
  const v=bigModeView(this.robot,this.fresh);
  for(const m of BIG_MODES){
   const b=this.doc.getElementById('bm-'+m.id);
   b.setAttribute('aria-pressed',String(v.mode===m.id));b.classList.toggle('on',v.mode===m.id);
   b.disabled=!!this.pending||!v.can;
  }
  this.doc.getElementById('bm-detail').textContent=this.pending?'发送中…':this.msg||v.detail;
 }
}
