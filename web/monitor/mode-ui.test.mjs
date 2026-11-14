import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
// mode-ui.js imports '/switches-ui.js' (the page path); point it at the file so the module loads here.
const src=readFileSync(new URL('./public/mode-ui.js',import.meta.url),'utf8')
 .replace("from '/switches-ui.js'",`from '${new URL('./public/switches-ui.js',import.meta.url).href}'`);
const ui=await import('data:text/javascript;base64,'+Buffer.from(src).toString('base64'));

const snap=(over={})=>({seq:1,mode:'talk',dir:null,host:null,return_to:null,billing:true,since:1,by:'page',
 look:{eye:'laughing_man',pool:null,label:'TALK',color:'E040A0'},touch:{tap:'server',double:'server'},...over});
const state=(over={},robot={connected:true,applied_seq:1})=>({snapshot:snap(over),robot,log:[]});

// ---- step 1b: hello owns the mode ----
const tele=(mode,over={})=>({online:true,fresh:{telemetry:true},boot:'b1',telemetry:{boot_id:'b1',seq:3,mode:{mode,dir:null,host:null,return_to:null,seq:7,by:'asr',since:5,...over}}});
const robotOf=(m,over)=>ui.robotMode(tele(m,over));
const hv=(m,over={},billing=true)=>ui.modeView({snapshot:snap({billing}),robot:{},log:[]},robotOf(m,over));

test('hello telemetry: ok / unsupported (0.4.91, no mode) / offline',()=>{
 assert.equal(robotOf('crowd').status,'ok');assert.equal(robotOf('crowd').boot,'b1');
 assert.equal(ui.robotMode({...tele('talk'),telemetry:{boot_id:'b1',seq:1,mode:'idle'}}).mode.mode,'idle');   // the id alone
 for(const t of [{boot_id:'b1',seq:1},{boot_id:'b1',seq:1,mode:{mode:'fly'}},{boot_id:'b1',seq:1,mode:null}])
  assert.equal(ui.robotMode({...tele('talk'),telemetry:t}).status,'unsupported');
 for(const st of [null,{},{...tele('talk'),online:false},{...tele('talk'),fresh:{telemetry:false}},{...tele('talk'),telemetry:null}])
  assert.equal(ui.robotMode(st).status,'offline');
});
test('the bar shows hello\'s own mode, with the mind billing, and can switch',()=>{
 const v=hv('subtitle',{dir:'en_zh'});
 assert.deepEqual([v.known,v.source,v.canSwitch,v.mode,v.dir,v.label,v.color,v.seq,v.by,v.billing],
  [true,'hello',true,'subtitle','en_zh','SUB EN>ZH','7C6CF0',7,'asr',true]);
 const h=ui.modeBarHtml(v,[]);
 assert.match(h,/hello 上的模式/);assert.doesNotMatch(h,/disabled/);
 assert.match(h,/data-mode="subtitle" data-dir="en_zh"[^>]*aria-pressed="true"/);
 assert.equal((h.match(/aria-pressed="true"/g)||[]).length,1);
 // mind's view of an older mode never wins over hello's own
 assert.equal(ui.modeView({snapshot:snap({mode:'idle'})},robotOf('crowd')).mode,'crowd');
});
test('no mode in hello\'s telemetry (0.4.91): shows mind\'s record, says 等 0.4.92, switching off',()=>{
 const r=ui.robotMode({...tele('talk'),telemetry:{boot_id:'b1',seq:1}});
 const v=ui.modeView(state({mode:'crowd',look:{label:'MEET',color:'30D060'}}),r);
 assert.deepEqual([v.known,v.source,v.canSwitch,v.mode],[true,'mind',false,'crowd']);
 const h=ui.modeBarHtml(v,[]);
 assert.match(h,/hello 固件还不支持模式（等 0.4.92）/);
 assert.equal((h.match(/data-mode="[^"]*"[^>]*disabled/g)||[]).length,9);
 assert.equal(ui.lockedWhy(v),ui.NO_MODES);
 const q=ui.quickHtml(v);
 for(const n of [4,5,6,7,8])assert.match(q,new RegExp(`data-hello-preset="${n}"[^>]*disabled`));
 for(const n of [0,9])assert.doesNotMatch(q,new RegExp(`data-preset="${n}"[^>]*disabled`));     // mind's keys still work
 const none=ui.modeView(null,r);                                                               // mind down too
 assert.equal(none.known,false);assert.match(ui.modeBarHtml(none,[]),/等 0.4.92/);
});
test('hello offline: mind\'s last record, switching off, says why',()=>{
 const v=ui.modeView(state({mode:'idle',look:{label:'IDLE',color:'505050'}}),ui.robotMode(null));
 assert.deepEqual([v.source,v.canSwitch,v.mode],['mind',false,'idle']);
 assert.match(ui.modeBarHtml(v,[]),/hello 没连上：这是 mind 最后记下的/);
 assert.match(ui.modeBarHtml(v,[]),/title="hello 没连上：要等它连上才能切" disabled/);
});
test('unknown (neither hello nor mind) says so, every button off',()=>{
 for(const st of [null,undefined,{},{snapshot:null},{snapshot:{mode:42}},{snapshot:{mode:'fly'}}]){
  const v=ui.modeView(st,null);assert.equal(v.known,false);
  const h=ui.modeBarHtml(v,[]);
  assert.match(h,/模式未知（hello 和情景服务都没连上）/);
  assert.equal((h.match(/data-mode="[^"]*"[^>]*disabled/g)||[]).length,9);
  assert.equal((ui.quickHtml(v).match(/disabled/g)||[]).length,11);       // billing + all ten keys
 }
 assert.match(ui.modeBarHtml(ui.modeView(null,null,'absent'),[]),/mind 还没有模式功能（等上线）/);
});
test('LOOK matches server/mind/mode_table.py (label and colour per mode)',()=>{
 const py=readFileSync(new URL('../../server/mind/mode_table.py',import.meta.url),'utf8');
 const table=py.slice(py.indexOf('MODES = {'),py.indexOf('_IDS = ('));
 const looks=[...table.matchAll(/'look': \{[^}]*'label': '([^']+)', 'color': '([0-9A-F]{6})'\}/g)].map(m=>[m[1],m[2]]);
 const ids=[...py.matchAll(/_IDS = \(([^)]*)\)/g)][0][1].match(/\w+/g);
 assert.deepEqual(Object.fromEntries(ids.map((id,i)=>[id,looks[i]])),ui.LOOK);
 assert.deepEqual(Object.keys(ui.LOOK),ids);
 assert.match(py,/'labels': \{'zh_en': 'SUB ZH>EN', 'en_zh': 'SUB EN>ZH'\}/);
});
test('a click asks hello: /api/control action mode, select, with hello\'s boot',()=>{
 assert.deepEqual(ui.selectRequest('crowd',undefined,'b1'),{action:'mode',boot:'b1',input:'select',value:{mode:'crowd'}});
 assert.deepEqual(ui.selectRequest('subtitle','en_zh','b1'),{action:'mode',boot:'b1',input:'select',value:{mode:'subtitle',dir:'en_zh'}});
});
test('quick panel: 0 / 9 go to mind, 4-8 are hello selects, 1-3 are empty',()=>{
 const q=ui.quickHtml(hv('talk'));
 assert.equal((q.match(/data-preset=|data-hello-preset=/g)||[]).length,10);
 for(const n of [0,9])assert.match(q,new RegExp(`data-preset="${n}"(?![^>]*disabled)`));
 for(const n of [1,2,3])assert.match(q,new RegExp(`data-preset="${n}"[^>]*disabled`));
 for(const n of [4,5,6,7,8])assert.match(q,new RegExp(`data-hello-preset="${n}"(?![^>]*disabled)`));
 assert.match(q,/data-hello-preset="7" title="字幕 中→英（hello 切）"/);
 assert.match(q,/data-preset="9" title="花费全开"/);
 assert.deepEqual(ui.HELLO_PRESETS,{4:['idle'],5:['crowd'],6:['companion'],7:['subtitle','zh_en'],8:['subtitle','en_zh']});
});
test('quick panel: billing switch and the talk-show switches',()=>{
 const on=ui.quickHtml(hv('talk'));
 assert.match(on,/data-billing[^>]*aria-pressed="true"/);assert.doesNotMatch(on,/现在不花钱/);
 const off=ui.quickHtml(hv('talk',{},false));
 assert.match(off,/data-billing[^>]*aria-pressed="false"/);assert.match(off,/现在不花钱/);
 const h=ui.quickHtml(hv('talk'),{prefetch:true,auto:false,family:false});
 assert.match(h,/提前写 开/);assert.match(h,/连续说 关/);assert.match(h,/家人声音 关/);
 assert.match(h,/<details class="mode-presets-box"><summary>预设 0–9<\/summary>/);
 assert.doesNotMatch(ui.quickHtml(hv('talk')),/mode-talk/);
 assert.match(ui.quickHtml(ui.modeView(null,robotOf('talk'))),/计费总闸 未知/);   // hello known, mind down
});
test('event-driven and settings',()=>{
 assert.equal(hv('event').event,'waiting');assert.match(ui.modeBarHtml(hv('event'),[]),/class="mode-auto"[^>]*>AUTO</);
 assert.equal(hv('crowd',{host:'event'}).event,'hosting');
 const v=hv('settings',{return_to:'subtitle',dir:'zh_en'});
 assert.equal(v.returnTo,'subtitle');assert.match(ui.modeBarHtml(v,[]),/关了回到 字幕 中→英/);
});
test('the colour only travels as a checked data attribute (CSP: no style attributes)',()=>{
 const good=ui.modeBarHtml(hv('talk'),[]);
 assert.match(good,/data-color="E040A0"/);assert.doesNotMatch(good,/style=/);
});
test('log rows: newest ten, readable, everything escaped',()=>{
 const rows=Array.from({length:12},(_,i)=>({at:i,source:'touch',input:'select',from:'talk',to:'crowd',code:'ok'}));
 rows.push({at:99,source:'<b>',input:'billing',value:false,from:'talk',to:'talk',code:'"><x'});
 const h=ui.modeBarHtml(hv('talk'),rows);
 assert.equal((h.match(/<li/g)||[]).length,10);
 assert.match(h,/触摸 · 脱口秀 → 围观/);assert.doesNotMatch(h,/<b>|"><x/);
 assert.match(ui.logLine({at:1,source:'page',input:'billing',value:false,from:'talk',to:'talk',code:'ok'}),/计费总闸 关/);
 assert.match(ui.logLine({at:1,source:'asr',input:'digit',value:9,from:'talk',to:'talk',code:'ok'}),/预设 9/);
 assert.match(ui.logLine({at:1,source:'timeout',input:'timeout',from:'settings',to:'talk',code:'ok'}),/超时/);
 assert.match(ui.logLine({at:1,source:'asr',input:'forward',from:'talk',to:'talk',code:'too_soon'}),/太快了/);
});
test('result and effect text',()=>{
 assert.equal(ui.resultText({code:'ok'}),'');
 assert.match(ui.resultText({code:'on_hello'}),/hello 上切/);
 assert.equal(ui.resultText({code:'<x>'}),'<x>');          // plain text: the bar uses textContent
 assert.match(ui.effectsText(['stop_speech','talk_enter'],true),/提前写、连续说打开/);
 assert.match(ui.effectsText(['stop_speech','talk_enter'],false),/计费关着.*没开/);
 assert.equal(ui.errorText(new Error('{"error":"计费关着","code":"billing_off"}')),'计费关着');
 assert.equal(ui.errorText(new Error('hello 固件还不支持模式（等 0.4.92）')),'hello 固件还不支持模式（等 0.4.92）');
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
 assert.match(out.message,/没关成：纪要、<x>/);
});
test('preset refused by mind (409) shows mind\'s words and skips the robot half',async()=>{
 const sent=[];
 const post=async path=>{sent.push(path);throw new Error('{"error":"计费关着","code":"billing_off"}');};
 const out=await ui.runPreset(9,{post,robotState});
 assert.deepEqual(sent,['/api/mind/preset']);assert.match(out.message,/预设 9.*计费关着/);
});
test('an undefined preset answers in words',async()=>{
 const post=async()=>({snapshot:snap(),result:{code:'undefined_preset',toast:'PRESET UNDEFINED'},effects:[],robot:{}});
 assert.match((await ui.runPreset(1,{post,robotState})).message,/还没定义/);
});
test('billing request and message from the /switches reply',()=>{
 assert.deepEqual(ui.billingRequest(true),{name:'billing',on:false});
 assert.deepEqual(ui.billingRequest(false),{name:'billing',on:true});
 assert.match(ui.billingMessage({billing:{on:false},result:{code:'ok'},failed:['tingwu']}),/计费总闸已关.*没关成：听悟/);
 assert.match(ui.billingMessage({billing:{on:true},result:{code:'ok'}}),/计费总闸已开/);
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
function fakeFetch(modeQueue,{robotQ=null,robot=tele('talk'),talk=res(200,{prefetch:{on:false},auto:{on:false}}),cfg=res(200,{speech:{round:{family:false}}})}={}){
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

test('poll reads hello and mind together; hello\'s mode wins, mind gives billing and the log',async()=>{
 const m=model(fakeFetch([res(200,{...modeReply({mode:'idle',billing:false}),log:[{at:1,source:'touch',input:'select',from:'talk',to:'crowd',code:'ok'}]})],{robot:tele('crowd')}));
 await m.poll();
 assert.deepEqual([m.view.mode,m.view.source,m.view.billing,m.state.log.length],['crowd','hello',false,1]);
 assert.equal(m.delay(),2000);
});
test('mind without /mode (404): asks mind once a minute, hello every poll',async()=>{
 let t=0;const f=fakeFetch([res(404,{error:'not found'})],{robot:tele('talk')});
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
  assert.match(ui.modeBarHtml(m.view,[]),/模式未知（hello 和情景服务都没连上/);
 }
});
test('an older poll landing after a newer one is dropped (no old repaint, one modechange)',async()=>{
 let releaseOld;const old=new Promise(r=>releaseOld=r);
 const m=model(fakeFetch([()=>old,res(200,modeReply())],{robotQ:[tele('talk',{seq:6}),tele('crowd',{seq:7})]}));
 const a=m.poll(),b=m.poll();
 await b;assert.equal(m.view.mode,'crowd');
 releaseOld(res(200,modeReply()));await a;
 assert.equal(m.view.mode,'crowd');
 assert.deepEqual(m.seen.map(v=>v.mode),['crowd']);
});
test('modechange fires once per change, not on every poll',async()=>{
 const m=model(fakeFetch([res(200,modeReply())],{robotQ:[tele('talk'),tele('talk'),tele('idle',{seq:8})]}));
 await m.poll();await m.poll();await m.poll();
 assert.deepEqual(m.seen.map(v=>v.mode),['talk','idle']);
});
test('select posts to hello through /api/control and says so; nothing goes to mind\'s /mode',async()=>{
 const sent=[];const post=async(path,body)=>{sent.push([path,body]);return {accepted:true};};
 const m=model(fakeFetch([res(200,modeReply())]),{post});
 await m.poll();await m.select('subtitle','zh_en');
 assert.deepEqual(sent,[['/api/control',{action:'mode',boot:'b1',input:'select',value:{mode:'subtitle',dir:'zh_en'}}]]);
 assert.match(m.msg,/已发给 hello：字幕 中→英/);
 await m.helloPreset(5);
 assert.deepEqual(sent[1],['/api/control',{action:'mode',boot:'b1',input:'select',value:{mode:'crowd'}}]);
 assert.match(m.msg,/预设 5：已发给 hello：围观/);
});
test('select while hello cannot switch sends nothing and says why',async()=>{
 const sent=[];const post=async(path,body)=>{sent.push(path);return {};};
 const m=model(fakeFetch([res(200,modeReply())],{robot:{...tele('talk'),telemetry:{boot_id:'b1',seq:1}}}),{post});
 await m.poll();await m.select('crowd');
 assert.deepEqual(sent,[]);assert.equal(m.msg,ui.NO_MODES);
});
test('a refused control shows the monitor\'s words',async()=>{
 const post=async()=>{throw new Error('机器人已重启，请根据新状态重新操作');};
 const m=model(fakeFetch([res(200,modeReply())]),{post});
 await m.poll();await m.select('crowd');
 assert.equal(m.msg,'机器人已重启，请根据新状态重新操作');
});
test('the line under the bar: kept right after the page\'s own click, cleared when hello changes later',async()=>{
 let t=0;
 const m=model(fakeFetch([res(200,modeReply())],{robotQ:[tele('talk'),tele('crowd',{seq:8}),tele('crowd',{seq:8}),tele('idle',{seq:9,by:'asr'})]}),
  {post:async()=>({accepted:true}),now:()=>t});
 await m.poll();
 await m.select('crowd');                                  // its own poll already sees crowd
 assert.match(m.msg,/已发给 hello：围观/);
 t=10000;await m.poll();assert.match(m.msg,/已发给 hello/); // same mode, nothing new
 await m.poll();assert.equal(m.msg,'');                     // hello's own word went to IDLE later: the old line goes
});
test('one action at a time; the buttons are locked while it runs',async()=>{
 let release;const post=()=>new Promise(r=>release=r);const painted=[];
 const m=model(fakeFetch([res(200,modeReply())]),{post,paint:()=>painted.push(m.pending)});
 await m.poll();
 const a=m.select('crowd');await Promise.resolve();assert.equal(m.pending,true);assert.equal(m.msg,'发送中…');
 await m.select('idle');                                   // ignored while pending
 release({accepted:true});await a;
 assert.equal(m.pending,false);assert.ok(painted.includes(true));
});
test('preset 0 / 9 and billing go to mind with the page post',async()=>{
 const sent=[];
 const post=async(path,body)=>{sent.push([path,body]);
  if(path==='/api/mind/preset')return {snapshot:snap({billing:false}),result:{code:'ok'},effects:[],robot:{}};
  return {billing:{on:true},result:{code:'ok'}};};
 const m=model(fakeFetch([res(200,modeReply())]),{post});
 await m.poll();
 await m.preset(0);assert.deepEqual(sent[0],['/api/mind/preset',{n:0,source:'page'}]);assert.match(m.msg,/预设 0 花费全关/);
 await m.billing();assert.deepEqual(sent[1],['/api/mind/switches',{name:'billing',on:false}]);assert.match(m.msg,/计费总闸已开/);
});
test('the message line can be closed by hand (not while an action runs)',async()=>{
 const m=model(fakeFetch([res(200,modeReply())]),{post:async()=>({accepted:true})});
 await m.poll();await m.select('crowd');assert.notEqual(m.msg,'');
 m.dismiss();assert.equal(m.msg,'');
});
test('talk readout: a slow older answer cannot overwrite a newer one',async()=>{
 let slow;const base=fakeFetch([res(200,modeReply())]);
 let n=0;const fetchFn=async(path,o)=>{if(path.startsWith('/api/mind/say/stats')&&n++===0)return new Promise(r=>slow=r);return base(path,o);};
 const m=model(fetchFn);
 const a=m.readTalk();
 await m.readTalk();assert.equal(m.talk.prefetch,false);
 slow(res(200,{prefetch:{on:true},auto:{on:true}}));await a;
 assert.equal(m.talk.prefetch,false);
});
test('hello\'s state can come from the bar\'s own event stream instead of /api/state',async()=>{
 const f=fakeFetch([res(200,modeReply())]);
 const m=model(f,{robotSource:()=>tele('companion')});
 await m.poll();
 assert.equal(m.view.mode,'companion');assert.equal(f.calls.filter(c=>c[0]==='/api/state').length,0);
 const js=readFileSync(new URL('./public/mode-ui.js',import.meta.url),'utf8');
 assert.match(js,/new EventSource\('\/api\/events\?camera=0&servo=0'\)/);
});
