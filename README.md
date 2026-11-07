# chan-X20A

基于 M5Stack StackChan（CoreS3 + 原装底座）的本地采集与遥控项目。将摄像头、麦克风、六轴和官方遥控数据实时传到 Android，支持观察、录制、标记、回放和导出；不依赖出厂账号或云服务。

本仓库 fork 自 [m5stack/StackChan](https://github.com/m5stack/StackChan)，开发起点为 [`f901cc2`](https://github.com/m5stack/StackChan/commit/f901cc2969189f3f0e96af1442c0f96e77e1e2c1)。保留上游提交历史，官方源码统一放在 `upstream/StackChan/`。

## 当前状态

截至 2026-10-31，开发设备已刷入 **collector-0.3.6**，Android 观察台已安装 **0.3.6 / code 7**。

- 机身开机显示纯黑背景、放大的绿色笑面男，去掉底部提示，文字圈每 12 秒逆时针转一圈；轻触打开绿色终端信息页，再点返回。
- 开机保留一次 10% 行程（13°）俯仰；此后官方摇杆、姿态控制均只驱动左右转头，上下输入仅记录并留给后续功能。
- 320×240 JPEG 预览；本轮 82.5 秒并发录制实收 7.35 fps，目标 10 fps 尚未达到。
- 24 kHz PCM 音频采集与观察台监听；机器人扬声器没有播放业务。
- 加速度、角速度三轴曲线与原始样本记录，目标采样 50 Hz；触摸、电量和链路状态展示。
- 官方 ESP-NOW 输入、已执行水平目标与位置反馈分开记录，未执行的俯仰目标留空；遥控接收独立于观察台连接。
- 已修正一次位置采集失败就关闭转头的状态错误；开机就绪、UI 停止再启用的状态保持已实测。

用户已实机确认“文字旋转正常，只有左右转头”。**此前舵机位置回传损坏的根因仍未解决，不能把本轮功能通过等同于恢复原厂稳定性。** App 仅支持前台采集，尚无后台/锁屏记录、SD 断网补录或肩上长时运行验收。

详见 [当前状态快照](docs/current-state.json)、[验证记录](docs/collector-validation.md)和[交接记录](docs/session-handoff.md)。

## 目录

| 路径 | 内容 |
| --- | --- |
| `upstream/StackChan/firmware/` | 基于官方源码的 ESP32-S3 采集固件 |
| `upstream/StackChan/remote/` | 官方遥控器源码，保留作协议参考 |
| `upstream/StackChan/app/`、`server/` | 上游手机端与服务端参考源码，不是当前采集入口 |
| `android/Collector/` | 独立 Android 观察台，原生 Java |
| `config/` | 当前版本 ESP-IDF 构建配置 |
| `assets/soul-eyes/v2-front/` | 三款正面眼睛资源，90 段动画；尚未接入 collector 首页 |
| `scripts/`、`tests/` | 构建入口、主机协议验证与联调工具 |
| `docs/` | 使用、协议、开发与验收记录 |

`upstream/StackChan/` 是仓库中的普通源码目录，无需初始化 submodule。构建依赖由其 `firmware/repos.json` 和 `dependencies.lock` 固定。

## 构建与使用

固定使用 **ESP-IDF 5.5.4**；Android 使用 **SDK 35 / Build Tools 35.0.0 / JDK 17**。新检出的工具链准备步骤见 [构建说明](docs/build.md)。环境就绪后，在仓库根目录运行：

```sh
./scripts/idf.sh build
bash android/Collector/test-host
bash android/Collector/build-app
python3 -B -m unittest discover -s tests -v
```

设备连接、记录和数据导出见 [使用说明](docs/collector-guide.md)，通信格式见 [协议](docs/collector-protocol.md)。部署前需核对设备与活动分区；项目内的开发设备偏移不能直接用于其他机器。

三款正面眼睛的预览、状态清单、SD 文件布局和 ESP-IDF／LVGL 接入约束见 [眼睛资源使用说明](docs/soul-eyes.md)。

仓库只发布源码、配置与整理后的验证结果。凭据、签名私钥、原机备份、原始串口日志、现场图像/录音和本机构建产物保留在本地，不随 Git 上传。历史文档中的 `logs/`、`artifacts/` 链接是本地证据路径，不是公开下载链接。

## 来源与许可

新增采集代码按 [MIT License](LICENSE) 发布；上游及第三方文件保留各自许可证、版权与署名，见 [来源说明](NOTICE.md)。本项目不是 M5Stack 官方发行固件。
