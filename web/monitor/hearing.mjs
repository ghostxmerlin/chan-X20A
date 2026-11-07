import fs from 'node:fs';
import {parseEnv} from 'node:util';
export function validateHearingConfig(url,token) {
 if(typeof url!=='string'||url.length>256||!/^(wss?):\/\/[A-Za-z0-9.-]+(?::[0-9]{1,5})?(?:\/[^\s?#@\\]*)?$/.test(url))throw new Error('请填写不含密钥的 ws:// 或 wss:// 地址');
 let parsed;try{parsed=new URL(url);}catch{throw new Error('后端地址或端口无效');}
 if(parsed.port==='0')throw new Error('后端端口无效');
 if(url.match(/^wss?:\/\/([^/:]+)/)?.[1]!==parsed.hostname || !/^\d+\.\d+\.\d+\.\d+$/.test(parsed.hostname)||parsed.hostname.split('.').some(n=>Number(n)>255))throw new Error('此版本请填写后端 IPv4 地址');
 if(typeof token!=='string'||token.length>192||/[^\x21-\x7e]/.test(token))throw new Error('密钥须为最多 192 个可见英文字符；留空保留原值');
 return {url,token};
}
export function existingHearingConfig(filename) {
 const env=parseEnv(fs.readFileSync(filename,'utf8'));
 return validateHearingConfig(env.SOUL_EAR_URL,env.SOUL_EAR_TOKEN);
}
export function hearingBody(input,config) {
 const fail=message=>{throw Object.assign(new Error(message),{status:400});};
 if(input.action==='hearing_import'){
  try{return {op:'config',...validateHearingConfig(...((v)=>[v.url,v.token])(config()))};}
  catch{fail('本机没有可用的语音后端配置，请手动填写');}
 }
 if(input.action==='hearing_config'){
  try{return {op:'config',...validateHearingConfig(input.url,input.token)};}catch(e){fail(e.message);}
 }
 if(input.action==='hearing_listen'){
  if(typeof input.enabled!=='boolean')fail('监听状态须为布尔值');return {op:'listen',enabled:input.enabled};
 }
 if(input.action==='hearing_cancel')return {op:'cancel'};
 if(typeof input.name!=='string'||!/^[\p{L}\p{N}_ -]{1,48}$/u.test(input.name)||!input.name.trim()||typeof input.replace!=='boolean')fail('请填写 1–48 字的姓名，只使用文字、数字、空格、下划线或短横线');
 return {op:'enroll',name:input.name,replace:input.replace};
}
