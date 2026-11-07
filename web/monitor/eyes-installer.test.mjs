import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { uploadAsset,verifiedAssets } from '../../scripts/install-eyes-assets.mjs';
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
