import test from 'node:test';
import assert from 'node:assert/strict';
import { InteractionPanel } from './public/control-ui.js';

function setup(send){
 const elements=new Map(),calls=[];
 const doc={getElementById(id){if(!elements.has(id))elements.set(id,{textContent:'',title:'',disabled:false,attrs:{},addEventListener(event,fn){this[event]=fn;},setAttribute(key,value){this.attrs[key]=value;}});return elements.get(id);}};
 const ui=new InteractionPanel(doc,send||(async body=>{calls.push(body);return {accepted:true,message:'已接收'};}));
 const s={boot:'a',capabilities:{web_control:'supported',interaction_modes:'supported',face_tracking:'supported'},telemetry:{m12:{valid:true},remote:{motor:{enabled:true,requested:true}},interaction:{tracking:{requested:false,active:false,state:'off',anchor_deg:null,target_deg:null,range_deg:10,max_step_deg:1},recognition:{requested:false,state:'off',owner_seen:false,owner_name:''},dialogue:{state:'off',owned:false},advanced:{requested:false,supported:false,state:'not_implemented'}}}};
 return {ui,s,calls,el:id=>doc.getElementById(id)};
}
test('interaction render and reconnect never send; explicit toggles preserve current boot',async()=>{
 const {ui,s,calls,el}=setup();ui.render(s,true);ui.render(s,true);assert.equal(calls.length,0);
 await el('interaction-tracking').click();assert.deepEqual(calls,[{action:'interaction',mode:'tracking',enabled:true,boot:'a'}]);
 assert.equal(el('interaction-tracking').attrs['aria-pressed'],'false','acceptance must not masquerade as telemetry');
 s.telemetry.interaction.tracking={requested:true,active:false,state:'waiting_feedback'};ui.render(s,true);
 assert.match(el('tracking-state').textContent,/等待反馈/);assert.equal(el('interaction-tracking').attrs['aria-pressed'],'true');
 await el('interaction-tracking').click();assert.equal(calls[1].enabled,false);
 s.boot='b';s.telemetry.interaction.tracking.requested=false;ui.render(s,true);assert.equal(calls.length,2);
 await el('interaction-recognition').click();assert.deepEqual(calls[2],{action:'interaction',mode:'recognition',enabled:true,boot:'b'});
});
test('stale and legacy modes block writes, clear owner/target and expose compatibility guidance',async()=>{
 const {ui,s,calls,el}=setup();
 s.telemetry.interaction.tracking={requested:true,active:true,state:'tracking',anchor_deg:2,target_deg:3};
 s.telemetry.interaction.recognition={requested:true,state:'owner_confirmed',owner_seen:true,owner_name:'Alex <tag>'};
 s.telemetry.interaction.dialogue={state:'listening',owned:true};
 ui.render(s,true);assert.match(el('tracking-detail').textContent,/2.0°.*3.0°/);assert.match(el('recognition-detail').textContent,/Alex <tag>/);
 ui.render(s,false);for(const mode of ['tracking','recognition','advanced']) {assert.equal(el('interaction-'+mode).disabled,true);await el('interaction-'+mode).click();}
 assert.doesNotMatch(el('recognition-detail').textContent,/Alex/);assert.doesNotMatch(el('tracking-detail').textContent,/3.0°/);
 s.capabilities={web_control:'supported'};ui.render(s,true);await el('interaction-tracking').click();
 assert.match(el('tracking-state').textContent,/固件/);assert.match(el('asr-mode-note').textContent,/open 台词/);assert.equal(calls.length,0);
});
test('tracking requires motor and UART; requested modes can still be switched off',async()=>{
 const {ui,s,calls,el}=setup();s.telemetry.remote.motor.requested=false;ui.render(s,true);
 assert.equal(el('interaction-tracking').disabled,true);assert.equal(el('interaction-recognition').disabled,false);
 s.telemetry.m12.valid=false;ui.render(s,true);assert.equal(el('interaction-recognition').disabled,true);
 for(const mode of ['tracking','recognition'])s.telemetry.interaction[mode].requested=true;
 ui.render(s,true);for(const mode of ['tracking','recognition']) {assert.equal(el('interaction-'+mode).disabled,false);await el('interaction-'+mode).click();}
 assert.deepEqual(calls.map(x=>[x.mode,x.enabled]),[['tracking',false],['recognition',false]]);
 delete s.capabilities.face_tracking;ui.render(s,true);assert.equal(el('interaction-tracking').disabled,true);
});
test('advanced is explicitly reserved and recognition does not imply a real QA backend',async()=>{
 const {ui,s,calls,el}=setup();ui.render(s,true);await el('interaction-advanced').click();
 assert.deepEqual(calls,[{action:'interaction',mode:'advanced',enabled:true,boot:'a'}]);
 s.telemetry.interaction.advanced.requested=true;s.telemetry.interaction.recognition={requested:true,state:'owner_confirmed',owner_seen:false,owner_name:''};
 s.telemetry.interaction.dialogue={state:'backend_unavailable',owned:false};ui.render(s,true);
 assert.match(el('advanced-state').textContent,/未实现/);assert.match(el('advanced-detail').textContent,/意图开启/);
 assert.match(el('recognition-detail').textContent,/后端未就绪/);assert.match(el('recognition-detail').title,/情绪/);
 assert.match(el('asr-mode-note').textContent,/open.*追踪.*close.*next.*台词/);
});
test('pending and uncertain results never duplicate, retry or replay an interaction command',async()=>{
 let reject;const calls=[];const {ui,s,el}=setup(body=>{calls.push(body);return new Promise((_,r)=>reject=r);});
 ui.render(s,true);const pending=el('interaction-tracking').click();await el('interaction-recognition').click();assert.equal(calls.length,1);
 s.boot='b';ui.render(s,true);reject(new Error('结果未确认'));await pending;ui.render(s,true);assert.equal(calls.length,1);
 assert.match(el('interaction-command').textContent,/未确认/);
});
