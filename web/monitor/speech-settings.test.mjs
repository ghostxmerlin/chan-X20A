import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const ui=await import('data:text/javascript;base64,'+readFileSync(new URL('./public/speech-settings.js',import.meta.url)).toString('base64'));
const cfg=()=>({version:4,memory:{learning:true,apply:true},roles:[{id:'sad',button:'伤心评述',dialect:'四川话'}],
 speech:{profile:'office',roles:{},profiles:{office:{environment:null,topics:['客户'],avoid:[]},home:{environment:'家',topics:['家务'],avoid:[]},friends:{environment:'',topics:[],avoid:[]}}},environments:{office:'公司',home:'家',friends:''}});
test('failed save keeps draft and switching scene preserves other profiles',async()=>{
 const m=new ui.SpeechSettingsModel(async(path,body)=>{if(body)throw Error('conflict');return cfg();});
 await m.load();m.draft.speech.profiles.office.environment='改过的公司';m.draft.speech.profile='home';
 await assert.rejects(m.save(),/conflict/);
 assert.equal(m.draft.speech.profiles.office.environment,'改过的公司');
 assert.deepEqual(m.draft.speech.profiles.home.topics,['家务']);assert.equal(m.config.version,4);
});
test('stale page or settings response cannot overwrite newer selection',async()=>{
 const resolvers=[];const m=new ui.SpeechSettingsModel(()=>new Promise(r=>resolvers.push(r)));
 const a=m.load(),b=m.load();const c=cfg();c.version=6;resolvers[1](c);await b;resolvers[0](cfg());await a;
 assert.equal(m.config.version,6);
 const p=m.memories(0),q=m.memories(20);resolvers[3]({offset:20,items:[]});await q;resolvers[2]({offset:0,items:[]});await p;
 assert.equal(m.page.offset,20);
});
test('saved config version and selected role audition are sent',async()=>{
 const calls=[];const m=new ui.SpeechSettingsModel(async(p,b)=>{calls.push([p,b]);return cfg();});
 await m.load();await m.save();await m.audition('sad');
 assert.equal(calls[1][1].expected_version,4);assert.deepEqual(calls[2],['/role/try',{role:'sad'}]);
});
test('an old save response cannot replace a newly opened settings view',async()=>{
 let resolveSave;const m=new ui.SpeechSettingsModel(async(p,b)=>b?new Promise(r=>{resolveSave=r;}):cfg());
 await m.load();const saving=m.save();await m.load();m.draft.speech.profile='home';resolveSave(cfg());await saving;
 assert.equal(m.draft.speech.profile,'home');
});
test('memory and explanation markup escape stored text',()=>{
 const html=ui.memoriesHtml({items:[{memory_id:'m1',statement:'<img>',status:'known',version:2}],offset:0,total:1},'办公室');
 assert.match(html,/&lt;img&gt;/);assert.doesNotMatch(html,/style=|<img>/);
 assert.match(ui.explanationText({attention:{focus:'客户改交期',reason_codes:['latest_speech']},context:{evidence:[{id:'a',text:'改需求'}]}}),/客户改交期/);
});
test('memory evidence shows current conflicts, source excerpts, windows and historical version',()=>{
 const row={statement:'习惯',status:'known',version:2,manual:true,kind:'habit',latest_evidence:{observed_windows:7,observed_status:'review',
  evidence_snippets:['新冲突'],evidence_refs:[{window_ms:1000,version:3,relation:'contradict',refs:[{c:'speech',id:'s1',excerpt:'<旧结论不对>'}]}]}};
 assert.match(ui.memoriesHtml({items:[row],total:1,offset:0},'办公室'),/7 个窗口/);
 const html=ui.memoryEvidenceHtml(row);
 assert.match(html,/新冲突/);assert.match(html,/&lt;旧结论不对&gt;/);assert.match(html,/第 3 版/);assert.match(html,/手动结论保留/);
});
