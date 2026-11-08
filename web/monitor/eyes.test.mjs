import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { Monitor, DeviceClient } from './core.mjs';
import { Controls } from './control.mjs';
import { EyesPanel } from './public/eyes-ui.js';
const id='12345678-1234-4234-8234-123456789abc';
test('eyes controls validate catalog, UUID, boot and fields through real client route',async t=>{
 const seen=[];const server=http.createServer((req,res)=>{let body='';req.on('data',b=>body+=b);req.on('end',()=>{seen.push({url:req.url,headers:req.headers,body:JSON.parse(body)});res.writeHead(202);res.end('{"accepted":true}');});});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
 const client=new DeviceClient({address:`http://127.0.0.1:${server.address().port}`,token:'a'.repeat(32),device_id:'test'});
 client.verified=true;client.info={boot_id:'b'};const m=new Monitor(client);m.online=m.running=true;m.telemetryAt=Date.now();m.telemetry={eyes:{enabled:true}};
 m.info={boot_id:'b',capabilities:{web_control:'supported',eyes:'supported'},eyes:{styles:[{id:'v05'}],states:[{id:'happy'}]}};
 const c=new Controls(m);const request={action:'eyes_play',request_id:id,state:'happy',boot:'b'};
 await c.execute(request);assert.deepEqual(seen[0].body,{op:'play',request_id:id,state:'happy',mode:'once'});assert.equal(seen[0].url,'/v1/eyes');assert.equal(seen[0].headers['x-boot-id'],'b');
 for(const bad of [{...request,state:'../happy'},{...request,request_id:'bad'},{...request,mode:'loop'},{...request,boot:'old'}])await assert.rejects(c.execute(bad));
 await c.execute({action:'eyes_configure',request_id:id,enabled:false,style:'v05',boot:'b'});
 assert.deepEqual(seen[1].body,{op:'configure',request_id:id,enabled:false,style:'v05'});
 await c.execute({action:'eyes_stop',request_id:id,boot:'b'});assert.equal(seen[2].body.op,'stop');
 m.info.capabilities.eyes='unsupported';await assert.rejects(c.execute(request));assert.equal(seen.length,3);
});
test('eyes panel never sends on load, reconnect or hidden render; click sends once and reports unknown',async()=>{
 const elements=new Map(),sent=[];const make=()=>({value:'',textContent:'',disabled:false,children:[],addEventListener(k,fn){this[k]=fn;},replaceChildren(...v){this.children=v;if(v.length)this.value=v[0].value;}});
 const doc={getElementById(id){if(!elements.has(id))elements.set(id,make());return elements.get(id);},createElement:make};
 const ui=new EyesPanel(doc,async b=>{sent.push(b);throw Error('结果未知');},()=>id);
 const s={boot:'b',capabilities:{eyes:'supported'},eyesCatalog:{styles:[{id:'v05',label:'猫眼'}],states:[{id:'happy',label:'开心'}]},telemetry:{eyes:{enabled:true,storage_ready:true,memory_ready:true,phase:'idle',selected_style:'v05',last_request:{}}}};
 ui.render(s,true);ui.render(s,true);assert.equal(sent.length,0);
 await doc.getElementById('eyes-play').click();assert.equal(sent.length,1);assert.equal(sent[0].request_id,id);assert.match(doc.getElementById('eyes-command').textContent,/未知/);
 s.boot='new';ui.render(s,true);ui.render(s,false);await doc.getElementById('eyes-play').click();assert.equal(sent.length,1);
 assert.equal(doc.getElementById('eyes-play').disabled,true);
});

function panelFixture() {
 const elements=new Map(),sent=[];
 const make=()=>({value:'',textContent:'',disabled:false,title:'',children:[],addEventListener(k,fn){this[k]=fn;},dispatchEvent(event){this[event.type]?.(event);},replaceChildren(...v){this.children=v;if(v.length)this.value=v[0].value;}});
 const doc={getElementById(name){if(!elements.has(name))elements.set(name,make());return elements.get(name);},createElement:make};
 const ui=new EyesPanel(doc,async body=>{sent.push(body);return {message:'已接收'};},()=>id);
 const state={boot:'b',capabilities:{eyes:'supported'},eyesCatalog:{natural_cycle:true,styles:[{id:'v05',label:'猫眼'},{id:'v14',label:'机械眼'}],states:[{id:'happy',label:'开心'},{id:'idle',label:'待机'}]},telemetry:{eyes:{enabled:true,natural_cycle:true,storage_ready:true,memory_ready:true,phase:'playing',selected_style:'v05',source:'natural',state:'happy',next_change_ms:2400,last_request:{}}}};
 return {ui,state,sent,el:name=>doc.getElementById(name)};
}
test('natural-cycle capability changes labels without inventing support on older firmware',()=>{
 const {ui,state,sent,el}=panelFixture();
 ui.render(state,true);
 assert.equal(el('eyes-enable').textContent,'自然循环');
 assert.equal(el('eyes-stop').textContent,'回到待机');
 assert.match(el('eyes-status').textContent,/自然循环/);
 assert.match(el('eyes-current').textContent,/猫眼.*开心.*自然循环.*2\.4\s*秒/);
 state.telemetry.eyes.phase='idle';state.telemetry.eyes.state='idle';state.telemetry.eyes.next_change_ms=0;ui.render(state,true);
 assert.match(el('eyes-status').textContent,/自然循环/);
 assert.match(el('eyes-current').textContent,/自然循环/);
 assert.doesNotMatch(el('eyes-current').textContent,/0\.0\s*秒/);
 delete state.eyesCatalog.natural_cycle;ui.render(state,true);
 assert.equal(el('eyes-enable').textContent,'启用款式');
 assert.equal(el('eyes-status').textContent,'眼睛待机');
 assert.doesNotMatch(el('eyes-current').textContent,/自然循环|下次变化/);
 delete state.telemetry.eyes.natural_cycle;state.telemetry.eyes.source='none';ui.render(state,true);
 assert.equal(el('eyes-status').textContent,'眼睛待机');
 assert.equal(sent.length,0);
});
test('manual and voice takeover are distinct from natural playback and inactive or stale state',()=>{
 const {ui,state,el}=panelFixture();
 for(const [source,label] of [['manual','手动'],['voice','语音']]) {
  state.telemetry.eyes.source=source;ui.render(state,true);
  assert.match(el('eyes-status').textContent,new RegExp(`自然循环.*${label}接管`));
  assert.doesNotMatch(el('eyes-current').textContent,/下次变化/);
 }
 state.telemetry.eyes.source='natural';state.telemetry.eyes.phase='suspended';ui.render(state,true);
 assert.equal(el('eyes-status').textContent,'已暂停');
 state.telemetry.eyes.phase='legacy';state.telemetry.eyes.enabled=false;ui.render(state,true);
 assert.equal(el('eyes-status').textContent,'原首页');
 assert.doesNotMatch(el('eyes-current').textContent,/下次变化/);
 ui.render(state,false);assert.equal(el('eyes-status').textContent,'连接或状态已过期');assert.equal(el('eyes-current').textContent,'—');
});
test('changing style never controls device and one-shot cannot silently use the previous style',async()=>{
 const {ui,state,sent,el}=panelFixture();ui.render(state,true);
 el('eyes-style').value='v14';el('eyes-style').dispatchEvent({type:'change'});ui.render(state,true);
 assert.equal(sent.length,0);
 assert.match(el('eyes-command').textContent,/先.*自然循环.*所选款式/);
 await el('eyes-play').click();assert.equal(sent.length,0);
 assert.match(el('eyes-command').textContent,/先.*自然循环.*所选款式/);
 await el('eyes-enable').click();
 assert.deepEqual(sent,[{action:'eyes_configure',boot:'b',request_id:id,enabled:true,style:'v14'}]);
 state.telemetry.eyes.selected_style='v14';ui.render(state,true);
 await el('eyes-play').click();
 assert.deepEqual(sent[1],{action:'eyes_play',boot:'b',request_id:id,state:'happy'});
 await el('eyes-stop').click();assert.equal(sent[2].action,'eyes_stop');
 await el('eyes-disable').click();assert.deepEqual(sent[3],{action:'eyes_configure',boot:'b',request_id:id,enabled:false,style:'v14'});
});
test('manual click can interrupt natural loading only when the firmware advertises the cycle',async()=>{
 const {ui,state,sent,el}=panelFixture();state.telemetry.eyes.phase='loading';ui.render(state,true);
 assert.equal(el('eyes-play').disabled,false);
 assert.equal(sent.length,0);
 await el('eyes-play').click();assert.deepEqual(sent,[{action:'eyes_play',boot:'b',request_id:id,state:'happy'}]);
 for(const source of ['configure','manual','voice']) {
  state.telemetry.eyes.source=source;ui.render(state,true);
  assert.equal(el('eyes-play').disabled,true);await el('eyes-play').click();
 }
 state.telemetry.eyes.source='natural';state.telemetry.eyes.phase='suspended';ui.render(state,true);
 assert.equal(el('eyes-play').disabled,true);await el('eyes-play').click();
 state.telemetry.eyes.phase='loading';delete state.eyesCatalog.natural_cycle;ui.render(state,true);
 assert.equal(el('eyes-play').disabled,true);await el('eyes-play').click();
 assert.equal(sent.length,1);
});
