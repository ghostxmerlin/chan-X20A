#!/usr/bin/env node
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {DeviceClient} from '../web/monitor/core.mjs';
import {uploadResource,waitForAssetSlot} from './device-asset-upload.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
export async function uploadVoice(binding,boot,id,bytes) {
  return uploadResource(binding,boot,{kind:'voice',clip_id:id},bytes);
}
export async function verifiedVoices(folder=path.join(root,'.private/voice-library')) {
  const manifest=JSON.parse(await fs.readFile(path.join(folder,'manifest.json')));
  if(manifest.format!=='ima_wav_mono_blocks'||manifest.sample_rate!==16000||manifest.playback_sample_rate!==24000||manifest.clips?.length!==40)throw Error('Invalid voice manifest');
  const list=[];
  for(let i=0;i<40;i++) {
    const clip=manifest.clips[i],source=`PLAY-${String(i+1).padStart(3,'0')}`;
    if(clip.id!==i+1||clip.source_id!==source||clip.sd_path!==`/sdcard/soul/voice/${source}.adpcm`||clip.bytes>262144)throw Error('Invalid voice ID/path');
    const bytes=await fs.readFile(path.join(folder,source+'.adpcm'));
    if(!bytes.length||bytes.length!==clip.bytes||createHash('sha256').update(bytes).digest('hex')!==clip.encoded_sha256)throw Error('Voice hash mismatch: '+source);
    list.push({id:clip.id,bytes});
  }
  return list;
}
async function main() {
  if(process.argv.length>3)throw Error('Usage: node scripts/install-voice-assets.mjs [private binding JSON]');
  const list=await verifiedVoices();
  const client=new DeviceClient(JSON.parse(await fs.readFile(process.argv[2]||path.join(root,'.private/stackchan-monitor.json'))));
  const info=await client.connect();
  if(info.voice_library?.storage!=='sd'||!info.voice_library.asset_install)throw Error('Firmware does not support SD voice installation');
  if(!(await client.json('/v1/eyes/status')).storage_ready)throw Error('SD unavailable; no files written');
  for(const a of list) {
    await waitForAssetSlot(client);
    const result=await uploadVoice(client.binding,info.boot_id,a.id,a.bytes);
    console.log(`${a.id}/40: ${result.unchanged?'verified existing':'installed and verified'}`);
  }
  console.log('All 40 SD voice files verified. No playback was requested.');
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url))main().catch(e=>{console.error(e.message);process.exitCode=1;});
