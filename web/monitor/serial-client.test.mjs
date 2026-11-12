import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {PassThrough} from 'node:stream';
import {createHmac} from 'node:crypto';
import {SerialDeviceClient} from './serial-client.mjs';
import {Monitor} from './core.mjs';
import {FrameParser,encodeFrame} from './serial-wire.mjs';
const binding={device_id:'aabbccddeeff',token:'a'.repeat(32),address:'http://127.0.0.1:8080'};
function bridge({badProof=false,dropControl=false,helloDelay=0,infoDelay=0}={}){
 const child=new EventEmitter();child.stdin=new PassThrough();child.stdout=new PassThrough();child.stderr=new PassThrough();child.killed=false;
 child.kill=()=>{child.killed=true;child.emit('close',0);};const calls=[];
 const send=m=>child.stdout.write(encodeFrame(1,Buffer.from(JSON.stringify(m))));
 child.stdin.on('data',b=>parser.feed(b));
 const parser=new FrameParser((kind,p)=>{
  const q=JSON.parse(p);calls.push(q);let body={accepted:true};
  if(q.op==='hello')body={device_id:binding.device_id,nonce:q.nonce,boot_id:'boot',protocol:1,proof:badProof?'0'.repeat(64):createHmac('sha256',binding.token).update(`chan-discovery-v1\n${binding.device_id}\n${q.nonce}`).digest('hex')};
  if(q.op==='info')body={device_id:binding.device_id,boot_id:'boot',firmware:'test'};
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
 const states=[];let stale=0;c.on('state',s=>states.push(s));c.on('stale',()=>stale++);
 b.send({type:'state',boot_id:'boot',revision:1,full:true,data:{boot_id:'boot',seq:1,power:{charging:true}}});
 b.send({type:'state',boot_id:'boot',revision:3,base_revision:2,full:false,data:{seq:2}});
 await new Promise(r=>setImmediate(r));assert.equal(states.length,1);assert.equal(stale,1);assert.equal(b.calls.filter(x=>x.op==='snapshot').length,1);await c.close();
});
