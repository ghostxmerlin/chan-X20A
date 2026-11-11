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
test('toggle requests: hearing_listen, m12 and the mind switch', ()=>{
 assert.deepEqual(sw.toggleRequest('hearing',robot(),true),{kind:'control',body:{action:'hearing_listen',enabled:true,boot:'b1'}});
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
 assert.equal((await fetch(url+'/api/mind/switches',{method:'POST',headers:{'Content-Type':'application/json',Origin:url},body:'{"name":"tingwu","on":true}'})).status,200);
 assert.deepEqual(seen,[['GET','/switches'],['POST','/switches','{"name":"tingwu","on":true}']]);
});
test('both pages carry the switch bar', ()=>{
 for(const f of ['index.html','mind.html']){
  const html=readFileSync(new URL('./public/'+f,import.meta.url),'utf8');
  assert.match(html,/id="switches"/);assert.match(html,/switches\.css/);assert.doesNotMatch(html,/style="/);
 }
});
