// hello 的身体 (2026-11-20): mind GET /bodies (/api/mind/bodies) as one card per kind (online, devices seen) and one
// table of abilities and tuning, the rows where the kinds differ marked. No inline styles (CSP).

export const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

export const ABILITIES = [
  ['hearing', '听觉', '麦克风送到耳朵'], ['voice_module', '语音口令', 'hello / play / 数字…'], ['camera', '摄像头', '画面变了才传'],
  ['faces', '认人', '脸纹格式不同就不能互认'], ['body_sense', '身体感觉', '摇晃、拿起、靠近'], ['motion', '动作', '点头、摇头、转头、跟脸'],
  ['screen', '屏幕', ''], ['touch', '触摸', '单击 / 双击'], ['clips', '台词', '按情绪选'], ['ota', '远程升级', '']];
export const TUNING = [
  ['quiet_labels', '安静声音低门槛', '打字、呼吸、咳嗽从 +5 dB 起算（机身麦克风有底噪）'],
  ['unclear_is_voice', '声纹录入收「不清楚」', '机身麦克风的人声概率偏低'], ['denoise', '降噪', 'GTCRN，在听写之前']];
const VALUES = { 'unit-asr': '硬件语音模块', 'on-device': '系统本机识别（可能没有）', 'm12-128': 'M12，128 维',
  'apple-featureprint': 'Apple 特征', servo: '舵机', eyes: '用眼睛动作代替', window: '窗口', screen: '触摸屏', mouse: '鼠标',
  sd: 'SD 卡', bundled: '打包在 app 里' };

export function value(v) {
  if (v === true) return ['有', ''];
  if (v === false || v === null || v === undefined) return ['没有', 'no'];
  return [VALUES[v] || String(v), ''];
}

export function ago(ms, now) {
  if (!ms) return '没见过';
  const s = Math.max(0, Math.round((now - ms) / 1000));
  return s < 60 ? `${s} 秒前` : s < 3600 ? `${Math.round(s / 60)} 分钟前` : s < 86400 ? `${Math.round(s / 3600)} 小时前` : `${Math.round(s / 86400)} 天前`;
}

export function deviceHtml(b, now) {
  const links = b.links || {};
  const state = b.online ? `在线（听觉 ${links.hearing || 0} 条，视觉 ${links.vision || 0} 条连接）` : '没连着';
  const fw = b.device?.firmware ? ` · 固件 ${esc(b.device.firmware)}` : '';
  return `<div class="dev"><span class="dot${b.online ? ' on' : ''}"></span> <span class="id">${esc(b.id)}</span>
    <div class="muted">${state} · 最后出现 ${esc(ago(b.last_seen_ms, now))}${fw}${b.declared ? ' · 自己报了能力' : ''}</div></div>`;
}

export function kindHtml(k, now) {
  const devs = (k.bodies || []).map(b => deviceHtml(b, now)).join('') || '<p class="muted">这套服务还没见过这种身体。</p>';
  return `<section class="box kind"><h2><span class="dot${k.online ? ' on' : ''}"></span>${esc(k.name)}<span class="muted">${esc(k.kind)}</span></h2>${devs}</section>`;
}

export function tableHtml(kinds) {
  const head = `<thead><tr><th></th>${kinds.map(k => `<th>${esc(k.name)}</th>`).join('')}</tr></thead>`;
  const row = (key, label, note, pick) => {
    const vals = kinds.map(k => pick(k)?.[key]);
    const diff = new Set(vals.map(v => JSON.stringify(v ?? null))).size > 1;
    return `<tr${diff ? ' class="diff"' : ''}><th class="k">${esc(label)}${note ? `<span class="sub">${esc(note)}</span>` : ''}</th>${
      vals.map(v => { const [t, c] = value(v); return `<td${c ? ` class="${c}"` : ''}>${esc(t)}</td>`; }).join('')}</tr>`;
  };
  const abil = ABILITIES.map(([k, l, n]) => row(k, l, n, x => x.abilities)).join('');
  const tune = TUNING.map(([k, l, n]) => row(k, l, n, x => x.tuning)).join('');
  return `<h2>能力</h2><div class="box tablebox"><table class="abil">${head}<tbody>${abil}</tbody></table></div>
    <h2>服务器怎么对待它的声音（调校）</h2><div class="box tablebox"><table class="abil">${head}<tbody>${tune}</tbody></table></div>`;
}

export function pageHtml(d, now = Date.now()) {
  const kinds = d.kinds || [];
  const unknown = (d.unknown || []).length ? `<p class="muted">还有 ${d.unknown.length} 个认不出种类的设备记录。</p>` : '';
  return `<div class="stats"><div><b>${kinds.length}</b><span>种身体</span></div><div><b>${d.online || 0}</b><span>个现在连着</span></div></div>
    <div class="kinds">${kinds.map(k => kindHtml(k, now)).join('')}</div>${unknown}${tableHtml(kinds)}`;
}

async function load() {
  const $ = id => document.getElementById(id);
  try {
    const r = await fetch('/api/mind/bodies', { cache: 'no-store' });
    if (!r.ok) throw new Error(await r.text());
    const d = await r.json();
    $('meta').textContent = `数据库 ${d.database || '?'}（这套服务）`;
    $('bodies').innerHTML = pageHtml(d);
  } catch (e) {
    $('bodies').innerHTML = `<p class="muted">读不到身体列表（${esc(e.message)}）。mind 要先部署这一版。</p>`;
  }
}

if (typeof document !== 'undefined') { load(); setInterval(load, 10000); }
