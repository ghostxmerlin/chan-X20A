import test from 'node:test';
import assert from 'node:assert/strict';
import { ControlPanel, InteractionPanel } from './public/control-ui.js';
function harness(send) {
 const elements=new Map();
 const doc={getElementById(id){if(!elements.has(id))elements.set(id,{textContent:'',value:'',disabled:false,addEventListener(e,fn){this[e]=fn;},setAttribute(){}});return elements.get(id);}};
 const calls=[];const ui=new ControlPanel(doc,send|| (async body=>{calls.push(body);return {message:'已接收'};}));
 const s={boot:'a',capabilities:{web_control:'supported',yaw_step_45:'supported'},telemetry:{timestamp_us:2000000,m12:{valid:true,view_enabled:false},remote:{motor:{enabled:true,requested:true,timestamp_us:2000000,yaw:{valid:true,angle_deg:0},yaw_trajectory_complete:true}},pitch_setting:{configured_deg:10,feedback_valid:true,feedback_timestamp_us:2000000,state:'complete'}}};
 return {ui,s,calls,doc,el:id=>doc.getElementById(id)};
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
test('m12d: the manual recognition button is gone; detection needs an online M12',async()=>{
 const {ui,s,calls,el}=harness();s.telemetry.m12.online=false;ui.render(s,true);
 assert.equal(el('m12-start').disabled,true);await el('m12-start').click();assert.equal(calls.length,0);
 s.telemetry.m12.online=true;ui.render(s,true);await el('m12-start').click();assert.deepEqual(calls,[{action:'m12',enabled:true,boot:'a'}]);
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
test('control panel leaves preview transmission controls to their own owner',()=>{
 const {ui,s,calls,el}=harness();ui.render(s,true);
 el('camera-toggle').textContent='开始传输';el('camera-toggle').disabled=false;
 s.capabilities.camera_control='supported';s.telemetry.health={camera_enabled:false};ui.render(s,true);
 assert.equal(el('camera-toggle').click,undefined);assert.equal(el('camera-toggle').textContent,'开始传输');assert.equal(el('camera-toggle').disabled,false);assert.equal(calls.length,0);
});
test('mode firmware explains four-way remote toggles while preserving explicit web movement',async()=>{
 const {ui,s,calls,el}=harness();s.capabilities.interaction_modes='supported';ui.render(s,true);
 assert.match(el('remote-pitch-note').textContent,/左追踪.*右认人.*上听觉.*下预留/);
 assert.match(el('remote-pitch-note').title,/中心.*开关屏幕/);
 await el('yaw-left').click();await el('pitch-apply').click();
 assert.deepEqual(calls,[{action:'yaw',delta_deg:-45,boot:'a'},{action:'pitch',angle_deg:10,boot:'a'}]);
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
test('main yaw needs the 45-degree capability and uses two slots each side',async()=>{
 const {ui,s,calls,el}=harness();delete s.capabilities.yaw_step_45;ui.render(s,true);
 assert.equal(el('yaw-left').disabled,true);await el('yaw-left').click();assert.equal(calls.length,0);
 s.capabilities.yaw_step_45='supported';s.telemetry.remote.motor.web_yaw={measured_slot:2};ui.render(s,true);
 assert.equal(el('yaw-right').disabled,true);assert.equal(el('yaw-left').disabled,false);
 await el('yaw-left').click();assert.deepEqual(calls,[{action:'yaw',delta_deg:-45,boot:'a'}]);
 s.telemetry.remote.motor.web_yaw.measured_slot=-2;ui.render(s,true);assert.equal(el('yaw-left').disabled,true);
});
test('timed turn is explicit, locally calibrated, gated and never replayed by rendering',async()=>{
 const {ui,s,calls,el}=harness();ui.render(s,true);assert.equal(el('yaw-turn').disabled,true);
 s.capabilities.yaw_turn='supported';s.telemetry.remote.motor.yaw_turn={state:'idle',busy:false,default_duration_ms:1000};ui.render(s,true);
 assert.equal(el('yaw-turn-duration').value,'1000');el('yaw-turn-duration').value='1250';
 await el('yaw-turn').click();assert.deepEqual(calls,[{action:'yaw_turn',duration_ms:1250,boot:'a'}]);
 for(const value of ['99','2001','100.5','']){el('yaw-turn-duration').value=value;await el('yaw-turn').click();}assert.equal(calls.length,1);
 el('yaw-turn-duration').value='1250';ui.render(s,false);await el('yaw-turn').click();s.boot='b';ui.render(s,true);
 assert.equal(calls.length,1);assert.equal(el('yaw-turn-duration').value,'1250');
 s.telemetry.pitch_setting.state='moving';ui.render(s,true);assert.equal(el('yaw-turn').disabled,true);
 s.telemetry.pitch_setting.state='complete';s.telemetry.remote.motor.web_yaw={state:'queued'};ui.render(s,true);assert.equal(el('yaw-turn').disabled,true);
 s.telemetry.remote.motor.web_yaw.state='idle';s.telemetry.remote.motor.yaw_trajectory_complete=false;ui.render(s,true);assert.equal(el('yaw-turn').disabled,false);
});
test('turn telemetry blocks other motion and keeps its estimated state visible',async()=>{
 const {ui,s,el,doc}=harness();Object.assign(s.capabilities,{yaw_turn:'supported',yaw_step_diagnostics:'supported',interaction_modes:'supported',face_tracking:'supported'});
 s.telemetry.interaction={tracking:{requested:false}};const interaction=new InteractionPanel(doc);
 const motor=s.telemetry.remote.motor;motor.yaw_turn={state:'rotating',busy:true,duration_ms:1000,pwm:400,direction:-1,request_seq:1};ui.render(s,true);interaction.render(s,true);
 for(const id of ['yaw-left','yaw-right','yaw-center','yaw-turn','yaw-step-left','yaw-step-right','pitch-apply','pitch-input','motor-enable','interaction-tracking'])assert.equal(el(id).disabled,true,id);
 assert.equal(el('motor-disable').disabled,false);assert.match(el('yaw-turn-state').textContent,/转身中/);
 s.telemetry.interaction.tracking.requested=true;interaction.render(s,true);assert.equal(el('interaction-tracking').disabled,false);
 motor.yaw_turn={state:'estimated_complete',busy:false,duration_ms:1000,request_seq:1};ui.render(s,true);assert.match(el('yaw-turn-state').textContent,/估计/);assert.doesNotMatch(el('yaw-turn-state').textContent,/已到位|实测.*180/);
 ui.render(s,false);assert.match(el('yaw-turn-state').textContent,/过期|等待/);
});
test('stop remains available during an uncertain turn request and cannot unlock pending controls',async()=>{
 let release;const calls=[];const {ui,s,el}=harness(async body=>{calls.push(body);if(body.action==='yaw_turn')await new Promise(r=>release=r);return {message:'已接收'};});
 s.capabilities.yaw_turn='supported';ui.render(s,true);const turn=el('yaw-turn').click();
 assert.equal(el('motor-disable').disabled,false);await el('motor-disable').click();assert.equal(el('yaw-left').disabled,true);
 assert.deepEqual(calls,[{action:'yaw_turn',duration_ms:1200,boot:'a'},{action:'motor',enabled:false,boot:'a'}]);release();await turn;
});
test('stop result survives an older turn response while independent panel feedback still completes',async()=>{
 let release;const {ui,s,el}=harness(body=>body.action==='motor'?Promise.resolve({message:'停止已接收'}):new Promise(r=>release=r));
 s.capabilities.yaw_turn='supported';ui.render(s,true);
 const turn=el('yaw-turn').click();await el('motor-disable').click();release({message:'转身已接收'});await turn;
 assert.equal(el('servo-command').textContent,'停止已接收');
 const m12=el('m12-start').click();await el('motor-disable').click();release({message:'检测已接收'});await m12;
 assert.equal(el('m12-command').textContent,'检测已接收');assert.equal(el('servo-command').textContent,'停止已接收');
});
test('uncertain turn results never replay after polling or reboot',async()=>{
 const calls=[];const {ui,s,el}=harness(async body=>{calls.push(body);throw Object.assign(new Error('timeout'),{name:'TimeoutError'});});
 s.capabilities.yaw_turn='supported';ui.render(s,true);await el('yaw-turn').click();assert.match(el('servo-command').textContent,/不会自动重发/);
 ui.render(s,false);s.boot='b';ui.render(s,true);ui.render(s,true);assert.equal(calls.length,1);
});
test('ordinary yaw accepts feedback through 128 degrees and rejects out of range or non-finite feedback',()=>{
 const {ui,s,el}=harness();
 for(const angle of [-128,128]){s.telemetry.remote.motor.yaw.angle_deg=angle;ui.render(s,true);assert.equal(el('yaw-center').disabled,false);}
 for(const angle of [-129,129,180,NaN,null]){s.telemetry.remote.motor.yaw.angle_deg=angle;ui.render(s,true);assert.equal(el('yaw-center').disabled,true);}
});
