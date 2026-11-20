import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
// mode-ui.js imports '/switches-ui.js' (the page path); point it at the file so the module loads here.
const src=readFileSync(new URL('./public/mode-ui.js',import.meta.url),'utf8')
 .replace("from '/switches-ui.js'",`from '${new URL('./public/switches-ui.js',import.meta.url).href}'`);
const ui=await import('data:text/javascript;base64,'+Buffer.from(src).toString('base64'));

const snap=(over={})=>({seq:1,mode:'crowd',dir:null,host:null,return_to:null,billing:true,since:1,by:'page',
 look:{eye:'v14',pool:'lively',label:'DOCK',color:'30D060'},touch:{tap:'server',double:'server'},...over});
const state=(over={},robot={connected:true,applied_seq:1})=>({snapshot:snap(over),robot,log:[]});

// ---- Docking only (owner 2026-11-20): hello reports its mode; the bar shows the fixed Docking badge ----
const tele=(mode,over={})=>({online:true,fresh:{telemetry:true},boot:'b1',telemetry:{boot_id:'b1',seq:3,mode:{mode,dir:null,host:null,return_to:null,seq:7,by:'touch',since:5,...over}}});
const robotOf=(m,over)=>ui.robotMode(tele(m,over));
const hv=(m,over={},billing=true)=>ui.modeView({snapshot:snap({billing}),robot:{},log:[]},robotOf(m,over));

test('hello telemetry: ok / unsupported (no mode) / offline',()=>{
 assert.equal(robotOf('crowd').status,'ok');assert.equal(robotOf('crowd').boot,'b1');
 assert.equal(ui.robotMode({...tele('crowd'),telemetry:{boot_id:'b1',seq:1,mode:'crowd'}}).mode.mode,'crowd');   // the id alone
 assert.equal(robotOf('talk').status,'ok');                                   // an old firmware's mode is still a mode
 for(const t of [{boot_id:'b1',seq:1},{boot_id:'b1',seq:1,mode:{mode:'fly'}},{boot_id:'b1',seq:1,mode:null}])
  assert.equal(ui.robotMode({...tele('crowd'),telemetry:t}).status,'unsupported');
 for(const st of [null,{},{...tele('crowd'),online:false},{...tele('crowd'),fresh:{telemetry:false}},{...tele('crowd'),telemetry:null}])
  assert.equal(ui.robotMode(st).status,'offline');
});
test('Docking only: the bar shows the Docking badge and has no mode buttons',()=>{
 const v=hv('crowd');
 assert.deepEqual([v.known,v.source,v.mode,v.name,v.label,v.color,v.seq,v.by,v.billing,v.oldMode],
  [true,'hello','crowd','Docking','DOCK','30D060',7,'touch',true,null]);
 const h=ui.modeBarHtml(v,[]);
 assert.match(h,/class="mode-chip" data-color="30D060">DOCK</);assert.match(h,/>Docking</);
 assert.doesNotMatch(h,/data-mode=|<button/);
 assert.equal(ui.MODE_BUTTONS,undefined);assert.equal(ui.selectRequest,undefined);
});
test('old firmware still reporting a removed mode: shown as Docking with a note naming it',()=>{
 for(const [m,name] of [['talk','脱口秀'],['idle','IDLE'],['subtitle','字幕']]){
  const v=hv(m);
  assert.deepEqual([v.known,v.mode,v.label,v.oldMode],[true,'crowd','DOCK',m],m);
  assert.match(ui.modeBarHtml(v,[]),new RegExp(`机器人还是旧固件：报的是「${name}」`),m);
 }
});
test('mind\'s record when hello is offline or has no mode',()=>{
 const off=ui.modeView(state(),ui.robotMode(null));
 assert.deepEqual([off.known,off.source,off.mode],[true,'mind','crowd']);
 assert.match(ui.modeBarHtml(off,[]),/hello 没连上：这是 mind 最后记下的/);
 const none=ui.modeView(state(),ui.robotMode({...tele('crowd'),telemetry:{boot_id:'b1',seq:1}}));
 assert.equal(none.source,'mind');assert.match(ui.modeBarHtml(none,[]),new RegExp(ui.NO_MODES));
});
test('unknown (neither hello nor mind) says so',()=>{
 for(const st of [null,undefined,{},{snapshot:null},{snapshot:{mode:42}},{snapshot:{mode:'fly'}}]){
  const v=ui.modeView(st,null);assert.equal(v.known,false);
  assert.match(ui.modeBarHtml(v,[]),/模式未知（hello 和情景服务都没连上）/);
  assert.equal((ui.quickHtml(v).match(/disabled/g)||[]).length,11);       // billing + all ten keys
 }
 assert.match(ui.modeBarHtml(ui.modeView(null,null,'absent'),[]),/mind 还没有模式功能（等上线）/);
});
test('LOOK matches server/mind/mode_table.py',()=>{
 const py=readFileSync(new URL('../../server/mind/mode_table.py',import.meta.url),'utf8');
 const table=py.slice(py.indexOf('MODES = {'),py.indexOf('_IDS = ('));
 const looks=[...table.matchAll(/'look': \{[^}]*'label': '([^']+)', 'color': '([0-9A-F]{6})'\}/g)].map(m=>[m[1],m[2]]);
 assert.deepEqual(ui.LOOK,{crowd:looks[0]});
 assert.match(py,/_IDS = \('crowd',\)/);
});
test('quick panel: only 0 and 9 (mind\'s) are live; 1-8 are empty',()=>{
 const q=ui.quickHtml(hv('crowd'));
 assert.equal((q.match(/data-preset=/g)||[]).length,10);
 for(const n of [0,9])assert.match(q,new RegExp(`data-preset="${n}"(?![^>]*disabled)`));
 for(const n of [1,2,3,4,5,6,7,8])assert.match(q,new RegExp(`data-preset="${n}" title="空" disabled`));
 assert.doesNotMatch(q,/data-hello-preset|提前写|连续说/);
 assert.match(q,/data-preset="9" title="花费全开"/);
});
test('quick panel: the billing switch',()=>{
 const on=ui.quickHtml(hv('crowd'));
 assert.match(on,/data-billing[^>]*aria-pressed="true"[^>]*title="点一下 = 预设 0 花费全关[^"]*"[^>]*>计费 全开</);assert.match(on,/上次整体：全开/);
 const off=ui.quickHtml(hv('crowd',{},false));
 assert.match(off,/data-billing[^>]*aria-pressed="false"[^>]*title="点一下 = 预设 9 花费全开/);assert.match(off,/上次整体：全关（单个开关照样能开）/);
 assert.match(ui.quickHtml(ui.modeView(null,robotOf('crowd'))),/计费 未知/);   // hello known, mind down
});
test('the colour only travels as a checked data attribute (CSP: no style attributes)',()=>{
 const good=ui.modeBarHtml(hv('crowd'),[]);
 assert.match(good,/data-color="30D060"/);assert.doesNotMatch(good,/style=/);
});
test('log rows: newest ten, readable, everything escaped',()=>{
 const rows=Array.from({length:12},(_,i)=>({at:i,source:'touch',input:'tap',from:'crowd',to:'crowd',code:'ok'}));
 rows.push({at:99,source:'<b>',input:'billing',value:false,from:'crowd',to:'crowd',code:'"><x'});
 const h=ui.modeBarHtml(hv('crowd'),rows);
 assert.equal((h.match(/<li/g)||[]).length,10);
 assert.match(h,/触摸 · Docking（tap）/);assert.doesNotMatch(h,/<b>|"><x/);
 assert.match(ui.logLine({at:1,source:'page',input:'billing',value:false,from:'crowd',to:'crowd',code:'ok'}),/计费 全关/);
 assert.match(ui.logLine({at:1,source:'asr',input:'digit',value:9,from:'crowd',to:'crowd',code:'asr_off'}),/预设 9 · 语音口令已关/);
 assert.match(ui.logLine({at:1,source:'page',input:'select',from:'crowd',to:'crowd',code:'removed_mode'}),/只有 Docking 模式/);
});
test('result and effect text',()=>{
 assert.equal(ui.resultText({code:'ok'}),'');
 assert.match(ui.resultText({code:'removed_mode'}),/只有 Docking/);
 assert.equal(ui.resultText({code:'<x>'}),'<x>');          // plain text: the bar uses textContent
 assert.equal(ui.effectsText(['stop_speech']),'');
 assert.match(ui.errorText(Object.assign(new Error('x'),{name:'TimeoutError'})),/结果未确认/);
});
test('robot half of a preset uses the switch bar tiers; junk is dropped',()=>{
 assert.deepEqual(ui.presetRobotSteps({hearing:'tingwu',vision:'recognize'}),[['hearing','tingwu'],['vision','recognize']]);
 assert.deepEqual(ui.presetRobotSteps(undefined),[]);
 assert.deepEqual(ui.presetRobotSteps({hearing:'off',vision:'evil',x:1}),[]);
});

const robotState={boot:'b1',online:true};
test('preset: server half first, then the switch bar requests for the tiers mind names',async()=>{
 const sent=[];
 const post=async(path,body)=>{sent.push([path,body]);
  if(path==='/api/mind/preset')return {snapshot:snap({billing:true}),result:{code:'ok',toast:'PRESET 9'},effects:[],robot:{hearing:'tingwu',vision:'recognize'}};
  return {};};
 const out=await ui.runPreset(9,{post,robotState});
 assert.deepEqual(sent.map(s=>s[0]),['/api/mind/preset','/api/mind/switches','/api/control','/api/mind/switches','/api/control','/api/control']);
 assert.deepEqual(sent[0][1],{n:9,source:'page'});
 assert.deepEqual(sent[1][1],{name:'tingwu',on:true});
 assert.deepEqual(sent[2][1],{action:'hearing_listen',enabled:true,boot:'b1'});
 assert.deepEqual(sent[3][1],{name:'eye',on:true});
 assert.deepEqual(sent[5][1],{action:'m12',enabled:true,boot:'b1'});
 assert.equal(out.failed.length,0);
 assert.match(out.message,/预设 9 花费全开/);assert.match(out.message,/机器人那一半已发/);
});
test('preset with no robot: the server half counts, each failed robot step is named, nothing retried',async()=>{
 const sent=[];
 const post=async(path,body)=>{sent.push(path);
  if(path==='/api/mind/preset')return {snapshot:snap({billing:false}),result:{code:'ok',toast:'PRESET 9'},effects:[],robot:{hearing:'tingwu',vision:'recognize'}};
  if(path==='/api/control')throw new Error('请恢复监控并等待新的机器人状态');
  return {};};
 const out=await ui.runPreset(9,{post,robotState:null});
 // hearing on stops at its first failure; vision tries camera then stops too (same as the switch bar)
 assert.deepEqual(sent,['/api/mind/preset','/api/mind/switches','/api/control','/api/mind/switches','/api/control']);
 assert.match(out.message,/服务器那一半已做/);
 assert.match(out.message,/听觉（本体听觉）没成：请恢复监控/);
 assert.match(out.message,/视觉（摄像头）没成/);
 assert.doesNotMatch(out.message,/机器人那一半已发/);
});
test('preset reply with failed steps from billing off names them',async()=>{
 const post=async path=>path==='/api/mind/preset'?{snapshot:snap({billing:false}),result:{code:'ok',toast:'BILLING OFF, 1 FAILED'},effects:[],robot:{},failed:['notes','<x>']}:{};
 const out=await ui.runPreset(0,{post,robotState});
 assert.match(out.message,/这几步没成：纪要、<x>/);
});
test('preset refused by mind (409) shows mind\'s words and skips the robot half',async()=>{
 const sent=[];
 const post=async path=>{sent.push(path);throw new Error('{"error":"情景服务连接中"}');};
 const out=await ui.runPreset(9,{post,robotState});
 assert.deepEqual(sent,['/api/mind/preset']);assert.match(out.message,/预设 9.*情景服务连接中/);
});
test('an undefined preset answers in words',async()=>{
 const post=async()=>({snapshot:snap(),result:{code:'undefined_preset',toast:'PRESET UNDEFINED'},effects:[],robot:{}});
 assert.match((await ui.runPreset(1,{post,robotState})).message,/还没定义/);
});
test('the billing switch is preset 0 when it shows on, preset 9 otherwise',()=>{
 assert.equal(ui.billingPreset(true),0);assert.equal(ui.billingPreset(false),9);
});
test('page files: modebar under the switch bar on both pages, no inline script or style',()=>{
 for(const f of ['index.html','mind.html']){
  const h=readFileSync(new URL('./public/'+f,import.meta.url),'utf8');
  assert.match(h,/<nav id="switches"[^>]*><\/nav>\s*<nav id="modebar" class="modebar" aria-label="模式"><\/nav>/);
  assert.match(h,/href="\/mode\.css"/);assert.match(h,/<script type="module" src="\/mode-ui\.js"><\/script>/);
  assert.doesNotMatch(h,/<script>|style="|onclick=/);
 }
 const js=readFileSync(new URL('./public/mode-ui.js',import.meta.url),'utf8');
 assert.doesNotMatch(js,/\balert\(|\bconfirm\(|\bprompt\(|style="/);
 assert.match(js,/querySelector\('\.mode-msg-text'\)\.textContent=m\.msg/);assert.doesNotMatch(js,/msg[\w.()'-]*\.innerHTML/);
});

// ---- the model: polling, ordering, actions ----
const res=(status,body)=>({ok:status>=200&&status<300,status,json:async()=>body,text:async()=>typeof body==='string'?body:JSON.stringify(body)});
const modeReply=(over={})=>({snapshot:snap(over),robot:{connected:true,boot:'b1',conn:1,seq:7,at:1},log:[]});
// fetch double: /api/mind/mode answers from a queue of (status, body), errors or deferred promises; /api/state from robotQ
function fakeFetch(modeQueue,{robotQ=null,robot=tele('crowd'),talk=res(200,{prefetch:{on:false},auto:{on:false}}),cfg=res(200,{speech:{round:{family:false}}})}={}){
 const calls=[];
 const f=async(path,opts={})=>{calls.push([path,opts.method||'GET',opts.body]);
  if(path==='/api/mind/mode'){const next=modeQueue.length>1?modeQueue.shift():modeQueue[0];if(next instanceof Error)throw next;return typeof next==='function'?next():next;}
  if(path.startsWith('/api/mind/say/stats'))return talk;
  if(path==='/api/mind/speech/settings')return cfg;
  if(path==='/api/state'){const r=robotQ?(robotQ.length>1?robotQ.shift():robotQ[0]):robot;return res(200,r);}
  return res(200,{});};
 f.calls=calls;return f;
}
const model=(fetchFn,extra={})=>{const seen=[];const m=new ui.ModeModel({fetchFn,emit:v=>seen.push(v),...extra});m.seen=seen;return m;};

test('poll reads hello and mind together; hello\'s report wins, mind gives billing and the log',async()=>{
 const m=model(fakeFetch([res(200,{...modeReply({billing:false}),log:[{at:1,source:'touch',input:'tap',from:'crowd',to:'crowd',code:'ok'}]})],{robot:tele('crowd')}));
 await m.poll();
 assert.deepEqual([m.view.mode,m.view.source,m.view.billing,m.state.log.length],['crowd','hello',false,1]);
 assert.equal(m.delay(),2000);
});
test('mind without /mode (404): asks mind once a minute, hello every poll',async()=>{
 let t=0;const f=fakeFetch([res(404,{error:'not found'})],{robot:tele('crowd')});
 const m=model(f,{now:()=>t});
 await m.poll();assert.equal(m.why,'absent');
 await m.poll();t=30000;await m.poll();
 assert.equal(f.calls.filter(c=>c[0]==='/api/mind/mode').length,1);
 assert.equal(f.calls.filter(c=>c[0]==='/api/state').length,3);
 assert.equal(m.view.source,'hello');assert.equal(m.view.billing,null);
 t=61000;await m.poll();assert.equal(f.calls.filter(c=>c[0]==='/api/mind/mode').length,2);
});
test('mind not reached and hello offline: unknown, normal pace',async()=>{
 for(const r of [res(503,'情景服务连接中'),res(502,'x'),new Error('net')]){
  const m=model(fakeFetch([r],{robot:{online:false}}));await m.poll();
  assert.equal(m.why,'down');assert.equal(m.view.known,false);assert.equal(m.delay(),2000);
 }
});
test('an older poll landing after a newer one is dropped (one modechange)',async()=>{
 let releaseOld;const old=new Promise(r=>releaseOld=r);
 const m=model(fakeFetch([()=>old,res(200,modeReply())],{robotQ:[tele('crowd',{seq:6}),tele('crowd',{seq:7})]}));
 const a=m.poll(),b=m.poll();
 await b;assert.equal(m.view.seq,7);
 releaseOld(res(200,modeReply()));await a;
 assert.equal(m.view.seq,7);assert.equal(m.seen.length,1);
});
test('modechange fires once per change, not on every poll',async()=>{
 const m=model(fakeFetch([res(200,modeReply())],{robotQ:[tele('crowd'),tele('crowd'),tele('crowd',{seq:8})]}));
 await m.poll();await m.poll();await m.poll();
 assert.equal(m.seen.length,2);
});
test('one action at a time; the buttons are locked while it runs',async()=>{
 let release;const post=()=>new Promise(r=>release=r);const painted=[];
 const m=model(fakeFetch([res(200,modeReply())]),{post,paint:()=>painted.push(m.pending)});
 await m.poll();
 const a=m.preset(9);await Promise.resolve();await Promise.resolve();assert.equal(m.pending,true);assert.equal(m.msg,'发送中…');
 await m.preset(0);                                        // ignored while pending
 release({snapshot:snap(),result:{code:'ok'},effects:[],robot:{}});await a;
 assert.equal(m.pending,false);assert.ok(painted.includes(true));
});
test('preset 0 / 9 and the billing switch (= preset 0 / 9) go to mind with the page post',async()=>{
 const sent=[];
 const post=async(path,body)=>{sent.push([path,body]);
  if(path==='/api/mind/preset')return {snapshot:snap({billing:false}),result:{code:'ok'},effects:[],robot:{}};
  return {billing:{on:true},result:{code:'ok'}};};
 const m=model(fakeFetch([res(200,modeReply())]),{post});
 await m.poll();
 await m.preset(0);assert.deepEqual(sent[0],['/api/mind/preset',{n:0,source:'page'}]);assert.match(m.msg,/预设 0 花费全关/);
 await m.billing();assert.deepEqual(sent[1],['/api/mind/preset',{n:0,source:'page'}]);   // billing showed on: one click = 0
 m.view={...m.view,billing:false};await m.billing();
 assert.deepEqual(sent[2],['/api/mind/preset',{n:9,source:'page'}]);assert.match(m.msg,/预设 9 花费全开/);
});
test('the message line can be closed by hand (not while an action runs)',async()=>{
 const m=model(fakeFetch([res(200,modeReply())]),{post:async()=>({snapshot:snap(),result:{code:'ok'},effects:[],robot:{}})});
 await m.poll();await m.preset(9);assert.notEqual(m.msg,'');
 m.dismiss();assert.equal(m.msg,'');
});
test('hello\'s state can come from the bar\'s own event stream instead of /api/state',async()=>{
 const f=fakeFetch([res(200,modeReply())]);
 const m=model(f,{robotSource:()=>tele('crowd')});
 await m.poll();
 assert.equal(m.view.source,'hello');assert.equal(f.calls.filter(c=>c[0]==='/api/state').length,0);
 const js=readFileSync(new URL('./public/mode-ui.js',import.meta.url),'utf8');
 assert.match(js,/new EventSource\('\/api\/events\?camera=0&servo=0'\)/);
});
