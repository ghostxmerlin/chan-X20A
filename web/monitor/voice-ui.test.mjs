import test from 'node:test';
import assert from 'node:assert/strict';
import { VoicePanel } from './public/voice-ui.js';
function setup(){
 const elements=new Map(),calls=[];
 const make=()=>({value:'',textContent:'',disabled:false,children:[],addEventListener(k,fn){this[k]=fn;},replaceChildren(...v){this.children=v;if(v.length)this.value=v[0].value;}});
 const doc={getElementById(id){if(!elements.has(id))elements.set(id,make());return elements.get(id);},createElement(){return make();}};
 const ui=new VoicePanel(doc,async x=>{calls.push(x);return {accepted:true,message:'已接收'};});
 const s={boot:'a',capabilities:{voice_library:'supported'},voiceLibrary:{clips:[{id:1,text:'测试 <tag>',title:'片段',seconds:2}]},telemetry:{health:{audio_ready:true},playback:{busy:false}}};
 return {ui,s,calls,el:id=>doc.getElementById(id)};
}
test('voice render and reconnect never play; explicit random/selected preserve boot',async()=>{
 const {ui,s,calls,el}=setup();ui.render(s,true);ui.render(s,true);assert.equal(calls.length,0);
 assert.match(el('voice-select').children[0].textContent,/<tag>/);
 await el('voice-random').click();assert.deepEqual(calls,[{action:'voice',clip_id:0,boot:'a'}]);
 await el('voice-play').click();assert.equal(calls[1].clip_id,1);
 s.boot='b';ui.render(s,true);assert.equal(calls.length,2);
});
test('new clip 40 is selectable and waiting for ASR blocks another request',async()=>{
 const {ui,s,calls,el}=setup();s.voiceLibrary.clips.push({id:40,text:'new',title:'new',seconds:2});
 ui.render(s,true);el('voice-select').value='40';await el('voice-play').click();assert.equal(calls[0].clip_id,40);
 s.telemetry.playback={busy:true,state:'waiting',source:'asr_open'};ui.render(s,true);
 assert.match(el('voice-status').textContent,/等待 ASR/);assert.equal(el('voice-play').disabled,true);
 await el('voice-random').click();assert.equal(calls.length,1);
});
test('busy, stale, unready and old firmware block playback and never replay',async()=>{
 const {ui,s,calls,el}=setup();s.telemetry.playback={busy:true,state:'playing',voice_text:'hello',source:'asr_open',submitted_bytes:24000,expected_bytes:48000};
 ui.render(s,true);assert.match(el('voice-status').textContent,/播放中/);assert.match(el('voice-current').textContent,/hello/);
 await el('voice-random').click();assert.equal(calls.length,0);
 ui.render(s,false);assert.equal(el('voice-current').textContent,'—');
 s.telemetry.playback.busy=false;s.telemetry.health.audio_ready=false;ui.render(s,true);await el('voice-play').click();
 s.telemetry.health.audio_ready=true;s.capabilities={};ui.render(s,true);await el('voice-random').click();assert.equal(calls.length,0);
});
