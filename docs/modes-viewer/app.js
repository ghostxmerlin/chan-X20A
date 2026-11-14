/*
 * hello 模式查看器：引擎 + 渲染。
 * 所有模式、输入、规则、文字都来自 design-data.js 的 window.DESIGN；这里不写死任何一个模式的行为。
 */
(function () {
  'use strict';
  var D = window.DESIGN;
  if (!D) { document.body.textContent = 'design-data.js 没有加载'; return; }

  /* ================= 工具 ================= */
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function clone(o) { return JSON.parse(JSON.stringify(o)); }
  function getp(o, p) { return p.split('.').reduce(function (a, k) { return a == null ? undefined : a[k]; }, o); }
  function setp(o, p, v) { var ks = p.split('.'), a = o; for (var i = 0; i < ks.length - 1; i++) a = a[ks[i]]; a[ks[ks.length - 1]] = v; }
  function runOps(st, ops) {
    (ops || []).forEach(function (op) {
      if (op[0] === 'set') setp(st, op[1], op[2]);
      else if (op[0] === 'toggle') setp(st, op[1], !getp(st, op[1]));
      else if (op[0] === 'inc') setp(st, op[1], (getp(st, op[1]) || 0) + 1);
    });
  }
  function $(sel, root) { return (root || document).querySelector(sel); }

  var INPUTS = {}, INPUT_LIST = [];
  D.inputGroups.forEach(function (g) {
    g.items.forEach(function (it) { it.group = g.id; INPUTS[it.id] = it; INPUT_LIST.push(it); });
  });
  var VIA = { robot: '机器人', dualkey: 'DualKey', page: '网页', mind: '系统', remote: '遥控器' };

  function modeName(id, dir) {
    var m = D.modes[id]; if (!m) return id;
    return m.name + (dir && m.dirs && m.dirs[dir] ? '（' + m.dirs[dir].short + '）' : '');
  }
  function modeLabel(st) {
    var m = D.modes[st.mode];
    return st.dir && m.dirs && m.dirs[st.dir] ? m.dirs[st.dir].label : m.label;
  }
  function inputLabel(inp) {
    var g = D.inputGroups.filter(function (x) { return x.id === inp.group; })[0];
    var prefix = { asr: 'ASR ', digits: 'ASR ', touch: '触摸 ', pick: '上滑页 ', quick: '快捷页 ', dk: 'DualKey ', web: '网页 ', webquick: '网页 ', websw: '网页 ', sys: '' }[inp.group] || '';
    return prefix + inp.label + (g ? '' : '');
  }

  /* ================= 引擎 ================= */
  function Ctx(st, inp) {
    this.before = st; this.st = clone(st); this.inp = inp;
    this.code = 'ok'; this.rules = []; this.effects = []; this.notes = [];
    this.toast = null; this.local = null; this.modeChanged = false; this.changedLook = false;
  }
  Ctx.prototype.rule = function (id, extra) { this.rules.push({ id: id, extra: extra || '' }); };

  function applyEffect(c, id) {
    var e = D.effects[id];
    if (!e) return;
    if (e.onlyIn && c.st.mode !== e.onlyIn) { c.notes.push(e.label + ' —— 现在不在' + modeName(e.onlyIn) + '，这一项跳过'); if (e.skipToast) c.skipToast = e.skipToast; return; }
    runOps(c.st, e.ops);
    c.effects.push(id);
    if (e.note) c.notes.push(e.note);
  }
  function guardFails(st, id) {
    var e = D.effects[id];
    var gs = (e && e.guards) || [];
    for (var i = 0; i < gs.length; i++) {
      var g = gs[i], ok = true;
      if (g.need === 'billing') ok = st.billing;
      else if (g.need === 'mouthTts') ok = st.env.mouthTts;
      else if (g.need === 'off') ok = !getp(st, g.path);
      else if (g.need === 'on') ok = !!getp(st, g.path);
      else if (g.need === 'mode') ok = st.mode === g.mode;
      else if (g.need === 'billingToTurnOn') ok = !!getp(st, g.path) || st.billing;
      if (!ok) return g;
    }
    return null;
  }
  function runGuarded(c, list) {
    for (var i = 0; i < list.length; i++) {
      var g = guardFails(c.st, list[i]);
      if (g) {
        c.code = g.code;
        c.notes.push(D.effects[list[i]].label + '：没做（' + D.codes[g.code].label + '）');
        return false;
      }
    }
    list.forEach(function (id) { applyEffect(c, id); });
    return true;
  }
  function changeMode(c, target, dir, opts) {
    var st = c.st, oldMode = st.mode;
    opts = opts || {};
    var same = oldMode === target && (st.dir || null) === (dir || null) && (st.host || null) === (opts.host || null);
    if (same) return false;
    st.mode = target; st.dir = dir || null; st.host = opts.host || null;
    st.return_to = opts.return_to || null;
    if (oldMode !== target) {
      c.modeChanged = true;
      D.onModeChange.forEach(function (id) { applyEffect(c, id); });
      (D.modes[oldMode].onLeave || []).forEach(function (id) { applyEffect(c, id); });
      (D.modes[target].onEnter || []).forEach(function (id) { applyEffect(c, id); });
      st.robot.badge = false;
    }
    c.changedLook = true;
    st.seq += 1; st.by = c.inp.via;
    return true;
  }
  function enterMode(c, target, dir) {
    var st = c.st, cur = D.modes[st.mode], tgt = D.modes[target], opts = {};
    c.rule('enter', modeName(target, dir));
    var hostOn = cur.kind === 'host' || !!st.host;
    if (tgt.kind === 'temporary') {
      // 记下当时显示的模式；事件驱动开着时算手动选模式：事件驱动停，等待中就记 IDLE，不再托管
      opts.return_to = { mode: cur.kind === 'host' ? 'idle' : st.mode, dir: st.dir, host: null };
      c.rule('enter_temp');
    }
    if (cur.kind === 'temporary' && tgt.kind !== 'temporary') c.rule('enter_from_temp');
    if (hostOn && tgt.kind !== 'host') c.rule('enter_from_host');
    changeMode(c, target, dir, opts);
  }
  function leaveCurrent(c, ruleId) {
    var st = c.st, cur = D.modes[st.mode];
    if (cur.leaveTo === '@return') {
      var r = st.return_to;
      c.rule('leave_return', r ? modeName(r.mode, r.dir) : 'IDLE');
      if (r) changeMode(c, r.mode, r.dir, { host: r.host }); else changeMode(c, 'idle', null, {});
      return;
    }
    c.rule(ruleId);
    if (st.host && cur.kind !== 'host') { c.rule('leave_hosted'); changeMode(c, st.host, null, {}); return; }
    if (!changeMode(c, cur.leaveTo || 'idle', null, {})) { c.code = 'already'; c.notes.push('已经在 ' + modeName(st.mode) + '，无处可离开'); }
  }
  function match(list, id) {
    list = list || [];
    for (var i = 0; i < list.length; i++) if (list[i].in === id) return list[i];
    return null;
  }
  function offline(c, force) {
    if (c.inp.via === 'robot' && (c.inp.needsServer || force) && !c.st.env.online) {
      c.code = 'offline'; c.rule('offline'); return true;
    }
    return false;
  }
  function runEntry(c, e) {
    var st = c.st;
    if (e.needMode && st.mode !== e.needMode) { c.code = 'only_talk'; c.rule('say_only_talk'); return; }
    if (e.guest) {
      c.rule(e.rule, modeName(e.guest));
      changeMode(c, e.guest, null, { host: st.mode });
      c.notes.push('角标：' + D.modes[e.guest].label + ' + 小 A；眼睛和单击跟着' + D.modes[e.guest].name + '走');
      return;
    }
    if (e.rule) c.rule(e.rule);
    if (e.alsoRule) c.rule(e.alsoRule);
    if (e.goto) {
      if (!changeMode(c, e.goto, null, {})) c.notes.push('已在' + modeName(e.goto) + '，模式不变');
    }
    (e.force || []).forEach(function (id) { if (c.effects.indexOf(id) < 0) applyEffect(c, id); });
    if (e.ops) { runOps(st, e.ops); }
    if (e.effects) runGuarded(c, e.effects);
    if (e.by) st.by = e.by;
  }
  function touchStep(c) {
    var st = c.st, inp = c.inp, t = D.modes[st.mode].touch[inp.touch], robot = inp.via === 'robot';
    if (t.act === 'badge') {
      if (robot) { st.robot.badge = !st.robot.badge; c.code = 'local'; c.local = 'badge'; c.rule('touch_badge'); }
      else { c.code = 'no_action'; c.rule('touch_badge_dk'); }
      return;
    }
    if (t.act === 'none') {
      if (robot) { c.code = 'local'; c.toast = 'NO TAP'; c.rule('touch_none'); }
      else { c.code = 'no_action'; c.rule('touch_none_dk'); }
      return;
    }
    if (robot && offline(c, true)) return;
    c.rule('touch_server', '补充：' + t.owner);
    var cs = (t.cases || []).filter(function (k) { return !!getp(st, k.if); })[0];
    if (cs) { c.rule(cs.rule); runGuarded(c, cs.effects); return; }
    if (t.effects) runGuarded(c, t.effects);
    else { c.code = t.step1Code || 'not_implemented'; c.rule('touch_shell'); }
  }
  function modePipeline(c) {
    var st = c.st, inp = c.inp, id = inp.id;
    if (offline(c)) return;
    var any = D.anyMode[id];
    if (any) { runEntry(c, any); return; }
    var cur = D.modes[st.mode];
    var mi = cur.inputs && cur.inputs[id];
    if (mi) { c.rule('mode_input'); runEntry(c, mi); return; }
    if (st.host) {
      var h = D.modes[st.host];
      if (match(h.leaveIn, id)) { c.rule('host_leave'); changeMode(c, h.leaveTo || 'idle', null, {}); return; }
      var he = match(h.enterIn, id);
      if (he) {
        if (he.toggle) { c.rule('toggle_leave'); c.rule('host_leave'); changeMode(c, h.leaveTo || 'idle', null, {}); }
        else { c.code = 'already'; c.rule('host_already'); }
        return;
      }
    }
    if (match(cur.leaveIn, id)) { leaveCurrent(c, 'leave'); return; }
    var en = match(cur.enterIn, id);
    if (en) {
      if ((en.dir || null) === (st.dir || null)) {
        if (en.toggle) leaveCurrent(c, 'toggle_leave');
        else { c.code = 'already'; c.rule('already', cur.name); }
      } else {
        c.rule('switch_dir', modeName(st.mode, en.dir));
        changeMode(c, st.mode, en.dir, { host: st.host, return_to: st.return_to });
      }
      return;
    }
    var k, m, e;
    for (k = 0; k < D.modeOrder.length; k++) {
      m = D.modes[D.modeOrder[k]];
      if (D.modeOrder[k] === st.mode) continue;
      e = match(m.enterIn, id);
      if (e) { enterMode(c, D.modeOrder[k], e.dir); return; }
    }
    for (k = 0; k < D.modeOrder.length; k++) {
      m = D.modes[D.modeOrder[k]];
      if (D.modeOrder[k] === st.mode) continue;
      if (match(m.leaveIn, id)) { c.code = 'not_in_mode'; c.toastArg = m.label; c.rule('not_in_mode', '不在' + m.name); return; }
    }
    if (inp.touch) { touchStep(c); return; }
    if (inp.fallback) { c.code = inp.fallback.code; c.rule(inp.fallback.rule); return; }
    c.code = 'unknown_input'; c.rule('unknown');
  }
  var HANDLERS = {
    mode: modePipeline,
    local: function (c, h) {
      c.code = 'local'; c.rule(h.rule);
      if (h.open) c.local = h.open;
      if (h.vol) { var v = Math.max(1, Math.min(5, c.st.robot.volume + h.vol)); if (v === c.st.robot.volume) c.notes.push('音量已经到头'); c.st.robot.volume = v; }
    },
    noop: function (c, h) { c.code = 'no_effect'; c.rule(h.rule); },
    entry: function (c, h) { runEntry(c, h); },
    switch: function (c, h) {
      var on = !!getp(c.st, h.path);
      if (!on && !c.st.billing) { c.code = 'billing_off'; c.rule('switch_blocked'); return; }
      c.rule('switch_toggle'); setp(c.st, h.path, !on);
    },
    billing: function (c, h) {
      if (offline(c)) return;
      if (h.on === c.st.billing) { c.code = 'already'; c.rule(h.on ? 'billing_on' : 'billing_off'); return; }
      c.rule(h.on ? 'billing_on' : 'billing_off');
      applyEffect(c, h.on ? 'billing_on' : 'billing_off_all');
      c.st.seq += 1; c.st.by = c.inp.via;
    },
    preset: function (c, h) {
      if (offline(c)) return;
      var st = c.st, p = D.presets[h.n];
      c.rule('preset', p.n + '「' + p.name + '」');
      if (p.undefined) { c.code = 'undefined_preset'; c.rule('preset_undefined'); return; }
      if (p.mode) {
        if (st.mode === p.mode && !st.host) { c.code = 'already'; c.rule('already', modeName(p.mode)); }
        else enterMode(c, p.mode, null);
      }
      var billingBefore = st.billing;
      if (p.server.length && !runGuarded(c, p.server)) return;
      if (st.billing !== billingBefore) { st.seq += 1; st.by = c.inp.via; }
      if (p.robot.length) {
        if (c.inp.via !== 'page') {
          c.code = 'senses_on_page'; c.rule('preset_robot_robot');
          p.robot.forEach(function (k) { c.notes.push('没做（要在网页上按）：' + D.controls[k].label); });
        } else if (!st.env.online) {
          c.rule('preset_robot_offline');
          p.robot.forEach(function (k) { c.notes.push('没成（机器人离线）：' + D.controls[k].label); });
        } else {
          c.rule('preset_robot');
          p.robot.forEach(function (k) { runOps(st, D.controls[k].ops); c.notes.push(D.controls[k].label + '  （' + D.controls[k].body + '）'); });
        }
      }
      if (c.inp.via === 'robot' && c.code === 'ok') c.toast = c.skipToast || ('PRESET ' + p.n);
    }
  };

  function apply(st, inputId) {
    var inp = INPUTS[inputId];
    var c = new Ctx(st, inp);
    HANDLERS[inp.handler.type](c, inp.handler);
    if (c.toast == null && inp.via === 'robot' && ['ok', 'local'].indexOf(c.code) < 0) {
      c.toast = (D.codes[c.code].toast || '').replace('{mode}', c.toastArg || '');
    }
    return c;
  }

  function stateKey(st) { var s = clone(st); delete s.seq; delete s.by; return JSON.stringify(s); }

  function summarize(c) {
    var b = c.before, a = c.st;
    if (c.code === 'offline') return { t: '离线，不执行', cls: 'blocked' };
    if (a.mode !== b.mode || a.dir !== b.dir || a.host !== b.host) {
      var t = (a.mode === b.mode && a.dir !== b.dir) ? '换方向 → ' + modeName(a.mode, a.dir) : '→ ' + modeName(a.mode, a.dir);
      if (a.host) t += '（托管）';
      var extra = c.effects.filter(function (x) { return x !== 'stop_speech' && x !== 'talk_leave'; }).map(function (x) { return D.effects[x].short; });
      if (extra.length) t += ' + ' + extra.join('、');
      return { t: t, cls: 'change' };
    }
    if (c.local === 'picker') return { t: '打开模式页', cls: 'local' };
    if (c.local === 'quick') return { t: '打开快捷设置', cls: 'local' };
    if (c.local === 'summary') return { t: '打开摘要页', cls: 'local' };
    if (c.local === 'badge') return { t: '翻 badge 页（本地）', cls: 'local' };
    if (c.code === 'ok' && c.effects.length) return { t: c.effects.map(function (x) { return D.effects[x].short; }).join('、'), cls: 'action' };
    if (c.code === 'ok' && stateKey(a) !== stateKey(b)) return { t: '改开关', cls: 'action' };
    if (c.code === 'local' && stateKey(a) !== stateKey(b)) return { t: '本地改（音量）', cls: 'local' };
    var map = {
      already: '不变（已经是）', not_in_mode: '无（' + (c.rules[c.rules.length - 1] || {}).extra + '）', only_talk: '无（只在脱口秀）',
      billing_off: '被拦（计费关着）', mouth_not_tts: '被拦（嘴不在 TTS）', not_implemented: '空壳（第一步没内容）',
      no_effect: '无作用', undefined_preset: '未定义', unknown_input: '无定义', local: '只提示一行'
    };
    return { t: map[c.code] || D.codes[c.code].label, cls: c.code === 'not_implemented' ? 'shell' : (c.code === 'billing_off' || c.code === 'mouth_not_tts' ? 'blocked' : 'none') };
  }

  /* ================= 屏幕（320×240 SVG） ================= */
  function eyePair(kind, color, pool) {
    var s = '';
    [100, 220].forEach(function (cx) {
      if (kind === 'cat') {
        s += '<path d="M' + (cx - 46) + ' 120 Q' + cx + ' 62 ' + (cx + 46) + ' 120 Q' + cx + ' 178 ' + (cx - 46) + ' 120Z" fill="' + color + '"/>' +
          '<ellipse cx="' + cx + '" cy="120" rx="7" ry="34" fill="#1a1205"/><circle cx="' + (cx + 14) + '" cy="104" r="6" fill="#fff8" />';
      } else if (kind === 'mecha') {
        var pts = [[-44, -24], [-24, -42], [44, -42], [44, 24], [24, 42], [-44, 42]].map(function (p) { return (cx + p[0]) + ',' + (120 + p[1]); }).join(' ');
        s += '<polygon points="' + pts + '" fill="none" stroke="' + color + '" stroke-width="5"/>' +
          '<rect x="' + (cx - 30) + '" y="' + (pool === 'calm' ? 116 : 104) + '" width="60" height="' + (pool === 'calm' ? 8 : 30) + '" fill="' + color + '"/>' +
          '<line x1="' + (cx - 38) + '" y1="150" x2="' + (cx + 38) + '" y2="150" stroke="' + color + '" stroke-opacity=".4" stroke-width="2"/>';
      } else if (kind === 'cream') {
        s += '<rect x="' + (cx - 44) + '" y="76" width="88" height="88" rx="40" fill="' + color + '"/>' +
          '<circle cx="' + cx + '" cy="124" r="22" fill="#4a3424"/><circle cx="' + (cx + 9) + '" cy="114" r="7" fill="#fff"/>';
      }
    });
    return s;
  }
  function laughingMan() {
    var g = '#3CE070';
    return '<g transform="translate(160 120)">' +
      '<g class="spin"><circle r="84" fill="none" stroke="' + g + '" stroke-width="1" stroke-dasharray="2 6"/>' +
      '<path id="ring" d="M0,-74 a74,74 0 1,1 -0.1,0" fill="none"/>' +
      '<text font-size="11" fill="' + g + '" font-family="monospace" letter-spacing="2"><textPath href="#ring">hello · hello · hello · hello · hello · hello · hello · hello ·</textPath></text></g>' +
      '<circle r="58" fill="none" stroke="' + g + '" stroke-width="6"/>' +
      '<path d="M-34 14 Q0 48 34 14" fill="none" stroke="' + g + '" stroke-width="7" stroke-linecap="round"/>' +
      '<rect x="-46" y="-26" width="120" height="14" rx="3" fill="' + g + '"/>' +
      '<circle cx="-18" cy="-2" r="6" fill="' + g + '"/><circle cx="18" cy="-2" r="6" fill="' + g + '"/></g>';
  }
  function txt(x, y, s, size, fill, anchor, extra) {
    return '<text x="' + x + '" y="' + y + '" font-size="' + size + '" fill="' + fill + '" text-anchor="' + (anchor || 'middle') + '" ' + (extra || '') + '>' + esc(s) + '</text>';
  }
  function lookBody(st, eyeId, pool) {
    var e = D.eyes[eyeId] ? D.eyes[eyeId].draw : 'idle';
    var m = D.modes[st.mode];
    if (e === 'laughing') return laughingMan();
    if (e === 'cat') return eyePair('cat', '#E8B030');
    if (e === 'mecha') return eyePair('mecha', '#30E070', pool) + txt(10, 222, pool === 'calm' ? 'calm pool' : pool === 'lively' ? 'lively pool' : '', 10, '#30E070', 'start', 'font-family="monospace" opacity=".7"');
    if (e === 'cream') return eyePair('cream', '#F4EAD0');
    if (e === 'subtitle') {
      var d = st.dir && m.dirs ? m.dirs[st.dir] : (D.modes.subtitle.dirs.zh_en);
      return txt(160, 92, '字幕', 44, '#E8E6FF') + txt(160, 128, d.name + '  ' + d.short, 18, '#B8B0FF') +
        '<rect x="40" y="160" width="240" height="10" rx="5" fill="#7C6CF0" opacity=".5"/><rect x="70" y="180" width="180" height="10" rx="5" fill="#7C6CF0" opacity=".35"/>' +
        txt(160, 214, '（第一步只显示方向）', 11, '#8880C0');
    }
    if (e === 'settings') {
      var ret = st.mode === 'settings' && st.return_to ? modeName(st.return_to.mode, st.return_to.dir) : '—';
      var rows = [['音量', String(st.robot.volume)], ['麦克风增益', 'max'], ['俯仰', '10°'], ['计费', st.billing ? '开' : '关'], ['模式', '设置 ← ' + ret]];
      var s = txt(16, 40, '参数', 20, '#EDEDE4', 'start');
      rows.forEach(function (r, i) {
        s += txt(16, 74 + i * 28, r[0], 15, '#A8A89C', 'start') + txt(304, 74 + i * 28, r[1], 15, '#EDEDE4', 'end') +
          '<line x1="16" x2="304" y1="' + (82 + i * 28) + '" y2="' + (82 + i * 28) + '" stroke="#ffffff22"/>';
      });
      return s + txt(160, 222, '只读', 10, '#88887C');
    }
    if (e === 'badge') return txt(160, 128, 'hello', 64, '#FFFFFF', 'middle', 'font-weight="700"') + txt(160, 170, 'collector-0.4.92', 13, '#9A9A9A', 'middle', 'font-family="monospace"');
    return txt(160, 126, 'hello', 16, '#6A6A6A', 'middle', 'font-family="monospace"');
  }
  function cornerTag(st) {
    var m = D.modes[st.mode], online = st.env.online;
    var fill = online ? m.color : '#6B6B6B';
    var dark = isLight(fill);
    var lbl = modeLabel(st), w = 12 + lbl.length * 8.4, x = 314 - w;
    var s = '<rect x="' + x.toFixed(1) + '" y="6" width="' + w.toFixed(1) + '" height="20" rx="4" fill="' + fill + '"/>' +
      txt((x + w / 2).toFixed(1), 20.5, lbl, 13, dark ? '#111' : '#fff', 'middle', 'font-weight="700" font-family="monospace"');
    if (!online) s += txt(x - 4, 20, 'OFF', 11, '#B0B0B0', 'end', 'font-family="monospace"');
    if (st.host) s += '<rect x="300" y="28" width="14" height="14" rx="3" fill="' + D.modes[st.host].color + '"/>' + txt(307, 39, 'A', 11, '#111', 'middle', 'font-weight="700" font-family="monospace"');
    return s;
  }
  function isLight(hex) {
    var h = hex.replace('#', ''); var r = parseInt(h.substr(0, 2), 16), g = parseInt(h.substr(2, 2), 16), b = parseInt(h.substr(4, 2), 16);
    return (r * 299 + g * 587 + b * 114) / 1000 > 150;
  }
  function bars(st) {
    var segs = [['#FFFFFF', false], ['#3A8BFF', st.robot.hearing], ['#FF4040', st.speaking], ['#30D060', true], ['#FFB020', st.robot.m12]];
    var s = '<rect x="0" y="0" width="320" height="3" fill="url(#emo)" opacity=".8"/>';
    segs.forEach(function (sg, i) { s += '<rect x="' + (i * 64) + '" y="237" width="64" height="3" fill="' + sg[0] + '" opacity="' + (sg[1] ? 1 : 0.18) + '"/>'; });
    return s;
  }
  function pickerOverlay(st, interactive) {
    var off = !st.env.online;
    var s = '<rect width="320" height="240" fill="#101014"/>' + txt(12, 22, '模式', 14, '#ddd', 'start') + (off ? txt(250, 22, '离线', 14, '#ff8a8a', 'end') : '');
    D.screens.picker.cells.forEach(function (id, i) {
      var e = matchAnyEnter(id), m = D.modes[e.mode];
      var x = 8 + (i % 3) * 103, y = 32 + Math.floor(i / 3) * 68;
      var cur = st.mode === e.mode && (e.dir || null) === (st.dir || null);
      var fill = off ? '#3a3a3a' : m.color;
      s += '<g ' + (interactive ? 'class="hit" data-input="' + id + '"' : '') + '>' +
        '<rect x="' + x + '" y="' + y + '" width="98" height="62" rx="8" fill="' + fill + '" fill-opacity="' + (cur ? 0.95 : 0.3) + '" stroke="' + (cur ? '#fff' : fill) + '" stroke-width="' + (cur ? 3 : 1) + '"/>' +
        txt(x + 49, y + 30, e.dir ? m.dirs[e.dir].label : m.label, 14, cur && !off && isLight(fill) ? '#111' : '#fff', 'middle', 'font-weight="700" font-family="monospace"') +
        txt(x + 49, y + 50, INPUTS[id].label, 11, cur && !off && isLight(fill) ? '#111' : '#eee') + '</g>';
    });
    return s;
  }
  function quickOverlay(st, interactive) {
    var hit = function (id) { return interactive ? 'class="hit" data-input="' + id + '"' : ''; };
    var s = '<rect width="320" height="240" fill="#101014"/>' + txt(12, 22, '快捷设置', 14, '#ddd', 'start');
    s += txt(16, 56, '音量', 15, '#bbb', 'start') +
      '<g ' + hit('quick.vol_down') + '><rect x="100" y="36" width="44" height="30" rx="6" fill="#333"/>' + txt(122, 57, '−', 18, '#fff') + '</g>' +
      txt(170, 57, String(st.robot.volume), 16, '#fff') +
      '<g ' + hit('quick.vol_up') + '><rect x="196" y="36" width="44" height="30" rx="6" fill="#333"/>' + txt(218, 57, '＋', 18, '#fff') + '</g>';
    s += txt(16, 98, '计费', 15, '#bbb', 'start') +
      '<g ' + hit('quick.billing_on') + '><rect x="100" y="78" width="64" height="30" rx="6" fill="' + (st.billing ? '#2E9E5B' : '#333') + '"/>' + txt(132, 99, '开', 15, '#fff') + '</g>' +
      '<g ' + hit('quick.billing_off') + '><rect x="176" y="78" width="64" height="30" rx="6" fill="' + (!st.billing ? '#B84040' : '#333') + '"/>' + txt(208, 99, '关', 15, '#fff') + '</g>';
    if (!st.billing) s += txt(250, 99, '不花钱', 11, '#9fe0b0', 'start');
    D.presets.forEach(function (p, i) {
      var x = 16 + (i % 5) * 58, y = 124 + Math.floor(i / 5) * 52;
      s += '<g ' + hit('quick.p' + p.n) + '><rect x="' + x + '" y="' + y + '" width="52" height="44" rx="6" fill="' + (p.undefined ? '#222' : '#2c2c3a') + '" stroke="#555"/>' +
        txt(x + 26, y + 20, String(p.n), 16, p.undefined ? '#555' : '#fff', 'middle', 'font-weight="700"') +
        txt(x + 26, y + 36, p.undefined ? '空' : p.name, 9, p.undefined ? '#555' : '#bbb') + '</g>';
    });
    return s;
  }
  function summaryOverlay(st) {
    var g = '#3CE070';
    var lines = D.screens.summary.lines.map(function (l) {
      return l.replace('{hearing}', st.robot.hearing ? 'ON' : 'OFF').replace('{camera}', st.robot.camera ? 'ON' : 'OFF');
    }).concat(['MODE  ' + modeLabel(st) + ' #' + st.seq, 'BILLING ' + (st.billing ? 'ON' : 'OFF')]);
    var s = '<rect width="320" height="240" fill="#000"/>';
    lines.forEach(function (l, i) {
      var isNew = i >= lines.length - 2;
      s += txt(14, 30 + i * 26, l, 14, isNew ? '#FFE070' : g, 'start', 'font-family="monospace"');
    });
    return s;
  }
  function matchAnyEnter(inputId) {
    for (var k = 0; k < D.modeOrder.length; k++) {
      var e = match(D.modes[D.modeOrder[k]].enterIn, inputId);
      if (e) return { mode: D.modeOrder[k], dir: e.dir };
    }
    return { mode: 'idle' };
  }
  function screenSVG(st, opt) {
    opt = opt || {};
    var m = D.modes[st.mode];
    var eye = st.mode === 'idle' && st.robot.badge ? 'badge' : m.look.eye;
    var body;
    if (opt.overlay === 'picker') body = pickerOverlay(st, opt.interactive);
    else if (opt.overlay === 'quick') body = quickOverlay(st, opt.interactive);
    else if (opt.overlay === 'summary') body = summaryOverlay(st);
    else body = '<rect width="320" height="240" fill="' + (eye === 'page_settings' ? '#1A1A18' : '#000') + '"/>' + lookBody(st, eye, m.look.pool);
    var s = '<svg class="screen" viewBox="0 0 320 240" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="' + esc(m.name + ' 屏幕') + '">' +
      '<defs><linearGradient id="emo" x1="0" x2="1"><stop offset="0" stop-color="#4060FF"/><stop offset="1" stop-color="#FF60A0"/></linearGradient></defs>' +
      (opt.interactive && opt.overlay ? body.replace('<rect width="320" height="240"', '<rect class="hit" data-close="1" width="320" height="240"') : body) +
      bars(st) + cornerTag(st);
    if (opt.toast) s += '<rect x="40" y="206" width="240" height="24" rx="5" fill="#000c" stroke="#ffffff55"/>' + txt(160, 223, opt.toast, 12, '#fff', 'middle', 'font-family="monospace"');
    if (opt.flash) s += '<g class="flash"><rect x="40" y="88" width="240" height="64" rx="12" fill="#000d"/>' + txt(160, 131, modeLabel(st), 28, m.color, 'middle', 'font-weight="700" font-family="monospace"') + '</g>';
    if (!st.env.online && !opt.overlay) s += '<rect width="320" height="240" fill="#808080" opacity=".18"/>';
    return s + '</svg>';
  }

  /* ================= 1. 模式一览 ================= */
  function badge(status) { return '<span class="badge b-' + status + '">' + esc(D.stepBadges[status]) + '</span>'; }
  function kindLabel(k) { return { normal: '普通模式', temporary: '临时层', host: '外层', idle: '空闲' }[k]; }
  function tagChip(m, text) { return '<span class="tag" style="background:' + m.color + ';color:' + (isLight(m.color) ? '#111' : '#fff') + '">' + esc(text || m.label) + '</span>'; }

  function renderModes() {
    var html = '';
    D.modeOrder.forEach(function (id) {
      var m = D.modes[id];
      var st = clone(D.initialState); st.mode = id; if (m.dirs) st.dir = Object.keys(m.dirs)[0];
      var eye = D.eyes[m.look.eye];
      var tapRow = function (t) {
        var step = t.effects ? '第一步：' + t.effects.map(function (x) { return D.effects[x].short; }).join('、') :
          t.act === 'badge' ? '第一步：本地翻 badge' : t.act === 'none' ? '第一步：无' : '第一步：空壳（' + t.step1Code + '）';
        if (t.cases) step += t.cases.map(function (k) { return '；' + k.design + '：' + k.effects.map(function (x) { return D.effects[x].short; }).join('、'); }).join('');
        return '<b>补充：</b>' + esc(t.owner) + ' <span class="muted">→ ' + esc(t.design) + '；' + esc(step) + (t.deferred ? '（推迟）' : '') + '</span>';
      };
      var dirs = m.dirs ? Object.keys(m.dirs).map(function (k) { return m.dirs[k].name + '（' + m.dirs[k].short + '，' + m.dirs[k].label + '）'; }).join('；') : '';
      html += '<article class="mode-card" id="mode-' + id + '" style="--mc:' + m.color + '">' +
        '<header>' + tagChip(m) + '<h3>' + esc(m.name) + (m.isDefault ? ' <small>默认</small>' : '') + '</h3><span class="kind k-' + m.kind + '">' + kindLabel(m.kind) + '</span>' + badge(m.step1.status) + '</header>' +
        '<div class="mc-body"><div class="mini">' + screenSVG(st, {}) + '<div class="mini-cap">' + esc(eye.name) + (m.look.pool !== 'none' ? ' · ' + esc(D.pools[m.look.pool].split('：')[0]) : '') + '</div></div>' +
        '<dl>' +
        '<dt>主人原话</dt><dd><q>' + esc(m.owner) + '</q></dd>' +
        '<dt>眼睛 / 屏幕</dt><dd><q>' + esc(m.ownerEye) + '</q> → ' + esc(eye.name) + (m.look.pool !== 'none' ? '（' + esc(D.pools[m.look.pool]) + '）' : '') + '<br><span class="muted">' + esc(m.lookNote) + '</span></dd>' +
        '<dt>角标</dt><dd>' + tagChip(m) + ' <code>' + esc(m.color) + '</code>' + (dirs ? '<br><span class="muted">' + esc(dirs) + '</span>' : '') + '</dd>' +
        '<dt>ASR 口令</dt><dd>' + esc(m.words.asr) + '</dd>' +
        '<dt>DualKey 摇杆</dt><dd>' + esc(m.words.dualkey) + '</dd>' +
        '<dt>其他入口</dt><dd>' + esc(m.words.other) + '</dd>' +
        '<dt>单击</dt><dd>' + tapRow(m.touch.tap) + '</dd>' +
        '<dt>双击</dt><dd>' + tapRow(m.touch.double) + '</dd>' +
        '<dt>可能花钱</dt><dd><ul>' + m.paid.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul></dd>' +
        '<dt>进入时</dt><dd><ul>' + m.enterDo.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul></dd>' +
        '<dt>离开时</dt><dd><ul>' + m.leaveDo.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul></dd>' +
        '<dt>第一步</dt><dd>' + esc(m.step1.text) + '</dd>' +
        '<dt>以后</dt><dd class="muted">' + esc(m.future) + '</dd>' +
        '</dl></div><button class="try" data-try-mode="' + id + '">在模拟器里进入</button></article>';
    });
    $('#mode-grid').innerHTML = html;
    $('#layers').innerHTML = D.layers.map(function (l) { return '<div><b>' + esc(l.title) + '</b>：' + esc(l.text) + '</div>'; }).join('');
  }

  /* ================= 2. 模拟器 ================= */
  var SIM = { st: clone(D.initialState), log: [], overlay: null, toast: null, flash: false, n: 0 };

  function simApply(inputId) {
    var inp = INPUTS[inputId];
    var before = SIM.st;
    // 触摸首页手势：先回首页
    if (inp.group === 'touch' && SIM.overlay) SIM.overlay = null;
    var c = apply(before, inputId);
    SIM.st = c.st;
    if (c.local === 'picker' || c.local === 'quick' || c.local === 'summary') SIM.overlay = SIM.overlay === c.local ? null : c.local;
    else if (inp.group === 'pick' && c.code !== 'offline') SIM.overlay = null;
    if (c.modeChanged) SIM.overlay = (inp.group === 'quick') ? SIM.overlay : null;
    SIM.toast = c.toast || null;
    SIM.flash = c.modeChanged;
    SIM.n += 1;
    SIM.log.unshift({ n: SIM.n, at: new Date(), c: c, sum: summarize(c) });
    if (SIM.log.length > 120) SIM.log.pop();
    renderSim();
  }

  function renderState() {
    var st = SIM.st, m = D.modes[st.mode];
    var rows = '<div class="cur" style="--mc:' + m.color + '">' + tagChip(m, modeLabel(st)) +
      '<div><div class="cur-name">' + esc(modeName(st.mode, st.dir)) + (st.host ? ' <small>（' + esc(D.modes[st.host].name) + '托管中）</small>' : '') + '</div>' +
      '<div class="muted">眼睛：' + esc(D.eyes[st.mode === 'idle' && st.robot.badge ? 'badge' : m.look.eye].name) + ' · 角标 ' + esc(modeLabel(st)) + (st.host ? ' + A' : '') + ' · seq ' + st.seq + ' · by ' + esc(st.by) + '</div>' +
      (st.return_to ? '<div class="muted">关掉设置会回到：' + esc(modeName(st.return_to.mode, st.return_to.dir)) + (st.return_to.host ? '（托管）' : '') + '</div>' : '') +
      '</div></div>';
    var groups = {}, order = [];
    D.spendItems.forEach(function (it) { if (!groups[it.group]) { groups[it.group] = []; order.push(it.group); } groups[it.group].push(it); });
    var spending = [];
    var sp = order.map(function (g) {
      return '<div class="sp-group"><div class="sp-title">' + esc(g) + '</div>' + groups[g].map(function (it) {
        var on = !!getp(st, it.path);
        var missing = (it.needs || []).filter(function (n) {
          if (n === 'billing') return !st.billing;
          if (n === 'mouthTts') return !st.env.mouthTts;
          if (n.indexOf('mode:') === 0) return st.mode !== n.slice(5);
          return false;
        }).map(function (n) { return n === 'billing' ? '计费关着' : n === 'mouthTts' ? '嘴不在 TTS' : '只在' + modeName(n.slice(5)); });
        if (on && it.paid) spending.push(it.label);
        var cls = it.master ? (on ? 'on master' : 'off master') : on ? (it.paid ? 'on paid' : 'on') : 'off';
        var state = on ? (it.onLabel || '开') : (it.offLabel || '关');
        return '<div class="sp ' + cls + '"><span class="dot"></span><span class="sp-l">' + esc(it.label) + '</span><span class="sp-s">' + esc(state) + '</span>' +
          (it.paid ? '<span class="sp-a">' + (missing.length ? '不能开：' + esc(missing.join('、')) : '可以开') + '</span>' : '') + '</div>';
      }).join('') + '</div>';
    }).join('');
    var spendLine = '<div class="spend-line ' + (spending.length ? 'is' : 'no') + '">' + (spending.length ? '正在花钱：' + esc(spending.join('、')) : (st.billing ? '现在没有在花钱（总闸开着，可以打开）' : '现在不花钱（计费总闸关）')) + '</div>';
    $('#sim-state').innerHTML = rows + spendLine + '<div class="sp-wrap">' + sp + '</div>';
  }

  function renderInputs() {
    var html = '';
    D.inputGroups.forEach(function (g) {
      html += '<fieldset class="ig"><legend>' + esc(g.name) + '</legend><div class="ig-btns">';
      g.items.forEach(function (it) {
        var c = apply(SIM.st, it.id), s = summarize(c);
        var cur = it.group === 'web' && (function () { var e = matchAnyEnter(it.id); return e.mode === SIM.st.mode && (e.dir || null) === (SIM.st.dir || null); })();
        html += '<button class="in ' + s.cls + (cur ? ' current' : '') + (it.deferred ? ' deferred' : '') + '" data-input="' + it.id + '" title="' + esc((it.was ? it.was + '；' : '') + (it.deferred ? '第一步推迟：' + it.deferred + '；' : '') + (it.note || '')) + '">' +
          '<span class="in-l">' + esc(it.label) + (it.deferred ? ' <i class="later">以后</i>' : '') + '</span><span class="in-s">' + esc(s.t) + '</span></button>';
      });
      html += '</div></fieldset>';
    });
    $('#sim-inputs').innerHTML = html;
  }

  function ruleLine(r) {
    var R = D.rules[r.id] || { text: r.id, ref: '' };
    return '<li><span class="r-t">' + esc(R.text) + '</span>' + (r.extra ? ' <b>' + esc(r.extra) + '</b>' : '') + ' <span class="ref">' + esc(R.ref) + '</span></li>';
  }
  function detailHtml(c) {
    var inp = c.inp;
    var h = '<ul class="rules">' + c.rules.map(ruleLine).join('') + '</ul>';
    if (c.effects.length) h += '<div class="eff">做了：' + c.effects.map(function (x) { return esc(D.effects[x].label); }).join('；') + '</div>';
    if (c.notes.length) h += '<ul class="notes">' + c.notes.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul>';
    if (c.toast) h += '<div class="toast-l">机器人屏幕提示：<code>' + esc(c.toast) + '</code></div>';
    if (inp.deferred) h += '<div class="deferred-l">第一步推迟：' + esc(inp.deferred) + '</div>';
    if (inp.was) h += '<div class="muted">' + esc(inp.was) + '</div>';
    return h;
  }
  function renderLog() {
    $('#sim-log').innerHTML = SIM.log.length ? SIM.log.map(function (e) {
      var c = e.c, t = e.at;
      var hh = ('0' + t.getHours()).slice(-2) + ':' + ('0' + t.getMinutes()).slice(-2) + ':' + ('0' + t.getSeconds()).slice(-2);
      return '<li class="log-e ' + e.sum.cls + '"><div class="log-h"><span class="log-n">#' + e.n + ' ' + hh + '</span>' +
        '<span class="via">' + esc(VIA[c.inp.via] || c.inp.via) + '</span><b>' + esc(inputLabel(c.inp)) + '</b>' +
        '<span class="arrow">→</span><span class="log-s">' + esc(e.sum.t) + '</span><span class="code">' + esc(c.code) + '</span></div>' +
        detailHtml(c) + '<div class="log-now">之后：' + esc(modeName(c.st.mode, c.st.dir)) + (c.st.host ? '（托管）' : '') + ' · 计费' + (c.st.billing ? '开' : '关') + '</div></li>';
    }).join('') : '<li class="muted">点右边任何一个输入，这里会写出它触发了哪条规则。</li>';
  }
  function renderSim() {
    $('#sim-screen').innerHTML = screenSVG(SIM.st, { overlay: SIM.overlay, interactive: true, toast: SIM.toast, flash: SIM.flash });
    SIM.flash = false;
    $('#env-online').checked = SIM.st.env.online;
    $('#env-tts').checked = SIM.st.env.mouthTts;
    $('#sim-overlay-name').textContent = SIM.overlay ? D.screens[SIM.overlay].title + '（点屏幕空白处关闭）' : '首页';
    renderState(); renderInputs(); renderLog();
  }

  /* ================= 3. 状态图 ================= */
  function renderDiagram() {
    var G = D.diagram, W = 150, H = 56;
    var pos = {};
    Object.keys(G.nodes).forEach(function (k) { pos[k] = G.nodes[k]; });
    Object.keys(G.pseudo).forEach(function (k) { pos[k] = G.pseudo[k]; });
    var edges = [];
    D.modeOrder.forEach(function (id) {
      var m = D.modes[id];
      if (id === 'idle' || m.kind === 'temporary') return;
      var enter = [], leave = [], toggle = false;
      m.enterIn.forEach(function (e) {
        var inp = INPUTS[e.in]; if (!inp) return;
        if (inp.group === 'asr' || inp.group === 'dk') enter.push(inp.short || inp.label);
        if (e.toggle && inp.group === 'dk') toggle = true;
      });
      m.leaveIn.forEach(function (e) { var inp = INPUTS[e.in]; if (inp && inp.group === 'asr') leave.push(inp.short || inp.label); });
      if (toggle) leave.push('再推同一方向');
      edges.push({ from: 'idle', to: id, label: enter.join(' / '), kind: 'solid', bend: 30 });
      edges.push({ from: id, to: 'idle', label: leave.join(' / '), kind: 'leave', bend: 30 });
    });
    G.extraEdges.forEach(function (e) { edges.push(e); });

    function edgePt(c, tx, ty, w, h) {
      var dx = tx - c.x, dy = ty - c.y;
      if (!dx && !dy) return { x: c.x, y: c.y };
      var s = Math.min((w / 2 + 4) / Math.abs(dx || 1e-9), (h / 2 + 4) / Math.abs(dy || 1e-9));
      return { x: c.x + dx * s, y: c.y + dy * s };
    }
    var svg = '<svg viewBox="0 0 ' + G.width + ' ' + G.height + '" class="diagram" role="img" aria-label="模式状态图" xmlns="http://www.w3.org/2000/svg">' +
      '<defs>' + ['solid', 'leave', 'return', 'deferred'].map(function (k) {
        return '<marker id="ar-' + k + '" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" class="ah-' + k + '"/></marker>';
      }).join('') + '</defs>';
    var hb = G.host;
    svg += '<rect x="' + hb.x + '" y="' + hb.y + '" width="' + hb.w + '" height="' + hb.h + '" rx="14" class="hostbox"/>' +
      '<text x="' + (hb.x + hb.w - 12) + '" y="' + (hb.y + 18) + '" class="hostlbl" text-anchor="end">' + esc(hb.label) + '</text>';
    var labels = '';
    edges.forEach(function (e) {
      if (e.self) {
        var p = pos[e.self];
        svg += '<path d="M' + (p.x - 30) + ' ' + (p.y - H / 2) + ' C' + (p.x - 40) + ' ' + (p.y - H / 2 - 50) + ' ' + (p.x + 40) + ' ' + (p.y - H / 2 - 50) + ' ' + (p.x + 30) + ' ' + (p.y - H / 2 - 4) + '" class="e-solid" marker-end="url(#ar-solid)"/>';
        labels += '<text x="' + p.x + '" y="' + (p.y - H / 2 - 44) + '" class="elbl" text-anchor="middle">' + esc(e.label) + '</text>';
        return;
      }
      var a = pos[e.from], b = pos[e.to];
      var mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2, dx = b.x - a.x, dy = b.y - a.y, len = Math.sqrt(dx * dx + dy * dy) || 1;
      var bend = e.bend == null ? 0 : e.bend;
      var cx = mx - dy / len * bend, cy = my + dx / len * bend;
      var wa = G.pseudo[e.from] ? 110 : W, ha = G.pseudo[e.from] ? 46 : H, wb = G.pseudo[e.to] ? 110 : W, hbb = G.pseudo[e.to] ? 46 : H;
      var p0 = edgePt(a, cx, cy, wa, ha), p2 = edgePt(b, cx, cy, wb, hbb);
      svg += '<path d="M' + p0.x.toFixed(1) + ' ' + p0.y.toFixed(1) + ' Q' + cx.toFixed(1) + ' ' + cy.toFixed(1) + ' ' + p2.x.toFixed(1) + ' ' + p2.y.toFixed(1) + '" class="e-' + e.kind + '" marker-end="url(#ar-' + e.kind + ')"/>';
      if (e.label) {
        var lx = 0.25 * p0.x + 0.5 * cx + 0.25 * p2.x, ly = 0.25 * p0.y + 0.5 * cy + 0.25 * p2.y;
        var nx = -dy / len * (bend >= 0 ? 1 : -1), ny = dx / len * (bend >= 0 ? 1 : -1);
        var anchor = Math.abs(nx) > 0.35 ? (nx > 0 ? 'start' : 'end') : 'middle';
        lx += nx * 6; ly += ny * 6 + (Math.abs(nx) > 0.35 ? 4 : (ny > 0 ? 13 : -4));
        labels += '<text x="' + lx.toFixed(1) + '" y="' + ly.toFixed(1) + '" class="elbl e-' + e.kind + '-t" text-anchor="' + anchor + '">' + e.label.split('\n').map(function (ln, li) { return '<tspan x="' + lx.toFixed(1) + '" dy="' + (li ? 14 : 0) + '">' + esc(ln) + '</tspan>'; }).join('') + '</text>';
      }
    });
    Object.keys(G.nodes).forEach(function (id) {
      var p = pos[id], m = D.modes[id];
      svg += '<g class="node" data-mode="' + id + '" tabindex="0"><rect x="' + (p.x - W / 2) + '" y="' + (p.y - H / 2) + '" width="' + W + '" height="' + H + '" rx="10" style="fill:' + m.color + '22;stroke:' + m.color + '"/>' +
        '<rect x="' + (p.x - W / 2 + 8) + '" y="' + (p.y - 10) + '" width="44" height="20" rx="4" fill="' + m.color + '"/>' +
        '<text x="' + (p.x - W / 2 + 30) + '" y="' + (p.y + 4) + '" text-anchor="middle" class="ntag" fill="' + (isLight(m.color) ? '#111' : '#fff') + '">' + esc(m.label) + '</text>' +
        '<text x="' + (p.x - W / 2 + 58) + '" y="' + (p.y - 2) + '" class="nname">' + esc(m.name) + '</text>' +
        '<text x="' + (p.x - W / 2 + 58) + '" y="' + (p.y + 15) + '" class="nsub">' + esc(kindLabel(m.kind) + (m.isDefault ? ' · 默认' : '') + (id === 'idle' ? ' · 预设 4' : '')) + '</text></g>';
    });
    Object.keys(G.pseudo).forEach(function (id) {
      var p = pos[id];
      svg += '<g class="pnode"><rect x="' + (p.x - 55) + '" y="' + (p.y - 23) + '" width="110" height="46" rx="23"/>' +
        '<text x="' + p.x + '" y="' + (p.y - 1) + '" text-anchor="middle" class="nname">' + esc(p.label) + '</text>' +
        '<text x="' + p.x + '" y="' + (p.y + 15) + '" text-anchor="middle" class="nsub">' + esc(p.sub) + '</text></g>';
    });
    svg += labels + '</svg>';
    $('#diagram').innerHTML = svg;
    $('#diagram-legend').innerHTML = '<ul>' + G.legend.map(function (l) { return '<li>' + esc(l) + '</li>'; }).join('') +
      '<li><span class="lg lg-solid"></span>进入　<span class="lg lg-leave"></span>离开　<span class="lg lg-return"></span>回到原来的模式　<span class="lg lg-deferred"></span>以后（第一步不会发生）</li></ul>';
  }

  /* ================= 4. 触发表 ================= */
  var MX = { cond: 'normal', groups: {}, sel: null };
  D.inputGroups.forEach(function (g) { MX.groups[g.id] = ['quick', 'webquick'].indexOf(g.id) < 0; });
  function matrixState(modeId) {
    var st = clone(D.initialState), m = D.modes[modeId];
    st.mode = modeId; if (m.dirs) st.dir = Object.keys(m.dirs)[0];
    if (m.kind === 'temporary') st.return_to = { mode: 'talk', dir: null, host: null };
    if (MX.cond === 'billing_off') { st.billing = false; }
    if (MX.cond === 'offline') st.env.online = false;
    if (MX.cond === 'prefetch') { st.talk.prefetch = modeId === 'talk'; }
    if (MX.cond === 'speaking') { st.speaking = modeId === 'talk'; }
    return st;
  }
  function renderMatrix() {
    var conds = [['normal', '正常（计费开、在线、嘴 TTS）'], ['billing_off', '计费总闸关'], ['offline', '机器人离线'], ['prefetch', '脱口秀里提前写已开'], ['speaking', '脱口秀里正在说']];
    $('#mx-cond').innerHTML = conds.map(function (c) { return '<label><input type="radio" name="mxc" value="' + c[0] + '"' + (MX.cond === c[0] ? ' checked' : '') + '> ' + esc(c[1]) + '</label>'; }).join('');
    $('#mx-groups').innerHTML = D.inputGroups.map(function (g) { return '<label><input type="checkbox" data-mxg="' + g.id + '"' + (MX.groups[g.id] ? ' checked' : '') + '> ' + esc(g.name.split('（')[0]) + '</label>'; }).join('');
    var head = '<tr><th class="rowh">输入</th>' + D.modeOrder.map(function (id) {
      var m = D.modes[id]; return '<th>' + tagChip(m, m.dirs ? m.dirs[Object.keys(m.dirs)[0]].label : m.label) + '<div>' + esc(modeName(id, m.dirs ? Object.keys(m.dirs)[0] : null)) + '</div></th>';
    }).join('') + '</tr>';
    var body = '';
    D.inputGroups.forEach(function (g) {
      if (!MX.groups[g.id]) return;
      body += '<tr class="gh"><th colspan="' + (D.modeOrder.length + 1) + '">' + esc(g.name) + '</th></tr>';
      g.items.forEach(function (it) {
        body += '<tr><th class="rowh">' + esc(it.label) + (it.deferred ? ' <i class="later">以后</i>' : '') + '</th>';
        D.modeOrder.forEach(function (id) {
          var c = apply(matrixState(id), it.id), s = summarize(c);
          var sel = MX.sel && MX.sel[0] === it.id && MX.sel[1] === id;
          body += '<td class="cell ' + s.cls + (sel ? ' sel' : '') + '" data-cell="' + it.id + '|' + id + '" tabindex="0">' + esc(s.t) + '</td>';
        });
        body += '</tr>';
      });
    });
    $('#matrix').innerHTML = '<thead>' + head + '</thead><tbody>' + body + '</tbody>';
    renderMatrixDetail();
  }
  function renderMatrixDetail() {
    var box = $('#mx-detail');
    if (!MX.sel) { box.innerHTML = '<span class="muted">点表里任何一格，这里显示规则原文。</span>'; return; }
    var inp = INPUTS[MX.sel[0]], c = apply(matrixState(MX.sel[1]), MX.sel[0]), s = summarize(c);
    box.innerHTML = '<div class="log-h"><span class="via">' + esc(VIA[inp.via] || inp.via) + '</span><b>' + esc(inputLabel(inp)) + '</b> 在 <b>' + esc(modeName(MX.sel[1], c.before.dir)) + '</b>' +
      '<span class="arrow">→</span><span class="log-s">' + esc(s.t) + '</span><span class="code">' + esc(c.code) + '</span></div>' + detailHtml(c);
  }

  /* ================= 5. 滑动页 ================= */
  function renderSwipes() {
    var st = clone(D.initialState);
    $('#swipe-grid').innerHTML = ['picker', 'quick', 'summary'].map(function (k) {
      var sc = D.screens[k];
      return '<figure class="swipe"><figcaption>' + esc(sc.title) + '</figcaption><div class="screen-frame">' + screenSVG(st, { overlay: k }) + '</div>' +
        '<ul>' + sc.notes.map(function (n) { return '<li>' + esc(n) + '</li>'; }).join('') + '</ul></figure>';
    }).join('');
  }

  /* ================= 6. 预设 ================= */
  function renderPresets() {
    var rows = D.presets.map(function (p) {
      var server = p.mode ? ['进入 ' + modeName(p.mode)] : [];
      server = server.concat(p.server.map(function (x) {
        var e = D.effects[x]; var g = (e.guards || []).map(function (gg) { return D.codes[gg.code].label; });
        return e.label + (g.length ? '（拦：' + g.join('、') + '）' : '');
      }));
      var robot = p.robot.map(function (k) { return D.controls[k].label; });
      return '<tr class="' + (p.undefined ? 'undef' : '') + '"><td class="pn">' + p.n + '</td><td><q>' + esc(p.owner) + '</q></td><td><b>' + esc(p.name) + '</b>' +
        (p.decided ? ' <span class="badge b-decided">主人已定 2026-11-14</span>' : '') + '</td><td>' + (server.length ? esc(server.join('；')) : '—') + '</td><td>' + (robot.length ? esc(robot.join('；')) : '不动') +
        '</td><td class="muted">' + esc(p.note) + '</td><td><button class="try" data-try-input="web.p' + p.n + '">试</button></td></tr>';
    }).join('');
    $('#presets').innerHTML = '<thead><tr><th>键</th><th>主人原话</th><th>名字</th><th>服务器那一半</th><th>机器人那一半（control）</th><th>说明</th><th></th></tr></thead><tbody>' + rows + '</tbody>';
  }

  /* ================= 7. 任务 ================= */
  function renderTasks() {
    var n = 0;
    $('#tasks').innerHTML = D.tasks.map(function (g) {
      return '<section class="tg"><h3>' + esc(g.group) + '</h3>' + g.items.map(function (t) {
        n++;
        return '<div class="task' + (t.ask ? ' ask' : '') + '"><div class="task-h"><code>' + esc(t.id) + '</code> ' + esc(t.title) + '</div>' +
          '<div class="muted"><code>' + esc(t.files) + '</code></div><div>' + esc(t.note) + '</div>' +
          (t.ask ? '<div class="ask-flag">⏸ ' + esc(t.ask) + '</div>' : '') + '</div>';
      }).join('') + '</section>';
    }).join('');
    $('#task-count').textContent = n;
    $('#deferred').innerHTML = D.deferredList.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('');
  }

  /* ================= 8. 问题 / 差异 / 含糊点 ================= */
  function renderQuestions() {
    $('#questions').innerHTML = D.questions.map(function (q) {
      return '<div class="q"><div class="q-h"><span class="qn">' + q.n + '</span><b>' + esc(q.q) + '</b>' +
        (q.decided ? '<span class="badge b-decided">主人已定 2026-11-14</span>' : '<span class="badge b-pending">待主人定</span>') + '</div>' +
        '<div class="q-def">默认（【我定】）：' + esc(q.proposed) + '</div>' +
        (q.decided ? '<div class="q-ans">主人：' + esc(q.answer) + '<div class="muted">原话：<q>' + esc(q.ownerWords) + '</q></div></div>' : '') +
        '<div class="muted">影响：' + esc(q.impact) + '</div></div>';
    }).join('') + '<div class="q"><div class="q-h"><b>还没想好</b><span class="badge b-deferred">以后再说</span></div>' + esc(D.openNote) + '</div>';
    $('#decisions').innerHTML = '<div class="q-h"><b>主人 2026-11-14 两轮回答（设计 §1.2）</b><span class="badge b-decided">主人已定 2026-11-14</span></div><ol class="dec">' +
      D.ownerAlsoConfirmed.map(function (x) { return '<li>' + esc(x.replace(/^\d+\.\s*/, '')) + '</li>'; }).join('') + '</ol>';
    $('#timing').innerHTML = '<thead><tr><th>规则</th><th>值</th><th>结果</th></tr></thead><tbody>' + D.timing.map(function (r) {
      return '<tr><td>' + esc(r[0]) + '</td><td>' + esc(r[1]) + '</td><td><code>' + esc(r[2]) + '</code></td></tr>';
    }).join('') + '</tbody>';
    $('#diffs').innerHTML = '<thead><tr><th>哪里</th><th>主人补充 / 第一版</th><th>设计现在</th><th>依据</th></tr></thead><tbody>' + D.differences.map(function (d) {
      return '<tr><td><b>' + esc(d.topic) + '</b></td><td>' + esc(d.source) + '</td><td>' + esc(d.design) + '</td><td><span class="badge b-tag">' + esc(d.tag) + '</span> <span class="ref">' + esc(d.ref) + '</span></td></tr>';
    }).join('') + '</tbody>';
    $('#ambig').innerHTML = D.ambiguities.map(function (a) { return '<li><b>' + esc(a.title) + '</b>：' + esc(a.text) + '</li>'; }).join('');
  }

  /* ================= 事件 ================= */
  document.addEventListener('click', function (ev) {
    var t = ev.target.closest('[data-input],[data-close],[data-try-mode],[data-try-input],[data-cell],#sim-reset,.node');
    if (!t) return;
    if (t.id === 'sim-reset') { SIM.st = clone(D.initialState); SIM.log = []; SIM.overlay = null; SIM.toast = null; renderSim(); return; }
    if (t.hasAttribute('data-close')) { SIM.overlay = null; SIM.toast = null; renderSim(); return; }
    if (t.hasAttribute('data-input')) { simApply(t.getAttribute('data-input')); return; }
    if (t.hasAttribute('data-try-input')) { simApply(t.getAttribute('data-try-input')); $('#sim').scrollIntoView({ behavior: 'smooth' }); return; }
    if (t.hasAttribute('data-try-mode') || t.classList.contains('node')) {
      var id = t.getAttribute('data-try-mode') || t.getAttribute('data-mode');
      var web = INPUT_LIST.filter(function (i) { return i.group === 'web' && matchAnyEnter(i.id).mode === id; })[0];
      if (web) {
        var e = matchAnyEnter(web.id);
        if (!(SIM.st.mode === id && (e.dir || null) === (SIM.st.dir || null))) simApply(web.id);
      }
      $('#sim').scrollIntoView({ behavior: 'smooth' });
      return;
    }
    if (t.hasAttribute('data-cell')) { MX.sel = t.getAttribute('data-cell').split('|'); renderMatrix(); return; }
  });
  document.addEventListener('keydown', function (ev) {
    if ((ev.key === 'Enter' || ev.key === ' ') && ev.target.hasAttribute && ev.target.hasAttribute('data-cell')) { ev.preventDefault(); ev.target.click(); }
  });
  document.addEventListener('change', function (ev) {
    var t = ev.target;
    if (t.id === 'env-online') { SIM.st.env.online = t.checked; SIM.log.unshift({ n: ++SIM.n, at: new Date(), c: envCtx(t.checked ? '机器人连上 /mouth（快照马上推一次）' : '机器人和服务器断开：角标变灰、加 OFF；本地的东西照常'), sum: { t: t.checked ? '在线' : '离线', cls: 'none' } }); renderSim(); }
    if (t.id === 'env-tts') { SIM.st.env.mouthTts = t.checked; SIM.log.unshift({ n: ++SIM.n, at: new Date(), c: envCtx(t.checked ? '嘴切到 TTS 档' : '嘴不在 TTS 档：说一轮、提前写都要求 TTS 档'), sum: { t: t.checked ? '嘴 = TTS' : '嘴 ≠ TTS', cls: 'none' } }); renderSim(); }
    if (t.name === 'mxc') { MX.cond = t.value; renderMatrix(); }
    if (t.hasAttribute && t.hasAttribute('data-mxg')) { MX.groups[t.getAttribute('data-mxg')] = t.checked; renderMatrix(); }
  });
  function envCtx(text) {
    return { inp: { via: 'mind', label: '环境', group: 'sys' }, code: 'env', rules: [], effects: [], notes: [text], toast: null, st: clone(SIM.st) };
  }

  /* ================= 启动 ================= */
  function boot() {
    document.title = D.meta.title;
    $('#meta').innerHTML = esc(D.meta.version) + '。' + esc(D.meta.note) + '<br><span class="muted">依据：' + D.meta.sources.map(esc).join('；') + '</span>';
    renderModes(); renderSim(); renderDiagram(); renderMatrix(); renderSwipes(); renderPresets(); renderTasks(); renderQuestions();
  }
  // 给本地检查用：window.__modes.apply(state, inputId)
  window.__modes = { apply: apply, initial: function () { return clone(D.initialState); }, summarize: summarize, inputs: INPUTS };
  boot();
})();
