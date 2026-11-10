// Proxy from the local monitor to the server's mind API (server/mind/api.py), which listens on the
// server's loopback only. An ssh forward to a Unix socket in a private (0700) directory carries it —
// no local TCP port another process could squat to catch the token. The token is added here and
// never reaches the browser. Only GET state / timeline / minute with numeric from, to, t, version pass.
import {spawn} from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const PATHS=new Set(['/state','/timeline','/minute','/now','/segments','/scene','/settings','/names','/prompts','/labels','/stats']);
const POST_PATHS=new Set(['/settings','/mark','/label','/rerun']);
const PARAMS=new Set(['from','to','t','version','days','lane']);
const MAX_BODY=16384;
const DEFAULT_SOCKET=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../../.private/mind-tunnel/mind.sock');
function prepareSocketDir(socketPath){
 const dir=path.dirname(socketPath);
 fs.mkdirSync(dir,{recursive:true,mode:0o700});fs.chmodSync(dir,0o700);
}
export function socketRequest(socketPath,pathAndQuery,headers,{method='GET',body=null,timeout=8000}={}){
 return new Promise((resolve,reject)=>{
  const h=body==null?headers:{...headers,'Content-Type':'application/json','Content-Length':Buffer.byteLength(body)};
  const req=http.request({socketPath,path:pathAndQuery,method,headers:h,timeout},res=>{
   let body='';res.setEncoding('utf8');res.on('data',c=>{body+=c;if(body.length>4e6)req.destroy(new Error('too large'));});
   res.on('end',()=>resolve({status:res.statusCode,body}));
  });
  req.on('timeout',()=>req.destroy(new Error('timeout')));req.on('error',reject);req.end(body??undefined);
 });
}
export class MindProxy {
 constructor({ssh,token,socketPath=DEFAULT_SOCKET,remotePort=52740,spawnFn=spawn,requestFn=socketRequest,prepareFn=prepareSocketDir,now=Date.now}={}){
  Object.assign(this,{ssh,token,socketPath,remotePort,spawnFn,requestFn,prepareFn,now});this.child=null;this.lastStart=-Infinity;
 }
 static check(pathAndQuery){
  let url;try{url=new URL(pathAndQuery,'http://x');}catch{return null;}
  if(!pathAndQuery.startsWith('/')||!PATHS.has(url.pathname)||url.pathname!==pathAndQuery.split('?')[0])return null;
  for(const [k,v] of url.searchParams){
   if(!PARAMS.has(k))return null;
   if(k==='lane'?!['live','rerun'].includes(v):!/^\d{1,16}$/.test(v))return null;
  }
  return url.pathname+url.search;
 }
 tunnel(){
  if(this.child)return true;
  if(this.now()-this.lastStart<5000)return false;
  this.lastStart=this.now();
  this.prepareFn(this.socketPath);
  const child=this.spawnFn('ssh',['-N','-o','BatchMode=yes','-o','ExitOnForwardFailure=yes','-o','ServerAliveInterval=15',
   '-o','ConnectTimeout=15','-o','StreamLocalBindUnlink=yes','-o','StreamLocalBindMask=0177',
   '-L',`${this.socketPath}:127.0.0.1:${this.remotePort}`,this.ssh],{stdio:'ignore'});
  child.on('exit',()=>{if(this.child===child)this.child=null;});
  child.on('error',()=>{if(this.child===child)this.child=null;});
  this.child=child;return true;
 }
 async get(pathAndQuery){
  if(!this.ssh||!this.token)return {status:503,body:'本机 .env 缺少 SOUL_SERVER_SSH 或 SOUL_EAR_TOKEN'};
  const target=MindProxy.check(pathAndQuery);
  if(!target)return {status:400,body:'参数不允许'};
  if(!this.tunnel())return {status:503,body:'情景服务连接中'};
  try{return await this.requestFn(this.socketPath,target,{Authorization:`Bearer ${this.token}`});}
  catch{return {status:503,body:'情景服务连接中'};}
 }
 async post(path,bodyText){
  if(!this.ssh||!this.token)return {status:503,body:'本机 .env 缺少 SOUL_SERVER_SSH 或 SOUL_EAR_TOKEN'};
  if(!POST_PATHS.has(path))return {status:400,body:'不允许的写入'};
  if(typeof bodyText!=='string'||Buffer.byteLength(bodyText)>MAX_BODY)return {status:413,body:'内容过长'};
  if(!this.tunnel())return {status:503,body:'情景服务连接中'};
  // a rerun may wait for two model calls (90 s each)
  try{return await this.requestFn(this.socketPath,path,{Authorization:`Bearer ${this.token}`},{method:'POST',body:bodyText,timeout:200000});}
  catch{return {status:503,body:'情景服务连接中'};}
 }
 stop(){const c=this.child;this.child=null;if(c&&!c.killed)c.kill();}
}
