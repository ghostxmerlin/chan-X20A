// 技能页 (2026-11-19): the Docking skills as they are in the repository (config/skills/skills.json + skills/<id>.md, via
// /api/skills), each skill's tools with their level from the tool registry (/api/tools), the current 千问用工具 gear
// (/api/mind/switches), and the call flow as the ear and mind code run it today (drawn here, not read from the server).

export const GROUPS = [
  ['look', '看和听', '看东西、认人'], ['recall', '回忆', '以前的事、一段时间的总结'], ['world', '查外面', '价格天气、新闻事实、大问题'],
  ['daily', '日常', '提醒日程、帮忙盯着'], ['body', '身体', '只对认识的人动'], ['manner', '说话方式', '长短、安静、换声音、角色'],
  ['self', '解释自己', '为什么、能做什么、花费'], ['note', '记下来', '记事、记人、开关'], ['free', '自由发挥', '一次给全部工具']];
const MODE_ZH = { off: '关', all: '全部工具', skills: '技能', auto: '自动' };
// the ear never hands these to the model (server/ear/realtime_link.py SKILL_TOOL_SKIP)
const SKIP = new Set(['say', 'stop_speaking', 'db_query']);

export const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const inline = s => esc(s).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');

// The small markdown the skill files use: "# title" (dropped, the card has it), "## section", "- " / "1. " lists, `code`, **bold**.
export function md(text) {
  const out = []; let list = null;
  const close = () => { if (list) { out.push(`</${list}>`); list = null; } };
  for (const raw of String(text ?? '').split('\n')) {
    const l = raw.replace(/\s+$/, '');
    if (!l.trim()) { close(); continue; }
    if (l.startsWith('# ')) { close(); continue; }
    if (l.startsWith('## ')) { close(); out.push(`<h4>${inline(l.slice(3))}</h4>`); continue; }
    let m = l.match(/^\s*-\s+(.*)/);
    if (m) {
      const nested = /^\s{2,}/.test(l);
      if (!nested && list !== 'ul') { close(); out.push('<ul>'); list = 'ul'; }
      out.push(`<li${nested ? ' style="margin-left:14px;list-style:circle"' : ''}>${inline(m[1])}</li>`); continue;
    }
    m = l.match(/^(\d+)\.\s+(.*)/);
    if (m) { if (list !== 'ol') { close(); out.push('<ol>'); list = 'ol'; } out.push(`<li>${inline(m[2])}</li>`); continue; }
    close(); out.push(`<p>${inline(l)}</p>`);
  }
  close();
  return out.join('');
}

// registry.json -> {name: {title, level, status}}
export function toolIndex(reg) {
  const out = {};
  for (const t of (reg && reg.tools) || []) out[t.name] = { title: t.title, level: t.level, status: t.status };
  return out;
}

const LV = { R: '--l-r', A: '--l-a', C: '--l-c' };
export function chip(name, tools, nAll) {
  if (name === '*') return `<span class="chip" style="--lc:var(--g-free)">全部工具<small>${nAll} 个</small></span>`;
  const t = tools[name];
  if (!t || t.status !== 'live') return `<span class="chip missing" title="工具注册表里没有，或还没接入">${esc(name)}<small>未接入</small></span>`;
  return `<span class="chip" style="--lc:var(${LV[t.level] || '--muted'})" title="${esc(t.level)} 级">${esc(name)}<small>${esc(t.title)}</small></span>`;
}

export function liveCount(tools) {
  return Object.entries(tools).filter(([n, t]) => t.status === 'live' && !SKIP.has(n)).length;
}

// tools the model can be given (live, not skipped) that no skill except 自由发挥 lists
export function uncovered(skills, tools) {
  const inSkill = new Set(skills.filter(s => !(s.tools || []).includes('*')).flatMap(s => s.tools || []));
  return Object.keys(tools).filter(n => tools[n].status === 'live' && !SKIP.has(n) && !inSkill.has(n));
}

export function groupsHtml(cfg, tools) {
  const skills = cfg.skills || [], nAll = liveCount(tools);
  const known = new Set(GROUPS.map(g => g[0]));
  const groups = [...GROUPS, ...[...new Set(skills.map(s => s.group || s.id))].filter(g => !known.has(g)).map(g => [g, g, ''])];
  return groups.map(([g, name, note]) => {
    const sk = skills.filter(s => (s.group || s.id) === g);
    if (!sk.length) return '';
    return `<div class="group" style="--gc:var(--g-${esc(g)},var(--accent))"><div class="group-head"><span class="gname" style="color:var(--gc)">${esc(name)}</span><span class="gid">${esc(g)}</span><span class="gnote">${esc(note)}</span></div><div class="cards">` +
      sk.map(s => {
        const free = (s.tools || []).includes('*');
        return `<article class="card" id="skill-${esc(s.id)}"><div class="t"><h3>${esc(s.title)}</h3><span class="id">${esc(s.id)}</span></div>
   <p class="when">${esc(s.when)}</p><div class="chips">${(s.tools || []).map(n => chip(n, tools, nAll)).join('')}</div>
   <p class="meta">打开后留 ${esc(s.keep_min || cfg.keep_min || 10)} 分钟 · ${free ? `不受 ${esc(cfg.max_tools || 16)} 个工具的上限` : `${(s.tools || []).length} 个工具`}${s.md ? '' : ' · <span class="gap">没有全文文件</span>'}</p>
   ${s.md ? `<details><summary>技能全文</summary><div class="md">${md(s.md)}</div></details>` : ''}</article>`;
      }).join('') + '</div></div>';
  }).join('');
}

export function statsHtml(cfg, tools, mode) {
  const skills = cfg.skills || [];
  const covered = liveCount(tools) - uncovered(skills, tools).length;
  return [
    [skills.length, '个技能'],
    [`${covered} / ${liveCount(tools)}`, '个可给模型的工具被技能覆盖'],
    [mode ?? '读取中', '千问用工具的当前档位'],
    [`${cfg.max_open || 4} 个 · ${cfg.max_tools || 16} 个`, '同时最多打开的技能 · 工具'],
  ].map(([b, s]) => `<div><b>${esc(b)}</b><span>${esc(s)}</span></div>`).join('');
}

export function modeText(sw) {
  const t = (sw && sw.realtime_tools) || {};
  return t.on ? (MODE_ZH[t.mode] || t.mode || '全部工具') : '关';
}

// ---- the call flow (auto mode), as server/ear/realtime_link.py and server/mind/tools.py run it on 2026-11-19 ----
export const STAGES = [
  { lane: 'ear', t: '① 听到一句话', d: ['耳朵自己的识别（SenseVoice）出字', '声纹认出是谁：主人、家里人、不认识'], side: ['说话人变了', '→ 更新说明里的「声音」行'] },
  { lane: 'ear', t: '② 分类器先猜（约 3 毫秒，不花钱）', d: ['要不要工具：P ≥ 0.5', '按最像的已标注句子认出大类，最多 2 类'], side: ['先装这一类的技能', '工具和全文一起进会话', '没用上 2 分钟撤'], sl: '预装' },
  { lane: 'qwen', t: '③ 千问听同一句（音频）', d: ['smart_turn 判断说完了没有', '→ 闲聊回答 / 打开技能 / 调工具'], side: ['闲聊：不碰工具', '直接到 ⑨'], sl: '闲聊' },
  { lane: 'ear', t: '④ 耳朵收到工具调用', d: ['把技能名当工具调 → 改成打开技能', '调了没打开的技能里的工具 → 自动打开它'], side: ['打开的上限', '最多 4 个技能、16 个工具', '留 10 分钟（自由发挥 5 分钟）'], sl: '打开' },
  { lane: 'ear', t: '⑤ 身体门 + 工具门', d: ['身体：只听认识的声音，或认出的脸', '工具：叫过 hello、规则词、分类器 ≥ 0.5、同链已放行'], side: ['挡回：悄悄退回给模型', '不放「我查查」', '照闲聊答，过「嗯」门'], sl: '不过', refuse: true },
  { lane: 'ear', t: '⑥ 垫话和次数', d: ['整条链只放一次「我查查」', '技能档 5 次；自动档实际 3 次，打开技能也算'], warn: true, side: ['超了：回一句', '「已经查了好几次，', '现在就用拿到的回答」'], sl: '超了', refuse: true },
  { lane: 'mind', t: '⑦ mind 执行（/tool/call）', d: ['模型只能用已接入的工具，先校验参数', 'C 级：耳朵认出主人的声音才直接做'], side: ['不是主人亲口', '→ 进「待确认」', '工具页上点同意才执行'], sl: 'C 级', refuse: true },
  { lane: 'ear', t: '⑧ 结果交回千问', d: ['结果最多 3000 字，超了截断', '千问可以再调一个（回到 ④），或者开始回答'] },
  { lane: 'ear', t: '⑨ 回答门 → 说出来', d: ['「嗯」门：不是跟它说的只回「嗯」，不播', '以【事件】这类标记开头的回答一律不播'] },
];
const LANE = { ear: '耳朵', qwen: '千问', mind: 'mind' };

export function flowSvg(stages = STAGES) {
  const X = 150, W = 440, H = 78, STEP = 104, Y0 = 12, SX = 640, SW = 250;
  const height = Y0 + stages.length * STEP;
  let g = '';
  stages.forEach((s, i) => {
    const y = Y0 + i * STEP;
    g += `<rect class="box${s.warn ? ' warnbox' : ''}" x="${X}" y="${y}" width="${W}" height="${H}" rx="6"/>`;
    g += `<rect class="stripe-${s.lane}" x="${X}" y="${y}" width="5" height="${H}" rx="2"/>`;
    g += `<text class="lane ${s.lane}" x="${X + W - 12}" y="${y + 20}" text-anchor="end">${LANE[s.lane]}</text>`;
    g += `<text class="ttl" x="${X + 18}" y="${y + 24}">${esc(s.t)}</text>`;
    s.d.forEach((l, j) => { g += `<text class="dt${s.warn && j === 1 ? ' w' : ''}" x="${X + 18}" y="${y + 46 + j * 18}">${esc(l)}</text>`; });
    if (i < stages.length - 1) g += `<line class="ar" x1="${X + W / 2}" y1="${y + H}" x2="${X + W / 2}" y2="${y + STEP - 2}" marker-end="url(#ah)"/>`;
    if (s.side) {
      const sh = Math.max(H - 10, 16 + s.side.length * 18), sy = y + (H - sh) / 2;
      g += `<rect class="side${s.refuse ? ' refuse' : ''}" x="${SX}" y="${sy}" width="${SW}" height="${sh}" rx="6"/>`;
      s.side.forEach((l, j) => { g += `<text class="sd" x="${SX + 14}" y="${sy + 22 + j * 18}"${j === 0 ? ' font-weight="700"' : ''}>${esc(l)}</text>`; });
      g += `<line class="ar${s.refuse ? '' : ' dash'}" x1="${X + W}" y1="${y + H / 2}" x2="${SX - 2}" y2="${y + H / 2}" marker-end="url(#ah)"/>`;
      if (s.sl) g += `<text class="lbl" x="${(X + W + SX) / 2}" y="${y + H / 2 - 6}" text-anchor="middle">${esc(s.sl)}</text>`;
    }
  });
  const mid = i => Y0 + i * STEP + H / 2;
  // ⑧ -> ④: another tool for the same sentence
  g += `<path class="ar" d="M${X},${mid(7)} H${X - 60} V${mid(3)} H${X - 2}" marker-end="url(#ah)"/>`;
  g += `<text class="lbl" transform="translate(${X - 68},${(mid(3) + mid(7)) / 2}) rotate(-90)" text-anchor="middle">再调一个工具（同一句话）</text>`;
  // ③ -> ⑨: chit-chat
  g += `<path class="ar dash" d="M${X},${mid(2) - 14} H${X - 120} V${mid(8)} H${X - 2}" marker-end="url(#ah)"/>`;
  g += `<text class="lbl" transform="translate(${X - 128},${(mid(2) + mid(8)) / 2}) rotate(-90)" text-anchor="middle">闲聊不碰工具，直接回答</text>`;
  return `<svg viewBox="0 0 900 ${height}" role="img" aria-label="自动档里一句话的九步：耳朵识别和分类器提前装技能，千问决定闲聊或调工具，耳朵过身体门和工具门、限次数，mind 执行并对 C 级工具生成待确认，结果交回千问，最后过回答门说出来。">` +
    `<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path class="head" d="M0,0 L10,5 L0,10 z"/></marker></defs>${g}</svg>`;
}

async function getJson(url) {
  const r = await fetch(url, { cache: 'no-store' });
  if (!r.ok) throw new Error(`${url} ${r.status}`);
  return r.json();
}

async function main() {
  const $ = id => document.getElementById(id);
  $('flow-svg').innerHTML = flowSvg();
  let cfg, tools;
  try {
    [cfg, tools] = await Promise.all([getJson('/api/skills'), getJson('/api/tools').then(toolIndex)]);
  } catch (e) {
    $('groups').innerHTML = `<p class="gap">读不到技能文件（${esc(e.message)}）。观察台要从仓库里运行。</p>`;
    return;
  }
  $('groups').innerHTML = groupsHtml(cfg, tools);
  $('stats').innerHTML = statsHtml(cfg, tools, null);
  const left = uncovered(cfg.skills || [], tools);
  $('uncovered').textContent = left.length ? `不在任何技能里（只有自由发挥能用）：${left.map(n => `${n}（${tools[n].title}）`).join('、')}` : '';
  let mode = null;
  try { mode = modeText(await getJson('/api/mind/switches')); } catch { mode = '读不到'; }
  $('stats').innerHTML = statsHtml(cfg, tools, mode);
  document.querySelectorAll('.mode[data-mode]').forEach(el => {
    const on = el.dataset.mode === mode;
    el.classList.toggle('on', on);
    el.querySelector('.tag').hidden = !on;
  });
}

if (typeof document !== 'undefined') main();
