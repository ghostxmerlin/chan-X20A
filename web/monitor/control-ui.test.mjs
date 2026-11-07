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
