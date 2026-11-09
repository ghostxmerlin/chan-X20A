import test from 'node:test';
import assert from 'node:assert/strict';
import { ControlPanel } from './public/control-ui.js';
function harness() {
 const elements=new Map();
 const doc={getElementById(id){if(!elements.has(id))elements.set(id,{textContent:'',value:'',disabled:false,addEventListener(e,fn){this[e]=fn;}});return elements.get(id);}};
 const calls=[];const ui=new ControlPanel(doc,async body=>{calls.push(body);return {message:'已接收'};});
 const s={boot:'a',capabilities:{web_control:'supported'},telemetry:{timestamp_us:2000000,m12:{valid:true,view_enabled:false},remote:{motor:{enabled:true,requested:true,timestamp_us:2000000,yaw:{valid:true},yaw_trajectory_complete:true}},pitch_setting:{configured_deg:10,feedback_valid:true,feedback_timestamp_us:2000000,state:'complete'}}};
 return {ui,s,calls,el:id=>doc.getElementById(id)};
}
test('opening or rendering controls never sends; only explicit click sends once',async()=>{
 const {ui,s,calls,el}=harness();ui.render(s,true);ui.render(s,true);assert.equal(calls.length,0);assert.equal(el('pitch-input').value,'10');
 await el('m12-start').click();assert.equal(calls.length,1);assert.deepEqual(calls[0],{action:'m12',enabled:true,boot:'a'});
 ui.render(s,true);assert.equal(calls.length,1);
});
test('stale data disables writes, invalid feedback blocks steps but allows stop, dirty pitch survives polling',async()=>{
 const {ui,s,calls,el}=harness();ui.render(s,true);el('pitch-input').value='15';el('pitch-input').input();ui.render(s,true);assert.equal(el('pitch-input').value,'15');
 s.telemetry.remote.motor.yaw.valid=false;ui.render(s,true);assert.equal(el('yaw-left').disabled,true);assert.equal(el('motor-disable').disabled,false);
 ui.render(s,false);await el('m12-start').click();assert.equal(calls.length,0);assert.equal(el('pitch-apply').disabled,true);
});
test('recognition is disabled on old firmware and only an explicit click requests it',async()=>{
 const {ui,s,calls,el}=harness();ui.render(s,true);
 await el('m12-recognize').click();assert.equal(calls.length,0);
 s.capabilities.m12_recognition='supported';ui.render(s,true);assert.equal(calls.length,0);
 await el('m12-recognize').click();assert.deepEqual(calls,[{action:'m12_mode',mode:'face_recognition',boot:'a'}]);
});
test('diagnostic step buttons send the typed degrees, speed and path only when the firmware supports them',async()=>{
 const {ui,s,calls,el}=harness();ui.render(s,true);
 await el('yaw-step-left').click();assert.equal(calls.length,0);assert.equal(el('yaw-step-input').disabled,true);
 s.capabilities.yaw_step_diagnostics='supported';s.telemetry.remote.motor.web_yaw={default_speed:600};ui.render(s,true);
 assert.equal(el('yaw-step-input').value,'5');assert.equal(el('yaw-speed-input').value,'600');
 el('yaw-step-input').value='15';el('yaw-speed-input').value='600';el('yaw-path-select').value='remote';
 await el('yaw-step-left').click();assert.deepEqual(calls,[{action:'yaw_step',delta_deg:-15,speed:600,path:'remote',boot:'a'}]);
 el('yaw-path-select').value='web';el('yaw-step-input').value='3';
 await el('yaw-step-right').click();assert.deepEqual(calls.at(-1),{action:'yaw_step',delta_deg:3,speed:600,path:'web',boot:'a'});
 el('yaw-step-input').value='0';await el('yaw-step-right').click();assert.equal(calls.length,2);
});
test('camera toggle needs the capability and sends the opposite of the reported state',async()=>{
 const {ui,s,calls,el}=harness();ui.render(s,true);
 await el('camera-toggle').click();assert.equal(calls.length,0);
 s.capabilities.camera_control='supported';ui.render(s,true);assert.equal(el('camera-toggle').disabled,true); // state unknown yet
 s.telemetry.health={camera_enabled:false};ui.render(s,true);
 assert.equal(el('camera-toggle').disabled,false);assert.equal(el('camera-toggle').textContent,'开启相机');
 await el('camera-toggle').click();assert.deepEqual(calls,[{action:'camera',enabled:true,boot:'a'}]);
 s.telemetry.health.camera_enabled=true;ui.render(s,true);assert.equal(el('camera-toggle').textContent,'关闭相机');
 await el('camera-toggle').click();assert.deepEqual(calls.at(-1),{action:'camera',enabled:false,boot:'a'});
});
test('mode firmware explains four-way remote toggles while preserving explicit web movement',async()=>{
 const {ui,s,calls,el}=harness();s.capabilities.interaction_modes='supported';ui.render(s,true);
 assert.match(el('remote-pitch-note').textContent,/左追踪.*右认人.*上听觉.*下预留/);
 assert.match(el('remote-pitch-note').title,/中心.*开关屏幕/);
 await el('yaw-left').click();await el('pitch-apply').click();
 assert.deepEqual(calls,[{action:'yaw',delta_deg:-20,boot:'a'},{action:'pitch',angle_deg:10,boot:'a'}]);
});
test('servo feedback sampled a few ms after the telemetry stamp still counts as fresh',()=>{
 // Firmware stamps telemetry before copying the servo snapshot; observed skew up to 24 ms.
 const {ui,s,el}=harness();
 s.telemetry.remote.motor.timestamp_us=2024125;s.telemetry.pitch_setting.feedback_timestamp_us=2022038;ui.render(s,true);
 assert.equal(el('yaw-left').disabled,false);assert.equal(el('pitch-apply').disabled,false);
 // A feedback stamp far in the future is still rejected.
 s.telemetry.remote.motor.timestamp_us=3000000;s.telemetry.pitch_setting.feedback_timestamp_us=3000000;ui.render(s,true);
 assert.equal(el('yaw-left').disabled,true);assert.equal(el('pitch-apply').disabled,true);
});
