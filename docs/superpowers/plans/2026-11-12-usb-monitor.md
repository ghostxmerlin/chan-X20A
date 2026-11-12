# USB 监视台实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 监视台经 USB 接收状态、事件、画面并控制机器人，停用旧网络监视服务以释放网络和内部 RAM。

**Architecture:** 共用业务响应/遥测构建器；有界 USB 帧协议与单发送者；Mac 字节桥与串口 DeviceClient；复用现有 SSE/网页。

**Tech Stack:** IDF 5.5.4 / C++17 / ArduinoJson，Node 24，Python 3 标准库。

**Spec:** ../specs/2026-11-12-usb-monitor-design.md

## Global Constraints

- 固定 IDF 5.5.4，不改锁文件、分区、NVS/SD/舵机策略；仅目标开发机 ota_1 应用部署。
- 网络监视默认关闭、代码保留；不自动回落 HTTP；听觉/视觉上传/TTS 继续联网。
- USB 无 DTR/RTS 操作；单 owner；控制不自动重发；有界缓冲，业务线程不等待串口。

## Review Focus

- 拔线或宿主停止读取不能卡住麦克风/播放，串口重连必须恢复新快照。
- USB 上混入启动日志或丢失帧时可恢复，超长声明不增长无限内存。
- 旧 boot 的控制拒绝；命令已经执行但 ACK 丢失时不重放。
- 安静时在线不误判失联；心跳不让过期传感器数据变新鲜。
- 关闭网络监视后仍可使用服务器的说话、听觉、视觉能力；资产安装功能限制必须说明。

### Task 1: 共用协议和固件业务响应
Files: 新增 collector/monitor_protocol.h、control_reply.h、tests/monitor_protocol_test.cpp、test_monitor_protocol.py；修改 collector.cpp、hearing、interaction、eyes/http。
- [x] 测试 CRC 固定向量、帧头边界、碎片恢复与变化字段行为，先观察缺失实现失败。
- [x] 实现帧协议与 transport-independent ControlReply，HTTP wrapper 使用同一业务逻辑。
- [x] 提取 fill_info/fill_telemetry，旧路径回归与 IDF 构建。

### Task 2: USB worker 和默认关闭网络
Files: 新增 collector/serial_monitor.{h,cpp}；修改 collector.cpp、runtime_log、Kconfig、CMakeLists、版本。
- [x] 测试租约、boot/token、幂等订阅/不重放、丢包重同步和事件游标。
- [x] 实现单发送者、日志有界队列、PSRAM JSON/栈、身份握手、初始快照/递归增量/事件/帧。
- [x] 关掉 HTTP/audio/discovery/asset worker 的启动，保留编译开关；构建 USB 默认配置，并编译检查启用 HTTP 宏的 collector 翻译单元。

### Task 3: Mac 串口接入与生命周期
Files: 新增 web/monitor/serial*.mjs、serial_bridge.py 及测试；修改 core/server/service 与绑定配置。
- [x] 测试噪声/碎片/CRC/边界、RPC deadline、断连不重放、状态合并/事件去重、身份校验和停止订阅。
- [x] Python 匹配序列号并无复位打开；Node SerialDeviceClient 采用相同 control 语义，状态推送给 Monitor。
- [x] 默认选择串口，HTTP 显式配置可恢复；保留网页和 cloud API；运行全部 Node 测试。

### Task 4: 设备验证与交付
- [x] 主机固件回归、协议测试、固定 IDF 构建；独立代码审阅并修复实质问题。
- [x] 备份/核验目标、应用刷写/独立 verify；串口握手、状态/帧/控制、重连/无读者与网络监听检查。
- [x] 对照 RAM/任务/socket 证据，更新使用说明和实际验证结果；保留本地证据不提交秘密。

执行结果：见 `docs/usb-monitor.md` 的实测记录；实机完成桥接断连/慢读/恢复，物理拔线和满负载听觉/视觉/TTS 并发尚未验收。
