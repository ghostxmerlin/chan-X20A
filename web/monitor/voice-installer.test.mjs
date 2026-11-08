import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {uploadVoice,verifiedVoices} from '../../scripts/install-voice-assets.mjs';
test('voice installer verifies forty SD files against original encoded hashes',async t=>{
 try {await fs.access(new URL('../../.private/voice-library/manifest.json',import.meta.url));}
 catch {t.skip('Prepare local voice assets first');return;}
 const list=await verifiedVoices();assert.equal(list.length,40);assert.equal(list.reduce((n,a)=>n+a.bytes.length,0),1402880);
 assert.deepEqual(list.map(a=>a.id),Array.from({length:40},(_,i)=>i+1));
});
test('voice verification rejects modified files and paths before upload',async t=>{
 const folder=await fs.mkdtemp(path.join(os.tmpdir(),'voice-assets-'));t.after(()=>fs.rm(folder,{recursive:true,force:true}));
 const bytes=Buffer.from([0,1,2,255]),clips=[];
 for(let id=1;id<=40;id++) {
  const source_id=`PLAY-${String(id).padStart(3,'0')}`;
  clips.push({id,source_id,sd_path:`/sdcard/soul/voice/${source_id}.adpcm`,bytes:bytes.length,encoded_sha256:createHash('sha256').update(bytes).digest('hex')});
  await fs.writeFile(path.join(folder,source_id+'.adpcm'),bytes);
 }
 const manifest={format:'ima_wav_mono_blocks',sample_rate:16000,playback_sample_rate:24000,clips};
 const save=()=>fs.writeFile(path.join(folder,'manifest.json'),JSON.stringify(manifest));await save();
 assert.equal((await verifiedVoices(folder)).length,40);
 await fs.writeFile(path.join(folder,'PLAY-040.adpcm'),Buffer.from([0,1,2,3]));await assert.rejects(verifiedVoices(folder),/hash mismatch/);
 clips[0].sd_path='/sdcard/other';await save();await assert.rejects(verifiedVoices(folder),/ID\/path/);
});
test('voice uploads bind clip, boot and binary; reject unknown IDs and redirect',async t=>{
 let count=0;const data=Buffer.from([0,255,10,13,1]);
 const server=http.createServer(async(req,res)=>{
  count++;assert.equal(req.url,'/v1/voice/assets');assert.equal(req.headers['x-voice-id'],'40');
  assert.equal(req.headers['x-boot-id'],'test-boot');assert.equal(req.headers.authorization,'Bearer '+'b'.repeat(32));
  const chunks=[];for await(const b of req)chunks.push(b);assert.deepEqual(Buffer.concat(chunks),data);
  res.writeHead(count===1?200:302,{'Content-Type':'application/json',Location:'/wrong'});
  res.end(JSON.stringify({installed:true,clip_id:40}));
 });await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
 const binding={address:`http://127.0.0.1:${server.address().port}`,token:'b'.repeat(32),device_id:'test'};
 assert.equal((await uploadVoice(binding,'test-boot',40,data)).installed,true);
 await assert.rejects(uploadVoice(binding,'test-boot',40,data));assert.equal(count,2);
 for(const id of [0,41,'1\r\nInjected: yes',1.2])await assert.rejects(uploadVoice(binding,'test-boot',id,data));
 assert.equal(count,2);
});
