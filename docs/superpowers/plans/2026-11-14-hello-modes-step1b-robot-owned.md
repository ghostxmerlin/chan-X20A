# hello 模式 第一步（改版）：以 hello 为准

> 2026-11-14 下午，主人改了方向，这份计划取代 `2026-11-14-hello-modes-step1-framework.md` 里还没做的部分：
> - "其实我理解当中服务端对模式应该没有什么工作要做，无非就是有些模式开了某些功能，某些关了。它的主要发起都应该是在客户端发起的。"
> - "改成以 hello 为准，对。DualKey 还连不连服务器——连，只打分。"
>
> 规则本身不变，仍按 spec `2026-11-14-hello-modes-design-v2.md` 第 1.2 节主人第 1–15 条和第 3.8、5.x 节。变的只是执行的地方：从 mind 挪到固件。主人要尽早在 hello 身上看到变化，所以这一版只做最小的可见部分。上滑选模式页、下滑快捷设置页和各模式的占位页放到 0.4.93。

## 谁做什么

| | 以前（服务器说了算） | 现在（以 hello 为准） |
|---|---|---|
| 当前模式存在哪 | mind `hello.mode` | hello 内存（开机默认脱口秀；不写 NVS） |
| 谁执行切换规则 | mind `modes.transition` | 固件 `mode_rules.h`，和 `modes.py` 用同一套测试向量 |
| 口令、触摸 | hello → mind → 切换 → 推回 hello | hello 自己切，然后报给 mind |
| 网页模式条 | 页面 → mind | 页面 → 观察台 → hello（`/v1/control {target:"mode"}`，和舵机、相机走同一条路）；hello 报给 mind |
| DualKey | 打分 + 方向切模式 + 单击停说 + 摇杆灯 | **只打分** |
| mind 做什么 | 存模式、规则、去重、推快照 | 只记 hello 报上来的模式；按"效果"开关自己的功能（说一轮、停说、进出脱口秀打开/关闭提前写和连续说、家人声音），计费总开关拦着；总计费开关和 0–9 预设仍在 mind |
| 数字口令 0–9 | mind 执行预设 | hello 把 `digit` 发给 mind，mind 执行预设（花钱的服务都在服务器上） |

## 消息（`/mouth`，hello 上行；ear 原样转给 mind）

- `{"type":"mode_report","seq":N,"mode":"talk","dir":null,"host":null,"return_to":null,"by":"asr|touch|page|boot","input":"forward","effects":["stop_speech","talk_enter"]}`：hello 每切一次发一次；连上 `/mouth` 后先发一次当前状态（`by:"boot"` 只在真的开机后第一次）。`seq` 是 hello 自己的递增编号。
- `{"type":"mode_input","seq":N,"age_ms":A,"source":"asr","input":"digit","value":3}`：只有预设走这条，C1/A5 已经有了。
- 下行 `{"type":"mode_result",...}`：只回给 `mode_input`（预设）。mind 不再推快照。

## 任务（不再每个单独审；0.4.92 编好以后统一审一次，刷机前问主人）

1. **F1 规则移植**：
   - 把 `server/mind/modes.py` 的 `transition` 导出成测试向量 `tests/data/mode_vectors.json`。脚本放在 `tests/gen_mode_vectors.py`，覆盖每个模式 × 每个输入 × 托管与否 × 正在说与否，包括 `boot`、`timeout`、`select` 各值。
   - 固件 `mode_rules.h` 是纯函数，包含模式表（label、eye、touch）。
   - 主机测试 `tests/mode_rules_test.cpp` 逐条重放向量，要求新状态、结果码、效果完全一致。
   - `digit` 和 `billing` 不在 `mode_rules` 里。
2. **F2 接线 + 外观**（复用 C1 `mode_wire.h`、C2 口令映射、C3 手势、C4 的 `/mouth` 收发）：
   - 口令、首页单击/双击/左滑，以及 `/v1/control {target:"mode", input, value}` 都交给 `mode_rules`。
   - 首页单击不再开摘要页，改成左滑开；上滑、下滑这一版先不接。
   - 切换后换眼睛：脱口秀一律笑面男；角上显示 ASCII 模式名（TALK、IDLE 等）；用 `mode_report` 报给 mind。
   - "正在说"的判断用 hello 自己是否在播放。
   - 设置模式 5 分钟没动作就回去，计时在 hello 上。
   - 状态放 PSRAM；LVGL 小对象放内部 RAM，用时才建。
3. **S1 mind 改成只记录**：
   - `POST /mode/report`（ear 转来的 `mode_report`）：记下状态，按 `effects` 执行钩子，计费总开关照样拦，同一个 `seq` 只执行一次。
   - `POST /mode` 和 `/mode/input`，除了 `digit` 和 `billing`，都不再自己切换。`digit` 和 `billing` 仍走 A3 的预设和计费。
   - 关掉 DualKey 方向键和单击 → 模式，只保留打分。
   - `GET /mode` 返回 hello 报上来的状态。
4. **S2 ear**：
   - 把 `mode_report` 转给 mind。
   - 不再按 `/mode/seq` 推快照，DualKey 摇杆灯也不推了（A6 撤掉）。
   - `mode_input`（预设）照旧。
5. **W1 网页**：
   - 模式条的点击改成发给 hello（观察台 `control.mjs` 加 `mode` 动作：`input`、`value`），状态从 hello 的遥测读，没有就读 mind 的 `/mode`。
   - 预设、计费开关还走 mind。
6. **B 构建与统一审查**：
   - 版本 `collector-0.4.92`，用 `IDF_PYTHON_ENV_PATH=.tools/espressif/python_env/idf5.5_py3.13_env ./scripts/idf.sh build` 编译。
   - 准备刷机脚本，照 `.private/wifi-factory-v0491/`：身份核对、整槽备份、只写 ota_1、独立校验。
   - 统一审查：C1–C3、F1、F2、S1、S2、W1 一起审。
   - **停下来问主人刷不刷**；服务器 S1、S2 和刷机同一次上线。

## 现在服务器上已经上线的（16:05）怎么办

不退回。0.4.92 刷进去之前，以 mind 为准的版本照样能用：8787 模式条能切，切到不是脱口秀时 hello 不说话。S1、S2 和 0.4.92 同一次上线，替换掉现在的版本。DualKey 切模式在 S1 上线前还有效，主人知道。
