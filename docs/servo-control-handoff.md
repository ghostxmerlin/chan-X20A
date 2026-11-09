# 网页舵机控制交接：左右步进与仰角

更新：2026-11-08。接手对象为同机 Claude；本文件只交接问题和证据，不表示故障已修复。

## 2026-11-08 Claude 接手结论（优先于下文）

1. **根因已确认，分两层。** 软件层：原厂自动卸力在最终目标写入后 20–40 ms 即释放且不核对实测，已在 0.4.39 改为到位门控（≥500 ms、≤1.0°、最长 2 s）。硬件/配置层：两颗 SCS0009 出厂 P=15、I=0、启动力 30‰，yaw 轴负载下舵机输出 ≈15×误差步数（‰），误差缩到 12–17 步（约 4–5°）时输出 20–27% 便被静摩擦停住；网页 ±5° 的 16 步误差整体落在停滞区，任何软件路径都一样。遥控"可靠"是大幅度加人工闭环。俯仰轴摩擦小，同参数能到位（已验收）。
2. **证据**：`logs/web-servo-v0439/`（ab-yaw_*、yaw-left-600-diag、pitch-15、pitch-10-restore、state-0440*）；0.4.40+ 遥测 `remote.motor.<axis>.diagnostics.motion_trace`（位置/负载/电压/温度/运动标志 100 ms 采样）与 `motion_summary`。
3. **下一步**：用 0.4.42 的"舵机参数"面板调 yaw 的 P/启动力/死区（建议从 P30、启动力 200、死区 2/2 起，异常即恢复出厂），验收口径仍是网页左右各一条、实测进入 ±1° 并保持 ≥0.5 s；不行再考虑固件外环或换 STS3032/XL330。追踪 1° 步进在当前参数下不可能到位，调参后再评估。

## 当前任务与结论

用户当前优先要求修好 **Mac 网页左右转动和仰角设置**。用户指出只有遥控器路径可靠，并表示“如果你搞不定，把问题写下来，我请 Claude 做”。先解决显式网页控制，再接回人脸追踪；不要继续靠增大追踪幅度、延长保护或反复刷机试错。

**根因未知。** 网页 yaw 没有完整 ±5°机械到位验收；网页 pitch 的 10°曾由用户确认到位，但当前 0.4.38 没有新一轮网页调角验收。HTTP 接受、写指令 ACK、目标寄存器读回、软件 `trajectory_complete` 都不等于实测到位。已知遥控正常不能被忽略，也不能据此断言两条入口用了不同的硬件驱动。

本轮因没有可靠修复而停止继续刷候选或试转，并交接给 Claude；这不撤回用户此前对开发设备刷机及小幅测试的授权。接手者形成可核验候选后，按既有设备边界继续，不必因本轮暂停泛泛重新索取权限。

头部装饰及 M12 增加了负载。用户此前报告俯仰运动抖动、过冲，要求停用遥控上下直控；现有慢俯仰曲线、保存角度和机械限制应保留。当前四向遥控是模式开关，不应为排查永久恢复直接转动。

## 源码、设备和未提交工作

- 项目：`/Users/ghostxmerlin/soul/chan-X20A`，Git 根目录在这里，`upstream/StackChan` 不是 submodule。
- 分支：`codex/continuous-hearing`；HEAD：`cbcadf045689a284b80dd5304094efa690b4fa44`。HEAD 对应 0.4.36；当前工作树包含尚未提交的 0.4.37/0.4.38 修改，不能仅检出 HEAD 就当作设备源码。
- 最后部署：`collector-0.4.38`，应用 3,506,432 B，SHA256 `341dd48978816ddd6a1499624c296a683f354270cdae0fa63f181ebd12ea7503`。产物：`artifacts/collector-0.4.38-tracking-curve/{stack-chan.bin,manifest.json}`。
- 本体底部 320×3 px 五色功能条已由用户在 0.4.37 实屏确认“看到了，显示正常”，0.4.38 没有改它；**保留该已验收功能**。追踪机械效果仍未通过。工作树还包括只读诊断、追踪 speed 0→200、对应测试和交接文档，不能全部丢弃或统称已修复。
- 收尾只读状态：0.4.38；追踪/认人/听觉关闭，M12 standby，v14 眼睛可见，保存仰角 10°。最新反馈 yaw 0.6°、pitch 10.9°均 valid；`motor_state=web_yaw` 是已有状态，不证明本轮自动执行了动作，也不证明网页到位。准确请求和时间以以下本机快照为准。
- 最小同机快照目录：`.private/servo-web-handoff-20261108/`。`manifest.json`、`working-tree.patch`、`untracked-files/` 由本轮收尾生成；先核对清单与 SHA，再使用。`device-state-redacted.json` 为脱敏设备状态，`device-state-private.json` 含设备私有信息，勿外发。不要在 dirty 工作树上重复套用 patch；其用途是独立目录恢复和差异审查。`servo-web-handoff.zip` 只包含源码差异、新增文件、清单与脱敏证据，未包含凭据、完整私有快照、固件及Flash备份；同机接手优先直接读原工作树。

## 已有证据：哪些真的动过

| 场景 | 指令、反馈与用户观察 | 能得出的结论 |
| --- | --- | --- |
| 2026-11-07 20:46–20:51，0.4.20 网页 yaw | 起始 4.3°/raw475；共接受 5 次 yaw。最后目标 −0.7°，20:48:39 和 20:51:32 反馈仍为 3.4°/raw472，状态 `trajectory_complete`；用户只说“有点动作” | 有小变化，不能认定完整 ±5°或准确到位。停用 20:47:26.171、启用 20:47:27.797 均早于首个 yaw 20:47:30.068；随后无停止记录且 `rx_packets=0`，没有提前取消/遥控抢占证据 |
| 2026-11-07，0.4.22→0.4.23 | 用户明确“左右正常”；另说“网页设置 10 度，可以到位”，随后遥控推上松手停在约 45° | “左右正常”是遥控基线；网页 10°到位是 **pitch**，不能转写为网页 yaw 验收。47.1°中位问题后来单独修正 |
| 2026-10-31，0.3.1 临时遥控限制 | 起始附近 ±5°、speed80；文档记录实测 yaw 1.5…6.5°，用户“能正常小幅转动” | 不能仅以“遥控幅度大/速度高”解释新入口失效。原始旧 JSON 当前指定路径缺失，本次依据保存的验证文档，不是新复测 |
| 2026-10-31，0.3.2 / 0.3.7 遥控 | 0.3.2 speed600，用户“两种转头正常”；0.3.7 following 期间有效 yaw −36.5…84° | 存在明确遥控运动历史；不是当前 0.4.38 直接遥控复测 |

0.4.20 原始证据：`logs/web-controls-v0420/{postflash-state,live-controls,final-state}.json`。最近遥控片段：`logs/recognition-v0422/{remote-live,final-state}.json`。历史原话和完整边界见 `docs/session-handoff.md` 中对应版本，以及 `docs/collector-validation.md` 的 0.3.1/0.3.2/0.3.7 节。

### 最新网页 yaw 只读证据：最终目标后很快卸力

优先读脱敏文件 `.private/servo-web-handoff-20261108/latest-web-yaw-evidence.json`。**本次交接整理与只读核对期间没有发送任何运动命令或刷机**；以下是读取到的既存网页请求及缓存事务：

- 0.4.38，网页 `request_seq=2`，请求时间 `1083073296µs`，目标 −3.8°，软件状态 `trajectory_complete`。
- 最后位置写事务从 `1087403488µs` 开始，负载目标 raw449、time20/speed0，ACK成功，持续5.542ms。这里证明发送负载及ACK，**没有该网页命令的goal42寄存器读回**。
- 扭矩关闭事务从 `1087438903µs` 开始，写40=0并ACK成功。两次事务开始时间相差 **35.415ms**；按记录计算，前次写事务结束到关闭事务开始约29.873ms。两者都是软件事务计时，不是物理保持时间。
- 较晚快照 `1240282074µs` 的实测 yaw 为raw463/0.6°，pitch10.9°、保存10°。这比卸力开始晚约152.843秒；没有卸力瞬间的位置采样，不能据较晚误差断言当时未到位，也不能直接认定早卸力就是机械根因。

代码中的 **200ms 是扭矩检查节流，不是最后目标提交后至少保持200ms**。本轮证实了具体的快速卸力时序；下一次应补“最后目标提交→实测位置→卸力”的同期记录，而不是继续猜测。

## 0.4.37 / 0.4.38 只读探针结果

motor task 是唯一 UART owner。新增诊断没有 `EnableTorque(1)`、EEPROM 写或周期轮询：

- 每条 **追踪** Move 最多三次读取寄存器 40…47：发令前、缓存确认首次新位置写入后、取消前。保存 torque40、goal42/time44/speed46、传输错误/状态/时间及首次写事务。未知/失败不伪装成 torque0。
- 启动静止时每轴一次读取 9…27，包含角度上下限、phase18、deadband26/27。保留原 `deadband_configuration` JSON 名称。
- HTTP `GET /v1/telemetry?servo_diag=1` 只返回缓存。路径：`remote.motor.yaw.diagnostics.tracking_torque` 和两轴 `diagnostics.deadband_configuration`。当前三阶段探针**没有覆盖网页 yaw / pitch 命令**；不能用最近一次追踪快照解释后来的网页动作。
- `IOTimeOut` 临时设为 2 后恢复；这不代表包含发送等待、解析、调度的整笔事务绝对 ≤2ms。

| 版本 | 观察窗口 | 结果 |
| --- | --- | --- |
| 0.4.37 | 5.567 秒，1 条 1°命令，起点 raw465/1.2°→目标 0.2° | 首次写 raw464 后 torque0→1；2秒保护前 goal462/torque1。实测465短暂466，卸力回465；没有朝目标进展。硬件限位20/1003、phase1、死区1/1 |
| 0.4.38 | 先有15.143秒无人脸新帧/0命令窗口；另一次5.699秒有效窗口 | 同一1°目标，取消前已读回完整goal461且torque1；实测465→466（反方向一格），卸力回465，仍无朝目标进展，累计活动2秒保护停止 |

0.4.37约2秒仅47次位置写，固定20ms动画模型只推进约0.94秒。0.4.38将追踪 speed0→200 后能在保护前提交完整目标，但机械仍未进展。因此“曲线来不及提交目标”只解释了一个已改善的环节，**不是已确认的唯一根因**。

先前 raw452→465 是用户手动转头，不能计为程序驱动。非零硬件限位排除了本库 `SwitchMode(PWM)` 所用的双零限位配置；历史遥控调用的是 `moveWithSpeed`，没有证据证明旧遥控切入 PWM。torque1 只证明寄存器状态，不能证明负载下物理扭矩。无需凭软件默认值恢复 EEPROM、改零点或判定舵机损坏。

证据：`logs/tracking-v0437/{tracking-live-motion,tracking-motion-summary}.json`；`logs/tracking-v0438/{tracking-no-frame,tracking-live-motion,tracking-motion-summary,deployment,boot-check}.json`。

## 精确入口与关键差异

以下路径以仓库根目录为基准；`F` 表示 `upstream/StackChan/firmware/main`。

| 层 | 文件与入口 | 重点 |
| --- | --- | --- |
| 网页按钮 | `web/monitor/public/control-ui.js`：`ControlPanel` | 左/右发送−5/+5，仰角显式保存；刷新、重连不发动作 |
| 本机代理 | `web/monitor/server.mjs` 的 `/api/control`，`web/monitor/control.mjs`：`Controls` | 同源/boot/状态门控；yaw→`/v1/motion/yaw-step`，pitch→`/v1/settings/pitch`，启停→`/v1/remote` |
| 设备HTTP | `F/collector/collector.cpp`：`web_control_handler`、`pitch_handler`、`remote_handler` | yaw 整数±5乘10后入队；pitch是0.1°单位，先保存后调度；HTTP不触碰UART |
| 任务与仲裁 | `F/collector/remote.cpp`：`remote_step_yaw`、`remote_set_pitch`、`motor_task` | 新鲜有效反馈、静止门控；网页调角退出追踪；实际消费请求、`update`和取消都在motor task |
| 请求状态 | `F/collector/web_control.h`：`ManualYaw`；`pitch_setting.h`；`startup_pose.h`：`StartupPose` | yaw队列1秒过期不等于已启动动作1秒截止；pitch等待反馈5秒、轨迹最多15秒，不自动重试 |
| 共同运动模型 | `F/stackchan/motion/servo.{h,cpp}` | `moveFromFeedbackWithSpeed`、`moveWithSpeed`、`update_angle_anim_target`、`update`、`cancelMotion`；固定20ms模型与真实调度不同 |
| 硬件写入 | `F/hal/hal_servo.cpp`：`ScsServo::set_angle_impl`、`check_mode`、`readFeedback` | angle是十分之一度；raw=`zero+angle*16/5/10`；`WritePos(id,raw,20,0)`；不凭回退值冒充实测 |
| 协议与诊断 | `F/hal/drivers/FTServo_Arduino/src/{SCSCL,SCS,SCSerial}.cpp` | 位置写42…47、ReadMove、扭矩40与配置读回；SCSCL字为高字节先行 |

旧遥控参考提交 `7700e7d`（0.4.23；0.4.22候选未单独提交），其 yaw 执行与父提交 `5b8036d`（0.4.20）相同。只用 `git show` 或独立目录比较，勿 reset 当前工作树。

- 旧遥控：绝对目标、通常speed600；新控制会话同步物理起点，连续改目标保留弹簧动量，持续 `update`。
- 网页yaw：相对±5°、speed100；每次从有效反馈重置轨迹。`delta_deg ±5→±50→HAL /10` 已核对，没有找到单位错配。
- 追踪：相对≤1°、speed0→200，另有累计2秒无进展保护；它不等同于网页yaw。
- 自动卸力共用：软件曲线结束、最终目标已提交且 `ReadMove=0` 后，周期检查扭矩再关闭；**没有验证实际目标误差**。0.4.38已捕获上述35.415ms事务间隔，但缺卸力瞬间位置；“卸力太早导致不动”仍是假设，不能定因。
- 俯仰保留 `gentle_load` 曲线及原限位/堵转保护；不要把yaw诊断改动扩散到pitch控制参数。

## 测试已经覆盖什么、漏了什么

0.4.37 Python77项中73通过/4旧跳过，Node63通过；0.4.38重跑了追踪3组回归及完整固件构建，没有重新跑全部套件。这些记录不是网页运动验收。

- `test_web_control.py`、`web/monitor/control*.test.mjs`：参数、排队、取消、过期、UI/HTTP门控；没有真实机械。
- `test_pitch_setting.py`：持久化、反馈起步、真实Servo/spring、曲线/时限；模拟轴在`set_angle_impl`里直接置torque=true，不能验证实物扭矩。
- `test_face_tracking.py`：运行真实Servo、方向/冻结反馈/累计保护等，但关闭动画库默认HAL后没有为相关跟踪测试安装其墙钟钩子；不能据其固定步长结果证明连续遥控retarget等价。独立复核 `.private/historical-motion-review/retarget-clock-comparison.{cpp,txt}` 用隔离7700e7d展示开启真实墙钟后轨迹不同；这只揭示覆盖缺口。
- `test_servo_diagnostics.py`：真实SCS解析器、只读配置块、错误区分/超时恢复；`test_servo_torque_snapshot.py`：40…47快照与每命令最多3读。仍不是负载下输出验证。
- 尚缺：同条件下旧遥控入口与网页入口的逐写对照、网页动作中与最后目标/释放同期的goal读回和position轨迹、当前固件网页pitch的用户实屏验收。现有35.415ms关闭记录补了事务时序，未补位置因果。不要用ACK或模拟物理跟随替代这些缺口。

## 下一次只做一组可判别的对照

先离线建立 **旧遥控yaw入口 vs 网页yaw入口** 的同条件A/B：使用真实Servo和真实动画库时钟钩子；同一有效反馈起点、同一内部绝对目标（起点+5°且在限位内）、同一speed、同一更新时序、同一卸力规则；禁用其它模式/俯仰请求。逐次比较目标raw、写间隔、扭矩释放与请求状态。不要让“一个连续retarget、一个单发”混进该对照；另把差异明确记录。

离线差异解释清楚后，若需上机验证，只把这组受限A/B接入同一motor task：旧入口用内部诊断适配层，不能永久恢复遥控四向直控，也不回刷会改其它功能的旧整机固件。两路各一次，不自动往返、加幅或续试；相同起点/负载/供电条件不成立时标为不可直接比较。完整记录从请求前到最后目标写入、实际位置变化、最终卸力的时间线，并请用户确认方向和运动。现有三阶段追踪探针若复用到网页，必须带独立来源和请求序号，不能误读旧追踪快照。

建议先定下验收口径再测试：网页左/右各一条明确请求，正确方向、无异常抖动/过冲，实测进入目标±1°并保持至少0.5秒，用户看到对应转动；只显示`trajectory_complete`不算。若单次不进展或异常立即停止，不靠重复请求“试到成功”。此容差是建议验收值，不是硬件规格结论。

网页pitch随后单独验：选用户认可的附近目标，保留`gentle_load`，检查NVS保存、有效起点、实际角度趋近目标、停止可取消且旧请求不重放。10°历史成功和当前10.9°静态读数都不能替代一次新的显式调角。不要让设备开机重置保存值，也不要为了证明持久化自动反复重启或抬头。

## 构建、身份与部署边界

本次只撰写交接，未因交接构建、刷机或执行动作。后续先读 `AGENTS.md`、`docs/build.md` 和本文件；保留 dirty 工作及本地媒体/配置。

1. 固定 ESP-IDF **5.5.4**；使用已有 `.tools`、`sdkconfig`、`dependencies.lock`、`repos.json`，不要升级或覆盖。本机需选用Python3.13 shim，仓库根目录的已验证命令为 `env PATH="$PWD/.tools/py313bin:$PATH" ./scripts/idf.sh build`；不要误用系统Python3.14创建另一套IDF环境。CMake已接入 `cmake/spi_allocation_fix.cmake` 的受控副本补丁，不手改原IDF。不要为“清理”删除当前SD/语音构建资源。
2. 可先跑相关host检查：`python3 -B -m unittest discover -s tests -p 'test_web_control.py'`、`test_pitch_setting.py`、`test_servo_diagnostics.py`、`test_servo_torque_snapshot.py`；网页用 `node --test web/monitor/control.test.mjs web/monitor/control-ui.test.mjs`。修改时钟/共享Servo后还要复查相应运动回归；完整命令/版本及失败应写入新日志。
3. 身份、地址和token只引用 `.private/stackchan-monitor.json`，通过已有 `web/monitor/core.mjs` 的 `DeviceClient` 做挑战验证与请求；不要打印配置、复制到命令行、文档或外发材料。本机监视服务入口 `scripts/open-stackchan-monitor.command`，页面在本机8787端口；刷新不应触发动作。
4. 如后续确需部署，先从实机重新确认芯片/Flash/绑定身份、分区表、otadata CRC及活动槽；现有开发机历史活动槽是 `ota_1 / 0x510000`，不可照搬给别的设备。完整备份当前活动槽、匹配已归档应用、独立verify备份，然后只写新应用并独立verify；不写bootloader/分区表/otadata/NVS/媒体或校准。`backups/`旧备份不覆盖。
5. `.private/tracking-v0438/{preflight,deploy}.py` 是上一轮流程参考，**硬编码旧版本、产物与备份名，不可直接重跑**；新轮次先改为新目录/当前镜像、重新核验。打开USB串口可能引发复位，不能无意反复开串口；USB路径和设备标识不得凭历史猜测。
6. 部署后读版本/boot/保存角/当前请求/位置/模式/错误，明确写出源码、构建、刷入、机械验收各自结果。现场 `logs/`、`artifacts/`、`backups/`、`.private/` 只留本机，不随Git提交或对外发包。

交付前至少回答：网页请求在哪里偏离已工作的遥控基线；目标何时到达寄存器；真实角度是否跟随；扭矩在什么条件下释放；pitch是否独立通过。没有这些证据就保持“未解决”，不要继续承诺已经修好。
