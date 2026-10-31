# 从源码构建

以下命令针对新检出的源码，不执行刷机。现有开发目录已具备工具链时，无需重新下载。固定 ESP-IDF 5.5.4，保留仓库中的依赖版本。

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

`config/collector-0.3.4.sdkconfig` 同时用于 0.3.4 / 0.3.5 / 0.3.6，三版配置完全一致，不含 Wi-Fi 密码或设备 token。`scripts/idf.sh` 仅在当前进程载入 `.tools/esp-idf-v5.5.4`，不修改系统 shell 配置。依赖补丁由官方 `fetch_repos.py` 应用。

产物位于 `upstream/StackChan/firmware/build/`。构建完成不等于设备验收通过；本版本的已知舵机回传问题见 [验证记录](collector-validation.md)。

刷写需先核对芯片、Flash、设备身份与活动分区。开发设备仅写活动 app 区域并独立校验；不要直接执行默认全量 flash 命令覆盖其他设备的 NVS、分区、资产或原厂校准。本文不自动选择 USB 设备。

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

## 主机验证

在仓库根目录运行：

```sh
python3 -B -m unittest discover -s tests -v
mkdir -p .cache/host-tests
c++ -std=c++17 tests/remote_protocol_test.cpp -o .cache/host-tests/remote_protocol_test
.cache/host-tests/remote_protocol_test
c++ -std=c++17 tests/servo_transport_test.cpp upstream/StackChan/firmware/main/hal/drivers/FTServo_Arduino/src/SCS.cpp -o .cache/host-tests/servo_transport_test
.cache/host-tests/servo_transport_test
bash android/Collector/test-host
```

另有官方运动数学测试：

```sh
cmake -S upstream/StackChan/firmware/tests -B .cache/motion-tests
cmake --build .cache/motion-tests
ctest --test-dir .cache/motion-tests --output-on-failure
```

Android 原生运行时测试见 `android/Collector/test-android-runtime`，需已连接模拟器或测试设备。实际遥控、机械动作与声音仍须实机验证，不能用主机模拟测试代替。

### 机身屏幕与启动姿态

屏幕采用 LVGL；纯黑底 `#000000`、窗口栏 `#0C1C12`、分隔线 `#244C32`、辅助文字 `#72A980` 和主色 `#79FF97`，搭配 JetBrains Mono 15 px。320×240 首页居中显示 260×240 笑面男，去掉底部提示；可见外圈高度约 230 px，文字环独立逆时针旋转（12 秒一圈，120 帧、最多 10 次刷新/秒），脸与帽子不动，进入信息页暂停动画；信息页使用 28 px 窗口栏、等宽状态行及两行配对码。图形和字库已生成并提交，普通固件编译不需要 Node/Pillow。

动画在生成资源时完成旋转与合成；固件保存一张不透明 RGB565 首帧、7,478 个变化像素的坐标、32 色调色板和 897,360 字节帧数据。运行时只分配一份 124,800 字节 PSRAM 图像缓冲，按时间选帧并更新变化像素，不逐帧做旋转、透明混合或解压；分配失败时保留静态首页。刷新限制在文字圈的 25 个矩形内，每帧实际刷新 12,872 像素，比整张图的 62,400 像素减少约 79%；主机 LVGL 部分刷新测试对此设有上限检查。原先实时旋转透明图层在实机上使 JPEG 编码延迟到秒级，已撤换，不能仅凭主机预览判断 S3 性能。 采集模式的 LVGL 固定 core 0，JPEG 仍在 core 1；保留原任务优先级和原厂模式的 core 1 设置。仅降低显示优先级会拖慢动画，已排除该方案。音频发送任务等候新块时至少阻塞一个 RTOS tick：当前 100 Hz 下 `pdMS_TO_TICKS(5)` 为 0，旧代码实际空转，实测占用单核约 76–78% 的 CPU 时间。采样率、音频块大小和协议保持不变。

使用同一份 LVGL 屏幕代码离线渲染两页并验证轻触/长按行为：

```sh
cmake -S tests/display -B .cache/display -DCMAKE_BUILD_TYPE=Debug
cmake --build .cache/display -j 8
mkdir -p .cache/display-preview
.cache/display/display_preview .cache/display-preview
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
