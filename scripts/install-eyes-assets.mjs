#!/usr/bin/env node
import fs from 'node:fs/promises';
import {uploadResource,waitForAssetSlot} from './device-asset-upload.mjs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { DeviceClient } from '../web/monitor/core.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
export async function uploadAsset(binding,boot,style,state,bytes) {
  return uploadResource(binding,boot,{kind:'eyes',style,state},bytes);
}
export async function verifiedAssets(folder=path.join(root,'assets/soul-eyes/v2-front')) {
  const manifest=JSON.parse(await fs.readFile(path.join(folder,'manifest.json')));
  const states=Object.keys(JSON.parse(await fs.readFile(path.join(folder,'states.json'))));
  if(states.length!==30||manifest.styles.map(s=>s.id).join(',')!=='v05,v14,v18')throw Error('Invalid catalog');
  const list=[];
  for(const style of manifest.styles) {
    if(style.animations.map(a=>a.id).join(',')!==states.join(','))throw Error('Invalid states');
    for(const a of style.animations) {
      const expected=`sdcard/soul/${style.id}/${a.id}.soul`;
      if(!/^[a-z_]+$/.test(a.id)||a.path!==expected||a.bytes>262144)throw Error('Invalid asset path');
      const bytes=await fs.readFile(path.join(folder,expected));
      if(bytes.length!==a.bytes||createHash('sha256').update(bytes).digest('hex')!==a.sha256)throw Error(`Asset hash mismatch: ${style.id}/${a.id}`);
      list.push({style:style.id,state:a.id,bytes});
    }
  }
  return list;
}
async function main() {
  if(process.argv.length>3)throw Error('Usage: node scripts/install-eyes-assets.mjs [private binding JSON]');
  const assets=await verifiedAssets();
  const config=JSON.parse(await fs.readFile(process.argv[2]||path.join(root,'.private/stackchan-monitor.json')));
  const client=new DeviceClient(config);const info=await client.connect();
  if(info.capabilities?.eyes!=='supported'||!info.eyes?.asset_install)throw Error('Firmware does not support eye resource installation');
  const status=(await client.json('/v1/telemetry')).eyes||{};
  if(!status.storage_ready)throw Error('SD storage unavailable; no files written, card was not formatted');
  let count=0;
  for(const a of assets) {
    await waitForAssetSlot(client);
    // Single attempt only. A new boot fails the device boot guard; an unknown
    // network result stops the installer rather than replaying automatically.
    const result=await uploadAsset(client.binding,info.boot_id,a.style,a.state,a.bytes);
    console.log(`${++count}/${assets.length} ${a.style}/${a.state}: ${result.unchanged?'verified existing':'installed and verified'}`);
  }
  console.log('All 90 resources verified. No display configuration changed.');
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url))main().catch(e=>{console.error(e.message);process.exitCode=1;});
