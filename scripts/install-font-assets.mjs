#!/usr/bin/env node
// Install the screen's CJK font parts (collector-0.4.95-font) on the robot's SD card over Wi-Fi, like the eyes
// and the voice clips: same authenticated /v1/assets upload, one part (<= 256 KiB) at a time, single attempt each.
//   python3 scripts/generate-cjk-font.py --fetch .cache/ui-font/source   # once, to make assets/ui-font/sdcard
//   node scripts/install-font-assets.mjs [private binding JSON]
// The robot accepts only the parts its own catalog names (size + SHA-256), verifies each after writing, and loads
// a variant as soon as all its parts are there (no reboot). Nothing else on the card or the device changes.
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {DeviceClient} from '../web/monitor/core.mjs';
import {uploadResource,waitForAssetSlot} from './device-asset-upload.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
export async function uploadFontPart(binding,boot,part,bytes) {
  return uploadResource(binding,boot,{kind:'font',part},bytes);
}
export async function verifiedFontParts(folder=path.join(root,'assets/ui-font')) {
  const manifest=JSON.parse(await fs.readFile(path.join(folder,'manifest.json')));
  if(!manifest.format?.startsWith('HCJK v1')||!manifest.variants?.length)throw Error('Invalid font manifest');
  // The OFL text goes first, so the card never holds the font without its license.
  const lic=manifest.license;
  if(lic?.path!=='sdcard/soul/font/LICENSE-OFL.txt')throw Error('Invalid font manifest license');
  const text=await fs.readFile(path.join(folder,'LICENSE-OFL.txt'));
  if(text.length!==lic.bytes||createHash('sha256').update(text).digest('hex')!==lic.sha256)throw Error('Font license hash mismatch');
  const list=[{variant:null,part:'license',bytes:text,sha256:lic.sha256}];
  for(const v of manifest.variants) {
    if(!/^cjk\d{2}$/.test(v.id)||!v.parts.length||v.parts.length>10)throw Error('Invalid font variant');
    const whole=createHash('sha256');
    for(const [n,p] of v.parts.entries()) {
      if(p.path!==`sdcard/soul/font/${v.id}.${n}.bin`||p.bytes>262144)throw Error('Invalid font part path');
      const bytes=await fs.readFile(path.join(folder,p.path)).catch(()=>{throw Error(`Missing ${p.path}: run scripts/generate-cjk-font.py`);});
      if(bytes.length!==p.bytes||createHash('sha256').update(bytes).digest('hex')!==p.sha256)throw Error(`Font part hash mismatch: ${p.path}`);
      whole.update(bytes);
      list.push({variant:v.id,part:`${v.id}.${n}`,bytes,sha256:p.sha256});
    }
    if(whole.digest('hex')!==v.sha256)throw Error(`Font variant hash mismatch: ${v.id}`);
  }
  return list;
}
async function main() {
  if(process.argv.length>3)throw Error('Usage: node scripts/install-font-assets.mjs [private binding JSON]');
  const parts=await verifiedFontParts();
  const client=new DeviceClient(JSON.parse(await fs.readFile(process.argv[2]||path.join(root,'.private/stackchan-monitor.json'))));
  const info=await client.connect();
  const fw=info.ui_font;
  if(!fw?.variants)throw Error('Firmware has no SD font support (collector-0.4.95-font or later); no files written');
  // Same catalog on both sides: each variant's part count and first part's hash prefix.
  for(const v of fw.variants) {
    const local=parts.filter(p=>p.variant===v.id);
    if(local.length!==v.parts||local[0]?.sha256.slice(0,16)!==v.part0_sha256_prefix)
      throw Error(`Firmware font catalog differs for ${v.id}: flash the matching firmware or regenerate. No files written`);
  }
  if(!(await client.json('/v1/telemetry')).eyes?.storage_ready)throw Error('SD unavailable; no files written');
  for(const [i,p] of parts.entries()) {
    await waitForAssetSlot(client);
    const result=await uploadFontPart(client.binding,info.boot_id,p.part,p.bytes);
    console.log(`${i+1}/${parts.length} ${p.part}: ${result.unchanged?'verified existing':'installed and verified'}`);
  }
  console.log('All font parts verified on SD. The robot loads them without a reboot; check info.ui_font.variants[].loaded.');
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url))main().catch(e=>{console.error(e.message);process.exitCode=1;});
