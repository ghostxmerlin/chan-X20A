import {spawn} from 'node:child_process';
import {EventEmitter} from 'node:events';
import {createHmac,randomBytes,timingSafeEqual} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {FrameParser,encodeFrame,StateCache} from './serial-wire.mjs';
const streamOptions = options => ({ camera: options?.camera === true, servo_diagnostics: options?.servo_diagnostics === true });
const sameStreams = (a,b) => a?.camera === b.camera && a?.servo_diagnostics === b.servo_diagnostics;
const bridge=fileURLToPath(new URL('./serial_bridge.py',import.meta.url));
export class SerialDeviceClient extends EventEmitter {
 constructor(binding,{spawnFn=spawn,timeout=3000}={}){
  super();if(!/^[0-9a-f]{32}$/i.test(binding.token||'')||!/^\w[\w-]{0,127}$/.test(binding.device_id||''))throw new Error('串口绑定无效');
  this.binding=binding;this.spawnFn=spawnFn;this.timeout=timeout;this.push=true;this.verified=false;this.pending=new Map();this.nextId=0;this.child=null;this.closed=Promise.resolve();
  this.session=0;this.subscribed=false;this.desiredStreams=streamOptions();this.appliedStreams=null;this.streamSync=null;
 }
 open(){
  if(this.child)return;
  const serial=this.binding.usb_serial||this.binding.device_id;
  if(!/^[0-9a-f:-]{12,24}$/i.test(serial))throw new Error('请配置机器人 USB 序列号');
  const child=this.spawnFn(this.binding.serial_python||'python3',[bridge,serial],{stdio:['pipe','pipe','pipe']});this.child=child;this.session++;
  this.closed=new Promise(resolve=>child.once('close',resolve));
  const parser=this.parser=new FrameParser((kind,p)=>{if(this.child!==child)return;try{this.receive(kind,p);}catch{this.resync();}});
  this.expiry=setInterval(()=>parser.expire(),100);
  child.stdout.on('data',b=>{if(this.child===child)parser.feed(b);});child.stderr.resume();
  child.stdin.on('error',()=>this.disconnect(new Error('USB 写入中断'),child));
  child.once('error',()=>this.disconnect(new Error('无法启动 USB 桥接'),child));
  child.once('close',()=>this.disconnect(new Error('USB 已断开，等待重连'),child));
 }
 disconnect(error,child=this.child){
  if(child!==this.child)return;
  this.child=null;this.verified=false;this.cache=null;this.subscribed=false;this.appliedStreams=null;this.streamSync=null;this.resyncing=false;this.session++;clearInterval(this.expiry);clearInterval(this.ping);
  for(const p of this.pending.values()){clearTimeout(p.timer);p.reject(error);}this.pending.clear();
  if(child&&!child.killed)child.kill();this.emit('disconnect',error);
 }
 rpc(op,extra={},publicRequest=false){
  if(!this.child||(!publicRequest&&!this.verified))return Promise.reject(new Error('串口尚未验证'));
  if(this.pending.size>=8)return Promise.reject(new Error('串口请求过多'));
  const id=++this.nextId;
  const message={id,op,...extra,...(publicRequest?{}:{token:this.binding.token,boot_id:this.info?.boot_id})};
  return new Promise((resolve,reject)=>{
   const timer=setTimeout(()=>{this.pending.delete(id);reject(new Error('串口响应超时；未自动重发'));},this.timeout);
   this.pending.set(id,{resolve,reject,timer});
   this.child.stdin.write(encodeFrame(1,Buffer.from(JSON.stringify(message))),error=>{
    if(error){const p=this.pending.get(id);if(p){clearTimeout(p.timer);this.pending.delete(id);p.reject(error);}}
   });
  });
 }
 async connect(signal){
  if(signal?.aborted)throw new Error('已取消');
  this.open();this.verified=false;this.cache=null;this.subscribed=false;
  const session=this.session;
  const check=()=>{if(session!==this.session||signal?.aborted)throw new Error('USB 连接已取消');};
  const nonce=randomBytes(16).toString('hex');
  const proof=await this.rpc('hello',{nonce},true);check();
  const expected=createHmac('sha256',this.binding.token).update(`chan-discovery-v1\n${this.binding.device_id}\n${nonce}`).digest();
  if(proof.protocol!==1||proof.device_id!==this.binding.device_id||proof.nonce!==nonce||!proof.boot_id||
     !/^[0-9a-f]{64}$/i.test(proof.proof||'')||!timingSafeEqual(expected,Buffer.from(proof.proof,'hex')))throw new Error('USB 机器人身份校验失败');
  this.info={boot_id:proof.boot_id};this.verified=true;
  try{
   const info=await this.rpc('info');check();
   if(info.device_id!==this.binding.device_id||info.boot_id!==proof.boot_id)throw new Error('USB 机器人启动状态不匹配');
   this.info=info;this.cache=new StateCache(info.boot_id);return info;
  }catch(e){if(session===this.session)this.verified=false;throw e;}
 }
 async subscribe(options=this.desiredStreams){
  this.desiredStreams=streamOptions(options);
  const session=this.session,child=this.child,sent={...this.desiredStreams};
  const supports=this.info?.capabilities?.monitor_streams==='supported';
  await this.rpc('subscribe',supports?sent:{});
  if(session!==this.session)throw new Error('USB 订阅已取消');
  this.subscribed=true;this.appliedStreams=sent;
  await this.flushStreams();
  if(session!==this.session)throw new Error('USB 订阅已取消');
  clearInterval(this.ping);this.ping=setInterval(()=>{
   if(session===this.session)void this.rpc('ping').catch(e=>this.disconnect(e,child));
  },2000);
 }
 setStreams(options){
  this.desiredStreams=streamOptions(options);
  return this.flushStreams();
 }
 flushStreams(){
  if(!this.verified||!this.cache||!this.subscribed||this.info?.capabilities?.monitor_streams!=='supported')return Promise.resolve();
  if(this.streamSync)return this.streamSync;
  const session=this.session,child=this.child;
  const pending=Promise.resolve().then(async()=>{
   while(session===this.session&&this.subscribed&&!sameStreams(this.appliedStreams,this.desiredStreams)){
    const sent={...this.desiredStreams};
    await this.rpc('streams',sent);
    if(session===this.session)this.appliedStreams=sent;
   }
  }).then(()=>{
   if(this.streamSync===pending)this.streamSync=null;
   if(session===this.session&&!sameStreams(this.appliedStreams,this.desiredStreams))return this.flushStreams();
  },error=>{
   if(this.streamSync===pending)this.streamSync=null;
   // An uncertain OFF must stop lease renewal. Reconnect restores the latest
   // preferences; control RPCs are never replayed.
   if(session===this.session)this.disconnect(error,child);
   throw error;
  });
  this.streamSync=pending;return pending;
 }
 receive(kind,payload){
  if(kind===2){
   if(!this.desiredStreams.camera||!this.subscribed||!this.verified||!this.cache?.valid||payload.length<16||payload[12]!==255||payload[13]!==216)return;
   this.emit('frame',{seq:payload.readUInt32LE(0),timestamp_us:Number(payload.readBigUInt64LE(4)),bytes:Buffer.from(payload.subarray(12)),at:Date.now()});return;
  }
  const m=JSON.parse(payload);
  if(m.type==='reply'){
   const p=this.pending.get(m.id);if(!p)return;clearTimeout(p.timer);this.pending.delete(m.id);
   if(m.status>=200&&m.status<300)p.resolve(m.body);else p.reject(Object.assign(new Error(typeof m.body==='string'?m.body:'设备拒绝请求'),{status:m.status}));return;
  }
  // A previous lease may still be emitting while the new info RPC is in flight.
  // Do not turn those frames into snapshot requests before the handshake ends.
  if(!this.verified||!this.cache||m.boot_id!==this.info?.boot_id)return;
  if(m.type==='state'){
   const state=this.cache.accept(m);if(!state){this.resync();return;}
   this.emit('state',state);return;
  }
  if(m.type==='events')this.emit('events',m);
  if(m.type==='log'&&typeof m.text==='string')this.emit('log',m.text);
 }
 resync(){
  if(!this.verified||!this.cache||!this.subscribed||this.resyncing)return;
  if(this.cache)this.cache.valid=false;this.emit('stale');this.resyncing=true;
  const session=this.session,child=this.child;
  void this.rpc('snapshot').catch(e=>this.disconnect(e,child)).finally(()=>{if(session===this.session)this.resyncing=false;});
 }
 async control(path,body,boot,check){
  check();if(path!=='/v1/control'||!this.verified||boot!==this.info?.boot_id)throw new Error('控制状态已变化');
  return this.rpc('control',{body});
 }
 async close(){
  clearInterval(this.ping);
  // No unsubscribe ACK is needed to relinquish the port. The firmware lease
  // expires even if the child or USB cable has already disappeared.
  this.disconnect(new Error('USB 监视已停止'));
  await this.closed;
 }
}
