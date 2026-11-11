import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {Monitor} from './core.mjs';
import {createMonitorServer} from './server.mjs';
const sw=await import('data:text/javascript;base64,'+readFileSync(new URL('./public/switches-ui.js',import.meta.url)).toString('base64'));

const robot=({listen=false,hstate='idle',m12='standby',valid=true}={})=>({boot:'b1',capabilities:{hearing:'supported'},
 telemetry:{hearing:{requested:listen,state:hstate,url:'wss://x',token_configured:true,ready:true,enrollment:{}},m12:{valid,online:true}},
 m12:{status:m12}});

test('all off: nothing costs money', ()=>{
 const v=sw.switchView(robot(),true,{enabled:false});
 assert.deepEqual([v.hearing.on,v.vision.on,v.mind.on],[false,false,false]);
 assert.equal(v.cost,'现在不花钱');
});
test('hearing on costs tingwu; mind on with a sense on costs model calls', ()=>{
 assert.match(sw.switchView(robot({listen:true,hstate:'listening'}),true,{enabled:false}).cost,/听悟/);
 const v=sw.switchView(robot({m12:'detecting'}),true,{enabled:true});
 assert.equal(v.vision.on,true);assert.match(v.cost,/情景模型/);assert.doesNotMatch(v.cost,/听悟/);
});
test('mind on with both senses off makes no calls', ()=>{
 const v=sw.switchView(robot(),true,{enabled:true});
 assert.equal(v.mind.on,true);assert.equal(v.cost,'现在不花钱');assert.match(v.mind.detail,/感官都关/);
});
test('stale robot state or unreachable mind is unknown, never shown as off', ()=>{
 const v=sw.switchView(robot({listen:true}),false,null);
 assert.equal(v.hearing.known,false);assert.equal(v.mind.known,false);assert.equal(v.hearing.can,false);assert.equal(v.mind.can,false);
 assert.match(v.cost,/未知/);
});
test('starting / stopping vision counts as the requested side', ()=>{
 assert.equal(sw.switchView(robot({m12:'starting'}),true,{enabled:true}).vision.on,true);
 assert.equal(sw.switchView(robot({m12:'stopping'}),true,{enabled:true}).vision.on,false);
});
const fresh=Date.now();
const SW=(on,running,{available=true,stale=false}={})=>({tingwu:{on},tingwu_status:{running,available,stale,at:fresh}});
test('level is local when listening with the tingwu switch off', ()=>{
 const v=sw.switchView(robot({listen:true,hstate:'listening'}),true,{enabled:false},SW(false,[]));
 assert.equal(v.hearing.level,'local');assert.equal(v.cost,'现在不花钱');
 assert.equal(sw.switchView(robot(),true,{enabled:false},SW(true,[])).hearing.level,'off');
});
test('level tingwu and cost only when the ear reports a running stream', ()=>{
 const r=robot({listen:true,hstate:'listening'});
 const v=sw.switchView(r,true,{enabled:false},SW(true,['s1']));
 assert.equal(v.hearing.level,'tingwu');assert.equal(v.hearing.tingwuRunning,true);assert.match(v.cost,/正在花钱：听悟/);
 const w=sw.switchView(r,true,{enabled:false},SW(true,[]));
 assert.equal(w.hearing.tingwuRunning,false);assert.equal(w.cost,'现在不花钱');assert.match(w.hearing.detail,/听悟未在运行|正在开启听悟/);
});
test('stale status with tingwu selected says unknown, never free', ()=>{
 const v=sw.switchView(robot({listen:true}),true,{enabled:false},SW(true,[],{stale:true}));
 assert.equal(v.hearing.tingwuRunning,null);assert.match(v.cost,/听悟状态未知/);assert.doesNotMatch(v.cost,/现在不花钱/);
});
test('unavailable tingwu disables the button', ()=>{
 const v=sw.switchView(robot({listen:true}),true,{enabled:false},SW(true,[],{available:false}));
 assert.equal(v.hearing.tingwuAvailable,false);assert.match(v.hearing.detail,/服务器未配置听悟/);assert.doesNotMatch(v.cost,/听悟（/);
});
test('switches unknown: no tingwu level is claimed', ()=>{
 const v=sw.switchView(robot({listen:true}),true,{enabled:false},null);
 assert.equal(v.hearing.level,'unknown');
});
test('level requests set the tier before turning hearing on', ()=>{
 const r=robot();
 assert.deepEqual(sw.levelRequests('off',r),[{kind:'switch',body:{name:'tingwu',on:false}},{kind:'control',body:{action:'hearing_listen',enabled:false,boot:'b1'}}]);
 assert.deepEqual(sw.levelRequests('local',r),[{kind:'switch',body:{name:'tingwu',on:false}},{kind:'control',body:{action:'hearing_listen',enabled:true,boot:'b1'}}]);
 assert.deepEqual(sw.levelRequests('tingwu',r),[{kind:'switch',body:{name:'tingwu',on:true}},{kind:'control',body:{action:'hearing_listen',enabled:true,boot:'b1'}}]);
});
test('off turns the paid tier off even when the robot request fails', async()=>{
 const sent=[];
 const post=async req=>{sent.push(req.kind);if(req.kind==='control')throw new Error('robot unreachable');return {};};
 const errors=await sw.runLevel('off',robot(),post);
 assert.deepEqual(sent,['switch','control']);assert.equal(errors.length,1);
 const sent2=[];
 const errors2=await sw.runLevel('tingwu',robot(),async req=>{sent2.push(req.kind);throw new Error('server down');});
 assert.deepEqual(sent2,['switch']);assert.equal(errors2.length,1);       // never turn hearing on in an unknown tier
});
test('关 stays clickable when the robot state is unknown but the switch is known', ()=>{
 const v=sw.switchView(null,false,{enabled:false},SW(true,['s1']));
 const b=sw.levelButtons(v,false);
 assert.equal(b.off,false);assert.equal(b.local,true);assert.equal(b.tingwu,true);
 assert.equal(sw.levelButtons(sw.switchView(null,false,null,null),false).off,true);
 assert.equal(sw.levelButtons(v,true).off,true);
});
const cam=(r,on)=>({...r,telemetry:{...r.telemetry,health:{camera_enabled:on}}});
test('vision level upload needs both camera and M12', ()=>{
 assert.equal(sw.switchView(cam(robot({m12:'detecting'}),true),true,{enabled:false}).vision.level,'upload');
 assert.equal(sw.switchView(cam(robot({m12:'standby'}),false),true,{enabled:false}).vision.level,'off');
 assert.equal(sw.switchView(cam(robot({m12:'standby'}),true),true,{enabled:false}).vision.level,'camera_only');
 assert.equal(sw.switchView(cam(robot({m12:'detecting'}),true),false,{enabled:false}).vision.level,'unknown');
});
test('m12 only when the camera is off', ()=>{
 assert.equal(sw.switchView(cam(robot({m12:'detecting'}),false),true,{enabled:false}).vision.level,'m12_only');
});
test('vision requests: camera before m12', ()=>{
 const r=robot();
 assert.deepEqual(sw.visionRequests('upload',r),[{kind:'control',body:{action:'camera',enabled:true,boot:'b1'}},{kind:'control',body:{action:'m12',enabled:true,boot:'b1'}}]);
 assert.deepEqual(sw.visionRequests('off',r),[{kind:'control',body:{action:'camera',enabled:false,boot:'b1'}},{kind:'control',body:{action:'m12',enabled:false,boot:'b1'}}]);
});
test('recognize is never clickable in this step', ()=>{
 const v=sw.switchView(cam(robot({m12:'detecting'}),true),true,{enabled:true});
 const b=sw.visionButtons(v,false);
 assert.equal(b.recognize,true);assert.equal(b.off,false);assert.equal(b.upload,false);
 assert.equal(sw.visionButtons(sw.switchView(null,false,null),false).upload,true);
});
test('vision 关 works with the M12 disconnected while the camera uploads', ()=>{
 const r=cam(robot({m12:'disconnected'}),true);
 const v=sw.switchView(r,true,{enabled:false});
 assert.equal(sw.visionButtons(v,false).off,false);
 assert.equal(v.vision.level,'camera_only');
});
test('toggle requests: m12 and the mind switch', ()=>{
 assert.deepEqual(sw.toggleRequest('vision',robot({m12:'detecting'}),true),{kind:'control',body:{action:'m12',enabled:false,boot:'b1'}});
 assert.deepEqual(sw.toggleRequest('mind',robot(),{enabled:true}),{kind:'mind',body:{enabled:false}});
});
test('server serves switches-ui.js / switches.css and routes POST /api/mind/switch', async t=>{
 const calls=[],mind={get:async()=>({status:200,body:'{}'}),post:async(p,b)=>{calls.push([p,b]);return {status:200,body:'{"enabled":false}'};},stop(){}};
 const server=createMonitorServer(new Monitor({binding:{address:'http://127.0.0.1:8080',token:'b'.repeat(32)}}),undefined,mind);
 await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
 const url=`http://127.0.0.1:${server.address().port}`;
 assert.equal((await fetch(url+'/switches-ui.js')).status,200);
 assert.equal((await fetch(url+'/switches.css')).status,200);
 const r=await fetch(url+'/api/mind/switch',{method:'POST',headers:{'Content-Type':'application/json',Origin:url},body:'{"enabled":false}'});
 assert.equal(r.status,200);assert.deepEqual(calls,[['/switch','{"enabled":false}']]);
});
test('server routes GET and POST /api/mind/switches', async t=>{
 const seen=[],mind={get:async p=>{seen.push(['GET',p]);return {status:200,body:'{}'};},post:async(p,b)=>{seen.push(['POST',p,b]);return {status:200,body:'{}'};},stop(){}};
 const server=createMonitorServer(new Monitor({binding:{address:'http://127.0.0.1:8080',token:'b'.repeat(32)}}),undefined,mind);
 await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
 const url=`http://127.0.0.1:${server.address().port}`;
 assert.equal((await fetch(url+'/api/mind/switches')).status,200);
 assert.equal((await fetch(url+'/api/mind/frames/latest')).status,200);
 assert.equal((await fetch(url+'/api/mind/eye/latest')).status,200);
 assert.equal((await fetch(url+'/api/mind/eye?from=1&to=2')).status,200);
 assert.equal((await fetch(url+'/api/mind/switches',{method:'POST',headers:{'Content-Type':'application/json',Origin:url},body:'{"name":"tingwu","on":true}'})).status,200);
 assert.deepEqual(seen,[['GET','/switches'],['GET','/frames/latest'],['GET','/eye/latest'],['GET','/eye?from=1&to=2'],['POST','/switches','{"name":"tingwu","on":true}']]);
});
test('both pages carry the switch bar', ()=>{
 for(const f of ['index.html','mind.html']){
  const html=readFileSync(new URL('./public/'+f,import.meta.url),'utf8');
  assert.match(html,/id="switches"/);assert.match(html,/switches\.css/);assert.doesNotMatch(html,/style="/);
 }
});
