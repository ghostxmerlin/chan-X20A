# 从源码构建

以下命令针对新检出的源码，不执行刷机。现有开发目录已具备工具链时，无需重新下载。固定 ESP-IDF 5.5.4，保留仓库中的依赖版本。

0.4.7 新增固定 `human_face_detect 0.4.1`、`esp-dl 3.3.0`，原有组件版本及哈希不变。MSR/MNP模型嵌入应用rodata；仅启用ESP-DL所需RGB888转换以适配现有应用分区。`cmake/face_dl_symbols.cmake`隔离新ESP-DL与原ESP-SR 2.3.1重复的48个底层函数，避免视觉推理误用语音库实现；构建自动运行`check-face-link.py`，碰撞、跨库引用或隔离数量变化时失败。不能只调整库链接顺序，也不要跳过该检查。下载组件源码和原语音库未改。

## 固件

先按 ESP-IDF 5.5.4 的平台要求安装 Git、Python、CMake/Ninja 等基础工具，然后在仓库根目录准备项目内工具链：

```sh
git clone https://github.com/ghostxmerlin/chan-X20A.git
cd chan-X20A
mkdir -p .tools
git clone --recursive --branch v5.5.4 --depth 1 https://github.com/espressif/esp-idf.git .tools/esp-idf-v5.5.4
export IDF_TOOLS_PATH="$PWD/.tools/espressif"
.tools/esp-idf-v5.5.4/install.sh esp32s3
python3 upstream/StackChan/firmware/fetch_repos.py
cp config/collector-0.3.4.sdkconfig upstream/StackChan/firmware/sdkconfig
./scripts/idf.sh build
```

`config/collector-0.3.4.sdkconfig` 同时用于 0.3.4 至 0.4.0，继续复用该配置，不含 Wi-Fi 密码或设备 token。`scripts/idf.sh` 仅在当前进程载入 `.tools/esp-idf-v5.5.4`，不修改系统 shell 配置。依赖补丁由官方 `fetch_repos.py` 应用。

产物位于 `upstream/StackChan/firmware/build/`。构建完成不等于设备验收通过；本版本的已知舵机回传问题见 [验证记录](collector-validation.md)。

刷写需先核对芯片、Flash、设备身份与活动分区。开发设备仅写活动 app 区域并独立校验；不要直接执行默认全量 flash 命令覆盖其他设备的 NVS、分区、资产或原厂校准。本文不自动选择 USB 设备。

### 0.4.0 自动连接与角度设置

沿用固定工具链和锁定依赖，使用现有 lwIP UDP 与 mbedTLS 实现已绑定设备发现，未新增 mDNS 依赖。HTTP 8080 增加公开挑战验证和鉴权角度设置，局域网发现使用 UDP 8082；不开放配对凭据读取。共享配置 `config/collector-0.3.4.sdkconfig` 保留历史文件名，本轮将 `CONFIG_LWIP_MAX_SOCKETS` 从 10 调到 16，并同步 defaults；实际生成的 `build/config/sdkconfig.h` 也必须为 16，以覆盖两个 HTTP 实例、客户端和发现任务。Android 构建后需保留或迁移已有私有绑定，公开启动 Intent 不再接受地址/令牌覆盖。协议、开发绑定和部署验收分别见协议说明、Android README 和最新验证记录。

统一 Python 测试新增 `test_connection_protocol.py` 与俯仰设置测试，覆盖请求边界、持久化失败、停止代际、真实 Servo/spring 轨迹与下沿恢复；编译和主机验证不能代替实际运动及并发采集验收。

### 0.3.9 中心键开关屏

本轮已部署 0.3.9，将中心按压绑定到显示开关，遥控器已部署的中心协议保持不变，无需重刷。机器人默认亮屏、仅 RAM 保存开关状态，不写 NVS；关屏仅门控 PMIC 背光并暂停该显示的重绘，保留 20 ms 输入处理和摄像头、麦克风、IMU、左右遥控任务。开屏恢复此前页面、亮度和最新摘要/日志，关屏触摸不唤醒或导航。`display.enabled` 为软件状态，不是物理亮度测量。

17 项 Python/主机测试（含中心首包/断线基线、序号回绕/重复/乱序、多按压奇偶）通过；真实 LVGL 测试覆盖关屏 5 秒零刷新、其他定时器继续、四页恢复、临时字符串拷贝、快速开关、触摸释放和旧表情丢弃。新增入口 `tests/test_remote_gestures.py` 随统一测试运行。Android 0.3.9/code9 构建及原生记录/渲染检查通过。固件已核对设备身份/分区，仅写应用并独立校验；用户确认“开关正常，页面正常”。37.193 秒录制验证通过：260 JPEG 全解码、1,835 音频块和 1,826 IMU 无序号缺口，采集错误及队列丢弃增量均 0；只记录一轮关/开，长按及快速多轮仍仅为主机测试。具体产物与实机数据见验证记录。

### 0.3.8 短片播放

本轮新增机器人扬声器的显式上传播放入口，已构建、部署并独立校验。沿用固定 IDF 5.5.4、现有音频驱动和构建入口，不升级依赖；原有麦克风采集、图像、IMU 与遥控行为保留；0.3.8 当时中心仅记录，0.3.9 的新绑定见上节。机器人镜像 3,492,336 字节，SHA256 `268e57790c2ea08490482114e294ec49e1b5e8bbeefa11e44ea563e18694f452`；核验设备及分区 metadata 后仅写 `ota_1 / 0x510000`，独立 verify 通过。

`POST /v1/playback` 使用 Bearer 配对码，客户端发送 `application/octet-stream`，服务端不强校验 Content-Type；内容固定为无容器头的 24 kHz / mono / s16le，偶数 2–384,000 字节（最多 8 秒）。完整上传到 PSRAM 并通过截止检查后才返回 `202`、开始单 worker 播放；上传/播放/清理全程拒绝并发任务（`409`）。上传截止 10 秒，单次接收阻塞最多 3 秒，实际关闭可能到约 13 秒再加调度时间。`202` 与播放完成分开记录，不完整上传或接受响应失败不启用输出；具体状态与错误字段见 [协议](collector-protocol.md)。

输出音量初始为 20，仅在 RAM 使用，不调用持久化音量设置，也不保存媒体。输出复用现有 I²S，不能为播放重配采集时钟或格式；开机保持功放复位，尝试启用输出后，完成或失败均尝试重新复位并记录清理错误。`output_enabled` 是 codec 运行状态，`hardware_reset_held` 为实际寄存器读回、失败时为 `null`；不能用主机构建、提交字节数或 `output_enabled=false` 代替听感和硬件状态。

16 项主机测试通过。实机 4 秒官方日语样本 job 1 完整接收并提交 192,000 字节，实际 TX 配置检查通过，终态 `completed` / `error=0` / `cleanup_error=0`，结束后读回 `hardware_reset_held=true`、`output_enabled=false`。未鉴权请求返回 401，空 body 与奇数字节返回 400，拒绝未改变 job 或启用输出。

21.357 秒录制通过 `verify-session.py`：160 JPEG 全解码 / 7.577 fps，1,051 音频块和 1,050 IMU / 50.013 Hz 无序列缺口，写盘丢弃、相机/音频错误与音频跳块增量为 0；其中 4.059 秒输出启用窗口持续收到 33 JPEG，203 音频块与 203 IMU 序号连续。8 处音频时间戳抖动超过 5 ms，听感仍待用户确认，不据此宣称无杂音或音量合适。两轴反馈仅 10/147 有效且播放前已大量无效，旧舵机回传根因未解决。上传中断、并发拒绝等其他硬件路径不以主机测试冒充实测。

已授权的官方日语试听样本仅保存在本机，不作为构建资源或测试素材提交。试听时观察台监听临时关闭以避免回声；现已停止录制、恢复监听开启，转头请求保持。记录、观察台监听和机器人扬声器播放是独立路径。

## Android 观察台

安装 Android SDK Platform 35、Build Tools 35.0.0、Platform Tools 和 JDK 17。可显式指定路径：

```sh
export ANDROID_SDK_ROOT="/path/to/android-sdk"
export COLLECTOR_JAVA_HOME="/path/to/jdk-17"
bash android/Collector/test-host
bash android/Collector/build-app
```

macOS 默认 SDK 路径为 `$HOME/Library/Android/sdk`；Java 优先使用 `COLLECTOR_JAVA_HOME`，其次 `JAVA_HOME`，最后 `$HOME/.local/jdks/temurin-17`。脚本直接使用 Android SDK，不需要 Gradle。

APK 输出到 `android/Collector/build/outputs/apk/debug/StackChan-Collector-debug.apk`。首次构建在本地生成调试签名，私钥位于被忽略的 `.private/`；他人构建不会拥有原开发签名，安装到已有不同签名的 App 上需自行处理签名兼容。原始记录应先导出保留。

## 遥控器中心按压输入固件

原厂 Mini JoyC 中心键未进入遥控包。中心按压扩展使用固定 ESP-IDF 5.5.4，在隔离目录构建 ESP32 遥控器程序；上游遥控工程锁文件中记载的 5.4.2 保留为原始基线，不直接改写：

```sh
bash scripts/build-remote.sh
```

入口从 `remote/code/dependencies.lock` 锁定全部组件版本和哈希，复制源码到 `.cache/remote-center-v1/`，原锁文件、配置和 manifest 不修改。按原厂 README，在隔离的 M5GFX 源码中启用旧 I²C 后端；验证新旧驱动冲突检查保留、新驱动未混入。构建记录在本机 `logs/remote-center-v1/`。此脚本只构建，不执行刷机；本轮产物已在另行完成身份、备份和分区核对后部署到遥控器。ESP32 产物不能用于 ESP32-S3 或未经身份核对的其他遥控器。

扩展读取 Mini JoyC `0x54 / 0x30` 中心按钮，将启动 nonce 与按压序号追加到原 8 字节包，发送 16 字节 `joystick_center_v1` 数据；B 键字段保留为独立原始观察值。开机/切换模式时按住不会计为新按压，约 200 ms 心跳保留原角度死区。接收端记录中心协议与按压序号，当前不绑定动作：`center_press_supported=true` 表示协议能力，`nod_trigger=none` 表示无点头触发。寄存器依据见 [官方协议](https://m5stack-doc.oss-cn-shenzhen.aliyuncs.com/610/HAT_MiniJoyC.pdf)与[官方读取实现](https://github.com/m5stack/M5Hat-Mini-JoyC/blob/main/src/M5HatMiniJoyC.cpp)。

本轮实物已识别为 ESP32-PICO-D4 / 4 MB，完整 4 MB 备份通过独立校验；应用区按实读分区表为 `factory / 0x10000`，仅写应用并独立校验。首次启动前的整片读回确认应用写入范围以外字节原样，原 bootloader、分区表和 NVS 不由刷机写入。运行时 PHY 校准等可能更新 NVS，不能表述为 NVS 从未变化；遥控器应用在 NVS 初始化失败时停止启动，不执行自动擦除。机器人的 `ota_1 / 0x510000` 不适用于遥控器；后续操作仍须重新核对目标芯片、Flash 和分区。

真实链路已收到中心扩展包。点头试验先采用速度 250 / 换向间隔 700 ms，再调整为 450 / 400 ms 并处理有效反馈略低于下限时的恢复；用户仍认为偏慢、幅度小，明确放弃中心点头。`artifacts/collector-0.3.7-nod-fast/` 属于已弃用的试验产物，不再等待验收或作为当前部署目标。中心未绑定版保留为历史，当前部署记录以 [验证记录](collector-validation.md) 为准。

## 主机验证

在仓库根目录运行：

```sh
python3 -B -m unittest discover -s tests -v
mkdir -p .cache/host-tests
c++ -std=c++17 tests/remote_protocol_test.cpp -o .cache/host-tests/remote_protocol_test
.cache/host-tests/remote_protocol_test
c++ -std=c++17 tests/servo_transport_test.cpp upstream/StackChan/firmware/main/hal/drivers/FTServo_Arduino/src/SCS.cpp -o .cache/host-tests/servo_transport_test
.cache/host-tests/servo_transport_test
cc -std=c11 -Wall -Wextra -Werror tests/remote_center_button_test.c -o .cache/host-tests/remote_center_button_test
.cache/host-tests/remote_center_button_test
c++ -std=c++17 -Wall -Wextra -Werror -Wno-unused-parameter \
  tests/remote_gestures_test.cpp \
  upstream/StackChan/firmware/components/smooth_ui_toolkit/src/core/animation/generators/spring/spring.cpp \
  -o .cache/host-tests/remote_gestures_test
.cache/host-tests/remote_gestures_test
c++ -std=c++17 -pthread -DCOLLECTOR_RUNTIME_LOG_TEST \
  -I upstream/StackChan/firmware/main tests/runtime_log_test.cpp \
  upstream/StackChan/firmware/main/collector/runtime_log.cpp \
  -o .cache/host-tests/runtime_log_test
.cache/host-tests/runtime_log_test
bash android/Collector/test-host
```

播放回归入口为 `tests/test_playback_session.py`，它编译并运行 `tests/playback_session_test.cpp`，直接覆盖生产上传门控与播放执行器，包括长度边界、不完整上传不启用、PCM 块与静音尾部、逐次写失败和清理失败，以及 20 轮、每轮 16 个并发请求竞争单任务。使用模拟输出，不连接设备。可单独运行：

```sh
python3 -B -m unittest discover -s tests -p 'test_playback_session.py' -v
```

另有官方运动数学测试：

```sh
cmake -S upstream/StackChan/firmware/tests -B .cache/motion-tests
cmake --build .cache/motion-tests
ctest --test-dir .cache/motion-tests --output-on-failure
```

Android 原生运行时测试见 `android/Collector/test-android-runtime`，需已连接模拟器或测试设备。实际遥控、机械动作与声音仍须实机验证，不能用主机模拟测试代替。

### 机身屏幕与启动姿态

屏幕采用 LVGL；纯黑底 `#000000`、窗口栏 `#0C1C12`、分隔线 `#244C32`、辅助文字 `#72A980` 和主色 `#79FF97`，搭配 JetBrains Mono 15 px。320×240 首页居中显示 260×240 笑面男，去掉底部提示；可见外圈高度约 230 px，文字环独立逆时针旋转（12 秒一圈，120 帧、最多 10 次刷新/秒），脸与帽子不动，进入信息页暂停动画。0.3.7 将信息拆成摘要、真实日志和独立配对详情；轻触先开摘要，左滑看日志，摘要长按查看配对信息。图形和字库使用现有生成资源或 LVGL 原生绘制，普通固件编译不需要 Node/Pillow。

动画在生成资源时完成旋转与合成；固件保存一张不透明 RGB565 首帧、7,478 个变化像素的坐标、32 色调色板和 897,360 字节帧数据。运行时只分配一份 124,800 字节 PSRAM 图像缓冲，按时间选帧并更新变化像素，不逐帧做旋转、透明混合或解压；分配失败时保留静态首页。刷新限制在文字圈的 25 个矩形内，每帧实际刷新 12,872 像素，比整张图的 62,400 像素减少约 79%；主机 LVGL 部分刷新测试对此设有上限检查。原先实时旋转透明图层在实机上使 JPEG 编码延迟到秒级，已撤换，不能仅凭主机预览判断 S3 性能。 采集模式的 LVGL 固定 core 0，JPEG 仍在 core 1；保留原任务优先级和原厂模式的 core 1 设置。仅降低显示优先级会拖慢动画，已排除该方案。音频发送任务等候新块时至少阻塞一个 RTOS tick：当前 100 Hz 下 `pdMS_TO_TICKS(5)` 为 0，旧代码实际空转，实测占用单核约 76–78% 的 CPU 时间。采样率、音频块大小和协议保持不变。

0.4.8将爱心/叉号的逐行填充在启动时一次缓存为RGB565，两个168×154图像共占103488字节PSRAM；分配失败才回退向量绘制。出现/消失只更新260×240的肖像范围，保留两侧黑边。`CollectorScreen`记录输入等待、声音排队及切换渲染提交耗时；提交完成不等于最后一块DMA完成或实际亮屏测量。

使用同一份 LVGL 屏幕代码离线渲染首页、摘要、日志、配对详情及表情，验证轻触、滑动、长按和 1000 ms 生命周期：

```sh
cmake -S tests/display -B .cache/display -DCMAKE_BUILD_TYPE=Debug
cmake --build .cache/display -j 8
mkdir -p .cache/display-preview
.cache/display/display_preview .cache/display-preview
.cache/display/reaction_benchmark
c++ -std=c++17 tests/startup_pose_test.cpp -o .cache/startup_pose_test
.cache/startup_pose_test
```

预览输出 PPM（含完整文字环动画周期），使用合成 IP/配对码。启动姿态测试覆盖等待有效回传、仅水平轴遥控与启动俯仰互不覆盖、停用再启用、超时跳过与一次启动仅执行一次；穷举 65,536 种俯仰输入，验证它们只保留为数据、不能驱动俯仰轴；实际机械到位仍以实机反馈为准。

需要修改图形/字体时，在本地安装可选生成工具，并准备 Pillow：

```sh
npm install --prefix .tools/ui-assets --no-audit --no-fund @resvg/resvg-js@2.6.2 lv_font_conv@1.5.3
curl -fL https://raw.githubusercontent.com/JetBrains/JetBrainsMono/v2.304/fonts/ttf/JetBrainsMono-Regular.ttf -o .tools/ui-assets/JetBrainsMono-Regular.ttf
python3 scripts/generate-screen-assets.py
```

生成器只修改 collector 内两份 C 资源文件及动画元数据头文件 `laughing_man.h`；来源和许可见根目录 `NOTICE.md`。

0.3.7 的界面与协议检查还包括真实日志固定环（非持久存储）、8/16 字节包、中心按压消抖/模式切换按住抑制、心跳保持原角度死区和中心输入不绑定动作。点头试验已经弃用，不再作为待交付功能。Android 区分中心协议支持与动作绑定，0.3.9 显示“中心开关屏”，连接未绑定固件仍显示“中心仅记录”；旧固件明确绑定点头时才显示“中心点头”。旧 8 字节遥控器只能提供左右轴和上下表情，不能由 B 键推断中心按压。

0.3.7 实心无圈表情与图形摘要版已构建、部署并独立校验；最终产物与实机结果以 [验证记录](collector-validation.md) 最新段为准，不能将主机预览或首候选帧率直接作为后续候选的验收结果。
