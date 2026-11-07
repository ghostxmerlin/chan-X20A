import { renderM12, renderThermal } from '/m12-ui.js';
const $ = id => document.getElementById(id);
const text = (id, value) => { const el = $(id); const next = String(value ?? '—'); if (el.textContent !== next) el.textContent = next; };
const num = (n, digits = 1) => Number.isFinite(n) ? n.toFixed(digits) : '—';
const bool = (v, yes, no) => v === true ? yes : v === false ? no : '—';
const duration = seconds => { const s = Math.max(0, Math.floor(seconds || 0)); return s >= 3600 ? `${Math.floor(s / 3600)}h ${Math.floor(s / 60) % 60}m` : `${Math.floor(s / 60)}m ${s % 60}s`; };
const age = (t, stamp) => stamp > 0 && t >= stamp ? `${duration((t - stamp) / 1e6)} 前` : '尚无数据';
let source, state, received = 0, paused = false, generation = 0, frameSeq = null, frameURL = null, framePending = false;
let lastBoot = '', lastEvent = -1, listening = false, audioContext, audioAbort, audioGeneration = 0;

function clearFrame() {
  generation++; framePending = false; frameSeq = null;
  $('camera').hidden = true; $('camera').removeAttribute('src'); $('camera-empty').hidden = false;
  if (frameURL) URL.revokeObjectURL(frameURL); frameURL = null;
}
async function updateFrame(s, fresh) {
  if (!fresh) { if (frameURL || framePending) clearFrame(); return; }
  if (framePending || frameSeq === s.frame?.seq) return;
  framePending = true; const own = generation;
  try {
    const res = await fetch('/api/frame', { cache: 'no-store' });
    if (!res.ok) throw new Error('画面不可用');
    const blob = await res.blob();
    if (own !== generation || paused) return;
    const nextURL = URL.createObjectURL(blob); const img = new Image(); img.src = nextURL;
    try { await img.decode(); } catch { URL.revokeObjectURL(nextURL); throw new Error('画面解码失败'); }
    if (own !== generation || paused) { URL.revokeObjectURL(nextURL); return; }
    const previous = frameURL; frameURL = nextURL; frameSeq = Number(res.headers.get('X-Frame-Seq'));
    $('camera').src = nextURL; $('camera').hidden = false; $('camera-empty').hidden = true;
    if (previous) URL.revokeObjectURL(previous);
  } catch { if (own === generation) { clearFrame(); text('camera-status', '画面暂不可用'); } }
  finally { if (own === generation) framePending = false; }
}
function plot(id, points, fields, limit, end) {
  const canvas = $(id), ratio = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== Math.round(w * ratio) || canvas.height !== Math.round(h * ratio)) { canvas.width = Math.round(w * ratio); canvas.height = Math.round(h * ratio); }
  const ctx = canvas.getContext('2d'); ctx.setTransform(ratio, 0, 0, ratio, 0, 0); ctx.clearRect(0, 0, w, h);
  const left = 34, right = w - 8, top = 10, bottom = h - 22, mid = (top + bottom) / 2;
  ctx.lineWidth = 1; ctx.strokeStyle = '#244c32'; ctx.fillStyle = '#72a980'; ctx.font = '12px monospace';
  for (const [y, label] of [[top, limit], [mid, 0], [bottom, -limit]]) {
    ctx.beginPath(); ctx.moveTo(left, y + .5); ctx.lineTo(right, y + .5); ctx.stroke(); ctx.fillText(String(label), 0, y + 4);
  }
  ctx.fillText('−4s', left, h - 2); ctx.fillText('现在', right - 28, h - 2);
  if (!points.length) return;
  const x = t => left + ((t - (end - 4)) / 4) * (right - left), y = value => mid - value / limit * (bottom - top) / 2;
  ctx.save(); ctx.beginPath(); ctx.rect(left, top, right - left, bottom - top); ctx.clip();
  const colors = ['#79ff97', '#72d8ff', '#d3a7ff'];
  fields.forEach((field, index) => {
    ctx.beginPath(); ctx.strokeStyle = colors[index]; ctx.lineWidth = 1.25; let previous;
    for (const p of points) {
      const value = field(p);
      if (!Number.isFinite(value)) { previous = undefined; continue; }
      if (previous === undefined || p.t - previous > .15) ctx.moveTo(x(p.t), y(value)); else ctx.lineTo(x(p.t), y(value));
      previous = p.t;
    }
    ctx.stroke();
  });
  ctx.restore();
}
function render() {
  const bridge = !paused && Date.now() - received < 2500;
  const connected = bridge && state?.online;
  const fresh = connected && state.fresh.telemetry;
  const cameraFresh = connected && state.fresh.camera;
  const audioFresh = connected && state.fresh.audio;
  const t = fresh ? state.telemetry : {}, motor = t.remote?.motor || {};
  $('status-dot').classList.toggle('live', !!fresh);
  text('status', paused ? '监控已暂停' : fresh ? '实时连接' : connected ? '数据过期' : '等待机器人');
  text('firmware', state?.firmware || 'StackChan');
  text('notice', paused ? '此页面已暂停；其他已打开的观察页面仍可继续采集。' : !bridge ? '本机数据桥未连接，正在重试…' : !connected ? (state?.error || '正在验证机器人并建立连接…') : !fresh ? '遥测未更新，旧数据已隐藏。' : '只读监控 · 声音需手动开启 · 不改变机器人工作模式');
  $('notice').classList.toggle('warn', !paused && !fresh);
  text('camera-status', cameraFresh ? '实时画面' : paused ? '已暂停' : (state?.cameraError || '等待新画面'));
  text('frame-meta', cameraFresh ? `#${state.frame.seq} · ${num(state.frame.bytes / 1024, 1)} KB` : '—');
  void updateFrame(state, cameraFresh);
  text('uptime', fresh ? '运行 ' + duration(t.timestamp_us / 1e6) : '—');
  text('battery', t.power?.valid ? `${num(t.power.battery_percent, 0)}% · ${bool(t.power.charging, '充电中', '未充电')}` : '—');
  text('wifi', t.health ? `${num(t.health.wifi_rssi_dbm, 0)} dBm · CH ${t.health.wifi_channel ?? '—'}` : '—');
  text('light', t.light?.valid ? `${num(t.light.lux)} lux` : '—');
  text('proximity', t.proximity?.valid ? `${t.proximity.raw} raw` : '—');
  text('heap', t.health ? `${num(t.health.heap_free / 1024, 0)} KB / PSRAM ${num(t.health.psram_free / 1048576, 1)} MB` : '—');
  text('remote', t.remote ? `${bool(t.remote.valid, '收到数据', '等待数据')} · ${t.remote.rx_packets ?? 0} 包` : '—');
  text('display', bool(t.display?.enabled, '开启', '关闭'));
  text('motor-status', bool(motor.enabled, '控制启用', '控制停用'));
  text('yaw', motor.yaw?.valid ? `${num(motor.yaw.angle_deg)}°` : '无有效反馈');
  text('pitch', motor.pitch?.valid ? `${num(motor.pitch.angle_deg)}°` : '无有效反馈');
  $('yaw').classList.toggle('warn', !!fresh && !motor.yaw?.valid); $('pitch').classList.toggle('warn', !!fresh && !motor.pitch?.valid);
  const pose = { feedback_unavailable: '反馈不可用', complete: '动作已完成', completed: '动作已完成', moving: '运动中', waiting: '等待反馈', idle: '空闲', cancelled: '已取消' };
  text('startup-pose', pose[motor.startup_pose_state] || motor.startup_pose_state);
  text('pitch-saved', t.pitch_setting ? `${num(t.pitch_setting.configured_deg)}° · ${bool(t.pitch_setting.persisted, '已保存', '未保存')}` : '—');
  for (const id of ['system-panel','servo-panel','m12-panel','asr-panel','mag-panel']) $(id).classList.toggle('stale', !fresh);
  text('db', audioFresh ? num(state.audio.db) : '—');
  const meterValue = audioFresh ? Math.max(-60, Math.min(0, state.audio.db)) : -60;
  $('meter-fill').style.width = `${(meterValue + 60) / 60 * 100}%`;
  $('meter-fill').parentElement.setAttribute('aria-valuenow', meterValue);
  text('audio-status', audioFresh ? '实时采集' : state?.audio?.error || '等待音频');
  text('audio-gaps', audioFresh ? `缺包 ${state.audio.gaps}` : '缺包 —');
  $('listen').disabled = !audioFresh;
  if (!audioFresh && listening) stopListening('连接中断，监听已关闭');
  const motion = fresh ? state.motion : [], end = motion.at(-1)?.t || 0;
  for (const key of ['accel', 'gyro']) {
    const base = key === 'accel' ? 20 : 180;
    const maximum = Math.max(base, ...motion.flatMap(p => (p[key] || []).map(Math.abs)));
    const scale = Math.ceil(maximum / base) * base;
    plot(key, motion, [0,1,2].map(i => p => p[key]?.[i]), scale, end);
    text(key + '-value', t.imu?.valid ? t.imu[key].map(v => num(v)).join(' / ') : '—');
  }
  const history = audioFresh ? state.audio.history : [];
  plot('wave', history, [p => p.min, p => p.max], 1, history.at(-1)?.t || 0);
  const mag = t.magnetometer?.valid ? t.magnetometer.field_ut : null;
  ['x','y','z'].forEach((axis, i) => text('mag-' + axis, mag ? num(mag[i]) : '—'));
  $('mag-arrow').hidden = !mag;
  if (mag) $('mag-arrow').style.transform = `rotate(${Math.atan2(mag[1], mag[0]) * 180 / Math.PI + 90}deg)`;
  text('asr-state', t.asr ? `${bool(t.asr.valid, '串口就绪', '串口异常')} · ${t.asr.frames} 帧` : '—');
  const last = t.asr?.events?.at(-1);
  text('asr-last', last ? `${last.word || last.id} · ${age(t.timestamp_us, last.timestamp_us)}` : '尚无指令');
  renderM12(state, fresh, bridge);
  renderThermal(t.temperature, fresh ? state.thermalHistory : [], t.timestamp_us);
  const asrList = $('asr-events'); asrList.replaceChildren();
  for (const event of (t.asr?.events || []).slice(-6).reverse()) {
    const li = document.createElement('li'), word = document.createElement('span'), when = document.createElement('span');
    word.textContent = event.word || `指令 ${event.id}`; when.textContent = age(t.timestamp_us, event.timestamp_us);
    li.append(word, when); asrList.append(li);
  }
  const events = state?.events || [];
  if (events.at(-1)?.id !== lastEvent) {
    lastEvent = events.at(-1)?.id; const list = $('events'); list.replaceChildren();
    for (const e of events.slice().reverse()) {
      const li = document.createElement('li'), time = document.createElement('time'), message = document.createElement('span');
      time.textContent = new Date(e.at).toLocaleTimeString('zh-CN', { hour12: false }); time.dateTime = new Date(e.at).toISOString();
      message.textContent = e.text; li.append(time, message); list.append(li);
    }
    text('event-count', `${events.length} 条`);
  }
  text('freshness', fresh ? `更新于 ${new Date(state.receivedAt).toLocaleTimeString('zh-CN', { hour12: false })}` : '实时读数不可用');
}
function connect() {
  source?.close(); source = new EventSource('/api/events');
  source.onopen = () => { lastEvent = -1; };
  source.onmessage = event => {
    try {
      const next = JSON.parse(event.data);
      if (next.boot && lastBoot && next.boot !== lastBoot) { clearFrame(); stopListening('机器人重启，监听已关闭'); }
      if (next.boot) lastBoot = next.boot;
      state = next; received = Date.now(); render();
    } catch { text('notice', '数据格式异常，等待下一帧'); }
  };
  source.onerror = () => { received = 0; clearFrame(); stopListening('连接中断，监听已关闭'); render(); };
}
function stopListening(reason = '外放关闭') {
  listening = false; audioGeneration++; audioAbort?.abort(); audioAbort = null;
  audioContext?.close().catch(() => {}); audioContext = null;
  text('listen', '开启监听'); $('listen').setAttribute('aria-pressed', 'false'); text('listen-state', reason);
}
async function startListening() {
  const own = ++audioGeneration; listening = true;
  text('listen', '关闭监听'); $('listen').setAttribute('aria-pressed', 'true'); text('listen-state', '准备播放…');
  try {
    const context = new AudioContext({ latencyHint: 'interactive' }); audioContext = context;
    await context.resume();
    if (own !== audioGeneration) return;
    if (context.state !== 'running') throw new Error('浏览器未允许播放');
    const controller = new AbortController(); audioAbort = controller;
    const res = await fetch('/api/listen', { signal: controller.signal });
    if (!res.ok) throw new Error('音频暂不可用');
    const reader = res.body.getReader(); let pending = new Uint8Array(0), nextAt = 0, previousSeq = null;
    text('listen-state', '监听中 · 第一通道');
    while (own === audioGeneration) {
      const { value, done } = await reader.read(); if (done) throw new Error('音频中断');
      const joined = new Uint8Array(pending.length + value.length); joined.set(pending); joined.set(value, pending.length); pending = joined;
      if (pending.length > 262144) throw new Error('音频积压');
      while (pending.length >= 32) {
        const view = new DataView(pending.buffer, pending.byteOffset, pending.byteLength);
        const seq = view.getUint32(4, true), rate = view.getUint32(16, true), frames = view.getUint32(20, true);
        const channels = view.getUint16(24, true), bytes = view.getUint32(28, true);
        if (view.getUint32(0, false) !== 0x53434131 || view.getUint16(26, true) !== 16 || rate < 8000 || rate > 96000 || channels < 1 || channels > 8 || frames < 1 || frames > rate || bytes !== frames * channels * 2 || bytes > 1048576) throw new Error('音频格式无效');
        if (pending.length < bytes + 32) break;
        const delta = previousSeq === null ? 1 : (seq - previousSeq) >>> 0; previousSeq = seq;
        if (nextAt - context.currentTime > .3) throw new Error('播放延迟过高，监听已关闭');
        if (delta !== 1 || nextAt < context.currentTime) nextAt = Math.max(nextAt, context.currentTime + .06);
        const buffer = context.createBuffer(1, frames, rate), channel = buffer.getChannelData(0);
        for (let i = 0; i < frames; i++) channel[i] = view.getInt16(32 + i * channels * 2, true) / 32768;
        const node = context.createBufferSource(); node.buffer = buffer; node.connect(context.destination); node.start(nextAt); nextAt += frames / rate;
        node.onended = () => node.disconnect(); pending = pending.subarray(bytes + 32);
      }
    }
  } catch (error) { if (own === audioGeneration) stopListening(error.name === 'AbortError' ? '外放关闭' : error.message); }
}
$('listen').addEventListener('click', () => { if (listening) stopListening(); else void startListening(); });
$('pause').addEventListener('click', () => {
  paused = !paused; $('pause').setAttribute('aria-pressed', String(paused)); text('pause', paused ? '恢复监控' : '暂停监控');
  clearFrame(); stopListening(); received = 0;
  if (paused) source?.close(); else connect(); render();
});
document.addEventListener('visibilitychange', () => { if (document.hidden) stopListening('页面在后台，监听已关闭'); });
window.addEventListener('pagehide', () => { source?.close(); stopListening(); clearFrame(); });
window.addEventListener('pageshow', event => { if (event.persisted && !paused) connect(); });
window.addEventListener('resize', render);
setInterval(render, 500);
connect(); render();
