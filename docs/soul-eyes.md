# Soul 三款正面眼睛：使用与接入说明

资源位置：[`assets/soul-eyes/v2-front/`](../assets/soul-eyes/v2-front/README.md)。collector-0.4.27 已实现后台解码、首页播放、网页控制和 ASR 台词配表情。实际设备部署和验收结果以[最新交接](session-handoff.md)为准。

## 设备使用（0.4.27）

- 在监视终端的“眼睛表情”选择款式，点“启用所选款式”；可以选择30种状态、播放一次、停止回待机或恢复原首页。
- ASR `open` 仍等待800毫秒避开模组OK；成功提交首块台词PCM后才触发表情。台词结束回idle；网页点播台词不触发此自动表情。
- 新开机保留原首页；首次ASR台词可自动启用v05。点“恢复原首页”会关闭本次开机的自动启用，重新启用款式后恢复联动。设置仅在RAM。
- 页面/熄屏/旧爱心叉号优先，其次手动、ASR、idle。切页或熄屏取消台词表情，返回只播放idle，不自动切页或点亮屏幕。读卡失败不影响声音播放。
- 40条实际PLAY台词使用 [`eyes-voice-map.json`](../assets/eyes-voice-map.json)；这是按台词语气做的近似编配，尚未逐条听校，不使用旧300条候选的编号。

### 不拔卡安装

固件启动时只尝试一次挂载现有FAT卡，不格式化。确认`storage_ready=true`后，在仓库根目录运行：

```sh
node scripts/install-eyes-assets.mjs
```

使用已有`.private/stackchan-monitor.json`身份绑定，经Wi-Fi逐个传输90个资源，共10,384,688字节；无须拔卡。先验本地SHA256，再验证设备身份；设备核对boot、白名单、大小和SHA256，并回读校验后改名。相同内容直接验证跳过；不同旧文件保存一次`.prev`，已有备份则拒绝覆盖。网络结果未知时停止，不自动重试；手动重跑会重新核对文件。该安装器不会启用眼睛或改变页面。不接受任意路径或格式化请求。

若`storage_ready=false`，页面仍保留原首页，不能凭音频可播放推断存在SD卡：40条语音驻留应用Flash，眼睛存于SD。卡插入后需用户选择合适时机重新启动以挂载，本版不热插拔重挂载。

### 协议与内存

鉴权`GET /v1/eyes`返回目录；`GET /v1/eyes/status[?request_id=<UUID>]`返回与遥测`eyes`同源的快照。`POST /v1/eyes`强制当前`X-Boot-Id`，最多384字节：

```json
{"op":"configure","request_id":"12345678-1234-4234-8234-123456789abc","enabled":true,"style":"v05"}
```

Play使用`state`和`mode:"once"`，或`mode:"loop",loop_for_ms:1000..30000`；Stop只有`op`和新UUID。每次新意图使用新UUID；16条/60秒内相同ID/内容返回原结果，不重播，冲突409。202表示接收，`submitted_seq`/`rendered_seq`表示软件提交/渲染，不证明物理显示效果。

安装入口`POST /v1/eyes/assets`同样鉴权和boot绑定；`X-Eyes-Style/State`只接受内建ID，正文为对应`.soul`原始字节，最多256KiB。使用独立有界后台接收/安装任务。

播放器占用835,584字节PSRAM：两份256KiB资产缓存、两份RGB565帧和4KiB scratch。后台读卡/校验/解码，LVGL线程只绑定就绪帧；旧帧到REFR_READY后才能复用。按单调时钟跳过迟帧，不积压播放。LCD与SD共享SPI3，依赖固定IDF5.5.4逐事务释放DC/MISO和DMA串行化，构建检查驱动哈希。

以下保留资源预览与移植说明；设备集成以本节和实现代码为准。

## 1. 先查看效果

在仓库根目录执行：

```sh
open assets/soul-eyes/v2-front/index.html
```

或直接用浏览器打开该文件。预览页不需要服务端，可选择全部 30 种状态，三款同步播放；也可按语音主题筛选建议表情。暂停、重播和 GIF／逐帧表入口都在页面中。

完整对比视频为 [`previews/comparison.mp4`](../assets/soul-eyes/v2-front/previews/comparison.mp4)，约一分钟。

| 款式 ID | 设计 | 中性基准 |
| --- | --- | --- |
| `v05` | 暖金色猫眼，黑色竖椭圆瞳孔；正面等大 | [v05.png](../assets/soul-eyes/v2-front/canonical/v05.png) |
| `v14` | 正面实心绿色机甲眼，柔和光晕，无瞳孔 | [v14.png](../assets/soul-eyes/v2-front/canonical/v14.png) |
| `v18` | 奶油白眼白、大黑瞳；正面等大 | [v18.png](../assets/soul-eyes/v2-front/canonical/v18.png) |

每款 30 段，每段 8 帧，合计 90 段／720 帧，均为 320×240 黑底。眨眼或单眼睑变化可以不对称，但不采用侧脸透视。每段首尾使用该款统一中性帧，便于切换。

## 2. 选择状态

设备文件路径规则为：`soul/<款式>/<状态>.soul`。例如：

```text
soul/v05/happy.soul
soul/v14/determined.soul
soul/v18/comforting.soul
```

| 分组 | 状态 ID 与中文含义 |
| --- | --- |
| 基本情绪 | `idle` 待机、`happy` 开心、`hurt` 扎心、`sad` 伤心、`angry` 愤怒、`affectionate` 亲密、`laughing` 嘲笑、`goofy` 搞怪、`sarcastic` 嘲讽、`helpless` 无奈 |
| 交流互动 | `greeting` 迎接、`grateful` 感谢、`apologetic` 歉意、`comforting` 安慰、`encouraging` 鼓励、`determined` 坚定／认真、`refusing` 拒绝、`pleading` 恳求、`relieved` 释然、`farewell` 告别 |
| 反应心境 | `surprised` 惊讶、`confused` 困惑、`thinking` 思考、`expectant` 期待、`curious` 好奇、`shy` 害羞、`worried` 担忧、`scared` 害怕、`sleepy` 困倦／疲惫、`proud` 得意 |

时序以 `states.json` 和 `.soul` 帧表中的毫秒值为准，不要把八帧统一按固定 FPS 播放。PNG、GIF、MP4 用于编辑与审阅；设备输入为 `.soul`。

## 3. 准备 SD 卡文件

将资源包内 `sdcard/soul/` 整个目录复制到 microSD 根目录，卡内结果应为：

```text
/soul/v05/idle.soul
/soul/v05/happy.soul
...
/soul/v14/idle.soul
...
/soul/v18/idle.soul
...
```

全部动画为 **10,384,688 字节，约 9.90 MiB**。当前仓库的 `assets` Flash 分区仅有 4 MiB，因此不能将整套直接塞入现有分区；本次没有更改分区表。

collector-0.4.27 挂载点为 `/sdcard`；保持原有卡内容，不自动格式化或替换首页。优先采用本文开头的 Wi-Fi 安装方式。

## 4. 接入当前 ESP-IDF／LVGL 固件的方式

本项目固定 ESP-IDF **5.5.4**，当前依赖为 LVGL **9.4.0**、esp_lvgl_port **2.7.2**。资源目录独立于现有固件构建，便于后续适配。

### 解码接口

[`decoder/soul_animation.h`](../assets/soul-eyes/v2-front/decoder/soul_animation.h) 只依赖 C++11 标准头文件。未来适配层需要：

1. 实现 `soul::Reader::size()` 和 `readAt(offset, buffer, length)`，由实际选定的文件系统或存储介质提供数据。
2. 调用 `soul::Animation::open(reader)`，成功后读取 `frameCount()` 和 `frame(i).durationMs`。
3. 使用 `decode(reader, i, pixels, 76800, scratch, scratch_size)` 解码帧，检查返回值。错误可用 `soul::errorName()` 转成说明；失败帧不能交给显示层。
4. 将就绪的 RGB565 帧交给当前 LVGL 显示层，按各帧毫秒时长调度；切换状态时回到对应中性帧。

单个解码输出缓冲是 76800 个 `uint16_t`，即 **153600 字节**；输入可使用 **4096 字节**流式缓冲，无需将整个动画放入 RAM。若采用双帧缓冲，图像本身需要 307200 字节 PSRAM，另外还需计入 LVGL、相机、音频等开销。

### 当前代码接入点与约束

- [`collector/screen.cpp`](../upstream/StackChan/firmware/main/collector/screen.cpp) 创建首页并执行动画更新。当前笑面男图像为 260×240、位置 `(30,0)`；新眼睛帧为完整 320×240，后续显示位置应采用 `(0,0)`。
- [`collector/collector.cpp`](../upstream/StackChan/firmware/main/collector/collector.cpp) 通过 `LvglLock/Unlock` 初始化，并以 20 ms LVGL timer 调用 `screen_tick`。SD 读入与解码应放在合适的工作任务中，避免阻塞该回调；仍需遵守本项目至少等待一个 tick 的约定。
- [`stackchan_display.cc`](../upstream/StackChan/firmware/main/hal/board/stackchan_display.cc) 使用 RGB565，已有显示端字节交换设置。解码器输出数值 RGB565；接入时沿用此链路，不要再照搬 Arduino 的 `M5.Display.setSwapBytes(true)`。
- **LVGL 会保留图像数据指针。** 图像描述符和像素数据必须有足够生命周期，不能在渲染尚未完成时覆盖同一缓冲。后续应安排受控缓冲交接或双缓冲，并明确旧缓冲何时可复用。
- [`main/assets/assets.cpp`](../upstream/StackChan/firmware/main/assets/assets.cpp) 目前不识别 `.soul`。不能通过改后缀为 `.bin` 或复制到该目录实现播放；新增适配层前，不会自动参与现有资源加载。
- 当前首页已有局部刷新逻辑。接入眼睛后应评估实际变更区域，并进行动画、摄像头、音频和遥控并发测试；主机预览流畅不能替代这一验收。已知性能背景见 [collector-validation.md](collector-validation.md)。

原制作工程的 `player/` 是 **独立 Arduino／M5Unified 演示固件**，其构建通过不等于当前 collector 已接入。本次只导入可移植解码器，没有把 PlatformIO 工程或刷机命令作为 chan-X20A 的启用步骤。后续本项目固件仍使用 `./scripts/idf.sh build`。

## 5. 使用语音候选映射

[`voice-coverage/candidate-animation-map.json`](../assets/soul-eyes/v2-front/voice-coverage/candidate-animation-map.json) 为 300 条候选保留原始 ID、`theme`、`emotion`、`intent`，给出 `primary_animation` 和 `alternates`。用候选 ID 查到状态后，拼接所选款式的资源路径即可；这只是数据层对应关系，目前没有新增固件控制协议或 HTTP API。

覆盖 20 个主题。72 条候选暂无具名情绪，按意图或主题兜底；所有映射都标注原声仍待核对。不要把“所有编号都有映射”当作“每句情绪已经验证”，也不要假定该资源包包含声音文件。

## 6. 校验资源与解码器

校验本资源目录所有文件：

```sh
cd assets/soul-eyes/v2-front
shasum -a 256 -c SHA256SUMS.txt
```

在仓库根目录运行已有 C++ 测试，输出写入被 Git 忽略的 `.cache/`：

```sh
mkdir -p .cache/soul-eyes
clang++ -std=c++11 -Wall -Wextra -Werror \
  -fsanitize=address,undefined -fno-omit-frame-pointer \
  -I assets/soul-eyes/v2-front/decoder \
  assets/soul-eyes/v2-front/decoder/tests/decoder_test.cpp \
  -o .cache/soul-eyes/decoder_test
.cache/soul-eyes/decoder_test assets/soul-eyes/v2-front/sdcard/soul/*/*.soul
```

测试检查格式、边界、RGB565、CRC 及所有实际动画。运行环境需要支持 ASan／UBSan 的 clang++；资源预览本身不需要安装编译器。

## 7. 本次交付状态

已复制三款正面基准、90 段设备动画、720 张 PNG、90 个 GIF、对比视频、状态／语音映射和解码源码，并对复制结果与源文件逐项校验。来源、文件数和检查结果见资源目录内 `import.json`。

以上为资源导入时的历史记录。当前 collector 集成、主机测试和设备部署边界见本文开头及最新交接。 原始故事板、生成提示词和制作工具保留在 `~/soul_ui/animation-v2/`。
