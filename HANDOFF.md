# 交接：机身 ASR + M12 → codex-astra

> **后续已变更（2026-11-07）：** 用户要求收成基础版；当前工作为 `collector-0.4.17`，情绪和 M12 自动策略已停用，只保留 ASR 显式开关 M12。最新源码/构建/设备状态见 [session-handoff](docs/session-handoff.md)。下文是 Claude 交接时的 0.4.16 历史，不能再照其自动策略验收。

2026-11-07，Claude（Opus）写给 codex-astra。

开工前先读 [AGENTS.md](AGENTS.md)（规则）和 [docs/session-handoff.md](docs/session-handoff.md) 最新一节，再读本文件。

## 现状

**设备：** 开发机器人运行 `collector-0.4.16`。

**源码：**
- **0.4.13 已提交并推送**（`0715d17`），ASR 和 M12 人脸视图在 0.4.13 上实测可用，用户确认过。
- **0.4.14–0.4.16 的源码和本文件一起提交了**，但 M12 开机策略还没在实机上验证通过，见第 4、5 节。

> 刷机前先读 ota_1 里的版本号，再和源码对照。这台设备曾经跑着比仓库新的 0.4.8，源码在另一台机器上没提交，差点被旧版覆盖。

## 1. 设备和接线（交接时）

| 设备 | 状态 |
| --- | --- |
| StackChan 开发机 | ESP32-S3 QFN56 v0.2，16 MB Flash，活动分区 ota_1。运行 0.4.16，USB 连 Mac（`/dev/cu.usbmodem1101`） |
| Unit ASR（CI-03T） | 顶部黑口（Port B），模块 TX 接 GPIO8（实测），UART2 只收。出厂英文词表，用户决定先不定制 |
| M12（UnitV2 M12） | 正式位置是顶部蓝口（Port C）：UART0，TX17 / RX18。**交接时它已从 StackChan 拔下，用 USB 直连 Mac**，用户刚在面板上做完人脸登记：已登记 **papa** |

**M12 的配置：**
- 开机默认功能：`boot_func` 是 `camera_stream`，切换功能不会改这一项。
- WiFi：保存了 4 个热点，两个手机热点排在两个家里网络前面，交接时连在家里网络。名字和顺序用 `python3 m12/panel/wifi_setup.py --status` 查看，这里不写。
- 用 USB 连 Mac 时，Mac 看到的网卡是 `en11`，M12 地址 `10.254.239.1`。

**三个串口：** UART0 = M12，UART1 = 舵机，UART2 = ASR。日志控制台只走 USB（共享的 `config/collector-0.3.4.sdkconfig` 和 `sdkconfig.defaults` 已改）。

**中断：** 0 号核只剩约 3 个通用中断。新的串口或中断驱动必须在 1 号核的任务里安装。0.4.11 就是装在 0 号核，WiFi 首次连接申请 AES 中断失败，每次开机约 35 秒 abort。每次开机 8 秒时 USB 日志会打印一次中断分配表。

## 2. 版本和产物

产物都在本机，不提交，SHA256 文件在同一目录。

| 版本 | 内容 | 实机结果 | 产物 |
| --- | --- | --- | --- |
| 0.4.8 | 另一台机器的版本 | 刷机前的原样备份 | `backups/ota_1-collector-0.4.8-20261107.bin`（`3e553ed1…`） |
| 0.4.10 | dev 的 0.4.9 源码加 ASR | USB 看不到程序日志 | `artifacts/collector-0.4.10-asr/` |
| 0.4.11 | 加 M12 和人脸页，日志只走 USB | 开机约 35 秒 abort（中断用尽）；人脸页一直显示 `no face`（小数坐标按整数读了） | `artifacts/collector-0.4.11-m12-faces/` |
| 0.4.12 | 串口驱动移到 1 号核，ASR 固定 GPIO8 | 不崩 | `artifacts/collector-0.4.12-intr-core1/` |
| **0.4.13** | 坐标按小数读 | **用户确认可用（已提交）** | `artifacts/collector-0.4.13-m12-float/`（`8aedeffa…`） |
| 0.4.14 | turn on 复用已在跑的检测；turn off 后保温 60 秒 | 冷启动 2.4 秒；60 秒后准时进待机 | `artifacts/collector-0.4.14-m12-warm/`（`17c19105…`） |
| 0.4.15 | M12 开机策略（第 4 节） | M12 不回应切换命令（第 5 节第 2 条） | `artifacts/collector-0.4.15-m12-policy/`（`0920e025…`） |
| **0.4.16** | 重发不再放弃：先 6 次每 8 秒，之后每 30 秒 | **设备上现在跑的版本**；M12 断电重启后还没实测 | `artifacts/collector-0.4.16-m12-retry/`（`56020536…`） |

## 3. 0.4.14–0.4.16 的改动（随本文件提交）

| 文件 | 改动 |
| --- | --- |
| `main/collector/m12_protocol.h` | 新增 `kM12FaceRecognitionCommand`；解析识别结果的 `name` 和 `match_prob`（`M12Line.recognition`）；`M12Policy` 开机策略，替换 0.4.14 的 `M12Scheduler` |
| `main/collector/m12_unit.{h,cpp}` | M12 任务按策略发命令；新增 `m12_wake()`、`m12_set_view()`，替换 `m12_request_faces`；识别统计；重发不封顶；遥测新增 `phase`、`function`、`identity`、`recognitions` |
| `main/collector/collector.cpp` | 任何 ASR 命令、屏幕按下、遥控收包都调用 `m12_wake()`；turn on / turn off 只开关人脸页；人脸页状态行显示识别结果（`who is it? ...`、`faces 1  papa`） |
| `upstream/StackChan/firmware/CMakeLists.txt` | 版本号 `collector-0.4.16` |
| `tests/m12_protocol_test.cpp` | 识别字段测试和策略测试，**已通过** |
| `m12/panel/wifi_setup.py` | 新增 `--order "A" "B" ...`：只改已保存 WiFi 的优先级，不需要密码，支持 `--dry-run`。已在设备上用过 |

**提交时的状态：**
1. **主机测试：** 提交前重跑了一遍，全部通过：Python 34 项（4 项跳过）、`asr_protocol_test`、`m12_protocol_test`（含策略）、`remote_protocol_test`，以及 `tests/display` 的四组。
2. **还没做：** 更新文档，以下几处还停在 0.4.13：`docs/collector-protocol.md` 的 M12 一节（策略、新的遥测字段）、`docs/collector-validation.md`、`docs/session-handoff.md`、README 和 AGENTS 的状态行。
3. **还没做：** 实机验证第 5 节第 1 条。

## 4. M12 开机策略（0.4.15 起，未实机验证）

用户的要求：开机后先做一段时间人脸追踪，甚至人脸识别，然后再考虑待机；待机用 Audio FFT 一类的功能降温。

**实现：** `M12Policy` 是纯逻辑，有主机测试。M12 任务把结果和时间喂给它，`function()` 一变就发命令。

**流程：**
1. **looking：** 开机就让 M12 跑 Face Detector。看到第一个真实结果后，至少再看 60 秒。
2. **identifying：** 新面孔出现时（之前 5 秒没有脸），切到 Face Recognition，最多 10 秒。
   - `match_prob` ≥ 0.5 就记下名字；收到 6 条结果仍没匹配，或者 10 秒超时，记作“没认出”。
   - 然后切回检测。两次识别至少间隔 2 分钟。
3. **watching：** 有人就一直检测。
4. **dozing：** 没有脸、也没有任何互动，超过 60 秒，进入 Audio FFT 待机（摄像头关闭，CPU 约 15%，约 78°C）。人脸页开着时不会待机。
5. **唤醒：** 任何 ASR 命令、屏幕按下、遥控收包，或者打开人脸页，都会回到 looking。

**其他规则：**
- **复用：** 要检测时，如果 1.5 秒内收到过检测结果，就直接用，不重发命令（避免 2.5 秒的重启）。
- **重发：** 没有回应时，前 6 次每 8 秒重发一次，之后每 30 秒一直重发。
- **识别期间不画框：** 识别结果的坐标量程没有实测（文档说是 0–320 / 0–240，检测是 640×480），所以识别期间人脸页不画框，状态行显示 `who is it? ...`。

**日志关键字：** `M12: phase: a -> b`、`send face_detector|face_recognition|standby`、`Face Detector already running, reused`、`identified: papa (match 0.83)`。另外每 10 秒打一行人脸结果统计。

## 5. 待验证和已知问题（按优先级）

1. **实机验证 0.4.16 的策略。** 接线时先让整机断电，再把 M12 接回蓝口、开机。理由见第 3 条。看日志是否依次出现：
   - `looking` → `send face_detector` → 有人脸结果
   - → `identifying` → `identified: papa`
   - → `watching`
   - → 人离开 60 秒后 `dozing` → 切到 `audio_fft`
   - → 说话或触摸后回到 `looking`

   顺便看一下识别结果的真实格式：每次切换后的前两行会以 `raw` 开头打出来。
2. **M12 在 Audio FFT 待机较久后可能切不出来（推测，没复现）。**
   - **0.4.15 刷机时**，M12 已经待机好几分钟。它只回了清缓冲空行那一条 `invalid json format`，人脸检测命令重发 6 次都没回应。
   - **0.4.16 时**，它一个字节都不回。
   - **断电重启后恢复了。** 0.4.14 里待机 8 秒后能正常切出。
   - **怀疑方向：** 设备上 `server_core.py` 的 `switchFunction` 在 `thread_pipe_recv.pause()` 或杀进程时卡住，可能是没人读 audio_fft 的 meta 管道。
   - **复现方法：** 让它待机 5–10 分钟，再说话唤醒。属实的话，要换待机方式，或者给 M12 打补丁。设备 SSH 和源码分析见 `docs/m12-unitv2.md`。
3. **热插 M12 时，StackChan 的 USB 掉线过一次。** 发生在开机约 42 秒，用户在插拔 M12。推测是 M12 上电时拉低了 5V，没有验证。
4. **识别结果的格式没有实测。** `name` 和 `match_prob` 两个字段名是按 M5 官方面板的代码推测的。
5. **0.4.9 的情绪角落和双音频随 0.4.10 起的版本上了机器，没有单独验收。**
6. **ASR 仍要先唤醒。**
   - 出厂固件要先说 Hi M Five / hello / hi A S R，之后 20 秒内的命令才有效，串口只接受 `AA 55 FE 55 AA` 这一条输入。
   - 要一直处于听命令状态，得在智能公元平台重新生成固件：设免唤醒词，或者加“保持听 / 停止听”的串口命令，由 StackChan 控制。用户还没决定。配置模板在仓库外的本机目录 `soul/voice/Unit_ASR_Config_Template/`。
7. **M12 冷启动 2.5 秒**：固件里固定等 1 秒，再加载模型、打开摄像头。可以改 M12 上的 `server_core.py`，或者写常驻程序（约 0.15 秒），都还没做。
8. **安全：** 设备发现的挑战证明可以被转发，安全检查报告过，用户在调试阶段暂不处理。

## 6. 本机环境和常用命令

以下命令都在仓库根目录运行。

**编译：** 这台 Mac 的 `.tools/esp-idf-v5.5.4` 是新装的。系统 python3 是 3.14，对 IDF 5.5 太新，所以用一个指向 python3.13 的链接：

```sh
PATH="$PWD/.tools/py313bin:$PATH" ./scripts/idf.sh build
```

**刷机：** 只写 ota_1，并单独校验。每次刷机前重做第 1 步。

```sh
ET=".tools/espressif/python_env/idf5.5_py3.13_env/bin/python -m esptool --chip esp32s3 -p /dev/cu.usbmodem1101"
# 1. 核对身份和活动分区（只读）
$ET --before default_reset --after no_reset flash_id        # ESP32-S3 (QFN56) v0.2、16MB
$ET --before default_reset --after no_reset read_flash 0xd000 0x2000 /tmp/otadata.bin
#    otadata：两条记录 seq，CRC 为 binascii.crc32(seq, 0xFFFFFFFF)；活动槽 = (最大有效 seq - 1) % 2，必须是 1
$ET --before default_reset --after no_reset read_flash 0x510000 0x200 /tmp/ota1.bin
#    偏移 0x30 处是版本字符串，和源码对照
# 2. 只写应用区，再独立校验
$ET --before default_reset --after no_reset write_flash 0x510000 upstream/StackChan/firmware/build/stack-chan.bin
$ET --before default_reset --after hard_reset verify_flash 0x510000 upstream/StackChan/firmware/build/stack-chan.bin
```

- esptool 连不上（`No serial data received`）时，拔插一次 USB 就好了。
- 用 `--after no_reset` 读完以后，芯片停在下载模式，记得用 `--after hard_reset` 重启它。

**看日志：** 日志走 USB。用 pyserial 打开端口，`dtr` 和 `rts` 都设为 False 再 `open()`。刷机后马上打开能录到完整开机；之后再打开一般不会复位芯片，但 0.4.16 有一次打开时让它重启了。

**M12：**
- 面板：`python3 m12/panel/server.py`，打开 http://localhost:8765 。M12 走 WiFi 时，加上 `UNITV2_HOST=<ip>`。
- WiFi：`python3 m12/panel/wifi_setup.py`（追加热点，密码由用户在自己的终端输入）；`--order` 调整顺序；`--status` 看状态。
- **调试 M12 时，不要同时把它接在 StackChan 上。** StackChan 会按自己的策略反复给它切功能。

**主机测试：**

```sh
python3 -B -m unittest discover -s tests
c++ -std=c++17 tests/asr_protocol_test.cpp -o .cache/host-tests/asr && .cache/host-tests/asr
c++ -std=c++17 -I upstream/StackChan/firmware/components/ArduinoJson/src tests/m12_protocol_test.cpp -o .cache/host-tests/m12 && .cache/host-tests/m12
cmake --build .cache/display -j 8 && .cache/display/display_preview .cache/display-preview
```

## 7. 规则提醒

- 只写 `ota_1 / 0x510000`，每次独立校验。不写 bootloader、分区表、otadata、assets，不擦 NVS。
- `.env`、`logs/`、`artifacts/`、`backups/`、`.private/`、`.tools/` 不提交。
- 服务器地址、端口和令牌只放 `.env`，公开文档里用占位。
- 服务端（`server/ear/`，耳朵服务）由 Claude 负责；机身固件和 Android 由 codex 负责。
