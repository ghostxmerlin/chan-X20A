# Soul 三款正面眼睛资源

已选款式：**v05 金瞳正面、v14 圆角机甲、v18 小黑正面**。每款 30 种状态、240 帧，合计 90 段／720 帧；显示尺寸 320×240。

- [打开交互预览](index.html)：切换情绪、暂停／重播，按 20 个语音主题筛选建议表情。
- [观看完整对比视频](previews/comparison.mp4)。
- **[chan-X20A 使用与接入说明](../../../docs/soul-eyes.md)**。
- [语音映射说明](voice-coverage/README.md)。

| 目录／文件 | 用途 |
| --- | --- |
| `canonical/v05.png`、`v14.png`、`v18.png` | 三款正面中性基准原图 |
| `sdcard/soul/<款式>/<状态>.soul` | 设备动画，RGB565 RLE，合计约 9.90 MiB |
| `frames/<款式>/<状态>/00.png`…`07.png` | 320×240 PNG 帧 |
| `previews/` | 单段 GIF、逐帧总表和三款对比 MP4 |
| `decoder/soul_animation.h` | 无 Arduino 依赖的 C++11 解码器 |
| `decoder/tests/decoder_test.cpp` | 解码器边界／CRC／实际资源测试 |
| `states.json`、`manifest.json` | 30 状态、逐帧时长、全部动画大小及校验值 |
| `voice-coverage/` | 20 主题和 300 候选的建议映射；原声仍待核对 |
| `import.json`、`SHA256SUMS.txt` | 导入来源、逐文件哈希及校验记录 |

本目录是资源交付，**当前 collector 固件尚未接入这些动画**。仅复制 SD 文件不会改变现有首页。项目仍使用固定 ESP-IDF 5.5.4／LVGL；原制作工程的独立 Arduino 播放器没有作为本项目固件导入。

资源来自 `~/soul_ui/animation-v2`。本目录带有播放数据、PNG、预览、基准图和解码源码；生成故事板、提示词及制图工具仍保留在原制作工程。
