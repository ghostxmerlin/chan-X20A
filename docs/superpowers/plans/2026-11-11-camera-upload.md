# 视觉上传：画面变化时每 10 秒一张传到服务器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 本体在机身摄像头开着时，判断画面有没有变化：变了，最多每 10 秒通过 `/vision` 通道发一张 JPEG；没变就不发。耳朵把每张帧存成文件，并写进 `hello.frames`。情景页显示服务器上收到的最新一帧。开关条的视觉按钮改成"关 / 上传 / 识别"（识别留到第 3 步，先显示但不能点）。

**Architecture:**
- 固件：
  - 摄像头任务在压 JPEG 之前，先从原始像素算一个 16×12 的亮度缩略签名，放进 `Frame`。
  - `vision_uplink` 任务比较最新帧和上一张已发帧的签名，按策略发一条二进制消息：`"SVF1"`，加 2 字节头长度，加 JSON 头，加 JPEG。
  - 判断逻辑写成一个不依赖 IDF 的头文件，在主机上用 C++ 测试。
- 服务器：
  - `vision_link.py` 解析二进制消息，把文件写到录音目录下的 `frames/`，用 `HelloDB` 写索引。
  - mind 接口提供 `GET /frames/latest`，Mac 代理放行，情景页用 blob URL 显示。

**Tech Stack:** ESP-IDF 5.5.4（固定）、C++17；Python 3.11；Node 24。

**Spec:** `docs/superpowers/specs/2026-11-11-senses-split-design.md`（第 2 节第 3 条、第 4 节第 2 步）。

## Global Constraints

- 上传策略（用户定）：
  - 画面有变化时，距上一张已发帧满 10 秒才发；画面不变就不发。
  - 每次连接建立后的第一帧一定发，作为对照基准。
- 变化判断：16×12 亮度格逐格比较，绝对差的平均值 ≥ `kFrameChangeThreshold = 6`（满分 255）就算变了。阈值是暂定值，现场按实测调整，调整时要写进记录。
- 帧只在机身摄像头开着时产生。摄像头开关保存在 NVS 的 `camera_on`，沿用现在的行为，这一步不改。
- 二进制消息：
  - 开头 4 字节是 ASCII `SVF1`，接着是 2 字节小端的头长度（不超过 512），然后是 JSON 头 `{"type":"frame","seq","t_us","now_us","w","h","diff","bytes"}`，最后是 JPEG。
  - 整条消息不超过 32768 字节；超过就跳过这一帧，计入 `frames_too_big`。
- `HearingSocket::send` 原来上限 8192 字节：二进制消息放宽到 32768，文本消息仍是 8192。
- **PSRAM 优先**（用户规矩）：拼消息用的缓冲区从 PSRAM 申请（`heap_caps_malloc(..., MALLOC_CAP_SPIRAM)`），不新开内部 RAM 缓冲区。
- 帧文件全部保留，不自动删除。存放路径是 `<recordings>/frames/<视觉会话 key>/<t_us>-<seq>.jpg`，路径只由服务器生成的会话 key 和整数拼成。
- 刷机只写 `ota_1 / 0x510000`，要备份并独立校验，沿用 `.private/web-servo-v0439` 里 0.4.74 的那套流程（preflight → make_artifact → deploy）。固件版本改为 `collector-0.4.75`。
- 服务器部署要用户明确说"部署"。

## Review Focus

1. **画面没变，人也不在**：期望只在连接后发 1 张，之后不再发。测试在 Task 1（策略）和 Task 6（现场）。
2. **连接断了，期间画面变过**：期望不攒积压，重连后先发当前这一帧。测试在 Task 1。
3. **复杂画面导致 JPEG 超过 32 KB**：期望只跳过这一帧，计数加一，连接不断。代码在 Task 2，状态里能看到计数。
4. **服务器收到坏的二进制**（魔数不对、头太长、seq 不是整数、不是 JPEG）：期望回错误、不写文件、连接不断。测试在 Task 3。
5. **摄像头关着**：期望没有帧；M12 也离线时，通道照旧在 30 秒后关闭。代码在 Task 2，现场在 Task 6。

---

### Task 1: 画面变化判断（主机可测的头文件）

**Files:**
- Create: `upstream/StackChan/firmware/main/collector/frame_change.h`
- Test: `tests/frame_change_test.cpp`、`tests/test_frame_change.py`（照 `tests/test_face_pixels.py` 的写法：用 `c++ -std=c++17 -Wall -Wextra -Werror` 编译后运行）

**Interfaces:**
- Consumes: `face_pixels.h` 里的 `FacePixelFormat`，以及它读 RGB565（`p[0] | p[1] << 8`）和 YUYV 的方式。
- Produces:
  - `struct FrameThumb { bool valid = false; uint8_t cell[192]; };`，16×12 格，每格是该格中心像素的亮度 0–255。
  - `FrameThumb frame_thumb(const uint8_t* pixels, size_t bytes, int width, int height, FacePixelFormat format)`：格式不支持，或字节数小于 `width × height × 每像素字节数` 时，返回 `valid = false`。
  - `int frame_diff(const FrameThumb& a, const FrameThumb& b)`：各格绝对差的平均值，四舍五入；有一方无效时返回 255。
  - `bool frame_should_send(bool have_last, int diff, int64_t since_last_us)`：返回 `!have_last || (since_last_us >= 10'000'000 && diff >= kFrameChangeThreshold)`。
  - `constexpr int kFrameChangeThreshold = 6; constexpr int64_t kFrameMinGapUs = 10'000'000;`

- [ ] **Step 1: 写失败的测试** `tests/frame_change_test.cpp`，用 `assert`：
  - 两张全 0x80 灰度帧，`diff == 0`；一张整体亮度加 20，`diff == 20`。
  - RGB565：纯白 `0xFFFF` 的亮度格接近 255（≥ 250），纯黑为 0。
  - YUYV：Y = 100 时，亮度格是 100。
  - 字节数不够时 `valid == false`；有一方无效时 `frame_diff` 返回 255。
  - 发送策略：
    - `frame_should_send(false, 0, 0)` 为 true（连接后的第一帧）；
    - `(true, 30, 9'999'999)` 为 false（还没满 10 秒）；
    - `(true, 5, 60'000'000)` 为 false（没变化）；
    - `(true, 6, 10'000'000)` 为 true。
- [ ] **Step 2: 运行，确认失败**。Run: `python3 -B -m unittest discover -s tests -p "test_frame_change.py" -v`。Expected: 编译失败，提示 `frame_change.h: No such file`。
- [ ] **Step 3: 实现 `frame_change.h`**：只依赖 `<cstdint>`、`<cstddef>` 和 `face_pixels.h`。亮度按 `(77R + 150G + 29B) >> 8` 计算；GREY 直接取值；RGB888 也支持。
- [ ] **Step 4: 运行，确认通过**（同一条命令，OK）
- [ ] **Step 5: 提交** `feat(collector): frame change signature and upload policy (host-tested)`

### Task 2: 固件——摄像头签名，`/vision` 发帧

**Files:**
- Modify:
  - `upstream/StackChan/firmware/main/collector/collector.cpp`：`struct Frame`（115 行）、`camera_task`（513–550 行）
  - `upstream/StackChan/firmware/main/collector/vision_uplink.cpp`：`wanted()`、主循环、`vision_uplink_json`
  - `upstream/StackChan/firmware/main/collector/hearing_socket.cpp`：`send` 的上限
  - `upstream/StackChan/firmware/CMakeLists.txt`：版本号
- Create: `upstream/StackChan/firmware/main/collector/camera_frames.h`，用来暴露最新帧
- Test: 主机测试跑 `test_frame_change.py`；整体用 `./scripts/idf.sh build` 构建通过。

**Interfaces:**
- Consumes: Task 1 的全部接口。
- Produces（Task 3 依赖它的消息格式）：
  - `camera_frames.h` 里：
    - `struct CameraFrame { const uint8_t* jpeg; size_t length; uint32_t seq; int64_t timestamp_us; int width, height; FrameThumb thumb; };`
    - `std::shared_ptr<const CameraFrame> camera_latest_frame();`（没有帧或摄像头关着时返回 `nullptr`）
    - `bool camera_streaming();`
  - 原来的 `Frame` 改为持有一个 `FrameThumb thumb`。具体结构由实现决定，只要 `CameraFrame` 的字段能从它取到。`camera_task` 在压 JPEG 之前用 `frame_thumb(camera->GetFrameData(), …)` 算出签名，格式换算沿用 `face_detection.cpp` 的映射。
  - `vision_uplink`：
    - `wanted()` 改为 `m12 在线 || 有待发事件 || camera_streaming()`。
    - 主循环每次取 `camera_latest_frame()`，`seq` 是新的、且 `frame_should_send(have_last, frame_diff(thumb, last_thumb), now - last_sent_us)` 为真时，在 PSRAM 拼好二进制消息发出去。发送成功才更新 `last_thumb` 和 `last_sent_us`。
    - 每次连接时把 `have_last` 重置为 false。
    - 超过 32768 字节就跳过，`frames_too_big` 加一。
  - 状态 JSON 增加 `frames_sent`、`frames_too_big`、`last_frame_diff`、`last_frame_age_ms`，在观察台的诊断里能看到。
  - `hearing_socket.cpp`：上限改为 `binary ? 32768 : 8192`，发送时限仍是 1 秒。

- [ ] **Step 1**：先确认 Task 1 的主机测试仍然通过（这一步不新增主机测试，固件集成靠构建和现场验证；记一条 Ruling）。
- [ ] **Step 2: 实现上述改动**，版本号改为 `collector-0.4.75`。
- [ ] **Step 3: 构建**。Run: `./scripts/idf.sh build 2>&1 | tail -5`。Expected: `Project build complete`，应用大小小于 ota_1 的槽位（沿用 make_artifact 的检查）。
- [ ] **Step 4: 运行全部主机测试**。Run: `python3 -B -m unittest discover -s tests -p "test_*.py"`。Expected: 除了已知缺包的 ear 测试，全部 OK；ear 测试用 `.private/venv-ear` 另跑，也要 OK。
- [ ] **Step 5: 提交** `feat(collector): 0.4.75 — body-camera frames to the server on change, at most one per 10 s`

### Task 3: 耳朵接收帧并存档

**Files:**
- Modify: `server/ear/vision_link.py`、`server/ear/hello_db.py`、`server/ear/ear_server.py`（调用 `serve_link` 的地方，传入 `frames_root`）
- Test: `tests/test_vision_link.py`

**Interfaces:**
- Consumes: Task 2 的二进制消息格式。
- Produces（Task 4 依赖）：
  - `hello.frames` 文档：`{session, seq, t_us, at, received, path, bytes, w, h, diff}`。
    - `at` 和视觉事件的算法一样：收到时间减去 `(now_us − t_us)`。
    - `path` 是相对 `recordings` 根目录的路径。
    - 以 `(session, seq)` 为键 upsert。
  - `VisionLink.on_binary(data: bytes) -> dict | None`：返回值和 `on_text` 一样，出错时回错误。
  - `HelloDB.frame(doc)`；会话关闭时把 `frames` 计数写进会话文档。
  - `serve_link(ws, db, number, peer, frames_root=None)`。

- [ ] **Step 1: 写失败的测试**：
  - `test_frame_is_stored_as_a_file_and_indexed`：临时目录；先发 hello，再发一帧合法的 `SVF1` 消息（JPEG 部分以 `b"\xff\xd8"` 开头）。期望文件存在、内容和 JPEG 一样；假库里出现 `frames` 的 upsert，`path` 是相对路径，`at` 按规则算出。
  - `test_bad_frames_are_rejected_and_nothing_written`：分别测魔数不对、头长度超过 512、JSON 不对、`seq` 不是整数、JPEG 不以 FFD8 开头、hello 之前就发帧。每种都返回错误字典，都不写文件、不写库。
  - `test_close_records_the_frame_count`。
- [ ] **Step 2: 运行，确认失败**。Run: `python3 -B -m unittest discover -s tests -p "test_vision_link.py" -v`
- [ ] **Step 3: 实现**：目录用 `os.makedirs(..., exist_ok=True)`；文件名 `f"{int(t_us)}-{int(seq)}.jpg"`；先写临时文件再 `os.replace`。
- [ ] **Step 4: 运行，确认通过**；再用 `.private/venv-ear` 跑 `test_hello_db.py` 和 `test_ear_protocol.py`，都要 OK。
- [ ] **Step 5: 提交** `feat(ear): store body-camera frames from the vision link (files + hello.frames)`

### Task 4: mind 接口 `/frames/latest`，代理放行

**Files:**
- Modify: `server/mind/api.py`、`server/mind/runner.py`（增加 `--recordings` 参数，默认 `~/soul-spike/recordings`，传给接口）、`server/mind/remote_setup.sh`（不改，使用默认值）、`web/monitor/mind.mjs`、`web/monitor/server.mjs`
- Test: `tests/test_mind_api.py`、`web/monitor/mind.test.mjs`

**Interfaces:**
- Produces:
  - `GET /frames/latest` 返回 `{"at", "session", "seq", "diff", "bytes", "w", "h", "today": 今天的帧数, "jpeg_b64"}`。没有帧时返回 404。文件缺失时返回同样的字段，但 `jpeg_b64` 为 null，并带 `"missing": true`。
  - 读文件前要确认真实路径落在 `recordings` 根目录之内。

- [ ] **Step 1: 写失败的测试**：
  - `test_latest_frame_returns_the_newest_image`：假库里放两条帧，临时目录里放文件，期望返回较新的那张，base64 解码后和文件一致。
  - `test_latest_frame_404_without_frames`。
  - `test_frame_path_outside_root_is_refused`：`path` 写成 `../x.jpg`，期望 `missing: true`。
  - Node：代理 `get('/frames/latest')` 返回 200，server 能把它转发出去。
- [ ] **Step 2: 运行，确认失败**。Run: `cd server && python3 -m unittest discover -s ../tests -p "test_mind_*.py"`；`cd web/monitor && node --test mind.test.mjs`
- [ ] **Step 3: 实现**
- [ ] **Step 4: 运行，确认通过**
- [ ] **Step 5: 提交** `feat(mind): GET /frames/latest — the newest body-camera frame the server received`

### Task 5: 页面——服务器收到的最新画面，视觉按钮改成三档

**Files:**
- Modify: `web/monitor/public/mind.html`、`web/monitor/public/mind-ui.js`（新增一块"服务器收到的画面"）、`web/monitor/public/switches-ui.js`、`web/monitor/public/switches.css`
- Test: `web/monitor/switches.test.mjs`、`web/monitor/mind.test.mjs`

**Interfaces:**
- Consumes: Task 4 的接口；本体遥测里的 `health.camera_enabled`，以及 M12 状态。
- Produces:
  - `switchView(...)` 的 `vision` 增加 `level`：`"off" | "upload" | "m12_only" | "unknown"`。`upload` 指 M12 在检测而且摄像头开着；`m12_only` 指 M12 在检测但摄像头关着。
  - `visionRequests(level, robot)`：
    - `off`：`[{kind:'control',body:{action:'camera',enabled:false,boot}}, {kind:'control',body:{action:'m12',enabled:false,boot}}]`
    - `upload`：先 `camera true`，再 `m12 true`。
  - 视觉区域显示三个按钮 `#sw-vision-off`、`#sw-vision-upload`、`#sw-vision-recognize`；最后一个固定禁用，提示"第 3 步：识别（待做）"。
  - 情景页的画面块：
    - 每 15 秒拉一次 `/api/mind/frames/latest`，用 `URL.createObjectURL(new Blob([bytes],{type:'image/jpeg'}))` 显示。CSP 只允许 `blob:`，不允许 `data:`。
    - 换图时释放旧的 URL。
    - 显示时间、变化值、今天张数。

- [ ] **Step 1: 写失败的测试**：
  - `vision level upload needs both camera and M12`
  - `m12 only when the camera is off`
  - `vision requests: camera before m12`
  - `recognize is never clickable in this step`
  - `frame panel markup has no style attributes`（沿用 CSP 测试的写法）
- [ ] **Step 2: 运行，确认失败**。Run: `cd web/monitor && node --test switches.test.mjs mind.test.mjs`
- [ ] **Step 3: 实现**
- [ ] **Step 4: 运行，确认通过**：`node --test *.test.mjs` 全部通过。
- [ ] **Step 5: 提交** `feat(monitor): vision switch 关 / 上传 / 识别(待做); 情景页 shows the server's newest frame`

### Task 6: 刷机、部署、现场验证

- [ ] **Step 1: 刷机**（只写 ota_1）：
  - 先运行 preflight，核对设备身份和 CRC，备份当前的 ota_1（0.4.74）；
  - 再用 make_artifact 生成产物，deploy 写入并独立校验；
  - 本体重启后，确认遥测里版本是 `collector-0.4.75`。
- [ ] **Step 2: 部署服务器**（要等用户说"部署"）：先 `server/ear/deploy.sh`，再 `server/mind/deploy.sh`，然后重启本机观察台。
- [ ] **Step 3: 静止画面**：页面选"上传"，人离开镜头 2 分钟。期望 `hello.frames` 里这段时间只有连接后的 1 张，最多再多 1 张（光线变化）。
- [ ] **Step 4: 有变化**：在镜头前活动 1 分钟。期望大约每 10 秒 1 张，共 5–6 张；文件存在；情景页的画面块显示最新一张。
- [ ] **Step 5: 关闭**：选"关"。期望之后没有新帧；M12 离线后，通道在约 30 秒内关闭。
- [ ] **Step 6**：把实测的变化值分布记下来（静止时和有人活动时的 diff 各是多少），必要时调整阈值。更新交接记录。
