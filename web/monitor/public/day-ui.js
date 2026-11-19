// 今天的工作 (2026-11-19): a day's plan from docs/day/<date>.json (/api/day), re-read every 30 s. Claude keeps the file
// up to date while working; the page only shows it. No inline styles (CSP): widths and colours are classes.

export const STATUS_ZH = { todo: '没开始', doing: '在做', done: '做完了', waiting: '等你给东西', needs_you: '要你定', later: '以后' };
export const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const st = s => (s in STATUS_ZH ? s : 'todo');
export const pill = s => `<span class="pill s-${st(s)}">${STATUS_ZH[st(s)]}</span>`;

// share of a task's steps that are done -> w0..w10
export function progress(task) {
  const steps = (task.plan || []).filter(p => p.status !== 'later');
  if (task.status === 'done') return 10;
  if (!steps.length) return 0;
  return Math.round(10 * steps.filter(p => p.status === 'done').length / steps.length);
}

export function taskHtml(t) {
  const steps = (t.plan || []).map(p => `<li class="${st(p.status) === 'done' ? 'done' : ''}">${pill(p.status)}<span>${esc(p.text)}</span></li>`).join('');
  return `<article class="task" id="task-${esc(t.id)}"><div class="head"><span class="n">${esc(t.n)}</span><h3>${esc(t.title)}</h3>${pill(t.status)}</div>
  ${t.ask ? `<p class="ask">${esc(t.ask)}</p>` : ''}${t.found ? `<p class="found">${esc(t.found)}</p>` : ''}
  <div class="bar"><i class="w${progress(t)}"></i></div>${steps ? `<ul class="steps">${steps}</ul>` : ''}</article>`;
}

export function dayHtml(d) {
  const qs = (d.questions || []).map(q => `<li>${esc(q)}</li>`).join('');
  const carry = (d.carry || []).map(c => `<li>${pill(c.status)} ${esc(c.text)}</li>`).join('');
  const tl = (d.timeline || []).map(x => `<b>${esc(x.when)}</b><span>${esc(x.what)}</span>`).join('');
  return (qs ? `<h2>要你回答的</h2><div class="box you"><ol>${qs}</ol></div>` : '') +
    (tl ? `<h2>今天的安排</h2><div class="box timeline">${tl}</div>` : '') +
    `<h2>六件事</h2><div class="tasks">${(d.tasks || []).map(taskHtml).join('')}</div>` +
    (carry ? `<h2>之前留下的</h2><div class="box"><ul>${carry}</ul></div>` : '');
}

async function load() {
  const $ = id => document.getElementById(id);
  try {
    const date = new URLSearchParams(location.search).get('date') || '';
    const r = await fetch('/api/day' + (date ? `?date=${encodeURIComponent(date)}` : ''), { cache: 'no-store' });
    if (!r.ok) throw new Error(await r.text());
    const d = await r.json();
    $('title').textContent = `${d.title || '今天的工作'} · ${d.date || ''}`;
    $('goal').textContent = d.goal || '';
    $('updated').textContent = d.updated ? `更新于 ${d.updated}` : '';
    $('day').innerHTML = dayHtml(d);
  } catch (e) {
    $('day').innerHTML = `<p class="muted">读不到今天的计划（${esc(e.message)}）。</p>`;
  }
}

if (typeof document !== 'undefined') { load(); setInterval(load, 30000); }
