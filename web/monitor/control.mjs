import {hearingBody} from './hearing.mjs';
const fail = (status, message) => { throw Object.assign(new Error(message), {status}); };
const fields = {interaction:['mode','enabled'],voice:['clip_id'],m12_mode:['mode'],m12:['enabled'],motor:['enabled'],yaw:['delta_deg'],yaw_center:['slot'],yaw_step:['delta_deg','speed','path'],pitch:['angle_deg'],camera:['enabled'],servo_refresh:['axis'],servo_restore:['axis'],servo_eeprom:['axis','p','d','i','punch','dead_cw','dead_ccw','max_torque'],servo_runtime:['axis','goal_time_ms','goal_speed','spring_stiffness','spring_damping','hold_min_ms','hold_max_ms','tolerance_deg'],wifi_scan:[],wifi_reconnect:[],wifi_add:['ssid','password','preferred'],wifi_remove:['ssid'],wifi_prefer:['ssid']};
// Every JSON control reaches the robot through one endpoint; `target` selects the feature.
const targets = {interaction:'interaction',voice:'voice_play',m12_mode:'m12_mode',m12:'m12_view',motor:'remote',yaw:'yaw_step',yaw_center:'yaw_step',yaw_step:'yaw_step',pitch:'pitch',camera:'camera'};
const route = '/v1/control';
Object.assign(fields,{eyes_configure:['request_id','enabled','style'],eyes_play:['request_id','state'],eyes_stop:['request_id']});
Object.assign(fields,{hearing_config:['url','token'],hearing_import:[],hearing_listen:['enabled'],hearing_enroll:['name','replace'],hearing_cancel:[]});
export class Controls {
  constructor(monitor,{loadHearingConfig=()=>{throw new Error('not configured');}}={}) { this.monitor=monitor; this.busy=false; this.loadHearingConfig=loadHearingConfig; }
  validate(input) {
    if (!input || typeof input !== 'object' || Array.isArray(input) || !Object.hasOwn(fields,input.action)) fail(400,'未知控制操作');
    const keys=['action','boot',...fields[input.action]];
    if (Object.keys(input).length !== keys.length || !keys.every(k=>Object.hasOwn(input,k))) fail(400,'控制参数无效');
    if (['interaction','m12','motor','camera'].includes(input.action) && typeof input.enabled !== 'boolean') fail(400,'启用状态必须是布尔值');
    if (input.action==='interaction' && !['tracking','recognition','advanced'].includes(input.mode)) fail(400,'未知交互模式');
    if (input.action==='m12_mode' && !['standby','face_detector','face_recognition'].includes(input.mode)) fail(400,'未知 M12 模式');
    if (input.action==='yaw' && ![-20,20].includes(input.delta_deg)) fail(400,'左右每次只调整一档（20°）');
    if (input.action==='yaw_center' && input.slot!==0) fail(400,'回中只接受 0 档');
    if (input.action.startsWith('wifi_')) {
      if (['wifi_add','wifi_remove','wifi_prefer'].includes(input.action) && (typeof input.ssid!=='string' || !input.ssid.trim() || input.ssid.length>32 || input.ssid.includes('\0'))) fail(400,'网络名须为 1–32 字符');
      if (input.action==='wifi_add' && (typeof input.password!=='string' || input.password.includes('\0') || (input.password.length>0 && (input.password.length<8 || input.password.length>63)) || typeof input.preferred!=='boolean')) fail(400,'密码需 8–63 位（开放网络留空），首选须为布尔值');
    }
    if (input.action.startsWith('servo_')) {
      if (!['yaw','pitch'].includes(input.axis)) fail(400,'轴只能是 yaw 或 pitch');
      const int=(v,lo,hi)=>Number.isInteger(v)&&v>=lo&&v<=hi;
      if (input.action==='servo_eeprom' && !(int(input.p,0,254)&&int(input.d,0,254)&&int(input.i,0,254)&&int(input.punch,0,1000)&&int(input.dead_cw,0,16)&&int(input.dead_ccw,0,16)&&int(input.max_torque,0,1000))) fail(400,'EEPROM 参数超出范围：P/D/I 0–254，启动力与最大扭矩 0–1000，死区 0–16');
      if (input.action==='servo_runtime' && !(int(input.goal_time_ms,0,9999)&&int(input.goal_speed,0,1000)&&Number.isFinite(input.spring_stiffness)&&Number.isFinite(input.spring_damping)&&input.spring_stiffness>=0&&input.spring_damping>=0&&input.spring_stiffness<=2000&&input.spring_damping<=500&&((input.spring_stiffness>0)===(input.spring_damping>0))&&int(input.hold_min_ms,0,10000)&&int(input.hold_max_ms,0,20000)&&input.hold_max_ms>=input.hold_min_ms&&Number.isFinite(input.tolerance_deg)&&input.tolerance_deg>=0&&input.tolerance_deg<=30)) fail(400,'运行时参数超出范围');
    }
    if (input.action==='yaw_step') {
      if (!Number.isInteger(input.delta_deg) || input.delta_deg===0 || Math.abs(input.delta_deg)>30) fail(400,'诊断步进须为 ±1…30 的整数度');
      if (!Number.isInteger(input.speed) || input.speed<50 || input.speed>1000) fail(400,'速度须为 50–1000');
      if (!['web','remote'].includes(input.path)) fail(400,'路径只能是 web 或 remote');
    }
    if (input.action==='pitch' && (typeof input.angle_deg!=='number' || !Number.isFinite(input.angle_deg) || input.angle_deg<5 || input.angle_deg>85 || Math.abs(input.angle_deg*10-Math.round(input.angle_deg*10))>1e-7)) fail(400,'仰角须为 5–85°，步长 0.1°');
    if (input.action==='voice' && (!Number.isInteger(input.clip_id) || input.clip_id<0 || input.clip_id>40)) fail(400,'请选择 1–40 条台词，或随机播放');
    const m=this.monitor,s=m.snapshot();
    if (!m.running || !s.online || !s.fresh.telemetry || !m.client.verified) fail(409,'请恢复监控并等待新的机器人状态');
    if (typeof input.boot!=='string' || input.boot !== s.boot) fail(409,'机器人已重启，请根据新状态重新操作');
    if (m.info?.capabilities?.web_control!=='supported') fail(409,'当前固件尚未支持网页控制');
    if (m.info?.capabilities?.unified_control!=='supported') fail(409,'固件过旧：需要 collector-0.4.46 或更新（单一 /v1/control 配置端）');
    if(input.action.startsWith('hearing_')) {
      if(m.info?.capabilities?.hearing!=='supported')fail(409,'当前固件尚未支持连续听觉');
      return {target:'hearing',...hearingBody(input,this.loadHearingConfig)};
    }
    if(input.action.startsWith('eyes_')) {
      if(m.info?.capabilities?.eyes!=='supported')fail(409,'当前固件尚未支持眼睛动画');
      if(typeof input.request_id!=='string'|| !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(input.request_id))fail(400,'请求编号无效');
      if(input.action==='eyes_configure' && (typeof input.enabled!=='boolean'||!m.info.eyes?.styles?.some(s=>s.id===input.style)))fail(400,'请选择有效款式');
      if(input.action==='eyes_play' && !m.info.eyes?.states?.some(s=>s.id===input.state))fail(400,'请选择有效表情');
      const body=Object.fromEntries(fields[input.action].map(k=>[k,input[k]]));
      return {target:'eyes',op:input.action.slice(5),...body,...(input.action==='eyes_play'?{mode:'once'}:{})};
    }
    if (input.action==='m12_mode' && m.info?.capabilities?.m12_recognition!=='supported') fail(409,'当前固件尚未支持手动认人');
    if (input.action==='camera' && m.info?.capabilities?.camera_control!=='supported') fail(409,'当前固件尚未支持相机开关');
    if (input.action==='yaw_step' && m.info?.capabilities?.yaw_step_diagnostics!=='supported') fail(409,'当前固件尚未支持步进诊断参数');
    if (input.action.startsWith('servo_') && m.info?.capabilities?.servo_tuning!=='supported') fail(409,'当前固件尚未支持舵机参数调节');
    if (input.action.startsWith('wifi_') && m.info?.capabilities?.wifi_profiles!=='supported') fail(409,'当前固件尚未支持网络管理');
    const t=s.telemetry,motor=t.remote?.motor,pitch=t.pitch_setting;
    if (input.action==='interaction') {
      if(m.info?.capabilities?.interaction_modes!=='supported') fail(409,'当前固件尚未支持交互模式');
      if(input.mode==='tracking' && m.info?.capabilities?.face_tracking!=='supported') fail(409,'当前固件尚未支持人脸追踪');
      if(input.enabled && ['tracking','recognition'].includes(input.mode) && !t.m12?.valid) fail(409,'M12 串口尚未就绪');
      if(input.enabled && input.mode==='tracking' && (!motor?.enabled || !motor?.requested)) fail(409,'请先启用舵机');
    }
    if (input.action==='voice' && (m.info?.capabilities?.voice_library!=='supported' || !t.health?.audio_ready || !t.playback || t.playback.busy)) fail(409,'等待语音就绪和当前播放结束');
    if (((input.action==='m12' && input.enabled) || (input.action==='m12_mode' && input.mode!=='standby')) && !t.m12?.valid) fail(409,'M12 串口尚未就绪');
    if (['yaw','yaw_center','yaw_step','pitch'].includes(input.action) && (!motor?.enabled || !motor?.requested)) fail(409,'请先启用舵机');
    if (['yaw','yaw_center','yaw_step'].includes(input.action) && (!motor.yaw?.valid || t.timestamp_us-motor.timestamp_us<0 || t.timestamp_us-motor.timestamp_us>=1000000 || !motor.yaw_trajectory_complete || ['queued','moving'].includes(motor.web_yaw?.state))) fail(409,'等待有效左右角度反馈和动作结束');
    if (input.action==='pitch' && (!pitch?.feedback_valid || t.timestamp_us-pitch.feedback_timestamp_us<0 || t.timestamp_us-pitch.feedback_timestamp_us>=1000000 || ['queued','moving','waiting_feedback'].includes(pitch.state))) fail(409,'等待有效仰角反馈和动作结束');
    if(input.action.startsWith('servo_')) return {target:'servo',op:input.action.slice(6),...Object.fromEntries(fields[input.action].map(k=>[k,input[k]]))};
    if(input.action.startsWith('wifi_')) return {target:'wifi',op:input.action.slice(5),...Object.fromEntries(fields[input.action].map(k=>[k,input[k]]))};
    return {target:targets[input.action],...Object.fromEntries(fields[input.action].map(k=>[k,input[k]]))};
  }
  async execute(input) {
    const body=this.validate(input);
    if(this.busy) fail(409,'另一条控制请求正在发送，请稍候');
    this.busy=true;
    try {
      const result=await this.monitor.client.control(route,body,input.boot,()=>this.validate(input));
      if(result.accepted!==true) fail(502,'设备没有确认接收请求');
      this.monitor.event('control',`网页 ${input.action} 请求已接收，执行状态以遥测为准`);
      return {...result,message:input.action.startsWith('wifi_')?(input.action==='wifi_add'?'网络已保存，正在重连；状态见上方':input.action==='wifi_scan'?'扫描已开始，结果稍后显示':'已接收，状态见上方'):input.action.startsWith('servo_')?'舵机参数请求已排队；结果以“舵机参数”对话框状态为准':input.action==='camera'?(input.enabled?'已请求开启机身相机并保存；画面以遥测为准':'已请求关闭机身相机并保存'):input.action==='pitch'?'仰角已保存并排队，执行状态见下方':input.action==='interaction'&&input.mode==='advanced'?'预留意图已接收；高级语音尚未实现':'设备已接收请求，执行状态见下方'};
    } catch(error) {
      const known=error.status>=400 && error.status<500;
      const message=known ? (error.status===409?'设备拒绝本次操作，请检查最新状态':error.message) : '请求结果未确认；请查看实时状态，本次不会自动重发';
      this.monitor.event('control',message);
      fail(known?error.status:502,message);
    } finally { this.busy=false; }
  }
}
