import {spawn} from 'node:child_process';
import {validateBinding} from '../web/monitor/core.mjs';
// HTTP success can arrive before async request cleanup releases the upload gate.
// Only poll status here; never replay a POST whose result was not confirmed.
export async function waitForAssetSlot(client) {
  for(let n=0;n<50;n++) {
    const status=await client.json('/v1/eyes/status');
    if(!status.storage_ready)throw Error('SD unavailable');
    if(status.storage?.installing===false)return;
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  throw Error('Previous asset installation is still busy');
}
export async function uploadResource(binding,boot,resource,bytes) {
  const b=validateBinding(binding),{kind,style,state,clip_id}=resource,voice=kind==='voice';
  if(!/^[\w-]{1,79}$/.test(boot)||!['voice','eyes'].includes(kind)||!Buffer.isBuffer(bytes)||!bytes.length||bytes.length>262144)throw Error('Invalid asset');
  if(voice?(!Number.isInteger(clip_id)||clip_id<1||clip_id>40):(!['v05','v14','v18'].includes(style)||!/^[a-z_]{1,31}$/.test(state)))throw Error('Invalid asset ID');
  const headers=voice?`X-Voice-Id: ${clip_id}\n`:`X-Eyes-Style: ${style}\nX-Eyes-State: ${state}\n`;
  return new Promise((resolve,reject)=>{
    // Match the monitor's system network path on macOS. No redirects/retries,
    // and credentials stay in stdin rather than argv or curl configuration.
    const child=spawn('/usr/bin/curl',['-q','--noproxy','*','--proto','=http',
      '--connect-timeout','3','--max-time','30','--silent','--show-error',
      '--include','--header','@-','--request','POST','--data-binary','@/dev/fd/3',
      new URL(`/v1/${voice?'voice':'eyes'}/assets`,b.address).href],{stdio:['pipe','pipe','pipe','pipe']});
    child.stdin.on('error',()=>{});child.stdio[3].on('error',()=>{});
    child.stdin.end(`Authorization: Bearer ${b.token}\nX-Boot-Id: ${boot}\n${headers}Content-Type: application/octet-stream\nConnection: close\nExpect:\n`);
    child.stdio[3].end(bytes);child.stderr.resume();
    const chunks=[];let size=0,error;
    child.stdout.on('data',data=>{size+=data.length;if(size>32768){error=Error('Response too large');child.kill();}else chunks.push(data);});
    child.on('error',()=>{error=Error('Cannot start system curl');});
    child.on('close',code=>{
      if(error||code!==0){
        const status=Buffer.concat(chunks).toString('latin1').match(/^HTTP\/\S+ (\d{3})/)?.[1];
        reject(error||Error(`Upload interrupted (curl ${code}, HTTP ${status||'unknown'}); result unknown`));return;
      }
      try {
        const response=Buffer.concat(chunks),at=response.indexOf('\r\n\r\n');
        if(at<0||at>16384)throw Error('Invalid response headers');
        const status=Number(response.subarray(0,at).toString('latin1').match(/^HTTP\/\S+ (\d{3})/)?.[1]);
        const result=JSON.parse(response.subarray(at+4));
        if(status!==200||result.installed!==true||(voice?result.clip_id!==clip_id:result.style!==style||result.state!==state))throw Error(`Install rejected: ${status}`);
        resolve(result);
      }catch(e){reject(e);}
    });
  });
}
