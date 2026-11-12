import test from 'node:test';
import assert from 'node:assert/strict';
import {Monitor} from './core.mjs';
test('independent events do not invent sensor samples or refresh expired telemetry',()=>{
 const m=new Monitor({binding:{},verified:true});m.acceptInfo({boot_id:'b',capabilities:{}});
 m.acceptTelemetry({boot_id:'b',seq:7,timestamp_us:1000000,asr:{events:[]}},1000);
 m.acceptEvents({boot_id:'b',timestamp_us:1100000,asr:{events:[{seq:1,timestamp_us:1050000,id:20,word:'next'}]}},3000);
 assert.equal(m.telemetry.seq,7);assert.equal(m.telemetryAt,1000);assert.equal(m.snapshot(3000).fresh.telemetry,false);
 assert.equal(m.events.filter(x=>x.kind==='asr').length,1);
 m.acceptEvents({boot_id:'b',timestamp_us:1200000,asr:{events:[{seq:1,id:20}]}},3100);
 assert.equal(m.events.filter(x=>x.kind==='asr').length,1);
 m.acceptEvents({boot_id:'old',timestamp_us:1200000,asr:{events:[{seq:2,id:20}]}},3200);
 assert.equal(m.events.filter(x=>x.kind==='asr').length,1);
});

test('a full diagnostic refresh on the same sensor sequence replaces fields without renewing freshness',()=>{
 const m=new Monitor({binding:{}});m.acceptInfo({boot_id:'b'});
 m.acceptTelemetry({boot_id:'b',seq:7,remote:{motor:{yaw:{valid:true}}}},1000);
 m.acceptTelemetry({boot_id:'b',seq:7,remote:{motor:{yaw:{valid:true},tuning:{p:15}}}},2000);
 assert.equal(m.telemetry.remote.motor.tuning.p,15);assert.equal(m.telemetryAt,1000);
 m.acceptTelemetry({boot_id:'b',seq:7,remote:{motor:{yaw:{valid:true}}}},3000);
 assert.equal(m.telemetry.remote.motor.tuning,undefined);assert.equal(m.snapshot(3000).fresh.telemetry,false);
});

test('viewer preferences OR together, last viewer off clears preview immediately, and removal is idempotent',()=>{
 const calls=[];const m=new Monitor({binding:{},setStreams:options=>{calls.push(options);}});m.running=true;
 const idle=m.addViewer(),camera=m.addViewer({camera:true}),servo=m.addViewer({servo_diagnostics:true}),second=m.addViewer({camera:true});
 assert.deepEqual(m.streams,{camera:true,servo_diagnostics:true});assert.equal(m.viewers,4);
 m.frame={seq:1,at:Date.now(),bytes:Buffer.from('jpeg')};m.removeViewer(camera);assert.ok(m.frame);m.removeViewer(camera);assert.equal(m.viewers,3);
 m.removeViewer(second);assert.equal(m.frame,null);assert.equal(m.snapshot().fresh.camera,false);assert.deepEqual(calls.at(-1),{camera:false,servo_diagnostics:true});
 m.removeViewer(servo);assert.deepEqual(calls.at(-1),{camera:false,servo_diagnostics:false});assert.equal(m.running,true);
 m.removeViewer(idle);clearTimeout(m.idleTimer);assert.equal(m.viewers,0);
});

test('push preview works with business camera off, clears on disconnect, and reconnect uses current demands',async()=>{
 const {EventEmitter}=await import('node:events');const client=new EventEmitter();client.push=true;client.verified=true;client.closed=Promise.resolve();
 const calls=[];client.connect=async()=>({boot_id:'b',capabilities:{monitor_streams:'supported'}});client.subscribe=async options=>calls.push(['subscribe',{...options}]);
 client.setStreams=async options=>calls.push(['streams',{...options}]);client.close=async()=>{};
 const m=new Monitor(client),idle=m.addViewer(),viewer=m.addViewer({camera:true});await new Promise(r=>setImmediate(r));
 try{
  client.emit('state',{boot_id:'b',seq:1,health:{camera_enabled:false}});
  client.emit('frame',{seq:1,at:Date.now(),bytes:Buffer.from('jpeg')});assert.equal(m.snapshot().fresh.camera,true);
  client.emit('state',{boot_id:'b',seq:2,health:{camera_enabled:false}});assert.ok(m.frame);
  m.removeViewer(viewer);assert.equal(m.frame,null);
  client.emit('frame',{seq:2,at:Date.now(),bytes:Buffer.from('jpeg')});assert.equal(m.frame,null);
  const second=m.addViewer({camera:true});client.emit('frame',{seq:3,at:Date.now(),bytes:Buffer.from('jpeg')});
  client.emit('disconnect',new Error('USB disconnected'));assert.equal(m.frame,null);assert.equal(m.online,false);
  m.removeViewer(second);assert.deepEqual(m.streams,{camera:false,servo_diagnostics:false});
  const deadline=Date.now()+2500;
  while(calls.filter(c=>c[0]==='subscribe').length<2&&Date.now()<deadline)await new Promise(r=>setTimeout(r,10));
  assert.deepEqual(calls.filter(c=>c[0]==='subscribe'),[['subscribe',{camera:true,servo_diagnostics:false}],['subscribe',{camera:false,servo_diagnostics:false}]]);
 }finally{m.removeViewer(idle);clearTimeout(m.idleTimer);m.stop();}
});
