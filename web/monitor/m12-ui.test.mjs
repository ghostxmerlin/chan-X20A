import test from 'node:test';
import assert from 'node:assert/strict';
function fakeDoc(){
 const elements=new Map();
 const make=()=>({textContent:'',value:'',disabled:false,dataset:{},children:[],className:'',
   addEventListener(e,fn){this['on'+e]=fn;},append(...c){this.children.push(...c);},replaceChildren(){this.children=[];}});
 return {getElementById(id){if(!elements.has(id))elements.set(id,make());return elements.get(id);},createElement:()=>make()};
}
globalThis.document=fakeDoc();
const { M12LibraryPanel, m12ErrorText } = await import('./public/m12-ui.js');
const state=(boot='a',m={})=>({boot,telemetry:{m12:{protocol:'m12d',online:true,...m}},m12:{library:[{name:'person-a',samples:5}],commands:[]}});
test('library: one automatic list read per boot, other requests only on click',async()=>{
 const doc=fakeDoc(),calls=[];const p=new M12LibraryPanel(doc,async b=>{calls.push(b);return {accepted:true,op:b.action.slice(4),id:calls.length};});
 p.render(state(),true);await new Promise(r=>setTimeout(r,0));p.render(state(),true);
 assert.deepEqual(calls,[{action:'m12_faces',boot:'a'}]);
 doc.getElementById('m12-name').value='  person-b ';await doc.getElementById('m12-learn').onclick();
 assert.deepEqual(calls.at(-1),{action:'m12_learn',boot:'a',name:'person-b'});
 doc.getElementById('m12-name').value='';await doc.getElementById('m12-learn').onclick();
 assert.equal(calls.length,2);assert.match(doc.getElementById('m12-library-note').textContent,/名字/);
 await doc.getElementById('m12-library-list').onclick({target:{dataset:{forget:'person-a'}}});
 assert.deepEqual(calls.at(-1),{action:'m12_forget',boot:'a',name:'person-a'});
 p.render(state('b'),true);await new Promise(r=>setTimeout(r,0));assert.deepEqual(calls.at(-1),{action:'m12_faces',boot:'b'});   // new boot: read again
});
test('library: offline disables everything and nothing is sent',async()=>{
 const doc=fakeDoc(),calls=[];const p=new M12LibraryPanel(doc,async b=>{calls.push(b);return {};});
 p.render(state('a',{online:false}),true);await doc.getElementById('m12-who').onclick();
 assert.equal(calls.length,0);assert.equal(doc.getElementById('m12-learn').disabled,true);
});
test('library: results and error codes become readable text',async()=>{
 const doc=fakeDoc();const p=new M12LibraryPanel(doc,async b=>({accepted:true,op:'learn',id:7}));
 p.render(state(),true);await new Promise(r=>setTimeout(r,0));
 doc.getElementById('m12-name').value='person-b';await doc.getElementById('m12-learn').onclick();
 const s=state();s.m12.commands=[{id:7,op:'learn',state:'error',err:'conflict',name:'person-a'}];p.render(s,true);
 assert.match(doc.getElementById('m12-library-note').textContent,/学习失败：和库里的「person-a」太像/);
 assert.match(m12ErrorText({err:'noface'}),/没有可用的脸/);assert.match(m12ErrorText({err:'full'}),/16/);assert.match(m12ErrorText({err:'zzz'}),/错误 zzz/);
});
test('0.2.0 pose, serial bandwidth and the event list read as plain Chinese',async()=>{
 const { poseText, serialText, renderM12Events } = await import('./public/m12-ui.js');
 assert.equal(poseText({yaw:-12,pitch:4,roll:0,look:true,dist:0.7}),'转头 −12° · 抬头 +4° · 歪头 0° · 脸朝着机器人 · 约 0.7 米');
 assert.equal(poseText({yaw:3,look:false}),'脸没朝机器人');                                   // partial: only what is there
 assert.equal(poseText({id:1}),'');
 assert.deepEqual(serialText({fps:14.8,drop:7,dropMinute:4,bytesPerSecond:10240,capacity:11520}),
  {text:'心跳帧率 14.8 · 丢行 7（近 1 分钟 +4） · 接收 10.0 KB/s（上限约 11.3）',warn:true});
 assert.equal(serialText({fps:15,drop:0,dropMinute:0,bytesPerSecond:2048,capacity:11520}).warn,false);
 assert.equal(serialText({fps:15,drop:0,dropMinute:0,bytesPerSecond:10000,capacity:11520}).warn,true);  // ≥ 85% of the link
 assert.equal(serialText(null).text,'尚无心跳');
 const doc=fakeDoc(),list=doc.getElementById('m12-events');
 renderM12Events(list,{eventsTotal:2,events:[{at:new Date(2026,10,17,9,5,7).getTime(),label:'点头',group:'head',who:'脸#3',detail:'2 下'}]},doc);
 assert.equal(list.children.length,1);
 assert.deepEqual(list.children[0].children.map(c=>c.textContent),['09:05:07','点头','脸#3','2 下']);
 assert.equal(list.children[0].children[1].className,'ev-head');
 renderM12Events(list,{eventsTotal:null,events:[]},doc);assert.match(list.children[0].textContent,/0\.5\.02/);
 renderM12Events(list,{eventsTotal:0,events:[]},doc);assert.equal(list.children[0].textContent,'还没有事件');
 renderM12Events(list,null,doc);assert.equal(list.children[0].textContent,'数据未连接');
});
test('eye events: bubbles go over their box for 3 s, the rest become a banner',async()=>{
 const { eventOverlays, EVENT_SHOW_MS } = await import('./public/m12-ui.js');
 const now=100000, ev=(o)=>({label:'x',group:'head',who:'',...o});
 const events=[ev({at:now-500,label:'点头',face:3,n:2}),ev({at:now-800,label:'有人来了',group:'presence',person:9,who:'人形#9'}),
  ev({at:now-1000,label:'捂住镜头',group:'light'}),ev({at:now-1200,label:'脸朝着我',face:7,who:'脸#7'}),ev({at:now-EVENT_SHOW_MS-1,label:'旧',face:3})];
 const o=eventOverlays(events,now,[{id:3}],[{id:9}]);
 assert.deepEqual(o.byFace.get(3).map(i=>i.text),['点头 ×2']);
 assert.deepEqual(o.byPerson.get(9).map(i=>i.text),['有人来了']);
 assert.deepEqual(o.banner.map(i=>i.text),['捂住镜头','脸朝着我 脸#7']);     // face 7 not in view: banner; the old one is gone
 assert.ok(o.byFace.get(3)[0].fade>0.8);
});
test('eye events: the newest four under the eye, with how long ago',async()=>{
 const { renderEyeEvents } = await import('./public/m12-ui.js');
 const doc=fakeDoc(),list=doc.getElementById('m12-eye-events'),now=50000;
 renderEyeEvents(list,{eventsTotal:null,events:[]},now,doc);assert.match(list.children[0].textContent,/0\.5\.02/);
 const events=Array.from({length:6},(_,i)=>({at:now-i*10000,label:`e${i}`,group:'head',who:`脸#${i}`,n:i===0?2:undefined}));
 renderEyeEvents(list,{eventsTotal:6,events},now,doc);
 assert.equal(list.children.length,4);
 assert.deepEqual(list.children[0].children.map(c=>c.textContent),['e0','脸#0 2 下','0 秒前']);
 assert.equal(list.children[3].children[2].textContent,'30 秒前');
});
