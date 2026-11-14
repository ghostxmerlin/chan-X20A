import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {uploadFontPart,verifiedFontParts} from '../../scripts/install-font-assets.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const binding=port=>({address:`http://127.0.0.1:${port}`,token:'a'.repeat(32),device_id:'test'});
test('font installer sends one part with X-Font-Part and checks the echoed id',async t=>{
 const bytes=Buffer.from([0,1,2,255]);let requests=0;
 const server=http.createServer(async(req,res)=>{
  requests++;const chunks=[];for await(const b of req)chunks.push(b);
  assert.deepEqual(Buffer.concat(chunks),bytes);
  assert.equal(req.headers['x-font-part'],'cjk16.1');assert.equal(req.headers['x-boot-id'],'boot');
  assert.equal(req.headers['x-eyes-style'],undefined);
  res.writeHead(200,{'Content-Type':'application/json'});
  res.end(JSON.stringify({installed:true,font_part:requests===1?'cjk16.1':'cjk16.0'}));
 });await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
 assert.equal((await uploadFontPart(binding(server.address().port),'boot','cjk16.1',bytes)).installed,true);
 await assert.rejects(uploadFontPart(binding(server.address().port),'boot','cjk16.1',bytes),/rejected/);   // wrong echo
 await assert.rejects(uploadFontPart(binding(server.address().port),'boot','../cjk16.1',bytes),/Invalid asset ID/);
 await assert.rejects(uploadFontPart(binding(server.address().port),'boot','cjk16.12',bytes),/Invalid asset ID/);
 assert.equal(requests,2);
});
test('font installer verifies every local part against the committed manifest',{skip:!fs.existsSync(path.join(root,'assets/ui-font/sdcard'))&&'parts not generated here'},async()=>{
 const parts=await verifiedFontParts();
 const manifest=JSON.parse(fs.readFileSync(path.join(root,'assets/ui-font/manifest.json')));
 assert.equal(parts.length,1+manifest.variants.reduce((n,v)=>n+v.parts.length,0));
 assert.equal(parts[0].part,"license");
 assert.ok(parts.every(p=>p.bytes.length<=262144));
});
