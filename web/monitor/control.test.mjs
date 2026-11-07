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
 for(const id of [-1,21,1.5,'1',null]) await assert.rejects(c.execute({...request,clip_id:id}));
 m.telemetry.playback.busy=true;await assert.rejects(c.execute({...request,clip_id:1}));
 m.telemetry.playback.busy=false;m.telemetry.health.audio_ready=false;await assert.rejects(c.execute(request));
 assert.equal(calls.length,1);
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
test('controls whitelist schema, boot, freshness and permissions before writing',async()=>{
  const {m,c,calls}=fixture();
  for(const input of [{action:'shell',boot:'boot-a'}, {action:'yaw',delta_deg:40,boot:'boot-a'}, {action:'motor',enabled:'false',boot:'boot-a'}, {action:'pitch',angle_deg:10.01,boot:'boot-a'}, {action:'m12',enabled:true,extra:1,boot:'boot-a'}, {action:'m12',enabled:true,boot:'old'}]) await assert.rejects(c.execute(input));
  m.telemetryAt=Date.now()-2000;await assert.rejects(c.execute({action:'m12',enabled:true,boot:'boot-a'}));
  assert.equal(calls.length,0);
  m.telemetryAt=Date.now();m.telemetry.remote.motor.yaw.valid=false;
  await assert.rejects(c.execute({action:'yaw',delta_deg:5,boot:'boot-a'}));
  await c.execute({action:'motor',enabled:false,boot:'boot-a'}); assert.equal(calls.length,1);
});
test('single command never retries after unknown network result and rejects competing actions',async()=>{
  const {m,c,calls}=fixture();let fail;
  m.client.control=()=>{calls.push(1);return new Promise((_,reject)=>fail=reject);};
  const first=c.execute({action:'m12',enabled:true,boot:'boot-a'});
  await assert.rejects(c.execute({action:'yaw',delta_deg:5,boot:'boot-a'}), /正在/);
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
