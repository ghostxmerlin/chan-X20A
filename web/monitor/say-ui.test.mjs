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
