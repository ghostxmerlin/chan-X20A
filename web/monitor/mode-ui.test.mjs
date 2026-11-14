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

// ---- brief tests (verbatim) ----
test('synced state follows robot ack',()=>{
 const st={snapshot:{seq:5,mode:'crowd',look:{label:'MEET',color:'30D060'},billing:true,since:1,by:'dualkey'},robot:{connected:true,applied_seq:5},log:[]};
 assert.equal(ui.modeView(st).synced,'yes');
 assert.equal(ui.modeView({...st,robot:{connected:true,applied_seq:4}}).synced,'waiting');
 assert.equal(ui.modeView({...st,robot:{connected:false,applied_seq:5}}).synced,'offline');
});
test('bar shows nine buttons and escapes log text',()=>{
 const h=ui.modeBarHtml(ui.modeView({snapshot:{seq:1,mode:'talk',look:{label:'TALK',color:'E040A0'},billing:true},robot:{connected:true,applied_seq:1}}),
  [{at:1,source:'page',from:'talk',to:'<img src=x>',code:'ok'}]);
 assert.equal((h.match(/data-mode=/g)||[]).length,9);
 assert.doesNotMatch(h,/<img/);
});
test('quick panel greys undefined presets and says when nothing costs',()=>{
 const h=ui.quickHtml({billing:false});
 assert.match(h,/现在不花钱/);
 for(const n of [5,6,7,8])assert.match(h,new RegExp(`data-preset="${n}"[^>]*disabled`));
});
test('robot half of a preset uses the switch bar tiers',()=>{
 assert.deepEqual(ui.presetRobotSteps({hearing:'local',vision:'upload'}),[['hearing','local'],['vision','upload']]);
 assert.deepEqual(ui.presetRobotSteps(undefined),[]);
});

// ---- more ----
test('unknown state says so instead of a blank bar, and every button is off',()=>{
 for(const st of [null,undefined,{},{snapshot:null},{snapshot:{mode:42}}]){
  const v=ui.modeView(st);assert.equal(v.known,false);
  const h=ui.modeBarHtml(v,[]);
  assert.match(h,/模式未知/);
  assert.equal((h.match(/data-mode="[^"]*"[^>]*disabled/g)||[]).length,9);
  for(const n of [0,1,2,3,4,9])assert.match(ui.quickHtml(v),new RegExp(`data-preset="${n}"[^>]*disabled`));
 }
});
test('sync text: shown, waiting, offline (shown after it connects)',()=>{
 assert.match(ui.modeBarHtml(ui.modeView(state()),[]),/机器人已显示/);
 assert.match(ui.modeBarHtml(ui.modeView(state({},{connected:true,applied_seq:0})),[]),/等机器人/);
 assert.match(ui.modeBarHtml(ui.modeView(state({},{connected:false,applied_seq:null})),[]),/机器人离线（连上后照这个显示）/);
 assert.equal(ui.modeView({snapshot:snap()}).synced,'offline');
});
test('current mode button is pressed; subtitle has two buttons with their direction',()=>{
 const h=ui.modeBarHtml(ui.modeView(state({mode:'subtitle',dir:'en_zh',look:{label:'SUB EN>ZH',color:'7C6CF0'}})),[]);
 assert.match(h,/data-mode="subtitle" data-dir="zh_en"[^>]*aria-pressed="false"/);
 assert.match(h,/data-mode="subtitle" data-dir="en_zh"[^>]*aria-pressed="true"/);
 assert.match(h,/字幕 英→中/);
 assert.equal((h.match(/aria-pressed="true"/g)||[]).length,1);
 assert.match(ui.modeBarHtml(ui.modeView(state()),[]),/data-mode="talk"[^>]*aria-pressed="true"[^>]*title="[^"]*再点 = 离开/);
});
test('picking a button sends select; the current one is sent too (mind leaves it)',()=>{
 assert.deepEqual(ui.selectRequest('crowd'),{input:'select',value:{mode:'crowd'}});
 assert.deepEqual(ui.selectRequest('subtitle','en_zh'),{input:'select',value:{mode:'subtitle',dir:'en_zh'}});
});
test('event-driven: AUTO while waiting, A when hosting',()=>{
 const wait=ui.modeView(state({mode:'event',look:{label:'AUTO',color:'20B8C8'}}));
 assert.equal(wait.event,'waiting');assert.match(ui.modeBarHtml(wait,[]),/class="mode-auto"[^>]*>AUTO</);
 const host=ui.modeView(state({mode:'crowd',host:'event'}));
 assert.equal(host.event,'hosting');assert.match(ui.modeBarHtml(host,[]),/class="mode-auto"[^>]*>A</);
 assert.equal(ui.modeView(state()).event,null);assert.doesNotMatch(ui.modeBarHtml(ui.modeView(state()),[]),/mode-auto/);
});
test('settings shows where it goes back to',()=>{
 const v=ui.modeView(state({mode:'settings',return_to:'subtitle',dir:'zh_en'}));
 assert.equal(v.returnTo,'subtitle');
 assert.match(ui.modeBarHtml(v,[]),/关了回到 字幕 中→英/);
});
test('the colour only travels as a checked data attribute (CSP: no style attributes)',()=>{
 const good=ui.modeBarHtml(ui.modeView(state()),[]);
 assert.match(good,/data-color="E040A0"/);assert.doesNotMatch(good,/style=/);
 const bad=ui.modeBarHtml(ui.modeView(state({look:{label:'X',color:'red;x"'}})),[]);
 assert.doesNotMatch(bad,/data-color=/);
 assert.equal(ui.modeView(state({look:{label:'X',color:'zzzzzz'}})).color,null);
});
test('log rows: newest ten, readable, everything escaped',()=>{
 const rows=Array.from({length:12},(_,i)=>({at:i,source:'page',input:'select',from:'talk',to:'crowd',code:'ok'}));
 rows.push({at:99,source:'<b>',input:'billing',value:false,from:'talk',to:'talk',code:'"><x'});
 const h=ui.modeBarHtml(ui.modeView(state()),rows);
 assert.equal((h.match(/<li/g)||[]).length,10);
 assert.match(h,/脱口秀 → 围观/);assert.doesNotMatch(h,/<b>|"><x/);
 assert.match(ui.logLine({at:1,source:'page',input:'billing',value:false,from:'talk',to:'talk',code:'ok'}),/计费总闸 关/);
 assert.match(ui.logLine({at:1,source:'quick',input:'digit',value:1,from:'talk',to:'talk',code:'ok'}),/预设 1/);
 assert.match(ui.logLine({at:1,source:'timeout',input:'timeout',from:'settings',to:'talk',code:'ok'}),/超时/);
});
test('quick panel: billing switch and ten keys with names',()=>{
 const on=ui.quickHtml(ui.modeView(state()));
 assert.match(on,/data-billing[^>]*aria-pressed="true"/);assert.doesNotMatch(on,/现在不花钱/);
 assert.equal((on.match(/data-preset=/g)||[]).length,10);
 assert.match(on,/data-preset="9"[^>]*title="花费全开"/);
 for(const n of [0,1,2,3,4,9])assert.doesNotMatch(on,new RegExp(`data-preset="${n}"[^>]*disabled`));
 const off=ui.quickHtml(ui.modeView(state({billing:false})));
 assert.match(off,/data-billing[^>]*aria-pressed="false"/);assert.match(off,/现在不花钱/);
});
test('quick panel shows the talk-show switches when known',()=>{
 const h=ui.quickHtml(ui.modeView(state()),{prefetch:true,auto:false,family:false});
 assert.match(h,/提前写 开/);assert.match(h,/连续说 关/);assert.match(h,/家人声音 关/);
 assert.doesNotMatch(ui.quickHtml(ui.modeView(state())),/mode-talk/);
});
test('robot steps: preset 9 takes the paid tiers, junk is dropped',()=>{
 assert.deepEqual(ui.presetRobotSteps({hearing:'tingwu',vision:'recognize'}),[['hearing','tingwu'],['vision','recognize']]);
 assert.deepEqual(ui.presetRobotSteps({}),[]);
 assert.deepEqual(ui.presetRobotSteps({hearing:'off',vision:'evil',x:1}),[]);
});
test('result and effect text',()=>{
 assert.equal(ui.resultText({code:'ok'}),'');
 assert.match(ui.resultText({code:'not_implemented'}),/还没做/);
 assert.equal(ui.resultText({code:'<x>'}),'<x>');          // plain text: the bar uses textContent
 assert.match(ui.effectsText(['stop_speech','talk_enter'],true),/提前写、连续说打开/);
 assert.match(ui.effectsText(['stop_speech','talk_enter'],false),/计费关着.*没开/);
 assert.match(ui.effectsText(['talk_leave'],true),/提前写、连续说关/);
 assert.equal(ui.effectsText([],true),'');
 assert.equal(ui.errorText(new Error('{"error":"计费关着","code":"billing_off"}')),'计费关着');
 assert.equal(ui.errorText(new Error('情景服务连接中')),'情景服务连接中');
 assert.match(ui.errorText(Object.assign(new Error('x'),{name:'TimeoutError'})),/结果未确认/);
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
  if(path==='/api/mind/preset')return {snapshot:snap({billing:false}),result:{code:'ok',toast:'PRESET 1'},effects:[],robot:{hearing:'local',vision:'upload'}};
  if(path==='/api/control')throw new Error('请恢复监控并等待新的机器人状态');
  return {};};
 const out=await ui.runPreset(1,{post,robotState:null});
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
 const out=await ui.runPreset(2,{post,robotState});
 assert.deepEqual(sent,['/api/mind/preset']);assert.match(out.message,/预设 2.*计费关着/);
});
test('an undefined preset answers in words',async()=>{
 const post=async()=>({snapshot:snap(),result:{code:'undefined_preset',toast:'PRESET UNDEFINED'},effects:[],robot:{}});
 assert.match((await ui.runPreset(5,{post,robotState})).message,/还没定义/);
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
 assert.match(js,/querySelector\('\.mode-msg'\)\.textContent=/);assert.doesNotMatch(js,/mode-msg'\)\.innerHTML/);
});
