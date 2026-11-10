import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {EventEmitter} from 'node:events';
import {MindProxy} from './mind.mjs';
import {Monitor} from './core.mjs';
import {createMonitorServer} from './server.mjs';
const ui=await import('data:text/javascript;base64,'+readFileSync(new URL('./public/mind-ui.js',import.meta.url)).toString('base64'));

function fakeSsh(log=[]){return (cmd,args)=>{const child=new EventEmitter();child.killed=false;child.kill=()=>{child.killed=true;child.emit('exit',0);};log.push({cmd,args,child});return child;};}
const okFetch=seen=>async(u,o)=>{seen.push([u,o.headers.Authorization]);return {status:200,text:async()=>'{"ok":1}'};};

test('proxy adds token and only forwards whitelisted paths', async()=>{
 const seen=[],spawned=[],proxy=new MindProxy({ssh:'h',token:'k',spawnFn:fakeSsh(spawned),fetchFn:okFetch(seen)});
 assert.equal((await proxy.get('/timeline?from=1&to=2')).status,200);
 assert.deepEqual(seen[0],['http://127.0.0.1:52741/timeline?from=1&to=2','Bearer k']);
 assert.equal(spawned[0].cmd,'ssh');
 assert.ok(spawned[0].args.includes('127.0.0.1:52741:127.0.0.1:52740')&&spawned[0].args.includes('BatchMode=yes')&&spawned[0].args.at(-1)==='h');
 assert.equal((await proxy.get('/timeline?from=1&evil=x')).status,400);
 assert.equal((await proxy.get('/timeline?from=1x')).status,400);
 assert.equal((await proxy.get('/../etc')).status,400);
 assert.equal((await proxy.get('/minute?t=5&version=2')).status,200);
 assert.equal(seen.length,2);
});
test('proxy restarts ssh after it exits, at most every 5 s', async()=>{
 let now=0;const spawned=[],proxy=new MindProxy({ssh:'h',token:'k',spawnFn:fakeSsh(spawned),fetchFn:okFetch([]),now:()=>now});
 await proxy.get('/state');assert.equal(spawned.length,1);
 spawned[0].child.emit('exit',255);now=2000;
 assert.equal((await proxy.get('/state')).status,503);assert.equal(spawned.length,1);
 now=6000;assert.equal((await proxy.get('/state')).status,200);assert.equal(spawned.length,2);
 proxy.stop();assert.equal(spawned[1].child.killed,true);
});
test('proxy answers 503 while the tunnel is not ready', async()=>{
 const proxy=new MindProxy({ssh:'h',token:'k',spawnFn:fakeSsh(),fetchFn:async()=>{throw new Error('ECONNREFUSED');}});
 const r=await proxy.get('/state');assert.equal(r.status,503);assert.match(r.body,/情景服务连接中/);
});
test('proxy without ssh or token config answers 503', async()=>{
 assert.equal((await new MindProxy({ssh:'',token:''}).get('/state')).status,503);
});
test('server routes /api/mind/* to the proxy and serves mind.html', async t=>{
 const calls=[],mind={get:async p=>{calls.push(p);return {status:200,body:'{"lane":"live"}'};},stop(){}};
 const server=createMonitorServer(new Monitor({binding:{address:'http://127.0.0.1:8080',token:'b'.repeat(32)}}),undefined,mind);
 await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
 const url=`http://127.0.0.1:${server.address().port}`;
 const r=await fetch(url+'/api/mind/timeline?from=1&to=2');
 assert.equal(r.status,200);assert.equal((await r.json()).lane,'live');assert.deepEqual(calls,['/timeline?from=1&to=2']);
 assert.equal((await fetch(url+'/api/mind/other')).status,404);
 const page=await fetch(url+'/mind.html');assert.equal(page.status,200);assert.match(await page.text(),/mind-ui\.js/);
 assert.equal((await fetch(url+'/mind-ui.js')).status,200);
 assert.equal((await fetch(url+'/mind.css')).status,200);
});
test('owner state colours and labels are fixed', ()=>{
 assert.equal(ui.stateColour('seen'),'#2e9d4f');assert.equal(ui.stateColour('likely_absent'),'#e8912d');
 assert.equal(ui.stateColour('sensors_off'),'#555555');assert.equal(ui.stateLabel('just_left'),'刚离开');
});
test('coverage bar text and speech line', ()=>{
 assert.equal(ui.coverageTitle({hearing_s:60,vision_s:15,m12_s:0,estimated:true}),'听觉 60 秒 · 视觉 15 秒 · M12 0 秒（估计）');
 assert.equal(ui.whoLabel({kind:'unsure',best:'me',score:0.55}),'不确定（最像 me 0.55）');
 assert.equal(ui.whoLabel({kind:'known',name:'me'}),'认出：me');
 assert.equal(ui.escapeHtml('<b>&'),'&lt;b&gt;&amp;');
});
test('page markup carries no style attributes (CSP)', ()=>{
 const row=ui.detailHtml({version:1,versions:[1],fingerprint:'f',package:{speech:[],tingwu_only:[],self_speech:[],sounds:[],faces:[],persons:{seconds:0,ids:0},warnings:[]},
  strong:{owner:{state:'seen',why:'x',codes:[]},owner_voice:{},owner_face:{},others:{known:[],unknown_faces:0,unknown_voices:false},coverage:{hearing_s:1,vision_s:0,m12_s:0},time:{label:'l',work_hours:false},place:{label:null},contradictions:[]}});
 assert.doesNotMatch(row,/style="/);assert.match(row,/data-c="#2e9d4f"/);
 assert.doesNotMatch(readFileSync(new URL('./public/mind.html',import.meta.url),'utf8'),/<script>|style="/);
});
