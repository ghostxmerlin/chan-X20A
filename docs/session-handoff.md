# 会话交接记录

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
