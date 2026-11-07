# Basic controls implementation plan

> **For agentic workers:** Follow the task ownership below; preserve concurrent edits. Use the normal execution and review workflow.

**Goal:** 收成可逐项扩展的基础版：保留首页、参数、日志、舵机、爱心/叉号及 ASR 控制 M12。

**Architecture:** 保持当前 collector 与 Android 基础采集协议。关闭情绪接入/显示与机身人脸检测；M12 改为显式开关，不运行开机识别、自动唤醒或空闲策略。试验源码和本机资源保留，后续逐项启用。

**Tech Stack:** ESP-IDF 5.5.4、C++17、LVGL、Python 主机测试。

**Spec:** 用户本轮要求及已确认选项：ASR turn on 开 M12 检测，turn off 或触摸退出后待机；暂停开机自动识别和自动唤醒。

## Global Constraints

- 保留保存的舵机角度、遥控左右/中心开关屏、爱心/叉号与原提示音；不改原厂运动/限位参数。
- 保留基础参数采集、相机/音频观察和 Android 连接；停止情绪试验进程，不动 Claude 的 server/ear 服务。
- M12 UART0、舵机 UART1、ASR UART2/GPIO8 保持；新 UART 安装仍在 core 1。
- 新版号 collector-0.4.17；源码、构建和实机证据分别记录。
- 如部署，仅在重新核对身份、活动分区和当前版本后写 ota_1 / 0x510000 并独立校验。

## Review Focus

- 无人脸、无 M12 回包时，不能自行开启识别或退出用户请求的检测。
- 遥控心跳、普通触摸和无关 ASR 命令不能启动 M12。
- 旧情绪客户端或 Android 人脸开关不能重新开启已停用的功能。
- 屏幕关闭/恢复、页面导航与爱心/叉号生命周期必须保持。
- 主机测试通过不等于 M12 长待机唤醒或实际舵机已验收。

### Task 1: 基础页面

**Ownership:** worker；`screen.cpp`、`screen.h`、`tests/display/display_preview.cpp`、显示测试 CMake 及必要的显示基准适配。

- [x] 在真实 LVGL 屏幕测试中验证首页不再显示情绪状态，保留原导航/反馈测试。
- [x] 移除首页情绪控件及 ScreenStatus 的情绪耦合；保留原页面布局、配对支持和 M12 人脸页。
- [x] 显示测试通过，保存预览供目视检查。

### Task 2: ASR 手动 M12

**Ownership:** worker；`m12_protocol.h`、`m12_unit.h/.cpp`、`tests/m12_protocol_test.cpp`。

**Interfaces:** 保留 `m12_set_view(bool)` 作为显式检测开关；`m12_wake()` 不得开启检测（可保留为空兼容入口）；根代理移除无关唤醒调用。

- [x] 为启动待机、显式开启、长期检测、关闭和无关唤醒加入可失败的行为测试。
- [x] 运行模式只有 standby / face detector；不自动识别、不因空闲时间或遥控心跳改变功能。
- [x] 保留串口解析、重试和遥测的真实状态，协议测试通过。

### Task 3: 固件集成与交付

**Ownership:** root；`collector.cpp`、`face_detection.cpp`、固件 CMake、文档及本机服务管理。

- [x] 关闭情绪 API/状态生产、移除 M12 自动唤醒调用；能力元数据明确标注停用。
- [x] 关闭机身人脸检测及其开启入口，保留 M12 独立检测；检查 Android 对禁用能力的行为。
- [x] 停止匹配本项目的本机 emotion 服务；保持资源与配置。
- [x] 运行 Python、ASR/M12/遥控和显示测试；固定工具链构建；独立代码审阅。
- [x] 检查设备可用性，若可用则核对后部署并记录实际日志；无法实测的项目明确列出。
- [x] 同步 README、AGENTS、交接与验证记录。

## Execution record

- Task 1/2 完成且行为测试先红后绿。审阅发现快关快开时旧检测复用会漏发开启；删除优化并增加生产命令选择回归，复核通过。
- 最终构建/部署与 45 秒启动、curl 接口检查已完成，见最新 session-handoff。首次慢速回读超时未写入，恢复后高速回读成功。
- Python HTTP 探测路径失败而系统 curl 成功；未更改网络。真人 ASR、长待机及舵机动作仍按实机边界列为待验。
- 在当前目录的独立功能分支保留本轮改动，复用既有工具链/构建目录；未创建额外工作树，未合并或推送共享分支。
