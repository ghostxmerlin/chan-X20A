const states={idle:'待机',connecting:'连接后端中',listening:'连续监听中',enrolling:'录入声纹中',retrying:'断线，等待重连'};
const emotions={happy:'开心',sad:'低落',angry:'生气',fearful:'害怕',disgusted:'嫌弃',surprised:'惊讶',neutral:'平静',unknown:'未确定'};
const phases={idle:'尚未录入',connecting:'连接后端中',started:'请面对 StackChan 自然说话',progress:'继续说下一句',done:'声纹已保存',cancelled:'录入已取消',error:'录入失败'};
export class HearingPanel {
 constructor(doc,send=async body=>{
  const response=await fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(7000)});
  if(!response.ok)throw new Error(await response.text());return response.json();
 }){
  this.doc=doc;this.send=send;this.pending=false;
  for(const [id,op,params] of [['save','config',{}],['import','import',{}],['start','listen',{enabled:true}],['stop','listen',{enabled:false}],['enroll','enroll',{}],['cancel','cancel',{}]])
   this.el('hearing-'+id).addEventListener('click',()=>this.act(op,params));
 }
 el(id){return this.doc.getElementById(id);}
 async act(op,params={}){
  if(!this.ready||this.pending)return;
  if(op==='config')params={url:this.el('hearing-url').value.trim(),token:this.el('hearing-token').value};
  if(op==='enroll'){
   const name=this.el('hearing-name').value.trim();
   if(!name){this.el('hearing-command').textContent='请先填写姓名';return;}
   params={name,replace:this.el('hearing-replace').checked};
  }
  const boot=this.state.boot;this.pending=true;this.el('hearing-command').textContent='正在发送…';this.render(this.state,this.fresh);
  try{
   const result=await this.send({action:'hearing_'+op,boot,...params});
   this.el('hearing-command').textContent=this.state.boot===boot?result.message:'机器人已重启，请核对当前状态';
  }catch(e){this.el('hearing-command').textContent=e.name==='TimeoutError'?'结果未确认，请查看状态；不会自动重发':e.message;}
  finally{
   if(op==='config')this.el('hearing-token').value='';
   this.pending=false;this.render(this.state,this.fresh);
  }
 }
 render(state,fresh){
  this.state=state;this.fresh=fresh;
  const h=state?.telemetry?.hearing||{},enrollment=h.enrollment||{};
  const supported=state?.capabilities?.hearing==='supported';
  this.ready=!!fresh&&supported&&!!h.ready;
  if(fresh&&supported&&this.loadedBoot!==state.boot){this.el('hearing-url').value=h.url||'';this.el('hearing-token').value='';this.loadedBoot=state.boot;}
  const blocked=!this.ready||this.pending,configured=!!h.token_configured&&!!h.url;
  for(const id of ['save','import','url','token','name','replace'])this.el('hearing-'+id).disabled=blocked;
  this.el('hearing-start').disabled=blocked||!configured||h.requested||enrollment.active;
  this.el('hearing-stop').disabled=blocked||(!h.requested&&!enrollment.active);
  this.el('hearing-enroll').disabled=blocked||!configured||enrollment.active||!state?.telemetry?.health?.audio_ready;
  this.el('hearing-cancel').disabled=blocked||!enrollment.active;
  this.el('hearing-status').textContent=!fresh?'连接或状态已过期':!supported?'等待支持连续听觉的新固件':states[h.state]||'等待状态';
  this.el('hearing-secret-status').textContent=h.token_configured?'密钥已保存；留空保留':'尚未配置密钥';
  const identity=h.speaker_band==='known'?h.speaker:h.speaker_band==='unsure'?'身份待确认':'未登记 / 未识别';
  this.el('hearing-identity').textContent=fresh?`身份：${identity||'—'}`:'身份：—';
  this.el('hearing-emotion').textContent=fresh&&h.emotion?`情绪标签：${emotions[h.emotion]||h.emotion} · 仅作台词互动参考`:'情绪标签：—';
  this.el('hearing-text').textContent=fresh&&h.text?`${h.text}${h.last_speech_age_ms>=0?' · '+Math.floor(h.last_speech_age_ms/1000)+' 秒前':''}`:'等待语音';
  this.el('hearing-progress').textContent=fresh?`${phases[enrollment.phase]||'尚未录入'} · ${enrollment.n||0} / 5 句${enrollment.name?' · '+enrollment.name:''}`:'录入状态已过期';
  this.el('hearing-speakers').textContent=fresh?`已登记：${h.speakers?.join('、')||'等待后端返回'}`:'已登记：—';
  this.el('hearing-error').textContent=fresh&&h.error?`状态提示：${h.error}`:'';
 }
}
