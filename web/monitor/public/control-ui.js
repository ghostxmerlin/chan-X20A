const names={queued:'等待执行',moving:'运动中',trajectory_complete:'轨迹结束（请核对实测角度）',complete:'轨迹结束（请核对实测角度）',completed:'轨迹结束（请核对实测角度）',feedback_unavailable:'反馈不可用，未确认动作',cancelled:'已取消',superseded:'已被实体遥控取代',expired:'请求已过期',idle:'尚无网页动作',waiting_feedback:'等待反馈',target_unavailable:'目标超出机械范围'};
export class ControlPanel {
  constructor(doc, send = async body => {
    const response=await fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(7000)});
    if(!response.ok) throw new Error(await response.text());
    return response.json();
  }) {
    this.doc=doc;this.send=send;this.pending=false;this.dirty=false;this.boot='';
    this.actions={
      'm12-recognize':()=>({action:'m12_mode',mode:'face_recognition'}),
      'm12-start':()=>({action:'m12',enabled:true}), 'm12-stop':()=>({action:'m12',enabled:false}),
      'motor-enable':()=>({action:'motor',enabled:true}), 'motor-disable':()=>({action:'motor',enabled:false}),
      'camera-toggle':()=>({action:'camera',enabled:this.state?.telemetry?.health?.camera_enabled!==true}),
      'yaw-left':()=>({action:'yaw',delta_deg:-20}), 'yaw-right':()=>({action:'yaw',delta_deg:20}), 'yaw-center':()=>({action:'yaw_center',slot:0}),
      'yaw-step-left':()=>this.stepBody(-1), 'yaw-step-right':()=>this.stepBody(1),
      'pitch-apply':()=>({action:'pitch',angle_deg:Number(this.el('pitch-input').value)})
    };
    for(const [id,make] of Object.entries(this.actions)) this.el(id).addEventListener('click',()=>this.dispatch(id,make()));
    this.el('pitch-input').addEventListener('input',()=>{this.dirty=true;});
  }
  el(id) {return this.doc.getElementById(id);}
  stepBody(direction) {
    const degrees=Math.round(Math.abs(Number(this.el('yaw-step-input').value)));
    return {action:'yaw_step',delta_deg:direction*degrees,speed:Math.round(Number(this.el('yaw-speed-input').value)),path:this.el('yaw-path-select').value==='remote'?'remote':'web'};
  }
  async dispatch(id,body) {
    if(this.pending || !this.ready || this.el(id).disabled) return;
    const boot=this.state.boot, message=body.action.startsWith('m12')?'m12-command':body.action==='camera'?'camera-command':'servo-command';
    if(body.action==='pitch' && (!Number.isFinite(body.angle_deg) || body.angle_deg<5 || body.angle_deg>85 || Math.abs(body.angle_deg*10-Math.round(body.angle_deg*10))>1e-7)) {this.el(message).textContent='请输入 5–85°，步长 0.1°';return;}
    if(body.action==='yaw_step' && (!Number.isInteger(body.delta_deg) || body.delta_deg===0 || Math.abs(body.delta_deg)>30 || !Number.isInteger(body.speed) || body.speed<50 || body.speed>1000)) {this.el(message).textContent='步进 1–30 整数度，速度 50–1000';return;}
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
    const cameraKnown=typeof t.health?.camera_enabled==='boolean', cameraOn=t.health?.camera_enabled===true;
    this.el('camera-toggle').textContent=cameraOn?'关闭相机':'开启相机';
    const stepDiag=state?.capabilities?.yaw_step_diagnostics==='supported';
    const availability={'m12-recognize':t.m12?.valid && state?.capabilities?.m12_recognition==='supported','m12-start':t.m12?.valid,'m12-stop':true,'motor-enable':!m.requested,'motor-disable':true,'yaw-left':yawReady&&(m.web_yaw?.measured_slot===undefined||m.web_yaw.measured_slot>-(m.web_yaw.slots??3)),'yaw-right':yawReady&&(m.web_yaw?.measured_slot===undefined||m.web_yaw.measured_slot<(m.web_yaw.slots??3)),'yaw-center':yawReady,'yaw-step-left':yawReady&&stepDiag,'yaw-step-right':yawReady&&stepDiag,'pitch-apply':pitchReady,'camera-toggle':state?.capabilities?.camera_control==='supported' && cameraKnown};
    for(const id of ['yaw-step-input','yaw-speed-input','yaw-path-select']) this.el(id).disabled=!this.ready||this.pending||!stepDiag;
    if(!this.stepDefaultsSet && fresh && stepDiag){this.el('yaw-step-input').value='5';this.el('yaw-speed-input').value=String(m.web_yaw?.default_speed||600);this.stepDefaultsSet=true;}
    for(const id of Object.keys(this.actions)) this.el(id).disabled=!this.ready || this.pending || !availability[id];
    this.el('pitch-input').disabled=!this.ready || this.pending;
    if(state?.boot !== this.boot) {this.boot=state?.boot;this.dirty=false;}
    if(!this.dirty && fresh && Number.isFinite(p.configured_deg)) this.el('pitch-input').value=String(p.configured_deg);
    this.el('controls-note').textContent=!fresh?'连接或数据已过期，控制暂不可用':!supported?'等待支持网页控制的新固件':!active?'舵机已停用，启用后可调整角度':!yawFresh?'左右角度反馈不可用，暂不能步进':!yawReady?'等待左右轴动作结束':`按机器人自身方向，每次一档 ${m.web_yaw?.step_deg??20}°，中心左右各 ${m.web_yaw?.slots??3} 档；当前约在第 ${m.web_yaw?.measured_slot??'—'} 档`;
    this.el('yaw-command-state').textContent=fresh ? (names[m.web_yaw?.state] || m.web_yaw?.state || '尚无网页动作') + (m.web_yaw?.request_seq ? ` · 目标 ${Number(m.web_yaw.target_deg).toFixed(1)}° · 速度 ${m.web_yaw.speed}${m.web_yaw.path==='remote_like'?' · 遥控同款路径':''}` : '') : '—';
    const pitchState=p.state==='feedback_unavailable' && p.feedback_valid && Number.isFinite(p.measured_deg) ? `反馈角度 ${p.measured_deg.toFixed(1)}°，未能启动调整` : names[p.state] || p.state || '—';
    this.el('pitch-command-state').textContent=fresh ? pitchState : '—';
    const modes=state?.capabilities?.interaction_modes==='supported';
    this.el('remote-pitch-note').textContent=!fresh?'遥控状态已过期':modes?'遥控：左追踪 · 右认人 · 上听觉 · 下预留':t.remote?.pitch_control_enabled===false?'实体遥控上下已屏蔽，左右与中心开关屏保留。':t.remote?.pitch_control_enabled===true?'实体遥控上下仍启用，等待固件更新。':'等待遥控上下状态。';
    this.el('remote-pitch-note').title=modes?'实体遥控四向只切换模式，不直接转动舵机；使用摇杆页，松回中位后再次拨动。中心仍开关屏幕。网页左右一档 20° 和保存仰角仍为显式动作，并退出自动追踪。':this.el('remote-pitch-note').textContent;
  }
}

const trackingStates={off:'已关闭',disabled:'已关闭',waiting_frame:'等待新鲜人脸',waiting_feedback:'等待反馈',recovering_feedback:'反馈恢复中',tracking:'追踪中',deadband:'中央死区，保持',at_limit:'已达追踪边界',mode_changed:'视觉模式已改变',direction_changed:'方向改变，等待新帧',face_lost:'丢脸，停止输出',frame_stale:'人脸结果过期',feedback_invalid:'反馈无效，停止输出',feedback_stale:'反馈过期，停止输出',fault_no_progress:'舵机未移动保护，请关闭后重开',frame_jump:'人脸跳变，等待稳定',fault_feedback_jump:'反馈跳变保护，请关闭后重开',fault_anchor_range:'超出锚点保护，请关闭后重开',manual_override:'手动接管，停止追踪',command_rejected:'动作被拒绝'};
const recognitionStates={off:'已关闭',waiting_face:'等待已登记人脸',confirming:'正在确认主人',owner_confirmed:'已确认主人',external_mode:'手动视觉接管'};
const dialogueStates={off:'听觉会话关闭',waiting_owner:'等待主人后开始听觉',listening:'听觉情绪回应中',shared_listening:'沿用手动听觉',stopped_by_user:'手动停止，等待重新开启',backend_unavailable:'听觉后端未就绪',enrolling:'声纹录入中'};
const degrees=value=>Number.isFinite(value)?`${value.toFixed(1)}°`:'—';
export class InteractionPanel {
  constructor(doc,send=async body=>{
    const response=await fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(7000)});
    if(!response.ok)throw new Error(await response.text());return response.json();
  }) {
    this.doc=doc;this.send=send;this.pending=false;
    for(const mode of ['tracking','recognition','advanced'])this.el('interaction-'+mode).addEventListener('click',()=>this.toggle(mode));
  }
  el(id){return this.doc.getElementById(id);}
  label(id,text,title=text){this.el(id).textContent=text;this.el(id).title=title;}
  async toggle(mode){
    if(!this.ready||this.pending||this.el('interaction-'+mode).disabled)return;
    const boot=this.state.boot,enabled=!this.state.telemetry.interaction[mode].requested;
    this.pending=true;this.label('interaction-command','正在发送…');this.render(this.state,this.fresh);
    try{const result=await this.send({action:'interaction',mode,enabled,boot});this.label('interaction-command',this.state?.boot===boot?result.message:'机器人已重启，请核对新状态');}
    catch(error){this.label('interaction-command',error.name==='TimeoutError'?'结果未确认；不会自动重发':error.message||'结果未确认；不会自动重发');}
    finally{this.pending=false;this.render(this.state,this.fresh);}
  }
  render(state,fresh){
    this.state=state;this.fresh=!!fresh;
    const supported=state?.capabilities?.interaction_modes==='supported';
    this.ready=!!fresh&&supported&&state?.capabilities?.web_control==='supported';
    const t=state?.telemetry||{},interaction=t.interaction||{},tracking=interaction.tracking||{},recognition=interaction.recognition||{},dialogue=interaction.dialogue||{};
    const trackingSupported=supported&&state?.capabilities?.face_tracking==='supported';
    const motorReady=!!t.remote?.motor?.requested&&!!t.remote?.motor?.enabled;
    for(const [mode,name] of [['tracking','追踪'],['recognition','认人对话'],['advanced','高级预留']]){
      const data=interaction[mode],requested=!!fresh&&!!data?.requested,button=this.el('interaction-'+mode);
      button.textContent=`${requested?'关闭':'开启'}${name}`;button.setAttribute('aria-pressed',String(requested));
      const allowed=mode==='tracking'?trackingSupported&&(requested||(motorReady&&t.m12?.valid)):mode==='recognition'?requested||t.m12?.valid:true;
      button.disabled=!this.ready||this.pending||typeof data?.requested!=='boolean'||!allowed;
      button.title=mode==='advanced'?'只记录预留意图；高级语音尚未实现，不会启动新的语音后端。':mode==='tracking'?'只水平追踪方向：人脸偏离中心超过约 40% 半宽才动，一次一档 20°，间隔 2.5 s，范围 ±60°；开启需舵机启用和 M12 串口就绪。': '任意已登记人脸视为主人，确认后启动现有听觉与情绪回应；真正问答尚未实现。';
    }
    const unavailable=!fresh?'连接或状态已过期':!supported?'等待支持模式的新固件':!interaction.tracking?'等待模式遥测':'';
    const trackingState=trackingStates[tracking.state]||tracking.state||'等待设备状态';
    this.label('tracking-state',unavailable||(!trackingSupported?'当前固件不支持追踪':`${tracking.requested?'已请求 · ':''}${trackingState}`));
    this.label('tracking-detail',unavailable?'锚点 / 目标：—':`锚 ${degrees(tracking.anchor_deg)} / 目标 ${degrees(tracking.target_deg)}`,unavailable||`锚点 ${degrees(tracking.anchor_deg)}；目标 ${degrees(tracking.target_deg)}（不是实测位置）；范围 ±${Number.isFinite(tracking.range_deg)?tracking.range_deg:60}°，一档 ${Number.isFinite(tracking.slot_deg)?tracking.slot_deg:20}°。${tracking.active?'追踪输出已激活':'当前未激活追踪输出'}。`);
    this.label('recognition-state',unavailable||recognitionStates[recognition.state]||recognition.state||'等待设备状态');
    const hearing=dialogueStates[dialogue.state]||dialogue.state||'等待听觉状态',owner=recognition.owner_seen&&recognition.owner_name?`${recognition.owner_name} · `:'';
    this.label('recognition-detail',unavailable?'身份 / 听觉：—':owner+hearing,unavailable||`${owner}${hearing}。${dialogue.owned?'认人模式持有听觉意图':'认人模式未持有听觉意图'}；使用现有听觉/情绪回应，真正问答后续实现。`);
    this.label('advanced-state',unavailable||'高级语音尚未实现');
    this.label('advanced-detail',!fresh?'预留意图：—':!supported?'等待新固件':interaction.advanced?.requested?'仅预留意图开启':'预留意图关闭');
    this.label('asr-mode-note',supported?'open 追踪 / close 关闭 · next 台词 · start / stop 听觉':'open 台词 · start / stop 听觉 · turn on / off 检测',supported?'open 开启追踪，close 关闭；next 随机台词并等待 800ms；start / stop 连续听觉；turn on / off 保留手动视觉切换。':'旧固件：open 随机台词并等待 800ms；start / stop 连续听觉；turn on / off 手动检测。');
  }
}
