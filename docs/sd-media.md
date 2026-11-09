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

## 播放和总线

网页、ASR open、听觉情绪回应共用同一台词库。所选台词完整读入 PSRAM 并校验后，解码为 PCM，随后才启用扬声器。缺卡、缺文件或内容校验失败会停止本次播放并显示错误，不回退到固件音频。ASR open 保留自接受时刻起至少 800 ms 的等待。

LCD DC 和 SD MISO 共用 GPIO35。SPI3 初始化与固定 IDF LCD 的 DC 释放保持不变。实际上传曾触发 SPI running-command 断言；文件操作现在先获取 LVGL 锁，并以无命令的 `esp_lcd_panel_io_tx_param(-1)` 排空已排队的 LCD DMA，才开始 SD I/O。锁或排空失败不会读写 SD；每个文件结束即解锁。安装或首次加载期间会短暂停留当前画面。眼睛整段动画缓存到 PSRAM；声音播放期间也使用内存 PCM，持续播放不需要逐帧读卡。SD 文件操作共用锁。上传使用开机创建的单个常驻 worker、单槽队列与16 KiB PSRAM栈；每次请求正常返回并释放响应JSON后才解除忙状态，避免逐文件任务创建、自删清理任务的内部内存峰值以及跳过C++析构导致的泄漏。安装器在下一文件前等待设备解除忙状态。

接口：`POST /v1/voice/assets`，原 Bearer、当前 `X-Boot-Id`、`X-Voice-Id: 1..80` 和二进制内容。使用与眼睛安装相同的互斥上传门和有界接收。`voice_library.storage=sd` 表示资源位置，不代表所有文件已安装；安装器逐文件读回校验和实际播放结果才是验收依据。

播放同样由开机创建的常驻 worker 执行，使用16 KiB PSRAM栈；避免两路麦克风订阅及眼睛运行后，临时创建播放任务因内部内存碎片失败。HTTP PCM、网页台词、ASR与后端情绪共用原播放互斥门，忙时拒绝或按既有回应策略排队；不会同时启用两个输出。遥测的`playback.worker_ready`和`worker_stack_free_bytes`分别表示任务可用与执行后的栈余量。

眼睛解码任务也使用16 KiB PSRAM栈，给DMA和网络保留内部内存。另有已实机捕获的IDF 5.5.4缺陷：SPI接收缓冲申请失败时，驱动从未赋值的临时指针复制，导致空指针崩溃。`scripts/prepare-spi-driver.py`校验固定原文件SHA256，在构建目录生成仅修正失败清理分支的副本；CMake将该副本编入原SPI组件，原IDF目录、版本和依赖锁均不修改。失败时只释放已申请的临时TX/RX内存，不回拷RX；成功路径保持。实际驱动函数的主机故障注入覆盖分配失败、分配后同步失败、成功回拷和零泄漏，`tests/test_spi_allocation.py`可单独运行。此保护不代表内部内存耗尽后SD操作仍能成功。
