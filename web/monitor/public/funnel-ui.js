// 漏答漏斗 (2026-11-19 night, Fable's audit step 0 "先看见"): every sentence the ear heard has one gate.addressed row
// (server/ear/rt_cards.py): its level (must = a tap / a call / the name in it, should, maybe, low, by a shadow score
// that decides nothing) and what became of it (answered / muted:<verdict> / ambient = Qwen said not for me / none =
// Qwen gave no verdict). This page counts them for a day. No sentence text here (heard.asr has it, the log page shows it).
export const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
export const LEVELS = [['must', '必答（点屏幕 / 叫名字 / 句里有名字）'], ['should', '该答（接话、问句…）'], ['maybe', '可能'], ['low', '不像跟它说']];
export const OUT_ZH = { answered: '答了', ambient: '千问判不是跟我说', none: '千问没判定', 'muted:quiet': '只回「嗯」被挡',
  'muted:repeat': '重复被挡', 'muted:unclear': '没听清被挡', 'muted:marker': '标记被挡', 'muted:blind': '看不清被挡',
  'muted:quiet_mode': '安静中', 'muted:low_mode': '少插话中', 'muted:debug_mode': '调试中', 'muted:order': '当成口令',
  'muted:wait_look': '等照片', 'muted:wait_search': '等搜索' };
export const outZh = o => OUT_ZH[o] || (String(o).startsWith('muted:') ? `被挡（${o.slice(6)}）` : String(o));

export function count(rows) {
  const by = {};
  for (const r of rows) {
    const x = r.extra || {};
    const lv = x.level || 'low', out = x.outcome || 'none';
    const g = by[lv] ||= { n: 0, out: {}, src: {} };
    g.n++; g.out[out] = (g.out[out] || 0) + 1;
    if (lv === 'must') { const s = x.called_source || (x.call_word ? 'text' : '?'); g.src[s] = (g.src[s] || 0) + 1; }
  }
  return by;
}

const SRC_ZH = { touch: '点屏幕', asr: '语音模块听到 hello', name: '关键词 Dynames', text: '句里有名字', '?': '其它' };
const pct = (a, b) => b ? Math.round(100 * a / b) : 0;
const w10 = (a, b) => `w${Math.max(0, Math.min(10, Math.round(10 * a / Math.max(1, b))))}`;

export function pageHtml(rows, day) {
  if (!rows.length) return `<p class="muted">${esc(day)} 还没有记录（耳朵部署了「先看见」之后才有）。</p>`;
  const by = count(rows);
  const head = LEVELS.map(([lv, zh]) => {
    const g = by[lv]; if (!g) return '';
    const ans = g.out.answered || 0;
    return `<div><b>${pct(ans, g.n)}%</b><span>${esc(zh)} 答了 ${ans} / ${g.n}</span></div>`;
  }).join('');
  const sections = LEVELS.map(([lv, zh]) => {
    const g = by[lv]; if (!g) return '';
    const outs = Object.entries(g.out).sort((a, b) => b[1] - a[1]);
    const bars = outs.map(([o, n]) => `<details class="mod"><summary><span class="fn">${esc(outZh(o))}</span>
      <span class="bar"><i class="${o === 'answered' ? 'L2' : o === 'none' ? 'L4' : 'L3'} ${w10(n, g.n)}"></i></span><span class="n">${n}</span>
      <span class="what">${pct(n, g.n)}%</span></summary></details>`).join('');
    const src = lv === 'must' ? `<p class="muted">怎么叫的：${Object.entries(g.src).map(([s, n]) => `${esc(SRC_ZH[s] || s)} ${n}`).join(' · ')}</p>` : '';
    return `<h2>${esc(zh)} · ${g.n} 句</h2><div class="box mods">${bars}${src}</div>`;
  }).join('');
  return `<div class="stats">${head}</div>${sections}`;
}

export function dayRange(day) {
  const lo = new Date(`${day}T00:00:00+08:00`).getTime();
  return [lo, lo + 86400_000];
}

async function fetchAll(fetchJson, lo, hi) {
  const rows = []; let before = null;
  for (let i = 0; i < 10; i++) {
    const q = new URLSearchParams({ from: lo, to: hi, kind: 'gate.addressed', limit: 1000, ...(before ? { before } : {}) });
    const d = await fetchJson('/log?' + q);
    rows.push(...(d.rows || []));
    if (!d.more || !d.rows?.length) break;
    before = d.rows[d.rows.length - 1]._id;
  }
  return rows;
}

async function load() {
  const $ = id => document.getElementById(id);
  const day = new URLSearchParams(location.search).get('day') ||
    new Date(Date.now() + 8 * 3600_000).toISOString().slice(0, 10);
  $('day').textContent = day;
  try {
    const fetchJson = async p => { const r = await fetch('/api/mind' + p, { cache: 'no-store', signal: AbortSignal.timeout(30000) });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`); return r.json(); };
    const [lo, hi] = dayRange(day);
    $('funnel').innerHTML = pageHtml(await fetchAll(fetchJson, lo, hi), day);
  } catch (e) {
    $('funnel').innerHTML = `<p class="muted">读不到日志（${esc(e.message)}）。</p>`;
  }
}

if (typeof document !== 'undefined') load();
