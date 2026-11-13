import { ControlPanel, InteractionPanel } from '/control-ui.js';
import { VoicePanel } from '/voice-ui.js';
import { EyesPanel } from '/eyes-ui.js';
import { HearingPanel } from '/hearing-ui.js';
import { ServoTuningPanel } from '/servo-tuning-ui.js';
import { WifiPanel } from '/wifi-ui.js';
import { renderM12, renderThermal, M12LibraryPanel } from '/m12-ui.js';
import { SwitchBar } from '/switches-ui.js';
import { SceneMinutesPanel } from '/scene-ui.js';
import { SayPanel } from '/say-ui.js';
import { SpeechSettings } from '/speech-settings.js';
const $ = id => document.getElementById(id);
const text = (id, value) => { const el = $(id); const next = String(value ?? '—'); if (el.textContent !== next) { el.textContent = next; el.title = next; } };
const num = (n, digits = 1) => Number.isFinite(n) ? n.toFixed(digits) : '—';
const bool = (v, yes, no) => v === true ? yes : v === false ? no : '—';
const duration = seconds => { const s = Math.max(0, Math.floor(seconds || 0)); return s >= 3600 ? `${Math.floor(s / 3600)}h ${Math.floor(s / 60) % 60}m` : `${Math.floor(s / 60)}m ${s % 60}s`; };
const age = (t, stamp) => stamp > 0 && t >= stamp ? `${duration((t - stamp) / 1e6)} 前` : '尚无数据';
const controls = new ControlPanel(document);
const interaction = new InteractionPanel(document);
const voices = new VoicePanel(document);
const eyes = new EyesPanel(document);
const hearing = new HearingPanel(document);
const servoTuning = new ServoTuningPanel(document);
const wifi = new WifiPanel(document);
const m12Library = new M12LibraryPanel(document);
const switches = new SwitchBar(document);
const say = new SayPanel(document);
new SceneMinutesPanel(document);
const speechSettings = new SpeechSettings(document);
for (const button of document.querySelectorAll('[data-open]')) {
  button.addEventListener('click', () => $(button.dataset.open).showModal());
}
// Keep the complete text available when dense status rows are ellipsized.
const tooltipObserver = new MutationObserver(records => {
  for (const record of records) {
    const el = record.target.nodeType === Node.TEXT_NODE ? record.target.parentElement : record.target;
    if (el.matches('.panel p, .panel dd, .panel-head > span, .identity-row p') && !el.hasAttribute('data-detail-title')) el.title = el.textContent;
  }
});
tooltipObserver.observe(document.querySelector('.dashboard'), { subtree: true, childList: true, characterData: true });
let source, state, received = 0, paused = false, pageHidden = false, previewRequested = false, generation = 0, frameSeq = null, frameURL = null, framePending = false;
let lastBoot = '', lastEvent = -1, lastFiltered = 0, logFilter = 'all';
// Combined log: ASR commands, hearing (speech / link / gaps), sound events and system state changes.
const LOG_GROUPS = { asr: ['asr'], hearing: ['hearing', 'response'], response: ['response'], sound: ['sound'], tingwu: ['tingwu'] };
const LOG_TAGS = { asr: 'ASR', hearing: '听觉', sound: '声音', response: '回应', tingwu: '听悟' };
const activityKinds = new Set(Object.values(LOG_GROUPS).flat());
const inGroup = (kind, filter) => filter === 'all' || (filter === 'system' ? !activityKinds.has(kind) : filter === 'non-system' ? activityKinds.has(kind) : (LOG_GROUPS[filter] || []).includes(kind));
try {
  const saved = localStorage.getItem('stackchan.logFilter');
  if (['all', 'non-system', 'system', ...Object.keys(LOG_GROUPS)].includes(saved)) logFilter = saved;
} catch {}
for (const button of document.querySelectorAll('[data-log-filter]')) button.setAttribute('aria-pressed', String(button.dataset.logFilter === logFilter));
for (const button of document.querySelectorAll('[data-log-filter]')) button.addEventListener('click', () => {
  logFilter = button.dataset.logFilter; lastEvent = -1;
  try { localStorage.setItem('stackchan.logFilter', logFilter); } catch {}
  for (const other of document.querySelectorAll('[data-log-filter]')) other.setAttribute('aria-pressed', String(other === button));
  render();
});

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
    if (own !== generation || paused || !previewRequested) return;
    const nextURL = URL.createObjectURL(blob); const img = new Image(); img.src = nextURL;
    try { await img.decode(); } catch { URL.revokeObjectURL(nextURL); throw new Error('画面解码失败'); }
    if (own !== generation || paused || !previewRequested) { URL.revokeObjectURL(nextURL); return; }
    const previous = frameURL; frameURL = nextURL; frameSeq = Number(res.headers.get('X-Frame-Seq'));
    $('camera').src = nextURL; $('camera').hidden = false; $('camera-empty').hidden = true;
    if (previous) URL.revokeObjectURL(previous);
  } catch { if (own === generation) { clearFrame(); text('camera-status', '画面暂不可用'); } }
  finally { if (own === generation) framePending = false; }
}
function render() {
  const bridge = !paused && Date.now() - received < 2500;
  const connected = bridge && state?.online;
  const fresh = connected && state.fresh.telemetry;
  const cameraFresh = previewRequested && connected && state.fresh.camera;
  const t = fresh ? state.telemetry : {}, motor = t.remote?.motor || {};
  $('status-dot').classList.toggle('live', !!fresh);
  text('status', paused ? '监控已暂停' : fresh ? '实时连接' : connected ? '数据过期' : '等待机器人');
  text('firmware', state?.firmware || 'StackChan');
  text('notice', paused ? '此页面已暂停；其他已打开的观察页面仍可继续采集。' : !bridge ? '本机数据桥未连接，正在重试…' : !connected ? (state?.error || '正在验证机器人并建立连接…') : !fresh ? '遥测未更新，旧数据已隐藏。' : '实时监控 · 点击控制按钮才发送操作');
  $('notice').classList.toggle('warn', !paused && !fresh);
  text('camera-toggle', previewRequested ? '停止传输' : '开始传输');
  $('camera-toggle').disabled = paused;
  $('camera-toggle').setAttribute('aria-pressed', String(previewRequested));
  text('camera-status', paused ? '已暂停' : !previewRequested ? '画面传输已停止' : cameraFresh ? '实时画面' : (state?.cameraError || '等待新画面'));
  text('camera-empty', previewRequested ? '等待机身画面' : '点击开始传输以查看机身画面');
  text('frame-meta', cameraFresh ? `#${state.frame.seq} · ${num(state.frame.bytes / 1024, 1)} KB` : '—');
  void updateFrame(state, cameraFresh);
  text('uptime', fresh ? '运行 ' + duration(t.timestamp_us / 1e6) : '—');
  text('battery', t.power?.valid ? `${num(t.power.battery_percent, 0)}% · ${bool(t.power.charging, '充电中', '未充电')}` : '—');
  text('wifi', t.wifi?.connected ? `${t.wifi.ssid || '—'} · ${num(t.wifi.rssi_dbm, 0)} dBm · CH ${t.wifi.channel ?? '—'}` : t.health ? `${num(t.health.wifi_rssi_dbm, 0)} dBm · CH ${t.health.wifi_channel ?? '—'}` : '—');
  text('light', t.light?.valid ? `${num(t.light.lux)} lux` : '—');
  text('proximity', t.proximity?.valid ? `${t.proximity.raw} raw` : '—');
  text('heap', t.health ? `${num(t.health.heap_free / 1024, 1)} KiB` : '—');
  text('heap-largest', t.health ? `${num(t.health.heap_largest_free / 1024, 1)} KiB` : '—');
  text('psram', t.health ? `${num(t.health.psram_free / 1048576, 2)} MiB` : '—');
  text('heap-minimum', t.health ? `${num(t.health.heap_min_free / 1024, 1)} KiB` : '—');
  text('remote', t.remote ? `${bool(t.remote.valid, '收到数据', '等待数据')} · ${t.remote.rx_packets ?? 0} 包` : '—');
  text('display', bool(t.display?.enabled, '开启', '关闭'));
  text('motor-status', bool(motor.enabled, '控制启用', '控制停用'));
  const turnBusy = motor.yaw_turn?.busy === true || ['queued', 'rotating', 'stopping'].includes(motor.yaw_turn?.state);
  const yawInRange = motor.yaw?.valid && Number.isFinite(motor.yaw.angle_deg) && Math.abs(motor.yaw.angle_deg) <= 128;
  text('yaw', motor.yaw_turn?.state==='power_cut' ? '断电保护' : turnBusy ? '旋转中' : yawInRange ? `${num(motor.yaw.angle_deg)}°` : motor.yaw?.valid && Number.isFinite(motor.yaw.angle_deg) ? '超出定位范围' : '无有效反馈');
  text('pitch', motor.pitch?.valid ? `${num(motor.pitch.angle_deg)}°` : '无有效反馈');
  $('yaw').classList.toggle('warn', !!fresh && !yawInRange && !turnBusy); $('pitch').classList.toggle('warn', !!fresh && !motor.pitch?.valid);
  const pose = { feedback_unavailable: '反馈不可用', complete: '动作已完成', completed: '动作已完成', moving: '运动中', waiting: '等待反馈', idle: '空闲', superseded: '已由新设置替代', cancelled: '已取消' };
  text('startup-pose', pose[motor.startup_pose_state] || motor.startup_pose_state);
  text('pitch-saved', t.pitch_setting ? `${num(t.pitch_setting.configured_deg)}° · ${bool(t.pitch_setting.persisted, '已保存', '未保存')}` : '—');
  for (const id of ['system-panel','servo-panel','m12-panel','log-panel']) $(id).classList.toggle('stale', !fresh);
  const mic = t.health?.mic_level_dbfs, micPeak = t.health?.mic_peak_dbfs;
  text('mic', Number.isFinite(mic) ? `${num(mic)} / 峰 ${num(micPeak)} dBFS` : '—');
  $('mic-fill').style.width = `${Number.isFinite(mic) ? (Math.max(-60, Math.min(0, mic)) + 60) / 60 * 100 : 0}%`;
  text('asr-state', t.asr ? `${bool(t.asr.valid, '串口就绪', '串口异常')} · ${t.asr.frames} 帧` : '—');
  controls.render(state, fresh && !document.hidden);
  interaction.render(state, fresh && !document.hidden);
  voices.render(state, fresh && !document.hidden);
  eyes.render(state, fresh && !document.hidden);
  hearing.render(state, fresh && !document.hidden);
  servoTuning.render(state, fresh && !document.hidden);
  wifi.render(state, fresh && !document.hidden);
  m12Library.render(state, fresh && !document.hidden);
  switches.render(state, fresh && !document.hidden);
  for (const mirror of document.querySelectorAll('[data-mirror]')) {
    const value = $(mirror.dataset.mirror).textContent;
    if (mirror.textContent !== value) mirror.textContent = value;
  }
  renderM12(state, fresh, bridge);
  renderThermal(t.temperature, fresh ? state.thermalHistory : [], t.timestamp_us);
  const events = state?.events || [];
  if (events.at(-1)?.id !== lastEvent || state?.filteredSpeech !== lastFiltered) {
    lastFiltered = state?.filteredSpeech;
    lastEvent = events.at(-1)?.id; const list = $('events'); list.replaceChildren();
    const shown = events.filter(e => inGroup(e.kind, logFilter)).sort((a, b) => b.at - a.at || b.id - a.id);
    for (const e of shown) {
      const li = document.createElement('li'), time = document.createElement('time'), tag = document.createElement('b'), message = document.createElement('span');
      time.textContent = new Date(e.at).toLocaleTimeString('zh-CN', { hour12: false }); time.dateTime = new Date(e.at).toISOString();
      tag.textContent = LOG_TAGS[e.kind] || '系统'; tag.className = e.kind; li.className = 'log-' + e.kind + (e.known ? ' log-known' : ''); message.textContent = e.text; li.append(time, tag, message); list.append(li);
    }
    if (!shown.length) { const li = document.createElement('li'); li.className = 'muted'; li.textContent = '暂无此类事件'; list.append(li); }
    text('event-count', `${shown.length} / ${events.length} 条${state?.filteredSpeech ? ` · 已过滤 ${state.filteredSpeech} 条无字/非人声` : ''}`);
  }
  text('freshness', fresh ? `更新于 ${new Date(state.receivedAt).toLocaleTimeString('zh-CN', { hour12: false })}` : '实时读数不可用');
}
function streamURL() {
  const servo = ['servo-tuning', 'servo-settings'].some(id => $(id).open);
  return `/api/events?camera=${previewRequested ? 1 : 0}&servo=${servo ? 1 : 0}`;
}
function connect() {
  source?.close();
  if (paused || pageHidden) return;
  const ownSource = source = new EventSource(streamURL());
  ownSource.onopen = () => { if (source === ownSource) lastEvent = -1; };
  ownSource.onmessage = event => {
    if (source !== ownSource || paused || pageHidden) return;
    try {
      const next = JSON.parse(event.data);
      if (next.boot && lastBoot && next.boot !== lastBoot) clearFrame();
      if (next.boot) lastBoot = next.boot;
      state = next; received = Date.now(); render();
    } catch { text('notice', '数据格式异常，等待下一帧'); }
  };
  ownSource.onerror = () => { if (source === ownSource) { received = 0; clearFrame(); render(); } };
}
$('camera-toggle').addEventListener('click', () => {
  if (paused) return;
  previewRequested = !previewRequested; clearFrame(); connect(); render();
});
// The open attribute covers showModal(), Escape, form method=dialog and
// programmatic close; unrelated dialogs never request servo diagnostics.
let diagnosticsRequested = false;
const dialogObserver = new MutationObserver(() => {
  const next = ['servo-tuning', 'servo-settings'].some(id => $(id).open);
  if (next === diagnosticsRequested) return;
  diagnosticsRequested = next; connect();
});
for (const id of ['servo-tuning', 'servo-settings']) dialogObserver.observe($(id), { attributes: true, attributeFilter: ['open'] });
$('pause').addEventListener('click', () => {
  paused = !paused; $('pause').setAttribute('aria-pressed', String(paused)); text('pause', paused ? '恢复监控' : '暂停监控');
  clearFrame(); received = 0;
  if (paused) source?.close(); else connect(); render();
});
window.addEventListener('pagehide', () => { pageHidden = true; source?.close(); clearFrame(); });
window.addEventListener('pageshow', event => { pageHidden = false; if (event.persisted && !paused) connect(); });
window.addEventListener('resize', render);
setInterval(render, 500);
connect(); render();
