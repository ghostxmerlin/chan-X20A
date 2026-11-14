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
      words: { asr: 'forward / backward', dualkey: '—（DualKey 只打分）', other: '网页模式条、开机默认；上滑页（0.4.93）' },
      touch: {
        tap: { owner: '说一轮', act: 'server', effects: ['say_round'], design: '没在说 → 说一轮；hello 正在播放 → 停（hello 自己看是不是在播放）',
          cases: [{ if: 'speaking', effects: ['stop_speech'], rule: 'talk_tap_stop', design: '正在说时' }] },
        double: { owner: '开启提前', act: 'server', effects: ['prefetch_on'], design: '开提前写' }
      },
      inputs: {
        'asr.forward': { effects: ['say_round'], rule: 'talk_forward' },
        'asr.next': { effects: ['prefetch_on'], rule: 'talk_next' },
        'asr.previous': { effects: ['prefetch_off'], rule: 'talk_previous' }
      },
      paid: ['说一轮（按了才写、才花）', '提前写（进脱口秀自动开）', '连续说（进脱口秀自动开）'],
      enterDo: ['每次进入（开机、从别的模式切来、从设置回来）都打开提前写和连续说，家人声音不选（主人第 14 条）', '付费感官（听悟、识别、情景、纪要）不动'],
      leaveDo: ['停 TTS（正在说的停）', '关提前写、关连续说，存货丢掉'],
      onLeave: ['talk_leave'],
      onEnter: ['talk_enter'],
      step1: { status: 'live', text: '已上线 0.4.92：今天的脱口秀，入口是 forward、首页单击、next / previous、双击、网页' },
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
      words: { asr: 'play / pause', dualkey: '—', other: '网页；上滑页（0.4.93）' },
      touch: {
        tap: { owner: '停', act: 'server', cases: [{ if: 'speaking', effects: ['stop_speech'], rule: 'shell_tap_stop', design: '正在说时' }], step1Code: 'not_implemented', design: '正在说 → 停；否则第一步 not_implemented（这些模式里还不会说话，“停”只防残留）' },
        double: { owner: '展示对话', act: 'server', step1Code: 'not_implemented', design: '屏幕上展示对话' }
      },
      paid: ['（以后）连续对话的模型调用、查询和搜索', '（以后）可以用情景、情绪、视觉、听觉——进入时自动开付费感官暂不做'],
      enterDo: ['第一步：只换眼睛和角标，不花钱'],
      leaveDo: ['停 TTS', '（以后）取消正在生成的回答'],
      step1: { status: 'shell', text: '已上线 0.4.92 的空壳：能进、能出、有眼睛和角标，没有内容（对话正在做）' },
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
      words: { asr: 'open / close', dualkey: '—', other: '网页；5 分钟没动作自动关（hello 计时）；上滑页（0.4.93）' },
      touch: {
        tap: { owner: '（补充的触摸表里没有）', act: 'none', design: '无【我定】：屏幕本地提示 NO TAP' },
        double: { owner: '（补充的触摸表里没有）', act: 'none', design: '无【我定】' }
      },
      paid: ['（以后）按语义改参数会调模型'],
      enterDo: ['记下进入前的模式（return_to）'],
      leaveDo: ['close 或 5 分钟无动作 → 回进入前的模式', '在设置里选别的模式 → 直接去那个模式，return_to 丢掉', '等确认的事作废'],
      step1: { status: 'shell', text: '已上线 0.4.92 的空壳：能进能出、角标 SET', pending: '参数页（只读）0.4.93' },
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
      words: { asr: 'start / stop', dualkey: '—', other: '网页；上滑页（0.4.93）' },
      touch: {
        tap: { owner: '取决于进入了什么模式', act: 'none', design: '跟着它进入的客人模式；等待时无（屏幕本地提示 NO TAP）' },
        double: { owner: '（空）', act: 'none', design: '同单击' }
      },
      inputs: {
        'sim.guest': { guest: 'crowd', rule: 'guest_enter' }
      },
      paid: ['（以后）进入客人模式后，那个模式自己的花钱行为'],
      enterDo: ['进入“事件驱动·等待”：IDLE 屏幕、角标 AUTO'],
      leaveDo: ['stop → IDLE（托管着客人模式时也是：客人一起停）', '主人手动选了别的模式（含 open 进设置）= 事件驱动停 + 进入那个模式（主人 2026-11-14）'],
      step1: { status: 'shell', text: '已上线 0.4.92 的空壳：不会自己进入任何模式，只有“等待”（角标 AUTO）', pending: 'IDLE 屏幕 0.4.93' },
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
      words: { asr: 'seven（中→英）/ eight（英→中），只进不出', dualkey: '—（DualKey 只打分）', other: '网页两个按钮；上滑页两格（0.4.93）' },
      touch: {
        tap: { owner: '无', act: 'none', design: '无：屏幕本地提示 NO TAP' },
        double: { owner: '无', act: 'none', design: '无' }
      },
      paid: ['（以后）本地识别 + 翻译调用'],
      enterDo: ['显示字幕页和方向'],
      leaveDo: ['停 TTS', '（以后）清屏、取消翻译调用'],
      step1: { status: 'shell', text: '已上线 0.4.92 的空壳：能进能出、角标带方向', pending: '字幕占位页 0.4.93' },
      future: '本地识别 + 翻译、只翻最清楚的声音（第一版 5.5）',
      enterIn: [
        { in: 'asr.d7', dir: 'zh_en' }, { in: 'asr.d8', dir: 'en_zh' }, { in: 'web.p7', dir: 'zh_en' }, { in: 'web.p8', dir: 'en_zh' },
        { in: 'quick.p7', dir: 'zh_en' }, { in: 'quick.p8', dir: 'en_zh' },
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
      words: { asr: 'five，只进不出', dualkey: '—（DualKey 只打分）', other: '网页；上滑页（0.4.93）' },
      touch: {
        tap: { owner: '停', act: 'server', cases: [{ if: 'speaking', effects: ['stop_speech'], rule: 'shell_tap_stop', design: '正在说时' }], step1Code: 'not_implemented', design: '正在说 → 停；否则第一步 not_implemented（这些模式里还不会说话，“停”只防残留）' },
        double: { owner: '抗议', act: 'server', step1Code: 'not_implemented', design: '抗议（表演）' }
      },
      paid: ['（以后）节目内容的模型调用和 TTS'],
      enterDo: ['第一步：只换眼睛和角标'],
      leaveDo: ['停 TTS'],
      step1: { status: 'shell', text: '已上线 0.4.92 的空壳：换眼睛和角标' },
      future: '节目、不谈不在场的人（第一版 5.6）',
      enterIn: [{ in: 'asr.d5' }, { in: 'web.p5' }, { in: 'quick.p5' }, { in: 'pick.crowd', toggle: true }, { in: 'web.crowd', toggle: true }],
      leaveIn: [],
      leaveTo: 'idle'
    },
    companion: {
      name: '陪伴', color: '#F2E6C8', label: 'PAL', kind: 'normal',
      owner: '7.陪伴模式，全天配置一位熟人，作为情绪助手和聊友 joy stick right',
      ownerEye: '陪伴模式--奶油白眼睛',
      look: { eye: 'v18', pool: 'none' },
      lookNote: '奶油白眼睛',
      words: { asr: 'six，只进不出', dualkey: '—（DualKey 只打分）', other: '网页；上滑页（0.4.93）' },
      touch: {
        tap: { owner: '停/唤醒', act: 'server', cases: [{ if: 'speaking', effects: ['stop_speech'], rule: 'shell_tap_stop', design: '正在说时' }], step1Code: 'not_implemented', design: '正在说 → 停；唤醒以后做' },
        double: { owner: '无', act: 'none', design: '无' }
      },
      paid: ['（以后）陪聊的模型调用和 TTS'],
      enterDo: ['第一步：只换眼睛和角标'],
      leaveDo: ['停 TTS'],
      step1: { status: 'shell', text: '已上线 0.4.92 的空壳：换眼睛和角标' },
      future: '一位熟人（人物甲）、近期说过话就算对 hello 说（第一版 5.7）',
      enterIn: [{ in: 'asr.d6' }, { in: 'web.p6' }, { in: 'quick.p6' }, { in: 'pick.companion', toggle: true }, { in: 'web.companion', toggle: true }],
      leaveIn: [],
      leaveTo: 'idle'
    },
    idle: {
      name: 'IDLE', color: '#505050', label: 'IDLE', kind: 'idle',
      owner: '8.IDLE 模式关闭后，且事件驱动模式没有开启。开放音乐或者视频（？放得动） 或展示badge',
      ownerEye: '（补充里没有写）',
      look: { eye: 'page_idle', pool: 'none' },
      lookNote: '黑底小字 hello；单击切 badge 页（大字 hello + 固件版本）',
      words: { asr: 'four，只进不出；离开别的模式也到这里', dualkey: '—', other: '网页；上滑页（0.4.93）' },
      touch: {
        tap: { owner: 'badge', act: 'badge', design: '本地翻徽章页，不经服务器（徽章页 0.4.93）' },
        double: { owner: '音乐模式', act: 'server', step1Code: 'not_implemented', deferred: true, design: '放音乐（以后）' }
      },
      paid: ['无。只停模式自己的花钱；听悟、识别、情景、纪要不动（主人已定：先手动）'],
      enterDo: ['停 TTS；从脱口秀来时关提前写、连续说', '付费感官不动（主人已定）'],
      leaveDo: ['—'],
      step1: { status: 'live', text: '已上线 0.4.92：能进（离开别的模式、four、网页）、角标 IDLE', pending: 'IDLE 页、徽章页 0.4.93' },
      future: 'badge；音乐（/mouth 的 Opus 下行能放）；视频放不放得动没验证',
      enterIn: [{ in: 'asr.d4' }, { in: 'web.p4' }, { in: 'quick.p4' }, { in: 'pick.idle', toggle: true }, { in: 'web.idle', toggle: true }],
      leaveIn: [],
      leaveTo: 'idle'
    }
  };

  var modeOrder = ['talk', 'chat', 'settings', 'event', 'subtitle', 'crowd', 'companion', 'idle'];

  /* ---------- 效果：每个效果先查 guards（任何一条不过 = 整个动作不做，返回那条的 code），再执行 ops ---------- */
  var effects = {
    stop_speech: { short: '停说话', label: '停 TTS（正在说的停）', server: true, offType: true, ops: [['set', 'speaking', false]] },
    talk_enter: { short: '开提前写+连续说', label: '进脱口秀：提前写、连续说打开，家人声音不选（主人第 14 条）', server: true, onlyIn: 'talk', ops: [['set', 'talk.prefetch', true], ['set', 'talk.auto', true]] },
    talk_leave: { short: '关提前写/连续说', label: '关提前写、关连续说，存货丢掉', server: true, offType: true, ops: [['set', 'talk.prefetch', false], ['set', 'talk.auto', false]] },
    say_round: { short: '说一轮',
      label: '说一轮（mind 写一轮并开口，花一次钱）', server: true, robotToast: 'SAYING',
      guards: [
        { need: 'mouthTts', code: 'mouth_not_tts' }
      ],
      ops: [['set', 'speaking', true], ['inc', 'rounds']],
      note: '实际还有：同时最多一轮（busy）、两轮至少隔 10 秒（too_soon），模拟器不计时'
    },
    prefetch_on: { short: '开提前写',
      label: '开提前写',
      server: true, robotToast: 'PREFETCH ON', guards: [{ need: 'off', path: 'talk.prefetch', code: 'already' }],
      ops: [['set', 'talk.prefetch', true]]
    },
    prefetch_off: { short: '关提前写',
      label: '关提前写', server: true, offType: true,
      guards: [{ need: 'on', path: 'talk.prefetch', code: 'already' }],
      ops: [['set', 'talk.prefetch', false]]
    },
    toggle_prefetch: { short: '提前写反转',
      label: '提前写反转',
      guards: [{ need: 'mode', mode: 'talk', code: 'only_talk' }],
      ops: [['toggle', 'talk.prefetch']]
    },
    toggle_auto: { short: '连续说反转',
      label: '连续说反转',
      guards: [{ need: 'mode', mode: 'talk', code: 'only_talk' }],
      ops: [['toggle', 'talk.auto']]
    },
    billing_off_all: { short: '花费全关',
      label: '花费全关：听悟、识别、情景、纪要、提前写、连续说全关；正在说的停（以后单个开关照样能开）',
      ops: [['set', 'senses.tingwu', false], ['set', 'senses.eye', false], ['set', 'senses.scene', false],
        ['set', 'senses.notes', false], ['set', 'talk.prefetch', false], ['set', 'talk.auto', false], ['set', 'speaking', false]]
    },
    paid_senses_on: { short: '付费感官开',
      label: '听悟、识别、情景、纪要开',
      ops: [['set', 'senses.tingwu', true], ['set', 'senses.eye', true], ['set', 'senses.scene', true], ['set', 'senses.notes', true]]
    },
    mouth_tts: { short: '嘴切 TTS', label: '嘴切到 TTS 档', ops: [['set', 'env.mouthTts', true]] },
    talk_paid_on: { short: '提前写+连续说开',
      label: '提前写、连续说开（只在脱口秀里执行；不在脱口秀时跳过，下次进脱口秀会自动开）',
      onlyIn: 'talk',
      ops: [['set', 'talk.prefetch', true], ['set', 'talk.auto', true]]
    },
  };


  /* ---------- 数字口令（主人 2026-11-14 新表，取代旧预设表；设计“现状”一节） ----------
   * kind：mind = 发给 mind 执行（花钱的服务都在服务器上）；select = hello 自己进模式（只进不出）；none = 空
   */
  var presets = [
    { n: 0, name: '花费全关', kind: 'mind', server: ['billing_off_all'], owner: '0 -- 花费全关', note: 'mind 执行；网页计费按钮“关”也是它' },
    { n: 1, name: '空', kind: 'none', owner: '1 -- 数据收集模式（不花钱，视觉听觉，情绪感知，连接到服务器）', note: '旧的“数据收集”作废，现在 no_action' },
    { n: 2, name: '空', kind: 'none', owner: '2 -- 听悟开关反转', note: '旧的“听悟反转”作废，现在 no_action' },
    { n: 3, name: '空', kind: 'none', owner: '3 -- 提前写开关反转', note: '旧的“提前写反转”作废，现在 no_action' },
    { n: 4, name: '进 IDLE', kind: 'select', mode: 'idle', owner: '4 -- IDLE模式', note: 'hello 自己当作 select 执行；已在 IDLE 再说 = 不变' },
    { n: 5, name: '进围观', kind: 'select', mode: 'crowd', owner: '5 --（空）', note: '只进不出：已在围观再说 five = 不变' },
    { n: 6, name: '进陪伴', kind: 'select', mode: 'companion', owner: '6 --（空）', note: '只进不出' },
    { n: 7, name: '进字幕（中音英字）', kind: 'select', mode: 'subtitle', dir: 'zh_en', owner: '7 --（空）', note: '只进不出；在英→中里说 seven = 换方向' },
    { n: 8, name: '进字幕（英音中字）', kind: 'select', mode: 'subtitle', dir: 'en_zh', owner: '8 --（空）', note: '只进不出；在中→英里说 eight = 换方向' },
    { n: 9, name: '花费全开', kind: 'mind', server: ['paid_senses_on', 'mouth_tts', 'talk_paid_on'], owner: '9 -- 花费全开', note: 'mind 执行；mind 每次启动也执行一遍；网页计费按钮“开”也是它；提前写、连续说只在脱口秀里开' }
  ];

  /* ---------- 规则文字（日志和触发表里显示的“为什么”） ---------- */
  var rules = {
    offline: { text: '和服务器断开（/mouth 没连上）：数字 0 / 9 要 mind 执行，做不了；模式切换照样在 hello 上做', ref: '设计“现状”' },
    offline_effects: { text: '和服务器断开时 hello 照样切模式；连上 /mouth 后先报一次当前状态。报告晚到超过 3 秒时 mind 只执行“关”的效果（停说、关提前写、关连续说），不执行说话和开的效果', ref: '设计“现状”' },
    any_boot: { text: 'hello 开机就是脱口秀（自己定，不等 mind），然后报给 mind；算一次“进入脱口秀”：正在说的停，提前写、连续说打开（家人声音不选）', ref: '设计“现状”、§3.8；主人第 14 条' },
    boot_paid: { text: '开机不动付费感官（听悟、识别、情景、纪要）', ref: '主人 2026-11-14 第 3 条' },
    mind_restart: { text: 'mind 重启：不改 hello 的模式（hello 重连后报一次当前状态）；mind 每次启动执行一遍 9：花费全开（提前写、连续说只在脱口秀里开）', ref: '设计“现状”；主人第 16 条' },
    mode_input: { text: '这个模式里这个输入有自己的意思', ref: '设计 §3.5、§5.1' },
    talk_forward: { text: '已在脱口秀时 forward = 说一轮', ref: '设计 §12 第 1 条（主人：按默认）' },
    talk_next: { text: '脱口秀里 next = 开提前写', ref: '设计 §5.1' },
    talk_previous: { text: '脱口秀里 previous = 关提前写', ref: '设计 §5.1' },
    talk_tap_stop: { text: '脱口秀里 hello 正在播放时单击 = 停；没在播放 = 说一轮（“正在说” = hello 自己看是不是在播放）', ref: '设计 §5.4、“现状”' },
    shell_tap_stop: { text: '对话、围观、陪伴（空壳）里单击：hello 正在播放 → 停（只防残留）；否则 not_implemented', ref: '设计 §3.8' },
    guest_enter: { text: '（以后）事件驱动按看到、听到的事自动进入一个客人模式，用那个模式的眼睛，角标旁加小 A。第一步不会发生', ref: '设计 §3.4；主人第 2、11 条' },
    guest_none: { text: '只有“事件驱动·等待”时才会自动进入客人模式；现在不在等待', ref: '设计 §3.4' },
    host_leave: { text: '事件驱动托管着客人模式时说 stop：事件驱动和客人模式一起停，→ IDLE', ref: '设计 §3.8' },
    host_already: { text: '事件驱动已经开着（正托管客人模式），再进入事件驱动不变', ref: '设计 §3.5' },
    leave: { text: '在模式 X 里说 X 的离开词：→ IDLE', ref: '设计 §3.5；主人第 1 条' },
    toggle_leave: { text: '已在这个模式，网页（或以后的上滑页）再点当前模式 = 离开 → IDLE', ref: '设计 §3.5、§3.8' },
    leave_hosted: { text: '客人模式离开时回“事件驱动·等待”，不是 IDLE', ref: '设计 §3.5' },
    leave_return: { text: '设置是临时层：close 或 5 分钟无动作（hello 计时），回到进入设置前的模式', ref: '设计 §3.4、“现状”' },
    switch_dir: { text: '在字幕里选另一个方向（seven / eight、网页另一个按钮）= 换方向，不经过 IDLE', ref: '设计 §3.5【我定】' },
    already: { text: '已在 X 时再进入 X：不变（数字 4–8 只进不出，再说一遍也不离开）', ref: '设计 §3.5、“现状”' },
    enter: { text: '某模式的进入（口令前一个词、数字 4–8、网页）：hello 直接切到那个模式，不经过 IDLE，然后报给 mind', ref: '设计 §3.5、“现状”；主人第 1 条' },
    enter_temp: { text: '进入设置（临时层）时记下当时显示的模式，关掉设置时回去', ref: '设计 §3.4、§3.8' },
    enter_from_temp: { text: '离开设置（不论哪条路）：记下的“原来的模式”一律清掉', ref: '设计 §3.8' },
    enter_from_host: { text: '事件驱动开着时手动选了模式（含 open 进设置）：事件驱动停，直接进那个模式；进设置时记下的是当时显示的模式（等待中就是 IDLE），不再托管', ref: '设计 §3.8；主人第 12 条' },
    not_in_mode: { text: '不在 X 时说 X 的离开词：不变，屏幕提示“不在 X”', ref: '设计 §3.5' },
    touch_server: { text: '首页单击 / 双击按当前模式的 touch（hello 按 mode_rules 执行，效果报给 mind）', ref: '设计 §5.4' },
    touch_shell: { text: '空壳模式：返回 not_implemented（补充里的“停 / 抗议 / 展示对话 / 音乐”以后做）', ref: '设计 §3.8' },
    touch_none: { text: '这个模式单击 / 双击没有动作：hello 本地提示 NO TAP', ref: '设计 §3.8、§5.4' },
    touch_badge: { text: 'IDLE 里屏幕单击：hello 本地翻徽章页（徽章页 0.4.93 才有）', ref: '设计 §5.4、“现状”' },
    only_talk: { text: 'next / previous 只在脱口秀里有意思（开 / 关提前写）；别的模式里无效，屏幕提示 ONLY IN TALK', ref: '设计 §5.1' },
    preset: { text: '数字 0 / 9 = 花费全关 / 全开，发给 mind 执行（花钱的服务都在服务器上）；不改模式', ref: '设计“现状”' },
    digit_empty: { text: '数字 1、2、3 空着：no_action（旧的数据收集、听悟反转、提前写反转作废）', ref: '设计“现状”' },
    switch_toggle: { text: '页面开关条：单个付费开关随时能开能关，计费不拦', ref: '设计“现状”；主人第 17 条' },
    say_only_talk: { text: '台词面板：说一轮、提前写、连续说只在脱口秀模式（花钱的效果只在脱口秀里执行）', ref: '设计“现状”' },
    say_panel: { text: '台词面板（今天就有）', ref: '设计 §2.3' },
    say_stop: { text: '“别说了” /say/stop 永远放行', ref: '计划 A4' },
    local_picker: { text: '首页上滑：模式选择页（0.4.93 待做；这里演示设计）', ref: '设计 §6、“现状”' },
    local_quick: { text: '首页下滑：快捷设置页（0.4.93 待做；这里演示设计）', ref: '设计 §6、“现状”' },
    local_summary: { text: '首页左滑：状态摘要页（已上线）；单击不再打开摘要', ref: '设计 §5.4；主人第 6 条' },
    local_right: { text: '首页右滑没有定义', ref: '计划 C3' },
    local_volume: { text: '音量 − / ＋：hello 本地直接改，存 NVS（快捷设置页 0.4.93）', ref: '设计 §6' },
    local_asr: { text: 'up / down、left / right、turn left / right、front / back、turn on / off、音量五词：照旧，所有模式都有效，不经过模式', ref: '设计 §5.1' },
    asr_ok: { text: 'ok 预留给设置模式的确认；第一步没有作用', ref: '设计 §5.1' },
    asr_hello: { text: '唤醒词 hello 第一步不变；“hello 出声时说 hello = 立即停”在对话那一步再做', ref: '设计 §5.1' },
    rating: { text: 'DualKey KEY_1 / KEY_2 = 好 / 坏打分，所有模式都有效，不改模式', ref: '设计 §5.2、“现状”' },
    dualkey_rating_only: { text: 'DualKey 只打分：摇杆方向、单击、长按不再切模式、不再停说话，摇杆灯也不推了', ref: '设计“现状”；主人 2026-11-14 下午' },
    remote: { text: '官方遥控器第一步不变：四向是开关（左追踪、右认人、上听觉、下预留），中心开关屏', ref: '设计 §5.3' },
    timeout_none: { text: '设置超时只对设置模式有意义；现在不在设置', ref: '设计 §3.4' },
    unknown: { text: '这个输入在当前模式里没有定义', ref: '—' }
  };

  /* ---------- 结果码（计划“接口”一节；offline / local / no_effect 是机器人本地或本页的说法） ---------- */
  var codes = {
    ok: { label: '成功', toast: 'OK' },
    already: { label: '不变（已经是这样）', toast: 'ALREADY' },
    not_in_mode: { label: '不在那个模式，不变', toast: 'NOT IN {mode}' },
    only_talk: { label: '只在脱口秀', toast: 'ONLY IN TALK' },
    mouth_not_tts: { label: '嘴不在 TTS 档', toast: 'MOUTH NOT TTS' },
    not_implemented: { label: '第一步空壳', toast: 'NOT YET' },
    no_action: { label: '这个模式里这个输入本来就没有动作', toast: 'NO ACTION' },
    busy: { label: '已经在说一轮（模拟器不计时）', toast: 'BUSY' },
    too_soon: { label: '两轮开始不到 10 秒（模拟器不计时）', toast: 'TOO SOON' },
    stale: { label: '报告晚到超过 3 秒：只执行“关”的效果（模拟器不计时）', toast: '' },
    unreachable: { label: 'mind 收不到（/mouth 断开）', toast: 'OFFLINE' },
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
    var asr = prefix.indexOf('asr') === 0;
    return presets.map(function (p) {
      var h = p.kind === 'mind' ? { type: 'preset', n: p.n } : p.kind === 'select' ? { type: 'mode' } : { type: 'noop', rule: 'digit_empty', code: 'no_action' };
      return {
        id: prefix + p.n, label: (asr ? words[p.n] : String(p.n)) + (p.kind === 'none' ? '' : '（' + p.name.replace('进', '') + '）'),
        short: asr ? words[p.n] : groupLabel + p.n, via: via, needsServer: via === 'robot' && p.kind === 'mind',
        handler: h
      };
    });
  }

  var inputGroups = [
    {
      id: 'asr', name: 'ASR 口令（先说 Hi M Five / hello / hi A S R 唤醒）', items: [
        { id: 'asr.forward', label: 'forward', via: 'robot', needsServer: false, handler: { type: 'mode' } },
        { id: 'asr.backward', label: 'backward', via: 'robot', needsServer: false, handler: { type: 'mode' } },
        { id: 'asr.play', label: 'play', via: 'robot', needsServer: false, handler: { type: 'mode' } },
        { id: 'asr.pause', label: 'pause', via: 'robot', needsServer: false, handler: { type: 'mode' } },
        { id: 'asr.open', label: 'open', via: 'robot', needsServer: false, handler: { type: 'mode' }, was: '今天：开水平追踪（不再）' },
        { id: 'asr.close', label: 'close', via: 'robot', needsServer: false, handler: { type: 'mode' }, was: '今天：关水平追踪（不再）' },
        { id: 'asr.start', label: 'start', via: 'robot', needsServer: false, handler: { type: 'mode' }, was: '今天：开听觉（不再；第一步听觉没有口令）' },
        { id: 'asr.stop', label: 'stop', via: 'robot', needsServer: false, handler: { type: 'mode' }, was: '今天：关听觉（不再）' },
        { id: 'asr.next', label: 'next', via: 'robot', needsServer: false, handler: { type: 'mode' }, fallback: { code: 'only_talk', rule: 'only_talk' }, was: '今天：随机播 SD 台词（不再）' },
        { id: 'asr.previous', label: 'previous', via: 'robot', needsServer: false, handler: { type: 'mode' }, fallback: { code: 'only_talk', rule: 'only_talk' } },
        { id: 'asr.ok', label: 'ok', via: 'robot', needsServer: false, handler: { type: 'noop', rule: 'asr_ok' } },
        { id: 'asr.hello', label: 'hello（唤醒词）', via: 'robot', needsServer: false, deferred: '“说 hello = 立即停”以后（对话那一步）', handler: { type: 'noop', rule: 'asr_hello' } },
        { id: 'asr.local', label: 'up/down、转头、turn on/off、音量', short: '转头/追踪/音量词', via: 'robot', needsServer: false, handler: { type: 'noop', rule: 'local_asr' } }
      ]
    },
    { id: 'digits', name: 'ASR 数字词（新表：0 / 9 由 mind 执行；4–8 hello 进模式、只进不出；1–3 空）', items: digitInputs('asr.d', 'robot', '') },
    {
      id: 'touch', name: '机器人屏幕触摸（只在首页）', items: [
        { id: 'touch.tap', label: '单击', via: 'robot', needsServer: false, touch: 'tap', handler: { type: 'mode' }, note: '要等 0.3 秒确认不是双击' },
        { id: 'touch.double', label: '双击', via: 'robot', needsServer: false, touch: 'double', handler: { type: 'mode' } },
        { id: 'touch.up', label: '上滑', via: 'robot', needsServer: false, deferred: '上滑选模式页 0.4.93 才有', handler: { type: 'local', open: 'picker', rule: 'local_picker' } },
        { id: 'touch.down', label: '下滑', via: 'robot', needsServer: false, deferred: '下滑快捷设置页 0.4.93 才有', handler: { type: 'local', open: 'quick', rule: 'local_quick' } },
        { id: 'touch.left', label: '左滑', via: 'robot', needsServer: false, handler: { type: 'local', open: 'summary', rule: 'local_summary' } },
        { id: 'touch.right', label: '右滑', via: 'robot', needsServer: false, handler: { type: 'noop', rule: 'local_right' } }
      ]
    },
    {
      id: 'pick', name: '上滑模式选择页（0.4.93 待做；九格，hello 自己切，再点当前格 = 离开）', deferred: '0.4.93', items: [
        { id: 'pick.talk', label: '脱口秀', via: 'robot', needsServer: false, deferred: '0.4.93', handler: { type: 'mode' } },
        { id: 'pick.chat', label: '对话', via: 'robot', needsServer: false, deferred: '0.4.93', handler: { type: 'mode' } },
        { id: 'pick.settings', label: '设置', via: 'robot', needsServer: false, deferred: '0.4.93', handler: { type: 'mode' } },
        { id: 'pick.event', label: '事件驱动', via: 'robot', needsServer: false, deferred: '0.4.93', handler: { type: 'mode' } },
        { id: 'pick.subtitle_zh_en', label: '字幕 中→英', via: 'robot', needsServer: false, deferred: '0.4.93', handler: { type: 'mode' } },
        { id: 'pick.subtitle_en_zh', label: '字幕 英→中', via: 'robot', needsServer: false, deferred: '0.4.93', handler: { type: 'mode' } },
        { id: 'pick.crowd', label: '围观', via: 'robot', needsServer: false, deferred: '0.4.93', handler: { type: 'mode' } },
        { id: 'pick.companion', label: '陪伴', via: 'robot', needsServer: false, deferred: '0.4.93', handler: { type: 'mode' } },
        { id: 'pick.idle', label: 'IDLE', via: 'robot', needsServer: false, deferred: '0.4.93', handler: { type: 'mode' } }
      ]
    },
    {
      id: 'quick', name: '下滑快捷设置页（0.4.93 待做）', deferred: '0.4.93', items: [
        { id: 'quick.vol_down', label: '音量 −', via: 'robot', needsServer: false, deferred: '0.4.93', handler: { type: 'local', vol: -1, rule: 'local_volume' } },
        { id: 'quick.vol_up', label: '音量 ＋', via: 'robot', needsServer: false, deferred: '0.4.93', handler: { type: 'local', vol: 1, rule: 'local_volume' } },
        { id: 'quick.billing_on', label: '花费 全开（= 9）', via: 'robot', needsServer: true, deferred: '0.4.93', handler: { type: 'preset', n: 9 } },
        { id: 'quick.billing_off', label: '花费 全关（= 0）', via: 'robot', needsServer: true, deferred: '0.4.93', handler: { type: 'preset', n: 0 } }
      ].concat(digitInputs('quick.p', 'robot', '键 ').map(function (it) { it.deferred = '0.4.93'; return it; }))
    },
    {
      id: 'dk', name: 'DualKey（只打分，主人 2026-11-14）', items: [
        { id: 'dk.key1', label: 'KEY_1 好', via: 'dualkey', handler: { type: 'noop', rule: 'rating' } },
        { id: 'dk.key2', label: 'KEY_2 坏', via: 'dualkey', handler: { type: 'noop', rule: 'rating' } },
        { id: 'dk.up', label: '摇杆 上 / 下 / 左 / 右', short: 'DualKey 摇杆', via: 'dualkey', handler: { type: 'noop', rule: 'dualkey_rating_only', code: 'no_action' } },
        { id: 'dk.click', label: '摇杆 单击 / 双击', via: 'dualkey', handler: { type: 'noop', rule: 'dualkey_rating_only', code: 'no_action' } },
        { id: 'dk.long', label: '摇杆 长按 1 秒', via: 'dualkey', handler: { type: 'noop', rule: 'dualkey_rating_only', code: 'no_action' } }
      ]
    },
    {
      id: 'web', name: '网页模式条（发给 hello；当前模式高亮，再点 = 离开）', items: [
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
      id: 'webquick', name: '网页快捷面板（计费按钮 = 9 / 0；0、9 发 mind，4–8 发 hello，1–3 灰）', items: [
        { id: 'web.billing_on', label: '计费 开（= 9）', via: 'page', handler: { type: 'preset', n: 9 } },
        { id: 'web.billing_off', label: '计费 关（= 0）', via: 'page', handler: { type: 'preset', n: 0 } }
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
    'sys.boot': { goto: 'talk', force: ['stop_speech', 'talk_enter'], rule: 'any_boot', alsoRule: 'boot_paid', by: 'boot' },
    'sys.mind_restart': { effects: ['paid_senses_on', 'mouth_tts', 'talk_paid_on'], rule: 'mind_restart' }
  };

  /* 模式一变就做的事（设计 §3.7，计划 A1“只要 mode 变了 effects 以 stop_speech 开头”） */
  var onModeChange = ['stop_speech'];

  /* 模拟器初始状态：mind 启动执行一遍 9（花费全开）；hello 开机进脱口秀 → 提前写、连续说开 */
  var initialState = {
    mode: 'talk', dir: null, host: null, return_to: null, seq: 1, by: 'boot',
    senses: { tingwu: true, eye: true, scene: true, notes: true },
    talk: { prefetch: true, auto: true },
    speaking: false, rounds: 0,
    robot: { hearing: false, camera: false, m12: false, volume: 3, badge: false },
    env: { online: true, mouthTts: true }
  };

  /* 花钱面板：每一项看哪个状态、开着要什么条件 */
  var spendItems = [
    { group: '付费感官（单个开关随时能开能关）', label: '听悟', path: 'senses.tingwu', paid: true },
    { group: '付费感官（单个开关随时能开能关）', label: '识别（视觉 eye）', path: 'senses.eye', paid: true },
    { group: '付费感官（单个开关随时能开能关）', label: '情景', path: 'senses.scene', paid: true },
    { group: '付费感官（单个开关随时能开能关）', label: '纪要', path: 'senses.notes', paid: true },
    { group: '脱口秀的花钱效果（只在脱口秀里执行）', label: '说一轮（按了才花）', path: 'speaking', paid: true, needs: ['mode:talk', 'mouthTts'], onLabel: '正在说', offLabel: '没在说' },
    { group: '脱口秀的花钱效果（只在脱口秀里执行）', label: '提前写', path: 'talk.prefetch', paid: true, needs: ['mode:talk'] },
    { group: '脱口秀的花钱效果（只在脱口秀里执行）', label: '连续说', path: 'talk.auto', paid: true, needs: ['mode:talk'] },
    { group: '机器人本地（不花钱）', label: '本体听觉（本地档）', path: 'robot.hearing', paid: false },
    { group: '机器人本地（不花钱）', label: '相机 / M12', path: 'robot.camera', paid: false }
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
      { from: 'any', to: 'settings', label: 'open\n（临时层）', kind: 'solid', bend: 40 },
      { from: 'settings', to: 'any', label: 'close / 5 分钟\n→ 回原来的模式', kind: 'return', bend: 40 },
      { from: 'event', to: 'guest', label: '按事件进入（以后）', kind: 'deferred', bend: -26 },
      { from: 'guest', to: 'event', label: '客人离开 → 回等待', kind: 'deferred', bend: -26 },
      { self: 'subtitle', label: 'seven ↔ eight 换方向' }
    ],
    legend: [
      '规则在 hello 上（固件 mode_rules.h），mind 只记录 hello 报上来的模式并执行效果',
      '每个模式之间也能直接切换（口令、数字 4–8、网页），不经过 IDLE —— 图里只画了进出 IDLE 的边',
      '离开词 / 网页再点当前模式 → IDLE；数字 4–8 只进不出；four 从任何模式进 IDLE',
      '设置是临时层，关掉回到原来的模式；事件驱动是外层，以后会自己进出客人模式；DualKey 只打分，不再切模式'
    ]
  };

  /* ---------- 快捷设置页 / 摘要页 / 模式选择页的布局（320×240 屏幕） ---------- */
  var screens = {
    picker: {
      title: '上滑：模式选择页（0.4.93 待做）',
      cells: ['pick.talk', 'pick.chat', 'pick.settings', 'pick.event', 'pick.subtitle_zh_en', 'pick.subtitle_en_zh', 'pick.crowd', 'pick.companion', 'pick.idle'],
      notes: ['3×3 九格，当前模式高亮；再点当前格 = 离开（→ IDLE），和网页一样', '点一格 hello 自己切（规则在 hello 上），然后报给 mind', '和服务器断开时照样能切', '点空白或下滑关闭', '用时才建、关了就删（省内部 RAM）']
    },
    quick: {
      title: '下滑：快捷设置页（0.4.93 待做）',
      notes: ['音量 − / ＋：本地直接改、存 NVS，离线也能用', '花费 全开 / 全关：= 9 / 0，发给 mind（计费只是两个整体动作，不存状态）', '数字键：0 / 9 发 mind；4–8 hello 进模式；1–3 灰', '上滑或点空白关闭', '补充原文只有“音量 计费开关”，0–9 键是【我定】加的']
    },
    summary: {
      title: '左滑：状态摘要页（已上线 0.4.92）',
      lines: ['collector-0.4.92', 'WiFi  (SSID)', 'IP    10.x.x.x', 'HEAR  {hearing}', 'CAM   {camera}'],
      notes: ['今天的摘要页（以前首页轻点进入，现在左滑）', '左滑进入已上线（0.4.92）；单击不再进入', '加一行：MODE <label> #<seq>（计费不再有状态，原设计的 BILLING 行不再适用）', '摘要页里的左滑 / 长按照旧（去日志、配对）']
    }
  };

  /* ---------- 上线情况（设计“现状”、两个计划的状态说明、docs/backlog.md） ---------- */
  var tasks = [
    { group: '已上线（0.4.92 + mind / ear，2026-11-14 17:13；计费 18:22 简化）', items: [
      { id: '✓', title: 'ear 自己终结 /mouth', note: '13:25 先单独上线并验证（A0 + D0）' },
      { id: '✓', title: '八个模式的切换规则在 hello 上', note: '固件 mode_rules.h，和 mind 的 modes.py 共用约 2100 条测试向量（1b F1）' },
      { id: '✓', title: '口令改绑', note: 'forward / backward、play / pause、open / close、start / stop、next / previous、数字新表' },
      { id: '✓', title: '首页触摸', note: '单击 / 双击按模式；左滑开摘要（单击不再开）' },
      { id: '✓', title: '眼睛和角标', note: '脱口秀一律笑面男，其他模式换眼睛；右上角 ASCII 角标（TALK、IDLE 等）' },
      { id: '✓', title: '网页模式条', note: '页面 → 观察台 → hello（/v1/control target:mode）；状态读 hello 遥测，读不到读 mind 的 /mode（1b W1）' },
      { id: '✓', title: 'mind 只记录 + 执行效果', note: 'POST /mode/report，同一个 seq 只执行一次；DualKey 只打分（1b S1、S2）' },
      { id: '✓', title: '计费两个整体动作', note: 'mind 启动 = 全开；网页按钮 / 口令 = 9 / 0；不拦任何东西' }
    ] },
    { group: '0.4.93 待做', items: [
      { id: '…', title: '上滑选模式页', note: '3×3 九格，再点当前格 = 离开', pending: true },
      { id: '…', title: '下滑快捷设置页', note: '音量、花费全开 / 全关、数字键', pending: true },
      { id: '…', title: '字幕 / 设置 / IDLE 的占位页', note: '字幕写方向、参数页只读、IDLE 黑底小字 hello（事件驱动等待也用它）', pending: true },
      { id: '…', title: 'IDLE 的徽章页', note: '大字 hello + 固件版本；IDLE 里单击切换', pending: true },
      { id: '…', title: '切换时的闪屏', note: '屏幕中间放大显示 label 约 1 秒', pending: true }
    ] },
    { group: '已知小问题（docs/backlog.md，先不做，做之前问主人）', items: [
      { id: '!', title: '脱口秀里随手单击脸就会说一轮', note: '按设计如此（单击要等 300 ms）；touch.double 为 none 时可以考虑不等 300 ms' },
      { id: '!', title: '角标挡住 Wi-Fi 页和键盘页的提示文字', note: '“返回 / 重新扫描”被挡，按钮照样能点' },
      { id: '!', title: 'mind 拒绝了屏幕上还是提示 SAYING / PREFETCH ON', note: '嘴不是 TTS、间隔太短等' },
      { id: '!', title: '离开脱口秀后台词面板的勾不跟着变', note: '看起来勾着、实际是关的；收到 modechange 时重读 /say/stats' },
      { id: '!', title: '口令任务的栈要再量一次', note: '4 KB 栈比以前深了约 0.4 KB' },
      { id: '?', title: '听觉没有口令', note: 'start / stop 已经给了事件驱动，主人还没想好' }
    ] },
    { group: '计划任务的去向', items: [
      { id: 'A0 D0', title: '做完、仍在用', note: 'ear 终结 /mouth' },
      { id: 'A1 C1–C3', title: '做完、被 1b 复用', note: 'modes.py（规则又移植到固件）、消息格式、口令改绑、手势分类' },
      { id: 'A2–A6 B1 D1', title: '做了，后来被 1b 改掉或撤掉', note: 'mind 存模式 / 推快照 → 只记录；计费闸门 → 两个整体动作；DualKey 摇杆灯撤掉；模式条改发给 hello' },
      { id: 'B2', title: '保留', note: '台词面板跟着模式' },
      { id: 'C4–C6 D2', title: '由 1b 取代', note: '1b 的 F2（接线 + 外观）、B（构建与统一审查）和 17:13 刷机' }
    ] }
  ];

  var deferredList = [
    '各模式的内容：对话（正在做），然后字幕、围观、陪伴、事件驱动',
    '听觉的口令（主人还没想好）',
    '中文小字库（和字幕一起）：角标改成一个中文字，缺字退回 label',
    '进入模式自动打开它要的付费感官（主人：先手动）',
    'ASR “hello” 打断；换 ASR 词表',
    'IDLE 的音乐和视频',
    '官方遥控器第二层绑定模式；遥控器情绪层'
  ];

  /* ---------- 第 12 节 ---------- */
  var questions = [
    { n: 1, q: '已在脱口秀时说 forward', proposed: '说一轮。另一种：什么都不做',
      decided: true, answer: '按默认：说一轮。', ownerWords: '按默认', impact: '不在脱口秀时 forward 只进入、不说话' },
    { n: 2, q: '不在脱口秀时按 9', proposed: '不切模式，提前写、连续说跳过',
      decided: true, answer: '按默认定过；随新数字表和计费简化已不再相关：9 只在 mind 上执行，提前写、连续说只在脱口秀里开，下次进脱口秀会自动打开。', ownerWords: '按默认', impact: '—' }
  ];
  var openNote = '唯一还没想好的是“听觉要不要一个口令”：start / stop 已经给了事件驱动，听觉先手动。';

  var ownerAlsoConfirmed = [
    '1. 关掉一个模式 → IDLE；直接打开另一个模式的指令 → 直接进那个模式',
    '2. 事件驱动是外面一层，由它进别的模式',
    '3. 进 IDLE 或开机时不动付费的听觉、视觉，先手动',
    '4. 预设 1 和 9 按字面〔1 已被新数字表取代〕',
    '5. 脱口秀的眼睛一律是笑面男，开着视觉也是；推翻 11-10 的规则',
    '6. 首页单击不再打开摘要页，改成左滑',
    '7. start / stop 不再开关听觉；第一步里听觉暂时没有口令',
    '8. 新的口令和事件分配直接取代旧的：forward = 进入脱口秀，backward = 离开（→ IDLE）',
    '9. ASR 和 DualKey 互不相关〔DualKey 后来改成只打分〕',
    '10. 听觉要不要口令：还没想好',
    '11. 事件驱动没有自己的眼睛：眼睛就是它所选模式的眼睛',
    '12. 手动选了一个模式，事件驱动停',
    '13. 计费总闸和预设放进第一步〔后来改成两个整体动作〕',
    '14. 每次进入脱口秀（开机、从别的模式切来、从设置回来）都打开提前写和连续说，家人声音不选',
    '15. 坏的计费存储值按关〔已被第 17 条取消〕',
    '下午：模式以 hello 为准，服务器只按模式开关自己的功能；DualKey 只打分；数字口令换新表（0 花费全关、9 花费全开、4–8 进 IDLE / 围观 / 陪伴 / 中音英字 / 英音中字且只进不出、1–3 空）',
    '16. 计费是两个整体动作：mind 启动 = 全开（同 9）；网页一个开关，开 = 9、关 = 0；口令 9 / 0 一样',
    '17. 取消：计费关着时拒绝单个付费开关、说话和进脱口秀上的计费检查、存计费状态、坏值当关、BILLING OFF 提示。保留第 14 条，以及花钱的效果只在脱口秀里执行'
  ];
  var ownerQuotes = [
    '“其实我理解当中服务端对模式应该没有什么工作要做……它的主要发起都应该是在客户端发起的。”“改成以 hello 为准，对。DualKey 还连不连服务器——连，只打分。”',
    '“计费开关是这样，启动默认全开，我就使用网页单点开关，或asr9 asr0来整体开关。其他奇怪的设定都取消。”'
  ];

  /* ---------- 与第一版 / 主人补充的差异 ---------- */
  var differences = [
    { topic: '谁说了算', source: '设计 v2 原文：mind 是唯一能改模式的地方，推快照给 hello', design: '以 hello 为准：hello 存模式、跑规则，mind 只记录 mode_report 并执行效果；断开时 hello 照样能切', tag: '主人已定', ref: '“现状”' },
    { topic: 'joy stick / DualKey', source: '补充：“joy stick up / down / left / right”进字幕、围观、陪伴', design: 'DualKey 只打分；字幕、围观、陪伴改由数字 5–8 和网页进入', tag: '主人已定', ref: '“现状”' },
    { topic: '七个模式 → 八个', source: '第一版七个', design: '加了 IDLE', tag: '补充', ref: '设计 §11.1' },
    { topic: '离开模式去哪', source: '第一版：离开回脱口秀', design: '关掉回 IDLE；直接打开另一个模式就直接去', tag: '主人已定', ref: '设计 §11.2' },
    { topic: '事件驱动的地位', source: '补充：单击“取决于进入了什么模式”', design: '外面一层；手动选模式（含 open）= 事件驱动停；托管时 stop = 一起停 → IDLE', tag: '主人已定 + 我定', ref: '设计 §11.3、§3.8' },
    { topic: 'forward / backward', source: '第一版：脱口秀里说一句 / 停', design: '进入 / 离开脱口秀；已在脱口秀时 forward = 说一轮', tag: '主人已定', ref: '设计 §5.1、§12' },
    { topic: '进脱口秀', source: 'v2：进入任何模式都不花钱', design: '每次进脱口秀（含开机）都打开提前写和连续说，家人声音不选；别的模式照旧不花钱', tag: '主人已定', ref: '第 14 条' },
    { topic: '眼睛', source: '11-10 规则：视觉关笑面男、视觉开机甲眼', design: '每个模式固定眼睛；脱口秀一律笑面男', tag: '主人已定', ref: '设计 §11.5' },
    { topic: '事件驱动的眼睛', source: '补充：“自己决定”', design: '没有自己的眼睛；等待时 IDLE 屏 + 角标 AUTO（IDLE 屏 0.4.93）', tag: '主人已定 + 我定', ref: '第 11 条' },
    { topic: '首页触摸', source: '今天：首页轻点去摘要页', design: '单击、双击按模式；左滑 = 状态摘要（已上线）；上滑模式页、下滑快捷设置页 0.4.93', tag: '主人已定', ref: '设计 §11.7、“现状”' },
    { topic: '数字口令', source: '补充：0 花费全关、1 数据收集、2 听悟反转、3 提前写反转、4 IDLE、5–8 空、9 花费全开', design: '0 / 9 不变（mind 执行）；4–8 进 IDLE / 围观 / 陪伴 / 中音英字 / 英音中字，只进不出；1–3 空', tag: '主人已定', ref: '“现状”' },
    { topic: '计费开关', source: '补充：“计费开关”；v2：总闸（关着时不许开、拦说话）', design: '两个整体动作：mind 启动 = 全开；网页按钮 / 口令 = 9 / 0；不拦任何东西、不存状态', tag: '主人已定', ref: '第 16–17 条' },
    { topic: '下滑快捷设置页', source: '补充：“音量 计费开关”', design: '音量、花费全开 / 全关、数字键（0.4.93）', tag: '我定', ref: '设计 §6' },
    { topic: 'start / stop、open / close、next', source: '今天：开关听觉、开关追踪、随机播 SD 台词', design: '进出事件驱动、进出设置、脱口秀里开提前写；听觉没有口令', tag: '主人已定', ref: '设计 §11.14' },
    { topic: '角标', source: '第一版：颜色 + ASCII 字母', design: '颜色 + ASCII 短名（TALK、CHAT、SET、AUTO、SUB、MEET、PAL、IDLE）；中文角标等中文字库', tag: '已上线', ref: '设计 §6' },
    { topic: 'IDLE 的内容', source: '补充：“开放音乐或者视频（？放得动）或展示badge”', design: 'IDLE 页、徽章页 0.4.93；音乐、视频以后', tag: '推迟', ref: '设计 §8、“现状”' }
  ];

  /* ---------- 文档里仍没写明的地方 ---------- */
  var ambiguities = [
    { title: '断线时进的脱口秀', text: '断开时 hello 照样切模式，连上后补报；报告晚到超过 3 秒 mind 只执行“关”的效果。那么断线期间进入脱口秀，重连后模式是脱口秀、提前写和连续说却还关着，要等下一次进入才开（和第 14 条“每次进入都开”不一致）。' },
    { title: '9 还开不开本地听觉和视觉', text: '旧表里 9 有“机器人那一半”（本地听觉、视觉上传）；新表只说“花费全开（mind 执行）”。页面按“只开服务器上花钱的服务 + 嘴切 TTS”处理，本地感官不动。' },
    { title: '摘要页的计费行', text: '设计 §6 原定摘要页加“计费总闸”一行，但计费已经不存状态，这一行显示什么没写。' },
    { title: '事件驱动等待时的单击', text: '等待时显示 IDLE 屏幕（0.4.93），但单击是 NO TAP，IDLE 里单击翻徽章页；屏幕一样、单击不一样，只能靠角标 AUTO / IDLE 区分。' },
    { title: '首页的范围', text: '手势只在首页解释（§5.4），但设置、字幕、IDLE 的占位页（0.4.93）算不算首页没写；页面按“算”处理，否则 IDLE 单击翻徽章页不成立。' },
    { title: '模拟器不计时', text: '时间规则（busy / too_soon、晚到 3 秒、0.3 秒双击、5 分钟设置超时）模拟器都不计时，只写在下面的时间规则表里。' }
  ];

  /* ---------- 时间规则（设计 §5.6，按“现状”更新） ---------- */
  var timing = [
    ['mode_report 从 hello 到 mind', '晚到超过 3 秒：只执行“关”的效果（停说、关提前写、关连续说）', '—'],
    ['同一个报告重发', '按 seq 只执行一次', '第一次的结果'],
    ['说一轮', '同一时间只有一轮；两轮开始至少隔 10 秒', 'busy、too_soon'],
    ['首页双击', '第一下松手后 300 ms 内第二下；单击晚约 0.3 秒生效', '—'],
    ['首页点 / 滑', '点：按住 30–700 ms、移动 ≤ 18 px；滑：主方向 ≥ 55 px、按住 30–1000 ms；松手后 180 ms 内的新按下忽略', '—'],
    ['设置模式无动作', '5 分钟（hello 计时）', '回原来的模式'],
    ['mode_result 屏幕提示', '2 秒', '—'],
    ['切换闪屏', '1 秒（0.4.93）', '—']
  ];

  /* 模式一览顶部的四句话 */
  var layers = [
    { title: '以 hello 为准（已上线）', text: '当前模式存在 hello 内存里，开机默认脱口秀；口令、首页触摸、网页模式条都由 hello 按 mode_rules 自己切，再用 mode_report 报给 mind。mind 只记录，并按报告里的效果开关说一轮、提前写、连续说。和服务器断开时 hello 照样能切。' },
    { title: '普通模式', text: '脱口秀（默认）、对话、字幕（两个方向）、围观、陪伴。同一时刻只有一个模式（严格互斥）。' },
    { title: '设置 = 临时层', text: 'open 进入时记下原来的模式；close 或 5 分钟没动作（hello 计时）就回去；在设置里选别的模式就直接去那个模式。' },
    { title: '事件驱动 = 外面一层', text: 'start 进入“等待”（角标 AUTO）；以后它自己进入客人模式，用那个模式的眼睛，客人离开回“等待”；stop 回 IDLE（托管时客人一起停）；手动选模式（含 open）= 事件驱动停。' },
    { title: 'IDLE', text: '关掉任何模式都回这里；直接打开另一个模式就直接去。four 也进 IDLE。' },
    { title: '计费 = 两个整体动作', text: 'mind 启动 = 花费全开（= 9）；网页计费按钮开 = 9、关 = 0；口令 nine / zero 一样。不拦任何东西，单个付费开关随时能开能关，不存计费状态。' }
  ];

  window.DESIGN = {
    layers: layers,
    meta: {
      title: 'hello 模式查看器',
      version: '第一步已上线（2026-11-14 17:13，collector-0.4.92 + mind / ear；18:22 计费简化）。以 hello 为准：规则在 hello 上，mind 只记录并执行效果',
      sources: [
        'docs/superpowers/specs/2026-11-14-hello-modes-design-v2.md',
        'docs/superpowers/plans/2026-11-14-hello-modes-step1b-robot-owned.md（上线的版本）',
        'docs/backlog.md',
        '主人 2026-11-14 补充（设计 §1 原文）',
        '主人 2026-11-14 对四个问题的回答'
      ],
      note: '依据设计第二版文首“现状（2026-11-14 晚，已上线）”、第 1.2 节第 1–17 条和 docs/backlog.md。'
    },
    eyes: eyes, pools: pools, modes: modes, modeOrder: modeOrder,
    effects: effects, presets: presets, rules: rules, codes: codes,
    inputGroups: inputGroups, anyMode: anyMode, onModeChange: onModeChange,
    initialState: initialState, spendItems: spendItems, diagram: diagram, screens: screens,
    tasks: tasks, deferredList: deferredList, questions: questions, openNote: openNote, ownerAlsoConfirmed: ownerAlsoConfirmed, ownerQuotes: ownerQuotes, timing: timing,
    differences: differences, ambiguities: ambiguities,
    stepBadges: { live: '已上线 0.4.92', impl: '已上线 0.4.92', shell: '已上线 0.4.92 · 空壳', deferred: '推迟', pending: '0.4.93 待做' }
  };
})();
