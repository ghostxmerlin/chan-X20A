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

export function groupHtml(reg, group, status = '') {
  const tools = reg.tools.filter(t => t.group === group && (!status || t.status === status));
  if (!tools.length) return '';
  const rows = tools.map(t => `<tr>
    <td><b>${esc(t.title)}</b><br><code>${esc(t.name)}</code></td>
    <td><span class="tag ${LEVEL_TAG[t.level] || ''}">${esc(t.level)}</span><span class="tag ${STATUS_TAG[t.status] || ''}">${esc(STATUS_SHORT[t.status] || t.status)}</span></td>
    <td>${esc(t.description)}<div class="small">返回：${esc(t.returns)}</div></td>
    <td class="small">${params(t.parameters)}</td>
    <td class="small">${esc(t.runs_on)} · ${esc(t.latency)} · ${esc(t.cost)}${t.needs.length ? `<br>前提：${esc(t.needs.join('、'))}` : ''}<br>来源：${esc(t.source)}${t.notes ? `<br>${esc(t.notes)}` : ''}</td>
  </tr>`).join('');
  return `<article class="card wide"><h2>${esc(reg.groups[group])}（${tools.length}）</h2>
    <table><tr><th>工具</th><th>级别 / 状态</th><th>什么时候用</th><th>参数</th><th>在哪 · 多快 · 多贵</th></tr>${rows}</table></article>`;
}

export function render(reg, status = '') {
  return `<section class="notes">${Object.keys(reg.groups).map(g => groupHtml(reg, g, status)).join('')}</section>`;
}

async function main() {
  const res = await fetch('/api/tools', { cache: 'no-store' });
  if (!res.ok) { document.getElementById('tools-summary').textContent = '读不到工具注册表'; return; }
  const reg = await res.json();
  document.getElementById('tools-summary').textContent = summaryText(reg) + `（${reg.updated}）`;
  const draw = status => { document.getElementById('tools-main').innerHTML = render(reg, status); };
  document.querySelectorAll('.tools-filter button').forEach(b => b.addEventListener('click', () => {
    document.querySelectorAll('.tools-filter button').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
    draw(b.dataset.status);
  }));
  draw('');
}
if (typeof document !== 'undefined' && document.getElementById('tools-main')) main();
