const names={queued:'等待执行',moving:'运动中',trajectory_complete:'轨迹结束（请核对实测角度）',complete:'轨迹结束（请核对实测角度）',completed:'轨迹结束（请核对实测角度）',feedback_unavailable:'反馈不可用，未确认动作',cancelled:'已取消',superseded:'已被实体遥控取代',expired:'请求已过期',idle:'尚无网页动作',waiting_feedback:'等待反馈',target_unavailable:'目标超出机械范围'};
export class ControlPanel {
  constructor(doc, send = async body => {
    const response=await fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(7000)});
    if(!response.ok) throw new Error(await response.text());
    return response.json();
  }) {
    this.doc=doc;this.send=send;this.pending=false;this.dirty=false;this.boot='';
    this.actions={
      'm12-start':()=>({action:'m12',enabled:true}), 'm12-stop':()=>({action:'m12',enabled:false}),
      'motor-enable':()=>({action:'motor',enabled:true}), 'motor-disable':()=>({action:'motor',enabled:false}),
      'yaw-left':()=>({action:'yaw',delta_deg:-5}), 'yaw-right':()=>({action:'yaw',delta_deg:5}),
      'pitch-apply':()=>({action:'pitch',angle_deg:Number(this.el('pitch-input').value)})
    };
    for(const [id,make] of Object.entries(this.actions)) this.el(id).addEventListener('click',()=>this.dispatch(id,make()));
    this.el('pitch-input').addEventListener('input',()=>{this.dirty=true;});
  }
  el(id) {return this.doc.getElementById(id);}
  async dispatch(id,body) {
    if(this.pending || !this.ready || this.el(id).disabled) return;
    const boot=this.state.boot, message=body.action==='m12'?'m12-command':'servo-command';
    if(body.action==='pitch' && (!Number.isFinite(body.angle_deg) || body.angle_deg<5 || body.angle_deg>85 || Math.abs(body.angle_deg*10-Math.round(body.angle_deg*10))>1e-7)) {this.el(message).textContent='请输入 5–85°，步长 0.1°';return;}
    this.pending=true;this.el(message).textContent='正在发送…';this.render(this.state,this.fresh);
    try {const result=await this.send({...body,boot});this.el(message).textContent=this.state?.boot===boot?result.message:'机器人已重启，请核对新状态';}
    catch(error) {this.el(message).textContent=error.name==='TimeoutError'?'请求结果未确认，请核对实时状态；不会自动重发':error.message || '请求结果未确认';}
    finally {this.pending=false;this.render(this.state,this.fresh);}
  }
  render(state,fresh) {
    this.state=state;this.fresh=fresh;
    const supported=state?.capabilities?.web_control==='supported';
    this.ready=!!fresh && supported;
    const t=state?.telemetry||{},m=t.remote?.motor||{},p=t.pitch_setting||{};
    const active=m.enabled && m.requested;
    const yawFresh=m.yaw?.valid && t.timestamp_us>=m.timestamp_us && t.timestamp_us-m.timestamp_us<1000000;
    const pitchFresh=p.feedback_valid && t.timestamp_us>=p.feedback_timestamp_us && t.timestamp_us-p.feedback_timestamp_us<1000000;
    const yawReady=active && yawFresh && m.yaw_trajectory_complete && !['queued','moving'].includes(m.web_yaw?.state);
    const pitchReady=active && pitchFresh && !['queued','moving','waiting_feedback'].includes(p.state);
    const availability={'m12-start':t.m12?.valid,'m12-stop':true,'motor-enable':!m.requested,'motor-disable':true,'yaw-left':yawReady,'yaw-right':yawReady,'pitch-apply':pitchReady};
    for(const id of Object.keys(this.actions)) this.el(id).disabled=!this.ready || this.pending || !availability[id];
    this.el('pitch-input').disabled=!this.ready || this.pending;
    if(state?.boot !== this.boot) {this.boot=state?.boot;this.dirty=false;}
    if(!this.dirty && fresh && Number.isFinite(p.configured_deg)) this.el('pitch-input').value=String(p.configured_deg);
    this.el('controls-note').textContent=!fresh?'连接或数据已过期，控制暂不可用':!supported?'等待支持网页控制的新固件':!active?'舵机已停用，启用后可调整角度':!yawFresh?'左右角度反馈不可用，暂不能步进':!yawReady?'等待左右轴动作结束':'按机器人自身方向，每次转动 5°';
    this.el('yaw-command-state').textContent=fresh ? names[m.web_yaw?.state] || m.web_yaw?.state || '尚无网页动作' : '—';
    const pitchState=p.state==='feedback_unavailable' && p.feedback_valid && Number.isFinite(p.measured_deg) ? `反馈角度 ${p.measured_deg.toFixed(1)}°，未能启动调整` : names[p.state] || p.state || '—';
    this.el('pitch-command-state').textContent=fresh ? pitchState : '—';
  }
}
