// Operator servo tuning dialog: reads current EEPROM/runtime values from telemetry,
// sends explicit requests only on click. Values are register units; see README.
const eepromFields=['p','d','i','punch','dead_cw','dead_ccw','max_torque'];
const runtimeFields=['goal_time_ms','goal_speed','spring_stiffness','spring_damping','hold_min_ms','hold_max_ms','tolerance_deg'];
const states={idle:'尚无请求',queued:'已排队，等待轴空闲',applying:'正在写入',written:'已写入舵机并读回',write_failed:'写入失败（见读回值）',applied:'运行时参数已应用',refreshed:'已重新读取',expired_axis_busy:'轴忙，请求已过期',invalid:'请求无效'};
export class ServoTuningPanel {
 constructor(doc,send=async body=>{
  const response=await fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(7000)});
  if(!response.ok)throw new Error(await response.text());return response.json();
 }){
  this.doc=doc;this.send=send;this.pending=false;this.dirty=false;this.loaded='';
  this.el('tune-axis').addEventListener('change',()=>{this.dirty=false;this.loaded='';this.render(this.state,this.fresh);});
  for(const id of [...eepromFields,...runtimeFields])this.el('tune-'+id).addEventListener('input',()=>{this.dirty=true;});
  this.el('tune-read').addEventListener('click',()=>this.act('servo_refresh',{}));
  this.el('tune-restore').addEventListener('click',()=>this.act('servo_restore',{}));
  this.el('tune-write').addEventListener('click',()=>this.act('servo_eeprom',Object.fromEntries(eepromFields.map(f=>[f,Math.round(Number(this.el('tune-'+f).value))]))));
  this.el('tune-apply-runtime').addEventListener('click',()=>this.act('servo_runtime',Object.fromEntries(runtimeFields.map(f=>[f,f.startsWith('spring')||f==='tolerance_deg'?Number(this.el('tune-'+f).value):Math.round(Number(this.el('tune-'+f).value))]))));
 }
 el(id){return this.doc.getElementById(id);}
 axis(){return this.el('tune-axis').value==='pitch'?'pitch':'yaw';}
 async act(action,params){
  if(!this.ready||this.pending)return;
  for(const v of Object.values(params)) if(!Number.isFinite(v)){this.el('tune-command').textContent='请填写所有数值';return;}
  const boot=this.state.boot;this.pending=true;this.el('tune-command').textContent='正在发送…';this.render(this.state,this.fresh);
  try{const result=await this.send({action,axis:this.axis(),boot,...params});this.dirty=false;this.loaded='';this.el('tune-command').textContent=this.state?.boot===boot?result.message:'机器人已重启，请核对当前状态';}
  catch(e){this.el('tune-command').textContent=e.name==='TimeoutError'?'结果未确认，请查看状态；不会自动重发':e.message||'结果未确认';}
  finally{this.pending=false;this.render(this.state,this.fresh);}
 }
 render(state,fresh){
  this.state=state;this.fresh=fresh;
  const supported=state?.capabilities?.servo_tuning==='supported';
  const t=state?.telemetry||{},m=t.remote?.motor||{},axis=this.axis(),a=m[axis]||{},tuning=a.tuning||{},eeprom=tuning.eeprom||{},runtime=tuning.runtime||{},summary=a.motion_summary||{};
  this.ready=!!fresh&&supported&&!!tuning.state;
  const blocked=!this.ready||this.pending||!!tuning.pending;
  for(const id of ['tune-read','tune-restore','tune-write','tune-apply-runtime',...eepromFields.map(f=>'tune-'+f),...runtimeFields.map(f=>'tune-'+f)])this.el(id).disabled=blocked;
  this.el('tune-write').disabled=blocked||!eeprom.valid;
  const key=`${state?.boot}/${axis}/${eeprom.timestamp_us}/${JSON.stringify(runtime)}`;
  if(this.ready&&!this.dirty&&this.loaded!==key){
   if(eeprom.valid)for(const f of eepromFields)this.el('tune-'+f).value=String(eeprom[f]);
   for(const f of runtimeFields)if(runtime[f]!==undefined)this.el('tune-'+f).value=String(runtime[f]);
   this.loaded=key;
  }
  const w=tuning.last_write||{};
  this.el('tune-status').textContent=!fresh?'状态已过期':!supported?'当前固件不支持舵机参数调节':`${states[tuning.state]||tuning.state||'—'}${tuning.pending?' · 排队中':''}${w.attempted?` · 最近写入 ${w.writes} 项，失败 ${w.failed_writes}${w.failed_register>=0?'（寄存器 '+w.failed_register+'）':''}`:''}`;
  this.el('tune-eeprom-extra').textContent=eeprom.valid?`只读：角度限位 ${eeprom.min_angle}–${eeprom.max_angle} 步 · 相位 ${eeprom.phase} · 卸载条件 ${eeprom.unloading} · LED 报警 ${eeprom.led_alarm} · 温度上限 ${eeprom.max_temperature}°C · 电压 ${eeprom.min_voltage/10}–${eeprom.max_voltage/10} V`:'EEPROM 尚未读到（开机 5 秒后自动读一次，或点“重新读取”）';
  this.el('tune-feedback').textContent=a.valid?`实测 ${Number(a.angle_deg).toFixed(1)}° / raw ${a.position_raw} · 负载 ${a.load_raw??'—'}‰ · 电压 ${a.voltage_raw!==undefined?(a.voltage_raw/10).toFixed(1)+' V':'—'} · 温度 ${a.temperature_c??'—'}°C · 运动标志 ${a.moving??'—'}`:'反馈无效';
  const d=v=>Number.isFinite(v)?Number(v).toFixed(1)+'°':'—';
  this.el('tune-summary').textContent=summary.samples?`上次动作（${summary.source}#${summary.request_seq}）：目标 ${d(summary.target_deg)}，起点 ${d(summary.start_deg)}，最后实测 ${d(summary.last_deg)}，误差 ${d(summary.error_deg)}，峰值负载 ${summary.peak_load_permille??'—'}‰，卸力在最终目标后 ${summary.released_after_ms??'—'} ms${summary.active?'（记录中）':''}`:'尚无动作记录';
 }
}
