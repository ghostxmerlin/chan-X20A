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
  // Keep the last real sentence: a punctuation-only misfire does not replace it.
  if(fresh&&h.text&&/[\p{L}\p{N}]/u.test(h.text))this.lastText={text:h.text,boot:state.boot};
  const shownText=this.lastText?.boot===state?.boot?this.lastText.text:'';
  this.el('hearing-text').textContent=fresh&&shownText?`${shownText}${shownText===h.text&&h.last_speech_age_ms>=0?' · '+Math.floor(h.last_speech_age_ms/1000)+' 秒前':''}`:'等待语音';
  this.el('hearing-progress').textContent=fresh?`${phases[enrollment.phase]||'尚未录入'} · ${enrollment.n||0} / 5 句${enrollment.name?' · '+enrollment.name:''}`:'录入状态已过期';
  this.el('hearing-speakers').textContent=fresh?`已登记：${h.speakers?.join('、')||'等待后端返回'}`:'已登记：—';
  this.el('hearing-error').textContent=fresh&&h.error?`状态提示：${h.error}`:'';
  // Heartbeat: the backend ticks every 100 ms; the robot drops the session after 3 s without one.
  const hb=h.heartbeat,d=h.disconnects||{},end=h.last_end||{};
  const db=v=>Number.isFinite(v)?v.toFixed(1):'—';
  const ms=v=>!Number.isFinite(v)||v<0?'—':v>=10000?`${Math.round(v/1000)} s`:`${v} ms`;
  this.el('hearing-heartbeat').textContent=!fresh||!hb?'心跳：—':!h.connected?`心跳：未连接 · 开机以来最大间隔 ${ms(hb.max_gap_boot_ms)}`:
   `心跳：${ms(hb.last_age_ms)} 前 · 本次 ${hb.ticks} 次 · 最大间隔 ${ms(hb.max_gap_ms)}（超时 ${ms(hb.timeout_ms)}）· 服务端电平 ${db(hb.server_level_db)} / 底噪 ${db(hb.server_noise_db)} dB · 待发 ${h.backlog?.depth??'—'} 包（最多 ${h.backlog?.max_depth??'—'} / ${h.backlog?.capacity_packets??'—'}，溢出 ${h.backlog?.dropped_packets??0}）· 卡顿 >200ms ${h.stalls?.over_200ms??0} 次 / >1s ${h.stalls?.over_1s??0} 次，最长 ${ms(h.stalls?.longest_ms)}${h.stalls?.current_ms?' · 正卡 '+ms(h.stalls.current_ms):''} · 本次丢块 ${h.session_dropped_blocks??'—'}`;
  const reasons={heartbeat_timeout:'心跳超时',send_stalled:'发送卡死',send_failed:'发送失败',transport_closed:'连接关闭',connect_failed:'连接失败',handshake_timeout:'握手超时',encode_failed:'编码失败',stopped:'停止'};
  const rp=h.response;
  const left=s=>`${Math.floor(s/60)}:${String(s%60).padStart(2,'0')}`;
  this.el('hearing-response').textContent=!fresh||!rp?'回应：—':`回应：${rp.pending?`等安静中 #${rp.pending_clip}，已等 ${(rp.pending_wait_ms/1000).toFixed(1)} s（${rp.server_speaking?'有人在说话':'正在安静'}，安静 ${rp.quiet_ms/1000} s 后说，最多等 ${rp.max_wait_ms/1000} s）`:'空闲'} · 开心 ${rp.happy_needed===1?'本窗口首次即回应':`${rp.happy_count}/${rp.happy_needed} 次回应一次`}${rp.happy_window_left_s?`（窗口剩 ${left(rp.happy_window_left_s)}）`:''}`;
  const counts=Object.entries(reasons).filter(([k])=>k!=='stopped'&&d[k]).map(([k,v])=>`${v} ${d[k]}`).join(' / ');
  this.el('hearing-link').textContent=!fresh?'断线：—':`断线：${counts||'无'}${end.reason&&end.age_ms>=0?` · 上次 ${reasons[end.reason]||end.reason} ${ms(end.age_ms)}前（连了 ${ms(end.duration_ms)}，丢 ${end.dropped_blocks} 块${end.error?'，'+end.error:''}）`:''}`;
 }
}
