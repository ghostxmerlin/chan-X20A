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
