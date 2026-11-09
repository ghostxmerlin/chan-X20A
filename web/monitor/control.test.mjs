import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { Monitor, DeviceClient, deviceRequest } from './core.mjs';
import { Controls } from './control.mjs';
import { createMonitorServer } from './server.mjs';
function fixture() {
  const calls=[];
  const m=new Monitor({binding:{}, verified:true, control:async (...a)=>{calls.push(a);return {accepted:true,request_seq:3};}});
  m.online=m.running=true; m.info={boot_id:'boot-a',capabilities:{web_control:'supported'}};
  m.telemetryAt=Date.now();m.telemetry={timestamp_us:2000000,m12:{valid:true},remote:{motor:{enabled:true,requested:true,yaw:{valid:true,angle_deg:0},timestamp_us:2000000,yaw_trajectory_complete:true}},pitch_setting:{state:'complete'}};
  return {m,calls,c:new Controls(m)};
}
test('local voice is explicit, bounded, fresh, audio-ready and single-job',async()=>{
 const {m,c,calls}=fixture();
 const request={action:'voice',clip_id:0,boot:'boot-a'};
 await assert.rejects(c.execute(request));
 m.info.capabilities.voice_library='supported';m.telemetry.health={audio_ready:true};m.telemetry.playback={busy:false};
 await c.execute(request);assert.equal(calls[0][0],'/v1/voice/play');assert.deepEqual(calls[0][1],{clip_id:0});
 for(const id of [21,40]) {await c.execute({...request,clip_id:id});assert.equal(calls.at(-1)[1].clip_id,id);}
 for(const id of [-1,41,1.5,'1',null]) await assert.rejects(c.execute({...request,clip_id:id}));
 m.telemetry.playback.busy=true;await assert.rejects(c.execute({...request,clip_id:1}));
 m.telemetry.playback.busy=false;m.telemetry.health.audio_ready=false;await assert.rejects(c.execute(request));
 assert.equal(calls.length,3);
});
test('yaw buttons send one 20-degree slot and center sends slot 0',async()=>{
 const {m,c,calls}=fixture();
 await c.execute({action:'yaw',delta_deg:20,boot:'boot-a'});assert.deepEqual(calls[0][1],{delta_deg:20});
 await c.execute({action:'yaw_center',slot:0,boot:'boot-a'});assert.equal(calls[1][0],'/v1/motion/yaw-step');assert.deepEqual(calls[1][1],{slot:0});
 for(const bad of [{action:'yaw',delta_deg:5,boot:'boot-a'},{action:'yaw',delta_deg:-10,boot:'boot-a'},{action:'yaw_center',slot:1,boot:'boot-a'}]) await assert.rejects(c.execute(bad));
 assert.equal(calls.length,2);
});
test('wifi profile actions require the capability and validate ssid/password shapes',async()=>{
 const {m,c,calls}=fixture();
 const add={action:'wifi_add',ssid:'office-example',password:'placeholder-1',preferred:true,boot:'boot-a'};
 await assert.rejects(c.execute(add));
 m.info.capabilities.wifi_profiles='supported';
 await c.execute(add);assert.equal(calls[0][0],'/v1/wifi');assert.deepEqual(calls[0][1],{op:'add',ssid:'office-example',password:'placeholder-1',preferred:true});
 await c.execute({action:'wifi_add',ssid:'open-example',password:'',preferred:false,boot:'boot-a'});assert.deepEqual(calls[1][1],{op:'add',ssid:'open-example',password:'',preferred:false});
 await c.execute({action:'wifi_remove',ssid:'office-example',boot:'boot-a'});assert.deepEqual(calls[2][1],{op:'remove',ssid:'office-example'});
 await c.execute({action:'wifi_prefer',ssid:'open-example',boot:'boot-a'});assert.deepEqual(calls[3][1],{op:'prefer',ssid:'open-example'});
 await c.execute({action:'wifi_scan',boot:'boot-a'});assert.deepEqual(calls[4][1],{op:'scan'});
 await c.execute({action:'wifi_reconnect',boot:'boot-a'});assert.deepEqual(calls[5][1],{op:'reconnect'});
 for(const bad of [{...add,ssid:''},{...add,ssid:'x'.repeat(33)},{...add,password:'short'},{...add,preferred:'yes'},{action:'wifi_remove',boot:'boot-a'},{action:'wifi_scan',extra:1,boot:'boot-a'}]) await assert.rejects(c.execute(bad));
 assert.equal(calls.length,6);
});
test('servo tuning actions require the capability, an axis and in-range values',async()=>{
 const {m,c,calls}=fixture();
 const eeprom={action:'servo_eeprom',axis:'yaw',p:30,d:15,i:0,punch:200,dead_cw:2,dead_ccw:2,max_torque:1000,boot:'boot-a'};
 await assert.rejects(c.execute(eeprom));
 m.info.capabilities.servo_tuning='supported';
 await c.execute(eeprom);assert.equal(calls[0][0],'/v1/servo');assert.deepEqual(calls[0][1],{op:'eeprom',axis:'yaw',p:30,d:15,i:0,punch:200,dead_cw:2,dead_ccw:2,max_torque:1000});
 await c.execute({action:'servo_refresh',axis:'pitch',boot:'boot-a'});assert.deepEqual(calls[1][1],{op:'refresh',axis:'pitch'});
 await c.execute({action:'servo_restore',axis:'yaw',boot:'boot-a'});assert.deepEqual(calls[2][1],{op:'restore',axis:'yaw'});
 const runtime={action:'servo_runtime',axis:'yaw',goal_time_ms:0,goal_speed:200,spring_stiffness:0,spring_damping:0,hold_min_ms:500,hold_max_ms:3000,tolerance_deg:0.6,boot:'boot-a'};
 await c.execute(runtime);assert.deepEqual(calls[3][1],{op:'runtime',axis:'yaw',goal_time_ms:0,goal_speed:200,spring_stiffness:0,spring_damping:0,hold_min_ms:500,hold_max_ms:3000,tolerance_deg:0.6});
 for(const bad of [{...eeprom,axis:'roll'},{...eeprom,p:255},{...eeprom,punch:1001},{...eeprom,dead_cw:17},{...eeprom,max_torque:-1},{...eeprom,p:1.5},{...runtime,spring_stiffness:50},{...runtime,hold_max_ms:400},{...runtime,goal_speed:1001},{action:'servo_eeprom',axis:'yaw',p:30,boot:'boot-a'}]) await assert.rejects(c.execute(bad));
 assert.equal(calls.length,4);
});
test('diagnostic yaw step requires the capability and exact integer parameters',async()=>{
 const {m,c,calls}=fixture();
 const ok={action:'yaw_step',delta_deg:-15,speed:600,path:'remote',boot:'boot-a'};
 await assert.rejects(c.execute(ok));
 m.info.capabilities.yaw_step_diagnostics='supported';
 await c.execute(ok);assert.equal(calls[0][0],'/v1/motion/yaw-step');assert.deepEqual(calls[0][1],{delta_deg:-15,speed:600,path:'remote'});
 for(const bad of [{...ok,delta_deg:0},{...ok,delta_deg:31},{...ok,delta_deg:2.5},{...ok,speed:40},{...ok,speed:1001},{...ok,path:'joystick'},{action:'yaw_step',delta_deg:5,boot:'boot-a'}]) await assert.rejects(c.execute(bad));
 m.telemetry.remote.motor.web_yaw={state:'moving'};await assert.rejects(c.execute(ok));
 assert.equal(calls.length,1);
});
test('camera streaming toggle requires the new capability and a boolean',async()=>{
 const {m,c,calls}=fixture();
 await assert.rejects(c.execute({action:'camera',enabled:true,boot:'boot-a'}));
 m.info.capabilities.camera_control='supported';
 await c.execute({action:'camera',enabled:true,boot:'boot-a'});
 assert.equal(calls[0][0],'/v1/camera');assert.deepEqual(calls[0][1],{enabled:true});
 await c.execute({action:'camera',enabled:false,boot:'boot-a'});assert.deepEqual(calls[1][1],{enabled:false});
 for(const bad of [{action:'camera',enabled:'true',boot:'boot-a'},{action:'camera',boot:'boot-a'},{action:'camera',enabled:true,extra:1,boot:'boot-a'},{action:'camera',enabled:true,boot:'old'}]) await assert.rejects(c.execute(bad));
 assert.equal(calls.length,2);
});
test('recognition requires new capability and an exact manual mode',async()=>{
 const {m,c,calls}=fixture();
 await assert.rejects(c.execute({action:'m12_mode',mode:'face_recognition',boot:'boot-a'}));
 m.info.capabilities.m12_recognition='supported';
 await c.execute({action:'m12_mode',mode:'face_recognition',boot:'boot-a'});
 assert.equal(calls[0][0],'/v1/m12/mode');assert.deepEqual(calls[0][1],{mode:'face_recognition'});
 await assert.rejects(c.execute({action:'m12_mode',mode:'train',boot:'boot-a'}));
 assert.equal(calls.length,1);
});
test('interaction modes require exact schema, new capabilities and unchanged boot',async()=>{
 const {m,c,calls}=fixture(),request={action:'interaction',mode:'tracking',enabled:true,boot:'boot-a'};
 await assert.rejects(c.execute(request));
 m.info.capabilities.interaction_modes='supported';
 await assert.rejects(c.execute(request));
 m.info.capabilities.face_tracking='supported';
 await c.execute(request);
 assert.deepEqual(calls[0].slice(0,3),['/v1/interaction',{mode:'tracking',enabled:true},'boot-a']);
 for(const changed of [{mode:'auto'},{enabled:1},{extra:true},{boot:'old'}]) await assert.rejects(c.execute({...request,...changed}));
 const {enabled,...missing}=request;await assert.rejects(c.execute(missing));
 m.telemetryAt=Date.now()-2000;await assert.rejects(c.execute(request));
 m.telemetryAt=Date.now();m.client.verified=false;await assert.rejects(c.execute(request));
 assert.equal(calls.length,1);
});
test('interaction enabling respects prerequisites while disabling remains available',async()=>{
 const {m,c,calls}=fixture();Object.assign(m.info.capabilities,{interaction_modes:'supported',face_tracking:'supported'});
 const request={action:'interaction',mode:'tracking',enabled:true,boot:'boot-a'};
 for(const field of ['enabled','requested']) {
  m.telemetry.remote.motor[field]=false;await assert.rejects(c.execute(request));
  await c.execute({...request,enabled:false});m.telemetry.remote.motor[field]=true;
 }
 m.telemetry.m12.valid=false;
 for(const mode of ['tracking','recognition']) {
  await assert.rejects(c.execute({...request,mode}));await c.execute({...request,mode,enabled:false});
 }
 await c.execute({...request,mode:'advanced'});
 m.telemetry.m12.valid=true;m.telemetry.remote.motor.enabled=false;
 await c.execute({...request,mode:'recognition'});
 assert.equal(calls.length,6);
 assert.deepEqual(calls.at(-2)[1],{mode:'advanced',enabled:true});
});
test('queued interaction rechecks freshness and never sends after its boot changed',async t=>{
 const seen=[];const server=http.createServer((req,res)=>{seen.push(req.url);res.end('{"accepted":true}');});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
 const client=new DeviceClient({address:`http://127.0.0.1:${server.address().port}`,token:'a'.repeat(32),device_id:'test-device'});
 client.verified=true;client.info={boot_id:'boot-a'};
 let release;client.requestTail=new Promise(r=>release=r);
 const command=client.control('/v1/interaction',{mode:'tracking',enabled:false},'boot-a',()=>{});
 client.info.boot_id='boot-b';release();await assert.rejects(command);assert.equal(seen.length,0);
 client.info.boot_id='boot-a';
 await client.control('/v1/interaction',{mode:'recognition',enabled:true},'boot-a',()=>{});
 assert.deepEqual(seen,['/v1/interaction']);
 await assert.rejects(client.control('/v1/interaction',{mode:'tracking',enabled:true},'boot-a',()=>{throw new Error('stale');}),/stale/);
 assert.equal(seen.length,1);
});
test('controls whitelist schema, boot, freshness and permissions before writing',async()=>{
  const {m,c,calls}=fixture();
  for(const input of [{action:'shell',boot:'boot-a'}, {action:'yaw',delta_deg:40,boot:'boot-a'}, {action:'motor',enabled:'false',boot:'boot-a'}, {action:'pitch',angle_deg:10.01,boot:'boot-a'}, {action:'m12',enabled:true,extra:1,boot:'boot-a'}, {action:'m12',enabled:true,boot:'old'}]) await assert.rejects(c.execute(input));
  m.telemetryAt=Date.now()-2000;await assert.rejects(c.execute({action:'m12',enabled:true,boot:'boot-a'}));
  assert.equal(calls.length,0);
  m.telemetryAt=Date.now();m.telemetry.remote.motor.yaw.valid=false;
  await assert.rejects(c.execute({action:'yaw',delta_deg:20,boot:'boot-a'}));
  await c.execute({action:'motor',enabled:false,boot:'boot-a'}); assert.equal(calls.length,1);
});
test('single command never retries after unknown network result and rejects competing actions',async()=>{
  const {m,c,calls}=fixture();let fail;
  m.client.control=()=>{calls.push(1);return new Promise((_,reject)=>fail=reject);};
  const first=c.execute({action:'m12',enabled:true,boot:'boot-a'});
  await assert.rejects(c.execute({action:'yaw',delta_deg:20,boot:'boot-a'}), /正在/);
  fail(new Error('connection lost'));await assert.rejects(first,/未确认/);assert.equal(calls.length,1);
});
test('local POST needs exact same origin and JSON; GET never actuates',async t=>{
  const {m,c,calls}=fixture();const server=createMonitorServer(m,c);
  await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
  const base=`http://127.0.0.1:${server.address().port}`;
  const body=JSON.stringify({action:'m12',enabled:false,boot:'boot-a'});
  assert.equal((await fetch(base+'/api/control')).status,404);
  assert.equal((await fetch(base+'/api/control',{method:'POST',body,headers:{'Content-Type':'application/json'}})).status,403);
  assert.equal((await fetch(base+'/api/control',{method:'POST',body,headers:{Origin:base,'Content-Type':'text/plain'}})).status,415);
  assert.equal(calls.length,0);
  const response=await fetch(base+'/api/control',{method:'POST',body,headers:{Origin:base,'Content-Type':'application/json'}});
  assert.equal(response.status,202);assert.equal((await response.json()).accepted,true);assert.equal(calls.length,1);
});
test('real curl POST sends JSON and boot guard, accepts 202, never follows a redirect',async t=>{
  const seen=[];const server=http.createServer((req,res)=>{let body='';req.on('data',b=>body+=b);req.on('end',()=>{seen.push({method:req.method,body,headers:req.headers});res.writeHead(req.url==='/redirect'?302:202,{Location:'/other'});res.end('{"accepted":true}');});});
  await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
  const base=`http://127.0.0.1:${server.address().port}`;
  const options={token:'a'.repeat(32),method:'POST',body:{enabled:false},boot:'boot-a'};
  assert.equal(JSON.parse((await deviceRequest(base+'/control',options)).body).accepted,true);
  assert.equal(seen[0].method,'POST');assert.deepEqual(JSON.parse(seen[0].body),{enabled:false});assert.equal(seen[0].headers['x-boot-id'],'boot-a');
  await assert.rejects(deviceRequest(base+'/redirect',options));assert.equal(seen.length,2);
});
test('real DeviceClient sends local voice through its route and boot whitelist',async t=>{
 const seen=[];const server=http.createServer((req,res)=>{let body='';req.on('data',b=>body+=b);req.on('end',()=>{seen.push({url:req.url,body,headers:req.headers});res.writeHead(202,{'Content-Type':'application/json'});res.end('{"accepted":true,"clip_id":8,"job_id":1}');});});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
 const client=new DeviceClient({address:`http://127.0.0.1:${server.address().port}`,token:'a'.repeat(32),device_id:'test-device'});
 client.verified=true;client.info={boot_id:'boot-a'};let checks=0;
 const result=await client.control('/v1/voice/play',{clip_id:0},'boot-a',()=>checks++);
 assert.equal(result.clip_id,8);assert.equal(checks,1);assert.equal(seen[0].url,'/v1/voice/play');assert.deepEqual(JSON.parse(seen[0].body),{clip_id:0});assert.equal(seen[0].headers['x-boot-id'],'boot-a');
 await assert.rejects(client.control('/v1/voice/play',{clip_id:1},'old',()=>{}));
 await assert.rejects(client.control('/v1/voice/delete',{clip_id:1},'boot-a',()=>{}));assert.equal(seen.length,1);
});
