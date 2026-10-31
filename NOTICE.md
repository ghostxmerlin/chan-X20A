# 来源与许可

chan-X20A 基于 [M5Stack StackChan](https://github.com/m5stack/StackChan)，固定开发起点为 `f901cc2969189f3f0e96af1442c0f96e77e1e2c1`。Git 历史保留；为容纳采集 App、测试和项目文档，将上游完整源码放在 `upstream/StackChan/`。

- 上游固件许可证：[MIT](upstream/StackChan/firmware/LICENSE)，Copyright (c) 2026 M5Stack Technology CO LTD。
- 上游遥控器与服务端许可证分别见 [remote/code/LICENSE](upstream/StackChan/remote/code/LICENSE)、[server/LICENSE](upstream/StackChan/server/LICENSE)。
- 包含的驱动与第三方代码保留原文件头和原许可证；根目录 LICENSE 不覆盖或替换第三方许可。
- 新增的 collector 固件、Android 观察台、项目脚本与文档使用根目录 [MIT License](LICENSE)。

ESP-IDF 固定 5.5.4；构建依赖版本见 `upstream/StackChan/firmware/repos.json`、`dependencies.lock`。依赖在本地构建时下载，不随本项目重新打包其工具链或私有构建产物。

屏幕笑面男图形为 [Wikimedia Commons 的 Ridanta-homo.svg](https://commons.wikimedia.org/wiki/File:Ridanta-homo.svg) 的静态绿色改版，原标识来自 Production I.G《攻壳机动队 SAC》；该文件页面标注 public domain / PD-ineligible。改版 SVG 与固件 A8 像素掩码保留在 collector 目录。

终端及图形文字使用 [JetBrains Mono v2.304](https://github.com/JetBrains/JetBrainsMono/tree/v2.304) 的 ASCII 位图子集，许可为 SIL OFL 1.1；完整声明保存在 `firmware/main/collector/artwork/JetBrainsMono-OFL.txt`。该字体许可不被本项目 MIT 许可替代。
