const fail = (status, message) => { throw Object.assign(new Error(message), {status}); };
const fields = {voice:['clip_id'],m12_mode:['mode'],m12:['enabled'],motor:['enabled'],yaw:['delta_deg'],pitch:['angle_deg']};
const routes = {voice:'/v1/voice/play',m12_mode:'/v1/m12/mode',m12:'/v1/m12/view',motor:'/v1/remote',yaw:'/v1/motion/yaw-step',pitch:'/v1/settings/pitch'};
export class Controls {
  constructor(monitor) { this.monitor=monitor; this.busy=false; }
  validate(input) {
    if (!input || typeof input !== 'object' || Array.isArray(input) || !Object.hasOwn(fields,input.action)) fail(400,'未知控制操作');
    const keys=['action','boot',...fields[input.action]];
    if (Object.keys(input).length !== keys.length || !keys.every(k=>Object.hasOwn(input,k))) fail(400,'控制参数无效');
    if (['m12','motor'].includes(input.action) && typeof input.enabled !== 'boolean') fail(400,'启用状态必须是布尔值');
    if (input.action==='m12_mode' && !['standby','face_detector','face_recognition'].includes(input.mode)) fail(400,'未知 M12 模式');
    if (input.action==='yaw' && ![-5,5].includes(input.delta_deg)) fail(400,'左右每次只调整 5°');
    if (input.action==='pitch' && (typeof input.angle_deg!=='number' || !Number.isFinite(input.angle_deg) || input.angle_deg<5 || input.angle_deg>85 || Math.abs(input.angle_deg*10-Math.round(input.angle_deg*10))>1e-7)) fail(400,'仰角须为 5–85°，步长 0.1°');
    if (input.action==='voice' && (!Number.isInteger(input.clip_id) || input.clip_id<0 || input.clip_id>40)) fail(400,'请选择 1–40 条台词，或随机播放');
    const m=this.monitor,s=m.snapshot();
    if (!m.running || !s.online || !s.fresh.telemetry || !m.client.verified) fail(409,'请恢复监控并等待新的机器人状态');
    if (typeof input.boot!=='string' || input.boot !== s.boot) fail(409,'机器人已重启，请根据新状态重新操作');
    if (m.info?.capabilities?.web_control!=='supported') fail(409,'当前固件尚未支持网页控制');
    if (input.action==='m12_mode' && m.info?.capabilities?.m12_recognition!=='supported') fail(409,'当前固件尚未支持手动认人');
    const t=s.telemetry,motor=t.remote?.motor,pitch=t.pitch_setting;
    if (input.action==='voice' && (m.info?.capabilities?.voice_library!=='supported' || !t.health?.audio_ready || !t.playback || t.playback.busy)) fail(409,'等待语音就绪和当前播放结束');
    if (((input.action==='m12' && input.enabled) || (input.action==='m12_mode' && input.mode!=='standby')) && !t.m12?.valid) fail(409,'M12 串口尚未就绪');
    if (['yaw','pitch'].includes(input.action) && (!motor?.enabled || !motor?.requested)) fail(409,'请先启用舵机');
    if (input.action==='yaw' && (!motor.yaw?.valid || t.timestamp_us-motor.timestamp_us<0 || t.timestamp_us-motor.timestamp_us>=1000000 || !motor.yaw_trajectory_complete || ['queued','moving'].includes(motor.web_yaw?.state))) fail(409,'等待有效左右角度反馈和动作结束');
    if (input.action==='pitch' && (!pitch?.feedback_valid || t.timestamp_us-pitch.feedback_timestamp_us<0 || t.timestamp_us-pitch.feedback_timestamp_us>=1000000 || ['queued','moving','waiting_feedback'].includes(pitch.state))) fail(409,'等待有效仰角反馈和动作结束');
    return Object.fromEntries(fields[input.action].map(k=>[k,input[k]]));
  }
  async execute(input) {
    const body=this.validate(input);
    if(this.busy) fail(409,'另一条控制请求正在发送，请稍候');
    this.busy=true;
    try {
      const result=await this.monitor.client.control(routes[input.action],body,input.boot,()=>this.validate(input));
      if(result.accepted!==true) fail(502,'设备没有确认接收请求');
      this.monitor.event('control',`网页 ${input.action} 请求已接收，执行状态以遥测为准`);
      return {...result,message:input.action==='pitch'?'仰角已保存并排队，执行状态见下方':'设备已接收请求，执行状态见下方'};
    } catch(error) {
      const known=error.status>=400 && error.status<500;
      const message=known ? (error.status===409?'设备拒绝本次操作，请检查最新状态':error.message) : '请求结果未确认；请查看实时状态，本次不会自动重发';
      this.monitor.event('control',message);
      fail(known?error.status:502,message);
    } finally { this.busy=false; }
  }
}
