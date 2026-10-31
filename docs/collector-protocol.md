# Collector 局域网协议 v1

状态：2026-10-31，协议版本仍为 1；兼容 collector-0.1.0 / 0.2.0，0.3 增加可选批量运动数据和官方遥控字段。0.3.2 增加独立遥控模式，0.3.4 分离位置采集与动作启用状态；实际部署及硬件验收以交接记录最新段为准。所有数值含义均以实际能力声明为准。

## 连接

机器人提供 HTTP 8080 控制/快照端口与 HTTP 8081 音频端口。所有端点要求 `Authorization: Bearer <token>`。token 是每台设备生成并存于独立 collector NVS 的 128 位随机十六进制值，屏幕/本地串口用于首次配对。示例不含真实值。该令牌不提供传输加密，首版限可信局域网。

`GET /v1/info` 返回 `protocol_version`、`device_id`、本次启动唯一的 `boot_id`、`firmware`、`capabilities`、`audio_port`，以及 `audio`（sample_rate、channels、bits、channel_roles）和 `camera`。采集端必须在连接、重连时获取 info；boot_id 变化时分段，不能延续旧序号。

## 传感器

`GET /v1/telemetry` 返回 JSON：

```json
{
  "boot_id": "example-boot",
  "seq": 1,
  "timestamp_us": 1000000,
  "imu": {"valid": true, "timestamp_us": 990000, "accel": [0,0,9.80665], "gyro": [0,0,0], "accel_unit": "m/s^2", "gyro_unit": "deg/s"},
  "head_touch": {"valid": true, "timestamp_us": 995000, "values": [0,0,0]},
  "touch": {"valid": true, "count": 0, "x": 0, "y": 0},
  "power": {"valid": true, "battery_percent": 80, "charging": true},
  "health": {"heap_free": 100000, "psram_free": 4000000}
}
```

以上为格式示例，不是实测数据。单位以字段为准；无效或未支持数据不得伪造为 0/满电。顶层时间为快照时间，传感器时间为最近一次成功采样时间。客户端可约 10 Hz 轮询，按 seq 去重，记录原 JSON 和接收时间。

### 0.3 批量运动记录

`GET /v1/telemetry?imu_after=N&remote_after=M` 中 N/M 是同一 boot 最近接受的 IMU / 遥控输入序号。首次传 0，重启必须清零。不带游标参数时只返回当前快照，不序列化历史数组，避免旧客户端每次重复下载整段缓存。显式传 `imu_after=0` / `remote_after=0` 才请求初始历史，后续传已收序号取增量；新客户端也兼容没有批量能力的旧固件。

- `imu_samples` 为最近 100 个读数中序号大于 N 的部分，每个含 `seq`、`timestamp_us`、`valid`、`accel` 和 `gyro`。无效样本不附向量。时间为读取完成的设备单调时间，不是 IMU 硬件中断时间。
- `imu_history_lost` 表示从已知游标到环形缓存最早样本之间丢失的数量；初始游标 0 不把启动之前数据计为丢失。图像、音频与传感器仍按各自采样时间记录。
- 采集读取目标 50 Hz，BMI270 ODR 100 Hz，加速度 ±8g、角速度 ±2000°/s。实际采样率须从导出时间差计算。没有声称 100 Hz 全量 FIFO。
- `accel` 单位 m/s²，包含重力，坐标为运动头部的传感器轴。安装到肩部/身体的坐标变换未标定；合加速度不是去重力后的线加速度。
- Android 除保留原 JSONL，还按样本序号去重写 `motion.csv`；可供后处理分析。无效向量留空，不写成 0。

### 0.3 官方遥控与舵机反馈

使用已固定上游的 `esp-now` 组件 DATA 数据类型，与官方遥控器一致；不是把裸 8 字节直接交给 `esp_now_register_recv_cb`。不重新初始化 Wi-Fi、不强制跳频道，接收频道跟随采集网络。

| 字节 | 官方含义 |
| --- | --- |
| 0 | 目标 ID；0 广播，当前接收 ID 1 |
| 1–2 | 有符号小端水平角 int16，名义映射 −1280…1280，单位 0.1° |
| 3–4 | 有符号小端俯仰角 int16，名义映射 0…900，单位 0.1° |
| 5–6 | 有符号小端速度，0…1000，无量纲 |
| 7 | 辅助按钮，0/1；只记录，不驱动 GPIO2 附件 |

官方映射可能外推至名义角度范围之外，实机已收到俯仰 95°、97.3°及水平 148.8°。0.3.1 解码保留完整 int16 目标，仍检查 8 字节长度、辅助值 0/1、速度 0…1000 与目标 ID；执行前单独限幅，不污染原始输入记录。

遥控器摇杆模式和姿态模式都发送上述映射后的目标，没有原始摇杆、遥控器 IMU、模式标志、发送序号或心跳。因此不能还原这些原始量、统计空口丢包，或根据静默时间判断遥控器断线。`seq` / `rx_packets` 是机器人实际接受的输入计数，组件负责同一 ESP-NOW 帧重发去重。

遥测 `remote` 包含接收器是否就绪、Wi-Fi 频道、接收 ID、拒绝/其他目标计数、最新 `last`、64 条环形历史中的 `packets` 和 `history_lost`。每包保存机器人接收时间、源 MAC、RSSI、频道和解码目标。Android 写 `remote.csv`，作为输入数据；`remote.motor` 和 `servos.csv` 另存已应用目标及舵机实际反馈。读回失败时 `valid=false`，不使用驱动回退角度冒充实测。

0.3.1 每轴位置回传增加 `transport_error`（0 正常、1 无回复、2 校验失败、3 ID 不符、4 长度不符）、`servo_status`、`position_raw`。仅位置事务正常、舵机状态为 0、原始位置在 0…1000 时 `valid=true` 并给出角度；负载/电流有各自的 `load_valid` / `current_valid`，可选读回失败不否定有效位置。拒绝包的诊断 `last_rejected` 保留长度、时间和可解出的字段，不作为已接受的输入。

0.3.2 的 info/telemetry `remote.control_mode="standalone"`、`requires_observer=false`，兼容字段 `lease_ms=0`。`POST /v1/remote` 使用同一 Bearer 鉴权，JSON `{"enabled":true}` 开启接收执行，`false` 停止并释放力矩；不需要续约。`motor.requested` 是请求状态，`motor.enabled` 是执行任务的当前状态，UI 以机器人的返回值为准。Android 0.3.2 仅在用户明确点击开关时发送控制请求，连接、断线、重连、boot 更新均不自动停用独立接收器；对旧固件仍保留原 750 ms 续约。

启动默认准备底座 VM 和舵机，只读校准、关闭力矩、不归位；等待本次启动的新遥控输入。停止后再启用只处理启用以后收到的包。0.3.4 的 info/telemetry 新增 `remote.feedback_required_for_control=false`，`remote.motor.feedback_valid` 表示本次两轴位置是否同时有效。位置读回失败不修改 `motor.requested`，也不阻止用户重新启用；无效位置仍不提供角度，不作为实测值绘图或记录。此前 0.3.2 的失败锁停是采集与控制耦合错误，已撤掉。水平夹到 ±128°、俯仰夹到 5–85°；速度保留官方包的 0…1000（原厂摇杆与姿态模式均发 600），取消此前 400 上限。

0.3.4 观察任务仅额外读取位置，单次观察读回的超时为 2 ms，之后恢复驱动原超时；不额外读取负载和电流，`load_valid` / `current_valid` 为 false，旧字段保留兼容。原厂动作内部的俯仰堵转反馈事务仍保留，未更改其超时。驱动的校验和、ID、长度和原始位置范围检查均未绕过。

连续输入沿用原厂弹簧轨迹，不在每包重复同步实测角度打断速度；新控制会话首包才同步位置。取消 3 秒命令处理窗口，静默时完成剩余轨迹并保留原厂到位自动释放力矩。官方遥控器没有心跳，静默不表示掉线。原厂俯仰堵转保护保留；辅助按钮只记录，不控制 GPIO2 附件。旧 0.3.1 的三秒租约属于历史行为，不适用于独立模式。

依据：[固定官方遥控源代码](https://github.com/m5stack/StackChan/blob/f901cc2969189f3f0e96af1442c0f96e77e1e2c1/remote/code/main/joystick/joystick_handle.c)、[机器人接收实现](https://github.com/m5stack/StackChan/blob/f901cc2969189f3f0e96af1442c0f96e77e1e2c1/firmware/main/apps/app_espnow_ctrl/app_espnow_ctrl.cpp)、[ESP-IDF 5.5.4 ESP32-S3 ESP-NOW](https://docs.espressif.com/projects/esp-idf/en/v5.5.4/esp32s3/api-reference/network/esp_now.html)、[M5Stack 运动角度说明](https://docs.m5stack.com/en/StackChan)。

## 图像

`GET /v1/frame` 返回 `image/jpeg`，响应头包含 `X-Boot-Id`、`X-Seq`、`X-Timestamp-Us`、`X-Width`、`X-Height`。无可用帧或最后成功采集超过 3 秒返回 503。客户端按 `/v1/info` 的 `camera.target_fps` 获取，Android 当前上限 10 Hz；0.1.0 目标 2 fps，0.2.0 目标 10 fps，均不等于保证的实收帧率。重复序号不重复记录；预览慢时只保留最新待显示画面。录制索引记录序号、采集时间、接收时间、尺寸及文件名。

这是连续请求 JPEG 帧的实时画面，尚无 multipart MJPEG 长连接、H.264 或 MP4 文件输出。`health.camera_processing_us`、`camera_jpeg_us`、`camera_jpeg_bytes` 是 0.2.0 新增的最近一次相机处理、JPEG 编码耗时（微秒）和 JPEG 长度，不是端到端延迟。

## 音频

8081 的 `GET /v1/audio` 返回 HTTP chunked 连续二进制；HTTP chunk 边界与音频包边界无关。首版只保证一个音频消费者，忙时返回 503。应用层每包包含 32 字节头和 PCM：

| 偏移 | 类型 | 内容 |
| --- | --- | --- |
| 0 | 4 bytes | ASCII `SCA1` |
| 4 | uint32 LE | 采集序号 |
| 8 | uint64 LE | 块首样本估算单调时间，微秒 |
| 16 | uint32 LE | 采样率 Hz |
| 20 | uint32 LE | 每通道帧数 |
| 24 | uint16 LE | 通道数 |
| 26 | uint16 LE | 位深，首版 16 |
| 28 | uint32 LE | payload 字节数 |
| 32 | bytes | 交错有符号 PCM16LE |

约定 `payload_bytes = frames × channels × 2`；客户端限制包长并完整读取，拒绝畸形和截断包。通常 24 kHz、480 帧一块，通道含义从 info 获取；现有 codec 的 mic/reference 不等于两个实体麦立体声。

序号由采集端递增，环形缓冲溢出或慢客户端会产生可检测 gap。块时间是首样本估算时间，非声学硬件同步。记录每块序号、设备时间、接收时间及 PCM 偏移；对缺失写入事件并在索引体现，不静默补齐后称无丢失。

## 会话边界和错误

重新连接或 boot_id 改变须生成分段/事件；设备与手机时钟通过同时记录单调设备时间和手机接收时间建立关联，不能用网络接收时间代替采样时间。视频丢帧、音频 gap、写盘队列溢出、传感器无效均显式记录。token 不写入导出 manifest 或普通调试日志。
