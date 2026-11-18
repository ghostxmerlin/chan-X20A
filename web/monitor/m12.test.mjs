import test from 'node:test';
import assert from 'node:assert/strict';
import { M12Tracker, m12EventView, M12_EVENTS } from './m12.mjs';
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
// collector-0.5.02 (m12d 0.2.0): events in telemetry m12.events.recent, newest first, robot clock at_us.
const ev=(us,recent,total=recent.length?recent[0].seq:0,m={})=>data(us,{events:{total,unknown:0,recent},...m});
test('events: new ones by seq, oldest first, wall time from the robot clock, page keeps the newest 40',()=>{
 const t=new M12Tracker();
 const a=t.accept(ev(10_000_000,[{seq:2,at_us:9_500_000,f:11,ev:'nod',face:3,n:2},{seq:1,at_us:9_000_000,f:10,ev:'enter',face:3}]),100_000);
 assert.deepEqual(a.map(e=>[e.seq,e.label,e.who,e.at]),[[1,'有人来了','脸#3',99_000],[2,'点头','脸#3',99_500]]);
 assert.equal(a[1].detail,'2 下');assert.equal(a[1].group,'head');assert.equal(a[0].group,'presence');
 assert.deepEqual(t.accept(ev(10_100_000,[{seq:2,at_us:9_500_000,ev:'nod',face:3,n:2}]),100_100),[]);     // nothing new
 const s=t.snapshot(ev(10_100_000,[]),true);
 assert.deepEqual(s.events.map(e=>e.seq),[2,1]);assert.equal(s.eventsMissed,0);
 // more than 12 between two reads: the gap is counted
 t.accept(ev(11_000_000,[{seq:20,at_us:10_900_000,ev:'far',person:18}]),101_000);
 assert.equal(t.snapshot(ev(11_000_000,[]),true).eventsMissed,17);
 for(let i=21;i<80;i++)t.accept(ev(11_000_000+i,[{seq:i,at_us:11_000_000,ev:'look',face:1}]),102_000);
 assert.equal(t.snapshot(ev(12_000_000,[]),true).events.length,40);
 t.clear();assert.equal(t.snapshot(ev(1,[]),true).events.length,0);
});
test('event views: every field becomes readable Chinese; unknown types stay visible as such',()=>{
 assert.deepEqual(m12EventView({ev:'wake',area:[100,50,200,300],amt:0.12,cd:30,faces:[{box:[1,1,9,9],name:'me',sim:0.71},{box:[2,2,9,9]}],persons:[[1,1,5,5]]}),
  {label:'待机唤醒',group:'wake',who:'',detail:'变化 12% · 区域 100,50 200×300 · 看到脸 2（me 71%） · 人形 1 · 冷却 30 秒'});
 assert.equal(m12EventView({ev:'wake',pan:true,hot:true,amt:1}).detail,'变化 100% · 镜头被转动 · 过热，没有分析');
 assert.equal(m12EventView({ev:'wake',faces:[],faces_seen:5,persons:[]}).detail,'看到脸 5 · 人形 0');
 assert.deepEqual(m12EventView({ev:'light',on:false,lum:[120,20]}),{label:'关灯',group:'light',who:'',detail:'亮度 120 → 20'});
 assert.equal(m12EventView({ev:'uncover',dur:3.4,lum:[8,139]}).detail,'捂了 3.4 秒 · 亮度 8 → 139');
 assert.equal(m12EventView({ev:'cover',lum:[-1,30]}).detail,'亮度 30');
 assert.equal(m12EventView({ev:'tilt',face:3,side:'r'}).detail,'头顶向画面右');
 assert.equal(m12EventView({ev:'near',face:3,d:[1.2,0.7]}).detail,'1.2 → 0.7 米');
 assert.equal(m12EventView({ev:'leave',person:18,dur:12.5}).who,'人形#18');
 assert.equal(m12EventView({ev:'look_end',face:1,dur:1.8}).detail,'朝着 1.8 秒');
 assert.deepEqual(m12EventView({ev:'zzz'}),{label:'未知 zzz',group:'other',who:'',detail:''});
 assert.equal(m12EventView({}).label,'未知');
 for(const k of Object.keys(M12_EVENTS))assert.ok(['wake','light','presence','head'].includes(M12_EVENTS[k][1]));
});
test('serial bandwidth: bytes per second over the last 10 s, drops over the last minute, reset when M12 restarts',()=>{
 const t=new M12Tracker(),hb=(up,drop,fps=14.8)=>({heartbeat:{t:70,fps,tier:0,up,mem:1,frames:1,reopens:0,drop,mhz:1400}});
 t.accept(data(1_000_000,{rx_bytes:1000,...hb(100,3)}));
 assert.equal(t.snapshot(data(1_000_000,{rx_bytes:1000,...hb(100,3)}),true).serial.bytesPerSecond,null);   // one sample
 t.accept(data(3_000_000,{rx_bytes:21000,...hb(102,3)}));
 t.accept(data(5_000_000,{rx_bytes:41000,...hb(104,7)}));
 const s=t.snapshot(data(5_000_000,{rx_bytes:41000,...hb(104,7)}),true).serial;
 assert.equal(s.bytesPerSecond,10000);assert.equal(s.drop,7);assert.equal(s.dropMinute,4);assert.equal(s.fps,14.8);assert.equal(s.capacity,11520);
 t.accept(data(7_000_000,{rx_bytes:42000,...hb(2,0)}));                                          // M12 restarted
 assert.equal(t.snapshot(data(7_000_000,{rx_bytes:42000,...hb(2,0)}),true).serial.dropMinute,null);
 assert.equal(t.snapshot(data(7_000_000),false).serial,null);
});
test('faces keep the 0.2.0 pose fields as the robot sent them',()=>{
 const t=new M12Tracker(),s=t.snapshot(data(5_000_000,{faces:[{id:3,x:100,y:80,w:120,h:150,prob:.97,yaw:-12,pitch:4,roll:-7,look:true,dist:0.7}]}),true);
 assert.deepEqual([s.faces[0].yaw,s.faces[0].pitch,s.faces[0].roll,s.faces[0].look,s.faces[0].dist],[-12,4,-7,true,0.7]);
});
