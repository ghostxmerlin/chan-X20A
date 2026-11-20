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

test('planSummary: minutes shared by two batches count once (plan.minutes)', ()=>{
 const p1={frames:3,looks:0,eye_minutes:2,scenes:2,minutes:[60000,120000],segments:[]};
 const p2={frames:2,looks:0,eye_minutes:1,scenes:2,minutes:[120000,180000],segments:[]};
 assert.equal(ui.planSummary([p1,p2]),'将删除 5 张照片、3 分钟的分钟描述和情景，删除后无法恢复。');
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

// ---- the delete state machine, with a scripted post ----
function script(handlers){
 const calls=[];
 const post=async(path,body)=>{calls.push([path,body]);return handlers(path,body,calls.filter(c=>c[0]===path).length);};
 return {post,calls};
}
const plan=(ids,fp)=>({status:200,json:{fp,frames:ids.length,looks:0,eye_minutes:0,scenes:0,segments:[]}});
const ids1001=Array.from({length:1001},(_,i)=>'i'+i);
const deletes=calls=>calls.filter(c=>c[0]==='/gallery/delete');

test('deleteFlow (a): all batches ok, counts per batch, all deleted', async ()=>{
 const asked=[];
 const {post,calls}=script((p,b)=>p==='/gallery/plan'?plan(b.ids,'fp'+b.ids[0]):{status:200,json:{frames:b.ids.length,looks:1}});
 const r=await ui.deleteFlow(ids1001,{post,confirm:async t=>{asked.push(t);return true;}});
 assert.equal(asked.length,1);assert.match(asked[0],/将删除 1001 张照片/);
 assert.deepEqual(r.deleted,ids1001);assert.equal(r.stopped,null);
 assert.equal(r.counts.reduce((n,o)=>n+o.frames,0),1001);
 assert.deepEqual(deletes(calls).map(c=>c[1].fp),['fpi0','fpi500','fpi1000']);
});

test('deleteFlow (b): stale on batch 2 re-plans only the rest and asks again', async ()=>{
 const asked=[];let stale=true;
 const {post,calls}=script((p,b)=>{
  if(p==='/gallery/plan')return plan(b.ids,'fp'+b.ids[0]+(stale?'-old':''));
  if(b.ids[0]==='i500'&&stale){stale=false;return {status:409,json:{code:'stale',error:'the delete plan changed'}};}
  return {status:200,json:{frames:b.ids.length}};
 });
 const ids=ids1001.slice(0,1000);
 const r=await ui.deleteFlow(ids,{post,confirm:async t=>{asked.push(t);return true;}});
 assert.equal(asked.length,2);assert.match(asked[1],/变化/);assert.match(asked[1],/将删除 500 张照片/);
 const plans=calls.filter(c=>c[0]==='/gallery/plan');
 assert.equal(plans.length,3);assert.equal(plans[2][1].ids[0],'i500');   // third plan = only batch 2
 assert.deepEqual(r.deleted,ids);assert.equal(r.stopped,null);
 assert.equal(deletes(calls).at(-1)[1].fp,'fpi500');
});

test('deleteFlow (c): a 503 on batch 2 stops, only batch 1 counted deleted', async ()=>{
 const {post}=script((p,b)=>p==='/gallery/plan'?plan(b.ids,'f'+b.ids[0]):b.ids[0]==='i500'?{status:503,json:{error:'busy'}}:{status:200,json:{frames:b.ids.length}});
 const ids=ids1001.slice(0,1000);
 const r=await ui.deleteFlow(ids,{post,confirm:async()=>true});
 assert.deepEqual(r.deleted,ids.slice(0,500));
 assert.deepEqual(r.stopped.remaining,ids.slice(500));
 assert.equal(r.stopped.error,'busy');assert.equal(r.counts.length,1);
});

test('deleteFlow (d): 409 not_maintenance on the first batch deletes nothing', async ()=>{
 const {post}=script((p,b)=>p==='/gallery/plan'?plan(b.ids,'f'):{status:409,json:{code:'not_maintenance',error:'机器人还连着，请先关机'}});
 const r=await ui.deleteFlow(['a','b'],{post,confirm:async()=>true});
 assert.deepEqual(r.deleted,[]);assert.equal(r.stopped.code,'not_maintenance');assert.deepEqual(r.stopped.remaining,['a','b']);
});

test('deleteFlow (e): declining the confirmation sends no delete', async ()=>{
 const {post,calls}=script((p,b)=>plan(b.ids,'f'));
 const r=await ui.deleteFlow(['a'],{post,confirm:async()=>false});
 assert.equal(deletes(calls).length,0);assert.deepEqual(r.deleted,[]);assert.equal(r.cancelled,true);
});

test('deleteFlow (f): post throwing on batch 2 resolves with batch 1 deleted and the rest remaining', async ()=>{
 const ids=ids1001.slice(0,1000);
 const post=async(p,b)=>{if(p==='/gallery/plan')return plan(b.ids,'f'+b.ids[0]);if(b.ids[0]==='i500')throw new Error('network down');return {status:200,json:{frames:b.ids.length}};};
 const r=await ui.deleteFlow(ids,{post,confirm:async()=>true});
 assert.deepEqual(r.deleted,ids.slice(0,500));
 assert.match(r.stopped.error,/network down/);assert.deepEqual(r.stopped.remaining,ids.slice(500));
});

test('deleteFlow (g): confirm gets the count; a failed re-plan after a deletion reports what was deleted', async ()=>{
 const counts=[];let stale=true;
 const ids=ids1001.slice(0,1000);
 const post=async(p,b)=>{
  if(p==='/gallery/plan'){if(!stale&&b.ids[0]==='i500')return {status:503,json:{error:'busy'}};return plan(b.ids,'f'+b.ids[0]);}
  if(b.ids[0]==='i500'&&stale){stale=false;return {status:409,json:{code:'stale',error:'x'}};}
  return {status:200,json:{frames:b.ids.length}};
 };
 const r=await ui.deleteFlow(ids,{post,confirm:async(t,n)=>{counts.push(n);return true;}});
 assert.deepEqual(counts,[1000]);
 assert.deepEqual(r.deleted,ids.slice(0,500));assert.equal(r.stopped.phase,'plan');assert.deepEqual(r.stopped.remaining,ids.slice(500));
});

test('lookRows: 看一眼 and 实时识别 (vision_looks kind scene) are shown apart', ()=>{
 const t=Date.UTC(2026,10,20,2,5,7);
 const r=ui.lookRows([{at:t,kind:'look',text:'一杯茶'},{at:t+1000,kind:'scene',text:'坐在床边'},{at:null,text:'旧行'}]);
 assert.deepEqual(r.look,['10:05:07  一杯茶','旧行']);
 assert.deepEqual(r.live,['10:05:08  坐在床边']);
 assert.deepEqual(ui.lookRows(undefined),{look:[],live:[]});
});

// ---- the delete job (review C2): 202 {job}, then the page waits on GET /gallery/job ----
const accepted=(b)=>({status:202,json:{job:'j'+b.ids[0]}});

test('deleteFlow (h): a 202 waits on the job; its counts are the batch result', async ()=>{
 const waited=[];
 const {post,calls}=script((p,b)=>p==='/gallery/plan'?plan(b.ids,'f'+b.ids[0]):accepted(b));
 const wait=async(job,info)=>{waited.push([job,info]);return {state:'done',counts:{frames:job==='ji0'?500:1}};};
 const r=await ui.deleteFlow(ids1001.slice(0,501),{post,wait,confirm:async()=>true});
 assert.deepEqual(waited,[['ji0',{batch:1,of:2}],['ji500',{batch:2,of:2}]]);
 assert.equal(r.stopped,null);assert.equal(r.deleted.length,501);
 assert.deepEqual(r.counts.map(c=>c.frames),[500,1]);
 assert.equal(deletes(calls).length,2);
});

test('deleteFlow (i): a failed job stops with what it did; later batches are not sent', async ()=>{
 const {post,calls}=script((p,b)=>p==='/gallery/plan'?plan(b.ids,'f'+b.ids[0]):accepted(b));
 const wait=async job=>job==='ji0'?{state:'done',counts:{frames:500}}:{state:'failed',error:'OSError',counts:{log_rows:3}};
 const ids=ids1001;
 const r=await ui.deleteFlow(ids,{post,wait,confirm:async()=>true});
 assert.deepEqual(r.deleted,ids.slice(0,500));
 assert.equal(r.stopped.code,'failed');assert.deepEqual(r.stopped.partial,{log_rows:3});
 assert.deepEqual(r.stopped.remaining,ids.slice(500));
 assert.equal(deletes(calls).length,2);
});

test('deleteFlow (j): a lost job or a lost delete answer is unknown, never "not deleted"', async ()=>{
 const {post}=script((p,b)=>p==='/gallery/plan'?plan(b.ids,'f'):accepted(b));
 const r=await ui.deleteFlow(['a'],{post,wait:async()=>({state:'unknown',error:'连接出错'}),confirm:async()=>true});
 assert.equal(r.stopped.code,'unknown');assert.deepEqual(r.deleted,[]);
 const t=ui.stopText(r.stopped,r.counts,1);
 assert.match(t.text,/可能仍在进行，请稍后刷新/);assert.doesNotMatch(t.text,/没有删/);
 for(const res of [{status:0,json:{error:'连接出错：socket hang up'}},{status:503,json:{error:'情景服务连接中'}}]){
  const {post:p2}=script((p,b)=>p==='/gallery/plan'?plan(b.ids,'f'):res);
  const r2=await ui.deleteFlow(['a'],{post:p2,wait:async()=>{throw new Error('not called');},confirm:async()=>true});
  assert.equal(r2.stopped.code,'unknown',String(res.status));
  assert.doesNotMatch(ui.stopText(r2.stopped,r2.counts,1).text,/没有删/);
 }
});

test('stopText: refused before the start says nothing was deleted; a failed job shows what it did', ()=>{
 const refused=ui.stopText({code:'not_maintenance',error:'机器人还连着，请先关机',remaining:['a'],phase:null},[],1);
 assert.equal(refused.title,'删除中断');assert.match(refused.text,/机器人还连着，请先关机。一张也没有删/);
 const backlog=ui.stopText({code:'spool_backlog',error:'先等日志上传完再删',remaining:['a']},[{frames:500}],501);
 assert.match(backlog.text,/已删除 500 张照片（共选了 501 张）；其余没有删/);
 const failed=ui.stopText({code:'failed',error:'OSError',partial:{log_rows:3,looks:2,frames:0},remaining:['b']},[],1);
 assert.match(failed.text,/OSError/);assert.match(failed.text,/2 条看一眼/);assert.match(failed.text,/刷新/);
 assert.equal(ui.stopText({code:null,error:'x',phase:'plan',remaining:['a']},[],1).title,'无法删除');
});

test('waitJob: polls until the job ends, reports steps, rides out a few poll errors', async ()=>{
 const seen=[],steps=[];let n=0;
 const get=async p=>{seen.push(p);n++;
  if(n===2)return {status:0,json:null};
  if(n<4)return {status:200,json:{state:'running',step:n===1?'bodies':'spool'}};
  return {status:200,json:{state:'done',step:'done',counts:{frames:3}}};};
 const j=await ui.waitJob('0123456789ab',{get,sleep:async()=>{},onStep:s=>steps.push(s.step)});
 assert.deepEqual(j,{state:'done',step:'done',counts:{frames:3}});
 assert.equal(seen[0],'/gallery/job?id=0123456789ab');assert.deepEqual(steps,['bodies','spool']);
 const down=await ui.waitJob('x',{get:async()=>{throw new Error('down');},sleep:async()=>{},tries:3});
 assert.equal(down.state,'unknown');assert.match(down.error,/可能仍在进行/);
 const gone=await ui.waitJob('x',{get:async()=>({status:404,json:{error:'这次删除已不在记录里'}}),sleep:async()=>{}});
 assert.equal(gone.state,'unknown');
 assert.equal(ui.STEP_ZH.spool,'改写日志缓存');
});

test('detailError: a photo deleted meanwhile says so', ()=>{
 assert.equal(ui.detailError(404,'no such frame'),'这张已被删除');
 assert.equal(ui.detailError(503,'MongoError'),'读不到这张：503 MongoError');
});

test('the job path and its id are whitelisted; page POSTs outwait the proxy', async ()=>{
 const {MindProxy}=await import(new URL('./mind.mjs',import.meta.url));
 assert.equal(MindProxy.check('/gallery/job?id=0123456789ab'),'/gallery/job?id=0123456789ab');
 assert.equal(MindProxy.check('/gallery/job?id=../x'),null);
 assert.equal(MindProxy.check('/gallery/job?id=0123456789abc'),null);
 const server=readFileSync(new URL('./server.mjs',import.meta.url),'utf8');
 assert.ok(server.includes(`'/gallery/job'`));
 const page=readFileSync(new URL('./public/gallery-ui.js',import.meta.url),'utf8');
 const post=Number(page.match(/POST_TIMEOUT_MS=(\d+)/)?.[1]);
 const proxy=Number(readFileSync(new URL('./mind.mjs',import.meta.url),'utf8').match(/method:'POST',body:bodyText,timeout:(\d+)/)?.[1]);
 assert.ok(post>=proxy&&proxy>0,`${post} >= ${proxy}`);
});
