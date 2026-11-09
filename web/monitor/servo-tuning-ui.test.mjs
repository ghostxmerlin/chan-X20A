import test from 'node:test';
import assert from 'node:assert/strict';
import { ServoTuningPanel } from './public/servo-tuning-ui.js';
function harness() {
 const elements=new Map();
 const doc={getElementById(id){if(!elements.has(id))elements.set(id,{textContent:'',value:id==='tune-axis'?'yaw':'',disabled:false,addEventListener(e,fn){this[e]=fn;}});return elements.get(id);}};
 const calls=[];const ui=new ServoTuningPanel(doc,async body=>{calls.push(body);return {message:'已排队'};});
 const yaw={valid:true,angle_deg:1.2,position_raw:465,load_raw:0,voltage_raw:52,temperature_c:28,moving:0,
  tuning:{state:'idle',pending:false,eeprom:{valid:true,timestamp_us:5,p:15,d:15,i:0,punch:30,dead_cw:1,dead_ccw:1,max_torque:1000,min_angle:20,max_angle:1003,phase:1,unloading:32,led_alarm:37,max_temperature:70,min_voltage:45,max_voltage:90},
   runtime:{goal_time_ms:20,goal_speed:0,spring_stiffness:0,spring_damping:0,hold_min_ms:500,hold_max_ms:2000,tolerance_deg:1},last_write:{attempted:false}},
  motion_summary:{samples:12,source:'web_yaw',request_seq:3,target_deg:-5.9,start_deg:-0.9,last_deg:-1.2,error_deg:4.7,peak_load_permille:270,released_after_ms:2043,active:false}};
 const s={boot:'a',capabilities:{web_control:'supported'},telemetry:{remote:{motor:{yaw,pitch:{valid:false,tuning:{state:'idle',eeprom:{valid:false},runtime:{goal_time_ms:20,goal_speed:0,spring_stiffness:2.25,spring_damping:3,hold_min_ms:500,hold_max_ms:2000,tolerance_deg:1},last_write:{}},motion_summary:{samples:0}}}}}};
 return {ui,s,calls,el:id=>doc.getElementById(id)};
}
test('without the capability nothing is enabled or sent; with it inputs prefill from telemetry and only clicks send',async()=>{
 const {ui,s,calls,el}=harness();ui.render(s,true);
 assert.equal(el('tune-write').disabled,true);await el('tune-write').click();assert.equal(calls.length,0);
 s.capabilities.servo_tuning='supported';ui.render(s,true);ui.render(s,true);
 assert.equal(el('tune-p').value,'15');assert.equal(el('tune-punch').value,'30');assert.equal(el('tune-hold_max_ms').value,'2000');assert.equal(calls.length,0);
 assert.match(el('tune-summary').textContent,/目标 -5.9°.*误差 4.7°.*270‰.*2043 ms/);
 el('tune-p').value='40';el('tune-p').input();el('tune-punch').value='200';el('tune-dead_cw').value='2';el('tune-dead_ccw').value='2';
 await el('tune-write').click();
 assert.deepEqual(calls,[{action:'servo_eeprom',axis:'yaw',boot:'a',p:40,d:15,i:0,punch:200,dead_cw:2,dead_ccw:2,max_torque:1000}]);
 await el('tune-restore').click();assert.deepEqual(calls.at(-1),{action:'servo_restore',axis:'yaw',boot:'a'});
 el('tune-goal_speed').value='200';el('tune-tolerance_deg').value='0.6';await el('tune-apply-runtime').click();
 assert.deepEqual(calls.at(-1),{action:'servo_runtime',axis:'yaw',boot:'a',goal_time_ms:20,goal_speed:200,spring_stiffness:0,spring_damping:0,hold_min_ms:500,hold_max_ms:2000,tolerance_deg:0.6});
 el('tune-axis').value='pitch';el('tune-axis').change();assert.equal(el('tune-write').disabled,true); // pitch EEPROM not read yet
 assert.equal(el('tune-spring_stiffness').value,'2.25');
 await el('tune-read').click();assert.deepEqual(calls.at(-1),{action:'servo_refresh',axis:'pitch',boot:'a'});
 ui.render(s,false);await el('tune-read').click();assert.equal(calls.length,4);
});
