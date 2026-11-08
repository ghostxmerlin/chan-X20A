import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { uploadAsset,verifiedAssets } from '../../scripts/install-eyes-assets.mjs';
import {waitForAssetSlot} from '../../scripts/device-asset-upload.mjs';
test('installer waits for asynchronous cleanup before the next upload',async()=>{
 let calls=0;
 await waitForAssetSlot({json:async path=>{assert.equal(path,'/v1/eyes/status');return {storage_ready:true,storage:{installing:++calls<3}};}});
 assert.equal(calls,3);
 await assert.rejects(waitForAssetSlot({json:async()=>({storage_ready:false})}),/SD unavailable/);
});
test('installer sends exact binary and authenticated boot headers',async()=>{
 const bytes=Buffer.from([0,10,13,255,128,1]);let requests=0;
 const server=http.createServer(async(req,res)=>{
  requests++;const chunks=[];for await(const b of req)chunks.push(b);
  assert.deepEqual(Buffer.concat(chunks),bytes);
  assert.equal(req.headers.authorization,'Bearer '+'a'.repeat(32));
  assert.equal(req.headers['x-boot-id'],'current-boot');
  assert.equal(req.headers['x-eyes-style'],'v14');assert.equal(req.headers['x-eyes-state'],'idle');
  res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify({installed:true,style:'v14',state:'idle'}));
 });await new Promise(r=>server.listen(0,'127.0.0.1',r));
 try {const result=await uploadAsset({address:`http://127.0.0.1:${server.address().port}`,token:'a'.repeat(32),device_id:'test'},'current-boot','v14','idle',bytes);assert.equal(result.installed,true);assert.equal(requests,1);}
 finally {await new Promise(r=>server.close(r));}
});
test('installer verifies all 90 local assets before any device operation',async()=>{
 const list=await verifiedAssets();assert.equal(list.length,90);assert.equal(list.reduce((n,a)=>n+a.bytes.length,0),10384688);
});
test('binary installer binds boot and IDs; interruptions and redirects never retry',async t=>{
 let requests=0;const server=http.createServer((req,res)=>{requests++;assert.equal(req.headers['x-boot-id'],'boot');assert.equal(req.headers['x-eyes-style'],'v05');req.resume();req.on('end',()=>{if(requests===1)res.destroy();else{res.writeHead(302,{Location:'/other'});res.end('{}');}});});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
 const binding={address:`http://127.0.0.1:${server.address().port}`,token:'a'.repeat(32),device_id:'test'};
 await assert.rejects(uploadAsset(binding,'boot','v05','idle',Buffer.from([1])));assert.equal(requests,1);
 await assert.rejects(uploadAsset(binding,'boot','v05','idle',Buffer.from([1])));assert.equal(requests,2);
 await assert.rejects(uploadAsset(binding,'boot','../v05','idle',Buffer.from([1])));assert.equal(requests,2);
});
