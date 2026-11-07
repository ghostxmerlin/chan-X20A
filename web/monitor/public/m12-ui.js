const $ = id => document.getElementById(id);
const text = (id, value) => { $(id).textContent = value ?? '—'; };
function surface(id) {
  const canvas = $(id), width = canvas.clientWidth, height = canvas.clientHeight, dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) { canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr); }
  const context = canvas.getContext('2d'); context.setTransform(dpr,0,0,dpr,0,0); context.clearRect(0,0,width,height);
  return { context, width, height };
}
const age = seconds => seconds == null ? '尚无结果' : seconds < 60 ? `${seconds.toFixed(1)} 秒前` : `${Math.floor(seconds / 60)} 分钟前`;
export function renderM12(state, fresh, bridge = true) {
  const summary = fresh ? state?.m12 : null, m = fresh ? state?.telemetry?.m12 : null;
  const status = summary?.status || 'disconnected';
  const labels = {disconnected:'数据未连接',serial_unavailable:'串口未就绪',standby:'已请求待机 · 曾收到待机报告',standby_pending:'已请求待机 · 等待 M12 回应',starting:'等待本次检测结果',stalled:'检测结果已过期',detecting:'检测中'};
  text('m12-state', labels[status]); text('m12-visual-state', labels[status]);
  $('m12-state').classList.toggle('warn', ['stalled','serial_unavailable','standby_pending'].includes(status));
  const hardware = bridge ? state?.m12?.system : null;
  const temperature = hardware?.temperature;
  const thermalValid = temperature?.valid && Date.now()-temperature.at < 6000;
  text('m12-temperature', thermalValid ? `${temperature.celsius.toFixed(0)} °C` : '—');
  text('m12-temperature-note', thermalValid ? '芯片温度 · 实时' : bridge ? temperature?.reason || hardware?.error || '等待网络数据' : '监控已暂停或连接中断');
  $('m12-temperature').classList.toggle('warn', thermalValid && temperature.celsius >= 90);
  text('m12-network', hardware?.connected ? '已连接 · 独立 Wi-Fi 采集' : hardware?.error || '未连接');
  const processes = {audio_fft:'Audio FFT（待机）',face_detector:'Face Detector',face_recognition:'Face Recognition',none:'未发现视觉进程'};
  text('m12-process', hardware?.connected ? processes[hardware.mode] || hardware.mode : '—');
  text('m12-fps', summary?.fps != null ? `${summary.fps.toFixed(1)}` : status === 'detecting' ? '计量中' : '—');
  text('m12-count', summary?.count ?? '—');
  const faces = summary?.faces || [];
  text('m12-confidence', faces.length ? `最高置信度 ${(Math.max(...faces.map(f => f.prob)) * 100).toFixed(1)}%` : status === 'detecting' ? '本次未检测到人脸' : '等待检测结果');
  const modes = {standby:'待机',face_detector:'人脸检测',face_recognition:'人脸识别'};
  text('m12-request', modes[m?.function] || m?.function);
  text('m12-running', m?.running || '尚无报告');
  const now = state?.telemetry?.timestamp_us || 0;
  text('m12-age', m?.last_rx_us > 0 ? age(Math.max(0,(now-m.last_rx_us)/1e6)) : '—');
  text('m12-result-age', fresh ? age(summary?.ageSeconds) : '—');
  text('m12-counters', m ? `${m.results} / ${m.errors + m.invalid_lines + m.overflow_lines}` : '—');
  const {context:c,width:w,height:h} = surface('m12-faces-canvas');
  c.strokeStyle = '#163621'; c.lineWidth = 1;
  for (let x = 0; x <= 4; x++) { c.beginPath();c.moveTo(w*x/4,0);c.lineTo(w*x/4,h);c.stroke(); }
  for (let y = 0; y <= 3; y++) { c.beginPath();c.moveTo(0,h*y/3);c.lineTo(w,h*y/3);c.stroke(); }
  c.font='14px monospace'; c.fillStyle='#72a980'; c.fillText('0,0',8,20); c.fillText('640,480',w-70,h-10);
  if (!faces.length) { c.textAlign='center'; c.fillText(status === 'detecting' ? '本次未检测到人脸' : labels[status],w/2,h/2); c.textAlign='left'; }
  const details=$('m12-face-details');details.replaceChildren();
  faces.forEach((f,i)=>{
    const x=f.x/640*w,y=f.y/480*h,bw=f.w/640*w,bh=f.h/480*h;
    c.strokeStyle='#79ff97';c.lineWidth=2;c.strokeRect(x,y,bw,bh);
    c.fillStyle='#79ff97';c.fillText(`#${i+1} ${(f.prob*100).toFixed(1)}%`,Math.max(4,Math.min(x,w-100)),Math.max(18,y-7));
    const row=document.createElement('p');row.textContent=`#${i+1} 位置 ${Math.round(f.x)}, ${Math.round(f.y)} · 尺寸 ${Math.round(f.w)}×${Math.round(f.h)} · ${(f.prob*100).toFixed(1)}%`;details.append(row);
  });
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
