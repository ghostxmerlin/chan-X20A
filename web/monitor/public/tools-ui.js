// The tool registry page (A2, 2026-11-18): config/tools/registry.json via /api/tools, grouped, filterable by status.
const esc = t => String(t ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const LEVEL_TAG = { R: 'see', A: 'say', C: 'note' };
const STATUS_TAG = { live: 'see', have: 'hear', wrap: 'think', new: 'say' };
const STATUS_SHORT = { live: '已接入', have: '已有接口', wrap: '要开接口', new: '要新做' };

export function params(p) {
  const props = Object.entries((p && p.properties) || {});
  if (!props.length) return '无';
  const req = new Set((p && p.required) || []);
  return props.map(([k, v]) => {
    const kind = v.enum ? v.enum.join(' / ') : v.type === 'array' ? `[${(v.items && (v.items.enum || [v.items.type]).join(' / ')) || ''}]` : v.type;
    return `<code>${esc(k)}${req.has(k) ? '' : '?'}</code> ${esc(kind)}`;
  }).join('<br>');
}

export function summaryText(reg) {
  const n = reg.tools.length, by = {};
  for (const t of reg.tools) by[t.status] = (by[t.status] || 0) + 1;
  return `共 ${n} 个工具：` + Object.keys(reg.statuses).map(s => `${reg.statuses[s]} ${by[s] || 0}`).join(' · ');
}

// 试一下 (B1): only read-only (R) tools that mind has implemented; an argument skeleton from the parameters.
export function skeleton(p) {
  const out = {};
  for (const k of (p && p.required) || []) {
    const v = p.properties[k];
    out[k] = v.enum ? v.enum[0] : v.type === 'integer' || v.type === 'number' ? (v.minimum ?? 1) : v.type === 'boolean' ? false : v.type === 'array' ? [] : '';
  }
  return out;
}

export function groupHtml(reg, group, status = '', available = new Set()) {
  const tools = reg.tools.filter(t => t.group === group && (!status || t.status === status));
  if (!tools.length) return '';
  const rows = tools.map(t => `<tr>
    <td><b>${esc(t.title)}</b><br><code>${esc(t.name)}</code>${available.has(t.name) && t.level === 'R' ? `<br><button class="try" data-tool="${esc(t.name)}">试一下</button>` : ''}</td>
    <td><span class="tag ${LEVEL_TAG[t.level] || ''}">${esc(t.level)}</span><span class="tag ${STATUS_TAG[t.status] || ''}">${esc(STATUS_SHORT[t.status] || t.status)}</span></td>
    <td>${esc(t.description)}<div class="small">返回：${esc(t.returns)}</div></td>
    <td class="small">${params(t.parameters)}</td>
    <td class="small">${esc(t.runs_on)} · ${esc(t.latency)} · ${esc(t.cost)}${t.needs.length ? `<br>前提：${esc(t.needs.join('、'))}` : ''}<br>来源：${esc(t.source)}${t.notes ? `<br>${esc(t.notes)}` : ''}</td>
  </tr>`).join('');
  return `<article class="card wide"><h2>${esc(reg.groups[group])}（${tools.length}）</h2>
    <table><tr><th>工具</th><th>级别 / 状态</th><th>什么时候用</th><th>参数</th><th>在哪 · 多快 · 多贵</th></tr>${rows}</table></article>`;
}

export function render(reg, status = '', available = new Set()) {
  return `<section class="notes">${Object.keys(reg.groups).map(g => groupHtml(reg, g, status, available)).join('')}</section>`;
}

async function main() {
  const res = await fetch('/api/tools', { cache: 'no-store' });
  if (!res.ok) { document.getElementById('tools-summary').textContent = '读不到工具注册表'; return; }
  const reg = await res.json();
  document.getElementById('tools-summary').textContent = summaryText(reg) + `（${reg.updated}）`;
  let available = new Set();
  try {
    const r = await fetch('/api/mind/tools?all=1', { cache: 'no-store' });
    if (r.ok) available = new Set((await r.json()).available || []);
  } catch {}
  const panel = document.getElementById('try-panel'), argsBox = document.getElementById('try-args');
  let current = null;
  const draw = status => {
    document.getElementById('tools-main').innerHTML = render(reg, status, available);
    document.querySelectorAll('button.try').forEach(b => b.addEventListener('click', () => {
      current = reg.tools.find(t => t.name === b.dataset.tool);
      document.getElementById('try-title').textContent = `试一下：${current.title}（${current.name}）`;
      argsBox.value = JSON.stringify(skeleton(current.parameters), null, 1);
      document.getElementById('try-out').textContent = '';
      panel.hidden = false; panel.scrollIntoView({ block: 'nearest' });
    }));
  };
  document.getElementById('try-run').addEventListener('click', async () => {
    if (!current) return;
    const out = document.getElementById('try-out');
    let args;
    try { args = JSON.parse(argsBox.value || '{}'); } catch { out.textContent = '参数不是合法的 JSON'; return; }
    out.textContent = '调用中…';
    try {
      const r = await fetch('/api/mind/tool/call', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: current.name, args, caller: 'page' }) });
      out.textContent = r.ok ? JSON.stringify(await r.json(), null, 2) : `${r.status} ${await r.text()}`;
    } catch (e) { out.textContent = `调用失败：${e.message}`; }
  });
  document.querySelectorAll('.tools-filter button').forEach(b => b.addEventListener('click', () => {
    document.querySelectorAll('.tools-filter button').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
    draw(b.dataset.status);
  }));
  draw('');
}
if (typeof document !== 'undefined' && document.getElementById('tools-main')) main();
