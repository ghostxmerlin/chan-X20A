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

`config/collector-0.3.4.sdkconfig` 与已部署构建的配置一致，不含 Wi-Fi 密码或设备 token。`scripts/idf.sh` 仅在当前进程载入 `.tools/esp-idf-v5.5.4`，不修改系统 shell 配置。依赖补丁由官方 `fetch_repos.py` 应用。

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
