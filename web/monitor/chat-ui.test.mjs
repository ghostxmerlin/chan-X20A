// Chat v0 on the page: the 对话要先开听觉 hint next to the mode bar, the last chat turn in the say panel (escaped),
// and the proxy lets /chat/turns through but never /chat/heard (ear only).
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {MindProxy} from './mind.mjs';
const modeSrc=readFileSync(new URL('./public/mode-ui.js',import.meta.url),'utf8')
 .replace("from '/switches-ui.js'",`from '${new URL('./public/switches-ui.js',import.meta.url).href}'`);
const mode=await import('data:text/javascript;base64,'+Buffer.from(modeSrc).toString('base64'));
const say=await import('data:text/javascript;base64,'+readFileSync(new URL('./public/say-ui.js',import.meta.url)).toString('base64'));

const robot=(m,hearing)=>({online:true,fresh:{telemetry:true},boot:'b1',capabilities:{hearing:'supported'},
 telemetry:{boot_id:'b1',seq:3,hearing,mode:{mode:m,dir:null,host:null,return_to:null,seq:7,by:'asr',since:5}}});
const viewOf=st=>mode.modeView(null,mode.robotMode(st));

test('in chat with hearing off the bar says 对话要先开听觉',()=>{
 const st=robot('chat',{requested:false,state:'idle'});
 assert.equal(mode.chatHearingHint(viewOf(st),st),'对话要先开听觉');
 const h=mode.modeBarHtml(viewOf(st),[],mode.chatHearingHint(viewOf(st),st));
 assert.match(h,/<span class="mode-warn" role="note">对话要先开听觉<\/span>/);
});
test('no hint with hearing on, outside chat, or when hearing is unknown',()=>{
 const on=robot('chat',{requested:true,state:'listening'});
 assert.equal(mode.chatHearingHint(viewOf(on),on),'');
 const talk=robot('talk',{requested:false});
 assert.equal(mode.chatHearingHint(viewOf(talk),talk),'');
 for(const st of [{...robot('chat',{requested:false}),fresh:{telemetry:false}},{...robot('chat',{requested:false}),capabilities:{}},
  {...robot('chat',null)}])
  assert.equal(mode.chatHearingHint(viewOf(robot('chat',{})),st),'');
 assert.doesNotMatch(mode.modeBarHtml(viewOf(on),[]),/mode-warn/);
});

test('the last chat turn: heard -> reply, latency, model, cost',()=>{
 const h=say.chatTurnHtml({at:Date.now(),text:'今天几号',reply:'十四号，周五。',state:'answered',model:'gpt-6.1-terra',
  cost_usd:0.0006,who:'访客甲',timings:{llm_ms:1310,end_to_submit_ms:2400,tts_first_sound_ms:700}});
 assert.match(h,/<b>访客甲<\/b>：今天几号 → 十四号，周五。/);
 assert.match(h,/模型 1\.3 秒/);assert.match(h,/说完到送出 2\.4 秒/);assert.match(h,/说完到开口 3\.1 秒/);
 assert.match(h,/gpt-6\.1-terra/);assert.match(h,/\$0\.00060/);
});
test('a skipped turn says why; nothing is shown without a turn',()=>{
 assert.match(say.chatTurnHtml({at:1,text:'嗯',state:'skipped',skip:'filler'}),/→ <span class="muted">（只有语气词）<\/span>/);
 assert.match(say.chatTurnHtml({at:1,text:'远处',state:'skipped',skip:'far_quiet'}),/太远（声音太小）/);
 assert.match(say.chatTurnHtml({at:1,text:'x',state:'skipped',skip:'new_reason'}),/new_reason/);
 assert.equal(say.chatTurnHtml(null),'');assert.equal(say.chatTurnHtml(undefined),'');
});
test('every text in the chat card is escaped',()=>{
 const bad='<img src=x onerror=alert(1)>';
 const h=say.chatTurnHtml({at:1,text:bad,reply:bad,who:bad,model:bad,state:'answered',timings:{}});
 assert.doesNotMatch(h,/<img/);assert.equal((h.match(/&lt;img/g)||[]).length,4);
 assert.doesNotMatch(say.chatTurnHtml({at:1,text:'x',state:'skipped',skip:bad}),/<img/);
 assert.doesNotMatch(mode.modeBarHtml(viewOf(robot('chat',{})),[],bad),/<img/);
});
test('the say panel has the chat card; the proxy passes /chat/turns, not /chat/heard',async()=>{
 const index=readFileSync(new URL('./public/index.html',import.meta.url),'utf8');
 assert.match(index,/<div id="say-chat" class="say-chat"><\/div>/);
 assert.equal(MindProxy.check('/chat/turns?limit=1'),'/chat/turns?limit=1');
 assert.equal(MindProxy.check('/chat/turns?text=x'),null);
 const server=readFileSync(new URL('./server.mjs',import.meta.url),'utf8');
 assert.match(server,/'\/chat\/turns'\]\.includes\(sub\)/);
 assert.doesNotMatch(server,/chat\/heard/);
 assert.doesNotMatch(readFileSync(new URL('./mind.mjs',import.meta.url),'utf8'),/chat\/heard/);
});
