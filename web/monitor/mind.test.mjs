import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {EventEmitter} from 'node:events';
import {MindProxy} from './mind.mjs';
import {Monitor} from './core.mjs';
import {createMonitorServer} from './server.mjs';
const ui=await import('data:text/javascript;base64,'+readFileSync(new URL('./public/mind-ui.js',import.meta.url)).toString('base64'));

function fakeSsh(log=[]){return (cmd,args)=>{const child=new EventEmitter();child.killed=false;child.kill=()=>{child.killed=true;child.emit('exit',0);};log.push({cmd,args,child});return child;};}
const okFetch=seen=>async(sock,p,h)=>{seen.push([sock,p,h.Authorization]);return {status:200,body:'{"ok":1}'};};
const SOCK='/tmp/x/mind.sock';

test('proxy adds token and only forwards whitelisted paths', async()=>{
 const seen=[],spawned=[],proxy=new MindProxy({ssh:'h',token:'k',socketPath:SOCK,prepareFn:()=>{},spawnFn:fakeSsh(spawned),requestFn:okFetch(seen)});
 assert.equal((await proxy.get('/timeline?from=1&to=2')).status,200);
 assert.deepEqual(seen[0],[SOCK,'/timeline?from=1&to=2','Bearer k']);
 assert.equal(spawned[0].cmd,'ssh');
 assert.ok(spawned[0].args.includes(SOCK+':127.0.0.1:52740')&&spawned[0].args.includes('StreamLocalBindUnlink=yes')&&spawned[0].args.includes('BatchMode=yes')&&spawned[0].args.at(-1)==='h');
 assert.ok(!spawned[0].args.some(a=>/52741/.test(a)));   // no loopback TCP port another process could squat
 assert.equal((await proxy.get('/timeline?from=1&evil=x')).status,400);
 assert.equal((await proxy.get('/timeline?from=1x')).status,400);
 assert.equal((await proxy.get('/../etc')).status,400);
 assert.equal((await proxy.get('/minute?t=5&version=2')).status,200);
 assert.equal(seen.length,2);
});
test('proxy restarts ssh after it exits, at most every 5 s', async()=>{
 let now=0;const spawned=[],proxy=new MindProxy({ssh:'h',token:'k',socketPath:SOCK,prepareFn:()=>{},spawnFn:fakeSsh(spawned),requestFn:okFetch([]),now:()=>now});
 await proxy.get('/state');assert.equal(spawned.length,1);
 spawned[0].child.emit('exit',255);now=2000;
 assert.equal((await proxy.get('/state')).status,503);assert.equal(spawned.length,1);
 now=6000;assert.equal((await proxy.get('/state')).status,200);assert.equal(spawned.length,2);
 proxy.stop();assert.equal(spawned[1].child.killed,true);
});
test('proxy answers 503 while the tunnel is not ready', async()=>{
 const proxy=new MindProxy({ssh:'h',token:'k',socketPath:SOCK,prepareFn:()=>{},spawnFn:fakeSsh(),requestFn:async()=>{throw new Error('ENOENT');}});
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
test('socket directory is private (0700)', async()=>{
 const fs=await import('node:fs'),os=await import('node:os'),path=await import('node:path');
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'mindt-')),sock=path.join(dir,'sub','mind.sock');
 new MindProxy({ssh:'h',token:'k',socketPath:sock,spawnFn:fakeSsh(),requestFn:okFetch([])}).tunnel();
 assert.equal(fs.statSync(path.dirname(sock)).mode&0o777,0o700);
});

test('proxy forwards whitelisted POSTs with the token and rejects others', async()=>{
 const seen=[],proxy=new MindProxy({ssh:'h',token:'k',socketPath:SOCK,prepareFn:()=>{},spawnFn:fakeSsh(),
  requestFn:async(sock,p,h,o={})=>{seen.push([p,h.Authorization,o.method,o.body]);return {status:200,body:'{}'};}});
 assert.equal((await proxy.post('/mark','{"kind":"arrive"}')).status,200);
 assert.deepEqual(seen[0],['/mark','Bearer k','POST','{"kind":"arrive"}']);
 assert.equal((await proxy.post('/state','{}')).status,400);
 assert.equal((await proxy.post('/place','{"label":"办公室"}')).status,200);
 assert.equal((await proxy.post('/switch','{"enabled":false}')).status,200);
 assert.equal((await proxy.post('/switches','{"name":"tingwu","on":true}')).status,200);
 assert.equal((await proxy.get('/switches')).status,200);
 assert.equal((await proxy.get('/frames/latest')).status,200);
 assert.equal((await proxy.get('/eye/latest')).status,200);
 assert.equal((await proxy.get('/eye?from=1&to=2')).status,200);
 assert.equal((await proxy.post('/switches','{"name":"eye","on":true}')).status,200);
 assert.equal((await proxy.post('/mark','x'.repeat(16385))).status,413);
 assert.equal((await proxy.get('/scene?t=5&lane=rerun')).status,200);
 assert.equal((await proxy.get('/scene?t=5&lane=evil')).status,400);
 assert.equal((await proxy.get('/stats?days=7')).status,200);
});
test('server POST /api/mind/* requires same origin and JSON', async t=>{
 const calls=[],mind={get:async()=>({status:200,body:'{}'}),post:async(p,b)=>{calls.push([p,b]);return {status:200,body:'{"ok":true}'};},stop(){}};
 const server=createMonitorServer(new Monitor({binding:{address:'http://127.0.0.1:8080',token:'b'.repeat(32)}}),undefined,mind);
 await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
 const url=`http://127.0.0.1:${server.address().port}`,origin=url;
 assert.equal((await fetch(url+'/api/mind/mark',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).status,403);
 assert.equal((await fetch(url+'/api/mind/mark',{method:'POST',headers:{'Content-Type':'text/plain',Origin:origin},body:'{}'})).status,415);
 const ok=await fetch(url+'/api/mind/mark',{method:'POST',headers:{'Content-Type':'application/json',Origin:origin},body:'{"kind":"arrive"}'});
 assert.equal(ok.status,200);assert.deepEqual(calls,[['/mark','{"kind":"arrive"}']]);
 assert.equal((await fetch(url+'/api/mind/other',{method:'POST',headers:{'Content-Type':'application/json',Origin:origin},body:'{}'})).status,404);
});
test('entries grouped in fixed dimension order', ()=>{
 assert.deepEqual(Object.keys(ui.groupEntries([{dimension:'topic'},{dimension:'people'},{dimension:'weird'}])),['people','topic','other']);
 assert.equal(ui.basisLabel('inferred'),'推断');
});
test('source label', ()=>{
 const T=Date.UTC(2026,10,10,8,52);
 assert.match(ui.sourceLabel({source:'carried',carried_from:T}),/^沿用自 \d\d:\d\d$/);
 assert.equal(ui.sourceLabel({source:'model'}),'模型');assert.equal(ui.sourceLabel({source:'rules_only'}),'仅规则');
});
test('source label shows 修订', ()=>{
 assert.equal(ui.sourceLabel({source:'model',reason:'revised',version:2}),'模型 · 修订版（第 2 版）');
 assert.equal(ui.sourceLabel({source:'model',reason:'changed',version:1}),'模型');
});
test('settings form round trip keeps lists and numbers', ()=>{
 const s={tz:'Asia/Shanghai',owner:{display:'主人',aliases:['Kira','Dave'],voice_names:['me','主人'],face_names:['me']},people:[{display:'同事A',voice_names:['a'],face_names:[]}],
  work:{days:[1,2,3,4,5],start:'09:00',end:'19:00'},thresholds:{just_left_min:2},self_facts:'我',place:{default:'办公室'},persona:'',
  sources:{speech_text:true,tingwu:true,sounds:false,vision:true},fusion:{enabled:true,model:'gpt-6-luna',prompt:'v1',max_age_min:60,review_after_min:[5,10,20,30]},budget_usd_per_day:20};
 assert.deepEqual(ui.settingsFromForm(ui.formFromSettings(s),s),s);
 assert.equal(ui.formFromSettings(s).owner_aliases,'Kira,Dave');
 assert.deepEqual(ui.settingsFromForm({...ui.formFromSettings(s),owner_aliases:'Kira，无限'},s).owner.aliases,['Kira','无限']);
});
test('new markup carries no style attributes (CSP)', ()=>{
 const scene={minute:0,source:'model',scene:{who_am_i:{text:'a',confidence:'high'},where_am_i:{text:'b',confidence:'mid'},people_doing:{text:'c',confidence:'low'},owner_view:'present',
  entries:[{key:'people.owner',dimension:'people',text:'在',basis:'seen',confidence:'high',evidence:['v1']}],changes:[],summary:'s',want_to_know:''},checks:{}};
 const html=ui.nowHtml({scene,strong:{owner:{state:'seen',why:'w'},coverage:{hearing_s:60,vision_s:60,m12_s:60}},segment:null})+ui.segmentsHtml([{start:0,end:null,cls:'present',summary:{title:'t',text:'x'}}])+ui.settingsHtml(ui.formFromSettings({owner:{display:'a',voice_names:[],face_names:[]},people:[],work:{days:[],start:'09:00',end:'19:00'},sources:{},fusion:{},place:{}}),{voice:['me'],face:[]});
 assert.doesNotMatch(html,/style="/);assert.match(html,/data-c=/);
});
test('model fields are escaped even when they are not known values', ()=>{
 const scene={minute:0,source:'model',scene:{who_am_i:{text:'a',confidence:'<qi>'},where_am_i:{text:'b',confidence:'mid'},people_doing:{text:'c',confidence:'low'},owner_view:'present',
  entries:[{key:'k',dimension:'people',text:'t',basis:'<img src=x onerror=alert(1)>',confidence:'<qb>',evidence:['<qx>']}],changes:[],summary:'s',want_to_know:''},checks:{},prompt:'<qp>',reason:'<qr>',version:'<qv>'};
 const html=ui.nowHtml({scene,strong:{owner:{state:'<qs>',why:'w'},coverage:{hearing_s:1,vision_s:0,m12_s:0}},segment:null})+ui.sceneHtml(scene,{model:'<qm>',latency_ms:'<ql>',usage:{},cost_usd:0})+ui.segmentsHtml([{start:'<qz>',end:null,cls:'<qc>',summary:null}]);
 assert.doesNotMatch(html,/<img|<q[a-z]>/);
});
test('refresh leaves an open minute alone', ()=>{
 assert.deepEqual(ui.refreshPlan(null),{timeline:true,detail:false});
 assert.deepEqual(ui.refreshPlan(123),{timeline:false,detail:false});
});
test('place line shows the reported place', ()=>{
 assert.equal(ui.placeLabel({label:'办公室',source:'owner_report',at:Date.UTC(2026,10,10,8,0)}).startsWith('办公室（你报告'),true);
 assert.equal(ui.placeLabel({label:null,source:'no_wifi_data'}),'未知（还没有报告）');
});
test('frame caption and panel markup', ()=>{
 assert.equal(ui.frameCaption(null),'还没有收到画面');
 const at=new Date(2026,10,11,10,5,7).getTime();
 assert.equal(ui.frameCaption({at,diff:12,today:34,missing:false}),'10:05:07 收到 · 变化 12 格 · 今天 34 张');
 assert.match(ui.frameCaption({at,diff:255,today:1,missing:true}),/文件缺失/);
 const html=readFileSync(new URL('./public/mind.html',import.meta.url),'utf8');
 assert.match(html,/id="mind-frame-img"/);assert.doesNotMatch(html,/style="/);
});
test('eye card text', ()=>{
 const html=ui.eyeHtml({minute:new Date(2026,10,11,12,8).getTime(),source:'model',result:{people_count:1,people:[{who:'主人',where:'桌前',doing:'喝水'}],
  activity:'主人在桌前喝水',scene:'办公室',changes:'拿起杯子',confidence:'high'},frames:[{seq:1},{seq:2}]});
 assert.match(html,/12:08/);assert.match(html,/1 人/);assert.match(html,/主人 · 桌前 · 喝水/);assert.match(html,/拿起杯子/);assert.doesNotMatch(html,/style="/);
 assert.match(ui.eyeHtml({minute:0,source:'failed',error:'x'}),/失败/);
 assert.match(ui.eyeHtml({minute:0,source:'budget'}),/预算/);
 assert.match(ui.eyeHtml(null),/还没有识别结果/);
 assert.match(ui.eyeHtml({minute:0,source:'model',result:{people_count:0,people:[],activity:'<b>',scene:'',changes:'',confidence:'low'}}),/&lt;b&gt;/);
});
test('此刻 shows the topic summary when there is one', ()=>{
 const base={minute:0,source:'model',version:1,scene:{summary:'s',who_am_i:{text:'a',confidence:'mid'},where_am_i:{text:'b',confidence:'mid'},people_doing:{text:'c',confidence:'mid'},entries:[],want_to_know:''}};
 const withTopic={scene:{...base,scene:{...base.scene,topic_summary:'两人在讨论地线要接到机壳上。'}},strong:null,segment:null};
 assert.match(ui.nowHtml(withTopic),/话题：两人在讨论地线要接到机壳上。/);
 assert.doesNotMatch(ui.nowHtml({scene:base,strong:null,segment:null}),/话题：/);
});

