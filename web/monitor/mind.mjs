// Proxy from the local monitor to the server's mind API (server/mind/api.py), which listens on the
// server's loopback only. An ssh port forward carries it; the token is added here and never reaches
// the browser. Only GET state / timeline / minute with numeric from, to, t, version pass.
import {spawn} from 'node:child_process';
const PATHS=new Set(['/state','/timeline','/minute']);
const PARAMS=new Set(['from','to','t','version']);
export class MindProxy {
 constructor({ssh,token,localPort=52741,remotePort=52740,spawnFn=spawn,fetchFn=globalThis.fetch,now=Date.now}={}){
  Object.assign(this,{ssh,token,localPort,remotePort,spawnFn,fetchFn,now});this.child=null;this.lastStart=-Infinity;
 }
 static check(pathAndQuery){
  let url;try{url=new URL(pathAndQuery,'http://x');}catch{return null;}
  if(!pathAndQuery.startsWith('/')||!PATHS.has(url.pathname)||url.pathname!==pathAndQuery.split('?')[0])return null;
  for(const [k,v] of url.searchParams)if(!PARAMS.has(k)||!/^\d{1,16}$/.test(v))return null;
  return url.pathname+url.search;
 }
 tunnel(){
  if(this.child)return true;
  if(this.now()-this.lastStart<5000)return false;
  this.lastStart=this.now();
  const child=this.spawnFn('ssh',['-N','-o','BatchMode=yes','-o','ExitOnForwardFailure=yes','-o','ServerAliveInterval=15',
   '-o','ConnectTimeout=15','-L',`127.0.0.1:${this.localPort}:127.0.0.1:${this.remotePort}`,this.ssh],{stdio:'ignore'});
  child.on('exit',()=>{if(this.child===child)this.child=null;});
  child.on('error',()=>{if(this.child===child)this.child=null;});
  this.child=child;return true;
 }
 async get(pathAndQuery){
  if(!this.ssh||!this.token)return {status:503,body:'本机 .env 缺少 SOUL_SERVER_SSH 或 SOUL_EAR_TOKEN'};
  const target=MindProxy.check(pathAndQuery);
  if(!target)return {status:400,body:'参数不允许'};
  if(!this.tunnel())return {status:503,body:'情景服务连接中'};
  try{
   const r=await this.fetchFn(`http://127.0.0.1:${this.localPort}${target}`,{headers:{Authorization:`Bearer ${this.token}`},signal:AbortSignal.timeout(8000)});
   return {status:r.status,body:await r.text()};
  }catch{return {status:503,body:'情景服务连接中'};}
 }
 stop(){const c=this.child;this.child=null;if(c&&!c.killed)c.kill();}
}
