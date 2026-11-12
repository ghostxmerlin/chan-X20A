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
