const $ = id => document.getElementById(id);
const text = (id, value) => { $(id).textContent = value ?? '—'; };
function surface(id) {
  const canvas = $(id), width = canvas.clientWidth, height = canvas.clientHeight, dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) { canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr); }
  const context = canvas.getContext('2d'); context.setTransform(dpr,0,0,dpr,0,0); context.clearRect(0,0,width,height);
  return { context, width, height };
}
const age = seconds => seconds == null ? '尚无结果' : seconds < 60 ? `${seconds.toFixed(1)} 秒前` : `${Math.floor(seconds / 60)} 分钟前`;
const M12_ERRORS = {
  noface: '画面里没有可用的脸（或者学习途中人离开了、换了人）',
  conflict: '和库里另一个名字太像，没有保存',
  full: '人脸库已满（最多 16 个名字），请先删除一个',
  busy: 'M12 正忙，请稍后再试',
  standby: 'M12 处于待机，请先开启检测',
  hot: 'M12 过热暂停中（≥ 90 °C），降温后再试',
  timeout: 'M12 在 3 秒内没有完成',
  client_timeout: 'M12 没有及时回复',
  badarg: '名字不合法，或者库里没有这个名字',
  nomodel: 'M12 的识别模型没有加载',
  io: 'M12 的人脸库写入失败，这次修改已撤销',
  unknown: 'M12 不认识这条命令',
  toolong: '请求太长',
  badjson: '请求格式错误',
};
export function m12ErrorText(command) {
  if (!command?.err) return '';
  const base = M12_ERRORS[command.err] || `错误 ${command.err}`;
  return command.err === 'conflict' && command.name ? `和库里的「${command.name}」太像，没有保存` : base;
}
const genders = {m:'男', f:'女', '?':'性别不确定'};
const ages = {child:'儿童', young:'青年', adult:'成年', elder:'老年'};
const attrs = f => [genders[f.gender], ages[f.age_group]].filter(Boolean).join(' · ');
export function renderM12(state, fresh, bridge = true) {
  const summary = fresh ? state?.m12 : null, m = fresh ? state?.telemetry?.m12 : null;
  const status = summary?.status || 'disconnected';
  const labels = {disconnected:'数据未连接',serial_unavailable:'串口未就绪',offline:'M12 离线 · 6 秒内没有心跳',hot:'M12 过热暂停（≥ 90 °C）',standby:'待机',
    starting:'正在开启检测 · 等待 M12 确认',stopping:'正在待机 · 等待 M12 确认',stalled:'检测已开 · 结果已过期',detecting:'检测中'};
  text('m12-state', labels[status]); text('m12-visual-state', labels[status]);
  $('m12-state').classList.toggle('warn', ['stalled','serial_unavailable','offline','hot'].includes(status));
  const hb = m?.heartbeat;
  const temperature = summary?.temperature;
  text('m12-temperature', temperature != null ? `${temperature.toFixed(0)} °C` : '—');
  text('m12-temperature-note', temperature != null ? `芯片温度 · 心跳 ${((m.hb_age_ms ?? 0)/1000).toFixed(1)} 秒前${summary.tier === 3 ? ' · 已暂停检测，降到 87 °C 以下自动恢复' : ''}` : status === 'offline' ? '收不到心跳' : '等待 M12 心跳');
  $('m12-temperature').classList.toggle('warn', temperature != null && temperature >= 90);
  const hardware = bridge ? state?.m12?.system : null;
  text('m12-network', hardware?.connected ? '已连接 · 独立 Wi-Fi 采集' : 'M12 只由 Grove 供电时没有网络，属正常');
  text('m12-process', m?.version ? `m12d ${m.version}${hb ? ` · 运行 ${Math.floor(hb.up/60)} 分钟 · ${hb.mhz} MHz · 内存 ${Math.round(hb.mem/1024)} MB` : ''}` : hb ? `m12d · 运行 ${Math.floor(hb.up/60)} 分钟` : '—');
  text('m12-rate-label', '检测帧率');
  const detecting = status === 'detecting';
  text('m12-fps', summary?.fps != null ? summary.fps.toFixed(1) : '—');
  text('m12-count', summary?.count ?? '—');
  const faces = summary?.faces || [], persons = summary?.persons || [];
  text('m12-confidence', faces.length ? `最高置信度 ${(Math.max(...faces.map(f => f.prob)) * 100).toFixed(1)}%` : persons.length ? `没有脸 · 看到 ${persons.length} 个人形（远处）` : detecting ? '画面里没有人' : '等待检测结果');
  const modes = {standby:'待机',detect:'检测',unknown:'未确认'};
  text('m12-request', m ? `请求 ${modes[m.requested_function] || '—'} · M12 确认 ${modes[m.function] || '—'}` : '—');
  text('m12-running', hb ? `${((m.hb_age_ms ?? 0)/1000).toFixed(1)} 秒前 · tier ${hb.tier} · 丢弃 ${hb.drop}` : '尚无心跳');
  text('m12-age', m ? `${m.rx_bytes} 字节` : '—');
  text('m12-result-age', fresh ? age(summary?.ageSeconds) : '—');
  const c = m?.counters;
  text('m12-counters', c ? `${c.lines} 行 / 坏行 ${c.invalid + c.overflow} / 超时 ${c.timeouts} / who ${c.who_sent}（忙 ${c.who_busy}）` : '—');
  const named = faces.filter(f => f.name);
  text('m12-identity', detecting ? faces.length ? faces.map(f => `#${f.id} ${f.name || '未认出'}${attrs(f) ? ' · ' + attrs(f) : ''}`).join('；') : '画面里没有脸' : '检测开启后，新出现的脸会自动认一次；名字随检测结果一直显示。');
  $('m12-identity').classList.toggle('known', named.length > 0);
  const {context:c2,width:w,height:h} = surface('m12-faces-canvas');
  c2.strokeStyle = '#163621'; c2.lineWidth = 1;
  for (let x = 0; x <= 4; x++) { c2.beginPath();c2.moveTo(w*x/4,0);c2.lineTo(w*x/4,h);c2.stroke(); }
  for (let y = 0; y <= 3; y++) { c2.beginPath();c2.moveTo(0,h*y/3);c2.lineTo(w,h*y/3);c2.stroke(); }
  c2.font='14px monospace'; c2.fillStyle='#72a980'; c2.fillText('0,0',8,20); c2.fillText('640,480',w-70,h-10);
  if (!faces.length && !persons.length) { c2.textAlign='center'; c2.fillText(detecting ? '画面里没有人' : labels[status],w/2,h/2); c2.textAlign='left'; }
  persons.forEach(p => { c2.setLineDash([6,4]); c2.strokeStyle='#3f8f57'; c2.lineWidth=1; c2.strokeRect(p.x/640*w,p.y/480*h,p.w/640*w,p.h/480*h); c2.setLineDash([]); });
  const details=$('m12-face-details');details.replaceChildren();
  faces.forEach(f=>{
    const x=f.x/640*w,y=f.y/480*h,bw=f.w/640*w,bh=f.h/480*h;
    c2.strokeStyle=f.name?'#ffd36b':'#79ff97';c2.lineWidth=2;c2.strokeRect(x,y,bw,bh);
    c2.fillStyle=c2.strokeStyle;c2.fillText(`#${f.id} ${f.name || `${(f.prob*100).toFixed(0)}%`}`,Math.max(4,Math.min(x,w-120)),Math.max(18,y-7));
    const row=document.createElement('p');row.textContent=`#${f.id} ${f.name || '未认出'}${attrs(f) ? ' · ' + attrs(f) : ''}${Number.isFinite(f.age) ? ` · 约 ${f.age} 岁` : ''} · 位置 ${Math.round(f.x)}, ${Math.round(f.y)} · 尺寸 ${Math.round(f.w)}×${Math.round(f.h)} · ${(f.prob*100).toFixed(1)}%`;details.append(row);
  });
  persons.forEach(p=>{const row=document.createElement('p');row.textContent=`人形 #${p.id}${p.face>=0?` · 对应脸 #${p.face}`:''} · 位置 ${Math.round(p.x)}, ${Math.round(p.y)} · 尺寸 ${Math.round(p.w)}×${Math.round(p.h)}`;details.append(row);});
}
const opNames = {learn:'学习', forget:'删除', faces:'刷新列表', who:'认一下', ping:'ping'};
// Face library on the M12 (learn / forget / faces / who). Results come back through telemetry
// m12.commands; requests are only sent on clicks, never on render or reconnect.
export class M12LibraryPanel {
  constructor(doc, send = async body => {
    const response=await fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(7000)});
    if(!response.ok) throw new Error(await response.text());return response.json();
  }) {
    this.doc=doc;this.send=send;this.pending=false;this.loadedBoot='';
    this.el('m12-learn').addEventListener('click',()=>this.act('m12_learn',{name:this.el('m12-name').value.trim()}));
    this.el('m12-who').addEventListener('click',()=>this.act('m12_who'));
    this.el('m12-faces').addEventListener('click',()=>this.act('m12_faces'));
    this.el('m12-library-list').addEventListener('click',event=>{
      const name=event.target?.dataset?.forget;
      if(name!==undefined)this.act('m12_forget',{name});
    });
  }
  el(id){return this.doc.getElementById(id);}
  async act(action,params={}){
    if(!this.ready||this.pending)return;
    if(action==='m12_learn'&&!params.name){this.el('m12-library-note').textContent='请先填写名字';return;}
    const boot=this.state.boot;this.pending=true;this.el('m12-library-note').textContent='正在发送…';this.render(this.state,this.fresh);
    try{const r=await this.send({action,boot,...params});this.sentId=r.id;this.el('m12-library-note').textContent=this.state.boot===boot?`已发送${opNames[r.op]||''}，等待 M12 回复…`:'机器人已重启，请核对状态';}
    catch(e){this.el('m12-library-note').textContent=e.name==='TimeoutError'?'结果未确认，请查看状态；不会自动重发':e.message;}
    finally{this.pending=false;this.render(this.state,this.fresh);}
  }
  render(state,fresh){
    this.state=state;this.fresh=fresh;
    const m=state?.telemetry?.m12||{},summary=state?.m12||{};
    this.ready=!!fresh&&m.protocol==='m12d'&&m.online===true;
    for(const id of ['m12-name','m12-learn','m12-who','m12-faces'])this.el(id).disabled=!this.ready||this.pending;
    if(this.ready&&this.loadedBoot!==state.boot){this.loadedBoot=state.boot;this.act('m12_faces');}   // one read per robot boot
    const list=this.el('m12-library-list');list.replaceChildren();
    const library=summary.library;
    if(!library){const li=document.createElement('li');li.textContent=this.ready?'尚未读取人脸库':'M12 在线后可读取';list.append(li);}
    else if(!library.length){const li=document.createElement('li');li.textContent='人脸库是空的';list.append(li);}
    else for(const e of library){
      const li=document.createElement('li');const span=document.createElement('span');span.textContent=`${e.name} · ${e.samples} 个样本`;
      const b=document.createElement('button');b.textContent='删除';b.dataset.forget=e.name;b.disabled=!this.ready||this.pending;b.className='stop-button';
      li.append(span,b);list.append(li);
    }
    const latest=(summary.commands||[]).find(c=>['learn','forget','who','faces'].includes(c.op));
    if(latest&&latest.id===this.sentId&&latest.state!=='pending'){
      const what=opNames[latest.op]||latest.op;
      this.el('m12-library-note').textContent=latest.state==='ok'
        ?(latest.op==='learn'?`已学习「${latest.name}」，共 ${latest.samples} 个样本`:latest.op==='forget'?`已删除「${latest.name}」`:latest.op==='who'?(latest.name?`认出：${latest.name}（相似度 ${(latest.sim*100).toFixed(0)}%）`:`没有认出（相似度 ${((latest.sim??0)*100).toFixed(0)}%）`):'人脸库已刷新')
        :`${what}失败：${m12ErrorText(latest)}`;
      this.sentId=null;
    }
  }
}
export function renderThermal(thermal, history = [], nowUs = 0) {
  const valid=thermal?.valid && Number.isFinite(thermal.celsius) && nowUs-thermal.timestamp_us < 3000000;
  text('chip-temperature', valid ? `${thermal.celsius.toFixed(1)} °C` : thermal ? '读取不可用' : '尚未上报');
  text('chip-temperature-note', thermal ? 'ESP32-S3 芯片 · 非室温/壳温' : 'ESP32-S3 · 等待温度遥测');
  const {context:c,width:w,height:h}=surface('chip-thermal');
  const left=30,right=w-16,top=8,bottom=h-16;
  c.font='12px monospace';c.fillStyle='#72a980';c.strokeStyle='#244c32';c.lineWidth=1;
  for(const value of [20,60,100]) { const y=bottom-(value-20)/80*(bottom-top);c.beginPath();c.moveTo(left,y);c.lineTo(right,y);c.stroke();c.fillText(String(value),0,y+4); }
  if(!valid || !history.length) return;
  const end=nowUs/1e6;
  c.strokeStyle='#79ff97';c.lineWidth=1.5;c.beginPath();let previous;
  for(const p of history) {
    const time=p.timestamp_us/1e6,x=left+(time-(end-120))/120*(right-left),y=bottom-(p.celsius-20)/80*(bottom-top);
    if(x<left || x>right) continue;
    if(previous===undefined || time-previous>3)c.moveTo(x,y);else c.lineTo(x,y);previous=time;
  }
  c.stroke();
}
