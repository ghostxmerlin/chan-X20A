# StackChan 开发基线

核对日期：2026-10-30。下文保留原机与官方源码的历史基线；当前设备已运行独立采集固件。

**当前工程链路已完成自有采集固件的构建、写入校验与实际启动。** `collector-0.2.0` 在 `feat/collector-v0.1` 分支，复用官方驱动并替换业务入口；镜像 SHA-256 为 `eade96bb7d7ba90eea28a5db325861e77fe96c3c72e51af985a3fc110c7c1157`，只写 `ota_1` / `0x510000`。启动日志确认目标分区、版本与 ELF 摘要；实机三流 60 秒验收通过。完整结果见 [采集验证记录](collector-validation.md)。

用户最新澄清：出厂功能等价仅用于验证取得的源码、编译和下载/烧录流程，验证后弃用出厂业务功能。当前优先实现图像、声音及传感器数据到 Android 的实时观察与记录，情绪宝宝后置。本文保留出厂差异作为历史证据，不再以补齐官方云鉴权为后续开发前提。

公开源码的云端鉴权仍为已知占位差异；没有验证与原机所有功能等价，也没有把公开编译产物当作原机恢复镜像。原机完整备份保留。后续优先顺序见 [会话交接记录](session-handoff.md)。

以下“本次/尚未烧录”等描述均指原机基线建立阶段，不能代替上方的最新采集状态。

## 源码与构建结果

- 上游：[m5stack/StackChan](https://github.com/m5stack/StackChan)，本地目录 `upstream/StackChan/`。
- 固定提交：`f901cc2969189f3f0e96af1442c0f96e77e1e2c1`，应用版本 `1.6.0`；构建后上游 Git 工作区保持干净。
- 构建依据：[官方 firmware/README.md](https://github.com/m5stack/StackChan/blob/f901cc2969189f3f0e96af1442c0f96e77e1e2c1/firmware/README.md)。已执行 `fetch_repos.py`，包含官方对 xiaozhi 的补丁，保留 `dependencies.lock`。
- ESP-IDF：`v5.5.4`，SDK 提交 `735507283d5b2f9fb363a1901172dbd9e847945d`，目标 `esp32s3`。
- 完整构建通过（2496 个构建步骤）；仍有上游编译警告，未为消除警告修改源码。
- 应用大小：`3,730,352` 字节，应用分区剩余约 `28%`；镜像校验和及内部验证摘要有效。
- 上游主机测试 `motion_math_test`：`1/1` 通过；不代表舵机实机行为已验证。
- 构建产物分区表与从设备读取的分区表在名称、类型、子类型、偏移、大小和标志上完全一致。

后续从项目根目录构建：

```bash
./scripts/idf.sh build
```

脚本只在当前进程激活项目内工具链。没有修改 shell 启动文件或系统 Python。

产物已整理到 [`artifacts/stackchan-1.6.0-public-f901cc2/`](../artifacts/stackchan-1.6.0-public-f901cc2/)，包含应用、ELF、引导程序、分区表、资源、配置和依赖锁文件。`manifest.json` 记录来源、依赖提交、验证范围及文件摘要，`SHA256SUMS` 用于校验。

应用文件：[`stack-chan.bin`](../artifacts/stackchan-1.6.0-public-f901cc2/stack-chan.bin)。SHA-256：

```text
0e9a6b997231c2dbf03aa50e03bf77e3575a32f5cce4d1715d53b096105c7d00
```

构建原始日志：`logs/firmware-build.log`；测试日志：`logs/host-tests.log`；镜像解析：`logs/built-app-image-info.txt`。

## 设备与原机备份

用户通过机身侧面 USB-C 连接设备。本次实际确认 StackChan 位于 `/dev/cu.usbmodem1301`：

| 项目 | 实际读取结果 |
| --- | --- |
| 芯片 | ESP32-S3 QFN56，revision v0.2，40 MHz 晶振 |
| 内存 | 16 MB Flash，8 MB PSRAM |
| 应用 / SDK | `stack-chan` 1.6.0 / ESP-IDF v5.5.4 |
| 原机应用编译时间 | 2026-10-19 17:19:01（镜像内字段） |
| 当前活动分区 | `ota_1`，偏移 `0x510000` |
| 其他启动信息 | ILI9342C 屏幕，摄像头初始化成功，头部、灯光和提醒 MCP 工具注册成功 |

本次还发现 `/dev/cu.usbmodem3101` 对应另一台 StickS3，应用为 `sticks3_starter`。后续不要仅按端口顺序选择设备；USB 端口名称也可能变化。套装 SKU 仍依据用户描述暂记 K151-R，未读取实物标签。

完整 16 MB Flash 备份保存在：

```text
backups/stackchan-original-1.6.0-20261030.bin
SHA-256: 4acf656bf5fac20613abbd0e5eca32a4f9d60d679875882166d25cf3deebe1db
```

已使用设备端摘要校验分区表（`0x8000`，4096 字节）和活动应用分区（`0x510000`，5,177,344 字节），两者均匹配备份。其余区域完成读取并纳入本地完整文件摘要，未逐分区再次进行设备端比对。备份包含设备配置和可能的凭据，仅保存在本机，已排除版本控制。读取及校验期间设备重启；未执行烧录、擦除或 eFuse 修改。

设备分区布局：

| 名称 | 偏移 | 大小 |
| --- | --- | --- |
| nvs | `0x9000` | `0x4000` |
| otadata | `0xd000` | `0x2000` |
| phy_init | `0xf000` | `0x1000` |
| ota_0 | `0x20000` | `0x4f0000` |
| ota_1 | `0x510000` | `0x4f0000` |
| assets | `0xa00000` | `0x400000` |
| coredump | `0xe00000` | `0x10000` |

## 为什么还不能认定与出厂效果相同

版本号、SDK 和分区布局一致，只能确认构建基线相符，不能证明所有运行行为一致。本次发现了具体差异：

1. 公开源码 [`secret_logic.cpp`](https://github.com/m5stack/StackChan/blob/f901cc2969189f3f0e96af1442c0f96e77e1e2c1/firmware/main/hal/utils/secret_logic/secret_logic.cpp) 中 `generate_auth_token()` 和 `generate_handshake_token()` 都是弱符号占位实现，返回固定字符串 `hi-stack-chan`。
2. 本次生成的 ELF 确实链接了这些弱符号，生成的应用包含该字符串；从原机提取的活动应用不包含该字符串。这是鉴权实现不同的证据，不能用“只是编译时间不同”来解释。
3. 官方公开的 [WebSocket 服务端校验逻辑](https://github.com/m5stack/StackChan/blob/f901cc2969189f3f0e96af1442c0f96e77e1e2c1/server/internal/web_socket/web_socket.go) 要求合法 Base64、RSA 解密后的字段及有效时间戳。占位字符串不满足该协议。因此按公开源码判断，默认构建不能直接通过该服务端的鉴权；没有对生产服务器进行登录测试。

此处说明公开源码与原机的已知差异；当前不再继续追查正式鉴权或搭建整套出厂服务。后续独立采集应用按自身需求验证，不以官方云等价性为目标。

本次未将自编译镜像刷入设备，表情、触摸、声音、摄像头、舵机、灯光、遥控器、配网、AI 对话及云端/App 连接的运行等价性均未验收。原机备份保留了本次读取时的状态，但尚未做恢复演练。

### 重启后的鉴权影响核对（2026-10-30）

本次只读核对同一固定提交的固件、App 和服务端，未连接生产服务，也未操作设备。以下是源码层面的依赖判断，不是实机结果。

| 功能链路 | 已确认的依赖与影响 | 固定源码中的主要证据 |
| --- | --- | --- |
| 远程 Avatar、账户和设备信息 | 使用 `generate_auth_token()`。占位值不能通过公开 WebSocket 服务端鉴权；REST 接口通常因缺少有效 MAC 返回业务错误，不能统一描述为 HTTP 401 | `firmware/main/hal/hal_ws_avatar.cpp:129`、`hal_account.cpp:167`；`server/internal/web_socket/web_socket.go:79`、`server/internal/middleware/middleware.go:28` |
| 手机 App 蓝牙验证/绑定 | 固件将 `generate_handshake_token()` 结果发回 App；App 必须先成功进行 Base64 / RSA-OAEP-SHA256 解密，才能继续绑定。占位值不符合协议。BLE 接收 Wi-Fi 配置是另一条路径，不能说蓝牙全部不可用 | `firmware/main/hal/hal_ble.cpp:530`；`app/lib/view/popup/select_blue_device.dart:351`、`app/lib/util/rsa_util.dart:34` |
| 手机 App 局域网 Avatar | 同样使用 `generate_handshake_token("stackchan-lan-v1")`。App 解密后核对字段和目标 MAC；占位值不能通过，因此不能假定绕开云端就能正常连接 App | `firmware/main/hal/hal_ws_avatar.cpp:90`；`app/lib/network/local_area_network_service.dart:373` |
| ESP-NOW 遥控、本地外设 | 所查遥控收发及本地硬件初始化链没有调用上述两个 token 函数，应独立验证 | `firmware/main/apps/app_espnow_ctrl/app_espnow_ctrl.cpp:64`、`:162`；`firmware/main/hal/hal_espnow.cpp:107`、`hal.cpp:35` |
| 小智 AI | OTA 地址、激活和 WebSocket token 使用独立配置；不能只凭 StackChan token 占位断言 AI 对话必定失效，也不能保证备份中的现有配置能让自编译固件正常工作 | `firmware/xiaozhi-esp32/main/ota.cc:46`；`firmware/xiaozhi-esp32/main/protocols/websocket_protocol.cc:83` |
| App Center | 列表请求为同一服务地址下的 `/stackChan/apps`，未附这两个 token；仍需验证服务器可达性和实际下载 | `firmware/main/hal/hal_app_center.cpp:18`、`:33` |

以上路径均相对 `upstream/StackChan/`。正式鉴权实现尚未取得；在本次查阅的固定源码、构建说明和官方产品文档中，未找到向开发者提供出厂鉴权实现的获取说明。这个结论不表示官方没有其他支持渠道。

自建部署已有部分入口：

- `firmware/main/Kconfig.projbuild:3` 定义 `CONFIG_STACKCHAN_SERVER_URL`，`firmware/CMakeLists.txt:11` 自动读取被 Git 忽略的 `sdkconfig.defaults.local`。已有 `sdkconfig` 时还需核对最终生效配置，不能认为创建 defaults 文件就覆盖了既有值。
- [公开服务端说明](https://github.com/m5stack/StackChan/blob/f901cc2969189f3f0e96af1442c0f96e77e1e2c1/server/README.MD#L611) 描述了 `<mac>|<nonce>|<unix_timestamp_seconds>` 经 RSA-OAEP-SHA256 加密、Base64 编码的 token 协议；还提供自建密钥配置说明。App README 也有自建地址和密钥配置入口。
- 两个 token 函数是弱符号，同签名强实现可覆盖；这是源码可扩展点，**不是已经提供的正式鉴权组件**。仅改服务 URL 不会补齐 token 实现。自建完整体验还涉及 App、账户系统和小智服务配置，不能视为官方云的等价替代已完成。

先前整理的官方支持问题包括：是否提供自编译鉴权组件、如何接入固定 1.6.0 / IDF 5.5.4 工程，以及 BLE 与 LAN 握手是否一并支持。随着用户明确后续弃用出厂业务功能，这些问题不再是待办；未发送支持请求。

### 出厂功能对照表（历史计划，尚未执行）

下表保留先前对照计划，已不再作为必须全部完成的验收门槛。后续以烧录/启动正确性、本地外设及 Android 采集链路为重点，按实际需要采用其中的设备核对与硬件操作。每项记录固件来源、版本、操作、预期、实际观察和是否通过。

| 项目 | 操作与预期 | 当前结果 |
| --- | --- | --- |
| 实物与配置基线 | 核对机身/遥控器 SKU、硬件及固件版本、遥控信道和目标 ID；保留当前配网、绑定及校准 | 待实机确认 |
| 本地交互 | 测试菜单触摸、表情、麦克风/扬声器、RGB 灯；分别记录响应、声音和颜色是否正常 | 待执行 |
| 舵机与 ESP-NOW | 先核对驱动角度坐标系和校准；使用低速、小幅摇杆及 IMU 输入，确认方向、回中和停止；收发同信道、目标 ID 对应 | 待执行；未据官方 5–85°直接换算代码角度 |
| 手机 App | 分别记录现有绑定下的远程 Avatar、同网 LAN、相机、动作及蓝牙功能；不为测试主动解绑；新绑定验证另行安排 | 待执行 |
| 小智 AI 与下载 | 单独验证唤醒、录音、回答、播放和应用下载；进入 AI Agent 前注意其会检查并可能自动安装更新，避免原机版本基线意外改变 | 待执行 |

验收功能和自动更新行为参照 [M5Stack 官方整机说明](https://docs.m5stack.com/en/StackChan)。目前未烧录自编译镜像、未校准或驱动舵机，原机仍保留此前固件。

## 本机开发环境

| 项目 | 当前状态 |
| --- | --- |
| 操作系统 / 架构 | macOS 26.5.1（25F80），Apple Silicon arm64 |
| 内存 / 可用磁盘 | 16 GiB；工具链安装后约 49 GiB 可用 |
| Git / Clang | Apple Git 2.39.5 / Apple Clang 17.0.0；Xcode Command Line Tools 已有 |
| Python | 系统 3.9.6；IDF 和主机串口工具各使用独立虚拟环境 |
| Node.js / npm | 24.14.0 / 11.9.0；当前固件构建不依赖它们 |
| ESP-IDF | 项目内 `.tools/esp-idf-v5.5.4/` |
| ESP 工具及 IDF Python | 项目内 `.tools/espressif/` |
| Xtensa GCC | 14.2.0，`esp-14.2.0_20260121` |
| CMake / Ninja / esptool | 3.30.2 / 1.12.1 / 4.12.0 |
| PlatformIO / Arduino IDE / M5Burner | 本次检查未发现安装；当前构建不需要 |

主机测试入口（在项目根目录运行）：

```bash
export IDF_PATH="$PWD/.tools/esp-idf-v5.5.4"
export IDF_TOOLS_PATH="$PWD/.tools/espressif"
source "$IDF_PATH/export.sh"
cmake -S upstream/StackChan/firmware/tests -B .cache/host-tests
cmake --build .cache/host-tests
ctest --test-dir .cache/host-tests --output-on-failure
```

外部仓库版本由 `firmware/repos.json` 固定：mooncake 2.3.3、mooncake_log 1.5.0、smooth_ui_toolkit 2.12.0、xiaozhi-esp32 2.2.4（附官方补丁）、ArduinoJson 7.4.2、esp-now 提交 `c33383de97f3ed2fc52117f5ef04f71990432e5d`。托管组件继续使用上游锁文件，不因发现新版本而自动升级。

开发辅助工具见 [ESP32 / StackChan 开发辅助工具](development-tools.md)。
