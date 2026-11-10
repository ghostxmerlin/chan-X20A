import test from 'node:test';
import assert from 'node:assert/strict';
import { M12Tracker } from './m12.mjs';
// Telemetry shaped like collector-0.4.66+ m12_json (m12d).
const data=(us,m={})=>({boot_id:'b',timestamp_us:us,m12:{protocol:'m12d',valid:true,online:true,hb_age_ms:500,
  heartbeat:{t:72,fps:15.2,tier:0,up:100,mem:65000,frames:10,reopens:0,drop:0,mhz:1400},
  function:'detect',requested_function:'detect',mode_request_pending:false,last_result_us:us-100000,num:1,
  faces:[{id:3,x:100,y:80,w:120,h:150,prob:.97,gender:'f',age:31,age_group:'adult',name:'person-a'}],
  persons:[{id:18,x:80,y:60,w:300,h:420,prob:.99,face:3}],library:{valid:true,faces:[{name:'person-a',samples:5}]},commands:[],...m}});
test('detecting: fps and temperature come from the heartbeat, faces carry id/name/attributes',()=>{
 const t=new M12Tracker(),s=t.snapshot(data(5_000_000),true);
 assert.equal(s.status,'detecting');assert.equal(s.fps,15.2);assert.equal(s.temperature,72);
 assert.equal(s.faces[0].id,3);assert.equal(s.faces[0].name,'person-a');assert.equal(s.persons.length,1);assert.equal(s.count,1);
 assert.deepEqual(s.library,[{name:'person-a',samples:5}]);
});
test('offline, serial, hot, standby and pending switches are reported and clear the boxes',()=>{
 const t=new M12Tracker();
 assert.equal(t.snapshot(data(1,{online:false}),true).status,'offline');
 assert.equal(t.snapshot(data(1,{online:false}),true).temperature,null);
 assert.equal(t.snapshot(data(1,{valid:false}),true).status,'serial_unavailable');
 assert.equal(t.snapshot(data(1),false).status,'disconnected');
 const hot=t.snapshot(data(1,{heartbeat:{t:91,fps:0,tier:3}}),true);assert.equal(hot.status,'hot');assert.equal(hot.faces.length,0);
 const sb=t.snapshot(data(1,{function:'standby',requested_function:'standby'}),true);assert.equal(sb.status,'standby');assert.equal(sb.fps,null);assert.equal(sb.faces.length,0);
 assert.equal(t.snapshot(data(1,{function:'standby',requested_function:'detect',mode_request_pending:true}),true).status,'starting');
 assert.equal(t.snapshot(data(1,{function:'detect',requested_function:'standby',mode_request_pending:true}),true).status,'stopping');
});
test('detection lines that stop arriving make the view stalled with fps 0',()=>{
 const t=new M12Tracker(),s=t.snapshot(data(10_000_000,{last_result_us:7_000_000}),true);
 assert.equal(s.status,'stalled');assert.equal(s.fps,0);assert.equal(s.faces.length,0);
});
test('malformed boxes and missing library are filtered, not drawn',()=>{
 const t=new M12Tracker();
 const s=t.snapshot(data(5_000_000,{faces:[{id:1,x:1,y:1,w:0,h:10,prob:.9},{id:2,x:1,y:1,w:10,h:10,prob:2}],library:{valid:false}}),true);
 assert.equal(s.faces.length,0);assert.equal(s.library,null);
});
