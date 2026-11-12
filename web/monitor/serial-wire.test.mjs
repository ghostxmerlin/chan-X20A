import test from 'node:test';
import assert from 'node:assert/strict';
import {crc32,encodeFrame,FrameParser,mergePatch,StateCache} from './serial-wire.mjs';
test('wire matches independent CRC vector and accepts fragments and noise',()=>{
 assert.equal(crc32(Buffer.from('123456789')),0xcbf43926);
 assert.equal(encodeFrame(1,Buffer.from('{}')).toString('hex'),'53434d31010100000200000043bfa6a37b7d');
 const out=[],p=new FrameParser((kind,b)=>out.push([kind,b.toString()]));
 for(const b of Buffer.concat([Buffer.from('ROM log\r\n'),encodeFrame(1,Buffer.from('{}'))]))p.feed(Buffer.from([b]));
 assert.deepEqual(out,[[1,'{}']]);
});
test('corruption, oversize and truncated candidates recover with bounded buffer',()=>{
 let n=0,now=1;const p=new FrameParser(()=>n++,()=>now),ok=encodeFrame(1,Buffer.from('{}'));
 const bad=Buffer.from(ok);bad[17]=0;p.feed(Buffer.concat([bad,ok]));assert.equal(n,1);
 const large=Buffer.from(ok);large.writeUInt32LE(0xffffffff,8);p.feed(Buffer.concat([large,ok]));assert.equal(n,2);
 const cut=Buffer.from(ok);cut.writeUInt32LE(120000,8);p.feed(Buffer.concat([cut,ok]));assert.equal(n,2);
 now=2000;p.expire();assert.equal(n,3);assert.ok(p.buffer.length<16);
});
test('large progressing reply survives a busy-device transfer longer than 250 ms',()=>{
 let now=1;const received=[];const p=new FrameParser((kind,data)=>received.push(data),()=>now);
 const payload=Buffer.alloc(24000,120),wire=encodeFrame(1,payload);
 p.feed(wire.subarray(0,8192));now=400;p.expire();p.feed(wire.subarray(8192,16384));now=800;p.expire();p.feed(wire.subarray(16384));
 assert.equal(received.length,1);assert.deepEqual(received[0],payload);assert.equal(p.errors,0);
});
test('patch preserves nulls, replaces arrays, rejects prototype fields',()=>{
 const d=mergePatch({a:{x:1,y:2},faces:[1,2]},{a:{x:null},faces:[3]});
 assert.deepEqual(d,{a:{x:null,y:2},faces:[3]});
 assert.throws(()=>mergePatch({},JSON.parse('{"__proto__":{"polluted":1}}')));
});
test('missing revision disables state until a new full snapshot; wrong boot rejected',()=>{
 const c=new StateCache('boot');
 assert.equal(c.accept({boot_id:'boot',revision:1,full:true,data:{seq:1,power:{charging:true}}}).power.charging,true);
 assert.equal(c.accept({boot_id:'boot',revision:3,base_revision:2,full:false,data:{seq:2}}),null);
 assert.equal(c.valid,false);
 assert.equal(c.accept({boot_id:'boot',revision:4,full:true,data:{seq:3,power:{charging:false}}}).power.charging,false);
 assert.equal(c.accept({boot_id:'old',revision:5,full:true,data:{}}),null);
});
