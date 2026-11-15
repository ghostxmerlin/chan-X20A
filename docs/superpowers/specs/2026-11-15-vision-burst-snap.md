# 视觉爆发 snap 线协议（collector-0.4.99，2026-11-15）

主人在围观（MEET/crowd）说“注意看”后，ear 在 2 分钟内循环：向机器人要一张照片 → 机器人立即拍并上传 → ear 识别（约 1.6 s）并存结果 → 再要下一张。**没有固定间隔，严格一次一张。** 本文是机器人侧（collector-0.4.99）已实现的契约，ear 侧按此编写。机器人代码：`main/collector/vision_snap.h`（纯逻辑，主机测试 `tests/vision_snap_test.cpp`）、`vision_uplink.cpp`、`mouth_link.cpp`。

## 1. 请求：ear → 机器人（/mouth 文本帧）

```json
{"type":"snap","id":17}
```

- `id`：uint32（0–4294967295），由 ear 选，建议每条 /mouth 连接内递增。机器人原样带回，不检查是否重复。
- 整条消息 **≤ 200 字节**（/mouth 总上限 1600 B 不变）。多余字段忽略。
- 超长、`id` 缺失或不是 0–4294967295 的整数（负数、小数、字符串、布尔、null）= 坏消息：机器人只计数 `mouth.bad_messages`，**不回复**。ear 必须自带超时（建议 3 s）。
- 旧固件（≤ 0.4.98）把 `snap` 当未知类型静默忽略，同样不回复——ear 可用“3 s 无 snap_result”判断机器人不支持。

## 2. 照片：机器人 → ear（/vision 二进制帧，现有 SVF1）

照片走现有 `/vision` 连接的 `"SVF1"` 帧，头部 JSON 多一个字段 `snap`：

```json
{"type":"frame","seq":1234,"t_us":...,"now_us":...,"w":320,"h":240,"diff":12,"bytes":18000,"snap":17}
```

- `snap` = 请求的 `id`；普通变化帧没有这个字段。现有 `vision_link.py on_binary` 忽略未知字段，照常存盘（`frames/<session>/<t_us>-<seq>.jpg`）并写 `db.frame`——**ear 若要按 snap 匹配，需要自己读 `head["snap"]`**（目前 `db.frame` 不存它）。
- 照片是**请求被接受之后拍下的**最新一帧（`t_us` ≥ 接受时刻；摄像头约 10 fps，通常等 ≤ 100–200 ms），不受“画面变化”规则和 1 s 最小间隔限制。
- 发出的 snap 帧也算“上一张已发帧”：之后 1 s 内的普通变化帧照常被间隔规则压住。
- M12 事件优先（`frame_gate_open`）：同一轮里先把排队的 M12 事件发完，队列空、且没有围观点头 look 进行中，才发 snap 帧。
- 同一 /vision 连接上，snap 帧一定先于它的 `snap_result`（后者走 /mouth）由机器人交给 socket；但两条连接不同，**ear 收到的顺序不保证**。匹配用 `snap_result.seq` == 帧头 `seq` 且帧头 `snap` == `id`；收到 `ok:true` 但帧还没到时，ear 应稍等（建议 ≤ 1 s）。

## 3. 结果：机器人 → ear（/mouth 文本帧）

成功（帧已交给 /vision socket）：

```json
{"type":"snap_result","id":17,"ok":true,"seq":1234,"bytes":18000,"ms":180}
```

- `seq`：相机帧序号，等于 SVF1 头的 `seq`；`bytes`：JPEG 字节数（= 头部 `bytes`）；`ms`：接受 → 帧交给 socket，机器人时钟。

失败：

```json
{"type":"snap_result","id":17,"ok":false,"why":"busy"}
```

| why | 含义 | 何时 |
|---|---|---|
| `busy` | 已有一个 snap 在途 | 立即 |
| `camera_off` | 本体相机关闭（主人说 9 才开；snap 不会开相机） | 立即 / 接受后相机被关时 |
| `no_uplink` | /vision 未连接，或上一次帧发送失败后的 60 s 帧保护期内（`kFrameFailHoldUs`），或 snap 帧发送时 socket 失败 | 立即 / 发送失败时 |
| `timeout` | 接受后 2 s 内没能发出（无新鲜帧、M12 事件一直排队、帧过大/内存不足反复失败） | 接受后 2.0 s |

判断顺序：busy → camera_off → no_uplink。

**每个被接受或被拒绝的请求恰好一个 snap_result**，例外只有：坏消息（不回复）、/mouth 断线（见 §4）。

## 4. 单飞、时限与断线

- 机器人最多一个 snap 在途：从接受到它的结果入队为止。结果入队后即可接受下一个。ear 应**收到 snap_result 后**（成功时再等帧到、识别完）才发下一个；在途时再发只会得到 `busy`。
- 时限 2 s（机器人时钟，从接受起算），由 /mouth 任务每 10 ms 检查，超时立即回 `timeout`。
- 超时后，如果那张帧当时正在发送（大帧发送最长 3 s），它仍会带着 `snap:<id>` 到达 /vision，但**不会再有第二个 snap_result**。ear 收到“已判超时的 id”的帧可当普通帧或丢弃。
- /mouth 断线：在途 snap 和未发出的结果全部丢弃（机器人计数），重连后不补发。ear 断线即视该 id 作废。
- /vision 断线：在途 snap 留到 2 s 时限；若 /vision 在时限内重连则照发，否则 `timeout`。新请求在 /vision 未连接时立即 `no_uplink`。

## 5. 时序（一次正常循环）

```
ear  --/mouth--> {"type":"snap","id":17}
robot: 接受，唤醒 vision 任务（不阻塞 /mouth、LVGL、ASR、音频）
robot: 等一帧 t_us ≥ 接受时刻（≈0–200 ms），先发完排队的 M12 事件
robot --/vision--> SVF1 {"type":"frame","seq":1234,...,"snap":17} + JPEG（典型 15–30 KB）
robot --/mouth--> {"type":"snap_result","id":17,"ok":true,"seq":1234,"bytes":18000,"ms":180}
ear: 识别（≈1.6 s）→ 存结果 → 发 {"type":"snap","id":18}
```

2 分钟内约 50–60 张。2 分钟窗口、“注意看”触发、停止条件全部在 ear；机器人不知道爆发是否开始或结束，也不检查当前模式。

## 6. ear 侧必须注意

- **今天的 ear `mouth_link.py _from_robot` 会把不认识的机器人文本转发给 soul-tts**：ear 侧必须拦截 `snap_result`（像 `place_select` / `mode_*` 那样 `continue`），不要转发。
- 调试：机器人遥测（与 `m12`、`hearing` 同一份状态 JSON）的 `m12.uplink.snap`：`busy/accepted/ok/refused/timeouts/failed/late/dropped`。
- 相机关闭时机器人只回 `camera_off`；要不要提示主人说 9，由 ear/mind 决定。
- 不新增 NVS 键；机器人新缓冲在 PSRAM（结果队列 4 条，满了丢最旧，计 `dropped`）。
