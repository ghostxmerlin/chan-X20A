# chan-X20A

基于 M5Stack StackChan（CoreS3 + 原装底座）的本地采集与遥控项目。将摄像头、麦克风、六轴和官方遥控数据实时传到 Android，支持观察、录制、标记、回放和导出；不依赖出厂账号或云服务。

本仓库 fork 自 [m5stack/StackChan](https://github.com/m5stack/StackChan)，开发起点为 [`f901cc2`](https://github.com/m5stack/StackChan/commit/f901cc2969189f3f0e96af1442c0f96e77e1e2c1)。保留上游提交历史，官方源码统一放在 `upstream/StackChan/`。

## 当前状态

截至 2026-10-31，开发设备已刷入 **collector-0.3.5**，Android 观察台已安装 **0.3.4 / code 6**。

- 机身开机显示绿色笑面男；轻触打开 Mac 绿色终端样式的信息页，再点返回。
- 开机俯仰目标为有效行程的 10%（13°），首次启动实测 12.8°；新遥控输入立即接管。
- 320×240 JPEG 预览；当前实收帧率会波动，目标 10 fps 尚未达到。
- 24 kHz PCM 音频采集与观察台监听；机器人扬声器没有播放业务。
- 加速度、角速度三轴曲线与原始样本记录，目标采样 50 Hz；触摸、电量和链路状态展示。
- 官方 ESP-NOW 摇杆/姿态输入、目标与位置反馈分开记录，遥控接收独立于观察台连接。
- 已修正一次位置采集失败就关闭转头的状态错误；开机就绪、UI 停止再启用的状态保持已实测。

**已知未解决问题：此前舵机位置回传大量无效的根因仍未解决；0.3.5 本次启动的 117 组反馈有效，但完整摇杆/姿态回归尚未重新验收。当前不能称为恢复原厂稳定性的版本。** 历史版本成功测试不能替代本版验收。App 仅支持前台采集，尚无后台/锁屏记录、SD 断网补录或肩上长时运行验收。

详见 [当前状态快照](docs/current-state.json)、[验证记录](docs/collector-validation.md)和[交接记录](docs/session-handoff.md)。

## 目录

| 路径 | 内容 |
| --- | --- |
| `upstream/StackChan/firmware/` | 基于官方源码的 ESP32-S3 采集固件 |
| `upstream/StackChan/remote/` | 官方遥控器源码，保留作协议参考 |
| `upstream/StackChan/app/`、`server/` | 上游手机端与服务端参考源码，不是当前采集入口 |
| `android/Collector/` | 独立 Android 观察台，原生 Java |
| `config/` | 当前版本 ESP-IDF 构建配置 |
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

仓库只发布源码、配置与整理后的验证结果。凭据、签名私钥、原机备份、原始串口日志、现场图像/录音和本机构建产物保留在本地，不随 Git 上传。历史文档中的 `logs/`、`artifacts/` 链接是本地证据路径，不是公开下载链接。

## 来源与许可

新增采集代码按 [MIT License](LICENSE) 发布；上游及第三方文件保留各自许可证、版权与署名，见 [来源说明](NOTICE.md)。本项目不是 M5Stack 官方发行固件。
