import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const {HearingPanel}=await import('data:text/javascript;base64,'+readFileSync(new URL('./public/hearing-ui.js',import.meta.url)).toString('base64'));
function fixture(){const elements=new Map();return {getElementById(id){if(!elements.has(id))elements.set(id,{value:'',checked:false,disabled:false,textContent:'',addEventListener(){}});return elements.get(id);}};}
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
