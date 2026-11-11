# 会话交接记录

## 2026-11-11 傍晚：对话纪要上线、环境说明、hello 自声屏蔽重做、eye 改用工具调用；两个待查问题（Claude）

**已上线并验收**（代码到 `4f77a61` 之后的提交，均已部署）：
- **环境说明**：设置第 4 版的 `environment` 字段，共 348 字，包括办公室布局、公司做生物识别芯片和指纹模组、10 多个同事、工作内容和常用词、作息。mind、eye、纪要三处的提示词都会带上它；在情景页的设置里可以修改。
- **hello 自声屏蔽**：
  - ear 在 hello 出声时也把静音交给听悟（以前听悟拿的是原始音频）。
  - mind 判断"hello 自己"改用台词文字比对（`selfvoice`，80 条台词，存在 `.private/clip_texts.json`，由 deploy 拷到服务器），不再看时间是否重叠。重叠时需 3 个字以上对上才算 hello，不重叠的回声需 6 个字以上。
  - 用 3 天数据离线核对：找回 162 句被误当作 hello 的同事说话；去掉 77 句听悟收进来的 hello 台词。现场 20 分钟没有台词混进 mind。
- **eye 改用强制工具调用**（eye-v4）。原因：大约 15:00 起，aihubmix 不再给 haiku 传递 `response_format json_schema`。之后 haiku 的回答被包在代码块里，自己起字段名，还会开始思考，单次耗时 30–180 秒；工具调用 3/3 合格，约 3 秒。mind 用的 luna 仍然遵守 json_schema。
- **对话纪要**（新单元 `soul-notes`，开关 `notes`，默认关；设计在 `docs/superpowers/specs/2026-11-11-conversation-notes-design.md`）：
  - 每分钟结束后 5 秒读一次别人说的话，按"60 秒没人说话"切段；最后一句开始满 90 秒才判定结束。luna 写约 10 字的标题和约 50 字的纪要。新对话开始时，会在当天已有的对话里找，判断是不是接着说。
  - mind 把纪要当作 `c1` 引用，v3 的提示词增加了【对话】一节。
  - 每轮最多调用 3 次，旧积压只记录不总结，晚到的句子归回原来那段。
  - 现场 16:27–16:49 被切成一段对话：225 句，23 次更新，没有失败，花费 $0.0069，单次中位 3.1 秒。纪要具体，例如"模组抗干扰与浪涌排查：TVS、浪涌经地线、4 个 TVS 烧毁、先确认方波"。
  - 纪要实际写到 80–100 字（用户觉得效果很好，暂不改）。
  - 22 分钟一直有人说话，所以只算成一段；中间换了几个话题，旧的被压缩掉了。以后可以考虑按话题分节。
- **mind 等待时间改为 20 秒**：第一版在分钟结束后中位 36 秒出来。v1、v2、v4 已删除，只保留 v3。

**待查（用户说"先放一下"）**：
1. **听觉频繁掉线**：
   - 本体统计 `send_failed` 28 次、`connect_failed` 7 次。ear 每小时记到的断线：9 点 2 次、10 点 1 次、14 点 13 次、15 点 7 次。
   - 掉线时刻和上传画面的时刻不吻合。Wi-Fi 信号 -42 dBm；Mac 到服务器 ping 往返平均 98 ms、最高 300 ms。
2. **画面花屏**：
   - 今天上传的 806 张画面里有 24 张出现一条洋红、绿色的乱色带，全都在 14:02 所有功能同时打开之后，上午只开摄像头和 M12 时一张都没有。
   - 推测是摄像头 DMA 往 PSRAM 写数据时，和听觉、JPEG 编码、上传、眼睛动画等争抢带宽，导致 RGB565 错位一个字节。
   - 补救方案（还没做）：本体或服务器丢掉花屏帧，不交给 eye。
3. 计划做的对照实验，每段 15 分钟：只关摄像头 → 只关听觉 → 全开，统计掉线次数和花屏张数。可能的改法：摄像头降到 5 帧/秒、调整任务优先级、丢掉花屏帧。

**暂缓的小问题**（从已删除的工作区记录合并过来）：
- **纪要**：
  - 新对话还没有标题时，c1 会退回上一段；
  - 已存的分钟包里 `end_ms` 会停在"进行中"；
  - 首次更新如果被预算或退避跳过，这段就不再做接续判断；
  - 页面上的"今天"按浏览器的午夜计算，不按设置的时区；
  - `/switches` 里 running 显示的是 `[]`。
- **mind 第 1 阶段**：
  - 退避从 tick 开始计时；
  - run_minute 出异常会丢掉分钟；
  - 第一次失败显示成"仅规则"；
  - 复查时不提"N 分钟没有新证据"；
  - 内部名字原样交给模型，会出现"最像None"；
  - 星期显示成"星期2"；
  - `/names` 取了前 5000 条且未排序；
  - `/now` 的分段起点和 fuse 不一致；
  - 设置文件解析失败时变成第 0 版；
  - 存在写竞争；
  - Content-Length 异常时会挂住；
  - `language_only` 的计数口径；
  - 情景段在两类之间交替时切不开；
  - 点击段落不会跳到对应的分钟；
  - 预算上限等于默认值。
- **mind 第 0 阶段**：
  - 时间线每次加载全部版本；
  - 指纹里含脸的 `to`；
  - mind 停机时端口可能被抢占；
  - `deploy --dry-run` 仍会在远端建目录；
  - remote_setup 写死了 venv 路径；
  - ssh 主机参数前没有 `--`；
  - 48 小时补跑上限没有记录被跳过的范围；
  - 数据库不可用时 last_error 会丢；
  - API 线程挂掉没人察觉；
  - rebuild 和正式进程会抢写。


## 2026-11-11 下午：感官拆分第 4 步——mind v3 已部署，设置第 3 版已切到 v3（Claude）

- 分钟包里多了 `e1`，内容是这一分钟的 eye 结果（builder 3）。提示词 v3 以 `e1` 作为画面的主要依据；没有 `e1` 时，退回 v2 逐条列脸的写法。
- 时间：分钟结束后 30 秒先出第一版。之后的复查如果发现内容变了（听觉句子、听悟、eye 后到），就出修订版（reason `revised`），每轮最多修订 3 分钟。
- `e1` 只在 v3 下算进"内容有没有变"：只看人数，不看措辞；某分钟没有 eye 记录时，沿用上一次的人数。v2 的这套判断和以前一样。
- luna 输出的 token 里大约一半是思考（940/1739）。v3 用 `reasoning_effort: low`，输出上限 4000。
  - 一开始把上限设成 2000，6 次里有 4 次被截断。
- 离线对比 6 分钟（重跑泳道）：
  - v3 每次 8–16 秒、约 $0.0007；v2 每次 12–25 秒、约 $0.0011。
  - 有画面的分钟，v3 能说出具体动作（擤鼻子后离开、托下巴思考）。
  - 用户看过对比，同意切换。现在设置第 3 版的 `fusion.prompt = v3`。
- **实测延迟**（所有感官加情景全开）：
  - 等待 30 秒（14:03–14:12）：第一版在分钟结束后中位 42 秒出来，修订 0 次；eye 在分钟结束后 15–18 秒到。
  - 改为等待 20 秒（`c3fef71`，已部署；14:22–14:31）：中位 36 秒，最快 31 秒，最慢 49 秒；10 分钟里修订 2 次，都在有人交谈的分钟。
  - 10 分钟的花费：mind $0.0089（13 次，每次约 12.9 秒），eye $0.0048，段总结 $0.0013；再加听悟。全开大约每小时 1.3 元。
- **已清理**：v1、v2 已删除（`2021667`），只保留 v3。
- 老问题（v2 和 v3 都有）：没有证据的旧条目（例如"桌边可能有访客短暂来往"）会一直从上一分钟带下来，以后处理。
- 暂缓的小问题：
  - 修订后，后面几分钟沿用的仍是第一版；
  - 听悟只看有没有，不看几句；花费页没有单独的修订行；
  - 修订如果撞上退避或预算，就不再补；
  - 修订可能让段总结重算；
  - 修订步骤出错时会跳过这一轮的新分钟；
  - v3 提示词是在 v2 文本上做替换生成的，导入时有断言；
  - v2 的 `language_only` 也把 `e1` 算了进去；
  - 往回看 30 分钟时会多读 eye 记录。


## 2026-11-11 午后：感官拆分第 3 步——eye（haiku 每分钟看一次画面），已部署、已验收（Claude）

- 新单元 `soul-eye`（代码在 `server/mind/eye.py`、`eye_runner.py`，和 mind 一起部署）：
  - 开关 `hello.switches.eye`，默认关；页面视觉选"识别"时打开。
  - 开着时，每分钟结束 15 秒后，把这一分钟的画面（≤6 张）和 M12 事件交给 `claude-haiku-5-5`，结果写进 `hello.eye_minutes`；运行记录写进 `mind_runs`（step `eye`）。
  - 这一分钟没有画面就不调用。开关关着的那段不补；补跑最多补 10 分钟；补跑期间每一分钟调用前都重新检查开关。
  - 调用后出错时记为失败并退避，不会重复调用同一分钟。
  - 和 mind 共用每天的预算上限。
- 现场（12:28–12:35）：
  - 每分钟 1 次调用，平均 3.9 秒，约 $0.0003；输入约 1500、输出约 380 token。
  - 离开、房间不动的那一分钟没有画面，也就没有调用；关掉之后也没有再调用。
  - 发现两件事，已修成 eye-v2 并部署：
    - 严格 schema 下仍会回空壳：人数 1、people 为空、scene 只写了 "x"。现在会检查内容并重试。
    - 没认出的脸，有时写成主人称呼，有时写成"未认出的人"。现在提示写明："坐在桌前没认出的人，很可能是主人"。
  - v2 还没在现场跑过。
- 页面：
  - 视觉三档（关 / 上传 / 识别）都能用。
  - 花费那一行会显示"视觉识别"；状态过期时显示"识别状态未知"。
  - 情景页的画面下面有"视觉识别"卡片。
- 暂缓的小问题：
  - 卡片只显示时分，看不出结果是不是旧的；
  - 结果的字段类型没有校验；
  - 日志等待用的 pipefail 可能误报；
  - 临时单元重启时沿用旧的启动参数；
  - 打开开关的那一分钟也会被处理；
  - 补跑中途失败时，剩下的分钟按退避处理；
  - eye 会占用 mind 的预算。
- 下一步：第 4 步 mind v3。读听觉整理、eye 结果和其他信息；更及时：一分钟结束后约 15 秒先出一版，晚到的记录再修订。


## 2026-11-11 午：感官拆分第 2 步——机身摄像头画面上传，已部署、已验收（Claude）

- 本体 **collector-0.4.76**（只写 ota_1，已独立校验；0.4.74 和 0.4.75 的备份都在 `backups/`）：
  - 摄像头任务给每一帧算一个 16×12 的亮度格签名。
  - 视觉通道 `/vision` 在画面有变化、且距上一张满 10 秒时，发送 `SVF1`（头部 JSON 加 JPEG）。每次连上后，第一张一定发。
  - 判断有没有变化：先去掉整体亮度的变化，再看 192 格里有没有至少 6 格的变动超过 30。
    - 11:56 那次现场数据：空房间（光线、影子）3–4 格；有人在桌前活动 5–26 格；人离开或回来 144–170 格。
    - 12:07 复验：空房间不再上传；同事进出、人在镜头前活动都会上传。
  - 一帧最大 32 KB，发送时限 3 秒。发送失败后暂停上传画面 60 秒，M12 事件照常发送。
- ear：帧存在 `recordings/frames/<视觉会话>/<t_us>-<seq>.jpg`，并写入 `hello.frames`，全部保留。帧有问题或磁盘出错时，ear 回一个错误，连接不断。
- mind：`GET /frames/latest`。情景页"服务器收到的画面"每 15 秒刷新一次。开关条的视觉改成三档：关 / 上传 / 识别；"识别"是第 3 步，现在点不了。
- 编译要加 `IDF_PYTHON_ENV_PATH=$PWD/.tools/espressif/python_env/idf5.5_py3.13_env`，因为 Homebrew 的 python3 已经是 3.14。
- 暂缓的小问题：
  - 取图失败时，页面上的旧图还留着；
  - 第一张就太大时，"过大"计数会按帧率一直往上涨；
  - 签名无效时，页面显示成"首张"；
  - 只开摄像头时，情景说明里没把它算作一种感官；
  - ear 是在事件循环里同步写文件的。
- 下一步：第 3 步 eye（haiku 每分钟看一次图）。


## 2026-11-11：总开关、mind v2 上线、感官拆分第 1 步（听觉两档）已部署（Claude）

**在运行**（ear `soul-spike` 与 mind `soul-mind` 都已部署到 `codex/continuous-hearing` 最新代码）：
- **总开关条**（观察台首页和情景页顶部）：
  - 听觉三档：关 / 本地 / 听悟。
  - 视觉：M12 检测开或待机。
  - 情景：mind 调模型开或关。
  - 一行显示"现在花不花钱"。
- **mind**：
  - 提示词 v2，设置版本 2；v1 已退役，只为旧记录保留文本。
  - 情景关时只建分钟包，不调用模型。两种感官都关的分钟记为 `rules_only/sensors_off`，不调用模型。
  - 换提示版本时会重新调用模型，不沿用旧版本的场景。
- **听悟按服务器开关 `hello.switches.tingwu` 开关**，连接中途也能切换：
  - ear 每 5 秒读一次开关，读不到超过 60 秒就按关处理。
  - 状态写在 `tingwu_status`，页面经 mind 的 `GET/POST /switches` 读写。
  - 部署后默认关。
  - 现场验证（11:22）：开启后 25 秒内听悟只出了 1 句，它的 pos [1480960,1653440] 覆盖同时间的 SenseVoice 句 [1497408,1537792]。开启点是第 1310400 个采样，时间对得上。切回本地后取回最终结果，写入 `tingwu_streams.1`，`running` 变成空。
- 视觉模型选定 `claude-haiku-5-5`，比拼数据在服务器 `~/soul-mind/bake-2026-11-11/`。

**下一步**（总设计见 `docs/superpowers/specs/2026-11-11-senses-split-design.md`）：
- 第 2 步：摄像头上传。画面变化时最多每 10 秒一张，经 `/vision` 二进制发送，要改固件和 ear。
- 第 3 步：eye 服务，用 haiku 每分钟识别一次画面。
- 第 4 步：mind v3，要求更及时：分钟结束后约 15 秒出第一版，听觉记录晚到时出修订版。
- 第 1 步暂缓的小问题：
  - 晚到的错误会影响重开时的 60 秒退避；
  - 会话的 `tingwu` 汇总被最后到达的那段覆盖；
  - createTask 之前就停掉的流不留统计；
  - 连接很慢时缓存超过 30 秒，时间会错位；
  - 连接中就显示"在跑"。
- 本机 ear 测试环境：`.private/venv-ear`（numpy、websockets、opuslib_next、sherpa-onnx）。


## 2026-11-10 夜：mind 第 1 阶段（Claude）——下班时的状态

**已部署、在运行**（服务器临时单元 `soul-mind`，代码到 `79ceb4a`）：
- 每分钟让 luna（aihubmix `gpt-6-luna`，严格 json_schema）写情景，提示词 **v1**；没变化的分钟沿用（按内容判断：主人状态、覆盖、画面里有谁、声音种类、新的话），复查间隔 5/10/20/30 分钟；失败重试一次、退避 1/2/5/10 分钟；每次调用记 `mind_runs`。
- 情景段：按主人在/不在/不确定/感官关切段（新类别持续 3 分钟才切），luna 写段标题和总结（`mind_segments`）。
- 设置存数据库带版本（`mind_settings`），现为**第 1 版**：主人称呼 Kira / Dave / 无限 / 劳埃德，每天随机一个（当天固定）；声纹名 me、主人；人脸名 me；认识的人暂不填（用户定）。
- 网页 `/mind.html`：此刻卡片、打点、“我在哪”地点报告（用户在页面报告，Wi-Fi 要等第 2 阶段快照）、情景段、分钟明细（模型情景、校验、完整提示和回复、标对错、换提示/模型重跑）、设置对话框、调用统计。
- 统计：花费不设限（上限 20 美元/天只防失控，用户定），`/stats` 按天×步骤×模型统计。补跑 17:14–19:14：119 分钟中 40 次调用、约 80 分钟沿用、1 次失败，共约 $0.06；luna 每次约 20 秒、约 2.2k 输出 token。

**已提交、未部署**（`a43463d`、`792d3d0`，明天先部署）：
- 用户反馈 v1 “全是否定式”。新增提示词 **v2**：用正向的话说最可能的情况（在画面里 + 打字 → 大概在桌前工作；画面没人 → 附近大概没人），不写免责句，条目 ≤ 8，who_am_i 写 hello 自己，有人说话时写最可能的话题。段总结同样改正向（segment-v2）。校验加 `negative_ratio`。离线对比 3 个真实分钟：否定式 62%/33%/56% → 0%/0%/17%，条目 8–9 条 → 2–6 条。
- **明天的步骤**：① `server/mind/deploy.sh`；② 设置里 `fusion.prompt` 改为 `v2`，存为第 2 版（`/api/mind/settings`）；③ 用 v2 重写最近 2 小时（`--rebuild 起 止 --fusion`，约 $0.06，新版本，v1 旧版保留）；④ 请用户读“此刻”和情景段，再按需要调提示。
- **夜里（已更新）**：用户关掉了 hello 的听觉和视觉，并要求现在不再产生任何费用；已执行 `sudo systemctl stop soul-mind`，mind 已停（临时单元，明天 `deploy.sh` 会重新建起）。
- **明天新增的首要任务（用户提出）**：前端显要位置加三个开关——**听觉、视觉、mind**，一眼能看到状态、一键开关。另外：两个感官都关着时 mind 不能调用模型（审阅时记为小问题，现在升为必须）；mind 关着时不调用任何模型（融合、段总结、重跑都不调）。

**观察**：常有“未认出的脸”持续在画面里，很可能是用户本人没被 M12 认出（角度/光线），待用户标注确认。

**审阅**：第 1 阶段独立审阅 1 个严重、7 个重要，均已修（见提交 `df07ae2`）；17 个小问题未修，记在本地执行记录 `.superpowers/sdd/2026-11-10-mind-phase1/progress.md`（如：两个感官都关时仍会调用、没登记的名字原样发给模型、点情景段不能展开、连续出错时个别分钟可能缺情景）。

**未推送**：第 1 阶段的提交都在本地 `codex/continuous-hearing`，用户说推送再推。


## 2026-11-10 晚：mind 第 0 阶段上线（Claude）

- **设计**：`docs/superpowers/specs/2026-11-10-mind-design.md`（Fable 独立设计稿为底稿，开头“修订”一节是核对后的更正、探测结果和已定事项）；计划 `docs/superpowers/plans/2026-11-10-mind-phase0.md`。用户定：mind 只做感知、不做输出；语言内容发给模型，但情景感知要全面，语言不是唯一重点。
- **服务器** `server/mind/`：临时单元 `soul-mind`（`server/mind/deploy.sh` 部署，服务器重启后要重新部署），每分钟在 N+1 分 20 秒处理第 N 分钟，只读原始表，写 `mind_packages`（带版本，规则或设置变了也出新版）和 `mind_state`。规则：SenseVoice 和声音按耳朵时钟 `t` 归分钟，听悟按采样位置；脸的区间靠 lost / 视觉关 / M12 掉线 / 会话结束收口；没收尾的旧会话在新会话开始处结束；最新的听觉会话即使安静也算在听。设置 `~/soul-mind/settings.json` 只在服务器（写错了保留上一份并在 `/state` 报 `settings_error`）。重算：`--rebuild FROM TO`。
- **监控页** `/mind.html`（首页右上“情景”）：经 ssh 转发到服务器本机 `127.0.0.1:52740`，Mac 侧是 `.private/mind-tunnel/` 里的 Unix 套接字，令牌不进浏览器。
- **核对**：16:30–17:30 句子 172 = 172、声音 44 = 44。16:55 以前（0.4.73）视觉覆盖常为 0，是已知历史缺口。
- **模型探测**（`server/mind/probe.py`，约 $0.03）：luna、terra 都能看图、都支持严格 JSON 结构；只给文字时两者都爱答“主人在”、按句数夸大人数。
- **用户反馈**：情景页“没什么感觉”。用 luna 在真实分钟上试了原型（不提交）：诚实但处处“不明”。原因和下一步见本节末。
- **审阅**：独立审阅 0 个严重、7 个重要（5 个已修，#5 脸区间上限、#7 听悟跨分钟对齐留给用户/第 1 阶段），14 个小问题记在本地执行记录里未修。
- **下一步**：第 1 阶段（luna 融合、运行记录、单分钟重跑、标注和一键打点、基础设置页）。开工前先和用户讨论“感觉”从哪里来：每分钟太短，要以情景段和“此刻”为主视图；输入要补地点、照片和设置。


## 2026-11-10 傍晚：视觉独立通道（0.4.74，Claude）

- 用户指出视觉不该借听觉的连接。改为独立 WebSocket `ws://<耳朵服务>/vision`（同主机、端口、令牌，地址由 `vision_url()` 从听觉 URL 推出，`hearing::backend()` 提供）。
- **hello 端** `collector/vision_uplink.cpp`：独立任务 `vision_up`（PSRAM 栈 8 KB），M12 在线或队列有事件就连，断线退避 2→30 s 重连，M12 离线且队列空 30 s 后断开；首条 `{"type":"hello","source","device"}`，每 15 s `ping`。M12 队列改为 peek → 发送 → commit（带 `seq`），只有发出去的才删除。遥测 `m12.uplink`（state/connects/failures/sent/last_error）。听觉会话里的视觉上传已删除。
- **服务器** `server/ear/vision_link.py`：路径 `/vision` 走单独处理（最多 4 条），`sessions` 记一条 `kind=vision`（键 `YYYYMMDD/HHMMSS-source-vN`），事件进 `hello.vision`，60 s 无消息关闭。0.4.73 在听觉会话里发的 `vision` 仍兼容。
- **实测**：听觉关、视觉开，独立通道连上，30 条事件全部入库（found → named me 约 0.6 s）。注意：年龄估计跳动（23→50→26 岁）会让 face_attrs 频繁出现，未过滤；同帧事件按 `seq` 排序，`at` 可能差几毫秒。


## 2026-11-10 傍晚：视觉变化事件入库（0.4.73，Claude）

- **hello 端**（`m12_protocol.h` `M12ChangeTracker`，`m12_unit.cpp`）：按 m12d 的 face/person id 跟踪，变化即生成事件：`face_found`、`face_attrs`（性别/年龄段一出现就报，用户要求“认到信息要尽快报”）、`face_named`（名字出现或变化）、`face_unknown`（who 回复无人匹配，带 sim）、`face_lost`（1.5 s 未见，带最后的框、名字、属性；`reason` timeout/vision_off/m12_offline/evicted）、`person_found/lost`、`vision_on/off`（以 cfg 回复为准）、`m12_online/offline`。事件进 PSRAM 64 条队列（满了丢最旧并计数，遥测 `m12.vision_events`），由听觉会话每块音频顺带上传 `{"type":"vision",...,"t_us","now_us"}`；听觉关着时先存着，连上再补发。
- **服务器**：耳朵收 `vision` 写 `hello.vision`（键 session+t_us+event+face+person；`at` = 收到时间 − 在机器人上等待的时间）。
- **实测**：脸 #165 found → attrs（男/成年/30）→ 0.5 s 后 named me；部署期间的事件补发入库。已知小问题：found 已带属性时会紧跟一条内容相同的 face_attrs（未修）。部署前进入画面的 #164 的 found/named 发给了旧服务端被拒。
- **部署**：服务端部署在用户说“部署吧”后执行；单独刷固件不受限，与部署写在同一条命令里会被权限检查拦截。


## 2026-11-10 傍晚：hello 数据库与听悟实时转写（0.4.72，Claude）

- **数据库 `hello`**（服务器本机 MongoDB；连接 `MONGO_URI` 运行时从 hubmix `~/aidea/agent/.env` 读，不进仓库）：`sessions`（每连接：来源、起止、录音路径、模型、听悟统计）、`speech`（SenseVoice 每句，已识别的熟人带 `speaker.name`）、`sounds`、`tingwu`（实时句 `kind=sentence`、会话后带说话人的 `kind=paragraph`）。键为 `session`=录音文件夹名（`YYYYMMDD/HHMMSS-source-cN`）+ id，可回到原声；**音频不进库**（录音文件永久保留，库里存路径和采样位置）。`server/ear/hello_db.py` 单后台线程 + 有界队列，库不可用只丢行（计数）不影响耳朵。
- **补录**：`server/ear/hello_db_import.py`（可重复运行，按 session+id upsert）；已导入 273 会话 / 1513 句 / 708 声音，重跑数量不变。
- **听悟实时**（`server/ear/tingwu.py`）：只对 stackchan 来源，每连接一个任务，送原始 16 kHz 声音（不降噪、不屏蔽）；实时句时间取 `words[].beginTime/endTime`；会话结束后 createTask(dataId) 结束任务、getTask 轮询取 `transcriptionPath` 段落。密钥 `DASHSCOPE_API_KEY/APP_ID` 同样从 hubmix 读。`--tingwu off` 可关，计费约 0.9 元/小时。服务端测过 30 s 真实片段。
- **到前端**：耳朵发 `{"type":"tingwu","phase":"sentence"|"error"}`；0.4.72 记入听觉事件环（kind `tingwu`）；监控页总 log 加“听悟”标签（橙色），声纹认出熟人的句子绿底绿字。固件把听觉文字截断改为不切断 UTF-8 字符。
- **实测**（16:36）：SenseVoice「不检查了，怎么看病啊？」（身份 me，绿色）→ 约 5 s 后听悟「不检查的，怎么看病，你没有检查报告***那。」；与 hello 播台词重叠的句子会标出。
- **部署**：服务器 ear venv 装了 pymongo 4.17.0；`deploy.sh` 文件列表加了三个新文件；用户授权后部署，日志 `hello db: on; tingwu realtime: on`。


## 2026-11-10 傍晚：正常眼睛模式、声纹录入/删除、降噪可控评估（0.4.68–0.4.71，Claude）

- **正常眼睛模式**（`collector/eye_mode.h`）：没开视觉一直笑脸男（台词也不切，`set_voice_auto_enable(false)`）；M12 被请求检测（追踪 / 认人 / 开启检测 / turn on）时 v14 机甲眼睛，自然循环只在 idle↔sleepy/thinking/curious（`set_natural_pool`）；听到有情绪的话且没有台词回应时按情绪播一次（新来源 `Source::Hearing`，台词表情优先、手动最优先）；41–80 号台词按台词情绪切表情（原来被忽略）。0.4.71 起开启视觉不再切到全屏人脸框页面。主循环每 250 ms 跟随 `m12_requested_function`，失败 2 s 重试。实测：认人模式下 v14 显示并循环 curious/sleepy；开启检测时主页显示 v14。
- **笑脸男缩到 90% 直径**：`scripts/generate-screen-assets.py` 加 `FACE_SCALE=0.9`（工具按 docs/build.md 装在 `.tools/ui-assets`，先验证原样再生成一字不差）；30 色、6153 变化像素，固件小约 100 KB。`tests/test_packed_palette.py` 改为从头文件读尺寸、用独立 Python 解包比对，不再钉死旧图。
- **声纹录入**：原因①录入开始固件把前 1.2 s 标为自身发声，第一句被丢（0.4.70 网页录入不再加此保护）；②hello 麦克风人声把握偏低（0.17–0.5），服务端对 stackchan 来源接受 `unclear`（≥0.2）；③不足 1 s 的句子不收。服务端 progress 带 `rejected`（too_short/too_long/not_voice/self_voice/bad_embedding），0.4.68 转到遥测 `hearing.enrollment.rejected`，网页显示原因。用户已部署并录入 “me”，录入后认出 me（0.62/0.80）。
- **声纹删除**：服务端 `{"type":"forget","name"}` → `hp.forget_speaker`（原子替换 + .bak），回 `speakers` 事件；0.4.71 `hearing` op `forget`（需会话在线），遥测 `hearing.forget`；网页“录入声纹”窗口列表 + 删除。需再部署服务端才生效。
- **降噪可控评估**：40 秒稿子（正常 / 小声 / 打字 / 拍手 / 叹气 / 2–3 m），从 8081 原始口录两次（录前关连续听觉以免回应插话）。`--denoise-mode stream|asr|off`：安静一次 stream 能抓到小声（60%）和远处（9%），off/asr 漏掉；嘈杂一次都不行，stream 把同事和用户连成大段；asr 模式无用。三种都没认出打字/拍手/叹气（背景 −30 dBFS）。用户定：**耳朵听到就报，是否结合人脸由思考服务判断**。服务器上没有 GTCRN 模型，降噪未启用。录音 `.private/mind-tingwu-test/20261110/ctrl-take*.wav`。
- **构建 / 部署**：每版只写 ota_1、独立 verify，备份 `backups/ota_1-collector-0.4.6{7,8,9}/0.4.70-before-*-20261110.bin`。服务端部署被权限检查拦截，需用户自己运行 `server/ear/deploy.sh`。


## 2026-11-10 下午：M12 换成 m12d 自研程序，驱动与网页重写（0.4.66–0.4.67，Claude）

**背景**：M12 原厂固件已换成用户自研常驻程序 m12d（协议：m12d 仓库 `docs/protocol.md`，0.1.0）。原厂功能切换、audio_fft 待机、Face Recognition 训练和原厂人脸库全部失效。盘点与迁移表见 [m12d 迁移](m12d-migration.md)。实机：迁移前 M12 已在推送 m12d 检测行，但旧固件 89 次待机命令全部无回应。
**固件**：
- `m12_protocol.h` 重写：检测行（face id/性别/年龄段/name、person[]）、心跳、回复（含 faces 库列表、cfg、status、ver）解析；行缓冲由调用方提供（机器人上 4 KB 放 PSRAM），丢弃超长/坏行；请求组包（名字 JSON 转义、1–32 字节、≤512 字节）；`M12Requests` 待回复表（learn/who 4 s、其余 1 s）；`M12WhoPolicy`（新 id 无名字最多 who 一次，busy 指数退避 0.5→4 s，standby/hot 等 5 s）；`m12_track_target`（同 face id 粘住，无脸用人形框上部）。删除 `M12Policy`、recognize 模式、`match_prob` 与原厂命令。
- `m12_unit.*` 重写：沿用原 M12 任务（PSRAM 栈 8 KB，core 1），共享状态、行缓冲、JSON 内存（自定义 PSRAM 分配器）、消息体都在 PSRAM；6 s 无心跳判离线；待机/唤醒走 `cfg det`（同时 `lm:false` 省带宽），以 cfg 回复确认并记录确认的请求版本；hb.up 回退或离线恢复视为 M12 重启，重发 cfg；温度跨 5 °C 或 tier 变化写日志，60 s 一行摘要；`m12_request(op,args,callback)` 供任何任务调用，回调在 M12 任务里执行；learn/forget 成功后自动刷新库列表；遥测 `m12`：online、heartbeat、cfg、faces（id/name/gender/age/age_group）、persons、commands（最近 6 条用户命令结果）、library、counters。
- 认人模式（`interaction*`）：只要求检测开着，身份取检测行里的 name，任一带名字的脸连续 2 帧确认。追踪（`remote.cpp`）：按 face id 粘住，无脸时用 person 框。`/v1/control`：`m12_mode` 只剩 face_detector/standby；新增 `m12_request`（op ping/ver/status/cfg/learn/who/forget/faces，可带 name/face，202 返回 id，结果见遥测）。屏幕人脸页按心跳提示离线/过热，脸有名字时显示名字。
- 0.4.66 输出遥测时把性别写成临时数组指针，JSON 出现非法 UTF-8；0.4.67 所有动态字符串改为复制。
**网页**：`m12.mjs` 状态改为心跳在线 + cfg 确认（离线/过热/待机/切换中/检测中/过期），帧率和温度取心跳；M12 面板去掉“手动认人”，加“认人库”（学习 / 认一下 / 刷新 / 删除，错误码中文提示，每次开机自动读一次列表）；脸框标 id 和名字（黄框=已认出，虚线=人形）；`control.mjs` 新增 m12_learn/forget/faces/who/ping（需 m12d 固件且在线，名字 1–32 字节）；SSH 温度桥不再覆盖心跳温度（留在 m12.system）。
**测试**：`tests/m12_protocol_test.cpp` 用协议文档示例行覆盖检测行、心跳、各类回复、全部错误码、半行、乱码、超长行、回复与推送交错、组包、超时表、who 策略、追踪目标；交互测试改为按名字确认。Python 88（4 跳过）、Node 85 通过。
**实机（0.4.67，USB 日志 `logs/m12d-v0466/`、`logs/m12d-v0467/`）**：心跳 ✅（开机 1.2 s 在线，68–75 °C）→ ping ✅ → ver ✅（0.1.0）→ 读 cfg ✅ → 待机/唤醒 ✅（唤醒 fps 15.1，待机 0、检测行停止）；0 坏行、0 超时、0 未匹配回复；唤醒期间自动 who 已触发 1 次。
**未完成**：人脸追踪、learn 用户名字、who 认出、forget 四步需用户在镜头前配合，尚未实测；网页界面因 Chrome 扩展未连接未做截图验收（接口与组件测试通过）。产物 `artifacts/collector-0.4.67-m12d/`（3,594,352 B，SHA256 `c5c352ca8575…`），备份 `backups/ota_1-collector-0.4.6{4,6}-before-*-20261110.bin`。未提交。
**另**：服务端降噪（GTCRN）已改为“说话检测/识别用降噪、声音分类用原声”，3 min 对话段识别 15→22 句且声音事件不变；早上测试段识别 51→73 句、打字 14→12，但小声句子变差、打字残渣多出一句假话。**未部署**，等用户决定（可试：说话检测用原声、只把语音段降噪后送识别）。代码在 `server/ear/`（未提交）。


## 2026-11-10 中午：听悟实测、底噪“次啦”来源、服务端降噪方向（Claude）

- **听悟**（11:48:18 起 3 min，0.4.64，+12 dB）：实时 14 句、最终 10 段带说话人；覆盖多于 SenseVoice（SenseVoice 漏的几处用户说是机器发音、被耳朵主动屏蔽），用户确认自己的声音清晰。结果与录音在本机 `.private/mind-tingwu-test/20261110/`（`clip-1148.wav`、`T1148-*`；脚本 `tingwu_rt3.py`，已修 getTask 为 POST）。
- **7.45 kHz 尖啸**：间歇出现（09:33、11:04、11:42–11:50），0.4.59 就有，与刷机无关；受控播放台词时麦克风与参考声道都没有 → 外部或未查明来源。用户说这不是“次啦”。
- **“次啦次啦”（用户：持续的底噪）**：原始 HTTP 采集（无 Opus）里爆裂点与发送节奏同步——只有 20 ms 原始流时 50 Hz 线 +25 dB；开听觉（60 ms Opus 包）后出现 16.7/33 Hz 线；不在块边界（非软件拼接）。0.4.65 临时加运行时 Wi-Fi 发射功率开关做扫描：14→2 dBm 同步干扰都约 −53 dBFS（hp），**与发射功率无关** → 推断为编码/发送时的数字电流冲击耦合进麦克风模拟电路（硬件层面）。0.4.65 已按用户要求撤回（源码回到 0.4.64，设备回刷 0.4.64 产物）。干扰约比说话低 30 dB，主要影响听感。
- **同步模板相减**（60 ms 周期）：同步分量 −12 dB，但爆裂点数不变（时刻有抖动），用户听不出区别 → 放弃。
- **sherpa-onnx GTCRN 降噪**（`gtcrn_simple.onnx`，0.5 MB）：安静段 −34→−46 dBFS；本机耳朵回放同一 3 min：说话 15→24 句、人声把握 0.5–0.7→0.8–0.9、空结果 0→1、声音事件 5→8（新增水声/电器可能误认）。服务器单线程 11× 实时（约 9% 单核）。用户：“降噪还行”；决定放在服务端（设备端需移植到 ESP-DL，GTCRN 浮点估计占一个核 30–60%，不划算）。
- **下一步**：耳朵只对 stackchan 来源接流式 GTCRN（`OnlineSpeechDenoiser`），原始录音照存、另存降噪版；先在今天录音上验证（含早上打字/拍手/叹气）再部署、实机测，然后提交。


## 2026-11-10 上午：网络栈失联根因与修复（0.4.61–0.4.64，Claude）

**用户要求**：断网最优先；尽量多用 PSRAM，内部 RAM 只留给真正必须的地方；噪声输入（+12 dB 后只有标点的空结果）等断网解决后再看。
**复现**：0.4.59/0.4.60 上开连续听觉后 30 s–3 min 内反复出现（ping 先通 TCP 先坏，随后 ping 也断，屏幕正常；0.4.59 一次 30–40 s 自恢复）。
**诊断版**：0.4.61 `main/collector/mem_diag.cpp`：USB 每 5 s `MEM`（内部/DMA/PSRAM 空闲、最大块、最低、分配失败数）、每 60 s `TASKS`（每个任务栈在 INTERNAL/psram 及余量）、`heap_caps_register_failed_alloc_callback` 打 `ALLOCFAIL`；遥测 `health.alloc_failures(_internal)`。0.4.62 加每 5 s ping 网关（`NET`），连续 2 次不通时打 `STUCK`：lwIP 线程能否执行回调、各任务状态与 CPU 增量、`esp_wifi_statis_dump(ALL)`。
**证据**（`logs/wedge-v0461/`、`logs/wedge-v0462/usb-continuous.log`）：失联时 Wi-Fi 关联一直在（无断开/信标超时），lwIP 线程活着（tcpip_alive=1），**Wi-Fi 动态 RX 缓冲 6/6 全被占住、RX 申请内存失败 378 次**；1630 B DMA 内部内存分配失败数十次，内部空闲最低 6 KB、DMA 空闲最低约 2 KB。0.4.61 那次失联时内部空闲约 30 KB、分配失败 0——说明主因不是总量耗尽，而是 RX 缓冲被占住。
**根因**：未开 `LWIP_L2_TO_L3_COPY` 时，收到的每帧（哪怕 100 字节的服务端 tick）都以零拷贝方式挂在 1.6 KB 的 Wi-Fi DMA RX 缓冲上，直到应用读走；`TCP_RECVMBOX_SIZE=6` 恰好等于 6 个动态 RX 缓冲。听觉 worker 在发送上卡住（发送时限 1 s）时，未读的 tick 占满全部 RX 缓冲 → 收不到 ACK/ping → 发送更卡，形成死锁；内部 RAM 太少（24/29 个任务栈在内部）又让 Wi-Fi 申请不到新缓冲，放大问题。
**修复**：
- 0.4.63：`sdkconfig(.defaults)` 加 `CONFIG_LWIP_L2_TO_L3_COPY=y`（帧复制进 lwIP pbuf，>512 B 走 PSRAM，RX DMA 缓冲立即归还）。10 min 无失联（ping 125/125、HTTP 122/122），但听觉仍有 3 次 send_0、内部最低 2.2 KB、分配失败 416。未开 `SPIRAM_TRY_ALLOCATE_WIFI_LWIP`（会强制 Static TX 缓冲，可能更占内部 RAM）。
- 0.4.64：`main/collector/psram_task.h` `create_psram_task()`（栈+TCB 在 PSRAM；任务内不得访问 flash/NVS、不得把栈上缓冲交给 DMA、用 `vTaskDeleteWithCaps` 结束）。搬到 PSRAM：sensors、environment、audio、camera、discovery、motor、m12、temp（约 44 KB）；麦克风增益 NVS 读取移到建任务前 `load_mic_gain_setting()`。留在内部：httpd×2、main、collector_serial（存 Wi-Fi）、collector_asr（存音量）、audio_stream（每客户端自删，现未使用）、系统任务。结果：内部空闲 18–22 KB → **约 66.8 KB**，最低 2.2 KB → **45 KB**，最大块 7.7 KB → 36.9 KB，分配失败 0；内部栈任务 25 → 17。
**构建**：`IDF_PYTHON_ENV_PATH=$PWD/.tools/espressif/python_env/idf5.5_py3.13_env ./scripts/idf.sh build`。每版只写 ota_1 并独立 verify，备份 `backups/ota_1-collector-0.4.6{0,1,2,3}-before-*-20261110.bin`；产物 `artifacts/collector-0.4.6{1,2,3,4}-*`。0.4.64：3,583,136 B，SHA256 `8449811b9f4419448597...`（完整值见 manifest）。
**0.4.64 实测（11:12–11:33，连续听觉开着，21 min）**：失联 0 次；网关 ping 253/253；Mac 读遥测 247/247；分配失败 0；内部空闲 55.7–66.8 KB、开机以来最低 39.5 KB。听觉仍有 2 次 send_0 + 1 次 connect_failed（自动重连）；当时网关 ping 1–8 ms 正常，所以是到后端的公网路径 RTT 尖峰撞上 1 s 发送时限（11-09 已知问题），不是设备失联。
**待办**：听觉公网 send_0（每次丢约 3 s 音频）；诊断 ping 任务占约 2.5 KB 内部栈，验证完可去掉或改 PSRAM；噪声输入问题。


## 2026-11-10 上午：麦克风数字增益 + 限幅（0.4.60，Claude）

**起因**：用户回到办公室实测采音（hello 的办公室 DHCP 地址会变，按设备 MAC（`.private/stackchan-monitor.json` 的 device_id）在 `arp -a -i en10` 里找；监控配置已改，备份 `.private/stackchan-monitor.json.bak-20261110`）。底噪约 −55 dBFS（比昨天 −43 低约 12 dB）；用户确认 **100 Hz 嗡嗡声是 M12 风扇**，不追底噪，**先把设备端声量调到合理区间**。
**实测（11-10 09:25 会话 c23，0.1 s RMS 有效语音电平）**：一臂远用户说话 −40…−44 dBFS，近处对话 −22…−28，个别峰值接近 0 dBFS；ES7210 模拟增益已在上限 37.5 dB（代码写 60 被截断），驱动没有用芯片数字音量寄存器。
**修改**：`main/collector/mic_gain.h`（新）——麦克风通道（交错 PCM 第 0 路；第 1 路扬声器参考不动）固定数字增益 + 块峰值限幅（−1 dBFS 上限，整块峰值已知故无需预读、不削顶；恢复 0.6 dB/块 = 30 dB/s，线性渐变）。`audio_task` 在读到每块后、写环形缓冲与送听觉之前处理，所以录音、耳朵、HTTP 音频流拿到同一音量。默认 **+12 dB**，范围 0–24，NVS `collector/mic_gain`；`POST /v1/control {"target":"mic_gain","value":dB}`；能力 `mic_gain`；遥测 `health.mic_gain_db/mic_gain_applied_db/mic_gain_source/mic_gain_saved/mic_gain_min_db/mic_gain_max_db/mic_limited_blocks`。**注意：`mic_level_dbfs/mic_peak_dbfs` 从 0.4.60 起是增益后的电平**，与之前的数不能直接比。监控页“连续听觉”面板加“麦克风增益”滑块。
**测试**：`tests/mic_gain_test.cpp`（`test_mic_gain.py`）：−40 dBFS→−28.0、0 dB 原样、−6 dBFS 正弦与满幅方波不过上限、0.34 s 恢复、降增益一块内生效、参考声道不动。Node 83、Python 85（4 跳过）通过。
**构建环境提示**：系统 python3 是 3.14，IDF 环境只有 py3.13，需 `IDF_PYTHON_ENV_PATH=$PWD/.tools/espressif/python_env/idf5.5_py3.13_env ./scripts/idf.sh build`。
**产物**：`artifacts/collector-0.4.60-mic-gain/`（3,572,672 B，SHA256 `c2c051942de3c025015c3475d62fb252395a0abd129ab545593d7dc8b9ba6dce`）。

## 2026-11-09 晚间：思考后端 mind 讨论与听悟实测（Claude）

**没有改设备，也没有改耳朵服务**；没有提交。完整记录见 [思考后端讨论记录](soul-mind.md)。

- **命名**：机器人叫 **hello**（用户定，暂定）；思考后端模块暂定 mind。
- **用户定**：
  - 情景用结构化条目，内容是自然语言；强力元素作为条目，听到的谈话不断积累；
  - 约一分钟更新一次；第一阶段只在服务器上做；
  - 假定 hello 每分钟至少送 1 张照片或一份分析；M12 认人已稳定可用（旧数据过期）；
  - 要区分角色的转写，并和 sherpa-onnx 对照。
- **听悟实时实测**（修正前的两段录音，约 3.5 分钟，约 5 分钱）：
  - 每句话出来时没有说话人编号，结束任务后的最终结果里才有，而且和 sherpa 一致；
  - 但漏字严重，比 SenseVoice 差很多；放大音量、滤掉嗡嗡声都没有解决。
  - 18 点以后的录音底噪低了约 10 dB，但修正后没有录到人说话，还没能测。
- **脚本和结果**：本机 `.private/mind-tingwu-test/`，服务器 `~/soul-mind-test/`。
- **hubmix 附带发现（未修改）**：`tingwu.py` 的 `query_task` 用 GET，DashScope 返回 400，应改为 POST 加 `getTask`。
- **明早**：用修正后的固件录一段真人说话，并排比较 SenseVoice、听悟实时、听悟离线、gpt-4o-transcribe-diarize，然后定转写方案、写设计文档。

## 2026-11-09 收工状态（Claude）

**设备**：collector-0.4.59，办公室 FreedomX20A，连续听觉开着，音量 80，舵机空闲；SD 台词 80 条已装齐。耳朵服务端已部署（机器人轻声门槛 + 她说话时不听）。代码已提交并推送 `codex/continuous-hearing`（78437c8、a206e98、4c8bb6a）。
**当日完成**：追踪一次转到位与两帧混合观察（视场 62°、死区 8°）；抬头恢复余量；听觉断线根因（lwIP 8 段门限 + 200 ms 发送时限）修复；监控页右侧总 log、心跳/断线原因、麦克风电平、无字/非人声过滤；台词 80 条全挂情绪；回应等安静、开心累计、音量（网页 + ASR 0x40–0x44）；舵机读重试/去抖/少读。
**未解决 / 下次优先**：
1. **设备网络栈失联**（ping 不通、屏幕正常）：0.4.52 与 0.4.57 各一次，之前伴随本地采集丢块、内部堆历史最低降到约 10–11.5 KB。0.4.57 上连续监测 41 分钟未复现。下次：USB 连续日志 `.private/web-servo-v0439/usb_boot_log.py <port> 3000 logs/...` + 内存采样（脚本思路见 `logs/wedge-v0457/`），复现后看 Wi-Fi/内存报错。
2. 底噪 100 Hz 谐波来源（关屏 / 舵机卸力 / 拔 M12 对照录音），提升信噪比。
3. 舵机总线帧错误约 3–8%（电机出力时更多），软件已兜底，待硬件检查走线/接头/电容/磁环。
4. 回应质量门槛（中英文、字数、人声把握）未做；噪声误识别仍可能触发 neutral/unknown 回应。
5. 待实机确认：ASR 语音调音量、音量重启后读回、舵机忙时 10 Hz 切换、卸力确认后头部正常放松。
6. 服务端偶发 `inference failed`（说话人嵌入 num_frames 0）待查。
**环境提示**：耳朵测试需 numpy/websockets/opuslib_next（系统 python3.14 缺包会报 13 项失败）；本机 websockets 客户端需 `proxy=None`；Mac 在办公室走有线 en10。

## 2026-11-09：舵机“反馈问题”与读取频率（0.4.58–0.4.59，Claude）

现象：网页常见舵机“无有效反馈”。`/v1/telemetry?servo_diag=1` 统计：yaw 13,087 次读 94.4% 成功、pitch 57,803 次 96.5%；失败为 no_reply / 校验 / ID / 长度错，UART 硬件事件 frame_errors 2013、FIFO 溢出 0、缓冲满 0。出错帧缺包头或半截中断 → 总线信号受干扰（帧错误丢字节），非软件丢数据。A/B 否定“听觉编码抢占”“播放导致”；电机 `web_yaw` 运动更新时失败率 4–13%，空闲 0–1%，疑似舵机出力时的电源/地噪声（待硬件确认）。
“一直在读”的来源：①motor_task 每 100 ms 读两轴完整状态块；②`Servo::update()` 静止卸力逻辑每次更新读 ReadMove、每 200 ms 读 ReadTorqueEnable，卸力完成后也不停（俯仰开机姿态后、左右网页转动后一直跑），每轴约 35 次/秒。
修复：`Servo::readTorqueState()`（-1 未知/0/1，HAL 返回原始值）；静止时只有确认读到扭矩 0 才停止轮询（`_rest_released`），下次写目标或显式上扭矩恢复，读失败不算卸力（`tests/servo_torque_release_test.cpp` 新增）。motor_task：读失败立即重试一次；连续两次失败才公布无效，期间保留上次有效值及其时间戳（追踪的 300 ms 过期判断照常生效）；忙（追踪请求、网页左右、手动步进、已启动的 yaw 命令未完成、保持中、开机姿态等待/运动、俯仰请求）及其后 3 s 为 10 Hz，否则 2 Hz。0.4.58 误用被动初始化后的 `isTrajectoryComplete()` 使空闲判断恒忙，0.4.59 改用 `manual_yaw_idle(yaw_command_started,…)`。遥测 `remote.motor.feedback_period_ms/feedback_reads/feedback_retries/feedback_masked`。
实测：0.4.57 每轴 ~35 读/秒、公布无效 6.5%；0.4.59 空闲每轴 2 读/秒、公布无效 0%（总线失败率仍约 2–8%，由重试与去抖吸收）。快速档切换未单独实机验收。Python 84、Node 82 通过。产物 `artifacts/collector-0.4.59-servo-reads/`（3,568,224 B，SHA256 `6975ee741615872b1443971fd8571bed11b30a05e43c30eab50cb7c9e36be7d8`），备份 `backups/ota_1-collector-0.4.5{7,8}-before-*.bin`。建议硬件检查舵机线与扬声器/电源线分开、接头、电源端电容或磁环。未提交。

## 2026-11-09：回应等安静、开心累计、音量控制（0.4.57，Claude）；网络栈失联复现

用户反馈回应太频繁，要求：①有人说话时不出声，下次安静时再说（不是立刻）；②开心 5 分钟窗口内首次触发、之后每 2 次触发一次；③网页和设备端音量控制。
- **等安静**（`hearing_policy.h` `QuietWait`）：选中的回应挂起，后端 tick 的 `speaking` 为 false 且安静 ≥1.5 s、本体不在发声时才播；10 s 内等不到则放弃，log 记“一直有人在说话，放弃”；成功时记“等安静 x s 后播放”。挂起期间不考虑新语句。遥测 `hearing.response{pending,pending_clip,pending_wait_ms,server_speaking,happy_count,happy_needed,happy_window_left_s}`，听觉面板显示。
- **开心累计**（`ResponsePolicy`）：窗口自首个有效开心起 5 分钟；首个即回应，此后每累计 2 个有效开心回应一次；冷却中达到次数则顺延到下一个；无效（非人声/自身/过期）不计数。
- **音量**（`volume_setting.h`、`playback_set_volume`）：10–100，默认 80，存 NVS `collector/volume`（在请求方任务写，不在播放 worker 写）；`POST /v1/control {"target":"volume","value":N}`；Unit ASR 出厂指令 0x40 increase / 0x41 decrease（±10）、0x42 maximum=100、0x43 medium=60、0x44 minimum=10；台词面板音量滑块，显示来源（网页/语音指令/开机保存值）。实测网页设 70/80 生效并 `volume_saved=true`，5 被拒绝；ASR 语音调音量与重启后读回未实测。
- 测试：Python 84、Node 80 通过。产物 `artifacts/collector-0.4.57-quiet-happy-volume/`（3,567,456 B，SHA256 `b92d87a55a45d1c5e2b841122af0fa572b8428a2439886ac14638223d2713f11`），备份 `backups/ota_1-collector-0.4.56-before-quiet-happy-volume-20261109.bin`。
- **未解决：设备网络栈失联第二次出现**（第一次 0.4.52）。0.4.57 开监听约 3.5 分钟后 ping 不通、监控连不上，屏幕正常；此前本地采集丢块成串（会话 439 块），服务器统计丢包率 0.4.54 0%（12.6 min）、0.4.55 0.7%（50 min）、0.4.56 7.3%、0.4.57 15.7%，期间无播放、无追踪/M12/相机。内部 RAM 历史最低降到 11.5 KB（此前 13.9 KB）。所以 0.4.52 的结论“待发队列导致失联”不成立，原因待查。已 USB 复位，`logs/wedge-v0457/usb-continuous.log` 持续录 USB 日志（≤50 min），`logs/wedge-v0457/memwatch.jsonl` 每 5 s 记内部堆/丢块/状态，用于下次失联定位。服务端另有一次 `inference failed`（说话人嵌入 num_frames 0），待查。未提交。

## 2026-11-09：她说话时不听（方案 A，耳朵服务端，Claude）

现象：连续听觉会把她自己播放的台词听回来（无回声消除）；原有保护只是“播放＋1.2 s 尾巴”期间打 self 标记，不触发回应，但仍转写并进 log。用户在 A（说话时不听）/ B（上传参考声道做服务端 AEC）/ C（esp-sr AFE 设备端 AEC）中选 A。实现在 `server/ear/ear_server.py`：收到 self.sounding=true 后，送入 VAD/ASR/声音分类的音频换成静音；录音文件仍保存原始音频，电平表仍统计原始声音；协议、序号、固件不变。新增测试 `test_her_own_voice_is_silenced_before_listening`（耳朵 17 项通过）。离线回放 16:56 段：原先被听回的“呀，我全都要 / 精神小伙”消失。已部署（服务器原文件与上次部署一致，只差此处）。实机 17:57–17:58 连续 4 次情绪回应（#41/#71/#43/#17），台词均未被听回。
附带观察：neutral/unknown 现在会回应，实测约每 11 s 一句；“しよ。/ 그对.”这类噪声误识别也会触发，建议后续加识别质量门槛（语言、字数、人声把握）。未提交。

## 2026-11-09：台词库 40 → 80 条，全部挂情绪（0.4.56，Claude）

用户在 `voice/outputs/20261108-playable-41-80/` 增补 PLAY-041…080，要求导入并让全部台词都能对应到后端情绪（实在不行随机）。依据 `stackchan_playable_80.json` 每条的 emotion/theme 写出 `config/voice-emotions.json`（8 个后端标签全覆盖，原 13 条不变；分布见[连续听觉](continuous-hearing.md)）。`prepare-voice-library.py` 改为按索引条数（须等于 `kVoiceCount`）并把情绪写入 `voice_assets.h` / manifest；固件 `kVoiceCount=80`，`VoiceClip.emotion`、`voice_emotion(id)`，`ResponsePolicy` 在该标签全部台词中均匀随机且避开上一次（`hearing_policy.h` 模板函数，主机测试用假目录）；info 每条台词带 `emotion`，网页台词下拉显示情绪；网页/控制/安装器/上传 ID 上限随目录。前 40 条重新编码后 SHA 与旧文件一致，安装时校验跳过；新 40 条 885,248 B 写入并读回校验（共 80 条 2,288,128 B）。安装器新增“固件目录条数须与本地一致，否则不写”。Node 78、Python 83 通过。产物 `artifacts/collector-0.4.56-voice-80/`（3,564,688 B，SHA256 `ac048af96e5533b42d2fe4b3dbbd8032bcac338278dc948df31dd2bcb9d92330`），备份 `backups/ota_1-collector-0.4.55-before-voice-80-20261109.bin`。
**行为变化**：此前 neutral/unknown（约占识别结果 91%）不回应；现在都会回应，仍受 10 s 冷却、人声、非自身、5 s 新鲜度限制，回应频率会明显上升。未做实际播放验收。未提交。

## 2026-11-09：听觉链路断线根因与修复、监控页总 log（0.4.51–0.4.55，Claude）

**根因（实测）**：断线全部是 `send_failed`（`send_0`），心跳稳定（最大间隔 ≤ 344 ms）。lwIP 发送缓冲 5760 B → `TCP_SND_QUEUELEN` 16 段，排队 ≥ 8 段即不可写；旧固件每 60 ms 发 2 段（self JSON + 音频）= 33 段/s，路径 RTT 超过约 240 ms 就写不进，200 ms 发送时限一到即断。Mac 走有线（en10）往同一后端 ping：平均约 85 ms、最大 312–523 ms、不丢包；Mac→机器人 ping 0.7% 丢、平均 5.5 ms，无线这一跳不是主因。
**A/B（同一办公室、各开连续监听）**：0.4.51 5 分钟断 6 次、丢 1143 块；0.4.53（self 标记仅在变化时发，段数减半）5 分钟 2 次、99 块；0.4.54（再把发送时限 200 ms→1 s、PSRAM 采集队列 32→150 块 = 3 s、旧块门限 0.5→3 s）8 分钟 0 次、0 块，最慢发送 421 ms，同期路径最大 RTT 523 ms。
**弃用 0.4.52**：PSRAM 待发队列 + “先 poll 可写再发”。lwIP 的可写标志会滞后，仍出现只写出帧头的 `send_0`；运行约 55 s 后机器人网络栈失联（ping 不通，屏幕/动画正常，USB 复位恢复），已回刷 0.4.51 再改。补丁留在 `logs/hearing-v0452/`。lwIP 统计在此 IDF 版本无重传计数，未启用；sdkconfig 未改。内部 RAM 仅约 29 KB 空闲（最低 13.9 KB），故不加大 lwIP 发送缓冲。
**0.4.51 起的观测**：遥测 `health.mic_level_dbfs/mic_peak_dbfs`（最近 0.5 s）；`hearing.heartbeat`（tick 间隔/次数/最大间隔、服务端电平/底噪/rx gap）、`hearing.disconnects` 按原因计数、`hearing.last_end`；听觉事件环（speech/sound/gap/error/link，0.4.55 加 response）经 `/v1/telemetry?hearing_after=` 增量读取。
**0.4.55**：情绪回应写入事件：感知文字 + 情绪 + 台词编号，未播放时附原因（忙/过期）。
**监控页**：不再拉原始音频流（`/api/listen`、外放按钮、麦克风/IMU/磁场面板删除，IMU 批量不再请求）；麦克风电平进机身状态；ASR 指令、事件记录、听觉、声音、回应合并为右侧上下贯通的“总 log”，可按全部/ASR/听觉/声音/回应/系统筛选，回应为白字（按台词库显示出处与台词），2 s 内连续丢包合并为一行；听觉面板显示心跳与断线原因。Node 78、Python 83（装齐 numpy/websockets/opuslib_next）全部通过。
**部署**：每版只写 ota_1 并独立 verify，备份 `backups/ota_1-collector-0.4.5{0,1,2,3,4}-before-*-20261109.bin`；当前 **0.4.55**（`artifacts/collector-0.4.55-response-log/`，3,557,456 B，SHA256 `12a46b156e22357570118f8711e23cb45c91174d9edccbf156715a6b1dfd1333`）。布局未做截图验收（无头 Chrome 被实时事件流挂住）。未提交。

## 2026-11-09：连续听觉“音量小、声音事件少”（耳朵服务端，Claude）

离线核对 127 分钟机器人录音（11-08 最长 8 段 + 11-09 全部，`recordings/server/`）：ES7210 输入增益代码写 60、芯片上限 37.5 dB，已是满增益；说话 −31…−38 dBFS，底噪 −42…−45 dBFS 且为 100 Hz 整数倍谐波（CED 把底噪认成昆虫/电器/鸟/心跳）；说话仅高于底噪 7–19 dB。CED 听出打字 31 个窗口（p 0.2–0.66，中位 +6.7 dB），全部被为 Mac 麦克风标定的 15 dB 门挡住；11-08 的 146 分钟只有 17 个声音事件，无打字/呼吸。麦克风通道与说话低频正常；11-09 会话丢包约 7%（成串，设备侧序号缺口）。
修改（`server/ear/ear_server.py`，只对 `stackchan*` 来源，Mac 不变）：打字/键盘/叹气/呼吸/喘/吸鼻/咳嗽/清嗓/喷嚏标签在非说话时，高于底噪 ≥5 dB 且 p≥0.30 即可开始事件（1 个窗口）；像底噪的种类保持原门。audio 回执加 `sound_profile: device|default`。离线回放：仅在用户打字的两段多出 9 个打字事件，其余不变；本机完整服务回放 16:36 段：机器人来源 7 个打字、Mac 来源 0、说话一致；另 4 段无打字录音无新增事件。已用 `deploy.sh` 部署（先 dry-run，服务器原文件与 HEAD 一致），公网回放（来源 `stackchan-replay-typing`）同样 7 个打字。`tests/test_ear_protocol.py` 新增 3 项；**耳朵测试需 numpy/websockets/opuslib_next，系统 python3.14 缺包才显示 13 项失败**，在装齐依赖的环境中 16 项全部通过。
待办：找底噪来源（关屏/舵机卸力/拔 M12 对照录音），提升信噪比；呼吸/叹气 CED 几乎没有 ≥0.3 的窗口，可能还要靠降噪。未提交。

## 2026-11-09：抬头（俯仰）无效修复（0.4.50，Claude）

现象：网页调角、开机姿态都不动。遥测 `pitch_setting.state=feedback_unavailable`、`request_seq=0`、反馈有效、实测 −3.4°（raw 631），保存 15°。根因：俯仰软件下限 5°，`canStartFromFeedback` 只允许从下限以下 2°（`kPitchSettingLowerMargin=20`）向内恢复；头卸力/断电后靠在 −3.4°，每次判不能起步，5 s 后放弃。修复：余量改 10°（`kPitchSettingLowerMargin=100`，`Servo::canStartFromFeedback` 上限 20→100），目标仍须在 5–85°、命令仍从限位起（首个目标即 5°，约 8.4° 向上阶跃）。`tests/pitch_setting_test.cpp` 加 −3.4°/−5.0° 可起、−5.1° 拒绝，先失败后通过；全量 Python 仅已知 `test_ear_protocol` 13 项失败。产物 `artifacts/collector-0.4.50-pitch-recovery/`（3,548,032 B，SHA256 `43828ae4915081c43fef02e46e17ca01e1b9235328624afd9f79d605336a4ee1`），备份 `backups/ota_1-collector-0.4.49-before-pitch-recovery-20261109.bin`，只写 ota_1 并独立 verify。重启后开机姿态 −3.4°→14.0°（目标 15°，误差 −1.0°，峰值负载 135‰，最终目标后 575 ms 卸力）。网页调角待用户确认。未提交。

## 2026-11-09：追踪混合观察（0.4.49，Claude）

用户指出 M12 只有 6–7 帧/秒，人在走时按单帧去追会追在半路，选择“混合”方案。每次观察用连续两帧（头静止时）：两帧间移动 ≤2° 视为静止，直接一次转到人脸；在移动则提前 速度×600 ms（最多 10°）；带提前量时预测会自己回到中央 8° 内则不转；两帧相隔 >600 ms 时第二帧只作新参照。头静止且人脸一直在画面中时，上一帧就是参照，离开死区当帧即可决定；只有转完后需多看一帧（约 0.15 s）。像素标定只取无提前量的转头。遥测 `last_turn` 加 `lead_deg/face_motion_deg/face_motion_dt_ms`，参数 `look_frames/still_deg/lead_ms/max_lead_deg`。14 组追踪测试与 Node 73 通过。产物 `artifacts/collector-0.4.49-tracking-hybrid/`（3,548,016 B，SHA256 `a8b3be9fece94b15754c4188d549ca074b0e83feab5471791f2885ba235675e2`），备份 `backups/ota_1-collector-0.4.48-before-tracking-hybrid-20261109.bin`，只写 ota_1 并独立 verify，在线确认。待用户实测；提前时间 600 ms 与上限 10° 为初值。未提交。

## 2026-11-09：追踪视场标定与死区收窄（0.4.48，Claude）

用户实测 0.4.47“还行，人走动时头跟得晚”。0.4.47 的 22 次转头无保护锁存，逐次标定 20 个样本，最近一次 10.3 px/°（假设 12.8），最后一转误差 −0.9°。原因：视场假设 50° 偏窄，128 px 死区实际约 12.4°，且每次只转到约 80%。0.4.48：`kFovTenths` 620（10.3 px/°）、死区 8°（82 px）、遥测加 `calibration.mean_px_per_deg`；网页文字同步；13 组追踪测试与 Node 73 通过。产物 `artifacts/collector-0.4.48-tracking-fov62/`（3,547,328 B，SHA256 `255795fa85ccc0bb90315fca94d3ae4edacef2f82ae8aa8dac4d20e89c7a4762`），备份 `backups/ota_1-collector-0.4.47-before-tracking-fov62-20261109.bin`，只写 ota_1 并独立 verify，重启在线确认参数。待用户再试手感与 `mean_px_per_deg`。未提交。

## 2026-11-09：追踪改为“看一次、一口气转到位”（0.4.47，Claude）

用户决定：追踪按人类转头的方式，看一次就一条命令转到人脸，确认到位后再看；人脸偏离中心一定角度后才启动；丢脸回中；放开单条命令 30° 上限；无进展预算按距离放宽；预补偿舵机停在目标前 3–4°。

- **策略**（`collector/face_tracking.h` 重写）：新鲜人脸偏离 >10°（按假设视场 50°＝12.8 px/°，128 px）→ 瞄准 = 实测 + 像素换算角度，命令再多给 3°；夹在锚点 ±60° 与舵机限位内，被夹到不足 5° 视为 at_limit 不发（审阅发现：边缘 3° 小命令会误触发无进展锁存）。转动中忽略画面；轨迹结束后实测 1° 内稳定 250 ms 判到位（或 1 s + 30 ms/° 截止），只用到位 300 ms 之后收到的帧做下一次观察，无固定 2.5 s 间隔。丢脸（num 0 或 700 ms 无帧）1.5 s 后回锚点一次（已在 5° 内不动），人脸回来继续。速度：20° 内 1000，更远按 1000×20°/距离（最低 300），峰值命令速率不超过已验证的 20° 档（弹簧模型任何距离约 0.6 s 走完，43° 全速峰值约 390°/s）。保留：反馈过期/无效取消、单样本跳变 >25° 锁存、锚点范围锁存、无进展（2 s + 20 ms/° 累计活动时间内走不到 min(5°, 距离)，跨观察保留，关开才清）、快速关开不丢取消、手动接管。
- **遥测** `interaction.tracking`：`policy=one_look_one_turn`、`moving`、`deadband_deg/px`、`assumed_fov_deg`、`precomp_deg`、`last_turn{start/aim/command/arrived/error_deg, settle=stable|deadline, speed, returning}`、`calibration{px_per_deg, samples, assumed_px_per_deg}`（每次转头后首帧实测像素位移/角度，用来标定视场）。去掉 `max_step_deg/min_interval_ms/slot_deg`。
- **网页**：追踪状态名、按钮提示、M12 诊断说明同步；舵机反馈“新鲜”判断允许反馈时间戳比遥测晚 ≤100 ms（固件先记遥测时间再取舵机快照，实测最多晚 24 ms，曾导致左右/仰角按钮每 1–2 s 闪灰；不是 Wi-Fi 断）。
- **测试**：`tests/face_tracking_test.cpp` 重写为 13 组（含 20 ms 节奏/140 ms 帧的真实节拍、两种丢脸回中、截止、无进展、边缘、真实 Servo 斜率/堵转/反向），全部通过；Node 73 通过。全量 Python 中 `test_ear_protocol` 13 项因听觉协议实现缺失失败，与本次无关。
- **部署**：`artifacts/collector-0.4.47-one-shot-tracking/`（3,547,088 B，SHA256 `b308c582de5a73caba774b1f2cea4173b17255b81c941fc0377563d7547573d7`）；备份 `backups/ota_1-collector-0.4.46-before-one-shot-tracking-20261109.bin`（与归档一致）；只写 ota_1/0x510000 并独立 verify，重启后在线 FreedomX20A 192.168.88.5，遥测确认新策略。机器人此次在 `/dev/cu.usbmodem101`，同机另有两块 Espressif 设备，按序列号核对后未触碰。构建日志 `logs/tracking-v0447/build.log`。
- **待实机验收**：人脸实测转头手感、`last_turn.error_deg`、长距离低速转动是否中途停住（跟随误差变小可能触发根因所述的小误差停转）、`calibration.px_per_deg` 与假设 12.8 的差距（据此改 `kFovTenths`）。未提交。

## 2026-11-08：网页舵机根因确认（0.4.39–0.4.42，Claude 接手）

本轮由 Claude 接手 codex 的交接。四次只写活动 ota_1 的刷机均先完整备份上一版并独立 verify（备份在 `backups/ota_1-collector-0.4.38/39/40/41-before-*.bin`）；一次 `write_flash` 因 USB 中途掉线失败，设备回落到 ota_0 原厂固件，降速 460800 重写并校验后恢复。当前设备 **collector-0.4.42**，应用 3,536,272 B，SHA256 `c0839580e94891400b88df188f9ca4f273ba7a8183b7e04256290a8068dc1501`。

- **软件缺陷（已修）**：原厂 `Servo::update()` 在最终目标写入后的第一个空闲 tick、`ReadMove=0` 即关扭矩（现场 35.4 ms，主机复现 20 ms），不核对实测位置。0.4.39 起改为到位门控：最后一次目标写入后至少保持 500 ms，每 200 ms 读实测，误差 ≤1.0° 才释放，最多 2000 ms。主机测试 `tests/test_servo_torque_release.py` 复现并覆盖。网页 yaw 缺省弹簧速度 100→600（与实体遥控相同）；离线仿真 `.private/web-servo-v0439/spring-sim.txt` 显示 speed 100 时目标每 2–7 tick 才前进 1 编码步。
- **俯仰网页调角验收通过**：10.6°→15.0°（约 9 s 到位）、15.0°→10.0°（约 7.6 s），实测轨迹保持，卸力在最终目标后 ~0.7 s。证据 `logs/web-servo-v0439/pitch-15.log`、`pitch-10-restore.log`。
- **yaw 不到位的根因是舵机输出不足，不是代码路径**：0.4.40 起每 100 ms 反馈读完整状态块（56..70），并读两轴 EEPROM 9..27。两颗 SCS0009 参数相同：P15 D15 I0、启动力 30‰、死区 1/1、最大扭矩 1000、限位 20..1003；总线 5.1–5.2 V、温度 28–32 °C、电流寄存器恒 0。扭矩保持 2 s 期间 yaw 负载（PWM 占空比）停在 **≈15×误差步数**：误差 17 步→255–270‰，12 步→180–195‰，位置不再变化。网页路径 −15°/+15° 各走了 31/36 步后分别停在离目标 17/12 步处；用户在页面发出的 +25° 一路顺畅；网页 ±5° 只能动 0–6 步。遥控同款路径与网页路径下发的位置写完全相同，遥控"可靠"是因为摇杆幅度大且由人闭环推到位。用户手感"有阻尼但没卡住"，机械自由。证据 `logs/web-servo-v0439/ab-yaw_*.log`、`yaw-left-600-diag.log`、`state-0440*.log`。
- **调研**：官方套件即 Feetech SCS0009（额定 0.7、峰值 2.3 kg·cm@6 V，无积分项），裸机头、大幅动作下没问题；社区重头版本改用 Dynamixel XL330 或 STS3032；Feetech 用户用 FD 工具调 P/启动力/死区。详见 `docs/servo-control-handoff.md` 新节。
- **0.4.41/0.4.42 新增诊断面板**：`/v1/motion/yaw-step` 支持 ±1…30° 整数步进、`speed`、`path=web|remote`；`POST /v1/servo` 排队给 motor task 读写舵机 EEPROM（P/D/I/启动力/死区/最大扭矩，先读后写读回、可恢复出厂值）与运行时参数（位置写入 time/speed、弹簧覆盖、卸力保持/容差）；遥测新增 `tuning`、`motion_summary`。网页"舵机 → 参数/详情"对话框全部开放给用户试验。Python 主机套件 66 项通过 + 13 项 `test_ear_protocol` 仅在带 numpy 的 `SOUL_MAC_PYTHON` 下通过 + 4 旧跳过；Node 69 项通过。
- **其他**：机身相机默认关闭、网页可开关并保存（`camera_on`）；摘要页第一行显示所连 SSID；机器人首选网络已通过 USB 改为 Pixel 热点（2.4 GHz 信道 1，IP 10.159.200.x），`.private/stackchan-monitor.json` 地址已更新。热点双频，Mac 在 5 GHz、机器人在 2.4 GHz 可互通；Mac 走 Pixel USB 共享时不可达。

### 0.4.43：左右改为 20° 档位，网页左右验收通过

设备 collector-0.4.43（3,537,360 B，SHA256 `b6cddd8f5635d7b2e0a68588670cdf51bfd770c7210a33ff4fdc1352738e7b9a`），刷前完整备份 0.4.42。用户试过 yaw P30/启动力200/死区2 不理想并已恢复出厂；决定改为按档位转动。网页左右每次一档 20°、速度 1000、中心 ±3 档（±60°）、新增回中；yaw 卸力容差 6°、最长保持 1.5 s。实测（出厂舵机参数）：

| 命令 | 目标 | 实测停点 | 备注 |
|---|---|---|---|
| 左 ×3 | −20 / −40 / −60° | −17.1 / −36.8 / −57.1° | 每档约差 3° |
| 第 4 次左 | — | 拒绝（409） | 档位边界 |
| 回中 | 0° | −2.8° | 峰值负载 675‰，最终目标后 669 ms 卸力 |
| 右 ×3 | 20 / 40 / 60° | 16.8 / 36.2 / 56.5° | 每档约差 3–4° |
| 第 4 次右 | — | 拒绝（409） | 档位边界 |
| 回中 | 0° | 3.1° | 峰值负载 735‰，654 ms 卸力 |

档位方案在出厂参数下每次都可靠转动，停点误差 3–4° 符合"方向即可"的要求。证据 `logs/web-servo-v0439/play-session.log`（含每条动作的完整轨迹）。人脸追踪改为按方向走档的工作待做。

### 0.4.44：人脸追踪改为按方向走 20° 档，实机通过

设备 collector-0.4.44（3,537,440 B，SHA256 `49003fb776add2e55bc5a96575107cc3499ce33e286b0df3fe2ef4e5c465da70`），刷前完整备份 0.4.43。追踪策略：人脸中心偏离 >130 px 才动、每条命令走到最近档位的下一档（20°，speed 1000）、间隔 2.5 s、锚点 ±60°、2 s 内进展 <5° 锁存、相邻样本跳变 >25° 或人脸中心跳变 >260 px 视为异常。真实 Servo 主机回归 3 组通过（档位时序重写）。

实机 40 s（用户在摄像头前左右移动）：人脸中心 552 → 命令 1 目标 20°，yaw −3.1→16.8°，人脸回到 397 进入死区停止；479 → 命令 2 目标 40°，yaw 36.5°；147 → 命令 3 目标 20°，yaw 22.8°；154 → 命令 4 目标 0°，yaw 3.1°。四条命令全部朝人脸方向、停点差 3–4°、死区内不抖动，无 fault_no_progress。证据：`logs/web-servo-v0439/`（probe 输出）与遥测 `motion_summary`。用户此前开启旧 1° 追踪立即触发"舵机未移动保护"，与根因一致。

### 0.4.46：单一配置端 `POST /v1/control`（2026-11-09 已刷入并验收）

用户对 0.4.45 的处理器溢出事故提出"为什么这么多路由，只保留一个配置端"，并决定 Android 观察台之后按网页重做、不再兼容。本版把 12 条 JSON 控制/配置 POST 合并为 `POST /v1/control {target,...}`，删除 `GET /v1/wifi`、`/v1/eyes`、`/v1/eyes/status`（内容本就在 info/遥测中），`/v1/eyes/assets` 与 `/v1/voice/assets` 合并为 `/v1/assets`（按头选择）；主端口从 22 条路由降到 7 条，`max_uri_handlers` 改由路由表长度决定。镜像 3,544,928 B，SHA256 `2de7205879eeec34d816d37fa24bf95ea36d90794b3c1b34a376bdc6516c56ca`（`artifacts/collector-0.4.46-unified-control/`），刷前备份 0.4.45 到 `backups/ota_1-collector-0.4.45-before-unified-control-20261109.bin`，USB 写入 + 独立 verify 通过，启动日志连上 FreedomX20A（192.168.88.5）。

实机验收（`.private/web-servo-v0439/verify_control.mjs`，日志 `logs/web-servo-v0439/verify-control-0446.log`）：17 条旧路由全部 404；缺少 target / 未知 target / 字段错误 / 截断 JSON / 多余字段均 400，旧 `X-Boot-Id` 409，超长 800 B 400；12 个 target 全部按原语义应答：remote 200、yaw_step slot 1 → 实测 15.6°（误差 -4.4°）、slot 0 → 1.5°、pitch 202、camera 开后 `/v1/frame` 200、关后 503、servo refresh 读回出厂 EEPROM、wifi scan 8 个结果、m12_mode/m12_view/interaction/hearing/eyes/voice_play 均 202 且遥测状态随之变化。观察台重启后 `/api/state` 在线、`capabilities.unified_control=supported`，`/api/control {action:motor}` 经单一端点返回 202。Node 72 项测试通过。

附带发现：本机 Node/Python 直接建的 TCP 连接到机器人返回 EHOSTUNREACH，而系统 `/usr/bin/curl`（观察台与脚本使用）正常；之前 asyncio 子网扫描"找不到机器人"是同一原因，不是网络问题。

### 0.4.45：已保存网络列表、扫描与本体 Wi-Fi 设置页（2026-11-09 已刷入）

首个 0.4.45 镜像刷入后进入重启循环：新增 `/v1/camera`、`/v1/servo`、`/v1/wifi` 后 HTTP 处理器超过 `max_uri_handlers=20`，`ESP_ERROR_CHECK(httpd_register_uri_handler)` 触发 abort（USB 日志 `ESP_ERR_HTTPD_HANDLERS_FULL`）。改为 32 后重建（3,549,296 B，SHA256 `d1ca85046ce90a3f39fac6d5139b99514208175774dee46505103ccead19b66a`，`artifacts/collector-0.4.45-wifi-profiles/`），在重启循环中抓住串口写入并独立 verify；刷前已备份 0.4.44（`backups/ota_1-collector-0.4.44-before-wifi-profiles-20261109.bin`）。机器人这次接在另一个 USB 口（串口名随口变化），同机还接着另一台序列号不同的 Espressif 设备，按身份核对后未触碰。USB 启动日志：`Wi-Fi profiles: 1 saved`、连上 Pixel（信道 11）、`HTTP ready`、5 s 心跳正常。**实机验收（2026-11-09 办公室）**：用户用本体屏幕 Wi-Fi 页扫描并输入数字密码加入办公室网络，机器人连上 FreedomX20A（192.168.88.5）；`GET /v1/wifi` 显示列表 `*FreedomX20A, Pixel`（新加网络为首选）、出厂 1 个、扫描结果 8 个且已保存项带标记。观察台地址已改为办公室 IP。内容：`wifi_profiles` 单值网络列表（最多 6 个，首项最先，首次启动自动迁移旧 `wifi_primary`/`wifi_ssid`），`GET/POST /v1/wifi`（列表/添加/删除/设首选/扫描/重连，不返回密码），遥测 `wifi` 对象，USB `cmd=wifi` 改为保存并重连不重启；本体屏幕新增 Wi-Fi 页（配对页左滑：自动扫描、`*` 已保存、`#` 加密、Rescan）和数字密码键盘（加密网络至少 8 位）；观察台新增"网络"对话框（数字密码输入）。主机测试：`test_wifi_profiles` 通过，Node 72 项通过。刷入后需验证：列表迁移正确、扫描结果、屏幕页点选与键盘加入、网页添加/删除。

**未完成**：0.4.45 刷入与实机验收；原“热点管理（删除其他网络、只保留 Pixel）尚未实现。原文：yaw 轴在出厂参数下无法完成小步进；待用户用参数面板试调（建议先 P30/启动力200/死区2），或改外环/换更高扭矩舵机；热点管理（删除其他网络）尚未实现；人脸追踪仍关闭。不要把 `trajectory_complete` 当到位，看 `motion_summary.error_deg`。

## 2026-11-08：0.4.38底部功能条与追踪只读诊断

本体已部署0.4.38，应用3,506,432 B，SHA256 `341dd48978816ddd6a1499624c296a683f354270cdae0fa63f181ebd12ea7503`。固定IDF5.5.4；分别核对0.4.36、0.4.37旧应用槽的完整备份，仅写活动ota_1并独立verify。SD媒体、NVS和校准不由刷机写入；保存10°、v14自然眼睛恢复，各模式默认关闭。

- 本体新增320×3 px顶层状态条，自左至右：追踪白、连续听力蓝、声音作业红、可见眼睛/心/X绿、认人琥珀；1 px黑色分隔，关闭段近黑，关屏隐藏，不拦截触摸。白/蓝/琥珀表示启用请求，红包含延时/加载/播放/清理，绿色表示显示对象可见；不冒充机械、网络或物理声光测量。状态在LVGL锁外采样，仅变化段重绘。**用户已确认0.4.37实屏“看到了，显示正常”**；0.4.38未再改此部分。
- 真LVGL主机渲染检查五色/几何、首页/摘要/log/M12覆盖、底部滑动、关屏恢复、同状态零刷新及单段≤192像素刷新通过；三款眼睛720帧解码/渲染、reaction benchmark通过。预览保存在`.cache/function-ribbon-preview/`。
- UART唯一所有者motor task增加有界只读采样：每条追踪命令最多读三次40..47（发令前、首次位置写后、取消前），保留寄存器扭矩与目标、写事务证据；启动静止时每轴一次读9..27的角度限位/相位/死区。HTTP只取缓存，错误保持未知，未添加EnableTorque或EEPROM写入。IOTimeOut临时2再恢复不代表整笔UART事务≤2ms。
- 用户确认曾用手转动，之前raw452→465不作为主动运动证据。0.4.37受控5.567秒窗口只有一条1°命令：实测raw465/1.2°→目标0.2°；首次写raw464后使能寄存器0→1，2秒保护前目标raw462/使能1。实测465短暂466、卸力回465，没有朝目标进展。硬件限位20/1003、phase1、死区1/1，排除本库的零限位PWM配置；使能寄存器1仍不证明物理输出扭矩。证据`logs/tracking-v0437/tracking-live-motion.json`。
- 同窗事务差分可归出47次位置写，约2秒只推进固定20ms模型约0.94秒。真实Servo回归复现现场起点在60ms调度、旧speed0时2秒前停raw462；仅把追踪speed改200，新候选≤1.6秒提交完整raw461。42组方向/跨零检查角度命令每次≤0.1°、不超目标，2秒取消后无新写；这是下发时序改善，**不证明机械无进展已解决**。1°单步、±10°锚点及累计2秒保护均未变，未放大角度或修改舵机配置。
- 0.4.37 Python77项（73通过/4旧跳过）、Node63通过；0.4.38唯一曲线改动的追踪3组回归及独立复核通过，两版完整固件构建通过。没有把0.4.38称为重新跑过全部主机套件。

0.4.38首次15.143秒窗口没有新的人脸框/0条命令，单独封存为`tracking-no-frame.json`。用户再次就位后，最终5.699秒受控窗口发出一条相同1°命令：起点raw465/1.2°、目标0.2°；首次写后使能0→1，取消前已读回**完整目标raw461且使能1**。实测465→466（反方向一格），卸力后回465，仍没有朝目标进展；累计2秒保护停止。由此确认完整目标提交改善，但不能把机械无进展仅归因于曲线过慢，**追踪未修复/运动验收未通过**。当前追踪关闭、M12待机、听力关闭，v14眼睛与保存10°保留；不继续自动反复试转，不增幅或延长保护。后续需新的区分性证据再排查，不据此宣布舵机损坏、缺电或负载为根因。状态条已由用户验收。证据`logs/tracking-v0438/{deployment.json,boot-check.json,tracking-live-motion.json}`；原始现场日志与固件仅存本地，不提交。

### 用户指出仅遥控路径可靠后的复核

暂停继续刷机、自动动作和追踪改动提交。状态条已验收，保留；当前设备0.4.38、追踪/认人/听力关闭、M12待机、v14眼睛、保存10°。下一轮不得沿用“网页yaw已正常”的假设。

- 0.4.20网页yaw没有完整±5°机械到位验收：起始4.3°/raw475，共接受5次请求，最后目标−0.7°但实测3.4°/raw472，软件已是trajectory_complete；用户仅说“有点动作”。停用/启用发生在首个请求之前，之后无停止记录且rx_packets=0，不能解释成测试提前取消或遥控抢占。用户“网页10°到位”指俯仰，不能转移为yaw证据。
- 已知有效基线是遥控：0.4.22→0.4.23用户“左右正常”；更早0.3.1临时±5°/speed80记录实际yaw1.5…6.5°、用户“能正常小幅转动”。早期原始JSON当前缺失，后一项为保存验证文档证据，不是本轮复测。不能仅以遥控“大幅/高速”解释新路径失效。
- 历史7700e7d（0.4.23；0.4.22候选未单独提交）实际motor_task与其父5b8036d（0.4.20）的yaw执行相同：遥控绝对目标/speed600、首包物理同步、连续改目标保留弹簧动量并持续update；网页相对±5°/speed100，每次反馈重置；追踪相对1°/speed0→200且有累计2秒保护。底层均调用同一位置写路径，不能声称存在已证实的遥控专属硬件驱动。
- 历史自动卸力只检查软件曲线结束及ReadMove=0，不检查实测目标误差。可能过早卸力仍是假设，缺少该网页窗口的逐写/扭矩记录。使能1、ACK、目标读回、trajectory_complete均不等于实际到位。
- 现有face_tracking主机测试虽运行真实Servo，但未给关闭默认HAL的动画库安装时钟钩子；其墙钟恒0，不能据此证明与连续遥控retarget等价。隔离7700e7d源码，合成2→4→6°/speed600/40ms更新对照表明，开启真实墙钟后轨迹及单写增量不同。该结果仅证明模型覆盖缺口，不是硬件根因；证据`.private/historical-motion-review/retarget-clock-comparison.{cpp,txt}`。

后续先建立同一受限目标、同一速度、同一更新与释放规则的共同执行基线，对照旧遥控和新入口；验收标准必须包含实测方向与到位，再接回人脸策略。不得继续凭测试通过推定机械通过，也不继续无证据调参或归咎硬件。

## 2026-11-08：0.4.36水平追踪、模式遥控与遥控器默认启动

本体已仅写活动ota_1并独立verify，应用3,502,464 B，SHA256 `9c3902a327f85f4528fb7175623ee01c56dec3fd053a9b1448c0e11d7aa347c4`。固定IDF5.5.4、原分区/依赖/身份库/保存10°保留。原0.4.34及中间0.4.35均完整应用槽备份并核对归档。操作和边界见[水平追踪与模式开关](face-tracking-modes.md)。

- ASR open开启水平追踪、close关闭，原随机台词迁到next并保留800ms和眼睛联动；start/stop保持连续听觉。turn on/off在UART接收处顺序执行，LVGL只应用预览，修复旧延迟回调覆盖后续open及丢失turn off的问题。
- 实体遥控左/右/上/下切换追踪、认人主人听觉、连续听觉、高级语音预留；四向不再直控舵机，中心开关屏保留。回中重新使能、首包/换源/nonce/断链偏转/斜向保护；16字节协议没有页面标记，必须说明使用摇杆页，不能宣称已排除IMU输入。
- 固定实测锚点±10°，每条轨迹从有效反馈出发≤1°，250ms节流；反向先取消，再从新实测起点生成目标。5°/s为单条模型值，不是跨取消同步的全部寄存器写或物理速度保证。累计活动命令2秒无≥0.3°进展锁存；暂停保留预算，反复换向不能重置。无效/过期反馈、旧/重复框、模式代际变化、丢脸、手动接管均受保护；cancel释放扭矩，不是物理锁止。
- 任意已登记人脸经两次独立新鲜结果确认后启动现有听觉情绪回应；回应所有人、声纹仅显示。显式stop后不自动重开，关闭认人只关闭自己仍持有的会话；手动start/录入接管受到保护。后台检测/认人与屏幕预览分离，M12请求代际已应用后才采纳结果。高级模式只有预留意图，没有问答/网络/音频动作。
- Python76项（72通过/4旧依赖跳过）、Node63通过，新增sender3组真实生产逻辑主机测试通过；最后反向/活动预算改动另跑tracking3项和pitch回归。独立复核修复无进展重试、暂停误报、反向卡住、ASR模式延迟、NVS歧义写及LVGL加载锁；当前无剩余审阅阻塞项。构建与主机测试不替代机械方向、真人主人认出、实体ASR及长时热稳定验收。

遥控器默认Channel6/Receiver ID1，开机直接摇杆页，A键循环摇杆→IMU→设置→摇杆；设置页退出时保存显式有效变化，默认读取/未改值零应用NVS写。版本化单blob、写后读回保留错误语义，不把Flash错误当回滚、不擦NVS。固定IDF隔离构建1,176,128 B，SHA256 `9ff96be02b5cb6d52d8f98538bc2abe5071f304f820bd676e748a43295df444b`。实读ESP32-PICO-D4/4MB、MAC匹配此前实时sender、唯一factory0x10000；完整4MB备份及独立verify后只写应用，首次启动前对整片预期镜像独立验证通过（应用擦除范围外不变）。921600/460800切速后无串口回复，115200成功；不能归为Flash损坏。重启自动收到CH6/ID1心跳；**用户确认电源开关关开后直接进入摇杆页**。改成其他有效设置后的真实持久化未额外操作，主机已覆盖。

中间0.4.35现场记录：第一轮看到首个1°目标即停止，实测角未变，用户答“没有明显转动”；第二轮目标+1°后人脸回中央提前取消，活动上界约0.554s，后墙钟2秒无进展误报。该窗口不能证明舵机堵转；真实Servo模型确认1°可以产生编码输出。0.4.36改累计活动预算，并补回中长暂停、反向及反复反向冻结反馈回归。认人模式24样本窗口正常请求/后台显示眼睛，但未返回人脸身份，不能称已认出主人。0.4.35浏览器1366×768一屏无溢出/脚本错/自动POST，新按钮与next映射可见。最终0.4.36在线补验见下文。

最终0.4.36补验：HTTP确认新版本、全部新模式开机关闭/保存10°，高级预留开关不触发听觉/M12/舵机。连续听觉24样本/8.593秒均connected、增加143包、丢块/重连0，眼睛增加12帧（前2样本初次加载未显示，后22样本可见）；停止后听觉关闭、M12待机，v14自然循环恢复。遥控继续收到CH6/ID1心跳。

**水平跟随实机未通过，当前已关闭。** 最后一次5.450秒受控窗口中，人脸持续位于画面左侧，目标-2.8°→-3.8°，单条命令累计活动2秒后fault_no_progress；反馈始终raw452/-2.8°。只读缓存确认最后目标raw450成功ACK，随后取消扭矩写入成功；当前CW/CCW死区均1步。不能据此判定机械堵转，也没有继续重复动作或放大幅度。静态路径缺少显式EnableTorque(1)，WritePos只写42–47；其是否由舵机固件自动恢复扭矩尚未确认。真实Servo主机测试使用外设替身，只证明动画/取消/预算，不能证明实际扭矩或运动。下一步须以严格寄存器模型和只读/受控证据核对扭矩启用及目标量化，再验方向；不能把当前代码/烧录通过写成追踪效果通过。

本机证据：`logs/tracking-v0435/`（含remote-deployment、remote-live、两次受控动作及认人窗口）、`logs/tracking-v0436/`（最终构建/测试/部署/在线检查）、`logs/remote-center-v1/build-20261108-180352-418122/`。身份、绑定及原始串口只在.private，备份/镜像不提交。

## 2026-11-08：0.4.34自然眼睛循环已部署，并发短窗口通过

最终collector-0.4.34已构建并仅写应用槽，独立verify通过。镜像3,488,640 B，SHA256 `7269e08487f52ad024dd466c79bb3a74e9ae6afaaf2552bc5faaf7043d586b88`；静态RAM173,163 B，固定IDF5.5.4、原分区与依赖锁保留。

实机总窗口110.725989秒/188样本，其中81.625482秒/120样本持续听觉连通且眼睛可见：rendered+162、decoded+187，出现7种自然表情及idle，SD reads+8；听觉1,360包、丢块0/重连0，SD/眼睛失败0，相机/音频错误及HTTP音频跳块0。台词job1完整提交214,272/214,272 B，结束输出关闭，播放/收尾错误0；DMA free最低17,643 B、largest最低9,728 B，串口panic/DMA申请失败/显示锁错误均0。**这是短窗口通过，不是长期稳定结论；并发相机仅约2.254次采集尝试/秒（错误0），仍低于10 fps目标。**

新增本体自然循环：随机等待2–7秒，同款29种非idle表情按权重选取、播放1–3轮后回idle，不连续重复；手动及ASR/听觉优先，切页/熄屏不补播，恢复原首页关闭循环。复用原worker、双资产缓存和帧池，不新增任务、大缓存或NVS写入。操作与边界见[眼睛说明](soul-eyes.md)。

“单次播放露出笑面男”已在主机用真实v14资源复现：1.5秒首帧预算遇慢读卡/调度时过期，generation变化又立即隐藏旧完整眼睛帧。0.4.31将手动首帧预算放宽至有界10秒，语音仍为1.5秒；正常播放→idle交接保留旧完整帧，显式门控照常立即隐藏。用户对0.4.31反馈“好像是正常哦”；实机v05/v14通过，v18手动读取遇SPI私有RX DMA申请失败，旧帧保持且未panic，SD进入不可用。此反馈不是三款及长期并发全部通过。

0.4.32收紧日志ring为实际可见9行、屏幕拼接缓冲4096→1089 B，源级容量减少5,790 B，链接`.bss`实际减少5,784 B；新增`health.dma_free/dma_largest_free`。固定IDF5.5.4，未改SDSPI/SPI成功路径。应用3,488,640 B，SHA256 `fa26ccea82e20365e5fec89d24a4a8a408543eb70005adf5c2dd5c2547178664`；核对身份/活动ota_1/CRC、完整备份匹配旧0.4.31后，仅写0x510000并独立verify通过。Python70项（66通过/4既有跳过）、Node53通过。

历史性能失败保留：0.4.32的166.640秒/281样本窗口三款自然/手动/回idle及台词通过，SD读取11/失败0，DMA free最低20,727 B、最大块最低7,680 B。但随后23.379秒/24样本听觉窗口只增加345包、丢弃240块；眼睛始终idle/source=none、SD读取未增加，没有观察到自然表情推进。前一窗口还出现单次解码墙钟时间27.944秒，不能把局部通过写成调度性能达标。

进一步受控基线：0.4.32听觉开启45.660秒/40样本（39个connected），眼睛rendered/decoded各仅增加1、SD读取不增，听觉增加680包/丢弃377块/重连0；暂停听觉后的16.180秒，眼睛rendered增加41、decoded增加45，SD读取增加1次/581,529 µs。这支持继续检验任务调度，尚不是单一根因证明。0.4.33仅将eyes worker从core0/priority1调至core1/priority3，栈、缓存及静态RAM173,163 B不变；已构建、完整备份0.4.32并仅写应用后独立verify。镜像仍3,488,640 B，SHA256 `3448ff794f891bb4b25d26917be0f5cec4f24645e1155b3c6a2d3f3017493aca`。但80.609秒并发窗口仅rendered+6/decoded+13，听觉889包/丢弃2,049块，台词失败`display_bus_unavailable`，SD失败仍0；调高eyes任务本身未解决问题。

临时USB任务运行时间采样进一步支持core0调度竞争判断，诊断代码已在0.4.34构建前移除。最终将原不绑核的听觉worker固定到core1/priority4；eyes维持core1/priority3，LVGL core0/priority3，JPEG core1/priority2，栈与缓存不变。最新主机Python70项（66通过/4既有跳过）、Node53通过；0.4.34实际浏览器1366×768检查无脚本错误、横向溢出或意外控制请求。Loading中手动Stop的12样本均保持可见，旧请求未复活、SD失败0。停止串口采集后确认同一boot仍在线，末态v05自然循环开启、听觉关闭。持续听觉、更多音画组合与长时间运行仍需继续观察。

最终证据见本机`logs/eyes-v0434/{result-summary.json,cancel-check.json,browser-live.json,final-state.json}`及浏览器截图；历史对照在`logs/eyes-v0432/{deployment.json,live-check.json,hearing-concurrent.json,concurrency-baseline.json}`与`logs/eyes-v0433/live-check.json`。原始私密日志不提交。保存10°、遥控俯仰禁用、ASR open800ms及M12手动策略保留。详细验证见[验证记录](collector-validation.md)。

## 2026-11-08 PC紧凑观察台与优化评估

网页已改四列三行一屏总览（1366×768起），配置/声纹、舵机详情、M12诊断使用弹窗；所有原控制保留，增加内部RAM最大连续块、PSRAM及正确口径的历史最低指标。49项Node回归和Chromium多尺寸/离线/无自动动作检查通过。设备重新在线，15:05内部free30,771 B、largest13,824 B、PSRAM5,975,844 B。只读评估找到日志去冗余5,790 B、IMU/音频订阅PSRAM迁移、首页底图/字体等Flash机会；**本轮未改固件或刷机，节省尚未兑现**。详见[优化评估](resource-optimization.md)。

## 2026-11-08：资源与接口盘点（0.4.30，未改固件）

用户反馈“还行，至少没有明显问题”，要求提交推送并为后续开发盘点资源。功能提交`9659664`已在`origin/codex/continuous-hearing`，再次push确认无遗漏；本轮只新增盘点文档和记录，没有刷机、重启、改接线或驱动舵机。

完整表见[资源与接口清单](resource-inventory.md)：内部SRAM512 KiB、PSRAM8 MiB、板载Flash16 MiB；应用3,486,976 B，当前ota_1槽余1,690,368 B。三个UART全部占用，Port A GPIO1/2为扩展候选；GDMA按当前路径推算3 TX / 4 RX，须为SPI、I2S、相机及SHA/AES计入资源。

本次直接读取设备超时，网页离线；最后缓存为**14:23:03**、与12:56验收同一次开机。内部free46,675 B、largest17,408 B、PSRAM free6,154,572 B；`heap_min_free=1,447 B`是多个堆区域各自历史最低之和，**不代表同一时刻总空闲只有1,447 B**。听觉累计54,794包、dropped0、reconnects80，HTTP音频跳块7,325；SD读取29/失败0。长期重连/跳块来源未确定，不能把旧一分钟零错误窗口外推为长期稳定，也不能直接归因于内存。较晚缓存ASR starts1/stops0，stop仍未实机验收；没有新增真人声纹录入。

后续优先补同步内存/DMA/任务栈遥测和长时并发诊断，再按预算增加功能。现有GPIO35共享总线屏障、驱动失败保护、遥控俯仰屏蔽、保存10°与M12手动策略保留。本机证据`logs/resource-inventory-20261108/`，不提交运行时日志或绑定配置。

## 2026-11-08：0.4.30 SD 媒体、总线交接与实机听觉修复

分支 `codex/continuous-hearing`，基于5952fd5继续实机部署。用户追加要求“最终声音也要移动到SD卡”；已实现40段台词与90眼睛动画共用SD，操作见[SD媒体](sd-media.md)。最新应用3,486,976字节，SHA256 `06b6cd100f19b23386b6077d9f716caad8fac7d524709a7bc970ddd680dcc08e`，本机 `artifacts/collector-0.4.30-sd-dma/`。本轮每次先核对身份/活动ota_1/otadata CRC并保留完整槽备份，仅写0x510000应用、独立verify通过；没有改分区、依赖锁或IDF5.5.4版本。

- 按用户要求对照原可用版本：0.4.26的40条声音实际嵌入Flash，不能用其能播放推断SD曾挂载。只读扇区诊断确认原卡是exFAT，固定FatFs不支持；完整断电仍失败。用户自行备份并格式化FAT32后，同一固件挂载恢复。固件从未自动格式化卡，临时扇区探针已移除。
- FAT32后首次实际mkdir捕获`spi_hal_setup_trans` running-command断言：LCD队列最后DMA未完成时SD获得总线并开始poll_busy。固定IDF的DC释放/总线锁不足以覆盖此交接窗口。现文件读写先锁LVGL，再以`esp_lcd_panel_io_tx_param(-1,nullptr,0)`排空LCD队列，持锁完成单文件I/O后释放；失败不进入SD。眼睛和PCM在播放前完整缓存，运行期没有逐帧读卡。独立复核确认没有LVGL/eyes锁逆序或ISR完成回调死锁。
- 资源安装改走系统curl，令牌仍只经stdin，解决Mac的Node直连EHOSTUNREACH。实机上传任务曾因内部RAM不足返回503；改为16 KiB PSRAM常驻worker、静态TCB及单槽队列。也修正原自删任务跳过JsonDocument析构的逐文件泄漏。下一文件先等installing=false，避免成功响应先到而收尾未完成时409。未知结果仍停止、不自动重放POST。
- 两路音频订阅与眼睛运行时，旧播放入口虽有约30 KiB总空闲内存，仍无法申请连续8 KiB任务栈而返回503。播放也改为开机创建16 KiB PSRAM常驻worker、静态TCB/单槽队列，保留播放门、取消归属和收尾复位；增加worker就绪/栈余量、最大连续内部空闲块及复位原因遥测。最新候选及最终固件实播第1/40条均完整提交214272/172800字节，error及cleanup_error均0，结束功放关闭且保持硬件复位。
- d46e3fd候选切换三款眼睛后捕获新的LoadProhibited：日志先报`Failed to allocate priv RX buffer`，回溯为SPI setup_priv_desc失败清理→uninstall_priv_desc→memcpy(NULL)。与之前running-command断言是不同问题。实际IDF函数故障注入先复现ASan空指针崩溃，再修正为仅释放局部临时TX/RX，256组合加实机同条件用例通过；成功路径不改。CMake构建时生成哈希固定的补丁副本，原IDF文件不改；原SHA `1981ca91462f20cb74978e794895cd4b0650990711031e03cf433509fcad90eb`，补丁后`2239515feaaa0e890aeb0c4c474e3f2fd318ae841001f66603a963feb1a89b1c`。眼睛worker移至16 KiB PSRAM，释放原8 KiB内部栈空间；最终实机验证见collector-validation。
- 台词逐段存`/sdcard/soul/voice/PLAY-001..040.adpcm`，合计1,402,880字节；与0.4.29 ELF中嵌入音频逐字节一致，SHA256 `4b00df24383f29a87c7fe4e1dcdc4d8d9efde86fd8fe2868113f5f08da9c1bba`。固件只留白名单元数据/哈希，缺卡、缺文件或损坏时明确失败，不回退Flash。网页标记SD播放；ASR open800ms、40条随机池、情绪配音/眼睛、遥控仅左右、保存10°、M12手动均保留。
- 实机听觉旧候选出现LVGL LoadProhibited，原12 KiB worker栈明显小于实测约24.7 KiB占用；改32 KiB PSRAM栈后该问题未再观察到。再修非阻塞poll、共享200ms读预算、TCP_NODELAY和编码优先级4；0.4.29比较窗口由大量丢块恢复为278包/约17秒、0丢块/0重连。仅audio_ack后重置退避；eyes request_id复制到JSON，修正悬空指针导致的遥测乱码。
- Python62项（58通过/4旧跳过）、Node49项通过；真实40段SD读取/解码与参考一致，错误路径无音频输出。独立复核覆盖SPI屏障、常驻worker所有权/析构和驱动失败清理。最终固件三款idle/happy及缓存5秒不读SD、首尾台词、同长度损坏上传拒绝通过；57.986秒并发窗口972个音频包、0丢块/重连/SD错误，台词与眼睛同时运行，停止回待机。后端实际记录976包/936960个16k样本，无错误。用户现场确认声音完整、眼睛正常。实体两次open已实收并播放38/39，comforting/encouraging眼睛以voice来源显示；指令至输出初始化约1002/1001ms，包含800ms等待。实体start/stop补验尚待完成，网页链路已通过。

诊断过程中SPI屏障候选曾有一次未捕获回溯的重启；之后带日志续装54个眼睛文件未再捕获panic。常驻上传worker候选约12:29–12:30又有一次未捕获回溯的重启，用户明确未碰USB或供电；不能把这些重启归因为用户动作、JSON泄漏或WithCaps清理峰值。随后同候选暂停Mac监视服务，全量复核130文件均unchanged且无SD错误，PSRAM窗口首尾均6242528字节，没有逐文件泄漏趋势。最终版本的通过窗口与长期稳定性要分开记录。没有新增真实声纹；声纹质量、后端情绪准确率和逐条音质仍需现场验收。

本机证据：`logs/sd-v0430/`、`logs/sd-v0430-spi/`、`logs/sd-v0430-final/`、`logs/sd-v0430-release/`、`logs/sd-v0430-dma/`，早期听觉对照在`logs/hearing-v0429*/`；串口原文/配置仅`.private/`，不提交。

## 2026-11-08：连续听觉与声纹（0.4.28；后端已更新，本体待部署）

分支 `codex/continuous-hearing`，基于已推送的638ea24。功能提交83d42e9（后端）、267cc68（本体）、5171463（网页），复核修正a6b3ddb。用户选定后端情绪标签，回应所有人、声纹仅显示身份。实现/操作/限制见[连续听觉](continuous-hearing.md)。

- 网页配置从本机私密.env导入后端，或手填IPv4 ws/wss与密钥；写NVS后待机。五句声纹录入/显式替换备份/取消；本体麦克风，非Mac麦克风。身份、转写、情绪、进度独立显示。ASR start0x12连续监听、stop0x13停止并失效旧会话回应。自身音频标记、5秒新鲜度、10秒回应冷却；既有40台词/眼睛映射、open800ms、遥控仅左右、M12手动不变。
- 服务器远端差异仅旧说明文字与默认端口；实际unit显式传端口/host，保留启动参数。远端`.backups/hearing-*`备份原代码与声纹，检查代码无漂移后部署四个Python文件。实际hello/Bearer/旧Mac鉴权/错误token拒绝、20个24k Opus包→19200个16k样本、取消录入通过；录音OpusHead24k正确，旧声纹库哈希未变。没有录入用户真实新声纹。
- 独立复核发现5项Important，已修复身份影响人声资格、缺口前滚/旧队列事件、传输生命周期及阻塞；后端13项测试、主机传输策略验证通过。传输改为IDF原生同步WebSocket，单worker处理回调，2秒连接/200ms发送共享预算，stop shutdown已建立socket。IPv4限制避免阻塞DNS；关闭会打印鉴权请求的transport_ws日志标签。已知Minor：应用握手前远端关闭可能导致固定1秒重试，待优化。
- Python59项（55通过/4旧跳过）、Node43通过；720眼睛帧、三款LVGL、心叉回归通过。旧首页120帧32色索引无损5位打包省336480字节，897360原索引SHA逐字节一致。固定IDF5.5.4构建通过，没有改依赖锁或分区。
- 本机固件 `artifacts/collector-0.4.28-hearing/stack-chan.bin`：4882832字节（0x4a8190），原0x4f0000槽余294512，SHA256 `bf92399c2b898b5a5a76b6d0026f94dcbf57319e95004871aca6cfa658cb8284`。未刷入；最后实机仍0.4.26。USB未枚举/网页离线，沿用用户稍后接的安排，不再催插USB。眼睛资源也待装，无需拔SD。设备接回时核对身份与活动槽、备份完整ota_1，仅写0x510000应用槽并独立verify；再显式导入后端配置、真人录入与台词/屏幕/并发验收。
- 观察台已重启，http://127.0.0.1:8787/服务可用。浏览器宿主不可用，独立Chrome因显示服务失败也未产图，专用进程已退出；网页只有组件/API验收，未作视觉验收。

## 2026-11-07：三款眼睛与ASR台词（0.4.27，代码已推送，待设备部署）

代码已推送 `origin/codex/asr-voice-eyes`；功能与修正提交为 `b3583a8`、`bb09b61`、`523a82a`、`343af4f`。独立复核至343af4f无剩余Critical/Important，最后Python39通过/4跳过、Node38、720帧/三款LVGL与旧页面、固定IDF构建通过。应用5,046,736字节，SHA256 `0ee945ca7c171ad59ed3c4d2a077ce57ae49d54df4fa06023f32de8110cc5037`；本机候选在 `artifacts/collector-0.4.27-eyes/stack-chan.bin`，未部署。

- 用户已授权实现 assets/immplement.md，并要求 ASR open 台词配近似表情、资源不拔卡传输。此前40台词/800ms代码736a31d已推送origin/codex/random-voice-yaw-only；本轮codex/asr-voice-eyes。
- 已实现三款×30状态、固定PSRAM双资产/双帧、后台读卡校验解码、LVGL安全交接、手动网页控制、同boot/UUID去重，以及窄范围Wi-Fi资源安装。只ASR来源的成功首块台词PCM触发表情，结束只取消自身Voice任务。手动优先；切页/熄屏/心叉取消，返回idle。首次ASR可启用默认v05；网页恢复原首页后本次开机不再自动启用，除非用户重新启用款式。
- 保留遥控上下禁用、保存10°、M12手动策略、40条台词和800ms等待。没有新增机械动作测试或恢复情绪感知。
- Python39通过/4旧跳过，Node38，真实LVGL720帧解码/三款预览/旧页面回归及IDF5.5.4构建通过。新款损坏保留原款启用状态已RED→GREEN。具体记录见collector-validation.md；资源映射为近似编配，未逐条听校。
- 用户明确选择“稍后接，先完成代码和推送”。最后检查Mac无USB设备、监视终端离线；上次部署仍0.4.26。此轮仅交付代码/主机验证/推送，SD卡不用拔，未刷入或安装资源，未作实屏音画/并发验收。稍后接回时，再次核对身份、活动ota_1和旧完整备份，只写0x510000并独立verify，然后运行node scripts/install-eyes-assets.mjs。
- 独立复核三项修正均已RED→GREEN：加载替换效果时保持旧完整眼睛帧（取消/离页仍不复活）；严格拒绝单引号/裸key/尾逗号JSON；worker创建失败释放PSRAM并明确NoMemory，configure不再永久Loading。用户未要求修改三款外观；主机截图已检查，网页CUA宿主不可用，仅确认本机HTTP提供新版面板和真实DeviceClient/组件测试，未宣称浏览器排版验收。

## 2026-11-07：40条台词与ASR等待OK（collector-0.4.26）

用户在0.4.25实播后确认open都是本人说出，ASR模组会先回OK，要求ASR触发延迟800毫秒再播放；同时将语音助手制作的PLAY021–040一起加入。此节覆盖下方20条版本；本轮基于本地提交 `9f075ff` 继续，不推送。

- 网页选择及ASR随机池扩为40条，固件/代理/网页边界统一0随机、1–40指定，仍排除紧接上一条。音源使用 `voice/outputs/stackchan_playable_40.json` 和两批目录，原文件哈希均核对。总172.254秒、最长7.6秒；无裁剪/变速，未播放全40条做听感验收。
- 40条24kHz IMA预计2101504字节超过应用余量；改为16kHz IMA共1402880字节，在原有有界PCM缓冲内倒序线性插值回原24kHz帧数，保持完整时长，不改变共享I²S设置/分区表。有损转换与听感分开；全部40条实际C++解码/扩展与独立FFmpeg加Python参考逐字节一致。
- ASR新open帧的接受时间确定最早启动时刻+800ms；只在播放worker让出调度等待，UART接收任务不等待。整个等待gate占用、输出未初始化，新请求仍拒绝/丢弃；网页不增加800ms等待。新增waiting状态、voice_accepted_us/voice_output_started_us和asr_start_delay_ms。后者开始时间是codec初始化成功，不是声学测量；实际还有原有输出初始化/DMA时间。结束后1.2秒ASR冷却保留。
- Python38项中34通过/4旧情绪依赖跳过，Node34通过。独立复核确认40条端到端边界、等待/功放顺序；2400组ASan+UBSan原地扩展/截断边界通过，含1–2048、随机和192000帧。两处旧20条文案已修复，无未解决Critical/Important。主机测试不能替代实际音质和机械验收。
- 使用已安装Python3.13虚拟环境的PATH运行固定IDF5.5.4（系统默认Python3.14使初次export查找不存在环境，未构建/刷写；修正后完整及最终增量构建通过）。最终应用4965248字节，槽余212096字节，SHA256 `007550d68a61273d168d3ec4e9dd9dd580494682b3c1098e99d755addb9a029f`；归档 `artifacts/collector-0.4.26-voice40/`。
- 重新核对USB设备身份/ESP32-S3/16MB/活动ota_1/otadata CRC，完整0.4.25槽备份5177344字节，SHA256 `30dad04d601de7a95eb641de07b0f6eb567ca9807236ed8f4632f22447572269`，应用前缀与归档完全一致；仅0x510000应用写入并独立verify_flash通过。没有写bootloader、分区表、otadata或NVS。
- 初始联网等待窗口55秒未连，随后HTTP确认0.4.26/40条/800ms，并已实际收到3次open、接受3次；捕获job3/clip11 completed、152064字节全提交、error/cleanup_error=0、输出关闭/硬件复位保持。accepted→codec初始化1017.914ms，符合至少800ms等待；未完整捕获前两个job的全过程。
- 为检查启动读取USB日志时出现USB_UART_CHIP_RESET，无捕获到panic；随后停止串口读取并等网络恢复。该断连不能当作持续在线或固件崩溃结论。新boot初始idle/job0、无ASR历史回放，40条/保存10°/request_seq0/remote_pitch_enabled=false均重新确认。没有追加遥控上下运动测试，重负载机械问题仍未修复。
- 恢复后真实网页同一 `/api/control` 点播clip40/job1返回202；completed，172800字节全提交，error/cleanup_error=0，output_enabled=false/hardware_reset_held=true。接受到codec初始化290.473ms，未加ASR专用等待。此项是本机API到真实设备链路；浏览器仍需刷新加载新JS，未冒充新按钮点击/窄屏视觉验收。已向用户询问OK与随机台词的实际衔接，现场答复待补。

证据：`logs/voice-v0426/`、`logs/voice-v0426-host.log`、`logs/voice-v0426-node.log`、`logs/collector-v0426-build-final.log`；启动原始串口仅 `.private/voice-v0426/boot-monitor.log`。不提交私密配置/媒体/备份；他人新增 `assets/immplement.md` 保留且未纳入。

## 2026-11-07：20条本体台词与遥控仅左右（collector-0.4.25）

用户对0.4.24反馈“上下移动幅度变小”，决定暂时屏蔽遥控上下，增加voice文件夹20条随机台词，由网页和ASR open触发。分支 `codex/random-voice-yaw-only`，基于 `71fe438`；本轮不推送。

- 所有实体遥控协议的上下接管入口关闭，实时能力标记disabled；左右方向和中心开关屏保留。网页显式保存角/启动姿态仍保留，当前持久角10°、启动后request_seq=0；没有追加上下运动测试，也不声称负载抖动或承重问题已修复。
- 音源为 `voice/outputs/20261107-playable-20` 的PLAY001–020，不是旧300条候选。新增转换脚本校验20条索引、PCM哈希/24kHz单声道/长度，编码为IMA ADPCM，生成文件仅 `.private/voice-library/`。原PCM共4258024字节，嵌入包1082624字节，最长7.184秒；20条C++实际解码均与FFmpeg独立解码逐字节一致。这证明解码一致，不证明有损编码听感。
- 新鉴权接口 `/v1/voice/play` 强制当前boot，仅接收整数clip_id=0（随机）或1–20。共用既有播放gate/worker/功放收尾；完整有界解码后才开启输出，忙时拒绝，无队列/自动重试。随机排除上一条，当前音量80不变。ASR UART接收任务只处理新的open无值帧，不重放历史；播放完成后1.2秒冷却，不能据此保证没有自声误识别。ASR与音源都在本体，运行不依赖Mac/网页。
- 网页增加20条选择、播放所选、随机播放、实际播放状态/提交进度；过期/忙/音频未就绪/旧能力时禁用，页面初始化与重连不自动播放。独立审阅发现底层DeviceClient路由白名单遗漏，已修复并新增真实DeviceClient→curl→本机HTTP的RED→GREEN回归。复核无Critical/Important阻塞。
- Python38项中34通过、4个原情绪依赖跳过；Node33通过；ESP-IDF5.5.4构建通过，应用4642368字节，占0x4f0000槽，余0x829c0。最终SHA256 `48051d8b533d7c6b004af2bb0cd32c7ac3d0edf46257f3fe1f63a9dd55ca5d25`，产物 `artifacts/collector-0.4.25-voice/`。
- USB原端口不存在，按串口设备身份核对新端口后重新读取身份/分区/otadata CRC；完整旧0.4.24活动槽备份5177344字节，SHA256 `15fc1f48a6d923341f8214c617cdf26afd60db168c383e9fb6cf127b39c53ead`，前缀与归档匹配。只写活动ota_1/0x510000应用并独立verify_flash通过，未写bootloader/分区表/otadata/NVS。重启HTTP已确认0.4.25、20条、remote_pitch_enabled=false；启动idle/job0、输出关闭/硬件复位保持，没有开机自动播放。
- Chrome已确认新版台词控件和可用状态；用户同时在操作浏览器，未继续抢占窗口，窄屏/控制台检查未完成。实际点播使用本机网页同一 `/api/control` 路径：clip1/job1返回202，捕获starting→playing→draining→completed，214272/214272字节，error/cleanup_error均0，输出关闭/硬件复位保持。此项是实际网页控制API验证，不冒充浏览器按钮点击。
- 随后实际UART收到9次open并接受9次，现场已捕获job2–5多条随机播放完成（12→11→20→4），最新job10/clip19也completed、214414字节全部提交、error/cleanup_error=0、输出关闭。其间job6采样在playing时结束，不用后来的job10替它补写完成。用户已确认open均由本人连续说出；ASR模组会先回OK，要求后续版本在ASR触发后等800毫秒再播放。不把软件播放完成等同于声音无卡顿。
- 本轮没有修改M12程序/身份库、没有自动恢复其状态；用户在测试中自行操作M12网页，因此旧“待机/资料保留”快照不能替代其最新状态。新增未跟踪的 `assets/immplement.md` 为其他工作，未纳入本轮。

证据：`logs/voice-host.log`、`logs/voice-node.log`、`logs/collector-v0425-build.log`、`logs/voice-v0425/`；私密配置、原音频、备份及现场采样不提交。

## 2026-11-07：负载俯仰缓动版（collector-0.4.24）

用户已确认0.4.23松手能回到保存10°；新增反馈为装饰和M12负载下“运动时来回抖、过冲”。被动35样本中俯仰反馈维持10°，电流/负载遥测无效，不能据此判定扭矩不足或机械根因。

- collector仅俯仰采用较慢的临界阻尼命令曲线（软件弹簧k=2.25、d=3），启动/网页设置/实体遥控共用。遥测motion_profile=gentle_load，speed仍为请求输入，不是实际角速度。左右、保存10°、校准、内部PID、限位和扭矩释放策略保留；没有恢复M12自动模式或改身份库。
- 真实Servo主机回归发现AnimateValue赋值在中途切换目标时会调用value()，在固定20ms推进以外再累计墙钟时间，出现约20.8°的首帧命令跳变。仅缓动轴改用directValue/retarget，从上次指令连续转向，保持速度连续性；未全局修改动画库或yaw。
- 主机20ms调度：5→85°软件轨迹7.94秒、最大每步0.9°；85→10°为7.90秒；10→15°为5.90秒、每步最多0.1°。这些是命令曲线，不是舵机实测速度或机械稳定性。15秒设置动作超时继续保留，严重调度延迟仍取消而非无限重试。
- Python37项中33通过/4情绪依赖跳过，Node29通过，IDF5.5.4构建通过。新增范围/单调性、松手、中途反向、错误参数、默认轴隔离及停止后新反馈起步回归。未自动往复驱动有负载的舵机。
- 独立审阅发现运动中网页接管会继承已取消速度（主机26.8→31.2→10°）。新增RED→GREEN回归，profile显式停止/反馈起步/新控制会话先清速度，零时长求值同步生成器缓存，不发UART；连续遥控转向仍保留速度。修后26.8→10°全程单调、6.8秒，停用后额外update也不复运动。独立定向复核解除阻塞，完整Python和IDF已重跑通过。
- 已重新核对身份/活动ota_1/otadata CRC，完整备份0.4.23槽并匹配归档；仅0x510000应用写入与独立verify通过。HTTP已确认0.4.24和gentle_load，保存10°持久值及request_seq=0保留，M12待机。USB日志检查期间发生一次USB_UART_CHIP_RESET，随后网页重新连接；没有panic证据，不把这段断连记录当稳定在线。已请用户只轻推一次，重负载抖动是否改善仍待现场答复。最终产物3554896字节，SHA256 `47588d3946cb95b23d297541145c7408b482cbea655d28b6450dce21776c82fe`。证据 logs/gentle-pitch-v0424/、logs/gentle-pitch-host.log、logs/gentle-pitch-node.log、logs/collector-v0424-build.log；分支codex/gentle-pitch-load，本轮不推送。

## 2026-11-07：摇杆回中修正与真人认人结果（collector-0.4.23）

本节覆盖下方候选状态。用户实际反馈左右正常、网页10°能到位，但推上松手停在约45°。核对当前发送端 `Y_CENTER=1960/Y_MIN=310/Y_MAX=3460`，松手固定输出471（47.1°），接收端此前直接当作机械目标，原因已定位。

- 0.4.23 将16字节 center_v1 的471±5映射为最新保存仰角，两侧单调映射5–85°，8字节旧协议保持绝对角。仲裁比较原始输入，不因保存中位改变而让固定心跳抢回控制；不改左右、校准或遥控器固件。16字节包没有摇杆/IMU模式位，因此该映射也影响IMU模式；实机范围是摇杆，不声称IMU原语义保持。
- 用户现场把保存仰角从30°改为10°；网页再次保存后见轨迹结束、反馈约10.3°。后续以实时设置为准，不能恢复旧30°。之前实体目标47.1°/实测46.5°证明接收与运动链已执行，但也正是本次回中问题的证据。旧反馈异常未做校准，不能据本次有效回传宣称永久修复。
- M12 真人短测实际累计124条识别结果；记录窗口约0.8–2.1结果/秒，有人脸暂离时窗口为0，温度78–79°C，发送计数保持不变，无8秒重启。均返回unidentified，未匹配已有资料；用户明确“先保留，稍后录入”。登记元数据和特征文件前后SHA256相同，未训练、覆盖或清空。
- 用户ASR turn off使认人退出Audio FFT，最终保持待机。真实Chrome桌面/400px控制区已检查；认人期间设备结果由实际HTTP记录，截图时用户已退出，不把待机截图说成姓名命中验收。
- Node29通过；Python37中33通过/4跳过；追加真实Servo松手回中采用active路径sync_start=false，轨迹到10°且释放扭矩。独立审阅额外穷举int16输入×4中位，单调/限位/legacy兼容/心跳抑制通过。IDF5.5.4构建完成。
- 0.4.23 实际HTTP已确认版本/新映射能力；上线后的记录仅见静止47.1°心跳，保存10°、设置轨迹结束，反馈约8.7°，未被心跳拉到47.1°。已请用户再轻推并松手，截至本次记录尚无新方向输入或答复，实机回中验收仍待确认，不把主机回归写成用户验收。
- 新部署再核对身份、实际活动ota_1/CRC、完整0.4.22槽备份并匹配归档，只写0x510000应用并独立verify。0.4.23为3554384字节，SHA256 `7f7a9c165478f9e835623dd7c7cf83aadce7407bcda6eba4e89173fe5f0b0233`。证据 `logs/recognition-v0423/`、相关host/build日志；其余分区/校准未由刷机写入。

## 2026-11-07：手动 M12 认人与遥控上下恢复（collector-0.4.22）

用户要求 M12 增加认人，实体遥控上下重新控制舵机。分支 `codex/m12-recognition-remote-pitch`，基于 5b8036d；左右方向、中心开关屏、ASR 爱心/叉号保留。

- 网页增加“人脸识别（认人）”，经同源控制白名单与当前 boot 核对，由机身 UART 请求 M12 内置 Face Recognition。新增 `/v1/m12/mode` 和能力 `m12_recognition`，原 `/view`、ASR turn on 检测、turn off/触摸离开待机兼容。启动仍待机，无自动认人或唤醒。只使用已有身份库，未写登记资料；本机已核实一个身份条目及 512 字节特征文件，具体名字和校验值不写公共文档。
- 检测与认人分别统计更新率，识别显示各脸名字/未匹配及匹配度。模式切换、过期、断线、空结果清显示；原始 M12 视频仍未接入。机身脸框页同步模式，识别时显示身份文字。
- 实体俯仰恢复原 Servo 的限位、弹簧和扭矩释放，motor task 仍是唯一 UART owner。中心协议首包仅建俯仰基线，新上下变化接管；固定心跳不覆盖网页/启动设置。新保存设置接管实体，停用取消两轴且旧请求不重放。上下不再触发首页表情；不写校准或保存角。
- Node 29/29、Python 37 项中 33 通过/4 情绪依赖跳过；包含 M12 实际 parser/policy、三模式/新鲜度/ACK、网页显式点击、遥控仲裁及真实 Servo 俯仰小幅轨迹/限位/扭矩释放。IDF 5.5.4 构建通过。独立审阅及 ACK 定向复核无阻塞。
- 首候选 0.4.21 实机能启动识别程序，但 8 秒内无脸结果时反复重启，短测约 78–79°C。核对官方源码发现无脸时可以不发结果，因此 0.4.22 将程序报告作为停止启动重试的 ACK。`last_recognition_report_us` 只表示报告，不增识别计数或创建身份。无新鲜结果时不宣称已认出人；用户明确再次点同模式仍发送命令并清旧帧。
- 两版都重新核对设备身份、ESP32-S3/16MB、实读分区/otadata CRC/活动 ota_1，完整旧槽备份并匹配归档；仅 0x510000 应用写入及独立 verify，未由刷机写 bootloader/分区/otadata/assets/NVS。最终应用 3553840 字节，SHA256 `6924bb75ef64e5ad28fc3f70858df4d593015f45c5c3ab3851a1838b94a177ac`；0.4.20 与 0.4.21 备份均保留。

证据：`logs/recognition-v0421/` 是首候选，`logs/recognition-v0422/` 是修正版；构建/主机测试日志在 logs 根；设备代码副本、部署脚本和身份库校验只在 .private。本轮不推送。保存仰角最后实读仍 30°，旧 -3.7°反馈问题未校准或声称修复。

## 2026-11-07：网页基础控制与 collector-0.4.20（Codex）

用户确认第一版包括M12开人脸检测/退出待机、舵机左右转/启停、仰角设置。分支 `codex/web-basic-controls`，基于3fe67a0；本轮本地保存，不推送。

- 网页在现有M12/舵机区域加入按钮和结果提示，打开/恢复/刷新不发动作。M12共用ASR/触摸的原显式请求与机身页面，网络SSH仅继续监控，不直接切换M12程序。左右按机器人自身方向单次5°、速度100、有效反馈起点、静止门控/±128°及实际机械限位、队列1秒过期；实体新遥控或停用取消网页待执行命令。仍只有motor task拥有舵机UART。
- 本机仅当前同源JSON POST `/api/control`白名单写入；配对凭据仍在本机，通过curl stdin传递。请求核对当前boot；新鉴权M12/yaw接口强制X-Boot-Id，旧remote/pitch兼容Android且校验网页附带的boot。未知结果不自动重发；接受/保存、软件轨迹结束与实测到位分开。
- 仰角按钮“保存并应用”会先持久化、立即尝试调整，并供下次开机使用。用户问是否必须等开机，已解释并补页面提示。用户现场操作把保存角从旧10°改到30°；本次末态读到30°，保留用户新值，后续以遥测为准，不能恢复历史10°。
- Node25/25通过；Python36项中32通过/4情绪依赖跳过；IDF5.5.4构建通过，未改依赖。独立审阅发现实体运动与网页步进竞争，消费时重查真实轨迹状态并增加先失败后通过回归；成功/运动中拒绝/取消状态亦有覆盖。
- 先部署0.4.19后实机发现被动初始化时动画done=false、没有动作也被误判忙碌。改为区分“尚未发过动作”和“已发动作未结束”，交付独立0.4.20；真实Servo/spring主机回归覆盖首次被动启动、小幅轨迹和结束。不能把0.4.19候选称最终版。
- 两次刷写均核对本机绑定身份、ESP32-S3/16MB、真实分区表、otadata有效CRC/活动ota_1与旧镜像；完整5MB旧槽备份并核对归档应用。仅写0x510000应用并独立verify，通过后复位。0.4.20产物3,551,568字节，SHA256 `bcdbfd4763fa26a6d6a3411e10f3e3ac684dd9e6c8eb935f0664ddcf8b076345`。未由刷机写bootloader/分区/otadata/assets/NVS。首次USB有枚举却无下载回应，两次尝试未写；用户重新插线后恢复，未认定根因。
- Chrome真实桌面/400px窄屏检查通过，按钮/输入无横向溢出，DevTools观察时0条控制台消息；已恢复桌面并关闭DevTools。实际网页点击开启M12后Face Detector/人脸框/计数出现，观察1张脸、最高置信度98.8%、约4.1结果/秒、79°C；随后用户ASR turn off回待机，不能把该退出写成网页按钮实测。网页退出路径有主机端到端POST测试，未额外开关重复加热。
- 用户实际操作已产生motor停用/启用、yaw多次接受以及pitch保存；用户反馈“有点动作”。末态M12 standby/Audio FFT、温度75°C，控制启用。Yaw软件轨迹结束不等于机械准确到位；曾请求-0.7°而反馈3.4°，俯仰实测-3.7°超出允许启动范围，因此feedback_unavailable、未发出仰角移动。此旧反馈/机械问题没有在本轮修复，未为测试修改校准或连续重复动作；用户现场操作与agent操作区分记录。

证据：`logs/web-controls-v0419/`为候选，`logs/web-controls-v0420/`为最终，build/host/node日志在logs根；完整串口与部署脚本仅.private。备份保留0.4.18和0.4.19。当前服务http://127.0.0.1:8787/；刷新载入最后补充的仰角提示，监听默认关闭。原始M12视频、身份识别、自动策略未加入。

## 2026-11-07：将 M12 网络温度真正接进网页（Codex）

用户指出网页仍看不到 M12。原因是上轮只确认网络/一次温度读取，页面仍固定显示“网络未接入”，没有后台持续读取；这次已补齐数据到页面的完整链路。

- M12 当前 Wi-Fi 地址与此前可信 SSH 主机密钥已独立核实；真实地址只在 `.private/stackchan-monitor.json`。新增可选 m12 绑定和一个共享 SSH 只读系统采集，每两秒读取 TEMP_R、运行时间、Wi-Fi 接口、频率和视觉进程。沿用已有可信 HostKeyAlias，严格验证密钥；密码仅 ASKPASS 环境，不进网页/参数/日志。不修改设备、重刷或启动检测/视频。
- 网页温度从新数据读取，新增网络监控和设备工作进程。M12 连接与 StackChan 独立，本体断线不隐藏仍新鲜的 M12 温度；超过六秒/无效/断线/暂停清空。最后一个订阅离开后关闭 SSH，重复网页共用连接。
- 主机测试先缺模块失败，实现后最终 Node **19/19** 通过。覆盖解析/无效温度/过期/重复时间/重启、SSH 参数不泄露密码、停止后的迟到数据、本体断线而 M12 仍可用。独立审阅通过，无阻塞。
- 真实网页 `/api/state` 连续收到 M12 67–68°C、Wi-Fi up、audio_fft、运行时间递增。生产页面 render 用真实 HTTP 状态在 Node DOM/Canvas stub 中验证：显示 `70 °C`、实时、已连接和 Audio FFT（待机）；暂停清空、仅本体断线仍显示温度。证据 `logs/thermal-v0418/m12-web-network.json`、`m12-web-render.json`。
- CUA 原生管道仍不可用，未完成本次浏览器目视检查，stub 不替代视觉验收。需刷新网页载入新的 JS；服务保持运行。原始 M12 视频依旧未接入，不为其切换模式。

## 2026-11-07：M12 独立监控与 0.4.18 芯片温度（Codex）

用户要求 M12 与 ASR 分开、重点关注 M12 温度/检测效果/帧率，并标出 StackChan 温度。检查本机邻居、当前局域网 HTTP 和默认 USB 地址后，没有找到可访问的 M12 服务；用户明确选择 **稍后接 M12 USB，先完成当前可做部分**。不能据扫描认定 M12 没有连 Wi-Fi，也不能推断其当前温度。

**已交付：** 网页增加独立 M12 区域，显示 640×480 真实检测坐标示意、人数、置信度、结果年龄、累计计数/异常，以及由设备结果计数和单调时间计算的五秒窗口更新率（至少一秒样本；不是视频 FPS）。待机、切换、重启/断线、结果过期时清空检测框，过期显示 0 FPS。ASR 独立列最近指令。M12 网络未接入时温度明确留空，原始 M12 视频未实现接入；当前显示的框不是相机原图。

**StackChan 温度：** `collector-0.4.18` 增加 ESP32-S3 内部芯片温度到 info/telemetry，独立优先级 1、core 1 任务以约 1Hz 读取；HTTP 只复制缓存。配置 20–100°C，读取失败/非有限值/超量程/超过三秒返回无效且数值为 null。网页显示读数与两分钟趋势，明确非室温/外壳温度。基础版 ASR/M12 策略、舵机/保存角度及停用功能源码未改变。

**构建与部署：** IDF 5.5.4 构建通过；产物 `artifacts/collector-0.4.18-thermal/stack-chan.bin`，3,548,608 字节，SHA256 `6fa6b2dfd0429c29ea9abf33ddcbe41e85aca2ed71128bb4a6a0f6a84c65bd08`。重新核对原开发机身份、16MB、真实分区表、otadata CRC/活动 ota_1、旧版本 0.4.17，完整回读并与归档旧应用匹配；备份 `backups/ota_1-collector-0.4.17-before-thermal-20261107.bin`。仅写 ota_1 / 0x510000 并独立 verify，通过后硬件复位。刷写未触 bootloader/分区表/otadata/assets/NVS。证据 `logs/thermal-v0418/deployment.json`。

**验证与现场发现：**
- Python 35 项：31 通过、4 情绪依赖缺失跳过；温度生产数据结构的读取失败/范围/过期主机测试通过。最终 Node 15 项通过，含 M12 计数/零人脸/重复/倒退/切换/重启/过期及待机报告语义。独立审阅及定向复核无阻塞。
- 真实 HTTP 新版本 0.4.18，15 秒采样 15/15 温度有效，72–75°C、14 个不同温度序号，音视频/遥测全部新鲜；随后读到 75°C。这是本体芯片温度，不是 M12 温度。保存仰角仍 10°、request_seq=0，旧启动反馈 `feedback_unavailable` 保留，未额外调角。
- 刷机前 M12 最近一次 ASR turn on 后曾累计 2,241 个检测结果；观察时最新结果已约四分钟未更新。刷机后基础流程请求待机，但 M12 RX=0、replies=0，没有确认回应。网页显示“已请求待机 · 等待 M12 回应”；历史 Audio FFT 也只称“曾收到待机报告”。不据此归因过热、供电或网络，未额外开启检测反复试探。
- 本次 45.2 秒 USB 捕获只有 2,011 字节，没有应用版本/启动标记，**不是应用启动验收证据**；运行版本与温度由已认证 HTTP 证明。未为补日志再次复位。
- 新版 JS 语法/DOM 目标检查通过，实际 HTTP 状态经 Node DOM/Canvas stub 调用生产页面组件，显示 `75.0 °C`、M12 等待回应及网络未接入；这是功能检查，**不是浏览器视觉验收**。CUA 原生管道和浏览器宿主均不可用，新版桌面/窄屏目视检查未完成。上一版网页目视验收不能替代本次。

**后续：** M12 USB 接 Mac 后再确认网络、读取 TEMP_R 温度/系统状态、接原始视频，并检查不再输出检测结果的原因；不要填历史温度或假帧率。新版当前没有有效 M12 实机检测输出，检测框/FPS 的新逻辑经过合成测试，真人检测效果和持续帧率仍待实际输出后验收。

**本机证据：** `logs/thermal-v0418/{preflash-state,live-summary,render-smoke,deployment,boot-summary}.json`，编译/主机测试日志在 `logs/collector-v0418-*`。原始串口只放 `.private/thermal-v0418/`。网页服务仍在 http://127.0.0.1:8787/，刷新载入新版；不自动外放。本轮源码本地提交，未推送。

## 2026-11-07：Mac 网页观察台（Codex）

用户要求 Mac 和 StackChan 在同一 Wi-Fi 时，像 Android 观察台一样打开网页查看状态。新增 `web/monitor/`，分支 `codex/mac-web-monitor`，基于基础版提交 `6d96e9a`；机器人仍运行 **collector-0.4.17**。本轮没有编译/刷写固件、安装 Android、重启机器人或发送设备控制指令。

**使用：** 本机 http://127.0.0.1:8787/；重启 Mac 后双击 `scripts/open-stackchan-monitor.command`。管理命令与绑定见 [网页 README](../web/monitor/README.md)。Node.js 24 标准库，无 npm 依赖；只监听 loopback，不安装登录自启。开发机绑定仅在 `.private/stackchan-monitor.json`（0600），不进浏览器、进程参数或日志。当前地址固定；DHCP 换地址需更新本机配置，尚无网页 UDP 自动发现。

**显示与范围：** 黑绿页面，机身相机、麦克风第一通道电平/四秒包络、四秒六轴、磁场/光照/raw 接近、电池/Wi-Fi、真实舵机反馈、ASR/M12。M12 请求、最近设备报告和接收时间分开；旧检测结果标注年龄，历史 ASR 事件标注历史并换算事件时间。事件记录是连接和状态变化，完整固件日志仍在 USB/机身日志页。M12 镜头未通过该接口传输。保存 10°不作为实测角度，旧启动 `feedback_unavailable` 仍如实呈现。

**连接：** 复用现有协议，先 nonce/HMAC 验证绑定设备，再发 Bearer；系统 curl 以 `-q` 开头禁用用户 `.curlrc`，不跟随重定向。Mac 的 Node/Python 直接连设备曾报 EHOSTUNREACH，而系统 curl 可用，所以由本机服务桥接；没有认定或修复系统网络权限根因。主端口遥测/相机顺序读取；所有网页共用一个设备音频流。最后一个页面暂停/关闭后 1.5 秒停止采集并清空媒体。浏览器外放默认关、手动开启；断线/切后台/暂停/重启关闭且不自动恢复，不持久录音录像。

**已验证：**
- `node --test web/monitor/*.test.mjs`：11 项通过，涵盖身份校验、拒绝重定向、PCM 分片/通道/序号、过期/重启、延迟帧取消、历史 ASR、本机只读 API 与 `.curlrc` 隔离；前端 JS 和 zsh 入口语法检查通过。
- 独立审阅发现 curl 用户配置会重新开启 location/trace；以临时 CURL_HOME、假服务器/假令牌先复现，再加首参数 `-q`。复核 11 项通过，审阅阻塞解除。
- Chrome Guest 桌面和 400px 窄屏实际显示真实画面/数据，控制台检查无消息。页面暂停后服务状态为 viewers=0、active=false、online=false、frame=null、audioAt=0、motion=0；恢复重新连接。
- 浏览器手动监听显示 Audio playing；切到新标签再返回后监听关闭且未自动恢复。只验证播放链路和开关行为，未替用户验收听感。
- 最终约 20.128 秒 / 80 次检查：遥测、相机、音频均 80/80 新鲜，73 个不同画面，观测更新约 3.58fps；音频缺包、设备相机/音频错误增量均 0，IMU/音频各约 200 个四秒历史点。M12 sends 未增加，pitch request_seq 仍 0，保存值 10°未改。
- 第二个网页订阅进入/离开后 viewers 为 1→2→1，机器人音频客户端保持 2（本观察台共享一路，另一路来源未在本轮核实），没有因多页新增设备音频连接。

**本机证据：** `logs/mac-web-monitor/live-summary.json`、`pause.json`、`shared-client.json`。浏览器实测通过 CUA；内置浏览器宿主不可用，改用原生 Chrome 检查。本机服务保持运行、观察页面已打开、监听关闭。长期运行、改 IP 自动发现、真实物理断网/机器人重启和音频听感不在本次通过范围；模拟过期/重启通过不替代这些验收。

## 2026-11-07：0.4.17 基础版，暂停自动感知（Codex）

用户要求先关闭无关功能，只保留首页、参数、log、舵机控制、爱心/叉号及 ASR 驱动 M12，再逐项增加；随后明确选择 M12 手动流程。

**本轮变更：**
- 首页去掉情绪角落，移除固件情绪接收接口和状态生产；本机 `emotion-service.py stop/status` 确认没有匹配的服务运行。情绪试验源码、模型和配置保留，Claude 的 `server/ear` 未操作。
- 机身 ESP-DL 人脸检测改链接恒关闭适配器，旧 Android 开关依据 `supported=false` 不可用；保留基础音视频/传感器采集及 Android 连接。
- M12 启动请求 Audio FFT；仅 ASR turn on 请求检测，turn off 或轻触人脸页退出请求待机。不开自动识别，不响应普通交互/遥控心跳唤醒，不因空闲自动停检测。
- 每次明确切换均发命令，删除最近检测结果复用优化，避免快速 off→on 漏发开启；待机明确 ACK 后停止重发，未回应仍按 8 秒/30 秒节奏重试。
- 保留首页/参数/log/配对、左右舵机、中心开关屏、1 秒心心/叉号和原提示音。没有修改舵机运动参数、校准或保存角度，也未修改依赖锁与 IDF 5.5.4。

**源码与构建：** 分支 `codex/basic-controls-v0417`；产物 `artifacts/collector-0.4.17-basic/stack-chan.bin`，3,544,144 字节，SHA256 `d6bf0562b65a8b6f8abbb1393e237effb208626457008fdd0028af0aaf553ff5`。最终构建日志 `logs/collector-v0417-build-final.log`。源文件和本机产物分别保留。

**验证：** Python 34 项中的 30 项通过、4 项因缺少情绪运行依赖跳过；ASR/M12/遥控协议通过，LVGL 四组和 90 次反馈基准通过，Android 五组主机测试通过。后者默认 JDK 路径已失效，使用本机 `COLLECTOR_JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home` 验证。独立审阅发现并已复核快速 off→on 修复；新回归先红后绿。旧依赖编译警告仍有，本轮无构建失败。

**部署已完成：** 重新核对原开发机身份、16 MB Flash、真实分区表、otadata CRC/有效槽和 ota_1 当前版本 0.4.16 后，完整备份 `backups/ota_1-collector-0.4.16-before-basic-20261107.bin`，应用前缀与归档 0.4.16 完全一致；只写 `ota_1 / 0x510000` 并独立 verify 通过，随后硬件复位。未由刷机写入 bootloader、分区表、otadata、assets 或 NVS。默认速率第一次完整回读在 120 秒超时、当时尚未写入并已恢复原机；改 921600 后 63.6 秒完整回读通过。证据 `logs/basic-controls-v0417/deployment.json`。

**实机启动与接口：** 45 秒启动捕获有一次 boot、0.4.17 版本和 ASR UART 就绪，无崩溃标记。M12 明确回复 Audio FFT，后续 HTTP 显示发送数 1、检测/识别数 0、mode=explicit，自动识别/唤醒均 false。新身份挑战通过；情绪与机身检测 POST 均为 404，情绪遥测缺席、机身检测 supported/enabled/ready 均 false；JPEG 读取 7,103 字节、音频读到 SCA1 头。Python 直连曾报 No route to host，系统 curl 同地址成功；不能称网络或该 Python 环境根因已修复。证据 `boot-summary.json`、`http-summary.json`、`network-check.json`。

**舵机与手机边界：** 保存角度仍为 10°、persisted=true、request_seq=0/source=boot，未提交调角。启动姿态 state=feedback_unavailable，后续单次反馈为 -3.7°，不能据此声称已经到达保存角度；保留旧回传问题，不自动补动作或校准。Android 未重新安装；真人 ASR 开关、实际屏幕观感/提示音、舵机手感与长待机重开尚未本轮验收。

**验收边界：** 源码/构建及模拟不代替真人 ASR、M12 长待机重开、真实屏幕听感与舵机动作验收；历史 UART 回传问题、M12 长待机可能不响应的问题未声称修复。M12 的 `valid/function` 仍分别是串口就绪/请求态。


## 2026-11-07：0.4.13 Unit ASR + M12 人脸视图已部署（Claude Opus）

用户先把另一台机器上未提交的 0.4.8/0.4.9 工作推到 `dev`，本机合并进 `main`（提交 `7dfadd4`），保留 main 当天的眼睛资源、M12、耳朵与 `server/ear` 提交；只有 README 冲突，两边内容都保留。

**部署：**
- 机器人运行 **collector-0.4.13**，4,630,112 字节，SHA256 `8aedeffa5035b1beb9540ca1fd295486c9027880862fe7bc8feb50ddd00c2467`，产物在 `artifacts/collector-0.4.13-m12-float/`。
- 每次写入前都重新核对：芯片 ESP32-S3 QFN56 v0.2、16 MB Flash，otadata 的有效活动槽为 ota_1，ota_1 里的版本号。只写 `ota_1 / 0x510000`，并用 `verify_flash` 独立校验。没写 bootloader、分区表、otadata、assets，也没擦 NVS。
- 第一次刷之前，原来的 0.4.8 已整块备份：`backups/ota_1-collector-0.4.8-20261107.bin`（SHA256 `3e553ed1…d4bd`，镜像自带的 SHA256 校验通过），可以原样刷回。
- Android 没有改动，仍是 0.4.7/code17。

**功能：**
- **ASR：** Unit ASR 接黑口，UART2 只收 GPIO8。forward / backward 在首页显示爱心 / 叉号，用的是 0.4.8 那套 1 秒反应，不发声。
- **M12：** 唤醒后说 turn on，M12 经蓝口 UART0（TX17/RX18）切到 Face Detector，屏幕进入人脸页：640×480 画面缩放并镜像，人脸画绿框。说 turn off 或轻触屏幕，回首页并让 M12 待机（Audio FFT）。
- **日志：** 控制台改为只走 USB，以腾出 UART0。

**版本经过：**

| 版本 | 内容 | 结果 |
| --- | --- | --- |
| 0.4.10 | 只加 ASR | 未见程序日志 |
| 0.4.11 | 加 M12 和人脸页 | 每次开机约 35 秒，WiFi 握手申请 AES 中断失败而 abort；坐标按整数读，人脸页永远显示 `no face` |
| 0.4.12 | 串口驱动移到 1 号核，ASR 固定 GPIO8，开机打印中断表 | 不再崩溃 |
| 0.4.13 | 坐标改按小数读 | 用户确认“可以用” |

实测数据见验证记录。

**本机环境：**
- 这台 Mac 新装了 `.tools/esp-idf-v5.5.4`。系统 python3 是 3.14，对 IDF 5.5 太新，所以 `.tools/py313bin/` 放了一个指向 Homebrew python3.13 的 `python3` 链接，构建时放在 PATH 最前面：`PATH="$PWD/.tools/py313bin:$PATH" ./scripts/idf.sh build`。
- esptool 在 `.tools/espressif/python_env/idf5.5_py3.13_env`。
- 刷机过程中有一次 esptool 连不上芯片（No serial data received），用户拔插 USB 后恢复。

**待办和限制：**
- 0.4.9 的情绪角落和双音频随这几版第一次上机，没有单独验收。
- `docs/current-state.json` 本轮没有更新，以本节为准。
- 设备发现的挑战证明可以被转发，安全检查已报告这个问题，用户在调试阶段暂不处理。
- 下一步候选：ASR 结果和 M12 人脸事件转发给服务器（见 `docs/soul-ear-interface.md`）；M12 不必在每次开机后保持原状态，可以默认待机。

## 2026-11-07：本机工作快照转入 dev 分支

用户要求将本机尚未提交的工作保存到 `dev`，避免覆盖另一台机器的 `main`。已获取远端：本机基点为 `517c6c3`，`origin/main` 为 `a8d66cc`，其间新增3个提交（soul eyes资源、M12探索与观察面板、soul ear音频上行接口草案）。`dev` 从本机原基点分出，本次保存当前完整源码和文档，不合并或变基到新main。

本快照包含0.4.8及之前的采集器/Android改进，也包含尚未部署的0.4.9情绪角落、双音频与Mac原型，不能作为纯0.4.8发布标签。此前最后确认机器人为0.4.8 cached、Android为0.4.7/code17，本轮没有连接、刷写或改变设备。用户后续已明确情绪推理只调用别人提供的模型API，优先原始音频；当前Mac源码仍为AIHubMix STT加本地Qwen3-VL，尚未切换。

本轮34项Python/协议/主机测试、Android主机测试与diff空白检查通过；待提交文件的已知本机密钥/令牌及常见密钥格式检查通过。`.private/`、`logs/`、`artifacts/`、`backups/`、工具链和模型权重留在本机。没有重跑固件构建或实机验证；历史验证仍按各节版本区分。后续整合main时需保留上述3个远端提交，不将本快照直接覆盖main。

## 2026-11-01 晚：先在 Mac 验证情绪框架，机器人未刷入

用户将当前任务改为情绪感知：首页指机器人笑面男首页，Mac后台推理；提供 AIHubMix key 后要求从该服务选择 STT，最后两次明确“先在 Mac 测框架”。不要继续催接 USB 或自行恢复刷机。用户选择真人测试稍后，先完成软件验证。先前电源/遥控诊断与旧手机录制待办保留。

已建立 `emotion/` 与 [运行说明](../emotion/README.md)。当前流程为机器人 Wi-Fi 音频/画面 → AIHubMix `gpt-live-transcribe` 实时文字 → Mac MLX/Qwen3-VL-4B-Instruct 4bit 联合推理。YuNet 检查单脸/多脸，无脸仅用文字，多脸或证据不足返回不确定。复用了 stick 中研究资料的证据规则，没有把 Emotion-LLaMA / AffectGPT 的研究链接冒称已运行框架；没有调用阿里听悟。Qwen2.5-VL-3B 首候选中文分类不佳，最终换用固定 revision 的 Qwen3-VL；详情与依赖锁在 emotion 目录，未升级固件工具链。

Mac后台进程由 `python3 scripts/emotion-service.py start|status|stop` 管理，测试页 <http://127.0.0.1:8765/> 已打开并保留。没有安装登录项；Mac需保持运行。私有配置 `.private/emotion-config.json` 中 `mac_preview:true`，不向机器人提交情绪或控制。密钥仅在 `.private/aihubmix-key`，绑定 token/地址继续使用本机私有文件，不能复制到公开说明。日志不记录现场原始音视频、转写文本或密钥；浏览器只访问loopback，当前画面/文字在内存。旧固件单路音频启动前已核对空闲，Mac现占用该路，尚不能与手机采集并行。

真实AIHubMix中文合成语音测试通过，首文字约2.128秒；本地自述/转述/否定/请求与公开单脸/多脸图片冒烟测试通过，明确保留“平静”自述保守漏判限制。34项自动化测试通过。实际600秒无STT输出后关闭WebSocket：超时发生在模型就绪后600.071秒，约2.009秒完成关闭；休眠时继续本地采集，未见该进程443连接。声音重开的假网络测试通过；真人说话、真人图像融合和现场声唤醒尚未验收。测试中累计44次音频序列间断，触发过4次重连，后续恢复；未确认网络/采集/调度来源，不能称流式完全连续。

Mac运行最终代码，页面可独立输入文字样例且不影响现场标签。`logs/emotion-service*.log`、`emotion-idle-live.json`、`aihubmix-stt-synthetic.json`、`emotion-multimodal-smoke.json`、`emotion-all-tests.log`、`emotion-mac-preview-idle.jpg` 均只在本机。结束前重启服务加载日志/时效修正，重新进入监听；10分钟无新文字仍自动关闭。

机器人 **仍为 collector-0.4.8 cached**；Android仍0.4.7/code17，当前ADB无手机。保存10°、CH6/ID1及原控制设置未改。准备中的0.4.9加入首页左下角14px文字、鉴权 `/v1/emotion`、8秒租约/20秒证据有效期、双音频消费者；IDF5.5.4编译、48个face kernel隔离检查和本机LVGL预览通过。产物 `artifacts/collector-0.4.9-emotion/stack-chan.bin`，4618144字节，SHA256 `8ae1077ac168073d3dafa23eb23862e457a1ca5391fbb4ded5b8805632858096`。USB串口缺席导致预读失败，**没有任何写入**；随后用户明确先测Mac，未再尝试。未来若继续硬件部署，必须重新核对身份和活动分区，仅ota_1并独立校验；当前不把首页角落或双音频写成已实机完成。

## 2026-11-01 晚：电池供电与电源键重新开机各一次通过，旧故障未复现

用户明确此前故障发生在拔掉USB后，且当时也是短按主机左侧USB旁的电源键；不能归因于按错键。先通过HTTP随机挑战确认原开发机，电量读数100%、charging=true，启动与此前晚间诊断相同。本轮保持0.4.8固件、Android及所有保存设置不变。

用户拔掉USB后回复“仍正常运行”。主机USB节点消失，遥测charging变false、boot不变，画面及CH6遥控继续工作。随后保持USB断开，按官方方式长按电源键约6秒关机，松手等5秒、短按一次开机；用户回复“正常亮屏进入首页”。网络恢复后boot已改变、运行时间重新计数，另做HTTP随机挑战再次通过；保存角度10°、source=boot/request_seq=0，CH6遥控恢复，相机和音频采集序号继续增长。网络空窗包含主动关机、等待及开机后的首选网络尝试／备用网络回退，不能当作亮屏耗时。

此次各一轮验证了USB转电池继续运行及电池供电下电源键重新开机，未复现此前故障，不能宣布间歇故障已修复。电量百分比为PMIC估计，没有电池负载电压、容量或失败瞬间供电证据，不能直接归因电量不足或硬件损坏。未刷机、未发软件重启或调角命令；唯一新重启是用户执行的电源键关开机试验。

本轮结束保持电池供电、USB断开、屏幕／左右控制开、遥控CH6/ID1、保存10°。旧手机录制封存和助手清理仍是未核实历史待办。实测统计见验证记录，本机证据 `logs/power-remote-v049/{usb-unplug-test.jsonl,power-result.json,power-user-observations.jsonl}`。按键方式核对自[M5Stack官方说明](https://docs.m5stack.com/en/StackChan)。

## 2026-11-01 晚：继续不能开机／遥控失效诊断，频道不匹配已确认

本节优先于下方16:59的表情优化收尾。用户明确当前任务是“不能开机和遥控器失效”，不是补刷表情日志版。晚间已有证据在本机 `logs/power-remote-v049/`；目录名不代表已发布0.4.9，机器人仍运行0.4.8。本次续接未刷机、未重启、未修改设备设置或控制源码。

20:44串口记录显示一次USB_UART_CHIP_RESET后从ota_1成功启动，显示、相机、麦克风和遥控接收器初始化完成。20:46遥测显示首选网络未连接，已连接备用网络CH6，接收器ready、控制enabled，但rx_packets=0。续接通过HTTP随机挑战核对原设备，boot与该记录相同，已运行约37分钟；当时电量98%且充电，软件显示enabled。不能把USB供电下这次成功运行当作独立电池冷启动通过，也没有捕获用户所述失败现场。

用户确认遥控器为CH11/ID1/摇杆模式；按提示改CH6/ID1后回复“可以”。实测接收计数增加、remote.valid=true、motor.state=following、applied_seq前进。此次遥控失效定位为频道不匹配：机器人接收频道跟随已连接Wi-Fi，首选热点未连接后备用网络使用CH6，遥控器仍固定CH11。原策略每网络尝试三次、每次间隔约10秒，已连通时不主动切回首选；首选连接失败的具体原因尚未记录，不能断言热点关闭。未新增自动找频道功能。

当前保存仰角为 **10°**、persisted=true、request_source=boot、request_seq=0；20:46及续接均如此，不能再恢复旧交接14.5°。本轮没有提交角度。原厂自动省电计时器在collector模式下已由ShouldEnablePowerSave返回false；启动日志中的sleep300/shutdown600只是初始化参数，不是已发生自动休眠或关机的证据。

开机问题仍待用户说明是脱离USB不能启动，还是接USB也黑屏，并捕获对应状态；保留当前供电和已恢复遥控，不凭推测调整PMIC或刷机。此前手机录制封存、监听状态和临时助手清理仍未核实，当前ADB无手机；这些是历史待办，不应再次取代电源诊断。详细本机证据及接收窗口见验证记录。

## 2026-11-01：0.4.8 表情延长至1秒，缓存绘制已部署；USB中断后收尾待续

已部署固件 `collector-0.4.8`，4610384字节，SHA256 `f1144c5eb61810fe134d26e456b53bd78bd8a2f55f702deda8df63d8712ae411`，产物 `artifacts/collector-0.4.8-reactions-cached/`。已核对原开发机器人身份、分区表和活动ota_1，仅写0x510000并独立校验。Android仍为0.4.7/code17，遥控器未更新；14.5°保存角度、左右控制和中心开关屏保留。IDF5.5.4、依赖锁和repos.json不变，人脸库48个符号隔离检查通过。

上推爱心、下推实心叉号的显示时长统一为1000ms，info/telemetry/gesture三个字段一致，从UI接受新鲜事件时计时。输入500ms判鲜、提示音360ms/80%与待播750ms界限保持独立。原绘图在每个屏幕分块中重复执行逐行多边形填充；现在启动时缓存两张168×154 RGB565，共103488字节PSRAM，事件时只复制，分配失败回退向量绘制。显示及恢复只刷新260×240肖像范围，保留左右黑边；颜色、实心样式、无外圈和文字环暂停/恢复保持。

同一主机/LVGL部分刷新配置的30轮对照：爱心绘制中位1026.5→40µs，叉号1406.5→39µs，恢复66.5→47.5µs；每次刷新76800→62400像素，减少18.75%。主机即时flush不模拟S3/SPI或竞争负载，不能把该倍数称为实机提速。像素比较只见RGB565量化差异（最大通道差7），预览已目视核对。24项协议/主机测试、LVGL120帧/1000ms边界/表情替换/页面/熄屏测试及刷新面积预算通过；本轮未改或重复构建Android。

实机记录5次表情（三次爱心、两次叉号），输入处理到刷新提交的日志前缀间隔为60/90/40/60/90ms，显示提交至恢复提交为1000/1000/1040/990/970ms。首候选newlib nano不支持新增日志的%lld，细分数值显示为ld，以上只能用10ms粒度日志前缀计算，不能作为DMA完成或实际亮屏测量。格式已改为有界32位打印并编译，待补刷产物 `artifacts/collector-0.4.8-reactions-timing-unflashed/`，SHA256 `ea50ea5ea959784eca225099cdf25a78e050e147a261f3af60a108cdf46ace5a`；机器人USB已断开，预读串口失败，**这次未写入**，目前运行的仍为上面的cached产物。用户肉眼流畅性反馈待回，不能标卡顿彻底解决。

vivo已开始录制并开启监听/人脸框；119.711秒外部观察取得343条合法遥测，人脸框全程开启，camera/audio/skipped错误增量0，保存14.5°、request_seq0和左右控制保持。随后手机USB断开，尝试恢复人脸框关闭、结束并拉取App记录均未能执行，未取得录制文件，不能声称音频/IMU无缺口、会话已封存或最终开关状态已恢复。之后机器人USB也断开。已请求用户重接并反馈表情；下一轮先核对手机记录是否仍在进行、封存取回并恢复测试前的监听关/人脸框关，再补刷诊断格式修正版并复测。不要因此重新索取已授权刷机或动作权限。

电脑上的串口记录已保存并停止；电话localhost诊断助手自带10分钟期限，断开后未能远程移除临时jar，重接时清理本轮jar/助手及自建转发，勿操作其他设备。当前实机显示优化已生效，但手机录制收尾、最终状态核对与观感确认仍未完成。私有证据 `logs/reactions-v048/`；原始媒体/身份/凭据不提交上传。

## 2026-11-01：0.4.7 人脸框试验及首页开关

固件 `collector-0.4.7` 与 vivo Android `0.4.7/code17` 已部署。最终固件4608592字节，SHA256 `71b4f15782821233e921a49585b2c5713fa8faa8f921e1bdfa0b66f5d30c77f2`，产物 `artifacts/collector-0.4.7-face-final/`；最终APK94685字节，SHA256 `8a58d664dea2439a5f8a7126d7c503f57409541cf7a056c7ae71f2184913f774`，产物 `artifacts/android-collector-0.4.7-face-final/`，安装包逐字节核对一致。各次固件候选均核对同一开发身份、分区表和活动ota_1，仅写0x510000并独立校验，没有写其他分区或升级IDF。临时输入转储、分级诊断和模型自测代码已移除，最终直接使用官方MSR/MNP。遥控器未刷，14.5°保存设置、中心开关屏和左右控制未改。

首页仰角下方将监听、人脸框开关并排，保留44dp触控面积。检测在机器人本地运行，160×120 RGB输入，最多4框；原图与同帧框元数据一起发往App，绿色角框按FIT_CENTER映射。关闭、断线、状态过期、epoch变化及画面停更1.5秒会隐藏；关闭后释放模型和输入缓冲。只在本次启动有效，重启默认关闭，不写NVS、不触发舵机。原图与框事件分别记录，当前回放不叠框。尚无人形检测、身份识别或自动跟随。

新增固定human_face_detect0.4.1及ESP-DL3.3.0，原有依赖版本/哈希保留；先前解析器自动升级的原组件已恢复并加精确约束。只启用所需RGB888转换，模型嵌入应用rodata，沿用原分区。首候选出现左侧固定误框：实际输入图像与模型输入逐字节量化核对正确，MSR即产生异常结果；链接map显示新ESP-DL调用了ESP-SR2.3.1的同名卷积函数，反汇编显示bias处理实现不同。构建仅为新ESP-DL的48个重复底层符号加前缀，保留原语音库；隔离后空景误框消失，真人检测出现。构建加入符号碰撞/错误引用检查，正例通过、碰撞反例拒绝。官方模型没有导出测试输入，调用Model::test失败不能冒称数值自测失败或通过；该诊断已移除。

用户完成正脸/左右移动后确认“可以了”，同时指出2fps太低。2fps为首轮保守软件上限；最终改为目标4fps，单核检测仍与JPEG顺序处理同一帧。最终159.992秒vivo会话已封存校验：682张JPEG全部解码，1367条遥测协议通过，7987音频块/7992IMU样本序号无缺口，写盘丢弃及camera/audio/skipped增量均0。246条框事件均对应保存的同boot/seq/timestamp JPEG；两段开启分别有37/42个有脸帧，实机截图框位置正确。App开启/关闭两轮均有确认，epoch0→1→2→3→4，最终模型已释放。

目标4fps不等于持续4fps：排除切换后3秒，两段检测开启实收2.864/3.238fps，短时界面3.9fps；推理中位141.650ms、最大719.071ms，最长收帧间隔1.283秒。关闭前两窗口7.914/7.270fps，但最后关闭窗口仅2.726fps，说明还有其他负载/传输波动，不能称关闭后始终满速。音频无序号丢失但24个时间戳间隔偏离20ms超过5ms；IMU有7次间隔超过150ms、最大380.667ms。磁场1367/1367有效，光照1352/1367、接近1353/1367；仍见过期/静态字形缺失，不宣称全程流畅或旧舵机问题修复。更高帧率需另评估手机端检测，当前未实现。

固件最终构建、24项Python/主机测试、Android主机及原生control/recorder/ui通过，HUD覆盖960×450、800×450、640×360、640×480dp；最终仅帧率常量/无障碍说明更新后重建两端，24项重跑通过。合成测试、首轮2fps用户认可和最终4fps真实录制证据分别保存，不互相替代。最终首页未记录、人脸框关、监听关（结束截图所见；开始录制时监听开），转头/屏幕开、保存14.5°。临时ADB桥/转发/jar已清理，未动其他设备。私有证据 `logs/face-v047/`；未上传现场图像或身份信息。

## 2026-11-01：0.4.6 机器人同向转头、声音表情、首页控制与首选热点

最终固件为 `collector-0.4.6`，3527328字节，SHA256 `e040a5c52e409a227a30f64ecd1e9132a49879d40c098ad071b64b6a9ac4cc02`；保留在 `artifacts/collector-0.4.6-controls-louder/`。每次写入前均核对开发身份、分区表和有效活动ota_1，仅写0x510000并独立verify，未写bootloader、分区表、otadata或资产。Wi-Fi通过已有USB配置通道写独立collector首选键，保留旧网络、工厂信息、配对令牌与校准。Android `0.4.6/code16` 已安装vivo V2337A，90589字节，SHA256 `fd54b2b87ace82bd3edf8435f802d66a87c7bd527e8a50eece951a94529842b0`，已安装APK与保留产物逐字节相同。模拟器生产App仍旧版且停止，本轮用独立测试包。

左右执行目标取发送端yaw的相反数，并保留原始包、零点、速度与原厂限位/平滑/卸力。第一次网络窗口1453组对应包/执行目标及热点窗口1120组比较均无映射不符。上下仍不操作俯仰；上推实心爱心500ms和上扬音，下推实心叉号500ms和拒绝音。用户反馈首版叉号音偏小，最终版提高拒绝音源电平及中高频，codec保持80%；用户确认“合适了”。本体播放11个作业已观察，3次有界等待，最终drop0、播放/关闭错误0、功放复位读回true。只保留最新等待音、750ms失效，HTTP媒体播放不插入表情音，长推不循环。原始首版有3次忙碌丢弃，不混作最终通过记录。

首页中部加入监听开关和±5°仰角按钮；立即提交并持久化，到5–85°边界截停，断线/过期/停用/待确认/执行中禁用，无长按重发。宽屏底栏92dp、窄屏116dp分行，所有按钮44dp；紧凑六轴图支持80dp高度，避免窄屏按钮增高后图表消失。手机在本轮自行调至14.5°后，自动化实际点加/减验证14.5→19.5→14.5，request_seq3/4，均trajectory_complete并保存；当前保留 **14.5°**。轨迹结束不是实测到位，舵机反馈旧问题仍未解决。

用户提供手机热点并要求首选。两次按文字拼写配置时均回退到旧网络；随后读取已连接手机的实际热点名称和2.4GHz状态，以实际名称修正首选（凭据与名称仅留.private）。最终开机约4.5秒关联首选、频道 **11**，遥控已切CH11/ID1收包。手机开热点时Wi-Fi客户端关闭，之前到旧LAN不可达；改连热点后，App通过既有签名发现与挑战自动迁移地址，未改绑定、无需输入码。`wifi_profile_count=2`、`wifi_preferred_configured/on_preferred=true`。每次开机/重连先尝试首选，最多3次后回退；健康连接不强行漫游。Mac未改网络；短时诊断通过仅监听phone localhost的ADB转发读取热点内机器人，验证后清理。

固件23项主机测试通过；最终提示音电平/边界和单条等待/过期测试重跑通过。Android主机、原生完整control/recorder/ui通过；最终HUD/UI通过960×450、800×450、640×360、640×480dp。LVGL120帧动画、实心图形/无圈/500ms、页面与熄屏检查通过。手机首页监听开启、记录与调角实际操作后，87.145秒会话正常封存并通过verify-session；263张JPEG全部解码，实收3.024fps、最长接收间隔1.332s；4346音频块、4345IMU样本序号无缺口、写盘丢弃0、camera/audio/skipped错误增量0。IMU有6个大于150ms的真实时间间隔，最大254ms，界面如实提示间断；768条遥测环境三组均valid，但实际画面仍偶有过期与静态缺字。yaw/pitch反馈有效仅135/128条，不能称舵机或整体流畅性恢复。未做眼镜/折叠切换验收。

结束状态：首页、vivo监听开启、录制停止，控制与屏幕保持开启；首选热点已连接，遥控CH11，仰角14.5°保存。证据在 `logs/vivo-v046/`、`logs/collector-v046-hotspot-live*.json*`、`logs/hud-v046/` 和上述私有产物。922个非忽略文件扫描本轮SSID/密码/令牌/IP/手机序号零匹配，diff检查通过；不提交或上传媒体、密钥和现场日志。

## 2026-11-01：Android 0.4.5 已安装 vivo，底栏文字放大

已核对实体手机为 **vivo X Fold3 Pro / V2337A / Android16**，仅对该目标安装 `0.4.5/code15`；90589字节，SHA256 `8fafde8ae566c60effe005ebc455e6a08bb6a5710a616122081488416bbe64fb`。手机已安装APK与保留产物逐字节一致。用户重插并授权后ADB识别成功，先前“待连接安装”状态已完成。模拟器也有同版，但为了释放机器人单路音频客户端，已停止模拟器观察台；当前观察与监听在vivo。

基础参数10→16sp亮绿/medium，分电量与Wi-Fi、遥控与转头/屏幕、设置/实测角度三行；连接和记录10→14sp，画面帧率10→12sp，底栏56→72dp。相机保留完整画幅，控制、连接协议及传感器判鲜未改。APK构建、既有原生HUD套件通过，800×450、640×360、640×480dp及模拟器实际布局已核对；未为此次字体调整重复主机/完整控制套件或另做并发录制，0.4.4采集数据为历史。

首次手机绑定由本机现有授权记录迁入App私有SharedPreferences，XML经stdin写入并核对、文件600权限；地址为手机可直达的机器人局域网地址，不沿用10.0.2.2模拟器转发。迁移前主机HTTP nonce/HMAC挑战匹配原设备，普通启动不附带地址/令牌extras。手机已自动连上，实际截图显示相机、音量柱、六轴、磁场及光照/接近在更新，放大底栏完整；“声音监听：开启”菜单已核对，录制关闭。保存24.5°、转头/显示开启在界面可见，没有提交新控制或调角请求，机器人/遥控器未刷写或重启。

本次为外屏横屏2748×1172的短时实际显示检查，未测试折叠切换、Epson眼镜输出或长时间采集/听感。手机一次ADB截图亦见静态字形短暂缺失、后续恢复，来源未定，不应再仅归为模拟器故障；未以有限截图宣称渲染问题解决。当前源码/产物、凭据、设备序号和媒体只存本机，917个非忽略文件扫描本机令牌/地址/手机序号零匹配。证据 `logs/vivo-install-v045/` 与 `artifacts/android-collector-0.4.5-readable/`。

## 2026-11-01：Android 0.4.4 新HUD已安装，固件仍0.4.3

`0.4.4/code14` 已安装到开发观察台模拟器，APK90,589字节、SHA256 `04d3676faba56541d9fed31a4fd22b219546658ee6d062d2b1eae91aae794a42`。本轮未刷写/重启机器人或遥控器，机器人仍为collector-0.4.3。

按用户图片：左侧瞬时音量柱，中间完整相机，右上加速度/角速度左右并排共4秒，下方为磁场XYZ µT及XY投影，光照/接近16sp。用户确认“位置”指磁场/朝向；箭头仅是传感器坐标磁向量，固定标“航向未校准”，不冒称机器人位置或地理朝向。音量柱为最新20ms首通道RMS，−60…0dBFS固定刻度，1秒过期清空；原始记录不变。磁场绘制时加入单调年龄，失败/过期隐藏值和箭头。

主机、APK构建、原生ui及独立hud通过，三种尺寸截图和实际首页已核对。首次图表fixture用全部变化像素导致位置断言误判，仅修测试定位对象后通过；生产绘图未因该失败修改。脚本要求明确PASS，避免仪器失败被ADB退出码掩盖。固件/完整控制录制套件未重复，动作与手机眼镜未验。实际并发采集与结束状态见下文；证据仅本机 `logs/hud-v044/`、`artifacts/android-collector-0.4.4-hud/`。

最终保留版本录制45.653秒：318JPEG全解码/6.988fps，2270音频和2270 IMU序号连续，433条遥测协议通过，写盘/采集错误增量0。磁场、光照/接近433/433有效，LTR新增1216次事务、错误3→3；24.5°配置与控制/亮屏保持，两轴同时反馈有效仅14/433，旧舵机问题未解决。实际约25秒和结束截图仍有静态缺字；随后仅模拟器首页软件layer试验同样复现，已撤回、重新安装上述SHA，源码与该artifact一致。未证明整体渲染/听感通过，vivo/Epson未验；当前首页、监听开启、记录停止。详细数值见验证记录。

## 2026-11-01：0.4.3 新增传感器已部署，舵机已有接收帧错误证据

当前固件 `collector-0.4.3`、Android `0.4.3/code13`，最终固件 3,522,640 字节 / SHA256 `f55215a4aef6751933a25eda96b4d3180545b3d9d36a886619b884d8089d9404`，APK 82,397 字节 / SHA256 `53a3214e4a55287ab1308df86e9f4ccd9d713b4ddbd9d337df34e6e327b84e6f`。机器人身份/分区核对后只写 `ota_1 / 0x510000` 并独立校验，遥控器未刷；IDF 5.5.4 和依赖保持。保存俯仰 **24.5°** 跨本轮重启仍保留（source=boot/request_seq=0），没有提交新角度，不得恢复旧13°。

BMM150 通过 BMI270 AUX 采集工厂补偿磁场 XYZ，LTR553 采集光照 lux/接近 raw，均目标 10 Hz，实时遥测、App 和录制/回放已接通。首页底栏显示光照/接近；「⋮ → 机器人设置」显示磁场和详细状态。无效值留空、1 秒判过期；磁场没有整机磁校准/航向，接近不是厘米。光照/接近照灯和手掌测试已响应，但动作发生在第二候选，应与最终并发录制分别统计。

LTR 初版 10 ms 初始化失败已改为 50 ms、仅 I/O 错误最多3次；第二候选运行仍10 ms错误较多，最终改50 ms并记录共享运行错误，不无限重试/补采。最终启动36条快照传感器均有效，但1040次LTR运行事务仍有1次超时后恢复。当前三个同版本产物必须依SHA区分，不能沿用首候选未连接或第二候选错误数。详见最新验证记录。

舵机修正扭矩寄存器读宽（2字节→1字节），增加被动事务/回包、保留目标写和卸力回执、UART错误事件诊断，以及一次只读死区（两轴均1/1）。最终显式开启FRAME_ERR中断，启动期间已捕捉1234个新增接收帧错误；旧版本该中断未开，0计数不说明没有问题。通信异常实际存在，来源未定；原厂串口解析/超时、平滑、限位、堵转、校准未改，不自动切RS485 RTS或修改EEPROM。

用户明确从机器人视角左侧回中不到位：第二候选抓到零目标成功写入后，卸力前−2.8°、卸力后−3.1°；不能单归因卸力后松动，也不能把ReadMove0当精确到位。最终静置窗口仍大量位置无效，未修复通信稳定性/回中偏差。未部署曾讨论的延时保力试验，若继续必须有明确可证伪对照、停止取消和时间上界，不应盲目补偿校准。

本轮22项Python/协议测试、Android主机、完整原生与HUD及固定IDF最终构建通过；UBSan通过，ASan运行环境未完成。真实vivo/Epson仍未连接验收，模拟器旧卡顿/缺字尚未解决。最终采集结果和结束状态见下文；详细数值及限制以[验证记录](collector-validation.md)本轮段落为准。证据仅本机 `logs/sensor-servo-v043/` 及两个最终artifact目录，未提交或上传。

最终63.597秒单段并发录制已封存：296 JPEG全解码/4.706fps，3144音频块和3134 IMU连续，IMU50.024Hz，写盘/相机/音频错误及跳块增量0。全部255条遥测协议检查通过，三组新传感器均255/255有效，更新率约10.029/9.150/8.701Hz；LTR新增1737次事务、新增错误0，保留早期1次超时。遥测批量到达最长1.005秒，外放听感未验，不能称整体无卡顿。

最终两轴同时有效仅9/255，yaw37/255（−3.1°）、pitch46/255（24.0°），未做最终版新物理动作验收。结束App回首页、监听/控制/显示开启、记录停止封存；配置24.5°不变。启动末到录制后的299.824秒UART帧错误新增11078，仍未解决。最后pitch卸力记录是录制前两次no_reply中的后一次，不能称最后卸力已成功。现场媒体/日志仅本机，不提交；当前状态优先于以下旧记录。

## 2026-11-01：Android 0.4.2 音频判鲜与 4 秒合并图已部署

本轮只改 Android，`0.4.2/code12` 已安装到原开发模拟器，APK 82,397 字节，SHA256 `00c001df22a5227d35331d0c5c0e15aaa568c57f06d58a310d58bbe346152318`。机器人仍为 collector-0.4.0，机器人和遥控器未刷写或重启；固定工具链及依赖不变。标题行整体移除，相机使用释放出的高度；基础状态和「⋮」移到底栏。加速度/角速度共用一个图框、上下分区及一条 4 秒时间轴，单位/量程独立，缺样仍断线；完整 CSV 记录不受显示窗口限制。

音频误判的确定路径：旧 UI 在刷新时缓存 now，异步音频包可在绘制之前更新 receive；旧判断把 receive > now 当过期，连续输入也会闪烁。现改用接收和实际绘制时的 elapsedRealtime，先快照再取当前时间，负年龄不当作停包；真实停包 1 秒仍标记过期。删除图中央重复提示，仅保留固定状态和变暗旧波形。主机覆盖旧刷新/新包时序、精确 1 秒边界、重复/倒序不刷新；原生测试覆盖延迟绘制、停止和恢复、默认单调时钟。测试没有修改设备真实系统时钟。

主机测试、APK 构建、原生 `ui` 与独立无绑定 `hud` 通过；4 秒边界/高频容量、双单位合图和 800×450、640×360、640×480 dp 布局及二级菜单检查通过，合成图逐图检查无裁切。完整控制/记录原生套件和固件协议测试沿用历史证据，本轮未重跑；未做新的机器人屏幕/动作验收。

最终监听录制 **33.142 秒** 已封存并通过 verify-session：119 张 320×240 JPEG 全像素解码，实收 **3.663 fps**、最大接收间隔 546.025 ms；1644 音频块序号连续、WAV 完整（24 kHz/双通道/16 bit，32.880 秒），最大接收间隔 172.938 ms、无 1 秒断流；存在 3 处设备时间戳误差超过 5 ms（−19.735 至 +24.987 ms），不能称均匀采样或听感通过。1644 IMU 约 50.003 Hz、零无效/序号缺口，保存跨度 32.858 秒，其中 1443 条早于最后 4 秒；133 遥控包连续。写盘丢弃、相机/音频错误及跳块增量 0。开始事件为监听开启，期间无监听切换；系统前后均显示该 App AudioTrack started、24 kHz 单声道、未静音，由模拟器 CoreAudio 输出到 Mac，未擅自调系统音量，听感未验。

实际操作为开启监听、进入记录开始、回首页、约 8/16/24 秒截图、进入记录停止、回首页。8/24 秒图可见音频收包中、4 秒坐标及持续记录；16 秒和停止后截图部分字形缺失，后续/其他截图又可正常显示，原生 TextView 和自绘文字均受影响。仅据这些图不能确定根因或宣称整个窗口无过期提示；确定竞态由回归测试验证。模拟器既有 HWUI/系统卡顿未解决，本轮不称整体渲染或视频流畅性通过。真实 vivo X Fold3 和 Epson 1080p 有线眼镜未连接/安装/验收，专用外屏传输未实现。

314/314 遥测控制请求/启用及软件亮屏保持，点头 disabled、连续 pitch_target null；两轴反馈有效仅 12/314，旧回传问题仍在。前后与全段均同一 boot，保存角度 **24.5°**、persisted=true、request_seq1 未变，没有提交调角；有效俯仰反馈 23.4°不等于配置到位。当前首页自动连接、监听开启、录制停止，控制和显示开启。

传感器清单已对照官方规格与当前采集调用：相机、麦克风、BMI270 六轴、头顶三区触摸、屏幕触摸、电池/充电及舵机位置已接入；BMM150 磁力计、LTR553 光线/接近、底座 INA226 电压/电流尚未发布到 App，NFC/红外也未接入。舵机可选负载/电流协议字段不代表实际采集，当前 HAL 只取位置。原装 CoreS3/StackChan 底座无 3.5 mm 监听口；监听通过 App 输出。

本机证据：`artifacts/android-collector-0.4.2-audio-freshness/`、`logs/audio-freshness/`、`logs/collector-v042-audio-freshness/`；原始媒体与凭据不提交上传。历史 0.4.1 状态以下保留，不能用其监听关闭、8 秒图或旧 APK 覆盖当前状态。

## 2026-11-01：Android 0.4.1 黑绿第一视角首页已部署

本轮仅修改 Android UI，`0.4.1/code11` 已安装到原开发观察台模拟器，APK 78,301 字节，SHA256 `155e57f6acb28a5dfd8ad244020d2ba3f08ed7f67a9e0de8440d897822de1aee`。机器人仍为 `collector-0.4.0`，未刷写或重启机器人、遥控器，未升级依赖/工具链。用户指定后续目标为 vivo X Fold3 与 Epson 1080p 有线眼镜；当前没有实体手机接入 ADB，未实现专用外屏传输，也未在该手机/眼镜安装、输出或验收。

首页纯黑，亮绿文字与曲线；相机区域约占 70%，以 `FIT_CENTER` 保留原色与完整画幅，无中央叠字。侧边三个固定区域为麦克风、加速度、角速度；底栏为电量/充电、Wi-Fi、最近遥控输入、控制、保存/有效实测角度及屏幕状态。遥控最近输入不冒充在线心跳；失效/超过有效期的实测显示「—」。首页不滚动，记录列表位于「⋮ → 记录」，角度及连接位于「⋮ → 机器人设置」，声音监听也在该菜单。记录/设置为同 Activity 浮层，打开关闭不触发后台封存；原后台/锁屏停止规则保留。Android12+ 启动页和图标资源亦改为黑绿，AAPT2 编译/资源检查通过；本次截图时序未抓到最终系统启动窗，不据此宣称全程无白闪已实测。

音频显示真实首 mic 通道 PCM 的 2 秒、5 ms 桶 min/max 包络，固定 ±1 FS，不自动增益；弱音可接近水平线。序号/时间/格式间断、不新鲜和断线明确显示，不跨间断连线；「间断」不是丢包计数。已知 boot 改变立即清音频窗口。六轴仍为 8 秒真实样本，XYZ 使用不同绿阶及实/虚/点线。UI 计划刷新约 15 fps，文字最多 4 Hz，不代表设备实际达到该帧率。

主机测试通过，新增真实PCM解码/首通道隔离、静音与弱音不放大、峰值、间断/重启/回绕、窗口界限和并发快照。原生 `ui` 合成检查通过，含紧凑图表、音频状态和角度控件；`hud` 独立无绑定 Activity 检查 800×450、640×360、640×480 dp，首页边界、无滚动/历史列表、完整相机及二级菜单导航均通过，逐图检查未见裁切。完整控制/记录原生套件和固件协议测试为此前0.4.0证据，本轮不冒称重跑。

最终 APK 实时录制 54.868 秒：从记录面板开始、回首页、打开设置并滚到角度、回首页、重新开记录面板停止；45秒截图仍显示正在记录，文件仅一个 start/stop、单 boot/单分段，无连接中断事件，正常封存并通过 `verify-session.py`。131张320×240 JPEG全部像素解码，实收 **2.437 fps**，最长接收间隔2.033秒；最新帧轮询略过91个生产序号，不能称全帧保存。2,687音频块的PCM/WAV索引完整且无序号缺口，时间戳偏差超过5ms为0；2,657 IMU约50.011Hz、215遥控包均无序号缺口或历史缺失。写盘丢弃、相机/音频错误及跳块增量0。声音外放监听全程关闭；完整采样不等于听感无杂音。

模拟器仍明显卡顿，启动阶段有缺样/过期提示。最终进程的gfxinfo（包含启动、菜单、截图等操作）为525帧中478帧janky、p50 450ms；冷启动10.738秒。该窗口没有受控基线，不能把卡顿全部归因新UI或单独归因模拟器，旧HWUI/SystemUI问题也未解决；本轮不标视频流畅性通过。实机手机/眼镜可读性、延迟和长期采集待另验。

157/157遥测控制请求、启用与亮屏保持，点头disabled，连续pitch_target仍null。两轴有效仅2/157、俯仰有效18/157，仍有旧回传问题；有效俯仰23.4°不等于配置24.5°。本轮开始前已读到持久保存24.5°、request_seq1，全程和结束后未变；未点击应用或提交调角，旧13°重启验证保留为历史，不自动恢复。当前App已回首页自动连接、记录关闭、监听关闭，左右控制和显示开启，机器人boot未变。

证据仅本机：`artifacts/android-collector-0.4.1-hud/`、`logs/hud-ui/`、`logs/collector-v041-hud/`。现场媒体/身份/令牌不提交上传；900个非忽略源码文件扫描当前本机令牌无匹配，`git diff --check`通过。

## 2026-11-01：关节轻响对照与观察台网络已恢复

用户反馈底座/关节轻微嗡嗡或吱吱声。检查时机器人播放状态 idle、job0、输出关闭，功放硬件复位读回 true；控制仍启用，保存俯仰 13°，新鲜有效实测已为 20°，不能用保存值代替实测，也未据此自动重新调角。短暂发送 remote enabled=false，取消动作路径直接发出两轴卸力指令；用户回复“明显变轻，但还有”。这支持声音部分与舵机通电有关，但未证明剩余声源或卸力读写失败。随后恢复 enabled=true，遥测 requested/enabled 均 true；未重刷或重启机器人。

源码只读核查：16 字节遥控相同 yaw/speed 心跳去重，不反复启动轨迹；原厂自动卸力仍依赖 ReadMove 和扭矩读取，串口失败可能阻止卸力，当前遥测不包含这些读取或扭矩关闭回执，所以仅为待验证路径。不能把 control.enabled 当作真实扭矩状态。

观察台实际运行在电脑上的 emulator-5556，ADB 当前没有实体手机。断开时模拟器没有活动网络、Wi-Fi 未连接；电脑直连机器人和本地 relay HTTP 均正常。切换网络及 guest reboot 没有恢复，截图出现 System UI 无响应；已对同一个 AVD 完整退出重开，保留 userdata、App、绑定和录制。冷启动后重新连接 AndroidWifi 成功，默认网络与路由已恢复；App 无输入凭据自动连接，实机确认 audio_client=true，画面序号及六轴持续更新。冷启动再次出现 System UI ANR 和“Data is paused / 达到流量上限”弹窗；当前 netpolicy 快照是 limit=-1、warning=2 GiB，因此该弹窗不能单独证明断网根因。本次保存的 System UI ANR 栈显示启动时等待 SurfaceComposer 的 getGPUContextPriority Binder 返回；同期图形服务负载高，仅取得一条启动 ANR，反复弹窗不代表多次新 ANR。该证据不证明最终根因，未据此修改图形配置。关闭模拟器移动数据，保留 Wi-Fi 通路，处理系统弹窗后界面恢复可用；短窗仍见媒体低帧率/运动缓存缺样，不能称系统卡顿根因已修复或做了连续录制验收。App 声音外放监听暂留关闭以排除回声，音频采集连接正常；录制未开始，左右控制和屏幕开启、保存角度 13°不改。机器人 boot 未变化，未刷机。证据在 `logs/sound-reconnect/`，所有原始媒体和标识仅本机。

## 2026-11-01：0.4.0 自动连接与持久抬头角度已部署

用户要求 Android 与机器人自动连接、不再重复输入码；角度设置“立即调整并记住，下次开机沿用”。已部署固件 `collector-0.4.0`，3,505,376 字节，SHA256 `74a4cad4d7a3f3af9a834aa42976d3f2f5fe41cf93142267b914ceb294bf1d77`；固定 ESP-IDF 5.5.4，核对开发设备身份、分区与 otadata 后仅写 `ota_1 / 0x510000`，独立校验通过。Android `0.4.0/code10` 已安装，70,109 字节，SHA256 `e5261e472b6ac4bea350949b2abb255ebfff1fbac4e40f42fe747292a4e88ff9`。遥控器未重刷，依赖锁和工具链未升级。

App 私有存储沿用已有授权绑定；普通启动、返回前台和机器人重启后自动连接均已观察，无需输入地址或配对码。所有保存地址和发现地址先通过随机 nonce/HMAC 的公开 HTTP 挑战，再发送原 Bearer；UDP 仅发送绑定 ID/nonce，不广播凭据。主机真实局域网单播、广播已通过；Android 原生测试覆盖错误证明、ID/nonce、旧回复和重定向不会触发 Bearer。模拟器仍使用本机固定转发地址，**Android 真机跨 IP 迁移尚未实测**；清除数据或新手机首次绑定也不在本轮免码恢复范围。退出前台仍停止/封存记录，没有后台采集能力。

“抬头角度”支持 5–85°、0.1°步进，拖动只改草稿，点击“应用”才保存并排入一次立即执行请求；NVS 提交成功后才运动，下次开机读取保存值。默认 13°；停止状态拒绝且不保存，不由角度请求擅自启用控制。保存、停止与重启竞态及请求丢失后不重放有主机/Android 测试。实际新鲜反馈不可用时等待最多 5 秒后放弃本次运动，仍保留设置；运动轨迹最长 15 秒，不自动重试。原厂平滑、动态限位、堵转与卸力保留；仅允许有效实测在下限以下最多 2°时向范围内部恢复，实测与命令起点分别记录。上下摇杆仍是表情、中心仍开关屏、点头 disabled，连续 `pitch_target_deg=null`。启动目标反映本次开机读取的设置，不被随后 App 修改倒写。

首个 0.4.0 候选仍为 10 sockets：App 三路接收加两个 HTTP 内部各 3 sockets 和 UDP 共用完总额，新增主机 HTTP 连接连续重置。该问题已定位到 lwIP socket 容量和 HTTP 会话槽回收条件；最终配置改为 16，并增加最少 12 的构建断言。最终录制并发额外主机 30 秒、64 次请求全部成功。HTTP 挑战、非法 nonce/角度/鉴权、超大 body 提前关闭及不改变设置均通过，UDP 单播/广播复测各首包成功；此前一次未标路径的 UDP 超时原因未明，不能宣称被 socket 修改修复。UDP 本身有 200 ms 全局回复限频，客户端持续重试。

首候选 App 将 13°应用到 15°后，有效反馈从 11.8°经 12.1°到 13.4°，不能称实测到达 15°；15°已跨重启读取保留。其 48.197 秒录制有 375 JPEG、2,398 音频块、2,397 IMU、201 遥控包，写盘/采集错误增量 0，仅作为被替换候选证据。最终 socket16 固件另做独立录制：**32.896 秒，174 JPEG 全像素解码，实收 5.372 fps；1,617 音频块完整 PCM/WAV，1,627 IMU 约 50.065 Hz、137 遥控包，音频/IMU/遥控序号无缺口，写盘丢弃及相机/音频错误/跳块增量均 0**，`verify-session.py` 通过。音频 4 处时间戳偏差超过 5 ms、IMU 最大间隔 102.325 ms；序列连续不代表均匀采样或听感无杂音。最新帧轮询并不保存每个生产帧。

最终录制通过 App 恢复保存 13°：响应 219 ms，首次观察轨迹结束距设备请求约 4.163 秒；有效实测全段为 13.4°，未观察到这次请求引起可分辨的新角度变化，不能称机械精确到达 13°。142/142 控制请求/启用、亮屏和点头禁用保持；两轴有效仅 25/142，俯仰有效 50/142。随后明确重启，51/51 启动样本控制和两轴有效，确认 `configured_deg=13`、`persisted=true`、来源 boot、seq0、启动目标 13°，轨迹结束/有效实测 13.4°；App 前台无人操作自动恢复连接。这个良好启动窗口不能证明旧舵机反馈根因已修复。

19 项 Python 测试、Android 主机测试、原生连接/控制/记录/渲染测试、固定 IDF 构建通过。最终 UI 微调后补跑原生 UI 测试通过；本轮未重跑真实 LVGL 屏幕验收或要求用户重复中心开关。旧模拟器 HWUI/ANR 原因未修复；本次启动曾短时缓慢和初始运动缓存缺口，最终录制内没有历史缺失。当前保存 **13°**、屏幕亮、左右控制与观察台声音监听开启，记录已封存停止，未重播扬声器。

证据仅留本机：`artifacts/collector-0.4.0-auto-pitch-net/`、`logs/auto-connect/{final-app-summary,final-live-summary,final-reboot-summary,network-checks}.json`、`logs/collector-v040-final13-*`。被替换的 10-socket 候选为 `artifacts/collector-0.4.0-auto-pitch/`，SHA256 `91c38815cf1d95239ede05f61bab712f90b92f1afbb6349bd992a7e61e0faa89`，不得与最终并发结果混用。凭据、完整设备身份、原始日志和现场媒体不提交或上传。

## 2026-10-31：0.3.9 中心键开关屏已部署并实机确认

用户将摇杆中心键的新用途确定为关闭/开启显示屏。已部署 `collector-0.3.9`：3,494,672 字节，SHA256 `86865c8265d3d2d6ce8a6310b750e691768c364eb9c28b3b1ac9cc3cbd83a621`；核对机器人身份、分区/otadata 后仅写 `ota_1 / 0x510000`，独立校验通过。Android `0.3.9/code9` 已安装，61,917 字节，SHA256 `4e3c2a5ce548400a03168ff9b137cea39204852044c96b8dd33ee4d9477b8b43`，观察台显示“中心开关屏”。遥控器继续使用已有中心协议，本轮未刷遥控器。

中心接收在独立路径维护 RAM 中的显示请求，默认亮屏；首包、新来源/启动 nonce 或至少 3 秒的接收间隔只建立基线，重复/倒退序号不切换，前进差量按奇偶处理。与转头请求、舵机反馈和观察台连接无关，`center_input_role=display_toggle`、`nod_trigger=none`；点头保持弃用，连续俯仰目标仍 null。每次开关直接门控背光 DLDO1，不改变或持久写入原亮度；灭屏禁用该显示的 invalidation 并暂停 refresh，但保留 20 ms 输入处理及各采集任务。恢复此前页面与最新状态，关屏触摸不唤醒/导航，跨开关触摸需先释放，旧表情不补播。显示遥测是软件应用状态，不是亮度实测。

主机 17 项 Python 测试通过；真实 LVGL 测试确认关屏 5 秒零 flush、其他 20 ms timer 继续、四页恢复、临时字符串拥有副本、快速开关、触摸释放与旧表情丢弃，原 120 帧/500 ms/12,872 像素局部刷新检查保留。Android 主机与原生渲染/记录测试通过。启动窗口 92/92 控制请求保持，41/92 两轴反馈有效；已有中心序号只建立基线，默认亮屏、applied event 为 0。

用户完成实际开关并回复 **“开关正常，页面正常”**。录制只观察到一轮 `on → off → on`：中心原始序号 20 基线、21 关、22 开，对应应用 event 1/2；软件关屏区间 1.978926 秒，末态亮屏。虽然测试提示邀请过两轮及长按，现有现场数据不证明已执行全部提示，长按与快速多轮仅记为主机验证，不再重复要求用户测试。

录制正常封存 37.193 秒，`verify-session.py` 通过：260 张 320×240 JPEG 全像素解码，实收 7.120 fps；1,835 音频块无序号缺口/重复/倒序、WAV 完整，1,826 IMU 样本约 49.998 Hz 无无效/历史缺失，155 遥控包无历史缺失。监听从开始开启，队列丢弃及相机/音频错误、跳块增量全 0。保守关屏采样窗口 1.782 秒仍收到 12 张图、89 音频块、89 IMU 与 9 遥控包，音频/IMU 序号连续，11/11 控制请求/启用保持。最新帧轮询会略过设备中间帧，不能声称 JPEG 序号无缺口。音频有 4 个时间戳偏差超过 5 ms，不把完整 PCM 当作听感验收。

全段 240/240 控制请求/启用保持，点头 disabled，俯仰目标 null；两轴反馈仅 73/240 同时有效，原回传根因仍未解决。新版安装连接旧 0.3.8、同时编译期间，模拟器曾有一次 App 无响应：精确栈为主线程等待 HWUI 同步，系统/图形调度压力高；没有直接证据归因于新标签，也不能仅凭并发编译断言原因。保留原始 ANR 后重启同版 App，后续本次记录正常完成；不能称 ANR 根因已修复。

当前屏幕亮、观察台已连接且监听开启，记录已停止，转头请求保持；扬声器未重新播放。证据 `artifacts/collector-0.3.9-display-toggle/`、`logs/collector-v039-display-toggle-*`、`logs/display-toggle/{first-app-summary,first-host-summary}.json`、`logs/display-toggle/anr-review.txt`。所有现场媒体和原始日志仅留本地。

## 2026-10-31：0.3.8 短语音播放已部署，现场听感待回复

用户要求让 chan 播放一段动漫日语。已部署 `collector-0.3.8`，保留图形摘要、运行日志、上下实心表情和中心仅记录；未再次刷写遥控器。固定 IDF 5.5.4 构建，固件 3,492,336 字节，SHA256 `268e57790c2ea08490482114e294ec49e1b5e8bbeefa11e44ea563e18694f452`；核对开发设备身份、分区与 otadata 后仅写 `ota_1 / 0x510000`，独立校验通过。Android 保持 0.3.7/code8。

新增鉴权 `POST /v1/playback`：24 kHz、单声道、16-bit little-endian 原始 PCM，最多 8 秒，单上传/播放任务，完整接收并成功回复 202 后才启用扬声器。沿用现有 I2S 输入与时钟，先在硬件复位期间清空 TX DMA，运行时核对现有 TX 格式；音量 20 仅存 RAM，不写 NVS。结束或错误均关闭输出并检查硬件复位，未知复位状态为 null。上传截止与单次等待的实际边界、状态语义见协议说明；`completed` 不能代替现场听感。

16 项 Python/主机测试通过，覆盖真实上传/输出 runner、长度、分块、写入/清理失败及并发 gate。实机未鉴权请求返回 401、奇数字节与空 body 返回 400，未改变 job 或开启输出。首次正式试听使用《莉可丽丝》千束官方角色语音的前 4 秒，192,000 字节，原片及处理样本仅留本地。来源为[官方角色语音活动公告](https://news.aniplex.co.jp/detail.html?id=60803)。job 1 完整提交、error/cleanup_error 均 0；结束后 `hardware_reset_held=true`、`output_enabled=false`，同一启动保持，43 个额外主机遥测请求无错误。用户是否听清、音量与音质尚待回复，不能写已通过听感验收。

观察台试听时关闭外放监听，录制正常封存 21.357 秒：160 张 JPEG 全像素解码，实收 7.577 fps；1,051 音频块无序号缺口/重复/倒序，WAV 完整；1,050 IMU 样本约 50.013 Hz，无无效或历史缺失；88 遥控包，队列丢弃与相机/音频错误、音频跳块增量均 0。输出启用的采样窗口约 4.059 秒内有 33 帧、203 音频块和 203 IMU 样本，音频与 IMU 均连续。音频 8 个时间戳偏差超过 5 ms，不把序号连续写成无杂音证明。`verify-session.py` 通过。

启动观察两轴有效 98/104；本次录制仅 10/147 两轴同时有效，播放前 idle 段已经有大量无效反馈，不能归因于播放或称旧问题已修复。147/147 控制请求/启用保持，俯仰连续目标全 null、中心仅记录、点头 disabled。旧舵机回传根因仍未解决。

当前录制已停止，观察台仍连接；外放监听已恢复开启并核对截图，左右控制请求保持开启，机器人扬声器硬件复位。证据：`artifacts/collector-0.3.8-playback/`、`logs/collector-v038-playback-*`、`logs/audio-playback/{audition20-result,audition20-app-summary,http-rejections}.json`。现场媒体、完整设备身份和凭据仅留本地。

## 2026-10-31：用户取消中心点头，中心按压仅保留记录

用户在加速版实测后反馈“偏慢，且幅度小”，决定放弃点头效果，中心键用途以后再定。此决定覆盖下方所有点头待验收及加速方案。已部署移除动作绑定的版本：保留遥控器 16 字节中心事件和心跳，机器人不响应中心按压执行动作；点头调度及专属下沿恢复移除，观察台改为“中心仅记录”。首页图形摘要、实心表情、日志和左右遥控保留；原单次启动姿态保持。遥控器本轮不再刷写。

最终 `artifacts/collector-0.3.7-center-idle/`：机器人 3,483,712 字节，SHA256 `1038a45f63b21d9b840f1469fcea4bb730dca1e970cb80f7e2fd082ae40c17f2`；核对身份及活动分区后只写 `ota_1 / 0x510000` 并独立校验。Android 0.3.7/code8 更新包 61,917 字节，SHA256 `96916a5d9bac8006fda64ab4193f9a985c3a045778c4ec1593b9cc18e44a6753`，已安装，原生记录/渲染测试通过。固件 15 项 Python、遥控协议 20 万随机包、中心消抖/模式保持、表情滞回和 stop/rearm/启动隔离测试通过。启动窗口 107/107 两轴反馈有效且控制保持，`motor.nod.state=disabled`、计数0、参数/目标null；严格启动姿态因实测约4°低于下限而跳过。未要求用户再次按中心验收，不能把此静置窗口写为新的物理按键测试。旧回传损坏根因仍未解决。

加速版录制已正常封存：76.232 秒，482 张 JPEG 完整像素解码、实收 6.368 fps；3,782 音频块无序号缺口，3,783 IMU 样本约 50.010 Hz，队列/采集错误增量均 0。6 次中心事件中 4 次完成轨迹，2 次在动作中未排队；事件到观察完成为 3.459–3.900 秒，不能当机械运动时长。真实 3.7/4.6°起点恢复有效，但用户不接受该效果，所以这份数据仅保留为弃用试验。音频存在时间戳抖动，不把序号连续解释为听感验收。证据 `logs/remote-center-v1/fast-app-summary.json`。

## 2026-10-31：中心按压已接通，点头加速版已部署

本节优先于下方“遥控器尚未识别/未刷写”的历史描述。遥控器已核对为 ESP32-PICO-D4、4 MB Flash，整片备份并独立校验；按实际分区表仅写 `factory / 0x10000`，首次启动前对整片预期镜像独立校验通过，应用区外字节保持原样。未刷 bootloader、分区表、NVS 或 PHY 区；启动后的原厂 PHY 校准可能正常写 NVS，不能泛称运行时 NVS 从未变化。NVS 初始化出错时停止启动，不再擦除 NVS。原机备份及恢复辅助脚本仅保存在本机，未执行恢复演练。

遥控器应用为 1,175,008 字节，SHA256 `efbb6b582a3ebfa1e7fe25ded9f1dafc6c7cf911857393187811ceea2dbf0c8d`，固定 IDF 5.5.4。实际收到 16 字节中心协议，空闲序号 0 不点头；首次按住中心产生一个新序号，用户确认点了两下，但认为偏慢。首版 250 / 700 ms 的四段目标均被观察到，内部轨迹约 6 秒后超时，不能写为完成。随后有效实测降到 3.7–4°，低于原 5°起点检查，导致后续请求被拒绝。

当前机器人 `artifacts/collector-0.3.7-nod-fast/` 为 3,485,664 字节，SHA256 `f0da01377a01fa1943e2efde319c12fab0021717e6353220b1c3675c577f1e63`。身份/活动分区核对后只写 `ota_1 / 0x510000` 并独立校验；Android 仍为 0.3.7/code8。点头改为速度 450、400 ms 换向，目标仍 17→5→17→5°、截止 6 秒。仅点头允许真实有效起点在下限以下最多 2°，从 5°规划向 17°的命令；测量原样保留，启动姿态仍严格。原厂更新、弹簧、HAL、堵转和校准没有改动。

18 项 Python 测试通过，其中新增测试直接链接真实 Servo/Animate 链，覆盖更新抖动、低频、超时不假报完成，以及下沿恢复的命令范围和原测量保留；另有动作状态机和启动隔离测试通过。理想 20 ms 更新的轨迹约 2.24 秒，不能作为机械动作时长。新启动 114 组中 113 组两轴有效，控制始终保持；实际起点 3.7°，严格启动姿态跳过，中心动作仍 idle，未因遥控器此前序号 9 被当作新按压。

加速版正在实机录制验收：已请用户按中心一次、等 5 秒后再按一次，检查节奏及重复触发。暂不能写为用户验收通过。原舵机回传损坏根因仍未解决。当前监听及转头开启。

本机证据：`logs/remote-center-v1/{deployment,initial-app-summary,initial-host-summary}.json`、`logs/collector-v037-nod-fast-*`、`artifacts/remote-center-v1-20261031/` 和新机器人 artifact。原始媒体、设备身份及备份不发布。

## 2026-10-31：0.3.7 实心表情、图形摘要和运行日志已部署

当前部署为 **实心无圈表情与图形摘要版**。用户已确认首候选摘要页、日志切换和上下表情正常；最终图形版再次完成上下表情、摘要/log 切换和返回首页，用户确认“正常，无卡顿”。本节将首候选与最终版证据分开记录。

- 首候选固件为 `collector-0.3.7`，3,481,824 字节，SHA256 `03c1a4526d8c4b9ee50a634c43d86800998b9c4cf20444ca6d8c5e1bcc316683`，封存在 `artifacts/collector-0.3.7/`。核对开发设备及活动分区后仅写 `ota_1 / 0x510000`，独立校验通过；原 NVS、校准和其他分区保留。Android 已安装 `0.3.7 / code8`，该次 APK SHA256 `1d3cc2f72ef33b8ad5fc10d8b678f06d82583891acb6af8933546b1c1c5f3bc4`。
- 首页轻触先进入摘要，包含 Wi-Fi、相机运行、最近遥控输入、电量/充电、加速度/角速度和有效舵机角度；左滑进入真实运行日志，右滑返回摘要，短按返回首页。摘要长按进入配对详情，凭据不进入摘要或日志。原厂 8 字节发送端没有心跳，不能把“最近没有输入”直接写成遥控器断线。
- 日志页读取 ESP 日志的固定环形缓存并跟随最新行滚动，保留原串口输出；这是本地、非持久缓存，不包含 ROM/早期启动日志或所有原始 `printf`。捕获路径限长、清理 ANSI 与敏感字段，竞争时允许丢弃，避免日志拖住采集。
- 上推/下推分别触发首页爱心/拇指，持续 500 ms；回中滞回防止保持方向重复触发，信息页收到的表情不排队到之后的首页。上下映射输入继续完整记录，连续控制仍仅执行 yaw，`pitch_target_deg=null`。
- 点头只响应明确的中心扩展包：原 8 字节后附 `sender_boot_nonce` 和 `center_press_seq`，共 16 字节；首次、新启动或来源改变只建立基线，重复/倒退序号忽略，停用期间仍消费序号。旧 8 字节的 B 键只记录，不能触发点头。原厂源码及现场观察均显示中心按压未进入旧包；Mini JoyC 中心键为独立 I²C `0x54 / 0x30`，不是 StickC 的 B 键。
- 双点头脚本目标为 17→5→17→5°，对应 5–85° 安全行程的 15%→0%；速度 250，换向间隔 700 ms，保留原厂弹簧、限位、堵转和卸力。等待新鲜有效起点最多 1 秒；动作中重复请求不排队，动态限位不足或超过 6 秒会取消。原厂弹簧离线模拟约 3.76 秒结束，仅为计算轨迹；实际点头幅度、节奏和中心触发尚未验收。动作信息独立存放于 `motor.nod`，不能以目标代替实测。
- 已修正短时停止后重新启用的竞态：即使两次请求都落在 motor task 的两轮采样之间，停止标记也会保留，由任务先取消两轴、启动姿态和现有点头，再处理重新启用之后的新事件；用户的启用请求保持不变。组合主机测试覆盖此时序。
- 遥控器候选通过固定 ESP-IDF 5.5.4 的隔离 ESP32 构建，入口为 `bash scripts/build-remote.sh`。依赖版本/哈希取原锁文件，原 `dependencies.lock`、`sdkconfig`、manifest 未改；旧遥控工程中记载的 IDF 5.4.2 属于上游基线。候选读取中心键，首次包及约 200 ms 心跳提供会话基线，模式切换时按住不触发；心跳/按钮包保留原角度死区，不把噪声变成连续目标。**遥控器 USB 尚未识别，未备份、未刷写，也没有实物点头验收。** 不可把机器人偏移用于遥控器。
- 首候选 71.133 秒录制保存 395 帧，实收 5.619 fps；3,507 个音频块无序号缺口，3,507 个 IMU 样本约 50.004 Hz，写盘丢弃 0。监听在录制后 17.33 秒才打开，不能称整段监听开启。该段没有新遥控包，缓存中的爱心发生在录制前，点头始终 idle；这不是遥控动作并发测试。启动窗口两轴仅 2/95 同时有效，后续录制 470/470 有效，**旧回传损坏根因仍未解决**。

最终图形版为 `artifacts/collector-0.3.7-solid/`，3,485,184 字节，SHA256 `226b0344326dd3da5ee6acd7024493bdfb03c3cad765657ac1dfadfd4224e3f0`。再次核对身份及活动分区，仅写 `ota_1 / 0x510000` 并独立校验通过；APK 与上列 0.3.7/code8 相同。主机完整 120 帧、实心内部逐像素、无外圆、500 ms、图形摘要正常/无效/极值及相同快照零刷新检查通过；文字环仍为 12,872 像素 / 25 区域局部刷新。摘要相机帧率只统计成功发布的 JPEG，不把失败尝试计入。最终启动窗口 3/76 两轴同时有效，控制请求/启用保持；回传根因仍未解决。

最终并发录制 64.129 秒，543 张 JPEG 全部像素解码，实收 8.536 fps；3,186 音频块无缺口，3,186 IMU 样本约 50.014 Hz，写盘丢弃和采集错误增量均 0。监听从录制开始即开启，记录到 79 个遥控包、17 个表情阈值事件及屏幕触摸。609/609 控制请求/启用保持，俯仰目标均 null；两轴有效 452/609，进入 following 后 442/449，不能声称旧回传根因已修复。点头全程 idle，原厂发送端不支持中心扩展。记录已封存并停止，转头请求和观察台监听保持开启。

最终证据：`artifacts/collector-0.3.7-solid/{manifest,flash-evidence}.json`、`logs/collector-v037-solid-{summary,app-summary,app-verification}.json`、`logs/collector-v037-solid-build-final.log`、`logs/display-v037-graphic/`。首候选证据为 `artifacts/collector-0.3.7/`、`logs/collector-v037-{summary,app-summary}.json`；遥控器构建见 `logs/remote-center-v1/result.json`。原始串口、现场媒体和凭据仅留本地。详细统计与未验收范围见 [验证记录](collector-validation.md) 本轮段落；已有动作/刷机授权无需重复索取。

## 2026-10-31：0.3.6 纯黑旋转首页、遥控仅左右轴

- 最终固件产物为 `artifacts/collector-0.3.6-black/`，3,472,048 字节，SHA256 `dcef4393489f05a88d27648f40662810724d073553cbad487f473d2d65e41a94`。身份、分区及 otadata 核对后仅写 `ota_1 / 0x510000`，独立 verify 通过；NVS、校准和其他分区保留。此前带 `release` / `final` 字样的候选有性能问题，不能当作最终部署。
- Android 已安装 0.3.6/code7，APK SHA256 `ca0bf57a914c104e80e2142889131bc232e89b0e1b201c4723548882652f5e8d`。遥控蓝点改称“输入”，显示“仅左右转动 · 上下输入保留”，旧固件仍按双轴目标显示。
- 首页背景改为纯黑 `#000000`，图形 260×240，移除 TAP FOR STATUS；脸/帽子固定，文字圈逆时针 12 秒一圈。用户已确认“纯黑、旋转和切页都正常”，此前已确认“文字旋转正常，只有左右转头”。两种遥控模式均只执行 yaw，pitch 输入继续接收与记录，`pitch_target_deg=null`；保留独立的单次启动俯仰 13°。控制、原厂 UART 驱动及动作平滑没有因显示性能调试而再次修改。
- 动画预生成不透明 RGB565，仅更新 7,478 个像素、刷新 25 个矩形共 12,872 像素，缓冲 124,800 B PSRAM；LVGL 在 core 0，JPEG 在 core 1。排除了实时 alpha 旋转、整图刷新、LCD 队列等待和降低 LVGL 优先级等不足以解决问题的候选，全部诊断日志和自动切换代码已移除。
- 找到先前音频发送任务的确定性调度错误：100 Hz 下 `pdMS_TO_TICKS(5)` 为 0，空缓冲等待实际空转。临时任务计数显示 audio_stream 占用单核约 76–78%，现在至少阻塞一个 tick，音频采样率、块大小和协议不变。
- 最终启动观察 112 组 requested/enabled 均保持开启，只有 11 组两轴位置同时有效。原有舵机回传损坏根因仍未解决，不把本轮显示、帧率或用户动作确认当作通信稳定性通过。
- 主机 LVGL 完整 120 帧、纯黑像素、旋转整圈一致性、文字区域部分刷新与触摸状态验证通过；启动姿态/仅 yaw 路径穷举 65,536 个 pitch 输入通过；遥控协议十万随机包、Python 15 项及 Android 原生绘图/录制测试通过。固定 IDF 5.5.4 和 sdkconfig 未变。

最终 82.463 秒并发录制实收 7.351 fps，4,103 音频块无缺口，IMU 50.000 Hz，写盘丢弃 0；完整数据见 [验证记录](collector-validation.md) 最新段。现场媒体、原始串口和凭据仅留本地。测试录制已封存，保留转头请求与声音监听开启；无需重新索取相同授权。

## 2026-10-31：0.3.5 绿色笑面男与 10% 启动俯仰

- 固件 `collector-0.3.5` 已刷入，2,480,400 字节，SHA256 `e2b06660f7297e3da17f10512a695379c035bda78bf0a4512c08fc03dbcc9160`。再次核对开发设备身份、分区和 otadata，只写 `ota_1 / 0x510000`，独立 verify 通过。Android 仍为 `0.3.4 / code6`，本轮没有 APK 变更。
- 机身首页为绿色笑面男，轻触切换 Mac 绿色终端风格的信息页，再点返回；信息保留版本、相机帧数、MIC、遥控 CH/ID/状态、HTTP 地址、音频端口、配对码与 USB Wi-Fi 提示。每次启动默认首页；长按不反复翻页。静态图形 216×216，新增 JetBrains Mono ASCII 字库；来源许可见根目录 NOTICE。
- 启动俯仰目标是当前 5–85° 安全行程的 10%，即 13°；yaw 不主动归位。使用原厂弹簧 `moveWithSpeed` / `update`、速度 250，保留原厂限位、俯仰堵转与到位释放。新入口只允许用有效、限位内实测角度初始化轨迹，不用回退值冒充测量，也不额外读一次位置作为起点。
- 一次启动最多执行一次；等待有效俯仰回传最多 5 秒，超时明确 `feedback_unavailable` 并跳过，不在很久以后突然归位。遥控输入优先且立即接管；手动停用取消启动动作，再启用不会重放。额外反馈仍不决定遥控是否启用。telemetry 新增 `startup_pitch_target_deg` / `startup_pose_state`，`trajectory_complete` 仅表示轨迹计算结束，实测仍看 pitch.valid / angle_deg。
- 本次实机日志从有效 46.2° 开始，目标 13°；35 秒启动观察所得 117 组两轴回传全部有效，俯仰均 12.8°，requested/enabled 均 true，无遥控包、无 POST enable。首次 0.3.5 启动表现良好不能证明之前的串口损坏根因已修复；完整摇杆/姿态动作回归未重复。
- 同一 LVGL 代码离线渲染 320×240 两页并逐图检查，无截断；启动首页、短按抬手、抖动、长按不翻页与点回首页通过。主机启动姿态用例、十万随机遥控协议用例及 Python 15 项通过。用户已实机确认“显示和切换都正常”，首页、打开信息页和返回均通过。
- Android 自动重连 0.3.5，ADB 已恢复声音监听并截图确认开启；随后 80 组反馈中 78 组两轴有效，控制请求保持开启，俯仰有效值为 [12.8]°。
- 本机证据：`artifacts/collector-0.3.5/`、`logs/collector-v035-{build-final,boot}.log`、`logs/collector-v035-summary.json`、`logs/display-v035/`。不上传现场截图、原始日志或凭据。


## 2026-10-31：建立公开仓库 chan-X20A

项目根目录现在是统一 Git 仓库，`main` 分支；`origin` 指向 `ghostxmerlin/chan-X20A`，`upstream` 指向 `m5stack/StackChan`。官方提交历史保留，源文件位于 `upstream/StackChan/`，它不是 submodule；下面对“独立 dirty checkout”和旧开发分支的描述属于历史。

本次公开提交包含当前 0.3.4 固件源码、Android 0.3.4/code6、构建配置、测试与说明，没有更新设备固件或重新安装 App。凭据、签名密钥、工具链、构建产物、原机备份、原始串口日志和现场媒体不入库。完整本机交接及旧 Git 元数据保存在 `.private/publication-before-chan-X20A/`。公开说明中的 IP/MAC 已替换为示例或占位，不能据此识别刷机设备。

当前状态及限制见 [README](../README.md)、[状态快照](current-state.json)和下方 0.3.4 记录。舵机位置回传问题仍未解决，本次提交不将其标记为验收通过。


## 2026-10-31 当前：0.3.4 修正状态耦合，回传通信仍未解决

本轮复现“开机后遥控不能自动就绪”和“启用后状态自行关闭”。原厂运行正常，应优先核对采集固件与原厂的集成差异；不得在证据不足时把问题归因于硬件。**整套舵机功能尚未验收通过，下面 0.3.2 的通过记录不是当前结论。**

### 当前部署与已确认修正

- 固件 `collector-0.3.4`，2,426,368 字节，SHA256 `099f69c78c148ee93916887aa0f324bb24d9b3b259f58607781bba97d2e39770`；Android 0.3.4/code6 已安装。重新核对 MAC / 分区 / otadata，只写 ota_1 / 0x510000，独立 verify 通过，boot `f974c31ddc2f89ac`。USB 底座接口，CH 6 / ID 1（兼容 0）。
- 0.3.2 在任一位置回传无效时把 `control_requested=false`，直接造成开关自行关闭；无线接收实际正常。新增的采集反馈被错误地绑定为动作前提，官方 `AppEspnowControl` 没有该依赖。0.3.4 已去掉反馈失败锁停与启用时的反馈条件，保留原厂限位、堵转处理、停止和到位自动释放。
- 恢复 `SCS.cpp`、`SCSerial.cpp`、`SCSerial.h` 为固定上游原文；UART/舵机在 app_main 同步初始化，之后启动音频/传感器/视频。去掉自行增加的每包 torque-on，运动仍走原厂 `moveWithSpeed` / `update`。首包同步实体位置，之后保留弹簧连续速度；不重放手动停用期间收到的旧输入。
- 额外遥测只读两轴位置，观察事务临时超时 2 ms 后恢复原值；不再额外读负载/电流，valid 为 false。原厂堵转内部完整反馈事务没有改动。info/telemetry `feedback_required_for_control=false`，motor 增加 `feedback_valid`；Android 将启用状态与“位置回传暂缺”分开显示。所有诊断阶段/自动重试/暂停恢复/原始 trace 均已撤出当前源码。
- 编译、APK 构建签名、安装与 diff check 通过。主机串口协议测试验证官方请求/写入字节以及损坏 CRC、错误 ID、截断拒绝；Android 原生控制测试验证独立模式停止/启用、无续约、重连、boot 变化和旧固件续约兼容。
- 开机没有 POST enable：117/117 遥测 requested/enabled 均 true，等待新输入；仅 19/117 两轴位置有效。UI 手动停止再启用通过，之后 30 秒 122/122 保持开启，119 次两轴位置无效。只证明状态问题修正，不能称动作和回传已通过。
- 已发出一次功能确认：“新版已刷入…请在 CH 6、ID 1 下轻推一次摇杆：机头是否跟随？”截至该记录尚无回复，本版没有真实运动验收。转头请求保持开启、applied_seq=0。麦克风监听已恢复，Android AudioTrack 24000 Hz / mono started，机器人扬声器仍保持复位。可见模拟器与 relay 保留；ADB 输入使用 `input touchscreen -d 0`。

### 未接受的方案与定位证据

- 0.3.3 的自动稳定资格/暂停恢复及 3 次重试已刷入过，但 35 秒仍有 24 次最终失败、12 次暂停、266 次重试。在 `artifacts/collector-0.3.3/validation-status.json` 标为 `not_accepted_as_fix`，已撤掉，不能包装为修好。
- `collector-0.3.3-factory-transport`：官方串口传输代码、无重试、无动作，119 快照仅 13 次两轴有效。该测试仍运行采集业务，不能据此归咎硬件。
- `collector-0.3.3-capture-stages`：同次启动依次加入任务，两轴有效数/样本：板级与无线 46/99，加入音频 11/129，传感器 12/129，视频 14/213。
- `collector-0.3.3-factory-init-order`：先初始化 UART，再创建音频 codec；同一分阶段测试为 54/91、4/134、2/132、7/212。恢复顺序没有修复通信；加入音频使读回更差是观察结果，机制尚不确定。
- `collector-0.3.3-raw-trace`：抓到实际 RX 缺字节/校验损坏，例如 ID1 的 `ffff01040001c7b2`（校验应为 32），以及缺少起始 FF 的回复。UART 当前速率确认为 1,000,000。不能忽略 CRC 把这些值当有效数据。原始串口日志含私有信息，只按 `CollectorServoTrace` 标签过滤查看，勿整段输出或上传。
- 官方驱动至 2026-06 的改动早于原机 2026-10-19 编译；sdkconfig 与最初固定官方构建逐项相同。官方底座原理图核对 TX6/RX7，收发方向由硬件自动切换，无额外软件方向脚。PY32 地址为 **0x6F**；0x41 是 INA226，旧交接曾误写，不能沿用。
- 本轮没有定位到回包损坏的最终原因，不宣称供电/接触故障；两个修改版之间对照也不能证明硬件坏。后续应围绕原厂控制与采集并发的具体差异取证，避免继续重复已经失败的重试、初始化延后、换线与断电路线。

证据：`logs/collector-v034-{build,apk-build,transport-test}.log`、`collector-0.3.4-{preflash,flash,verify}.log`、`collector-v034-summary.json`、`collector-v034-enable-summary.json`、UI 截图；之前分阶段报告位于 `logs/collector-v033-*-summary.json`。诊断镜像各自封存，当前候选产物和源码见 `artifacts/collector-0.3.4/`。

待完成：本版真实摇杆/姿态输入与运动验收、回传损坏根因与修复、重启后重复验证。用户已授权刷机/动作/声音，不要重复索取权限，也不要把未完成写为成功。

## 2026-10-31 最新：0.3.2 转头与声音已启用，用户验收通过

本段优先于所有旧静止、静音和租约要求。用户明确要求“开启转头和声音，转头要像出厂固件一样与遥控器配合”，并在完整关机再开机后的实机测试确认“**两种转头正常，也能听到声音**”。结束时保持转头和观察台监听开启，不要再次关闭。工程仍在 `~/chan-X20A`，不是 BOX-3 的 `~/s3`；不操作 StickS3。

- 固件 **collector-0.3.2**，2,426,224 B，SHA256 `9a5ec11be24df61f4c87d60d8e7ada5096a6ed54db466ba9792336f09fb8c4d8`；Android **0.3.2/code4**。再次核对 MAC、分区表与活动 otadata，仅写 ota_1 / 0x510000 并独立校验。当前 boot `1b4e6da4d922de9e`，USB 接底座，遥控 CH 6 / ID 1（广播 0 兼容）。产物 `artifacts/collector-0.3.2/`，旧产物不变。
- 按固定官方 `app_espnow_ctrl` 和 `joystick_handle.c` 恢复原厂速度 600（协议 0…1000），连续输入保留弹簧速度，不再每包重置到物理角度。取消 Android 三秒租约和命令三秒处理窗口；开机被动初始化、只读原厂零点、不归位，等新遥控输入。停止后再启用不重放旧包。保留机械限位、俯仰堵转保护、无效位置回传停机、原厂到位释放力矩。没有恢复官方云业务或 GPIO2 附件动作。
- 客户端通过 `remote.control_mode=standalone` 判断独立控制；仅明确点击发送 POST，连接/重连/boot 更新/退出不自动停用机器人。开关依据 `motor.requested` 显示；旧固件仍续约。实际 Home 退后台 7.95 秒、32 组遥测均 enabled 且两轴有效；回到前台重新连接后仍开启。UI 停止确认 disarmed，再启用为 waiting_for_input，applied_seq 保持 148，未重放旧目标。
- 声音为 **机器人麦克风 → Android AudioTrack → Mac 输出**。模拟器原先 `-no-audio`，本轮关闭并保留用户数据重开 `-audio coreaudio`；用户已确认听到。机器人自身扬声器仍硬件复位、没有播放业务。保留可见 emulator-5556 和 loopback relay，进程元数据在 `.private/collector-visible-processes.json`。App 断线/切后台仍会停止监听，重连后已手动恢复。
- **回传异常没有被宣称根治**：0.3.2 两次暖重启两轴仅 24/136、16/101 有效；延后初始化 4 秒的诊断镜像只有 11/117，已撤回该修改。原封不动恢复 0.3.1 对照仍仅 18/114 有效，因此不能单独归咎新控制逻辑。用户确认仍接底座。按官方方式拔 USB、长按机头左侧电源键 6 秒关机、等待后开机并接回底座，回传恢复为 100/100、30/30、动作 166/166、应用记录 482/482。开机阶段曾触发故障锁定，回传稳定后通过 UI 重新启用成功；具体供电/接触原因仍未确定，不继续无根据修改 UART。
- 实录 `20261031-120158-932`：50.061 s、291 帧全解码、实收 5.832 fps、2485 IMU（50.028 Hz）、148 遥控输入、2486 音频块。482 条遥测两轴全有效且控制开启；99 个实际应用目标，速度 600，水平 -105…120.9°、俯仰 10.3…79°。音频序号缺口、IMU 缓存缺失和落盘丢弃为 0。用户确认摇杆及姿态两种控制，极限行程/纯电池/肩上动作未专项验收。原始媒体只在 `logs/collector-v032-app/`，汇总 `logs/collector-v032-app-summary.json`。
- 固件完整编译、APK 构建安装、十万随机遥控包及真实越界目标测试、Android 原生 HTTP 控制回归均通过。新 `CollectorControlTest` 使用 loopback 假接收器，验证连接不发停用、显式停止/启用、无续约、重启/重连与旧版续约兼容。运行 `bash android/Collector/test-android-runtime emulator-5556 control`；测试 APK 无 Activity，不操作真实机器人。

未实现的边界保持不变：后台/锁屏采集、SD 断网补录、真实手机专项、肩上安装校准与长时续航。软件改动未提交 Git。四秒延后诊断产物 `collector-0.3.2-startup-test` 不是最终版本；构建源码撤回后 SHA 与最终 0.3.2 完全一致。

更新时间：2026-10-31 上午。本轮工程链路验证与 Android 采集首版 M0–M4 已完成。默认中文沟通。

## 2026-10-31 最新：0.3.1 已部署，官方遥控小幅转头通过

本段优先于后面的排障过程和旧部署状态。实际工程目录 `~/chan-X20A`；`upstream/StackChan` 是 dirty checkout，分支 `feat/collector-v0.1`，不要 reset。继续固定 IDF 5.5.4，不操作另一台 StickS3。

- 用户已授权 ADB 操作/截图以及桌面低速小幅动作，并确认出厂转头正常、本轮改接底座后“能正常小幅转动”。这些授权无需重复询问；声音保持关闭。
- **当前 USB 接在底座**。机头接法下位置回复损坏；多个 UART 参数/时钟/核心、VM 重启和接收等待实验未根治。未改变固件或 boot、只换底座接口后 131/131 有效，随后两次重启 122/122、123/123，动作观察 270/270。尚未判定供电/接触的具体原因，不要再把单次 atomic UART 成功当作根因证据。
- 最终固件 0.3.1：`artifacts/collector-0.3.1/stack-chan.bin`，2,426,128 B，SHA256 `48fe34baa1fd1d7160e784a866ab58f0ef46f24b604748195343d05267cfd53c`；只写 ota_1 / 0x510000 并独立校验，boot `f4b0e22a920bb378`。短暂初始化后关闭控制，147 组两轴位置全有效、applied_seq=0。正式版已移除临时 ±5°/速度 80 与 VM 断电实验；水平 ±128°、俯仰 5–85°、速度上限 400。原厂 yaw=461、pitch=642 校准只读。
- 软件修正：官方角度映射会外推，解码保存 int16 原值、执行另限幅；普通 SCS 请求完整帧一次发送；UART 阻塞等待替代零 tick 轮询；位置与可选 load/current 分别校验；被动初始化不写目标、不归位。诊断含 transport_error/servo_status，无效角度不冒充实测。源代码中不再有 UART probe 或 ServoTrace。
- 观察台已安装 0.3.0/code3，视频、三轴加速度/角速度曲线、紧凑触摸、遥控输入/实际反馈和 CSV 已实测。动作会话 `20261031-113755-982` 68.355 s，344 帧（实收 5.043 fps）、3410 IMU（50.006 Hz）、255 遥控输入、3402 音频块，所有 IMU/位置有效，音频序号缺口、历史缺失和队列丢弃为 0。细节在 `logs/collector-v031-bench-app-summary.json`。
- UI 停止控制通过；停止续约约 3.12 s 观测到关闭。此为控制状态，不是独立机械力矩测量。正式全行程、纯电池、肩上运动、真实手机直连、后台/锁屏和 SD 补录未验收；锁屏/切后台仍会停止采集。
- 保留 emulator-5556 和现有本机 relay；地址/令牌在 `.private`，不要输出。真实媒体与串口日志仅留本地。打开串口可产生 USB_UART_CHIP_RESET；需要串口日志时合并在预期重启里。
- 本轮各诊断镜像保留在独立 `artifacts/collector-0.3.*-*`，最终版本以 `collector-0.3.1/manifest.json` 为准；不要覆盖旧 0.3.0 manifest。所有源码和文档更新均未提交 Git。

验证详情与最终录制补充见 [验证记录](collector-validation.md)。后续按用户新需求推进；不需要重做 USB 问答、备份、工具链安装或已通过的有限动作测试。

最终版录制已封存：`20261031-114124-962`，77.425 s、308 帧（4.002 fps）、3849 个有效 IMU（50.006 Hz）、31 条遥控输入、3860 音频块，零音频序号缺口、IMU 历史丢失及写盘丢弃。470 条遥测两轴位置均有效；控制始终关闭且 applied_seq=0。帧率并非稳定 5 fps。停止录制后保留观察台连接、图表界面，控制/声音均关闭。汇总 `logs/collector-v031-release-app-summary.json`；原始记录 `logs/collector-v031-release-app/`。

## 2026-10-31：遥控排障与小幅动作验收进行中

**重要更正：`collector-0.3.1-bench` 重新启动后，位置读回失败再次复现（yaw 11/30、pitch 7/30 有效）。因此单次启动的整包发送成功不能视为最终修复，尚未进行运动。正在准备 UART 核心/时钟/上拉/事务间隔的只读对照诊断；所有最终验收和正式部署结论仍待完成。**

本段优先于下方旧记录。用户已同意 ADB UI 操作、截图；已把遥控器设为 CH 6 / ID 1 并把机器人放稳，明确允许低速、小幅转头。用户确认出厂固件的遥控转头正常。继续静音，不操作 StickS3。

- 已在 emulator-5556 安装观察台 0.3.0/code3，实际界面已看到三轴曲线、视频、遥控目标与有效位置反馈。现有 relay 和可见模拟器继续保留。
- 第一轮点击“启用转头”触发位置反馈无效而停机，没有应用运动命令。底座 PY32 0x41/VM 初始化正常；原厂零点只读得到 yaw=461、pitch=642。
- 将位置与可选 load/current 读取分开后，捕获到 CRC/ID/长度错误（两轴仅约 1/4 至 1/3 读回成功）。进一步把 `SCS::writeBuf` 从头/参数/校验和三次 UART 写入改为一个完整帧单次写入，连续各 50 次读回成功；后续 30 秒 182 组两轴位置全部有效，负载仅一条可选读回无效。保持 CRC 校验和无效反馈停机，未绕过保护。
- 真实遥控曾发送 pitch_raw=950 / speed=600，被旧的角度解析范围误拒绝。解析器现保留完整 int16 目标，执行边界统一限制 yaw ±128°、pitch 5–85°、speed ≤400；随机十万包和真实越界回归通过。当前修正运行版收 91 包、拒绝 0，最近包频道已是 6。
- 普通 SCS 指令的主机测试已通过：整帧请求/写入、真实 461 位置回复、损坏校验和/错误 ID/截断拒绝。临时 ServoTrace 日志已从后续源码删除。
- 当前实机为 `artifacts/collector-0.3.0-servo-atomic/`，bin SHA256 `f8c644b33bbc96dfdfa02f840440a8fc0ee21398d4a596e3e4fd2218435b560d`，boot_id `c3beaf317c8b6f9c`；控制关闭。初步诊断/trace 二进制保留在单独目录。下一步正在构建 0.3.1-bench，限制每个控制会话在起始实测姿态 ±5°，速度 80；完成真实遥控动作后移除临时限幅并发正式 0.3.1。
- 已拉取并验证真实 0.3 App 会话 `20261031-110331-827`：2 段、760 帧、1193 遥测、6947 音频块、13 事件、队列丢弃 0。两段实收 5.184 / 5.726 fps，IMU 均约 50 Hz、历史缺口 0、段内音频序号缺包 0。中间 boot 变化是打开 USB 串口产生的 `USB_UART_CHIP_RESET`，并非 panic；应用正确分段并保留恢复间隙。原始记录在 logs，含媒体，不上传。
- 串口监视打开可能触发 USB 复位；需要监视时与刷机后的预期重启合并。只写活动 ota_1 / 0x510000，每轮先验 MAC 与 OTA 元数据再独立 verify_flash。`logs/flash-validation-build.py` 是本轮本地验收辅助脚本。

待完成：受限实机动作、关闭/租约验证；恢复正式常规限幅；最终构建/部署、产物源码摘要与相关文档更新。不要把当前读回成功描述为动作已经验收。

## 2026-10-31：0.3 修正版已部署，观察台 UI 待更新

用户“接回了”后核对 `/dev/cu.usbmodem1301` 的 StackChan MAC `<STACKCHAN_MAC>` / ESP32-S3 rev0.2，重新读取分区和有效 OTA 元数据，确认活动 `ota_1` / `0x510000`。先刷入候选版，实测发现旧观察台不传游标时每次重复序列化 100 条运动历史；随后修正为仅在显式传入对应游标时返回历史，重新构建、仅写应用区并独立 verify_flash 通过。没有重写 bootloader、分区表、otadata、assets 或 NVS，没有操作另一台 StickS3。

- **当前固件** `collector-0.3.0`，2,425,136 字节，SHA256 `d52fdd3c366bec80bed5a320dffced07bc46da3d72b311e5712c0a343fc000d2`；boot_id `bbc084d3d9029d08`。产物与源码快照 `artifacts/collector-0.3.0/`。初次候选目录 `collector-0.3.0-candidate/` 保留为历史，不修改其原 manifest。
- Wi-Fi/token 保留，IP `192.168.1.42`；遥控接收器 ready，**CH 6 / ID 1（接受广播 0）**。全程扬声器硬件复位、舵机 `not_initialized`、控制 false，没有发送启用动作请求。
- 旧客户端快照从约 13.8 KB 降至 **1,438 字节**，不含历史数组；新客户端 `imu_after=0` / `remote_after=0` 初次取历史、后续取增量。四种游标组合和四类非法游标实机通过；320×240 JPEG 实际像素解码通过。
- 仅原 Android 0.2 观察台持续收流时，25.01 秒设备视频产出约 **5.518 fps**（修正前同类观察约 4.226 fps）。这是设备产出率，不是新版 App 接收率；摄像头目标 10 fps 仍未达到。
- 另加主机 10 Hz 增量遥测观察约 60 秒：570 次响应，**3,101 个 IMU 样本全部 valid，平均 50.016 Hz，缓存丢失/序号缺口为 0**。首包包含约 2 秒历史。读取间隔中位 19.457 ms、p95 52.763 ms、最大 113.159 ms、最小 1.154 ms；存在调度抖动和追赶，不能宣称均匀的硬件 50 Hz 时刻或完整 FIFO。
- 上述额外遥测负载下视频产出约 **4.898 fps**，音频发送计数增加 3,000，设备跳过块和音频/图像采集错误为 0。未从旧 App 导出本轮录制，因此这不是接收端音频零缺包验收。未新增麦克风监听或播放。
- 真实遥控计数仍为 0；已请用户设置 CH 6 / ID 0 或 1 后推摇杆，待回复。官方遥控、机械角度反馈、限位与租约动作仍未验收。不要自动启用转头。
- **可见观察台仍为 0.2.0/code2**。0.3 APK 已构建并通过原生离屏/记录测试，但没有安装；CUA 无法识别模拟器窗口，用户尚未明确允许改用 ADB 做界面操作。已询问，等待回复；现有窗口和 relay 保持运行。
- 当前仍是前台采集，锁屏/退后台停止。真实手机直连、肩上固定姿态校准、后台服务、断网补录与续航均未验收。

证据：`logs/collector-v03-compat-build.log`、`collector-v03-compat-preflash-retry.log`、`collector-v03-compat-flash.log`、`collector-v03-compat-flash-verify.log`、`collector-v03-compat-boot.log`、`collector-v03-compat-live/report.json`。一次预读在 USB 重枚举时发生串口配置错误，尚未写入；自动以 115200 重试成功。当前部署元数据见 `artifacts/collector-0.3.0-deployment-20261031.json`。检查脚本与本地原始遥测在 `logs/collector-v03-compat-check.py` / `collector-v03-compat-live/`，不上传现场媒体或凭据。

以下“未部署”部分为用户接回 USB **之前**的候选构建历史，不是当前状态。

## 10 月 31 日后续：肩上采集 0.3 候选版（未部署）

用户从 `~/s3` 会话提出当前视频 FPS、官方遥控接入、运动曲线、触摸简化和肩上外出数据需求。实际开发目录已定位到本工程；未改 `~/s3` 的 BOX-3 固件。

- 当前实机仍为 **collector-0.2.0**，正常单观察台记录实测 5.66–5.87 fps；本轮额外并发读帧 15 秒、62 个不重复帧约 4.06 fps，保存 `logs/collector-pre-remote-fps.json`。并发额外拉流会影响性能，不可混作单观察台测量。目标 10 fps / 传感器配置 20 fps 都不是实收保证。
- 0.3 源码：新增 `collector/remote.{h,cpp}`、`remote_protocol.h`，与官方 esp-now 组件 DATA 格式相同；不重启 STA、不切到固定频道。接收 ID 1，兼容广播 0，UI 显示 Wi-Fi 频道；只有用户启用转头后才初始化 VM 和舵机，启动保持无动作。
- 官方包只提供映射后的角度/速度/辅助按钮，既无原始摇杆、模式或遥控器 IMU，也无心跳/发送序号。遥控“输入计数”不是空口丢包统计。辅助 GPIO2 输出未启用。
- IMU 目标读取 50 Hz、芯片 ODR 100 Hz，加速度改 ±8g（其余调用默认仍 ±2g），环形缓存 100 条，遥控缓存 64 条，通过游标取增量。缺口明确计数，时间为读取/接收完成的设备单调时间。固件批量链路尚未实机评测。
- Android 原生图表 `MotionHistory` / `MotionChart` / `RemoteView`，8 秒三轴、明确原始加速度含重力，目标与实际反馈分开；触摸在画面下方小区域，步行/静止/转身/颠簸/遥控快捷打标。新增 `motion.csv`、`remote.csv`、`servos.csv`，无效反馈留空。
- 0.3 固件与 APK 已构建签名通过；主机音频/运动窗口测试通过，Python 协议 15/15；遥控 8 字节边界、负数、ID、十万随机包检查通过。Android 原生运行时录制集成测试通过，覆盖批量去重、无效值、重启分段、CSV/WAV 封存。
- 图表以**合成数据**在独立无 Activity 测试 APK `dev.stackchan.collector.tests` 中离屏绘制，已查看 `android/Collector/build/runtime-test/chart-render.png`；这不是当前 App 整页截图/实时信号。早期 app_process 离屏绘制触发字体初始化断言，改用正规 Instrumentation 无窗口运行后通过。未停止或替换用户的 0.2 观察台。
- 新版未刷入，未安装到用户观察台，未检验真实遥控器、机械运动、视频/音频/50 Hz 共存、真实手机或后台录制。启动、断线释放、3 秒控制租约和反馈逻辑仍须实际验收。
- 此时只有 `/dev/cu.usbmodem3101`（另一台 StickS3），禁止误刷。已异步请用户接回 StackChan USB、确认官方遥控器是否在手边；尚未收到回答。仍应重新核对 MAC/活动分区后，仅写应用区域。
- CUA 无法识别无 App bundle 的 qemu 模拟器窗口，已询问用户是否允许改用 ADB 做观察台的界面操作/截图；尚未答复。只运行了无窗口、无用户输入的独立 SDK 单元/离屏渲染测试，不把它当成已获准操作原观察台 UI。
- 当前仍是前台采集。锁屏/退后台封存停止，未实现 SD 补录、GPS、姿态/身体坐标校准；外出长采的这些边界须明确。
- 候选产物与源码快照：`artifacts/collector-0.3.0-candidate/`，包含 manifest/SHA256SUMS。固件 2,425,008 字节，SHA256 `2b1e9edcb8aeba3241be25b6151973c1f412d68975dce82d8f15d98005f6b9a7`；APK 57,821 字节，SHA256 `d0d261860396418dccd2f2e3edea2f88b02dc5be252c854022e0c753c2eb2c1e`。产物标明未部署；只把已识别的 StackChan 作为未来刷机目标。

说明与协议见 [0.3 设计](field-collector-design.md)、[使用说明](collector-guide.md)、[协议](collector-protocol.md)。下面为 0.2 的既有实机基线，部署状态以这里的“未部署”说明为准。

## 用户目标与授权

第一优先是摄像头图像、声音和传感器实时传到 Android 观察与记录；Haro 情绪宝宝后置。用户明确出厂等价只为验证工程链路，不再追求官方云/App 功能等价。

用户已授权刷入自编译固件、安装独立 App、重启设备、同 Wi-Fi 直连、摄像头/麦克风/传感器测试，并允许 Android 模拟器。**保持机器人静音、舵机静止**。不要重复索要这些授权，不触碰另一个 StickS3。媒体、配对码、备份和串口日志仅存本机。

## 当前成果和入口

- [实施计划](collector-plan.md)、[使用说明](collector-guide.md)、[协议](collector-protocol.md)、[验证记录](collector-validation.md)。
- [情绪宝宝后续计划](haro-next-plan.md)：已核对 `~/stick/research/haro-resource-kit-20261028` 离线原型，H0–H6 明确交付/验收；本轮不实施、不改 stick。
- 官方源码 `upstream/StackChan/` 为独立 Git checkout，基线 `f901cc2969189f3f0e96af1442c0f96e77e1e2c1`。当前分支 **feat/collector-v0.1**，新增采集入口，修改仍未提交；保留这些改动。
- 根目录不是 Git 仓库。历史 0.1 产物仍在 `artifacts/collector-0.1.0/`；当前 0.2 产物和源码快照在 `artifacts/collector-0.2.0/`，有 manifest 和 SHA256SUMS。
- 固件入口 `firmware/main/collector/collector.cpp`，版本 `collector-0.2.0`。复用板级驱动，不启动官方云、小智 Application、OTA、舵机或播放；AW88298 RESET_N 保持 LOW，完全跳过输出 codec 初始化。
- Android 原生 Java App `android/Collector/`，包名 `dev.stackchan.collector`，0.2.0，最低 API26 / target35。固定横屏，左侧等比例画面及记录/标记，右侧IMU/触摸/电源/音频；连接设置为弹窗，观察历史在右栏向下滚动。支持录制、静音回放、ZIP 导出；实时监听需用户手动开启，默认关闭。只在前台采集，退后台自动封存。
- 协议：HTTP8080 info/telemetry/frame，8081单客户端带32字节SCA1头的PCM，Bearer32hex令牌。24kHz/16bit/两个逻辑通道 mic/reference，不能宣称物理双麦立体声。图像320×240，0.2.0目标10fps；这是连续HTTP JPEG帧，非H.264/MP4，实收以界面和录制报告为准。其他未实现外设明确unsupported。

## 设备与烧录状态

- StackChan 机身侧面 USB-C `/dev/cu.usbmodem1301`，MAC `<STACKCHAN_MAC>`，ESP32-S3 rev0.2，16MB Flash/8MB PSRAM。端口可能变化，每次使用先核对身份。
- `/dev/cu.usbmodem3101` 是另一台 StickS3，MAC `<OTHER_DEVICE_MAC>`，不要操作。
- 原机完整备份 `backups/stackchan-original-1.6.0-20261030.bin`，16,777,216字节，SHA256 `4acf656bf5fac20613abbd0e5eca32a4f9d60d679875882166d25cf3deebe1db`。保留，不重读、不上传。
- 刷机前核对有效otadata，活动为ota_1/0x510000。仅写当前采集应用2,374,544字节，并独立verify_flash digest matched；未写bootloader/分区表/assets/otadata，不擦除NVS。
- 当前 0.2 应用SHA256 `eade96bb7d7ba90eea28a5db325861e77fe96c3c72e51af985a3fc110c7c1157`。0.1 历史固件仍在旧artifact目录；0.2沿用同一应用区域，构建与写入记录前缀`collector-v02-release-`。
- 当前APK 0.2.0/code2 SHA256 `d84f11fd2443eb26d81aa8776505f28127be3a269dbc94c40f45f2aa029abf9b`，旧会话保留，录制manifest从PackageInfo读取实际版本。
- 初版曾短暂启动，代码审查发现功放构造默认解除硬件静音（没有播放数据）；已主动回loader并修正后刷入最终镜像。不要使用初版镜像或将主动中止的logs/collector-60s当作通过报告。
- 原机Wi-Fi已复用，当前机器人192.168.1.42；IP与token在`.private/collector-address`、`.private/collector-token`，勿回显token。新配置写collector命名空间；上游Board可能在uuid缺失时创建，但已确认原机board/uuid存在。

## 构建与环境

固定 ESP-IDF5.5.4，不升级SDK或依赖：`./scripts/idf.sh build`。IDF Python `.tools/espressif/python_env/idf5.5_py3.9_env/bin/python` 有esptool4.12；`.tools/host-python/bin/python`有pyserial；全量JPEG解码使用系统Python与Pillow11.3.0，实际环境见长测汇总。

Android SDK `~/Library/Android/sdk`、JDK `~/.local/jdks/temurin-17`，`android/Collector/build-app`无需Gradle，`test-host`运行音频解析/首通道隔离测试。ADB需绝对路径。AVD **StackChan_Collector_API_35**，当前序号emulator-5556；Android15/API35/arm64，2048MB，摄像头关闭、音频关闭。

本机模拟器直连机器人TCP报No route to host，Mac直连正常。使用 `scripts/collector-emulator-relay.py 192.168.1.42` 仅回环监听8080/8081，App填10.0.2.2:8080；透明转发真实字节，不生成/改写媒体。真手机仍应直接填机器人IP，模拟器结果不能代替真手机Wi-Fi验收。

## 已通过的验证

- 0.2：固定横屏布局、实收fps、六轴/触摸/电池/音频首屏展示已截图核对。95秒实机+模拟器录制530张JPEG全解码、5.66fps、831遥测/8.88Hz、4680音频块/零gap，写盘丢弃0、四组传感器valid全true、首末内存分布一致。报告`logs/collector-v02-app-summary.json`。最终统一版本信息镜像另测38秒/222帧5.87fps/347遥测/1887音频块，零gap/写盘丢弃，报告`logs/collector-v02-release-summary.json`。相机target10尚未达到，不能写成稳定10fps；当前仍是逐帧HTTP JPEG，没有multipart MJPEG/H.264/MP4。
- 0.2保留摄像头低优先级2但固定core1；StreamCaptures逐帧WARN降DEBUG。固件和App版本来自各自项目定义，避免manifest漂移。

以下为 0.1 的历史验收：

- 固件构建、diff check、写入校验和启动身份通过；源码patch反向check通过。未升级锁文件。
- Python协议工具15/15合成测试通过；Android解析/首通道隔离、构建和签名通过。
- 实机主机直连60秒PASS：2,997音频块、59.94秒、序号缺包0、最大时间连续性偏差2.192ms；114张320×240JPEG约1.90fps；IMU/头顶/屏幕触摸/电源valid，采集错误0。报告`logs/collector-final-60s/report.json`。
- 未授权/错误token返回401，第二音频客户端503。保存PCM有真实非零信号，无满量程削顶；不试听、不推断内容、不声称已验证通道物理映射。
- 模拟器合成故障记录/打标/静音回放/SAF导出通过；14.753秒，2boot段、29帧、142遥测、657音频块、9WAV，7个故意缺包均分WAV，无队列丢弃。`logs/collector-mock-export.zip`可验证。重启重连另有约1.2秒空窗。
- 实机长测会话 `20261030-235121-287` 正常封存620.243秒：1180张JPEG全部解码，6027遥测四组valid全true，30999音频块零缺失/重复/倒退，单WAV619.98秒、覆盖99.9576%，最大块间时间误差3.868ms。无意外重启/重连/写盘丢弃，首末分钟内存分布一致。见 `logs/collector-long-summary.json`。
- 真实记录静音回放/拖动、SAF导出通过，`logs/collector-long-export.zip`共31,919,460字节；1186个文件逐一摘要与封存目录一致。首次提取过早与flush竞争的副本已重新拉取，完整副本验证通过。
- 单独恢复会话 `20261031-000205-263`：停止/恢复本机relay和实际USB硬复位，App自动恢复、记录时间/序号空窗，按boot分段；令牌跨重启保持。HOME退后台自动停止并封存。56.950秒/61帧/330遥测/1674音频块/3段/队列丢弃0，verify-session通过。
- 断线故障测试没有断开实际Wi-Fi路由器；真实手机直连、监听/回放听感、麦克风物理映射、人工触摸/运动刺激、时间校准和长时续航仍未验收。首版只支持前台采集，不把后台服务列为已实现。

## 留存状态与继续方式

- 机器人正常运行最终采集固件，最近boot_id `6d00b62673f29236`；串口无人占用，扬声器硬件复位、无舵机命令。固件仍在内存采集并显示MIC+CAM ON，当前有观察台实时客户端，无写盘会话；停止硬件采集需关闭机器人电源。
- 夜间测试服务已清理。2026-10-31 用户要求看到观察台后，已重新启动**可见**模拟器与本机relay，进程记录在 `.private/collector-visible-processes.json`；观察台连接机器人，声音监听关闭，未开始录制。保留窗口供用户使用，不要在收尾时关闭。合成服务仍停止，无构建或验收监控运行。
- 用户可按 [使用说明](collector-guide.md) 启动可见AVD和本机relay，打开观察台；模拟器用10.0.2.2:8080，真手机直接用机器人IP。配对码见机器人屏幕或本机私有文件，不在聊天回显。
- 当前产物和源码/文档快照在 `artifacts/collector-0.2.0/`（保留 0.1 历史）；原始媒体与日志在 `logs/`，仅本机保存。不上传、不提交凭据；不删除已有记录。

## 建议下一步

1. 用户清醒后，用真实Android手机同Wi-Fi安装APK，确认图像、听感、触摸和IMU响应；不需要重刷已通过的固件或重复安装工具链。
2. 按真实使用决定后台录制、音频压缩、配网体验、补齐光线/接近等传感器与时间校准的优先级；首版限制已在文档写明。
3. 采集稳定后按 [情绪宝宝计划](haro-next-plan.md) 从离线事件回放、四种屏幕表情、持久心情逐步推进。声音/舵机需未来清醒实测，今晚授权不扩展为自动动作许可。

## 历史基线与MCP

原官方1.6.0完整构建与motion_math_test(1/1)已通过，原产物`artifacts/stackchan-1.6.0-public-f901cc2/`保留。出厂鉴权weak占位不等价于原厂服务，不再补齐该业务；详情[开发基线](development-baseline.md)。原机恢复尚未演练。

两个用户级MCP `espressif-documentation`、`esp-component-registry`已注册并启用，OAuth和实际调用均通过；无需重装/重登。查询仍核对ESP32-S3/IDF5.5.4，不能拿latest自动升级。未安装第三方ESP32 skills或IDF6+工程MCP。详情[开发工具](development-tools.md)。
