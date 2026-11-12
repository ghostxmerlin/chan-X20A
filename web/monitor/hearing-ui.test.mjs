import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const {HearingPanel}=await import('data:text/javascript;base64,'+readFileSync(new URL('./public/hearing-ui.js',import.meta.url)).toString('base64'));
function fixture(){const elements=new Map();return {getElementById(id){if(!elements.has(id))elements.set(id,{value:'',checked:false,disabled:false,textContent:'',addEventListener(){}});return elements.get(id);}};}
test('initial page render before the first telemetry message is safe and passive',()=>{
 const doc=fixture(),calls=[],panel=new HearingPanel(doc,async body=>{calls.push(body);return {};});
 panel.render(undefined,false);
 assert.equal(doc.getElementById('hearing-text').textContent,'等待语音');
 assert.equal(doc.getElementById('hearing-start').disabled,true);
 assert.equal(calls.length,0);
});
test('configuration never writes on render; enrollment needs name and live status',async()=>{
 const doc=fixture(),calls=[],panel=new HearingPanel(doc,async body=>{calls.push(body);return {message:'accepted'};});
 const state={boot:'a',capabilities:{hearing:'supported'},telemetry:{health:{audio_ready:true},hearing:{ready:true,url:'ws://127.0.0.1/',token_configured:true,state:'idle',enrollment:{active:false}}}};
 panel.render(state,true);assert.equal(calls.length,0);assert.equal(doc.getElementById('hearing-url').value,'ws://127.0.0.1/');
 doc.getElementById('hearing-name').value='papa';await panel.act('enroll');
 assert.deepEqual(calls[0],{action:'hearing_enroll',boot:'a',name:'papa',replace:false});
 panel.render(state,false);await panel.act('listen',{enabled:true});assert.equal(calls.length,1);
 assert.equal(doc.getElementById('hearing-start').disabled,true);
});
test('secret clears after save and telemetry never overwrites in-progress editing',async()=>{
 const doc=fixture(),panel=new HearingPanel(doc,async()=>({message:'saved'}));
 const state={boot:'a',capabilities:{hearing:'supported'},telemetry:{hearing:{ready:true,url:'ws://127.0.0.1/',token_configured:true,enrollment:{}}}};
 panel.render(state,true);doc.getElementById('hearing-url').value='ws://127.0.0.2/';doc.getElementById('hearing-token').value='secret';
 panel.render(state,true);assert.equal(doc.getElementById('hearing-url').value,'ws://127.0.0.2/');
 await panel.act('config');assert.equal(doc.getElementById('hearing-token').value,'');
});

test('heartbeat and disconnect reasons are shown from telemetry', () => {
 const doc=fixture(),panel=new HearingPanel(doc,async()=>({}));
 const hearing={ready:true,url:'ws://x/',token_configured:true,state:'listening',connected:true,enrollment:{active:false},send_max_us:183000,session_dropped_blocks:2,backlog:{capacity_packets:167,depth:3,max_depth:40,dropped_packets:1},stalls:{over_200ms:5,over_1s:2,longest_ms:1180,current_ms:0},
  heartbeat:{interval_ms:100,timeout_ms:3000,last_age_ms:80,ticks:1200,max_gap_ms:450,max_gap_boot_ms:3100,server_level_db:-40.25,server_noise_db:-44.1,server_rx_gap_ms:60},
  disconnects:{heartbeat_timeout:2,send_failed:1,transport_closed:0,stopped:4},last_end:{reason:'heartbeat_timeout',age_ms:65000,duration_ms:34000,dropped_blocks:12,error:''}};
 panel.render({boot:'a',capabilities:{hearing:'supported'},telemetry:{health:{audio_ready:true},hearing}},true);
 const hb=doc.getElementById('hearing-heartbeat').textContent,link=doc.getElementById('hearing-link').textContent;
 assert.match(hb,/80 ms 前 · 本次 1200 次 · 最大间隔 450 ms（超时 3000 ms）/);assert.match(hb,/电平 -40.3 \/ 底噪 -44.1 dB/);assert.match(hb,/待发 3 包（最多 40 \/ 167，溢出 1）· 卡顿 >200ms 5 次 \/ >1s 2 次，最长 1180 ms · 本次丢块 2/);
 assert.match(link,/心跳超时 2 \/ 发送失败 1/);assert.doesNotMatch(link,/停止 4/);assert.match(link,/上次 心跳超时 65 s前（连了 34 s，丢 12 块）/);
 hearing.connected=false;panel.render({boot:'a',capabilities:{hearing:'supported'},telemetry:{health:{},hearing}},true);
 assert.match(doc.getElementById('hearing-heartbeat').textContent,/未连接 · 开机以来最大间隔 3100 ms/);
});

test('pending response and happy accumulation are shown', () => {
 const doc=fixture(),panel=new HearingPanel(doc,async()=>({}));
 const hearing={ready:true,url:'ws://x/',token_configured:true,state:'listening',connected:true,enrollment:{active:false},
  response:{pending:true,pending_clip:41,pending_wait_ms:2300,server_speaking:true,quiet_ms:1500,max_wait_ms:10000,happy_count:1,happy_needed:2,happy_window_left_s:200}};
 panel.render({boot:'a',capabilities:{hearing:'supported'},telemetry:{health:{},hearing}},true);
 assert.equal(doc.getElementById('hearing-response').textContent,'回应：等安静中 #41，已等 2.3 s（有人在说话，安静 1.5 s 后说，最多等 10 s） · 开心 1/2 次回应一次（窗口剩 3:20）');
 hearing.response={pending:false,happy_count:0,happy_needed:1,happy_window_left_s:0,quiet_ms:1500,max_wait_ms:10000};
 panel.render({boot:'a',capabilities:{hearing:'supported'},telemetry:{health:{},hearing}},true);
 assert.equal(doc.getElementById('hearing-response').textContent,'回应：空闲 · 开心 本窗口首次即回应');
});

test('a punctuation-only misfire does not replace the last real sentence', () => {
 const doc=fixture(),panel=new HearingPanel(doc,async()=>({}));
 const base={ready:true,url:'ws://x/',token_configured:true,state:'listening',connected:true,enrollment:{active:false}};
 panel.render({boot:'a',capabilities:{hearing:'supported'},telemetry:{health:{},hearing:{...base,text:'你好呀',last_speech_age_ms:2000}}},true);
 assert.equal(doc.getElementById('hearing-text').textContent,'你好呀 · 2 秒前');
 panel.render({boot:'a',capabilities:{hearing:'supported'},telemetry:{health:{},hearing:{...base,text:'.',last_speech_age_ms:500}}},true);
 assert.equal(doc.getElementById('hearing-text').textContent,'你好呀');
});
