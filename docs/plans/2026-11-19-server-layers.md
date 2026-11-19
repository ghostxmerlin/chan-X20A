# 服务器分层重构（2026-11-19 夜里长跑）

主人 2026-11-19：「夜里我们可能会对服务器侧进行分层重构。晚上要下班之前，我们要把分层和结构做好。夜里我想进行一个 longrun，把层次整理好。」
给主人看的版本：观察台 http://127.0.0.1:8787/layers.html 。本文件是夜里执行用的完整步骤。

## 1. 现状

### 进程（服务器上 7 个，Mac 上 1 个）
| 进程 | 代码 | 端口 | 做什么 |
|---|---|---|---|
| soul-spike（耳朵） | server/ear，10.5k 行 | ws 52732，ask 52743 | 机器人 4 条连线（听 / 看 / 嘴 / DualKey），本地 ASR + 声纹，Docking（千问实时）的全部对话逻辑，视觉爆发、即时画面，在场判断，身体感觉，听悟，日志 |
| soul-mind | server/mind，15.6k 行 | http 52740 | 工具框架和大部分工具，开关 / 模式 / 设置，每分钟情景，暂停和花费上限，日常提醒，深入研究，按意思找同步，资料问答，调试记录，聊天，脱口秀 |
| soul-eye / soul-notes / soul-memory | 同一份 mind 代码 | — | 每分钟画面、谈话纪要、记忆整理 |
| soul-tts | 另一份代码 | 52750 | 说话 |
| soul-kws | server/kws | 52760 | 关键词耳朵（Dynames） |
| Mac 观察台 | web/monitor（node） | 8787 | 网页、串口桥、代理 mind |

### 谁调谁
- 机器人 ⇄ 耳朵：4 条 WebSocket。
- 耳朵 → mind（http 52740）：情景上下文、工具列表、工具调用、模式报告、开关。
- mind → 耳朵（ask 52743）：让 hello 说话、屏幕字、机器人动作、调试提醒。
- mind → soul-tts；耳朵 → DashScope（千问实时、听悟、搜索）；mind → aihubmix（luna / terra / sol）、DashScope 向量。
- 所有进程 ⇄ Mongo hello：既是数据库，也被当成进程之间的「公告板」（hello.switches 里的 rt_modes_status、presence_status、pause、*_status 文档）。

### 今天踩到的坑（都是结构问题的症状）
1. realtime_link.py 一个文件 3640 行、一个类管十几件事（会话协议、回答闸门、安静 / 调试 / 角色、技能和工具、招呼和事件、触摸、视觉等待、垫词、说明拼装、状态持久化）。今天每个改动都落在这里：触摸、工具运行算说话、被晃说一句、模式时长、调试模式，互相牵连，测试只能在服务器上跑。
2. 跨进程的数据没有约定：调试记录读 speech.self 当成布尔值，其实是 {sounding, moving}，第一次真调试 42 句全被滤掉。
3. 同一个工具要在三处登记：config/tools/registry.json、mind 的实现表、耳朵 ask_api 的 NAMES / mind 的 EAR_TOOLS 列表；漏一处就悄悄不能用。
4. 字库、屏幕字、服务器的词表三处各写一份，「调试中」写了半天屏幕上从来没显示。
5. mind/api.py（1160 行）同时是路由、工具装配、mind 状态；tool_impls.py（811 行）十几个领域的工具挤在一个函数里。
6. 「模式」一词两用：机器人自己的模式（Docking / IDLE / 字幕…，机器人和服务器各有一份规则、靠测试向量对齐）和耳朵里的「安静 / 少插话 / 角色 / 调试」，名字一样、机制不同。

## 2. 目标分层

从上到下五层，**只能往下调**；同一层的两个服务之间只走第 1 层的约定（http / ws / 数据库里登记过的文档）。

| 层 | 名字 | 放什么 | 例子 |
|---|---|---|---|
| 1 | 接口层 | 只做「线上格式 ⇄ 函数调用」的翻译，不放规则 | 机器人 4 条连线、mind 的 http 路由、耳朵的 ask 接口、观察台代理 |
| 2 | 编排层 | 一件事从头到尾怎么串起来，按功能分模块 | Docking 会话（hub）、技能和工具执行、说话方式模式（安静 / 少插话 / 角色 / 调试）、日常提醒、深入研究、调试记录、脱口秀 |
| 3 | 规则层 | 纯函数、不碰网络和数据库，电脑上就能测 | 回答闸门、时长解析、身体感觉判断、说明拼装、记忆召回打分、资料切块、模式规则表 |
| 4 | 适配层 | 跟外面打交道：数据库、模型、语音、机器人命令 | Mongo 仓库（每组集合一个模块，写明字段）、千问实时客户端、aihubmix / DashScope 客户端、TTS 客户端 |
| 5 | 基础层 | 大家都用的 | 配置读取、运行日志（hello_log）、价格和钱、时钟、http 小工具 |

另外三条约定（第 1 层的一部分）：
- **数据约定**：docs/contracts/collections.md 列出每个集合 / 公告板文档：谁写、谁读、字段和含义（speech.self 这种）。读写都经过第 4 层的仓库模块，不再到处写 db["x"]。
- **工具约定**：registry.json 是唯一的工具清单；耳朵 ask_api 的名字表、mind 的 EAR_TOOLS 都从它生成或用测试对齐；每个 live 工具在它 runs_on 的那一边必须有实现（测试）。
- **屏幕字约定**：服务器能发的屏幕字都在一个表里，测试检查都在机器人字库里（今天已加了一条测试，夜里把词表收拢到一处）。

## 3. 夜里的范围

**做（第一阶段：不改行为，只整理结构）**
1. 基线：先把所有测试跑一遍记下数字，再给关键输出拍「快照」（见 4.0）。
2. 约定和守护：集合约定文档、层次检查脚本（谁 import 谁违反了层次就失败）、工具三处对齐的测试。
3. 拆大文件：
   - ear/realtime_link.py → ear/realtime/ 一个包：会话协议、回答闸门、说话方式模式、事件队列（招呼 / 被晃 / 调试提醒 / 触摸）、技能、工具执行、说明拼装，各一个模块；RealtimeHub 由这些部分组合而成；realtime_link.py 留成「门面」，原来的名字照样能 import（测试和 ear_server 不用改）。
   - ear/ear_server.py → 装配（建对象、接线）和连线处理分开。
   - mind/api.py → 路由按功能分文件；工具装配挪到一处。
   - mind/tool_impls.py → 按领域分文件（回忆、情景、开关和钱、日常、耳朵转发、实时数据…），导出同一张表。
4. 公告板文档：hello.switches 里被几个进程共用的文档（rt_modes_status、presence_status、pause、realtime_status…）各给一个读写函数，放到 common/，调试记录、暂停这些地方改用它。
5. 清掉确认没用的代码：旧的「单击打招呼」路径、机器人那边已经不用的「看一眼」暂留逻辑在服务器侧的对应部分（只删服务器侧、测试证明没人用的）。

**不做（以后再说，要你点头）**
- 不改任何对外的协议（机器人线上格式、http 接口、数据库字段）；不改固件；不改模型、提示词和门槛。
- 不把 mind 拆成子包（部署脚本是按平铺文件拷的，要改部署，放到第二阶段）。
- 不改 systemd 的临时单元（服务器重启会丢，这是运维问题，单独做）。
- 不重写任何功能。

## 4. 步骤和验收

每一步：一个小提交；跑完全部测试，数字和基线一样（新加的测试另算）；快照一致；层次检查通过。任何一步不过，就回到上一步的提交，记下原因，跳到下一项。

### 4.0 基线（先做，不改代码）
- 分支 refactor/layers-2026-11-19，从 codex/usb-monitor 当前提交开出。
- 跑并记下：mind 测试（本机 python3 -m unittest）、耳朵测试（服务器 /tmp 拷一份用 soul-spike 的 venv 跑：test_realtime_link、test_intent、test_body_sense、test_ask_api、test_vision_link、test_hello_db、test_ear_log、test_vision_burst、test_ear_presence、test_ear_protocol、test_signal_link）、固件主机测试（c++ 编 tests/*_test.cpp）、观察台测试（node --test）。
- 快照（tests/snapshots/，新加）：
  - instructions() 在 8 组固定输入下的完整文本（安静 / 调试 / 角色 / 身体感觉 / 技能打开 / 说话人 …）；
  - 会话打开时发出的 session.update（工具列表、技能目录）；
  - mind GET /tools 的工具列表和参数；mind 的 mode vectors（已有）；
  - 回答闸门对 20 个固定回合的判定（play / muted_* / rescue）。
  快照测试在每一步之后都要一字不差。

### 4.1 约定和守护（只加文件）
- docs/contracts/collections.md：从代码里扫出每个集合的读写方（今天的扫描结果做底），人工补字段含义；标出公告板文档。
- 已有（2026-11-19 下班前做好）：tools/arch/layers.json（每个模块 → 层，89 个全登记；现状的 11 处往上调列在 allowed 里）和 tools/arch/code_map.py（扫代码写 docs/arch/code-map.json，观察台「代码结构」页 /code.html 读它；--check 遇到新的往上调或没登记的模块就失败）。夜里：把 --check 加进测试；每拆一步登记新模块、重扫、提交 code-map.json；清掉一处往上调就从 allowed 里删掉一项（目标：第一阶段结束时 allowed 只剩说得出理由的）。
- tests/test_tool_contract.py：registry 里每个 live 工具，runs_on=ear 的在 ask_api 里有处理函数且在 EAR_TOOLS 里，runs_on=mind 的在 mind_tools / live_tools / daily_tools / docs / debug 的表里有实现；反过来实现了却没登记的也报。

### 4.2 拆 realtime_link.py（最大的一步，分 7 个小提交）
做法：把 RealtimeHub 的方法按主题挪进 ear/realtime/ 下的模块，每个模块一个 mixin 类（只放方法，不放新状态）；RealtimeHub(SessionMixin, GateMixin, MannerMixin, EventsMixin, SkillsMixin, ToolsMixin, InstructionsMixin)；常量和纯函数挪到对应模块，realtime_link.py 用 `from .realtime.x import *` 再导出。顺序（每个一提交）：
1. 纯函数和常量（norm_text、is_hush、addressed、instructions()、session_config、look_lines…）→ realtime/text.py、realtime/instructions.py
2. 说话方式模式（set_quiet / set_role / set_debug / debug_alert / mode_span 用法 / _persist_modes / restore_modes / _manner_line / _show_mode_word / robot_back）→ realtime/manner.py
3. 事件队列（招呼、tap 旧路径、body_event、debug_alert 投递、_deliver_greet、_beep）→ realtime/events.py
4. 触摸和打断（touched、touch_quiet、called、ordered、_interrupt）→ realtime/interrupt.py
5. 技能（目录、预装、打开、收起、工具门）→ realtime/skills.py
6. 工具执行（_run_tool、垫词、工具忙、取消）→ realtime/tools.py
7. 会话协议和回答闸门（_open、step、事件处理、reply gate、rescue、look / search 等待）→ realtime/session.py、realtime/gate.py
每个提交后：耳朵全部测试 + 快照 + 层次检查。

### 4.3 拆 ear_server.py
- ear/wiring.py：main() 里建对象和接线（hub、burst、live eye、body、presence、kws、ask、log）；ear_server.py 只剩参数解析、启动、连线分发。
- 验收：耳朵测试全过；在服务器上用 --dry-run（新加：只装配不监听，打印装配清单）对比装配清单前后一样。

### 4.4 mind：api.py、tool_impls.py
- mind/api.py → api.py（服务器和路由表）+ api_*.py（开关和模式、设置、日志、工具、说话、调试 / 资料…）；路由表前后一样（测试：列出所有路由）。
- tool_impls.py → tools_recall.py、tools_scene.py、tools_money.py、tools_ear.py、tools_live.py…；mind_tools() 拼出同一张表（测试：表的键和每个函数的来源模块）。
- 部署脚本按 *.py 平铺拷贝，新文件自动带上（不用改部署）。

### 4.5 公告板文档
- common/state_docs.py：每个公告板文档一对 read_x / write_x（字段写在函数说明里）；耳朵、mind 里直接读写这些文档的地方改用它。耳朵和 mind 的部署脚本都要把 common/state_docs.py 拷过去（和 hello_log.py 一样）。
- 验收：测试全过；数据库里的文档形状不变（快照：写出的字典）。

### 4.6 清理
- 只删「测试证明没人用」的：先用工具（vulture 或 ast 扫引用）列候选，逐个确认，每删一组跑一次全部测试。
- 屏幕字表收拢：realtime 的 TOOL_WORD / 模式字 / 在听 放到一个表，测试对照固件字库（已有测试改成读这个表）。

### 4.7 收尾
- 写 docs/longrun-2026-11-19.md：做了什么、每步测试数字、快照结果、跳过了什么和为什么、白名单还剩哪些。
- 更新看板第 6 件。
- 部署：见第 5 节第 3 条。

## 5. 安全规则（夜里）
1. 只在分支上提交；不推 codex/usb-monitor 以外的东西，不 force push。
2. 不刷机、不碰机器人、不改开关和设置；不动数据库里的数据（只读做快照）。
3. 部署：按主人选的方式（见第 6 节问题 1）。
4. 花钱：夜里不跑要花钱的验收（离线回放、技能验收评测）除非主人同意；同意的话上限 $1。
5. 一步不过：回退到上一步、记下来、跳过；连续三步不过就停下，写清楚卡在哪。
6. 不改行为：发现 bug 只记录，不顺手修（除非是重构本身引入的）。

## 6. 要主人定的
1. 部署：夜里全部通过后自动部署（部署后跑冒烟检查，不对就自动回退到旧代码），还是只留在分支上、早上你看过我再部署？
2. 范围：第 3 节的「做 / 不做」可以吗？
3. 花钱的验收：要不要用录音离线回放（真连千问，几美分）验证 Docking 行为不变？
