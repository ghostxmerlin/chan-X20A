// Saved network management: list/prefer/remove, add with a numeric password, scan, reconnect.
// Reads telemetry.wifi; sends only on explicit clicks. Passwords are never displayed.
export class WifiPanel {
 constructor(doc,send=async body=>{
  const response=await fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(7000)});
  if(!response.ok)throw new Error(await response.text());return response.json();
 }){
  this.doc=doc;this.send=send;this.pending=false;this.rendered='';
  this.el('wifi-scan').addEventListener('click',()=>this.act('wifi_scan',{}));
  this.el('wifi-reconnect').addEventListener('click',()=>this.act('wifi_reconnect',{}));
  this.el('wifi-add').addEventListener('click',()=>{
   const ssid=this.el('wifi-ssid').value.trim(),password=this.el('wifi-password').value.trim();
   if(!ssid||ssid.length>32){this.el('wifi-command').textContent='请填写 1–32 字符的网络名';return;}
   if(password&&(password.length<8||password.length>63)){this.el('wifi-command').textContent='密码需 8–63 位，开放网络留空';return;}
   this.act('wifi_add',{ssid,password,preferred:this.el('wifi-preferred').checked});
  });
  this.el('wifi-profiles').addEventListener('click',event=>{
   const button=event.target?.closest?.('button[data-op]');if(!button)return;
   this.act(button.dataset.op==='prefer'?'wifi_prefer':'wifi_remove',{ssid:button.dataset.ssid});
  });
  this.el('wifi-scan-list').addEventListener('click',event=>{
   const button=event.target?.closest?.('button[data-ssid]');if(!button)return;
   this.el('wifi-ssid').value=button.dataset.ssid;this.el('wifi-password').focus();
  });
 }
 el(id){return this.doc.getElementById(id);}
 async act(action,params){
  if(!this.ready||this.pending)return;
  const boot=this.state.boot;this.pending=true;this.el('wifi-command').textContent='正在发送…';this.render(this.state,this.fresh);
  try{const result=await this.send({action,boot,...params});this.el('wifi-command').textContent=this.state?.boot===boot?result.message:'机器人已重启，请核对当前状态';if(action==='wifi_add')this.el('wifi-password').value='';}
  catch(e){this.el('wifi-command').textContent=e.name==='TimeoutError'?'结果未确认，请查看状态；不会自动重发':e.message||'结果未确认';}
  finally{this.pending=false;this.render(this.state,this.fresh);}
 }
 render(state,fresh){
  this.state=state;this.fresh=fresh;
  const supported=state?.capabilities?.wifi_profiles==='supported';
  const w=state?.telemetry?.wifi||{};
  this.ready=!!fresh&&supported&&Array.isArray(w.profiles);
  const blocked=!this.ready||this.pending;
  for(const id of ['wifi-scan','wifi-reconnect','wifi-add','wifi-ssid','wifi-password','wifi-preferred'])this.el(id).disabled=blocked;
  this.el('wifi-scan').disabled=blocked||!!w.scanning;
  this.el('wifi-status').textContent=!fresh?'状态已过期':!supported?'当前固件不支持网络管理':`${w.connected?`已连接 ${w.ssid||'—'} · ${w.rssi_dbm??'—'} dBm · ${w.ip||'—'}`:'未连接'}${w.status?` · ${w.status}`:''}`;
  const key=`${state?.boot}|${JSON.stringify(w.profiles)}|${JSON.stringify(w.scan)}|${w.scanning}|${blocked}`;
  if(key===this.rendered)return;this.rendered=key;
  const profiles=this.el('wifi-profiles');profiles.replaceChildren();
  for(const p of w.profiles||[]){
   const li=this.doc.createElement('li');
   const name=this.doc.createElement('span');name.textContent=`${p.preferred?'★ ':''}${p.ssid}`;li.append(name);
   if(!p.preferred){const prefer=this.doc.createElement('button');prefer.textContent='设为首选';prefer.dataset.op='prefer';prefer.dataset.ssid=p.ssid;prefer.disabled=blocked;li.append(prefer);}
   const remove=this.doc.createElement('button');remove.textContent='删除';remove.className='stop-button';remove.dataset.op='remove';remove.dataset.ssid=p.ssid;remove.disabled=blocked;li.append(remove);
   profiles.append(li);
  }
  this.el('wifi-profiles-note').textContent=supported?`已保存 ${w.profiles?.length??0}/${w.profile_limit??6} 个；首选最先尝试，之后依次尝试，最后回落到出厂配置（${w.factory_count??0} 个）。`:'—';
  const scan=this.el('wifi-scan-list');scan.replaceChildren();
  for(const s of w.scan||[]){
   const li=this.doc.createElement('li');
   const pick=this.doc.createElement('button');pick.textContent=`${s.saved?'★ ':''}${s.ssid} · ${s.rssi_dbm} dBm${s.secure?' · 加密':' · 开放'}`;pick.dataset.ssid=s.ssid;pick.disabled=blocked;li.append(pick);
   scan.append(li);
  }
  this.el('wifi-scan-note').textContent=w.scanning?'正在扫描…':Array.isArray(w.scan)&&w.scan.length?`扫描结果 ${w.scan.length} 个（${Math.round((w.scan_age_ms??0)/1000)} 秒前），点击填入名称`:'尚无扫描结果';
 }
}
