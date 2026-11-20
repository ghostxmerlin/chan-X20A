import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,existsSync} from 'node:fs';
const src=new URL('./public/gallery-ui.js',import.meta.url);
const ui=existsSync(src)?await import('data:text/javascript;base64,'+readFileSync(src).toString('base64')):{};
const M=60000;
const f=(id,at)=>({id,at,session:'s',snap:false,look:false,eye:false,scene:false});

test('groupByMinute: one group per minute, in order', ()=>{
 const t=Date.UTC(2026,10,20,12,0,0);
 const g=ui.groupByMinute([f('a',t+1000),f('b',t+30000),f('c',t+M+5),f('d',t+3*M)]);
 assert.deepEqual(g.map(x=>[x.minute,x.items.map(i=>i.id)]),[[t,['a','b']],[t+M,['c']],[t+3*M,['d']]]);
 assert.deepEqual(ui.groupByMinute([]),[]);
});

test('selectRange: inclusive, either direction, unknown id gives the known one', ()=>{
 const fs=['a','b','c','d','e'].map((id,i)=>f(id,i*1000));
 assert.deepEqual([...ui.selectRange(fs,'b','d')],['b','c','d']);
 assert.deepEqual([...ui.selectRange(fs,'d','b')],['b','c','d']);
 assert.deepEqual([...ui.selectRange(fs,'c','c')],['c']);
 assert.deepEqual([...ui.selectRange(fs,'zz','c')],['c']);
 assert.equal(ui.selectRange(fs,'x','y').size,0);
});

test('batches: 1001 ids -> 500, 500, 1', ()=>{
 const ids=Array.from({length:1001},(_,i)=>'i'+i);
 assert.deepEqual(ui.batches(ids).map(b=>b.length),[500,500,1]);
 assert.deepEqual(ui.batches(['a','b','c'],2),[['a','b'],['c']]);
 assert.deepEqual(ui.batches([]),[]);
});

test('planSummary: counts over batches, segment titles once', ()=>{
 const p1={fp:'x',frames:30,looks:1,eye_minutes:3,scenes:3,segments:[{start:1,end:2,title:'夜间调整 Hello'}]};
 const p2={fp:'y',frames:7,looks:1,eye_minutes:2,scenes:1,segments:[{start:1,end:2,title:'夜间调整 Hello'},{start:5,end:9,title:null}]};
 assert.equal(ui.planSummary([p1,p2]),'将删除 37 张照片、2 条看一眼、5 分钟的分钟描述和情景、2 段段落总结（夜间调整 Hello、未命名），删除后无法恢复。');
 assert.equal(ui.planSummary([{frames:1,looks:0,eye_minutes:0,scenes:0,segments:[]}]),'将删除 1 张照片，删除后无法恢复。');
});

test('maintenanceHtml: three states', ()=>{
 assert.match(ui.maintenanceHtml({on:false,robot_connected:true,can_delete:false,why_not:'还没开始维护'}),/未在维护/);
 const busy=ui.maintenanceHtml({on:true,robot_connected:true,can_delete:false,why_not:'机器人还连着，请先关机'});
 assert.match(busy,/维护中/);assert.match(busy,/机器人还连着，请先关机/);
 const ok=ui.maintenanceHtml({on:true,robot_connected:false,can_delete:true,why_not:null});
 assert.match(ok,/维护中/);assert.match(ok,/可以删除/);
 assert.match(ui.maintenanceHtml(null),/读不到/);
});

test('the mind proxy lets the gallery paths through, ids strictly', async ()=>{
 const {MindProxy}=await import(new URL('./mind.mjs',import.meta.url));
 assert.equal(MindProxy.check('/gallery/images?ids=a1,b2'),'/gallery/images?ids=a1,b2');
 assert.equal(MindProxy.check('/gallery/images?ids=../x'),null);
 assert.equal(MindProxy.check('/gallery/images?ids='),null);
 assert.equal(MindProxy.check('/gallery/images?ids='+'a'.repeat(1601)),null);
 assert.equal(MindProxy.check('/gallery/days'),'/gallery/days');
 assert.equal(MindProxy.check('/gallery/hours?day=2026-11-20'),'/gallery/hours?day=2026-11-20');
 assert.equal(MindProxy.check('/gallery/hours?day=x'),null);
 assert.equal(MindProxy.check('/gallery/frames?from=1000&to=2000'),'/gallery/frames?from=1000&to=2000');
 assert.equal(MindProxy.check('/gallery/frame?id=0123456789abcdef01234567'),'/gallery/frame?id=0123456789abcdef01234567');
 assert.equal(MindProxy.check('/gallery/frame?id=../x'),null);
 assert.equal(MindProxy.check('/gallery/maintenance'),'/gallery/maintenance');
 assert.equal(MindProxy.check('/log?ids=a'),null);
});

test('the gallery writes are whitelisted in both places', async ()=>{
 const server=readFileSync(new URL('./server.mjs',import.meta.url),'utf8');
 const mindSrc=readFileSync(new URL('./mind.mjs',import.meta.url),'utf8');
 for(const p of ['/gallery/maintenance','/gallery/plan','/gallery/delete']){
  assert.ok(server.includes(`'${p}'`)&&mindSrc.includes(`'${p}'`),p);
 }
 const {MindProxy}=await import(new URL('./mind.mjs',import.meta.url));
 for(const p of ['/gallery/plan','/gallery/delete','/gallery/maintenance']){
  const m=new MindProxy({ssh:'x',token:'t',requestFn:async()=>({status:200,body:'{}'}),spawnFn:()=>({on(){}})});
  assert.equal((await m.post(p,'{}')).status,200,p);
 }
 for(const p of ['/gallery.html','/gallery-ui.js','/gallery.css'])assert.ok(server.includes(`'${p}'`),p);
 assert.match(readFileSync(new URL('./public/index.html',import.meta.url),'utf8'),/href="\/gallery.html"[^>]*>图库</);
});
