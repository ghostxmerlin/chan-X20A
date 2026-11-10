# M12 原厂固件 → m12d 迁移（2026-11-10）

M12 上的原厂程序已换成自研常驻程序 m12d（协议见 m12d 仓库 `docs/protocol.md`，本机副本在用户的 unitv2m12 工程里）。原厂的 `{"function":...}` 功能切换、`audio_fft` 待机、`Face Recognition` 训练和原厂人脸库全部失效。m12d 上电 20–25 s 后自动运行，一直做人脸检测（约 15 fps，带跟踪 id）、性别年龄和人形检测；身份识别只在被问到时做。

迁移前实机观察：M12 已经在串口输出 m12d 检测行（旧解析器靠兼容字段 `"running":"Face Detector"` 还能读出框），但旧固件发出的 89 次“切到待机”全部没有回应。

## 盘点：依赖原厂协议的地方与迁移方式

| 位置 | 原厂依赖 | 迁移 |
|---|---|---|
| `collector/m12_protocol.h` | 三条 `{"function":...}` 命令；`running` 区分检测/认人；`msg`/`error` 回复；`match_prob` 与 `unknown` 名字；按 `running` 和回复文字判断待机/切换；`M12Policy` 模式状态机 | 重写为 m12d 解析器：检测行（`f`）、心跳（`hb`）、回复（`id`），face 的 `id/gender/age/age_group/name`，`person[]`；请求组包（JSON 转义名字，≤512 字节）；待回复表（learn/who 4 s，其余 1 s）；自动 who 策略（新 id 无名字最多问一次，busy 退避）；追踪目标选择（按 face id 粘住，没脸时用 person）。删除 `M12Policy`、`recognize` 模式、`match_prob`、旧命令常量 |
| `collector/m12_unit.*` | 发送功能切换并按回复文字/输出判断是否生效、8 s/30 s 重发；`Face Recognition` 结果；info 里的原厂命令 | 新驱动：2–4 KB 行缓冲放 PSRAM，按行切分，丢弃超长/坏行；心跳 6 s 判离线；待机/唤醒改为 `cfg det`，以 cfg 回复为准，M12 重启（hb.up 回退或离线后恢复）后重发；learn/who/forget/faces/ping/ver/status/cfg 请求接口，完成回调在 M12 任务里执行；不新增常驻任务 |
| `collector/interaction.cpp`、`interaction_policy.h` | 认人模式 = 把 M12 切到 `Face Recognition`，`OwnerGate` 读 `match_prob` | 认人模式只要求检测开着；身份来自检测行里的 `face[].name`（由自动 who 或 learn 挂上），任一已登记的脸连续 2 帧确认 |
| `collector/remote.cpp`（追踪输入） | 取最大的脸；`frame_recognition` 判断帧来源 | 用 face id 粘住同一个人；没有脸时用 `person[]` 的框（1.5 m 外）；帧来源只有检测 |
| `collector/face_tracking.h` | `mode` 1/2 = 检测/认人 | 只剩 1 = 检测开着；模式确认由 cfg 回复给出 |
| `collector/collector.cpp` | `m12_view`、`m12_mode`（`face_detector`/`face_recognition`/`standby`）；屏幕人脸页的“starting M12…/check blue port”提示 | `m12_mode` 只接受 `face_detector`/`standby`；新增 `m12_face` target：`learn`/`forget`/`faces`/`who`；屏幕页改为按心跳在线状态提示 |
| `collector/asr_unit.cpp` | 0x14 turn on / 0x15 turn off 开关 M12 人脸页 | 不变（经 `m12_set_view` → `cfg det`） |
| `collector/temperature.cpp`、`screen.cpp` | 不读 M12（温度是 ESP32 自己的） | M12 温度来自心跳 `hb.t`，进遥测 `m12.heartbeat`，网页 M12 面板显示；跨 5 °C 或 tier 变化时写日志。屏幕不改 |
| `web/monitor/m12.mjs`、`public/m12-ui.js`、`index.html` | 状态用 `running`/`face_recognition`/`audio_fft` 判断；“手动认人”按钮；匹配度 | 状态改为心跳在线 + cfg det；帧率用 hb.fps；去掉“手动认人”，加“认人库”：学习 / 删除 / 列表 / 认一下，错误码给中文提示；每张脸显示 id、名字、性别、年龄段 |
| `web/monitor/control.mjs` | `m12_mode` 的 `face_recognition` | 删除；新增 `m12_learn`/`m12_forget`/`m12_faces`/`m12_who` |
| `web/monitor/m12-system.mjs` | 经 Wi-Fi SSH 读 M12 温度、进程名 | M12 现在只由 Grove 供电、不插 USB-C，网络通常不可达；保留为可选诊断，温度显示改用心跳 |
| `tests/m12_protocol_test.cpp`、`interaction_*_test.cpp`、`face_tracking_test.cpp`、`tests/test_web_control.py`、`web/monitor/*.test.mjs` | 旧行格式和模式 | 改用 protocol.md 示例行；覆盖心跳、各类回复、错误码、半行、超长行、回复与推送交错 |
| `docs/m12-unitv2.md`、`face-tracking-modes.md`、`collector-protocol.md` | 原厂说明 | 指向本文与 m12d 协议 |

## 约束

- 不改 M12 端；协议问题写清复现交给用户转达。
- M12 只由 StackChan Grove 口供电，不要同时插 USB-C。
- 名字、人脸数据、设备地址不进仓库（测试用占位名字）。
