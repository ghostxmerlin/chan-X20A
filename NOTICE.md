# 来源与许可

Mac 情绪试验使用 MLX / MLX-VLM、Qwen3-VL 与 OpenCV YuNet，模型来源、固定 revision 和校验值见 [emotion/models.json](emotion/models.json)，依赖版本见 [emotion/requirements.lock](emotion/requirements.lock)。模型仅下载到本机 `.tools/`，不重新分发权重，遵循各上游许可。分类提示借鉴本机 haro-resource-kit-20261028 的 `emotion_prompt.txt` 原型设计；其 REUSE 声明允许修改这些原创文本用于原型，不意味着已复用 Emotion-LLaMA 的实现。

准备中的 0.4.9 首页情绪文字使用 Adobe Source Han Sans SC Regular 的小字集，按 SIL OFL 1.1 分发，完整声明见 [SourceHanSans-OFL.txt](docs/licenses/SourceHanSans-OFL.txt)。字体许可不被本项目 MIT 替代。

chan-X20A 基于 [M5Stack StackChan](https://github.com/m5stack/StackChan)，固定开发起点为 `f901cc2969189f3f0e96af1442c0f96e77e1e2c1`。Git 历史保留；为容纳采集 App、测试和项目文档，将上游完整源码放在 `upstream/StackChan/`。

- 上游固件许可证：[MIT](upstream/StackChan/firmware/LICENSE)，Copyright (c) 2026 M5Stack Technology CO LTD。
- 上游遥控器与服务端许可证分别见 [remote/code/LICENSE](upstream/StackChan/remote/code/LICENSE)、[server/LICENSE](upstream/StackChan/server/LICENSE)。
- 包含的驱动与第三方代码保留原文件头和原许可证；根目录 LICENSE 不覆盖或替换第三方许可。
- 除另有文件头许可声明的第三方派生实现外，新增的 collector 固件、Android 观察台、项目脚本与文档使用根目录 [MIT License](LICENSE)。

ESP-IDF 固定 5.5.4；构建依赖版本见 `upstream/StackChan/firmware/repos.json`、`dependencies.lock`。依赖在本地构建时下载，不随本项目重新打包其工具链或私有构建产物。

0.4.7 人脸框使用 Espressif 官方 [ESP-DL v3.3.0](https://github.com/espressif/esp-dl/tree/v3.3.0) 和 `human_face_detect 0.4.1` 的 MSR/MNP 模型，固定版本经组件管理器下载，原MIT许可保留在各组件LICENSE中。新组件及校验值记录在dependencies.lock，原有组件的版本与哈希保持不变。项目构建配置裁减不用的图像转换选项以适配原OTA应用容量，未修改下载组件源码或Flash分区。

屏幕笑面男图形为 [Wikimedia Commons 的 Ridanta-homo.svg](https://commons.wikimedia.org/wiki/File:Ridanta-homo.svg) 的绿色分层改版，原标识来自 Production I.G《攻壳机动队 SAC》；该文件页面标注 public domain / PD-ineligible。改版 SVG 和由其生成的 RGB565 首帧、文字旋转帧数据保留在 collector 目录；脸、帽子与外圈固定。

终端及图形文字使用 [JetBrains Mono v2.304](https://github.com/JetBrains/JetBrainsMono/tree/v2.304) 的 ASCII 位图子集，许可为 SIL OFL 1.1；完整声明保存在 `firmware/main/collector/artwork/JetBrainsMono-OFL.txt`。该字体许可不被本项目 MIT 许可替代。

已部署的 collector-0.4.3 包含 [BMM150 补偿实现](upstream/StackChan/firmware/main/hal/drivers/environment/bmm150_data.cpp)，派生自 [Bosch Sensortec BMM150 SensorAPI v2.0.0](https://github.com/boschsensortec/BMM150_SensorAPI)，保留其 BSD-3-Clause 许可、版权和免责声明；未替换或升级原有依赖。参考 `bmm150.c` 的 SHA256 为 `477270b71a7c7b47acf47a21a7bc0b972bdf8deb021a6597b44b6cf2a3f1e926`。此派生文件不由本项目 MIT 许可替代。BSD 声明如下，二进制分发亦须携带：

```text
Copyright (c) 2020 Bosch Sensortec GmbH. All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright
   notice, this list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright
   notice, this list of conditions and the following disclaimer in the
   documentation and/or other materials provided with the distribution.
3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT,
STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING
IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE.
```
