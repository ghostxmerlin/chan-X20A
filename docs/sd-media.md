# SD 眼睛和台词资源

collector-0.4.30 将 40 段台词移到 SD；固件只保留目录、台词文本、长度和 SHA256。声音编码与 0.4.26 相同：16 kHz IMA，播放前恢复为原时长的 24 kHz PCM。没有裁剪或重新挑选台词。

## 安装

当前固定 IDF/FatFs 配置支持 FAT32，不支持 exFAT。固件不自动格式化卡。卡挂载成功后，通过已经绑定并验证身份的 Wi-Fi 连接安装，无须拔卡：

```sh
python3 scripts/prepare-voice-library.py /path/to/voice/outputs/stackchan_playable_80.json
node scripts/install-voice-assets.mjs
node scripts/install-eyes-assets.mjs
```

大批量传输时可以先用 `node web/monitor/service.mjs stop` 暂停本机监视服务，完成后用 `node web/monitor/service.mjs start` 恢复，减少并发网络占用。设备本体继续运行。

安装器从本机私密绑定读取地址和令牌，先做 HMAC 身份验证；令牌经 stdin 传给系统 curl，不放命令参数。上传绑定当前 boot，断线或结果未知立即停止，不自动重试。重新运行会校验并跳过已经一致的文件。

- 台词：`/sdcard/soul/voice/PLAY-001.adpcm` 至 `PLAY-080.adpcm`，共 2,288,128 字节（collector-0.4.56 起 80 条；前 40 条与原文件逐字节一致）。条数由 `voice_policy.h` 的 `kVoiceCount` 决定，准备脚本校验二者一致；每条的回应情绪见 `config/voice-emotions.json`。
- 眼睛：`/sdcard/soul/{v05,v14,v18}/*.soul`，90 个文件，共 10,384,688 字节。
- 设备只接收固件白名单中的编号、长度及哈希；HTTP 不接受文件路径。先写 `.part` 并读回校验，再重命名；替换既有文件时保留 `.prev`。
- 屏幕中文字库（collector-0.4.95-font 起）：`/sdcard/soul/font/cjk16.{0,1,2}.bin`（16 px、4 bpp，533,806 字节）、`cjk24.{0,1,2}.bin`（24 px、2 bpp，581,043 字节），另有同目录 `LICENSE-OFL.txt`（4,463 字节）。见下节。

## 屏幕中文字库

思源黑体（Adobe Source Han Sans 2.005R，区域子集 CN，Medium；与 Noto Sans CJK SC 同一设计），SIL OFL 1.1。字集为 ASCII、GB2312 第 1/3 区（中文标点、符号、全角字符）、GB2312 一级汉字 3,755 个及 `config/ui-font-extra.txt`，共 4,040 字。字形不进应用镜像：`scripts/generate-cjk-font.py` 用 lv_font_conv 1.5.3 生成 LVGL 二进制字体，再转成 HCJK 容器（格式见 `scripts/ui_font_tools.py`），按安装器的单文件上限切成 ≤256 KiB 的分片，放在被忽略的 `assets/ui-font/sdcard/`；`assets/ui-font/manifest.json` 与固件 `ui_font_catalog.generated.h` 固定每片长度和 SHA-256。

```sh
python3 scripts/generate-cjk-font.py --fetch .cache/ui-font/source   # 下载官方发布包，核对 zip/OTF/许可 SHA-256 后生成
node scripts/install-font-assets.mjs                                 # 先装许可，再逐片上传；已一致的跳过
```

开机建屏约 10 秒后（让开机先用屏幕和触摸；每个 256 KiB 分片读入时持 LVGL 锁约 0.8–1 秒），PSRAM 栈的 `ui_font` 任务先逐片 stat，某一字号的分片齐全且长度正确才逐片读入一块 PSRAM 缓冲（每片照常持 LVGL 锁并排空 LCD DMA、核对 SHA-256），再检查容器边界，才交给 LVGL；位图和码表就地使用，只另配字形表（各约 65 KB PSRAM）。没有卡、缺片或校验失败时屏幕保持 ASCII 字体，`info.ui_font.variants[].error` 给出原因；装完最后一片后无需重启即加载。字集变更（改 `config/ui-font-extra.txt`）需要重新生成、重编固件并重装分片，`tests/test_ui_font.py` 会指出固件字符串中不在字集里的字。

## 播放和总线

网页、ASR open、听觉情绪回应共用同一台词库。所选台词完整读入 PSRAM 并校验后，解码为 PCM，随后才启用扬声器。缺卡、缺文件或内容校验失败会停止本次播放并显示错误，不回退到固件音频。ASR open 保留自接受时刻起至少 800 ms 的等待。

LCD DC 和 SD MISO 共用 GPIO35。SPI3 初始化与固定 IDF LCD 的 DC 释放保持不变。实际上传曾触发 SPI running-command 断言；文件操作现在先获取 LVGL 锁，并以无命令的 `esp_lcd_panel_io_tx_param(-1)` 排空已排队的 LCD DMA，才开始 SD I/O。锁或排空失败不会读写 SD；每个文件结束即解锁。安装或首次加载期间会短暂停留当前画面。眼睛整段动画缓存到 PSRAM；声音播放期间也使用内存 PCM，持续播放不需要逐帧读卡。SD 文件操作共用锁。上传使用开机创建的单个常驻 worker、单槽队列与16 KiB PSRAM栈；每次请求正常返回并释放响应JSON后才解除忙状态，避免逐文件任务创建、自删清理任务的内部内存峰值以及跳过C++析构导致的泄漏。安装器在下一文件前等待设备解除忙状态。

接口：`POST /v1/voice/assets`，原 Bearer、当前 `X-Boot-Id`、`X-Voice-Id: 1..80` 和二进制内容。使用与眼睛安装相同的互斥上传门和有界接收。`voice_library.storage=sd` 表示资源位置，不代表所有文件已安装；安装器逐文件读回校验和实际播放结果才是验收依据。

播放同样由开机创建的常驻 worker 执行，使用16 KiB PSRAM栈；避免两路麦克风订阅及眼睛运行后，临时创建播放任务因内部内存碎片失败。HTTP PCM、网页台词、ASR与后端情绪共用原播放互斥门，忙时拒绝或按既有回应策略排队；不会同时启用两个输出。遥测的`playback.worker_ready`和`worker_stack_free_bytes`分别表示任务可用与执行后的栈余量。

眼睛解码任务也使用16 KiB PSRAM栈，给DMA和网络保留内部内存。另有已实机捕获的IDF 5.5.4缺陷：SPI接收缓冲申请失败时，驱动从未赋值的临时指针复制，导致空指针崩溃。`scripts/prepare-spi-driver.py`校验固定原文件SHA256，在构建目录生成仅修正失败清理分支的副本；CMake将该副本编入原SPI组件，原IDF目录、版本和依赖锁均不修改。失败时只释放已申请的临时TX/RX内存，不回拷RX；成功路径保持。实际驱动函数的主机故障注入覆盖分配失败、分配后同步失败、成功回拷和零泄漏，`tests/test_spi_allocation.py`可单独运行。此保护不代表内部内存耗尽后SD操作仍能成功。
