import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {PassThrough} from 'node:stream';
import {createHmac} from 'node:crypto';
import {SerialDeviceClient} from './serial-client.mjs';
import {Monitor} from './core.mjs';
import {FrameParser,encodeFrame} from './serial-wire.mjs';
const binding={device_id:'aabbccddeeff',token:'a'.repeat(32),address:'http://127.0.0.1:8080'};
function bridge({badProof=false,dropControl=false,helloDelay=0,infoDelay=0,streamSupport=false,subscribeDelay=0,streamsDelay=0,closeDelay=0}={}){
 const child=new EventEmitter();child.stdin=new PassThrough();child.stdout=new PassThrough();child.stderr=new PassThrough();child.killed=false;
 child.kill=()=>{child.killed=true;if(closeDelay)setTimeout(()=>child.emit('close',0),closeDelay);else child.emit('close',0);};const calls=[];
 const send=m=>child.stdout.write(encodeFrame(1,Buffer.from(JSON.stringify(m))));
 child.stdin.on('data',b=>parser.feed(b));
 const parser=new FrameParser((kind,p)=>{
  const q=JSON.parse(p);calls.push(q);let body={accepted:true};
  if(q.op==='hello')body={device_id:binding.device_id,nonce:q.nonce,boot_id:'boot',protocol:1,proof:badProof?'0'.repeat(64):createHmac('sha256',binding.token).update(`chan-discovery-v1\n${binding.device_id}\n${q.nonce}`).digest('hex')};
  if(q.op==='info')body={device_id:binding.device_id,boot_id:'boot',firmware:'test',capabilities:streamSupport?{monitor_streams:'supported'}:{}};
  if((q.op==='subscribe'&&subscribeDelay)||(q.op==='streams'&&streamsDelay)){
   setTimeout(()=>{if(!child.killed)send({type:'reply',id:q.id,status:200,body});},q.op==='subscribe'?subscribeDelay:streamsDelay);return;
  }
  if(q.op==='control'&&dropControl)return;
  if(q.op==='info'&&infoDelay){
   send({type:'state',boot_id:'boot',revision:1,full:true,data:{boot_id:'boot',seq:99}});
   setTimeout(()=>{if(!child.killed)send({type:'reply',id:q.id,status:200,body});},infoDelay);return;
  }
  if(q.op==='hello'&&helloDelay)setTimeout(()=>{if(!child.killed)send({type:'reply',id:q.id,status:200,body});},helloDelay);
  else send({type:'reply',id:q.id,status:200,body});
 });return {child,calls,send};
}
test('leftover telemetry during info handshake cannot trigger snapshot traffic',async()=>{
 const children=[];const c=new SerialDeviceClient(binding,{spawnFn:()=>{const b=bridge({infoDelay:20});children.push(b);return b.child;},timeout:100});
 const states=[];c.on('state',s=>states.push(s));
 try{
  await c.connect();assert.equal(states.length,0);assert.equal(children[0].calls.filter(x=>x.op==='snapshot').length,0);
  children[0].send({type:'state',boot_id:'boot',revision:2,full:true,data:{boot_id:'boot',seq:100}});assert.equal(states.length,1);
  await c.close();await c.connect();assert.equal(states.length,1);assert.equal(children[1].calls.filter(x=>x.op==='snapshot').length,0);
 }finally{await c.close();}
});
test('verified serial session handles control once and rejects stale boot before sending',async()=>{
 const b=bridge(),c=new SerialDeviceClient(binding,{spawnFn:()=>b.child,timeout:100});
 try{await c.connect();assert.equal(c.info.boot_id,'boot');assert.equal((await c.control('/v1/control',{target:'camera',enabled:false},'boot',()=>{})).accepted,true);
  await assert.rejects(()=>c.control('/v1/control',{},'old',()=>{}));assert.equal(b.calls.filter(x=>x.op==='control').length,1);
 }finally{await c.close();}assert.equal(b.child.killed,true);
});
test('rapid pause and resume does not let the old loop close the new bridge',async()=>{
 const children=[];const c=new SerialDeviceClient(binding,{spawnFn:()=>{const b=bridge({helloDelay:20});children.push(b);return b.child;},timeout:100});
 const m=new Monitor(c);m.start();await new Promise(r=>setTimeout(r,5));m.stop();m.start();
 try{await new Promise(r=>setTimeout(r,100));assert.equal(c.verified,true);assert.equal(children.length,2);assert.equal(children[1].child.killed,false);}
 finally{m.stop();await c.close();}
});
test('identity mismatch never sends token; disconnect rejects command without replay',async()=>{
 const wrong=bridge({badProof:true}),a=new SerialDeviceClient(binding,{spawnFn:()=>wrong.child,timeout:100});
 await assert.rejects(()=>a.connect());assert.equal(wrong.calls.length,1);assert.equal(wrong.calls[0].token,undefined);await a.close();
 const b=bridge({dropControl:true}),c=new SerialDeviceClient(binding,{spawnFn:()=>b.child,timeout:30});await c.connect();
 await assert.rejects(()=>c.control('/v1/control',{target:'voice_play',clip_id:1},'boot',()=>{}));
 assert.equal(b.calls.filter(x=>x.op==='control').length,1);await c.close();
});
test('lost state revision requests resync and exposes no partial state',async()=>{
 const b=bridge(),c=new SerialDeviceClient(binding,{spawnFn:()=>b.child,timeout:100});await c.connect();
 await c.subscribe();
 const states=[];let stale=0;c.on('state',s=>states.push(s));c.on('stale',()=>stale++);
 b.send({type:'state',boot_id:'boot',revision:1,full:true,data:{boot_id:'boot',seq:1,power:{charging:true}}});
 b.send({type:'state',boot_id:'boot',revision:3,base_revision:2,full:false,data:{seq:2}});
 await new Promise(r=>setImmediate(r));assert.equal(states.length,1);assert.equal(stale,1);assert.equal(b.calls.filter(x=>x.op==='snapshot').length,1);await c.close();
});

test('streams default off, use the negotiated capability and coalesce changes during subscribe',async()=>{
 const b=bridge({streamSupport:true,subscribeDelay:15,streamsDelay:5}),c=new SerialDeviceClient(binding,{spawnFn:()=>b.child,timeout:100});
 try{
  await c.setStreams({camera:true});assert.equal(b.calls.length,0);
  await c.connect();
  const starting=c.subscribe();
  await c.setStreams({camera:false,servo_diagnostics:true});
  await starting;
  assert.deepEqual(b.calls.filter(q=>['subscribe','streams'].includes(q.op)).map(q=>[q.op,q.camera,q.servo_diagnostics]),[
   ['subscribe',true,false],['streams',false,true]
  ]);
  const a=c.setStreams({camera:true,servo_diagnostics:true});
  await new Promise(r=>setImmediate(r));
  const z=c.setStreams({camera:false,servo_diagnostics:false});await Promise.all([a,z]);
  assert.equal(b.calls.at(-1).op,'streams');assert.equal(b.calls.at(-1).camera,false);assert.equal(b.calls.at(-1).servo_diagnostics,false);
  await c.setStreams({camera:false});assert.equal(b.calls.filter(q=>q.op==='control').length,0);
 }finally{await c.close();}
 const legacy=bridge(),old=new SerialDeviceClient(binding,{spawnFn:()=>legacy.child});
 try{
  await old.connect();await old.subscribe({camera:true,servo_diagnostics:true});await old.setStreams({camera:false});
  const sub=legacy.calls.find(q=>q.op==='subscribe');assert.equal(sub.camera,undefined);assert.equal(sub.servo_diagnostics,undefined);
  assert.equal(legacy.calls.filter(q=>q.op==='streams').length,0);
 }finally{await old.close();}
 const modern=bridge({streamSupport:true}),off=new SerialDeviceClient(binding,{spawnFn:()=>modern.child});
 try{await off.connect();await off.subscribe();const q=modern.calls.find(q=>q.op==='subscribe');assert.equal(q.camera,false);assert.equal(q.servo_diagnostics,false);}
 finally{await off.close();}
});

test('viewer demand while hello/info is pending produces no invalid pre-subscription RPC',async()=>{
 const b=bridge({streamSupport:true,helloDelay:10,infoDelay:15}),c=new SerialDeviceClient(binding,{spawnFn:()=>b.child,timeout:100});
 try{
  const connecting=c.connect();await c.setStreams({camera:true});
  await new Promise(r=>setTimeout(r,13));await c.setStreams({servo_diagnostics:true});
  assert.deepEqual(b.calls.map(q=>q.op),['hello','info']);
  await connecting;await c.subscribe();
  assert.deepEqual(b.calls.map(q=>q.op),['hello','info','subscribe']);
  assert.equal(b.calls.at(-1).camera,false);assert.equal(b.calls.at(-1).servo_diagnostics,true);
 }finally{await c.close();}
});

test('late close and failed streams from an old bridge cannot disconnect its replacement',async()=>{
 const old=bridge({streamSupport:true,streamsDelay:20,closeDelay:20}),next=bridge({streamSupport:true});let opens=0;
 const c=new SerialDeviceClient(binding,{spawnFn:()=>opens++?next.child:old.child,timeout:100});
 try{
  await c.connect();await c.subscribe();
  const updating=c.setStreams({camera:true});await new Promise(r=>setImmediate(r));
  c.disconnect(new Error('unplugged'));await assert.rejects(updating);
  await c.connect();await c.subscribe({servo_diagnostics:true});
  await new Promise(r=>setTimeout(r,30));
  assert.equal(c.verified,true);assert.equal(next.child.killed,false);
  assert.equal(next.calls.filter(q=>q.op==='control').length,0);
  assert.equal(next.calls.find(q=>q.op==='subscribe').camera,false);
 }finally{await c.close();}
});

test('USB JPEGs are ignored when preview demand is off and after disconnect',async()=>{
 const b=bridge({streamSupport:true}),c=new SerialDeviceClient(binding,{spawnFn:()=>b.child});const frames=[];c.on('frame',f=>frames.push(f));
 try{
  await c.connect();await c.subscribe();
  b.send({type:'state',boot_id:'boot',revision:1,full:true,data:{boot_id:'boot',seq:1}});
  const jpeg=Buffer.alloc(18);jpeg.writeUInt32LE(1);jpeg[12]=255;jpeg[13]=216;
  c.receive(2,jpeg);assert.equal(frames.length,0);
  await c.setStreams({camera:true});c.receive(2,jpeg);assert.equal(frames.length,1);
  await c.setStreams({camera:false});c.receive(2,jpeg);assert.equal(frames.length,1);
  c.disconnect(new Error('unplugged'));c.receive(2,jpeg);assert.equal(frames.length,1);
 }finally{await c.close();}
});

test('an unconfirmed streams OFF ends lease renewal without replaying a control',async()=>{
 const b=bridge({streamSupport:true,streamsDelay:60}),c=new SerialDeviceClient(binding,{spawnFn:()=>b.child,timeout:15});
 try{
  await c.connect();await c.subscribe({camera:true});
  await assert.rejects(c.setStreams({camera:false}));
  assert.equal(c.verified,false);assert.equal(c.subscribed,false);assert.equal(b.child.killed,true);
  assert.equal(b.calls.filter(q=>q.op==='streams').length,1);assert.equal(b.calls.filter(q=>q.op==='control').length,0);
 }finally{await c.close();}
});
