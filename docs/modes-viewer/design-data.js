/*
 * hello 模式查看器：全部内容都在这一个对象里。
 * 改设计（审阅、主人回答）只改这个文件；app.js 只按这里的数据渲染和推演。
 *
 * 依据：
 *   docs/superpowers/specs/2026-11-14-hello-modes-design-v2.md（下文“设计 §x”）
 *   docs/superpowers/plans/2026-11-14-hello-modes-step1-framework.md（下文“计划”）
 *   主人 2026-11-14 的补充原文（设计 §1 引用）
 *   主人 2026-11-14 对第 12 节四个问题的回答（.superpowers/sdd/2026-11-14-modes-owner-answers.md）
 *
 * 公开仓库：只写“主人”“人物甲”，不放名字、地址、口令。
 */
(function () {
  'use strict';

  /* ---------- 眼睛 / 屏幕外观词表（快照 look.eye） ---------- */
  var eyes = {
    laughing_man: { name: '笑面男', note: '绿色笑面男 + 文字环（今天的首页）', draw: 'laughing' },
    v05: { name: '金色猫眼 v05', note: 'SD 上的眼睛款式，30 种状态', draw: 'cat' },
    v14: { name: '绿色机甲眼 v14', note: 'SD 上的眼睛款式，30 种状态', draw: 'mecha' },
    v18: { name: '奶油白眼睛 v18', note: 'SD 上的眼睛款式，30 种状态', draw: 'cream' },
    page_subtitle: { name: '字幕页', note: '第一步占位：大字“字幕”+ 方向', draw: 'subtitle' },
    page_settings: { name: '参数页', note: '第一步占位：只读列出音量、麦克风增益、俯仰、计费、模式', draw: 'settings' },
    page_idle: { name: 'IDLE 页', note: '黑底小字 hello；单击切 badge 页', draw: 'idle' },
    badge: { name: 'badge 页', note: '大字 hello + 固件版本（机器人本地翻页）', draw: 'badge' }
  };

  var pools = {
    none: '该款默认的自然循环',
        lively: '热闹的几种：迎接、开心、好奇、搞怪、惊讶'
  };

  /* ---------- 八个模式（设计 §3.2，计划 Task A1） ----------
   * enterIn / leaveIn：哪些输入进入 / 离开这个模式（in = 输入 id，dir = 字幕方向，toggle = 已在这个模式时再按一次 = 离开）
   * leaveTo：离开去哪；'@return' = 回到进入前的模式（设置层）
   * inputs：只在这个模式里有特殊意思的输入
   * touch：首页单击 / 双击。act：server = 发给服务器；badge = 机器人本地翻 badge 页；none = 只提示“没有作用”
   * onLeave：离开这个模式时的收尾效果（设计 §3.7）
   */
  var modes = {
    talk: {
      name: '脱口秀', color: '#E040A0', label: 'TALK', kind: 'normal', isDefault: true,
      owner: '1.脱口秀模式（现在在调试的） 默认模式 forward backward',
      ownerEye: '脱口秀模式--笑面男',
      look: { eye: 'laughing_man', pool: 'none' },
      lookNote: '不论视觉开没开都显示笑面男（主人已定 2026-11-14，推翻 11-10 首页规则）',
      words: { asr: 'forward / backward', dualkey: '—（长按 1 秒回这里）', other: '上滑页、网页、开机默认' },
      touch: {
        tap: { owner: '说一轮', act: 'server', effects: ['say_round'], design: '没在说 → 说一轮；hello 正在说话 → 停（触摸和 DualKey 单击一样，主人 2026-11-14）',
          cases: [{ if: 'speaking', effects: ['stop_speech'], rule: 'talk_tap_stop', design: '正在说时' }] },
        double: { owner: '开启提前', act: 'server', effects: ['prefetch_on'], design: '开提前写' }
      },
      inputs: {
        'asr.forward': { effects: ['say_round'], rule: 'talk_forward' },
        'asr.next': { effects: ['prefetch_on'], rule: 'talk_next' },
        'asr.previous': { effects: ['prefetch_off'], rule: 'talk_previous' }
      },
      paid: ['说一轮（按了才写、才花）', '提前写（默认关，服务重启后关）', '连续说（默认关，只在网页开）'],
      enterDo: ['不开任何付费感官（主人已定：先手动）', '提前写、连续说保持关（离开脱口秀和机器人重启时都会关）'],
      leaveDo: ['停 TTS（正在说的停）', '关提前写、关连续说，存货丢掉'],
      onLeave: ['talk_leave'],
      step1: { status: 'impl', text: '= 今天的行为；触发入口改接 forward、单击、DualKey 单击、next / previous、双击' },
      future: '统一按钮一轮、提前写、连续说、家人声音',
      enterIn: [{ in: 'asr.forward' }, { in: 'pick.talk', toggle: true }, { in: 'web.talk', toggle: true }],
      leaveIn: [{ in: 'asr.backward' }],
      leaveTo: 'idle'
    },
    chat: {
      name: '对话', color: '#F0B000', label: 'CHAT', kind: 'normal',
      owner: '2.对话模式 （小智模式，连续对话，可打断，后端需mcp，可搜索，可以使用情景，情绪，视觉，听觉） play pause',
      ownerEye: '对话模式--猫眼',
      look: { eye: 'v05', pool: 'none' },
      lookNote: '金色猫眼；模式里的表情反应照旧（听到开心的话播一次“开心”）',
      words: { asr: 'play / pause', dualkey: '—', other: '上滑页、网页' },
      touch: {
        tap: { owner: '停', act: 'server', cases: [{ if: 'speaking', effects: ['stop_speech'], rule: 'shell_tap_stop', design: '正在说时' }], step1Code: 'not_implemented', design: '正在说 → 停；否则第一步 not_implemented（这些模式里还不会说话，“停”只防残留）' },
        double: { owner: '展示对话', act: 'server', step1Code: 'not_implemented', design: '屏幕上展示对话' }
      },
      paid: ['（以后）连续对话的模型调用、查询和搜索', '（以后）可以用情景、情绪、视觉、听觉——进入时自动开付费感官暂不做'],
      enterDo: ['第一步：只换眼睛和角标，不花钱'],
      leaveDo: ['停 TTS', '（以后）取消正在生成的回答'],
      step1: { status: 'shell', text: '空壳：能进、能出、有眼睛和角标，没有内容' },
      future: '连续对话、“hello”打断、查询和搜索、改设置先确认（第一版 5.2）',
      enterIn: [{ in: 'asr.play' }, { in: 'pick.chat', toggle: true }, { in: 'web.chat', toggle: true }],
      leaveIn: [{ in: 'asr.pause' }],
      leaveTo: 'idle'
    },
    settings: {
      name: '设置', color: '#D8D8CC', label: 'SET', kind: 'temporary',
      owner: '3.设置模式 （根据语义调整参数，录入人脸，改变情绪等）open close',
      ownerEye: '设置模式--参数',
      look: { eye: 'page_settings', pool: 'none' },
      lookNote: '参数页：当前音量、麦克风增益、俯仰、计费、模式（第一步只读）',
      words: { asr: 'open / close', dualkey: '—', other: '上滑页、网页；5 分钟没动作自动关' },
      touch: {
        tap: { owner: '（补充的触摸表里没有）', act: 'none', design: '无【我定】：屏幕本地提示 NO TAP；DualKey 单击 = no_action' },
        double: { owner: '（补充的触摸表里没有）', act: 'none', design: '无【我定】' }
      },
      paid: ['（以后）按语义改参数会调模型'],
      enterDo: ['记下进入前的模式（return_to）'],
      leaveDo: ['close 或 5 分钟无动作 → 回进入前的模式', '在设置里选别的模式 → 直接去那个模式，return_to 丢掉', '等确认的事作废'],
      step1: { status: 'shell', text: '空壳：参数页只读显示' },
      future: '按语义改参数、录入人脸、改情绪（第一版 5.3）',
      enterIn: [{ in: 'asr.open' }, { in: 'pick.settings', toggle: true }, { in: 'web.settings', toggle: true }],
      leaveIn: [{ in: 'asr.close' }, { in: 'sys.timeout' }],
      leaveTo: '@return'
    },
    event: {
      name: '事件驱动', color: '#20B8C8', label: 'AUTO', kind: 'host',
      owner: '4.事件驱动模式（根据视觉和听觉自动执行） start stop',
      ownerEye: '事件驱动模式--自己决定',
      look: { eye: 'page_idle', pool: 'none' },
      lookNote: '没有自己的眼睛（主人 2026-11-14）：托管客人模式时用那个模式的眼睛，角标旁加小 A；还没进入任何模式（等待）时显示 IDLE 的屏幕，角标 AUTO【我定】',
      words: { asr: 'start / stop', dualkey: '—', other: '上滑页、网页' },
      touch: {
        tap: { owner: '取决于进入了什么模式', act: 'none', design: '跟着它进入的客人模式；等待时无（屏幕本地提示 NO TAP；DualKey 单击 = no_action）' },
        double: { owner: '（空）', act: 'none', design: '同单击' }
      },
      inputs: {
        'sim.guest': { guest: 'crowd', rule: 'guest_enter' }
      },
      paid: ['（以后）进入客人模式后，那个模式自己的花钱行为'],
      enterDo: ['进入“事件驱动·等待”：IDLE 屏幕、角标 AUTO'],
      leaveDo: ['stop → IDLE（托管着客人模式时也是：客人一起停）', '主人手动选了别的模式（含 open 进设置）= 事件驱动停 + 进入那个模式（主人 2026-11-14）'],
      step1: { status: 'shell', text: '空壳：不会自己进入任何模式，只有“等待”' },
      future: '按事件进入客人模式、从轻到重的动作、防烦；最后做（第一版 5.4）',
      enterIn: [{ in: 'asr.start' }, { in: 'pick.event', toggle: true }, { in: 'web.event', toggle: true }],
      leaveIn: [{ in: 'asr.stop' }],
      leaveTo: 'idle'
    },
    subtitle: {
      name: '字幕', color: '#7C6CF0', label: 'SUB', kind: 'normal',
      owner: '5.字幕模式 （输入中文，屏幕显示英文字幕）joy stick up 中音英字， joy stick down, 英音中字。',
      ownerEye: '字幕模式-- 字幕',
      look: { eye: 'page_subtitle', pool: 'none' },
      lookNote: '字幕独占屏幕；两个方向是同一个模式；角标带方向（SUB ZH>EN / SUB EN>ZH）',
      dirs: {
        zh_en: { name: '中音英字', short: '中→英', label: 'SUB ZH>EN' },
        en_zh: { name: '英音中字', short: '英→中', label: 'SUB EN>ZH' }
      },
      words: { asr: '—（没有口令）', dualkey: '摇杆上（中→英）/ 下（英→中）', other: '上滑页两格、网页两个按钮' },
      touch: {
        tap: { owner: '无', act: 'none', design: '无：屏幕本地提示 NO TAP；DualKey 单击 = no_action' },
        double: { owner: '无', act: 'none', design: '无' }
      },
      paid: ['（以后）本地识别 + 翻译调用'],
      enterDo: ['显示字幕页和方向'],
      leaveDo: ['停 TTS', '（以后）清屏、取消翻译调用'],
      step1: { status: 'shell', text: '空壳：字幕页只显示方向' },
      future: '本地识别 + 翻译、只翻最清楚的声音（第一版 5.5）',
      enterIn: [
        { in: 'dk.up', dir: 'zh_en', toggle: true }, { in: 'dk.down', dir: 'en_zh', toggle: true },
        { in: 'pick.subtitle_zh_en', dir: 'zh_en', toggle: true }, { in: 'pick.subtitle_en_zh', dir: 'en_zh', toggle: true },
        { in: 'web.subtitle_zh_en', dir: 'zh_en', toggle: true }, { in: 'web.subtitle_en_zh', dir: 'en_zh', toggle: true }
      ],
      leaveIn: [],
      leaveTo: 'idle'
    },
    crowd: {
      name: '围观', color: '#30D060', label: 'MEET', kind: 'normal',
      owner: '6.围观模式，适合一群人第一次见到hello joy stick left',
      ownerEye: '围观模式--机甲眼',
      look: { eye: 'v14', pool: 'lively' },
      lookNote: '绿色机甲眼 + 热闹表情池（迎接、开心、好奇、搞怪、惊讶）',
      words: { asr: '—（没有口令）', dualkey: '摇杆左', other: '上滑页、网页' },
      touch: {
        tap: { owner: '停', act: 'server', cases: [{ if: 'speaking', effects: ['stop_speech'], rule: 'shell_tap_stop', design: '正在说时' }], step1Code: 'not_implemented', design: '正在说 → 停；否则第一步 not_implemented（这些模式里还不会说话，“停”只防残留）' },
        double: { owner: '抗议', act: 'server', step1Code: 'not_implemented', design: '抗议（表演）' }
      },
      paid: ['（以后）节目内容的模型调用和 TTS'],
      enterDo: ['第一步：只换眼睛和角标'],
      leaveDo: ['停 TTS'],
      step1: { status: 'shell', text: '空壳' },
      future: '节目、不谈不在场的人（第一版 5.6）',
      enterIn: [{ in: 'dk.left', toggle: true }, { in: 'pick.crowd', toggle: true }, { in: 'web.crowd', toggle: true }],
      leaveIn: [],
      leaveTo: 'idle'
    },
    companion: {
      name: '陪伴', color: '#F2E6C8', label: 'PAL', kind: 'normal',
      owner: '7.陪伴模式，全天配置一位熟人，作为情绪助手和聊友 joy stick right',
      ownerEye: '陪伴模式--奶油白眼睛',
      look: { eye: 'v18', pool: 'none' },
      lookNote: '奶油白眼睛',
      words: { asr: '—（没有口令）', dualkey: '摇杆右', other: '上滑页、网页' },
      touch: {
        tap: { owner: '停/唤醒', act: 'server', cases: [{ if: 'speaking', effects: ['stop_speech'], rule: 'shell_tap_stop', design: '正在说时' }], step1Code: 'not_implemented', design: '正在说 → 停；唤醒以后做' },
        double: { owner: '无', act: 'none', design: '无' }
      },
      paid: ['（以后）陪聊的模型调用和 TTS'],
      enterDo: ['第一步：只换眼睛和角标'],
      leaveDo: ['停 TTS'],
      step1: { status: 'shell', text: '空壳' },
      future: '一位熟人（人物甲）、近期说过话就算对 hello 说（第一版 5.7）',
      enterIn: [{ in: 'dk.right', toggle: true }, { in: 'pick.companion', toggle: true }, { in: 'web.companion', toggle: true }],
      leaveIn: [],
      leaveTo: 'idle'
    },
    idle: {
      name: 'IDLE', color: '#505050', label: 'IDLE', kind: 'idle',
      owner: '8.IDLE 模式关闭后，且事件驱动模式没有开启。开放音乐或者视频（？放得动） 或展示badge',
      ownerEye: '（补充里没有写）',
      look: { eye: 'page_idle', pool: 'none' },
      lookNote: '黑底小字 hello；单击切 badge 页（大字 hello + 固件版本）',
      words: { asr: '—（离开别的模式就到这里；说 four = 预设 4）', dualkey: '—', other: '上滑页、网页、预设 4' },
      touch: {
        tap: { owner: 'badge', act: 'badge', design: '屏幕单击：本地翻 badge 页，不经服务器；DualKey 单击 = no_action（不翻）' },
        double: { owner: '音乐模式', act: 'server', step1Code: 'not_implemented', deferred: true, design: '放音乐（以后）' }
      },
      paid: ['无。只停模式自己的花钱；听悟、识别、情景、纪要不动（主人已定：先手动）'],
      enterDo: ['停 TTS；从脱口秀来时关提前写、连续说', '付费感官不动（主人已定）'],
      leaveDo: ['—'],
      step1: { status: 'impl', text: '有自己的屏幕和 badge 页；音乐、视频推迟' },
      future: 'badge；音乐（/mouth 的 Opus 下行能放）；视频放不放得动没验证',
      enterIn: [{ in: 'pick.idle', toggle: true }, { in: 'web.idle', toggle: true }],
      leaveIn: [],
      leaveTo: 'idle'
    }
  };

  var modeOrder = ['talk', 'chat', 'settings', 'event', 'subtitle', 'crowd', 'companion', 'idle'];

  /* ---------- 效果：每个效果先查 guards（任何一条不过 = 整个动作不做，返回那条的 code），再执行 ops ---------- */
  var effects = {
    stop_speech: { short: '停说话', label: '停 TTS（正在说的停）', ops: [['set', 'speaking', false]] },
    talk_leave: { short: '关提前写/连续说', label: '关提前写、关连续说，存货丢掉', ops: [['set', 'talk.prefetch', false], ['set', 'talk.auto', false]] },
    say_round: { short: '说一轮',
      label: '说一轮（写一轮并开口，花一次钱）',
      guards: [
        { need: 'billing', code: 'billing_off' },
        { need: 'mouthTts', code: 'mouth_not_tts' }
      ],
      ops: [['set', 'speaking', true], ['inc', 'rounds']],
      note: '实际还有：同时最多一轮（busy）、两轮至少隔 10 秒（too_soon），模拟器不计时'
    },
    prefetch_on: { short: '开提前写',
      label: '开提前写',
      guards: [{ need: 'billing', code: 'billing_off' }, { need: 'off', path: 'talk.prefetch', code: 'already' }],
      ops: [['set', 'talk.prefetch', true]]
    },
    prefetch_off: { short: '关提前写',
      label: '关提前写',
      guards: [{ need: 'on', path: 'talk.prefetch', code: 'already' }],
      ops: [['set', 'talk.prefetch', false]]
    },
    toggle_prefetch: { short: '提前写反转',
      label: '提前写反转',
      guards: [{ need: 'mode', mode: 'talk', code: 'only_talk' }, { need: 'billingToTurnOn', path: 'talk.prefetch', code: 'billing_off' }],
      ops: [['toggle', 'talk.prefetch']]
    },
    toggle_auto: { short: '连续说反转',
      label: '连续说反转',
      guards: [{ need: 'mode', mode: 'talk', code: 'only_talk' }, { need: 'billingToTurnOn', path: 'talk.auto', code: 'billing_off' }],
      ops: [['toggle', 'talk.auto']]
    },
    toggle_tingwu: { short: '听悟反转',
      label: '听悟开关反转',
      guards: [{ need: 'billingToTurnOn', path: 'senses.tingwu', code: 'billing_off' }],
      ops: [['toggle', 'senses.tingwu']]
    },
    billing_off_all: { short: '花费全关',
      label: '计费总闸关；听悟、识别、情景、纪要、提前写、连续说全关；正在说的停（第一步 = 关掉 + 关着时不许再开）',
      ops: [['set', 'billing', false], ['set', 'senses.tingwu', false], ['set', 'senses.eye', false], ['set', 'senses.scene', false],
        ['set', 'senses.notes', false], ['set', 'talk.prefetch', false], ['set', 'talk.auto', false], ['set', 'speaking', false]]
    },
    billing_on: { short: '总闸开', label: '计费总闸开（不恢复任何东西，只是允许再打开）', ops: [['set', 'billing', true]] },
    paid_senses_on: { short: '付费感官开',
      label: '听悟、识别、情景、纪要开',
      ops: [['set', 'senses.tingwu', true], ['set', 'senses.eye', true], ['set', 'senses.scene', true], ['set', 'senses.notes', true]]
    },
    mouth_tts: { short: '嘴切 TTS', label: '嘴切到 TTS 档', ops: [['set', 'env.mouthTts', true]] },
    talk_paid_on: { short: '提前写+连续说开',
      label: '提前写、连续说开（只在脱口秀里；不在脱口秀时不切模式、跳过这两项、屏幕提示一句 —— 主人 2026-11-14 按默认定了）',
      onlyIn: 'talk', skipToast: 'TALK-ONLY SKIPPED',
      ops: [['set', 'talk.prefetch', true], ['set', 'talk.auto', true]]
    },
  };

  /* ---------- 机器人那一半：第一步只由网页执行（和开关条“本地”听觉档、“上传”视觉档同一组请求）；
   *            机器人发的预设 1 / 9 只做服务器那一半，提示 SENSES: USE PAGE（设计 §7） ---------- */
  var controls = {
    hearing: { label: '本体听觉开（本地档；每句话带 SenseVoice 情绪标签 = 情绪感知）', body: '网页 → /api/control hearing_listen', ops: [['set', 'robot.hearing', true]] },
    vision: { label: '视觉“上传”档（相机 + M12 检测）', body: '网页 → /api/control camera + interaction', ops: [['set', 'robot.camera', true], ['set', 'robot.m12', true]] }
  };

  /* ---------- 0–9 预设（设计 §7；1 和 9 按主人 2026-11-14 回答“字面意思”） ---------- */
  var presets = [
    { n: 0, name: '花费全关', owner: '0 -- 花费全关', server: ['billing_off_all'], robot: [], note: '本地听觉和视觉上传不花钱，不动' },
    { n: 1, name: '数据收集', owner: '1 -- 数据收集模式（不花钱，视觉听觉，情绪感知，连接到服务器）',
      server: ['billing_off_all'], robot: ['hearing', 'vision'],
      note: '主人已定：字面意思。同 0（计费总闸也关）+ 本地听觉 + 视觉上传；“情绪感知”没有单独开关 = 本地听觉每句话的情绪标签，听觉开着就有', decided: true },
    { n: 2, name: '听悟反转', owner: '2 -- 听悟开关反转', server: ['toggle_tingwu'], robot: [], note: '总闸关着时不能开，提示“计费关着”' },
    { n: 3, name: '提前写反转', owner: '3 -- 提前写开关反转', server: ['toggle_prefetch'], robot: [], note: '只在脱口秀里有效' },
    { n: 4, name: 'IDLE', owner: '4 -- IDLE模式', server: [], robot: [], mode: 'idle', note: '唯一会改模式的预设' },
    { n: 5, name: '空', owner: '5 -- ', undefined: true, server: [], robot: [], note: '空着：听觉先手动（主人 2026-11-14），以后可以放听觉口令' },
    { n: 6, name: '空', owner: '6 --', undefined: true, server: [], robot: [], note: '只记日志，提示“未定义”' },
    { n: 7, name: '空', owner: '7 --', undefined: true, server: [], robot: [], note: '只记日志，提示“未定义”' },
    { n: 8, name: '空', owner: '8 --', undefined: true, server: [], robot: [], note: '只记日志，提示“未定义”' },
    { n: 9, name: '花费全开', owner: '9 -- 花费全开', server: ['billing_on', 'paid_senses_on', 'mouth_tts', 'talk_paid_on'], robot: ['hearing', 'vision'],
      note: '主人已定：字面意思，一切付费的都开，加嘴切 TTS；不在脱口秀时不切模式、提前写和连续说跳过并提示一句（主人 2026-11-14 按默认定了）', decided: true }
  ];

  /* ---------- 规则文字（日志和触发表里显示的“为什么”） ---------- */
  var rules = {
    offline: { text: '机器人和服务器断开（/mouth 没连上）时，模式口令、上滑页、要发给服务器的触摸、计费和预设都不执行，屏幕提示 OFFLINE；本地的东西照常（转头词、追踪、音量、badge 页）', ref: '设计 §3.6' },
    any_boot: { text: '机器人开机（新的 boot_id）：mind 把模式设成脱口秀，记一条 by: boot，不论开机前是什么模式；即使本来就在脱口秀，也停说话、关提前写和连续说', ref: '设计 §3.6、§3.8；计划 A1' },
    boot_paid: { text: '开机不动付费感官（听悟、识别、情景、纪要），先手动', ref: '主人 2026-11-14（设计 §1.2 第 3 条）' },
    mind_restart: { text: 'mind 重启：从数据库读回当前模式和 seq，不改；提前写、连续说照今天的规则回到关', ref: '设计 §3.6' },
    any_long: { text: 'DualKey 长按 1 秒：停下当前的事，回脱口秀（任何模式，含已在脱口秀）', ref: '设计 §3.5、§5.2【我定】' },
    mode_input: { text: '这个模式里这个输入有自己的意思', ref: '设计 §3.5、§5.1' },
    talk_forward: { text: '已在脱口秀时 forward = 说一轮（主人 2026-11-14 按默认定了）', ref: '设计 §5.1、§12' },
    talk_next: { text: '脱口秀里 next = 开提前写', ref: '设计 §5.1' },
    talk_previous: { text: '脱口秀里 previous = 关提前写', ref: '设计 §5.1' },
    talk_tap_stop: { text: '脱口秀里 hello 正在说话时单击（屏幕或 DualKey）= 停；没在说 = 说一轮。ASR 和 DualKey 互不相关', ref: '设计 §5.4、§5.2；主人 2026-11-14 第 9 条' },
    shell_tap_stop: { text: '对话、围观、陪伴（空壳）里单击：hello 正在说话 → 停（只防残留）；否则 not_implemented', ref: '设计 §3.8' },
    guest_enter: { text: '（以后）事件驱动按看到、听到的事自动进入一个客人模式，用那个模式的眼睛，角标旁加小 A。第一步不会发生', ref: '设计 §3.4；主人 2026-11-14 第 2、11 条' },
    guest_none: { text: '只有“事件驱动·等待”时才会自动进入客人模式；现在不在等待', ref: '设计 §3.4' },
    host_leave: { text: '事件驱动托管着客人模式时说 stop：事件驱动和客人模式一起停，→ IDLE', ref: '设计 §3.8' },
    host_already: { text: '事件驱动已经开着（正托管客人模式），再进入事件驱动不变', ref: '设计 §3.5' },
    leave: { text: '在模式 X 里说 X 的离开词：→ IDLE', ref: '设计 §3.5；主人 2026-11-14 第 1 条' },
    toggle_leave: { text: '已在这个模式，再按同一个入口（DualKey 再推同一方向、上滑页或网页再点当前模式）= 离开 → IDLE', ref: '设计 §3.5、§3.8、§6' },
    leave_hosted: { text: '客人模式离开时回“事件驱动·等待”，不是 IDLE', ref: '设计 §3.5' },
    leave_return: { text: '设置是临时层：close 或 5 分钟无动作，回到进入设置前的模式', ref: '设计 §3.4' },
    switch_dir: { text: '在字幕里推另一个上下方向 = 换方向，不经过 IDLE', ref: '设计 §3.5【我定】' },
    already: { text: '已在 X 时说 X 的进入词：不变', ref: '设计 §3.5' },
    enter: { text: '某模式的进入（口令前一个词、DualKey 方向、上滑页、网页、预设 4）：直接去那个模式，不经过 IDLE，先按 §3.7 收尾', ref: '设计 §3.5；主人 2026-11-14 第 1 条' },
    enter_temp: { text: '进入设置（临时层）时记下当时显示的模式，关掉设置时回去', ref: '设计 §3.4、§3.8' },
    enter_from_temp: { text: '离开设置（不论哪条路）：记下的“原来的模式”一律清掉', ref: '设计 §3.8' },
    enter_from_host: { text: '事件驱动开着时主人手动选了模式（含 open 进设置）：事件驱动停，直接进那个模式；进设置时记下的是当时显示的模式（等待中就是 IDLE），不再托管', ref: '设计 §3.4、§3.8；主人 2026-11-14 第 12 条' },
    not_in_mode: { text: '不在 X 时说 X 的离开词：不变，只记日志，屏幕提示“不在 X”', ref: '设计 §3.5' },
    touch_server: { text: '首页单击 / 双击按当前模式的 touch（发给服务器）', ref: '设计 §5.4；补充触摸表' },
    touch_shell: { text: '第一步这个模式是空壳：返回 not_implemented（补充里的“停 / 抗议 / 展示对话 / 音乐”以后做）', ref: '计划 Task A1' },
    touch_none: { text: '这个模式单击 / 双击没有动作：机器人本地提示 NO TAP，不发服务器', ref: '设计 §3.8、§5.4（touch = none）' },
    touch_none_dk: { text: 'DualKey 单击 / 双击 = 当前模式首页单击 / 双击；这个模式没有动作：服务器结果 no_action，什么都不做', ref: '设计 §3.8；计划 A1' },
    touch_badge: { text: 'IDLE 里屏幕单击：机器人本地翻 badge 页，不经服务器', ref: '设计 §5.4' },
    touch_badge_dk: { text: 'IDLE 里 DualKey 单击：no_action（不翻 badge；badge 是机器人本地的一页，第一步没有“翻页”消息）', ref: '设计 §3.8' },
    only_talk: { text: 'next / previous 只在脱口秀里有意思（开 / 关提前写）；别的模式里无效，屏幕提示 ONLY IN TALK', ref: '设计 §5.1' },
    preset: { text: '数字词 = 0–9 预设：一次改一组开关；预设不改当前模式（4 除外）', ref: '设计 §7' },
    preset_undefined: { text: '5–8 没有定义：只记日志，提示“未定义”（5 空着：听觉先手动）', ref: '设计 §7；主人 2026-11-14 第 10 条' },
    preset_robot: { text: '网页按的预设：网页再用开关条同一组请求执行机器人那一半', ref: '设计 §7' },
    preset_robot_robot: { text: '机器人发的预设 1 / 9：服务器那一半照做，机器人那一半第一步做不了（下行控制在第二步），屏幕提示 SENSES: USE PAGE', ref: '设计 §7；计划 A3' },
    preset_robot_offline: { text: '网页按预设时机器人不在线：服务器那一半照做；页面照实写机器人那一半没成，不排队、不重试', ref: '设计 §3.8' },
    billing_off: { text: '计费总闸关 = 预设 0 的服务器那一半：付费开关全关、关着时不许再开、说话类接口拒绝；seq 加 1 推给机器人和 DualKey（第一步没有进程级模型闸门，第二步加）', ref: '设计 §3.3、§4.1' },
    billing_on: { text: '计费总闸开：不恢复任何东西，只是允许再打开；seq 加 1；“全开”用预设 9', ref: '设计 §3.3' },
    switch_toggle: { text: '页面开关条（今天就有）：感官开关独立于模式', ref: '设计 §3.3' },
    switch_blocked: { text: '计费总闸关着时，付费开关打不开（计费关着）', ref: '设计 §3.3；计划 A4' },
    say_only_talk: { text: '台词面板：说一轮、提前写、连续说只在脱口秀模式（409 只在脱口秀模式）', ref: '计划 Task A4、B2' },
    say_panel: { text: '台词面板（今天就有）', ref: '设计 §2.3' },
    say_stop: { text: '“别说了” /say/stop 永远放行', ref: '计划 Task A4' },
    local_picker: { text: '首页上滑：打开模式选择页（3×3 九格，当前模式高亮；点一格才发给服务器，再点当前格 = 离开）', ref: '设计 §6' },
    local_quick: { text: '首页下滑：打开快捷设置页（音量、计费、0–9 预设）', ref: '设计 §6；补充“下滑 快捷设置页 音量 计费开关”' },
    local_summary: { text: '首页左滑：打开状态摘要页（今天的摘要页 + 模式、计费两行）；单击不再打开摘要', ref: '设计 §5.4；主人 2026-11-14 第 6 条' },
    local_right: { text: '首页右滑没有定义（分类器认得 SwipeRight，首页不用）', ref: '计划 Task C3' },
    local_volume: { text: '音量 − / ＋：机器人本地直接改，存 NVS，和音量口令同一个设置；离线也能用', ref: '设计 §6' },
    local_asr: { text: 'up / down、left / right、turn left / right、front / back、turn on / off、音量五词：照旧，所有模式都有效，不经过模式', ref: '设计 §5.1' },
    asr_ok: { text: 'ok 预留给设置模式的确认；第一步没有作用', ref: '设计 §5.1' },
    asr_hello: { text: '唤醒词 hello 第一步不变；“hello 出声时说 hello = 立即停”在对话那一步再做', ref: '设计 §5.1' },
    rating: { text: 'DualKey KEY_1 / KEY_2 = 好 / 坏打分，所有模式都有效，不改模式', ref: '设计 §5.2' },
    remote: { text: '官方遥控器第一步不变：四向是开关（左追踪、右认人、上听觉、下预留），中心开关屏；上推仍开关听觉', ref: '设计 §5.3' },
    timeout_none: { text: '设置超时只对设置模式有意义；现在不在设置', ref: '设计 §3.4' },
    unknown: { text: '这个输入在当前模式里没有定义', ref: '—' }
  };

  /* ---------- 结果码（计划“接口”一节；offline / local / no_effect 是机器人本地或本页的说法） ---------- */
  var codes = {
    ok: { label: '成功', toast: 'OK' },
    already: { label: '不变（已经是这样）', toast: 'ALREADY' },
    not_in_mode: { label: '不在那个模式，不变', toast: 'NOT IN {mode}' },
    only_talk: { label: '只在脱口秀', toast: 'ONLY IN TALK' },
    billing_off: { label: '计费关着', toast: 'BILLING OFF' },
    mouth_not_tts: { label: '嘴不在 TTS 档', toast: 'MOUTH NOT TTS' },
    not_implemented: { label: '第一步空壳', toast: 'NOT YET' },
    no_action: { label: '这个模式里这个输入本来就没有动作', toast: 'NO ACTION' },
    senses_on_page: { label: '服务器那一半做了，机器人那一半要在网页上做', toast: 'SENSES: USE PAGE' },
    busy: { label: '已经在说一轮（模拟器不计时）', toast: 'BUSY' },
    too_soon: { label: '两轮开始不到 10 秒（模拟器不计时）', toast: 'TOO SOON' },
    stale: { label: '设备输入超过 3 秒才到，丢掉（模拟器不计时）', toast: '' },
    undefined_preset: { label: '未定义的预设', toast: 'PRESET UNDEFINED' },
    offline: { label: '机器人离线，没执行', toast: 'OFFLINE' },
    local: { label: '机器人本地处理', toast: '' },
    no_effect: { label: '没有作用（不经模式）', toast: '' },
    unknown_input: { label: '没有定义', toast: 'NO EFFECT' }
  };

  /* ---------- 输入（模拟器按钮、触发表的行） ----------
   * via：robot（机器人上的 ASR、触摸、上滑页、快捷设置页）/ dualkey / page（网页）/ mind（系统）
   * needsServer：机器人上的输入要不要经服务器（离线时不执行）
   * handler.type：mode（走模式规则）/ local / noop / preset / billing / switch / entry / system
   */
  function digitInputs(prefix, via, groupLabel) {
    var words = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine'];
    return presets.map(function (p) {
      return {
        id: prefix + p.n, label: prefix.indexOf('asr') === 0 ? words[p.n] : String(p.n),
        short: (prefix.indexOf('asr') === 0 ? words[p.n] : groupLabel + p.n), via: via, needsServer: via === 'robot',
        handler: { type: 'preset', n: p.n }
      };
    });
  }

  var inputGroups = [
    {
      id: 'asr', name: 'ASR 口令（先说 Hi M Five / hello / hi A S R 唤醒）', items: [
        { id: 'asr.forward', label: 'forward', via: 'robot', needsServer: true, handler: { type: 'mode' } },
        { id: 'asr.backward', label: 'backward', via: 'robot', needsServer: true, handler: { type: 'mode' } },
        { id: 'asr.play', label: 'play', via: 'robot', needsServer: true, handler: { type: 'mode' } },
        { id: 'asr.pause', label: 'pause', via: 'robot', needsServer: true, handler: { type: 'mode' } },
        { id: 'asr.open', label: 'open', via: 'robot', needsServer: true, handler: { type: 'mode' }, was: '今天：开水平追踪（不再）' },
        { id: 'asr.close', label: 'close', via: 'robot', needsServer: true, handler: { type: 'mode' }, was: '今天：关水平追踪（不再）' },
        { id: 'asr.start', label: 'start', via: 'robot', needsServer: true, handler: { type: 'mode' }, was: '今天：开听觉（不再；第一步听觉没有口令）' },
        { id: 'asr.stop', label: 'stop', via: 'robot', needsServer: true, handler: { type: 'mode' }, was: '今天：关听觉（不再）' },
        { id: 'asr.next', label: 'next', via: 'robot', needsServer: true, handler: { type: 'mode' }, fallback: { code: 'only_talk', rule: 'only_talk' }, was: '今天：随机播 SD 台词（不再）' },
        { id: 'asr.previous', label: 'previous', via: 'robot', needsServer: true, handler: { type: 'mode' }, fallback: { code: 'only_talk', rule: 'only_talk' } },
        { id: 'asr.ok', label: 'ok', via: 'robot', needsServer: false, handler: { type: 'noop', rule: 'asr_ok' } },
        { id: 'asr.hello', label: 'hello（唤醒词）', via: 'robot', needsServer: false, deferred: '“说 hello = 立即停”以后（对话那一步）', handler: { type: 'noop', rule: 'asr_hello' } },
        { id: 'asr.local', label: 'up/down、转头、turn on/off、音量', short: '转头/追踪/音量词', via: 'robot', needsServer: false, handler: { type: 'noop', rule: 'local_asr' } }
      ]
    },
    { id: 'digits', name: 'ASR 数字词 = 0–9 预设', items: digitInputs('asr.d', 'robot', '') },
    {
      id: 'touch', name: '机器人屏幕触摸（只在首页）', items: [
        { id: 'touch.tap', label: '单击', via: 'robot', needsServer: false, touch: 'tap', handler: { type: 'mode' }, note: '要等 0.3 秒确认不是双击' },
        { id: 'touch.double', label: '双击', via: 'robot', needsServer: false, touch: 'double', handler: { type: 'mode' } },
        { id: 'touch.up', label: '上滑', via: 'robot', needsServer: false, handler: { type: 'local', open: 'picker', rule: 'local_picker' } },
        { id: 'touch.down', label: '下滑', via: 'robot', needsServer: false, handler: { type: 'local', open: 'quick', rule: 'local_quick' } },
        { id: 'touch.left', label: '左滑', via: 'robot', needsServer: false, handler: { type: 'local', open: 'summary', rule: 'local_summary' } },
        { id: 'touch.right', label: '右滑', via: 'robot', needsServer: false, handler: { type: 'noop', rule: 'local_right' } }
      ]
    },
    {
      id: 'pick', name: '上滑模式选择页（九格，点一格发给服务器）', items: [
        { id: 'pick.talk', label: '脱口秀', via: 'robot', needsServer: true, handler: { type: 'mode' } },
        { id: 'pick.chat', label: '对话', via: 'robot', needsServer: true, handler: { type: 'mode' } },
        { id: 'pick.settings', label: '设置', via: 'robot', needsServer: true, handler: { type: 'mode' } },
        { id: 'pick.event', label: '事件驱动', via: 'robot', needsServer: true, handler: { type: 'mode' } },
        { id: 'pick.subtitle_zh_en', label: '字幕 中→英', via: 'robot', needsServer: true, handler: { type: 'mode' } },
        { id: 'pick.subtitle_en_zh', label: '字幕 英→中', via: 'robot', needsServer: true, handler: { type: 'mode' } },
        { id: 'pick.crowd', label: '围观', via: 'robot', needsServer: true, handler: { type: 'mode' } },
        { id: 'pick.companion', label: '陪伴', via: 'robot', needsServer: true, handler: { type: 'mode' } },
        { id: 'pick.idle', label: 'IDLE', via: 'robot', needsServer: true, handler: { type: 'mode' } }
      ]
    },
    {
      id: 'quick', name: '下滑快捷设置页', items: [
        { id: 'quick.vol_down', label: '音量 −', via: 'robot', needsServer: false, handler: { type: 'local', vol: -1, rule: 'local_volume' } },
        { id: 'quick.vol_up', label: '音量 ＋', via: 'robot', needsServer: false, handler: { type: 'local', vol: 1, rule: 'local_volume' } },
        { id: 'quick.billing_on', label: '计费 开', via: 'robot', needsServer: true, handler: { type: 'billing', on: true } },
        { id: 'quick.billing_off', label: '计费 关', via: 'robot', needsServer: true, handler: { type: 'billing', on: false } }
      ].concat(digitInputs('quick.p', 'robot', '键 '))
    },
    {
      id: 'dk', name: 'DualKey（经 ear 到 mind，不经机器人）', items: [
        { id: 'dk.up', label: '摇杆 上', short: 'DualKey 上', via: 'dualkey', handler: { type: 'mode' } },
        { id: 'dk.down', label: '摇杆 下', short: 'DualKey 下', via: 'dualkey', handler: { type: 'mode' } },
        { id: 'dk.left', label: '摇杆 左', short: 'DualKey 左', via: 'dualkey', handler: { type: 'mode' } },
        { id: 'dk.right', label: '摇杆 右', short: 'DualKey 右', via: 'dualkey', handler: { type: 'mode' } },
        { id: 'dk.click', label: '摇杆 单击', via: 'dualkey', touch: 'tap', handler: { type: 'mode' }, note: '晚约 0.2 秒' },
        { id: 'dk.double', label: '摇杆 双击', via: 'dualkey', touch: 'double', handler: { type: 'mode' } },
        { id: 'dk.long', label: '摇杆 长按 1 秒', via: 'dualkey', handler: { type: 'mode' } },
        { id: 'dk.key1', label: 'KEY_1 好', via: 'dualkey', handler: { type: 'noop', rule: 'rating' } },
        { id: 'dk.key2', label: 'KEY_2 坏', via: 'dualkey', handler: { type: 'noop', rule: 'rating' } }
      ]
    },
    {
      id: 'web', name: '网页模式条（当前模式高亮，再点 = 离开）', items: [
        { id: 'web.talk', label: '脱口秀', via: 'page', handler: { type: 'mode' } },
        { id: 'web.chat', label: '对话', via: 'page', handler: { type: 'mode' } },
        { id: 'web.settings', label: '设置', via: 'page', handler: { type: 'mode' } },
        { id: 'web.event', label: '事件驱动', via: 'page', handler: { type: 'mode' } },
        { id: 'web.subtitle_zh_en', label: '字幕 中→英', via: 'page', handler: { type: 'mode' } },
        { id: 'web.subtitle_en_zh', label: '字幕 英→中', via: 'page', handler: { type: 'mode' } },
        { id: 'web.crowd', label: '围观', via: 'page', handler: { type: 'mode' } },
        { id: 'web.companion', label: '陪伴', via: 'page', handler: { type: 'mode' } },
        { id: 'web.idle', label: 'IDLE', via: 'page', handler: { type: 'mode' } }
      ]
    },
    {
      id: 'webquick', name: '网页快捷设置面板', items: [
        { id: 'web.billing_on', label: '计费 开', via: 'page', handler: { type: 'billing', on: true } },
        { id: 'web.billing_off', label: '计费 关', via: 'page', handler: { type: 'billing', on: false } }
      ].concat(digitInputs('web.p', 'page', '预设 '))
    },
    {
      id: 'websw', name: '网页开关条和台词面板（今天就有）', items: [
        { id: 'sw.tingwu', label: '听悟 开/关', via: 'page', handler: { type: 'switch', path: 'senses.tingwu' } },
        { id: 'sw.eye', label: '识别 开/关', via: 'page', handler: { type: 'switch', path: 'senses.eye' } },
        { id: 'sw.scene', label: '情景 开/关', via: 'page', handler: { type: 'switch', path: 'senses.scene' } },
        { id: 'sw.notes', label: '纪要 开/关', via: 'page', handler: { type: 'switch', path: 'senses.notes' } },
        { id: 'say.round', label: '说一轮', via: 'page', handler: { type: 'entry', needMode: 'talk', effects: ['say_round'], rule: 'say_panel' } },
        { id: 'say.prefetch', label: '提前写 开/关', via: 'page', handler: { type: 'entry', needMode: 'talk', effects: ['toggle_prefetch'], rule: 'say_panel' } },
        { id: 'say.auto', label: '连续说 开/关', via: 'page', handler: { type: 'entry', needMode: 'talk', effects: ['toggle_auto'], rule: 'say_panel' } },
        { id: 'say.stop', label: '别说了', via: 'page', handler: { type: 'entry', effects: ['stop_speech'], rule: 'say_stop' } }
      ]
    },
    {
      id: 'sys', name: '系统和其他', items: [
        { id: 'sys.boot', label: '机器人开机（新 boot）', short: '开机', via: 'mind', handler: { type: 'mode' } },
        { id: 'sys.mind_restart', label: 'mind 重启', via: 'mind', handler: { type: 'mode' } },
        { id: 'sys.timeout', label: '设置 5 分钟无动作', short: '设置超时', via: 'mind', handler: { type: 'mode' } , fallback: { code: 'no_effect', rule: 'timeout_none' } },
        { id: 'sim.guest', label: '事件：看到一群人 → 围观', short: '事件进客人', via: 'mind', deferred: '第一步事件驱动不会自己进入任何模式；这里只演示以后的托管', handler: { type: 'mode' }, fallback: { code: 'no_effect', rule: 'guest_none' } },
        { id: 'remote.any', label: '官方遥控器 四向 / 中心', short: '官方遥控器', via: 'remote', deferred: '第二层绑定模式以后', handler: { type: 'noop', rule: 'remote' } }
      ]
    }
  ];

  /* 任何模式都一样处理的输入（在模式自己的 inputs 之前查） */
  var anyMode = {
    'sys.boot': { goto: 'talk', force: ['stop_speech', 'talk_leave'], rule: 'any_boot', alsoRule: 'boot_paid', by: 'boot' },
    'dk.long': { goto: 'talk', force: ['stop_speech'], rule: 'any_long' },
    'sys.mind_restart': { ops: [['set', 'talk.prefetch', false], ['set', 'talk.auto', false]], rule: 'mind_restart' }
  };

  /* 模式一变就做的事（设计 §3.7，计划 A1“只要 mode 变了 effects 以 stop_speech 开头”） */
  var onModeChange = ['stop_speech'];

  /* 模拟器初始状态（开机后的样子；付费感官都关 = 11-11 定的重启后默认关） */
  var initialState = {
    mode: 'talk', dir: null, host: null, return_to: null, seq: 1, by: 'boot',
    billing: true,
    senses: { tingwu: false, eye: false, scene: false, notes: false },
    talk: { prefetch: false, auto: false },
    speaking: false, rounds: 0,
    robot: { hearing: false, camera: false, m12: false, volume: 3, badge: false },
    env: { online: true, mouthTts: true }
  };

  /* 花钱面板：每一项看哪个状态、开着要什么条件 */
  var spendItems = [
    { group: '总闸', label: '计费总闸', path: 'billing', paid: false, master: true },
    { group: '付费感官（独立开关）', label: '听悟', path: 'senses.tingwu', paid: true, needs: ['billing'] },
    { group: '付费感官（独立开关）', label: '识别（视觉 eye）', path: 'senses.eye', paid: true, needs: ['billing'] },
    { group: '付费感官（独立开关）', label: '情景', path: 'senses.scene', paid: true, needs: ['billing'] },
    { group: '付费感官（独立开关）', label: '纪要', path: 'senses.notes', paid: true, needs: ['billing'] },
    { group: '模式的花钱行为（只在脱口秀）', label: '说一轮（按了才花）', path: 'speaking', paid: true, needs: ['billing', 'mode:talk', 'mouthTts'], onLabel: '正在说', offLabel: '没在说' },
    { group: '模式的花钱行为（只在脱口秀）', label: '提前写', path: 'talk.prefetch', paid: true, needs: ['billing', 'mode:talk'] },
    { group: '模式的花钱行为（只在脱口秀）', label: '连续说', path: 'talk.auto', paid: true, needs: ['billing', 'mode:talk'] },
    { group: '机器人本地（不花钱）', label: '本体听觉（本地档，带每句情绪标签）', path: 'robot.hearing', paid: false },
    { group: '机器人本地（不花钱）', label: '相机（视觉上传）', path: 'robot.camera', paid: false },
    { group: '机器人本地（不花钱）', label: 'M12 人脸检测', path: 'robot.m12', paid: false }
  ];

  /* ---------- 状态图：节点位置（viewBox 980×640）；进 / 出 IDLE 的边从 enterIn / leaveIn 自动生成 ---------- */
  var diagram = {
    width: 980, height: 660,
    nodes: {
      talk: { x: 490, y: 78 }, chat: { x: 300, y: 190 }, subtitle: { x: 740, y: 190 },
      settings: { x: 120, y: 330 }, idle: { x: 490, y: 330 },
      crowd: { x: 270, y: 460 }, companion: { x: 740, y: 460 },
      event: { x: 420, y: 604 }
    },
    pseudo: {
      any: { x: 120, y: 78, label: '任何模式', sub: '（原来的模式）' },
      boot: { x: 860, y: 78, label: '开机', sub: '新 boot_id' },
      guest: { x: 690, y: 604, label: '客人模式', sub: '（以后）' }
    },
    host: { x: 300, y: 540, w: 500, h: 108, label: '外层：事件驱动开着时' },
    extraEdges: [
      { from: 'boot', to: 'talk', label: '开机默认', kind: 'solid' },
      { from: 'any', to: 'talk', label: 'DualKey 长按 1 秒', kind: 'solid', bend: -30 },
      { from: 'any', to: 'settings', label: 'open\n（临时层）', kind: 'solid', bend: 40 },
      { from: 'settings', to: 'any', label: 'close / 5 分钟\n→ 回原来的模式', kind: 'return', bend: 40 },
      { from: 'event', to: 'guest', label: '按事件进入（以后）', kind: 'deferred', bend: -26 },
      { from: 'guest', to: 'event', label: '客人离开 → 回等待', kind: 'deferred', bend: -26 },
      { self: 'subtitle', label: '上 ↔ 下 换方向' }
    ],
    legend: [
      '每个普通模式之间也能直接切换（口令、DualKey、上滑页、网页），不经过 IDLE —— 图里只画了进出 IDLE 的边',
      '离开词 / 再推同一方向 / 网页再点 → IDLE；预设 4 从任何模式直接进 IDLE',
      '设置是临时层，关掉回到原来的模式；事件驱动是托管层，以后会自己进出客人模式'
    ]
  };

  /* ---------- 快捷设置页 / 摘要页 / 模式选择页的布局（320×240 屏幕） ---------- */
  var screens = {
    picker: {
      title: '上滑：模式选择页',
      cells: ['pick.talk', 'pick.chat', 'pick.settings', 'pick.event', 'pick.subtitle_zh_en', 'pick.subtitle_en_zh', 'pick.crowd', 'pick.companion', 'pick.idle'],
      notes: ['3×3 九格，当前模式高亮；再点当前格 = 离开（→ IDLE），和网页一样', '点一格就发给服务器，格子显示“...”直到新快照到', '离线时整页灰、写 OFFLINE、点了不发', '点空白或下滑关闭', '用时才建、关了就删（省内部 RAM）']
    },
    quick: {
      title: '下滑：快捷设置页',
      notes: ['音量 − / ＋：本地直接改、存 NVS，离线也能用', '计费 开 / 关：发给服务器，显示以快照为准', '0–9 预设键：发给服务器；5–8 灰；按 1 / 9 只做服务器那一半，提示 SENSES: USE PAGE', '上滑或点空白关闭', '补充原文只有“音量 计费开关”，0–9 键是【我定】加的']
    },
    summary: {
      title: '左滑：状态摘要页',
      lines: ['collector-0.4.92', 'WiFi  (SSID)', 'IP    10.x.x.x', 'HEAR  {hearing}', 'CAM   {camera}'],
      notes: ['今天的摘要页（以前首页轻点进入，现在左滑）', '加两行：MODE <label> #<seq>、BILLING ON/OFF', '摘要页里的左滑 / 长按照旧（去日志、配对）']
    }
  };

  /* ---------- 第一步计划的任务（计划 A0–A6、B1–B2、C1–C6、D0–D2，共 18 个） ---------- */
  var tasks = [
    { group: '服务器（mind / ear）', items: [
      { id: 'A0', title: 'ear 自己终结 /mouth（行为不变，单独上线）', files: 'server/ear/mouth_link.py、ear_server.py', note: '风险最大的一块先单独做：上游中途断开发 tts.stop、机器人断开即关上游、上游重连重放 hello', ask: '经 D0 单独上线，要主人同意' },
      { id: 'A1', title: '模式表和纯状态机', files: 'server/mind/mode_table.py、modes.py', note: 'MODES 八行（ASCII label）；transition(…, speaking) 纯函数；离开设置一律清 return_to' },
      { id: 'A2', title: '模式的存储、去重、前置检查、开机默认、设置超时', files: 'server/mind/mode_store.py', note: 'hello.mode、hello.mode_log（永久保留）；device+boot+seq 去重；>3 秒丢弃；计费变化也加 seq' },
      { id: 'A3', title: '计费总闸和预设（服务器那一半）', files: 'server/mind/billing.py、mode_table.py', note: '总闸 = 关掉付费开关 + 关着时不许再开；PRESETS；机器人发的 1 / 9 → senses_on_page' },
      { id: 'A4', title: 'mind 接口、前置检查和钩子、按模式拦说话、DualKey 接入', files: 'server/mind/api.py 等', note: '/mode、/mode/input、/mode/robot、/preset；不在脱口秀 409；DualKey 传 age_ms' },
      { id: 'A5', title: 'ear 在 /mouth 上推快照、转模式输入（D0 通过之后）', files: 'server/ear/mouth_link.py、ear_server.py', note: '每秒问 mind 的 seq，变了就推' },
      { id: 'A6', title: 'ear 给 DualKey 推摇杆灯', files: 'server/ear/signal_link.py', note: 'idle 关灯' }
    ] },
    { group: '页面（观察台）', items: [
      { id: 'B1', title: '模式条和快捷设置面板', files: 'web/monitor/public/mode-ui.js、mode.css', note: '九个按钮（再点 = 离开）、同步状态、最近 10 次、计费总闸、0–9；预设 1 / 9 的机器人那一半由页面执行' },
      { id: 'B2', title: '台词面板跟着模式', files: 'web/monitor/public/say-ui.js', note: '不在脱口秀时禁用；计费关着写原因；“别说了”永远可点' }
    ] },
    { group: '固件（ESP32 一侧，构建可以做）', items: [
      { id: 'C1', title: 'mode_wire.h：快照、输入、回执、输入队列', files: 'fw/mode_wire.h', note: '坏快照保持原样；相同 seq 不算坏；握手后第一条总收下' },
      { id: 'C2', title: '口令改绑', files: 'fw/asr_mode_words.h、asr_unit.cpp 等', note: 'open/close 不再追踪、start/stop 不再听觉、next 不再播台词' },
      { id: 'C3', title: '首页手势分类', files: 'fw/touch_gesture.h、screen.cpp', note: '单击 / 双击（0.3 秒窗口）/ 上下左右滑；按真机数据（松手坐标 −1,−1）' },
      { id: 'C4', title: '/mouth 上收发模式消息', files: 'fw/mouth_link.cpp', note: '输入队列放 PSRAM；第一步不收本体控制' },
      { id: 'C5', title: '屏幕：角标、每模式外观、模式选择页、快捷设置页', files: 'fw/screen.cpp、mode_look.h', note: 'ASCII 角标（现有字体）；事件驱动等待 = IDLE 屏 + AUTO；页面用时才建' },
      { id: 'C6', title: '版本 0.4.92、协议文档、构建产物、刷机脚本，然后停下问主人', files: 'CMakeLists.txt、docs/collector-protocol.md', note: 'preflight / deploy / rollback 三个脚本；只写 ota_1 / 0x510000、写后 verify', ask: '停下，等主人当次同意再刷（刷机在 D2）' }
    ] },
    { group: '部署与验收（每一步都要主人同意）', items: [
      { id: 'D0', title: '单独上线 A0，在 0.4.91 上验证“行为不变”', files: 'server/ear/deploy.sh', note: '先在服务器上留旧文件、写回滚命令；不通过就回滚', ask: '要主人同意，挑没人用的时候（ear 重启断一次听觉）' },
      { id: 'D1', title: '模式版本的服务器上线（和 D2 同一次，紧挨着做）', files: 'server/mind/deploy.sh、server/ear/deploy.sh', note: '留 mind、ear 备份；D2 当次刷不了就回滚到 D0 的状态', ask: '要主人同意' },
      { id: 'D2', title: '刷 0.4.92、实机验收、需要时回滚', files: 'preflight.py、deploy.py、rollback.py', note: 'DualKey 在真服务器上第一次推通；固件和服务器成对回滚', ask: '刷机要主人当次同意' }
    ] }
  ];

  var deferredList = [
    '下行控制（第二步）：/mouth 上的 control 消息；有了它机器人发的预设 1 / 9 才能执行机器人那一半，“听觉跟着模式”也靠它',
    '计费加固（第二步）：llm.chat 一处的进程级闸门、TTS 也查、各后台循环先查总闸、ear 的听悟也要总闸',
    '中文小字库（和字幕一起）：角标改成一个中文字，缺字退回 label',
    '进入模式自动打开它要的付费感官、听觉跟着模式（主人：先手动）',
    '对话、字幕、围观、陪伴、设置的内容；事件驱动的事件规则和客人模式',
    'ASR “hello” 打断；换 ASR 词表；中文语音口令进模式',
    'IDLE 的音乐和视频',
    '官方遥控器第二层绑定模式；遥控器情绪层',
    '每个模式的每日花费上限'
  ];

  /* ---------- 主人的决定（设计 §1.2、§12） ---------- */
  var questions = [
    { n: 1, q: '已在脱口秀时说 forward', proposed: '说一轮【我定】。理由：脱口秀里没有别的口令能让它说；主人第 8 条只说 forward = 进入。另一种：已在脱口秀时 forward 什么都不做',
      decided: true, answer: '按默认：已在脱口秀时 forward = 说一轮。', ownerWords: '按默认', impact: '不在脱口秀时 forward 只进入、不说话' },
    { n: 2, q: '不在脱口秀时按预设 9', proposed: '不切模式，提前写、连续说跳过，提示一句【我定】。理由：预设不改模式（4 除外），提前写只在脱口秀里有意义。另一种：9 顺带进脱口秀',
      decided: true, answer: '按默认：留在当前模式，提前写、连续说跳过，屏幕提示一句。', ownerWords: '按默认', impact: '要“全开”含提前写，先 forward 再 nine' }
  ];
  var openNote = '唯一还没想好的是“听觉要不要一个口令”：第一步不做，听觉先手动，预设 5 空着；以后用空着的预设键或别的口令加上，不影响框架。';

  var ownerAlsoConfirmed = [
    '1. 关掉一个模式 → IDLE；直接打开另一个模式的指令 → 直接进那个模式',
    '2. 事件驱动是外面一层，由它进别的模式',
    '3. 进 IDLE 或开机时不动付费的听觉、视觉，先手动',
    '4. 预设 1 和 9 按字面：1 = 数据收集（不花钱；视觉、听觉、情绪感知开；连着服务器）；9 = 花费全开（含提前写和连续说）',
    '5. 脱口秀的眼睛一律是笑面男，开着视觉也是；推翻 11-10 的规则',
    '6. 首页单击不再打开摘要页，改成左滑',
    '7. start / stop 不再开关听觉；第一步里听觉暂时没有口令',
    '8. 新的口令和事件分配直接取代旧的：forward = 进入脱口秀，backward = 离开（→ IDLE）；第一版“backward = 停”“别的模式里 forward 直接说一轮”作废',
    '9. ASR 和 DualKey 互不相关：脱口秀里 DualKey 单击照旧能让它别说（触摸单击在 hello 说话时也是停）',
    '10. 听觉要不要口令：还没想好；听觉先手动，预设 5 空着',
    '11. 事件驱动没有自己的眼睛：机器处在它选的模式里，眼睛就是那个模式的眼睛',
    '12. 手动选了一个模式，事件驱动停',
    '13. 计费总闸和预设放进第一步（没有反对，按默认）'
  ];

  /* ---------- 与第一版 / 主人补充的差异（设计 §1.1、§11、§3.8） ---------- */
  var differences = [
    { topic: 'joy stick 是哪个摇杆', source: '补充：“joy stick up / down / left / right”', design: '= DualKey 的摇杆，不是官方遥控器的摇杆（主人 11-13 说过；DualKey 设计原话“上下左右推时分别入 4 种模式”）', tag: '用户定', ref: '设计 §1.1' },
    { topic: '七个模式 → 八个', source: '第一版七个', design: '加了 IDLE', tag: '补充', ref: '设计 §11.1' },
    { topic: '离开模式去哪', source: '第一版：离开回脱口秀', design: '关掉回 IDLE；直接打开另一个模式就直接去', tag: '主人已定', ref: '设计 §11.2' },
    { topic: '事件驱动的地位', source: '补充：单击“取决于进入了什么模式”', design: '外面一层：开着时自己进出客人模式，客人离开回“等待”；手动选模式（含 open）= 事件驱动停；托管时 stop = 一起停 → IDLE', tag: '主人已定 + 我定', ref: '设计 §11.3、§3.8' },
    { topic: 'forward / backward', source: '第一版：脱口秀里说一句 / 停；不在脱口秀时 forward 先回再说', design: '进入 / 离开脱口秀；不在脱口秀时 forward 只进入；已在脱口秀时 forward = 说一轮', tag: '主人已定', ref: '设计 §11.4、§5.1、§12' },
    { topic: '停说话', source: '第一版：backward = 停', design: '脱口秀里正在说时单击（屏幕或 DualKey）= 停；/say/stop；离开模式', tag: '主人已定', ref: '设计 §5.4；第 9 条' },
    { topic: '眼睛', source: '11-10 规则：视觉关笑面男、视觉开机甲眼', design: '每个模式固定眼睛；脱口秀一律笑面男。还没收到快照（开机到 /mouth 连上前）照旧规则', tag: '主人已定', ref: '设计 §11.5' },
    { topic: '事件驱动的眼睛', source: '补充：“自己决定”；v2 曾读成“我们来定 = 机甲眼安静表情”', design: '没有自己的眼睛，用它所选模式的眼睛；等待时 IDLE 屏 + 角标 AUTO', tag: '主人已定 + 我定', ref: '设计 §3.2；第 11 条' },
    { topic: '字幕', source: '第一版第 10 节第 3 条（待定）', design: '字幕独占屏幕；两个方向是同一个模式，在字幕里推另一方向直接换', tag: '补充 + 我定', ref: '设计 §11.6、§3.5' },
    { topic: '首页触摸', source: '今天：首页轻点去摘要页', design: '单击、双击按模式；左滑 = 状态摘要；新增上滑模式页、下滑快捷设置页', tag: '主人已定', ref: '设计 §11.7' },
    { topic: '单击“无”的模式', source: '补充表：字幕“无”；设置、事件驱动没写', design: '屏幕本地提示 NO TAP；DualKey 单击到服务器 = no_action', tag: '我定', ref: '设计 §3.8' },
    { topic: 'IDLE 里 DualKey 单击', source: '补充：IDLE 单击 = badge', design: '屏幕单击翻 badge；DualKey 单击 = no_action（badge 是本地页）', tag: '我定', ref: '设计 §3.8' },
    { topic: '点当前模式', source: '—', design: '上滑页和网页再点当前模式 = 离开 → IDLE', tag: '我定', ref: '设计 §3.8、§6' },
    { topic: '机器人重启', source: '—', design: '回脱口秀；本来就在脱口秀也关提前写、连续说，停说话；付费感官不动', tag: '我定 + 主人已定', ref: '设计 §3.6、§3.8' },
    { topic: '下滑快捷设置页', source: '补充：“音量 计费开关”', design: '再加 0–9 十个预设键', tag: '我定', ref: '设计 §6' },
    { topic: '预设 1', source: '补充：“数据收集模式（不花钱，视觉听觉，情绪感知，连接到服务器）”', design: '同 0（计费总闸也关）+ 本地听觉 + 视觉上传；情绪感知 = 本地听觉每句话的情绪标签', tag: '主人已定 + 我定', ref: '设计 §7' },
    { topic: '预设 9', source: '补充：“花费全开”；v2 曾默认不含提前写、连续说', design: '总闸开、听悟、识别、情景、纪要、嘴切 TTS；在脱口秀时提前写、连续说也开，不在时跳过并提示', tag: '主人已定', ref: '设计 §7、§12' },
    { topic: '预设的机器人那一半', source: '—', design: '第一步只有网页能执行；机器人发的 1 / 9 提示 SENSES: USE PAGE（下行控制在第二步）', tag: '审计后改', ref: '设计 §7、§11.11' },
    { topic: '预设 5', source: 'v2 曾提议“听觉反转”', design: '空着：听觉先手动', tag: '主人已定', ref: '第 10 条' },
    { topic: '计费开关', source: '补充：“计费开关”', design: '计费总闸；第一步 = 关掉付费开关 + 关着时不许再开 + 拦说话；进程级模型闸门第二步；打开不恢复任何东西', tag: '补充 + 审计后改', ref: '设计 §3.3、§11.9' },
    { topic: '进入模式时的付费感官', source: '主人 11-13：进入模式时自动打开；补充里对话“可以使用情景、情绪、视觉、听觉”', design: '第一步进入模式不开；IDLE 和开机也不动（先手动）', tag: '主人已定', ref: '设计 §11.10' },
    { topic: 'start / stop、open / close、next', source: '今天：开关听觉、开关追踪、随机播 SD 台词', design: '进出事件驱动、进出设置、脱口秀里开提前写；听觉第一步没有口令', tag: '主人已定', ref: '设计 §11.14、§5.1' },
    { topic: 'DualKey 单击 / 双击 / 长按', source: '主人：“点击 joystick 表示一个事件，用于每个模式触发任务或者终止任务”', design: '单击 = 首页单击、双击 = 首页双击；长按 1 秒回脱口秀（第一版 3 秒）', tag: '我定', ref: '设计 §5.2、§11.13' },
    { topic: '下行通道', source: '第一版：甲 / 乙两选一', design: 'ear 自己终结 /mouth，先单独上线（A0 / D0）；第一步不下发本体控制', tag: '按 DualKey 设计', ref: '设计 §11.11' },
    { topic: '角标', source: '第一版：颜色 + ASCII 字母', design: '颜色 + ASCII 短名（TALK、CHAT、SET、AUTO、SUB、MEET、PAL、IDLE）；中文角标等中文字库', tag: '审计后改', ref: '设计 §11.12、§6' },
    { topic: 'IDLE 的内容', source: '补充：“开放音乐或者视频（？放得动）或展示badge”；双击“音乐模式”', design: '第一步只有 IDLE 页和 badge 页；音乐以后，视频放不放得动没验证', tag: '推迟', ref: '设计 §8' }
  ];

  /* ---------- 编码时发现、修订后仍没写明的地方 ---------- */
  var ambiguities = [
    { title: '“正在说”从哪一刻算', text: '脱口秀单击按“在不在说”分两种意思（设计 §5.4）。说一轮刚按下、模型还在写、还没出声的那几秒算不算“在说”，文档没写；按“不算”的话，这几秒里连点两下会排第二轮（被 busy 挡掉）而不是停。' },
    { title: '事件驱动等待时的单击', text: '事件驱动·等待显示的就是 IDLE 屏幕，但单击是 NO TAP（不翻 badge），IDLE 里单击翻 badge。屏幕一样、单击不一样，只能靠角标 AUTO / IDLE 区分。' },
    { title: '机器人说 one', text: '机器人发的预设 1 只做服务器那一半（关总闸），机器人那一半要去网页做。所以对机器人说 one 的效果和 zero 一样，只多一句 SENSES: USE PAGE；“数据收集”要在网页上按 1 才完整。' },
    { title: '首页的范围', text: '手势只在首页解释（§5.4），但设置、字幕、IDLE（和事件驱动等待）的样子是专用页。页面按“这些占位页就是该模式的首页”处理，否则 IDLE 单击翻 badge 就不成立。' },
    { title: '字幕、围观、陪伴没有口令', text: '这三个只有 DualKey、上滑页、网页能进出；DualKey 不在手边时只能靠上滑页、预设 4 或别的模式的进入词离开。不是矛盾，主人知道就好。' },
    { title: '模拟器不计时', text: '§5.6 的时间规则（busy / too_soon、3 秒 stale、0.3 秒双击、5 分钟设置超时、2 秒提示）模拟器都不计时，只在日志和下面的时间规则表里写明。' }
  ];

  /* ---------- 时间规则（设计 §5.6） ---------- */
  var timing = [
    ['设备输入（机器人、DualKey）从按下到 mind 收到', '超过 3 秒丢掉（打分不受影响）', 'stale'],
    ['同一设备输入重发', '按 device + boot + seq 只算一次', '第一次的结果'],
    ['说一轮', '同一时间只有一轮；两轮开始至少隔 10 秒', 'busy、too_soon'],
    ['首页双击', '第一下松手后 300 ms 内第二下；单击晚约 0.3 秒生效', '—'],
    ['首页点 / 滑', '点：按住 30–700 ms、移动 ≤ 18 px；滑：主方向 ≥ 55 px、按住 30–1000 ms；松手后 180 ms 内的新按下忽略', '—'],
    ['DualKey 长按', '按住 1 秒（DualKey 固件自己计时）', '回脱口秀'],
    ['设置模式无动作', '5 分钟', '回原来的模式'],
    ['ear 问 mind 的 seq', '每 1 秒；另外每 60 秒重推一次', '约 2 秒内到机器人'],
    ['mode_result 屏幕提示、切换闪屏', '2 秒、1 秒', '—']
  ];

  /* 模式一览顶部的四句话 */
  var layers = [
    { title: '普通模式', text: '脱口秀（默认）、对话、字幕（两个方向）、围观、陪伴。同一时刻只有一个模式（严格互斥）。' },
    { title: '设置 = 临时层', text: 'open 进入时记下原来的模式；close 或 5 分钟没动作就回去；在设置里选别的模式就直接去那个模式。' },
    { title: '事件驱动 = 外面一层（主人已定）', text: 'start 进入“等待”（IDLE 屏 + 角标 AUTO）；以后它按看到、听到的事自己进入客人模式，用那个模式的眼睛，客人离开回“等待”；stop 回 IDLE（托管时客人一起停）；主人手动选模式（含 open）= 事件驱动停。' },
    { title: 'IDLE', text: '没有模式在跑、事件驱动也没开。关闭任何模式都回这里；直接打开另一个模式就直接去，不经过 IDLE（主人已定）。' }
  ];

  window.DESIGN = {
    layers: layers,
    meta: {
      title: 'hello 模式查看器',
      version: '设计第二版（2026-11-14 修订：两轮主人回答 + 模拟页边界情况）+ 第一步计划（18 个任务）',
      sources: [
        'docs/superpowers/specs/2026-11-14-hello-modes-design-v2.md',
        'docs/superpowers/plans/2026-11-14-hello-modes-step1-framework.md',
        '主人 2026-11-14 补充（设计 §1 原文）',
        '主人 2026-11-14 对四个问题的回答'
      ],
      note: '“第一步：实现”指计划里要做的，代码还没写。'
    },
    eyes: eyes, pools: pools, modes: modes, modeOrder: modeOrder,
    effects: effects, controls: controls, presets: presets, rules: rules, codes: codes,
    inputGroups: inputGroups, anyMode: anyMode, onModeChange: onModeChange,
    initialState: initialState, spendItems: spendItems, diagram: diagram, screens: screens,
    tasks: tasks, deferredList: deferredList, questions: questions, openNote: openNote, ownerAlsoConfirmed: ownerAlsoConfirmed, timing: timing,
    differences: differences, ambiguities: ambiguities,
    stepBadges: { impl: '第一步：实现', shell: '第一步：空壳', deferred: '推迟' }
  };
})();
