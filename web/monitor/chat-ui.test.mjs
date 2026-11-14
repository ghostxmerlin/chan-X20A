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
test('a streamed turn shows the measured end-of-speech to first sound',()=>{
 const h=say.chatTurnHtml({at:1,text:'几点了',reply:'三点。',state:'answered',
  timings:{llm_ms:1000,stream:true,end_to_submit_ms:2000,tts_first_sound_ms:1600,end_to_sound_ms:2700}});
 assert.match(h,/流式/);assert.match(h,/说完到开口 2\.7 秒/);assert.doesNotMatch(h,/说完到开口 3\.6 秒/);
});
test('a skipped turn says why; nothing is shown without a turn',()=>{
 assert.match(say.chatTurnHtml({at:1,text:'嗯',state:'skipped',skip:'filler'}),/→ <span class="muted">（只有语气词）<\/span>/);
 assert.match(say.chatTurnHtml({at:1,text:'远处',state:'skipped',skip:'far_quiet'}),/太远（声音太小）/);
 assert.match(say.chatTurnHtml({at:1,text:'噪声',state:'skipped',skip:'not_voice'}),/不像人声/);
 assert.match(say.chatTurnHtml({at:1,text:'x',state:'skipped',skip:'budget'}),/今天的模型预算/);
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
 assert.match(server,/'\/chat\/turns'[,\]]/);
 assert.doesNotMatch(server,/chat\/heard/);
 assert.doesNotMatch(readFileSync(new URL('./mind.mjs',import.meta.url),'utf8'),/chat\/heard/);
});

// ---- stage indicator (2026-11-14): live dots + per-turn timeline ----
test('live stage dots: the current one is lit, off / unknown shows nothing',()=>{
 const h=say.chatStageHtml({active:true,stage:'thinking',since:1});
 assert.match(h,/class="chat-dot on" data-stage="thinking"/);
 assert.equal((h.match(/chat-dot on/g)||[]).length,1);
 for(const k of ['listening','speaking','skipped'])assert.match(h,new RegExp(`data-stage="${k}"`));
 assert.equal((say.chatStageHtml({active:true,stage:'idle'}).match(/chat-dot on/g)||[]).length,0);
 assert.equal(say.chatStageHtml({active:false,stage:'off'}),'');assert.equal(say.chatStageHtml(null),'');
 assert.doesNotMatch(say.chatStageHtml({active:true,stage:'<img src=x>'}),/<img/);
});
const T=1794300000000;
const turn={tid:'t1',at:T+2600,text:'今天几号',reply:'十四号。',state:'answered',model:'gpt-6.1-terra',
 timings:{speech_start_at:T,speech_end_at:T+1200,ear_done_at:T+2500,received_at:T+2600,picked_at:T+2605,
  stream_open_at:T+2620,llm_start_at:T+2630,llm_end_at:T+3700,submit_at:T+3710,first_audio_at:T+4200,sound_at:T+4500,
  done_at:T+6000,llm_ms:1070,end_to_sound_ms:3300,stream:true}};
test('the timeline has every mark in order with ms from the speech start',()=>{
 const m=say.chatTimelineMarks(turn);
 assert.deepEqual(m.map(x=>x.key),['speech_start_at','speech_end_at','ear_done_at','received_at','picked_at',
  'stream_open_at','llm_start_at','llm_end_at','submit_at','first_audio_at','sound_at','done_at']);
 assert.deepEqual([m[0].ms,m[1].ms,m.at(-2).ms,m.at(-1).ms],[0,1200,4500,6000]);
 const h=say.chatTimelineHtml(turn);
 assert.match(h,/<svg class="chat-tl"/);assert.match(h,/开始说 <b>0 ms<\/b>/);assert.match(h,/开口 <b>4500 ms<\/b>/);
 assert.equal((h.match(/<line class="m-/g)||[]).length,12);
 assert.doesNotMatch(h,/style=/);                                       // CSP: positions are SVG attributes
 assert.equal(say.chatTimelineHtml({timings:{sound_at:T}}),'');          // one mark is no timeline
 assert.equal(say.chatTimelineHtml({timings:{speech_start_at:'x',sound_at:T}}),'');
});
test('the chat card: the latest turn with its timeline, then skipped sentences with reason and loudness',()=>{
 const rows=[turn,{tid:'t0',at:T-5000,text:'远处<b>说话</b>',state:'skipped',skip:'far_quiet',loud_db:4.2},
  {tid:'t9',at:T-9000,text:'嗯',state:'skipped',skip:'filler',loud_db:12}];
 const h=say.chatTurnsHtml(rows);
 assert.match(h,/今天几号 → 十四号。/);assert.match(h,/<svg class="chat-tl"/);
 assert.match(h,/远处&lt;b&gt;说话&lt;\/b&gt;/);assert.match(h,/太远（声音太小）/);assert.match(h,/4\.2 dB/);
 assert.match(h,/只有语气词/);assert.doesNotMatch(h,/<b>说话/);
 assert.equal(say.chatTurnsHtml([]),'');assert.equal(say.chatTurnsHtml(null),'');
});
test('the page has the dots box; the proxy passes /chat/state but never /chat/stage',()=>{
 const index=readFileSync(new URL('./public/index.html',import.meta.url),'utf8');
 assert.match(index,/<div id="chat-stage" class="chat-stage"><\/div>/);
 assert.equal(MindProxy.check('/chat/state'),'/chat/state');
 const server=readFileSync(new URL('./server.mjs',import.meta.url),'utf8');
 assert.match(server,/'\/chat\/state'/);assert.doesNotMatch(server,/chat\/stage/);
 assert.doesNotMatch(readFileSync(new URL('./mind.mjs',import.meta.url),'utf8'),/chat\/stage/);
});
