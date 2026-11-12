export const MAX_PAYLOAD=131072;
const MAGIC=Buffer.from('SCM1');
const table=Uint32Array.from({length:256},(_,x)=>{for(let i=0;i<8;i++)x=(x>>>1)^((x&1)?0xedb88320:0);return x>>>0;});
export function crc32(b){let c=0xffffffff;for(const x of b)c=(c>>>8)^table[(c^x)&255];return (c^0xffffffff)>>>0;}
export function encodeFrame(kind,payload){
 if(![1,2].includes(kind)||!payload.length||payload.length>MAX_PAYLOAD)throw new Error('无效串口帧');
 const h=Buffer.alloc(16);MAGIC.copy(h);h[4]=kind;h[5]=1;h.writeUInt32LE(payload.length,8);h.writeUInt32LE(crc32(payload),12);return Buffer.concat([h,payload]);
}
export class FrameParser {
 constructor(accept,now=Date.now){this.accept=accept;this.now=now;this.buffer=Buffer.alloc(0);this.began=null;this.errors=0;}
 feed(chunk){
  // Split large OS reads so even a hostile stream cannot grow the accumulator without bound.
  for(let i=0;i<chunk.length;i+=4096){this.buffer=Buffer.concat([this.buffer,chunk.subarray(i,i+4096)]);this.drain();}
 }
 expire(){this.drain();}
 drop(n){this.buffer=this.buffer.subarray(n);this.began=null;}
 drain(){
  while(this.buffer.length>=4){
   const at=this.buffer.indexOf(MAGIC);
   if(at<0){this.drop(Math.max(0,this.buffer.length-3));return;}if(at>0)this.drop(at);
   if(this.buffer.length<16)return;
   const h=this.buffer,n=h.readUInt32LE(8);
   if(![1,2].includes(h[4])||h[5]!==1||h[6]||h[7]||!n||n>MAX_PAYLOAD){this.errors++;this.drop(1);continue;}
   this.began??=this.now();
   // Firmware permits a progressing packet up to 1 s; leave scheduling margin.
   if(h.length<16+n){if(this.now()-this.began>1500){this.errors++;this.drop(1);continue;}return;}
   const payload=h.subarray(16,16+n);
   if(crc32(payload)!==h.readUInt32LE(12)){this.errors++;this.drop(1);continue;}
   const kind=h[4],copy=Buffer.from(payload);this.drop(16+n);this.accept(kind,copy);
  }
 }
}
const object=x=>x!==null&&typeof x==='object'&&!Array.isArray(x);
export function mergePatch(base,patch){
 if(!object(patch))return structuredClone(patch);
 const out=object(base)?structuredClone(base):{};
 for(const [k,v] of Object.entries(patch)){
  if(['__proto__','prototype','constructor'].includes(k))throw new Error('无效状态字段');
  out[k]=object(v)?mergePatch(out[k],v):structuredClone(v);
 }return out;
}
export class StateCache {
 constructor(boot){this.boot=boot;this.valid=false;this.revision=0;this.data=null;}
 accept(m){
  if(m.boot_id!==this.boot||!Number.isSafeInteger(m.revision)||m.revision<1||!object(m.data)){this.valid=false;return null;}
  if(!m.full&&(!this.valid||m.base_revision!==this.revision)){this.valid=false;return null;}
  this.data=m.full?mergePatch({},m.data):mergePatch(this.data,m.data);this.revision=m.revision;this.valid=true;return this.data;
 }
}
