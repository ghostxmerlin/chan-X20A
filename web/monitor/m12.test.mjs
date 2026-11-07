import test from 'node:test';
import assert from 'node:assert/strict';
import { M12Tracker } from './m12.mjs';
const data = (us, results, extra = {}) => ({ boot_id:'a', timestamp_us:us, m12:{valid:true,function:'face_detector',requested_us:1_000_000,results,last_result_us:us,num:1,faces:[{x:10,y:20,w:30,h:40,prob:.9}],...extra}});
test('recognition uses its own counter and identities; stale/switching/empty frames clear names',()=>{
 const t=new M12Tracker();
 const sample=(us,n,extra={})=>data(us,999,{function:'face_recognition',recognitions:n,last_recognition_us:us,frame_us:us,frame_recognition:true,faces:[{x:10,y:20,w:30,h:40,prob:.9,name:'papa',match_prob:.83,known:true}],...extra});
 t.accept(sample(2000000,3));t.accept(sample(3000000,5));
 const s=t.snapshot(sample(3000000,5),true);assert.equal(s.status,'recognizing');assert.equal(s.fps,2);assert.equal(s.faces[0].name,'papa');
 assert.deepEqual(t.snapshot(sample(6000000,5,{last_recognition_us:3000000,frame_us:3000000}),true).faces,[]);
 assert.deepEqual(t.snapshot(sample(4000000,5,{requested_us:4000000,frame_us:3000000,last_recognition_us:3000000}),true).faces,[]);
 assert.deepEqual(t.snapshot(sample(4000000,6,{num:0,faces:[]}),true).faces,[]);
 assert.deepEqual(t.snapshot(sample(4000000,6,{frame_recognition:false}),true).faces,[]);
 assert.deepEqual(t.snapshot(sample(4000000,6),false).faces,[]);
});
test('recognition startup acknowledgement without faces is not an identity or result',()=>{
 const t=new M12Tracker();
 const waiting=data(3000000,0,{function:'face_recognition',recognitions:0,last_recognition_us:0,last_recognition_report_us:2000000,frame_us:0,faces:[]});
 t.accept(waiting);const s=t.snapshot(waiting,true);
 assert.equal(s.status,'recognition_waiting');assert.equal(s.fps,null);assert.equal(s.count,null);assert.deepEqual(s.faces,[]);
 assert.equal(t.snapshot({...waiting,m12:{...waiting.m12,requested_us:2500000}},true).status,'starting');
});
test('M12 rate is device result delta, not poll count; duplicate samples do not inflate it',()=>{
 const t=new M12Tracker();t.accept(data(2_000_000,10));t.accept(data(3_000_000,17));t.accept(data(3_000_000,17));
 const s=t.snapshot(data(3_000_000,17),true);assert.equal(s.fps,7);assert.equal(s.status,'detecting');assert.equal(s.faces.length,1);
});
test('zero-face results count; standby, new request, stale data and reboot discard old detections',()=>{
 const t=new M12Tracker();t.accept(data(2_000_000,10));t.accept(data(3_000_000,17,{num:0,faces:[]}));
 assert.equal(t.snapshot(data(3_000_000,17,{num:0,faces:[]}),true).fps,7);
 const standby=data(4_000_000,17,{function:'standby',last_result_us:3_000_000});t.accept(standby);
 assert.deepEqual(t.snapshot(standby,true).faces,[]);assert.equal(t.snapshot(standby,true).fps,null);
 const restart=data(5_000_000,17,{requested_us:5_000_000,last_result_us:3_000_000});t.accept(restart);
 assert.equal(t.snapshot(restart,true).status,'starting');assert.deepEqual(t.snapshot(restart,true).faces,[]);
 assert.deepEqual(t.snapshot(restart,false).faces,[]);
 const reboot={...data(2_000_000,1),boot_id:'b'};t.accept(reboot);assert.equal(t.snapshot(reboot,true).fps,null);
});
test('stalled results expire while telemetry remains fresh; counter/time reset cannot produce negative FPS',()=>{
 const t=new M12Tracker();t.accept(data(2_000_000,10));t.accept(data(3_000_000,17));
 const stale=data(6_000_000,17,{last_result_us:3_000_000});t.accept(stale);
 assert.equal(t.snapshot(stale,true).status,'stalled');assert.equal(t.snapshot(stale,true).fps,0);assert.deepEqual(t.snapshot(stale,true).faces,[]);
 t.accept(data(7_000_000,1));assert.equal(t.snapshot(data(7_000_000,1),true).fps,null);
 t.accept(data(1_500_000,0));assert.equal(t.snapshot(data(1_500_000,0),true).fps,null);
});


test('standby UI distinguishes a pending request from a historical report',()=>{
 const t=new M12Tracker();
 const pending=data(4_000_000,17,{function:'standby',requested_us:4_000_000,running:'Face Detector',last_rx_us:3_000_000});
 t.accept(pending);assert.equal(t.snapshot(pending,true).status,'standby_pending');
 const old=data(4_000_000,17,{function:'standby',requested_us:4_000_000,running:'Audio FFT',last_rx_us:3_000_000});
 assert.equal(t.snapshot(old,true).status,'standby_pending');
 const reported=data(5_000_000,17,{function:'standby',requested_us:4_000_000,running:'Audio FFT',last_rx_us:4_500_000});
 assert.equal(t.snapshot(reported,true).status,'standby');
});
