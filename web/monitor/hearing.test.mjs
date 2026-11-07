import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {execFileSync} from 'node:child_process';
import {Monitor,deviceRequest} from './core.mjs';
import {Controls} from './control.mjs';

function fixture(){
 const calls=[],m=new Monitor({binding:{},verified:true,control:async(...args)=>{calls.push(args);return {accepted:true};}});
 m.running=m.online=true;m.info={boot_id:'current',capabilities:{web_control:'supported',hearing:'supported'}};
 m.telemetryAt=Date.now();m.telemetry={hearing:{ready:true,token_configured:true},health:{audio_ready:true}};
 return {m,calls,c:new Controls(m,{loadHearingConfig:()=>({url:'ws://127.0.0.1:8765/',token:'private-key'})})};
}
test('hearing routes strict config, everyone listening and explicit enrollment',async()=>{
 const {c,calls,m}=fixture();
 await c.execute({action:'hearing_config',boot:'current',url:'ws://127.0.0.1:8765/',token:''});
 assert.deepEqual(calls.at(-1).slice(0,2),['/v1/hearing',{op:'config',url:'ws://127.0.0.1:8765/',token:''}]);
 await c.execute({action:'hearing_listen',boot:'current',enabled:true});
 await c.execute({action:'hearing_enroll',boot:'current',name:'爸爸',replace:false});
 assert.deepEqual(calls.at(-1)[1],{op:'enroll',name:'爸爸',replace:false});
 await c.execute({action:'hearing_cancel',boot:'current'});
 for(const url of ['http://ear/','ws://user:pass@ear/','ws://127.0.0.1/?token=secret','ws://ear:70000/','ws://127.1/','ws://01.2.3.4/'])
  await assert.rejects(c.execute({action:'hearing_config',boot:'current',url,token:'x'}));
 for(const name of ['', '../papa', 'a'.repeat(49)])await assert.rejects(c.execute({action:'hearing_enroll',boot:'current',name,replace:false}));
 const before=calls.length;m.telemetryAt=Date.now()-9000;
 await assert.rejects(c.execute({action:'hearing_listen',boot:'current',enabled:true}));assert.equal(calls.length,before);
});
test('existing backend import remains server-side and does not enter returned state',async()=>{
 const {c,calls,m}=fixture();const result=await c.execute({action:'hearing_import',boot:'current'});
 assert.equal(calls[0][1].token,'private-key');
 assert.ok(!JSON.stringify({result,state:m.snapshot()}).includes('private-key'));
});
test('real curl POST keeps payload secret out of argv',async t=>{
 const secret='hearing-secret-'+Date.now();let got,argv;
 const server=http.createServer((req,res)=>{
  argv=execFileSync('/bin/ps',['-axo','command'],{encoding:'utf8'});
  let body='';req.on('data',d=>body+=d);req.on('end',()=>{got=JSON.parse(body);res.end('{"accepted":true}');});
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
 await deviceRequest(`http://127.0.0.1:${server.address().port}/v1/hearing`,{method:'POST',token:'a'.repeat(32),boot:'current',body:{op:'config',token:secret}});
 assert.equal(got.token,secret);assert.ok(!argv.includes(secret),'backend secret leaked through curl argv');
});
