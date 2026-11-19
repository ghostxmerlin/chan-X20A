// Docking 流程 (2026-11-19 evening, owner "再重新给我docking mode的流程图"): how one sentence goes through Docking now,
// after today's touch, tool-busy, mode spans, debug mode, events and 资料问答. Drawn from STAGES like skills-ui.js's
// flow, with its own loops. No inline styles (CSP): every colour is a class from skills.css.
export const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

export const STAGES = [
  { lane: 'ear', t: '① 开口的入口', d: ['有人说话 · 叫「hello」/「Dynames」→ 10 秒内的话都当跟它说', '点屏幕（没在说话）→ 20 秒「必答」，比叫名字更强'],
    side: ['事件也会让它开口', '主人出现 → 打招呼', '被用力晃 → 说一句（1 分钟 1 次）', '调试提醒 · 调试结束问要不要总结'], sl: '事件' },
  { lane: 'ear', t: '② 耳朵听到一句话', d: ['本地识别出字 + 声纹认出是谁', '认出模式词：安静 / 少插话 / 角色 / 调试，带时长或「直到我说退出」'],
    side: ['【你的眼睛】随时更新', '人脸 · 摄像头画面 · 注视', '身体感觉：被拿起、晃了、靠近', '说话人是谁'], sl: '写进说明' },
  { lane: 'ear', t: '③ 分类器 + 规则词先装技能（不花钱）', d: ['要不要工具 P ≥ 0.5；按大类预装', '资料 / 型号 / Tab5 → 资料问答；调试模式 → 帮我调试（一直开）'],
    side: ['17 个技能', '最多同时开 4 个', '没用上 2 分钟撤'], sl: '预装' },
  { lane: 'qwen', t: '④ 千问听同一句（音频）', d: ['smart_turn：是不是跟它说的、说完了没', '→ 闲聊回答 / 打开技能 / 调工具'],
    side: ['判「不是跟我说」', '叫过名字或点过屏幕：照答', '否则不答（蓝点闪一下）'], sl: '不是跟我说' },
  { lane: 'ear', t: '⑤ 耳朵收到工具调用：几道门', d: ['技能名当工具 → 打开技能；没开的技能里的工具 → 自动开', '身体门：认识的声音或脸才动 · 工具门：被叫 / 规则词 / 分类器 / 同一条链'],
    side: ['挡回：悄悄退给模型', '照闲聊答', '一句话最多 5 次工具'], sl: '不过', refuse: true },
  { lane: 'ear', t: '⑥ 工具运行中 = 算在说话', d: ['三个点变琥珀色，眼睛下面：查资料中 / 上网查 / 在回忆…', '这时不收新的话；放一句「我查查」类垫话'],
    side: ['点屏幕 / 喊 hello', '→ 马上取消，屏幕 STOP', '结果丢掉，不再提它'], sl: '打断', refuse: true, warn: true },
  { lane: 'mind', t: '⑦ mind 执行（/tool/call）', d: ['校验参数；C 级不是主人亲口 → 待确认', '资料问答：查资料（按意思 + 按字面）→ sol 推理，带出处'],
    side: ['调试模式里', 'mind 每两三分钟记一次调试记录', '发现问题 → 调试提醒（①）'], sl: '另一路' },
  { lane: 'ear', t: '⑧ 结果交回千问', d: ['最多 3000 字；资料问答给「要说的」≤ 120 字 + 详细的', '千问可以再调一个（回到 ⑤），或者开始回答'] },
  { lane: 'ear', t: '⑨ 回答门', d: ['调试中 / 安静中：没被叫、没点屏幕就不播；少插话：只答明确问它的', '「嗯」门：不是跟它说的只回「嗯」不播；以【事件】标记开头的不播'],
    side: ['被挡下的回答', '照样计费', '日志里能看到原因'], sl: '不播', refuse: true },
  { lane: 'ear', t: '⑩ 说出来（机器人播放）', d: ['说话中单击屏幕 = 马上停（STOP）', '双击 = 停下并进入「安静一下」（QUIET）'] },
];

const LANE = { ear: '耳朵', qwen: '千问', mind: 'mind' };
export const LOOPS = [
  { from: 7, to: 4, x: 60, label: '再调一个工具（同一句话）' },
  { from: 3, to: 8, x: 120, label: '闲聊不碰工具，直接到回答门', dash: true, dy: -14 },
];

export function flowSvg(stages = STAGES, loops = LOOPS) {
  const X = 150, W = 460, H = 80, STEP = 106, Y0 = 12, SX = 660, SW = 240;
  const height = Y0 + stages.length * STEP;
  let g = '';
  stages.forEach((s, i) => {
    const y = Y0 + i * STEP;
    g += `<rect class="box${s.warn ? ' warnbox' : ''}" x="${X}" y="${y}" width="${W}" height="${H}" rx="6"/>`;
    g += `<rect class="stripe-${s.lane}" x="${X}" y="${y}" width="5" height="${H}" rx="2"/>`;
    g += `<text class="lane ${s.lane}" x="${X + W - 12}" y="${y + 20}" text-anchor="end">${LANE[s.lane]}</text>`;
    g += `<text class="ttl" x="${X + 18}" y="${y + 24}">${esc(s.t)}</text>`;
    s.d.forEach((l, j) => { g += `<text class="dt" x="${X + 18}" y="${y + 47 + j * 19}">${esc(l)}</text>`; });
    if (i < stages.length - 1) g += `<line class="ar" x1="${X + W / 2}" y1="${y + H}" x2="${X + W / 2}" y2="${y + STEP - 2}" marker-end="url(#ah)"/>`;
    if (s.side) {
      const sh = Math.max(H - 6, 14 + s.side.length * 18), sy = y + (H - sh) / 2;
      g += `<rect class="side${s.refuse ? ' refuse' : ''}" x="${SX}" y="${sy}" width="${SW}" height="${sh}" rx="6"/>`;
      s.side.forEach((l, j) => { g += `<text class="sd" x="${SX + 12}" y="${sy + 20 + j * 18}"${j === 0 ? ' font-weight="700"' : ''}>${esc(l)}</text>`; });
      g += `<line class="ar${s.refuse ? '' : ' dash'}" x1="${X + W}" y1="${y + H / 2}" x2="${SX - 2}" y2="${y + H / 2}" marker-end="url(#ah)"/>`;
      if (s.sl) g += `<text class="lbl" x="${(X + W + SX) / 2}" y="${y + H / 2 - 6}" text-anchor="middle">${esc(s.sl)}</text>`;
    }
  });
  const mid = i => Y0 + i * STEP + H / 2;
  for (const l of loops) {
    const a = mid(l.from) + (l.dy || 0), b = mid(l.to);
    g += `<path class="ar${l.dash ? ' dash' : ''}" d="M${X},${a} H${X - l.x} V${b} H${X - 2}" marker-end="url(#ah)"/>`;
    g += `<text class="lbl" transform="translate(${X - l.x - 8},${(a + b) / 2}) rotate(-90)" text-anchor="middle">${esc(l.label)}</text>`;
  }
  return `<svg viewBox="0 0 910 ${height}" role="img" aria-label="Docking 里一句话的十步：入口（说话、叫名字、点屏幕、事件），耳朵听到并认出模式，分类器预装技能，千问决定，耳朵过几道门，工具运行时算在说话可被打断，mind 执行（资料问答用 sol 推理），结果交回千问，回答门（调试、安静、少插话、嗯门），最后说出来，说话中可点屏幕停。">` +
    `<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" class="head"/></marker></defs>${g}</svg>`;
}

export const MODES = [
  ['安静一下', '「你先安静一会儿」/ 双击屏幕', '不说时长：叫到就结束；说了时长或「直到我说退出」：叫它只答一句，模式不断', '「可以说话了」「退出安静模式」/ 叫它（不带时长时）', '安静中'],
  ['少插话', '「我们在聊天」「在看电视」', '默认 30 分钟；可说时长或「直到我说退出」', '「可以说话了」', '少插话'],
  ['临时角色', '「hello，当个大姐姐…」', '默认 2 小时；可说时长或「直到我说退出」', '「做回你自己」「恢复」', '角色中'],
  ['调试模式', '「进入调试模式」「帮我调试（一个小时）」', '不说时长：一直到你说退出；记调试记录，有问题提醒', '「退出调试模式」→ 问要不要总结', '调试中'],
];
export const HINTS = [
  ['STOP', '说话中或工具运行中点了一下：停下'], ['LISTEN + 眼睛下「在听」', '没在说话时点了一下：20 秒必答'],
  ['QUIET + 「安静中」', '双击：安静一下'], ['三个点琥珀色 + 「查资料中」等', '工具在跑，算在说话'],
  ['三个点蓝色闪一下', '千问判断不是跟它说的'], ['眼睛下：调试中 / 安静中 / 少插话 / 角色中', '正在这个模式（几个同时开时显示最要紧的）'],
];

export function tablesHtml() {
  const m = MODES.map(r => `<tr><td class="k">${esc(r[0])}</td><td>${esc(r[1])}</td><td>${esc(r[2])}</td><td>${esc(r[3])}</td><td>${esc(r[4])}</td></tr>`).join('');
  const h = HINTS.map(r => `<tr><td class="k">${esc(r[0])}</td><td>${esc(r[1])}</td></tr>`).join('');
  return `<h2>模式（都能说时长，重启后接着生效）</h2><div class="tbl"><table><thead><tr><th>模式</th><th>怎么进</th><th>持续多久</th><th>怎么退</th><th>屏幕</th></tr></thead><tbody>${m}</tbody></table></div>` +
    `<h2>机器人屏幕上的提示</h2><div class="tbl"><table><thead><tr><th>看到</th><th>意思</th></tr></thead><tbody>${h}</tbody></table></div>`;
}

if (typeof document !== 'undefined') {
  document.getElementById('flow').innerHTML = flowSvg();
  document.getElementById('tables').innerHTML = tablesHtml();
}
