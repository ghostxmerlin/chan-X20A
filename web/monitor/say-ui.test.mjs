import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {Monitor} from './core.mjs';
import {createMonitorServer} from './server.mjs';
const ui=await import('data:text/javascript;base64,'+readFileSync(new URL('./public/say-ui.js',import.meta.url)).toString('base64'));
const roles=[{id:'roast',button:'吐槽',name:'毒舌脱口秀演员'},{id:'comfort',button:'鼓励',name:'程序员安慰师小姐姐'}];

test('say errors explain missing context and unwrap structured API errors', ()=>{
 assert.equal(ui.sayErrorMessage(new Error('{"code":"no_situation","error":"两路语音识别不一致，暂时无法确认内容"}')),
  '两路语音识别不一致，暂时无法确认内容');
 assert.equal(ui.sayErrorMessage(new Error('{"error":"暂时没有可靠的新情况"}')), '暂时没有可靠的新情况');
 assert.equal(ui.sayErrorMessage(new Error('{"error":"请先切到 TTS 模式"}')), '先把顶上的「嘴」切到 TTS');
 assert.equal(ui.sayErrorMessage(new Error('{"error":"生成超时"}')), '没说成：生成超时');
 assert.equal(ui.sayErrorMessage(new Error('Bad Gateway')), '没说成：Bad Gateway');
});

test('role buttons and a stop button', ()=>{
 const b=ui.roleButtonsHtml(roles);
 assert.match(b,/data-role="roast"/);assert.match(b,/吐槽/);assert.match(b,/data-say-stop/);assert.doesNotMatch(b,/style="/);
});
test('voice buttons show the rate', ()=>{
 const html=ui.voiceButtonsHtml([{id:'tang',label:'汤师爷',rate:1.15},{id:'xiaohei',label:'小黑',rate:1.2},{id:'x',label:'<b>',rate:1}]);
 assert.match(html,/data-voice="xiaohei"/);assert.match(html,/小黑 · 1.2×/);assert.match(html,/&lt;b&gt;/);assert.doesNotMatch(html,/style="/);
 assert.match(ui.voiceButtonsHtml([]),/没有音色/);
});
test('last lines: time, role, text, state, start time, cost', ()=>{
 const at=new Date(2026,10,11,17,5,3).getTime();
 const list=ui.utterancesHtml([{at,role:'roast',text:'这板子焊得，比我的发型还有个性。',state:'done',tts_cost_cny:0.0036,timings_s:{first_accepted:1.2}},
  {at,role:'try',text:'试听一句',state:'sent'}],roles);
 assert.match(list,/17:05:03/);assert.match(list,/吐槽/);assert.match(list,/说完了/);assert.match(list,/TTS 起播 1.2 秒/);assert.match(list,/试听/);
 assert.match(ui.utterancesHtml([{at,role:'roast',text:'一句',server_ms:2150,state:'sent'}],roles),/服务端 2.1 秒/);
 assert.match(ui.utterancesHtml([],roles),/还没说过话/);
 assert.match(ui.utterancesHtml([{at,role:'roast',text:'<b>',state:'sent'}],roles),/&lt;b&gt;/);
});
test('the say panel lives in the 台词 panel of the main page, not on 情景', ()=>{
 const index=readFileSync(new URL('./public/index.html',import.meta.url),'utf8');
 const start=index.indexOf('<section class="panel voice-panel"');
 assert.notEqual(start,-1);
 const end=index.indexOf('<section class="panel ',start+1);
 const voicePanel=index.slice(start,end===-1?undefined:end);
 for(const id of ['say-buttons','say-voices','say-msg','say-utterances'])assert.match(voicePanel,new RegExp(`id="${id}"`));
 assert.match(readFileSync(new URL('./public/app.js',import.meta.url),'utf8'),/SayPanel/);
 assert.doesNotMatch(readFileSync(new URL('./public/mind.html',import.meta.url),'utf8'),/mind-say-buttons|让 hello 说一句/);
});
test('server serves say-ui.js', async t=>{
 const server=createMonitorServer(new Monitor({address:'http://127.0.0.1:9',token:'x'.repeat(32),device_id:'d'}));
 await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>server.close());
 const r=await fetch(`http://127.0.0.1:${server.address().port}/say-ui.js`);
 assert.equal(r.status,200);assert.match(await r.text(),/export class SayPanel/);
});

// ---- one-button talk show (2026-11-13) ----
const round={rid:'r1',moment_id:'r1',order:['worst','roast','sad'],weak:false,timings:{write_ms:1900,judge_ms:1300},
 plays:[{key:'worst',why:'stock'}],thin_moment:false,
 lines:[{key:'roast',text:'吐槽<句>',voice_label:'汤师爷',dialect:'普通话',score:6},{key:'worst',text:'最坏句',voice_label:'主人自己的声音',dialect:'武汉话',score:8},
  {key:'sad',text:'伤心句',voice_label:'小黑',dialect:'四川话',score:4},{key:'comfort',text:null,dropped:'invalid'},
  {key:'huyu',text:'侬讲讲看伐',gloss:'你说说看嘛',voice_label:'龙安欢',dialect:'上海话'}]};
test('round card: the played line first and largest, the rest by score, the fallback with its gloss', ()=>{
 const html=ui.roundCardHtml(round,{ratings:{roast:3},none:false});
 assert.ok(html.indexOf('最坏句')<html.indexOf('吐槽')&&html.indexOf('吐槽')<html.indexOf('伤心句'));
 assert.match(html,/class="line played"/);
 assert.match(html,/你说说看嘛/);
 assert.match(html,/吐槽&lt;句&gt;/);
 assert.match(html,/好 ×3/);
 assert.doesNotMatch(html,/style="/);
 assert.doesNotMatch(html,/dropped|invalid/);
});
test('rating buffer merges presses after 600 ms, caps at 5, settles on a polarity switch', async()=>{
 const sent=[],buf=new ui.RatingBuffer(x=>sent.push(x),{settleMs:30});
 for(let i=0;i<3;i++)buf.press('r1','roast',1);
 await new Promise(r=>setTimeout(r,60));
 assert.deepEqual(sent,[{kind:'rating',value:{polarity:1,count:3},refers_to:{rid:'r1',line:'roast'}}]);
 for(let i=0;i<7;i++)buf.press('r1','sad',-1);
 buf.press('r1','sad',1);
 await new Promise(r=>setTimeout(r,60));
 assert.deepEqual(sent.slice(1),[{kind:'rating',value:{polarity:-1,count:5},refers_to:{rid:'r1',line:'sad'}},
                                 {kind:'rating',value:{polarity:1,count:1},refers_to:{rid:'r1',line:'sad'}}]);
});
test('round messages', ()=>{
 assert.equal(ui.sayRoundMessage({why:'silent'}),'这一刻先不说了');
 assert.match(ui.sayRoundMessage({why:'fresh',server_ms:4200,round}),/现写/);
 assert.match(ui.sayRoundMessage({why:'stock',server_ms:80,round}),/最坏句/);
});
test('the card follows the latest triggered round, not unspoken pre-writes (final review I3)', ()=>{
 const rows=[{rid:'r1',triggered:true,lines:[{key:'roast',text:'甲'}],plays:[{key:'roast'}],marks:{ratings:{}}},
  {rid:'r2',triggered:true,lines:[{key:'sad',text:'乙'}],plays:[{key:'sad'}],marks:{ratings:{sad:1}}},
  {rid:'r3',triggered:false,lines:[{key:'worst',text:'未说'}],plays:[],marks:{ratings:{}}}];
 assert.equal(ui.pickRound(rows).rid,'r2');
 assert.equal(ui.pickRound([rows[2]]),null);
 assert.equal(ui.roundKey(rows[1]),ui.roundKey(structuredClone(rows[1])));
 assert.notEqual(ui.roundKey(rows[1]),ui.roundKey({...rows[1],marks:{ratings:{sad:2}}}));
 const earlier=ui.earlierRoundsHtml(rows);
 assert.match(earlier,/<details/);assert.match(earlier,/甲/);assert.doesNotMatch(earlier,/未说/);assert.doesNotMatch(earlier,/乙/);
});
test('remote signals list', ()=>{
 const at=new Date(2026,10,13,15,4,5).getTime();
 const html=ui.signalsHtml([{at,kind:'rating',value:{polarity:1,count:3},source:{device:'dualkey-ae1c'},refers_to:{uid:'u1'}},
  {at,kind:'mode',value:{direction:'left'},source:{device:'dualkey-ae1c'}},{at,kind:'click',value:{type:'single'},source:{device:'page'}}]);
 assert.match(html,/好 ×3/);assert.match(html,/左/);assert.match(html,/点击/);assert.match(html,/dualkey-ae1c/);
 assert.match(ui.signalsHtml([]),/还没有/);
});
test('pre-written rounds are listed with their state and the worker status', ()=>{
 const now=new Date(2026,10,13,16,10,0).getTime();
 const ln=(key,text)=>({key,text,voice_label:'甲'});
 const rows=[{rid:'r1',source:'prefetch',written_at:now-400000,at:now-400000,triggered:false,order:['roast','sad'],lines:[ln('roast','<老>板'),ln('sad','唉')]},
  {rid:'r2',source:'prefetch',written_at:now-60000,at:now-60000,triggered:true,order:['sad','roast'],plays:[{key:'sad'}],lines:[ln('roast','甲句'),ln('sad','乙句')]},
  {rid:'r3',source:'prefetch',written_at:now-20000,at:now-20000,triggered:false,order:['roast','sad'],lines:[ln('roast','丙句'),ln('sad','丁句')]},
  {rid:'r4',source:'page',written_at:now-10000,at:now-10000,triggered:true,order:['roast'],lines:[ln('roast','页面')]}];
 const html=ui.prefetchRoundsHtml(rows,{on:true,last:{reason:'spacing',at:now-2000}},now);
 assert.match(html,/离上一轮不到 30 秒/);
 assert.doesNotMatch(html,/页面/);
 assert.ok(html.indexOf('丙句')<html.indexOf('乙句')&&html.indexOf('乙句')<html.indexOf('&lt;老&gt;板'));
 assert.match(html,/待用/);assert.match(html,/已用/);assert.match(html,/过期/);
 assert.match(ui.prefetchRoundsHtml([],{on:false},now),/提前写关着/);
});

test('家人声音 switch: rendered state and the settings body it saves',()=>{
 assert.match(ui.roundControlsHtml(false,true),/data-family="1" checked/);
 assert.doesNotMatch(ui.roundControlsHtml(false,false),/data-family="1" checked/);
 const cfg={version:7,speech:{pipeline:'round',round:{huyu:true,family:false},max_chars:40},memory:{learning:true,apply:false},roles:[],history:[]};
 const body=ui.familySettingsBody(cfg,true);
 assert.deepEqual(Object.keys(body).sort(),['expected_version','memory','note','speech']);
 assert.equal(body.expected_version,7);
 assert.equal(body.speech.round.family,true);assert.equal(body.speech.round.huyu,true);assert.equal(body.speech.max_chars,40);
 assert.equal(cfg.speech.round.family,false);              // the loaded settings are not mutated
});

test('连续说 switch: rendered next to 提前写 with its checked state',()=>{
 const on=ui.roundControlsHtml(true,false,true),off=ui.roundControlsHtml(true,false,false);
 assert.match(on,/data-auto="1" checked/);assert.doesNotMatch(off,/data-auto="1" checked/);
 assert.match(on,/连续说（每 2–3 分钟，播高分的提前写）/);
 assert.ok(on.indexOf('data-prefetch')<on.indexOf('data-auto')&&on.indexOf('data-auto')<on.indexOf('data-family'));
 assert.doesNotMatch(on,/style="/);
});
test('连续说 and 提前写 move together: auto on ticks prefetch, prefetch off unticks auto',()=>{
 assert.deepEqual(ui.linkedSwitches('auto',true),{prefetch:true});
 assert.deepEqual(ui.linkedSwitches('auto',false),{});
 assert.deepEqual(ui.linkedSwitches('prefetch',false),{auto:false});
 assert.deepEqual(ui.linkedSwitches('prefetch',true),{});
});
test('连续说 state line: on/off, next wake, last result with its time',()=>{
 const now=new Date(2026,10,14,10,0,0).getTime();
 assert.match(ui.autoStatusHtml({on:false,next_at:null,last:null}),/连续说关着/);
 const html=ui.autoStatusHtml({on:true,next_at:now+150000,last:{reason:'played',at:now-30000,n:2,stopped:false}});
 assert.match(html,/连续说开着/);assert.match(html,/下次 10:02:30/);assert.match(html,/09:59:30 播了 2 句/);
 for(const [reason,text] of [['no_stock','没有可用的提前写'],['no_high_score','没有高分'],['checked_none','都不合适'],['thin','没人说话'],['off','条件不满足'],['stopped','被叫停'],['busy','跳过']])
  assert.match(ui.autoStatusHtml({on:true,next_at:now,last:{reason,at:now,n:0}}),new RegExp(text),reason);
 assert.match(ui.autoStatusHtml({on:true,next_at:now,last:{reason:'played',at:now,n:1,stopped:true}}),/中途停了/);
 assert.match(ui.autoStatusHtml({on:true,next_at:now,last:{reason:'<x>',at:now,n:0}}),/&lt;x&gt;/);
 const pf=ui.prefetchRoundsHtml([],{on:true},now,{on:true,next_at:now+150000,last:null});
 assert.match(pf,/连续说开着/);assert.match(ui.prefetchRoundsHtml([],{on:false},now),/提前写关着/);
 assert.doesNotMatch(html,/style="/);
});

// ---- the panel follows the mode (Task B2) ----
test('round controls follow the mode only (billing gates nothing, owner 2026-11-14 evening)',()=>{
 const off=ui.roundControlsHtml(false,false,false,{talk:false});
 assert.match(off,/data-say-round="1"[^>]*disabled/);
 assert.match(off,/data-prefetch="1"[^>]*disabled/);
 assert.match(off,/data-auto="1"[^>]*disabled/);
 assert.match(off,/只在脱口秀模式/);
 assert.doesNotMatch(off,/data-say-stop="1"[^>]*disabled/);
 assert.doesNotMatch(off,/data-family="1"[^>]*disabled/);
 const open=ui.roundControlsHtml(true,false,true);   // no gate = today's panel
 assert.doesNotMatch(open,/disabled|只在脱口秀|计费/);
 assert.equal(open,ui.roundControlsHtml(true,false,true,{talk:true}));
 assert.equal(ui.roundControlsHtml(true,false,true,{talk:true,billing:false}),open);     // an old billing field: ignored
});
test('gate comes from the mode view only; unknown or absent mode never disables',()=>{
 assert.deepEqual(ui.gateOfView({known:true,mode:'idle',billing:true}),{talk:false});
 assert.deepEqual(ui.gateOfView({known:true,mode:'talk',billing:false}),{talk:true});
 for(const v of [null,undefined,{},{known:false,why:'absent',mode:null},{known:false,why:'down',billing:false}])
  assert.deepEqual(ui.gateOfView(v),{talk:true});
});
test('gate note text: not-talk, else nothing',()=>{
 assert.match(ui.gateNote({talk:false}),/只在脱口秀模式/);
 assert.equal(ui.gateNote({talk:true}),'');assert.equal(ui.gateNote(undefined),'');
});
test('role buttons follow the gate; stop stays; 试听 is never disabled',()=>{
 const r=ui.roleButtonsHtml(roles,{talk:false});
 assert.match(r,/data-role="roast"[^>]*disabled/);assert.doesNotMatch(r,/data-say-stop="1"[^>]*disabled/);
 assert.doesNotMatch(ui.roleButtonsHtml(roles),/disabled/);
 assert.doesNotMatch(ui.voiceButtonsHtml([{id:'tang',label:'汤'}]),/disabled/);
});
test('409 mode codes show mind\'s reason',()=>{
 assert.equal(ui.sayErrorMessage(new Error('{"error":"只在脱口秀模式","code":"only_talk"}')),'只在脱口秀模式');
 assert.equal(ui.conflictCode(new Error('{"error":"只在脱口秀模式","code":"only_talk"}')),'only_talk');
 assert.equal(ui.conflictCode(new Error('{"error":"计费关着","code":"billing_off"}')),null);
 assert.equal(ui.conflictCode(new Error('{"error":"x","code":"no_situation"}')),null);
 assert.equal(ui.conflictCode(new Error('Bad Gateway')),null);
 assert.deepEqual(ui.gateAfterConflict('only_talk',{talk:true}),{talk:false});
 assert.deepEqual(ui.gateAfterConflict('billing_off',{talk:true}),{talk:true});
});
