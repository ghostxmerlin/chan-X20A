import test from 'node:test';
import assert from 'node:assert/strict';
function fakeDoc(){
 const elements=new Map();
 const make=()=>({textContent:'',value:'',disabled:false,dataset:{},children:[],className:'',
   addEventListener(e,fn){this['on'+e]=fn;},append(...c){this.children.push(...c);},replaceChildren(){this.children=[];}});
 return {getElementById(id){if(!elements.has(id))elements.set(id,make());return elements.get(id);},createElement:()=>make()};
}
globalThis.document=fakeDoc();
const { M12LibraryPanel, m12ErrorText } = await import('./public/m12-ui.js');
const state=(boot='a',m={})=>({boot,telemetry:{m12:{protocol:'m12d',online:true,...m}},m12:{library:[{name:'person-a',samples:5}],commands:[]}});
test('library: one automatic list read per boot, other requests only on click',async()=>{
 const doc=fakeDoc(),calls=[];const p=new M12LibraryPanel(doc,async b=>{calls.push(b);return {accepted:true,op:b.action.slice(4),id:calls.length};});
 p.render(state(),true);await new Promise(r=>setTimeout(r,0));p.render(state(),true);
 assert.deepEqual(calls,[{action:'m12_faces',boot:'a'}]);
 doc.getElementById('m12-name').value='  person-b ';await doc.getElementById('m12-learn').onclick();
 assert.deepEqual(calls.at(-1),{action:'m12_learn',boot:'a',name:'person-b'});
 doc.getElementById('m12-name').value='';await doc.getElementById('m12-learn').onclick();
 assert.equal(calls.length,2);assert.match(doc.getElementById('m12-library-note').textContent,/名字/);
 await doc.getElementById('m12-library-list').onclick({target:{dataset:{forget:'person-a'}}});
 assert.deepEqual(calls.at(-1),{action:'m12_forget',boot:'a',name:'person-a'});
 p.render(state('b'),true);await new Promise(r=>setTimeout(r,0));assert.deepEqual(calls.at(-1),{action:'m12_faces',boot:'b'});   // new boot: read again
});
test('library: offline disables everything and nothing is sent',async()=>{
 const doc=fakeDoc(),calls=[];const p=new M12LibraryPanel(doc,async b=>{calls.push(b);return {};});
 p.render(state('a',{online:false}),true);await doc.getElementById('m12-who').onclick();
 assert.equal(calls.length,0);assert.equal(doc.getElementById('m12-learn').disabled,true);
});
test('library: results and error codes become readable text',async()=>{
 const doc=fakeDoc();const p=new M12LibraryPanel(doc,async b=>({accepted:true,op:'learn',id:7}));
 p.render(state(),true);await new Promise(r=>setTimeout(r,0));
 doc.getElementById('m12-name').value='person-b';await doc.getElementById('m12-learn').onclick();
 const s=state();s.m12.commands=[{id:7,op:'learn',state:'error',err:'conflict',name:'person-a'}];p.render(s,true);
 assert.match(doc.getElementById('m12-library-note').textContent,/学习失败：和库里的「person-a」太像/);
 assert.match(m12ErrorText({err:'noface'}),/没有可用的脸/);assert.match(m12ErrorText({err:'full'}),/16/);assert.match(m12ErrorText({err:'zzz'}),/错误 zzz/);
});
