// 代码结构 (2026-11-19): docs/arch/code-map.json (/api/code) as one section per service, the modules grouped by their
// target layer, a bar for the lines, the upward imports on top. No inline styles (CSP): widths and colours are classes.

export const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
export const SERVICES = [['ear', '耳朵 soul-spike'], ['mind', 'mind（soul-mind / eye / notes / memory）'], ['common', '共用'], ['kws', '关键词耳朵 soul-kws']];
export const LAYERS = [1, 2, 3, 4, 5, 'tool'];
const BIG = 800;

// lines -> w0..w10 against the largest module
export const width = (lines, max) => `w${Math.max(1, Math.round(10 * lines / Math.max(1, max)))}`;
export const short = p => String(p).replace(/^[a-z]+\//, '');

export function rowHtml(rel, m, max, up) {
  const bad = up.filter(v => v.from === rel);
  const imp = m.imports.map(i => `<code class="${bad.some(v => v.to === i) ? 'up' : ''}">${esc(short(i))}</code>`).join(' ');
  const cols = m.collections.length ? `<div class="sub">数据库：${m.collections.map(c => `<code>${esc(c)}</code>`).join(' ')}</div>` : '';
  return `<details class="mod${m.lines >= BIG ? ' big' : ''}"><summary><span class="fn">${esc(short(rel))}</span>
    <span class="bar"><i class="L${esc(m.layer)} ${width(m.lines, max)}"></i></span><span class="n">${m.lines}</span>
    <span class="what">${esc(m.zh || m.doc || '')}</span>${bad.length ? `<span class="badup">往上调 ${bad.length}</span>` : ''}</summary>
    <div class="sub">调用：${imp || '（不调别的模块）'}</div>${cols}</details>`;
}

export function pageHtml(d) {
  const mods = d.modules || {};
  const max = Math.max(...Object.values(mods).map(m => m.lines), 1);
  const up = d.violations || [];
  const names = d.layer_names || {};
  const legend = LAYERS.map(l => `<span><i class="sw L${l}"></i>${esc(l === 'tool' ? names.tool : `${l} ${names[l] || ''}`)}</span>`).join('');
  const tot = SERVICES.map(([p, t]) => d.totals?.[p] ? `<div><b>${(d.totals[p].lines / 1000).toFixed(1)}k</b><span>${esc(t)} · ${d.totals[p].modules} 个文件</span></div>` : '').join('');
  const upHtml = up.length ? `<h2>往上调的 ${up.length} 处（夜里要清掉）</h2><div class="box"><ul>${up.map(v =>
    `<li><code>${esc(v.from)}</code>（${v.from_layer} 层）调了 <code>${esc(v.to)}</code>（${v.to_layer} 层）</li>`).join('')}</ul></div>` :
    '<h2>没有往上调的</h2>';
  const sections = SERVICES.map(([p, title]) => {
    const own = Object.entries(mods).filter(([, m]) => m.pkg === p);
    if (!own.length) return '';
    const groups = LAYERS.map(l => {
      const rows = own.filter(([, m]) => m.layer === l).sort((a, b) => b[1].lines - a[1].lines);
      if (!rows.length) return '';
      return `<h3 class="lh"><i class="sw L${l}"></i>${esc(l === 'tool' ? names.tool : `${l} ${names[l] || ''}`)}</h3>` +
        rows.map(([rel, m]) => rowHtml(rel, m, max, up)).join('');
    }).join('');
    return `<h2>${esc(title)}</h2><div class="box mods">${groups}</div>`;
  }).join('');
  return `<div class="stats">${tot}</div><div class="legend">${legend}<span><b class="bigmark">粗框</b> 超过 ${BIG} 行</span></div>${upHtml}${sections}`;
}

async function load() {
  const $ = id => document.getElementById(id);
  try {
    const r = await fetch('/api/code', { cache: 'no-store' });
    if (!r.ok) throw new Error(await r.text());
    const d = await r.json();
    $('meta').textContent = `扫描于 ${d.generated}，代码版本 ${d.commit || '?'}`;
    $('code').innerHTML = pageHtml(d);
  } catch (e) {
    $('code').innerHTML = `<p class="muted">读不到代码地图（${esc(e.message)}）。</p>`;
  }
}

if (typeof document !== 'undefined') load();
