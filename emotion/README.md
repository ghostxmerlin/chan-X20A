# Mac 情绪观察试验

当前阶段先在 Mac 测试，机器人仍运行 collector-0.4.8。浏览器打开 <http://127.0.0.1:8765/>，看到机器人 Wi-Fi 画面、实时转写和角落的情绪推测。必须保持 Mac 运行且能访问机器人网络；尚未配置登录自启。

音频由机器人采集，经 Mac 送往 [AIHubMix 实时转录](https://docs.aihubmix.com/cn/api/realtime-transcription) 的 `gpt-live-transcribe`，采用 24 kHz 单声道 PCM16 和手动分句。当前没有调用阿里听悟。没有进行多个 STT 模型的质量排行榜比较；选择此接口是因为支持流式中文并已通过真实接口测试。

图片与转写文字在 Mac 上由 MLX / Qwen3-VL-4B-Instruct 4bit 联合判断，YuNet 只检查人脸数量。实现沿用了先前 haro-resource-kit 的“证据不足则 unknown、自述与转述分开、纠正优先、严格 JSON”设计，**不是已经运行 Emotion-LLaMA / AffectGPT 权重**。仅单张图像和最近一句话，尚未分析语调、连续表情或长期情绪。

## 启停

本开发机已经完成安装与私有配置，在仓库根目录运行：

```sh
python3 scripts/emotion-service.py start
python3 scripts/emotion-service.py status
python3 scripts/emotion-service.py stop
```

`start` 使用 `.private/emotion-config.json`，进程与日志由当前用户管理，不修改系统登录项。更新 Python 代码后先 stop 再 start，浏览器刷新。页面的“暂停分析”关闭云转写并暂停推理，保留本地画面和声音电平。文本样例测试独立显示，不会伪装成现场判断。

## 其他 Apple Silicon Mac 的准备

Python 3.11、Apple Silicon 与足够的可用内存；本次在 16 GB Mac 实测。模型约数 GB，保存在忽略的 `.tools/`，不随仓库提交。

```sh
python3.11 -m venv .tools/emotion-venv
.tools/emotion-venv/bin/python -m pip install -r emotion/requirements.lock
.tools/emotion-venv/bin/python scripts/setup-emotion-models.py
```

将 `config.example.json` 复制到 `.private/emotion-config.json`，填入已有绑定的设备 ID、token 文件、地址文件与 AIHubMix key 文件路径。地址文件内容为 `http://机器人地址:8080`。凭据文件仅本机可读（权限 600）；不要将凭据写入源码、URL 或命令行参数。模型固定版本、来源和 YuNet 校验值见 `models.json`。`mac_preview:true` 不向机器人发送情绪标签；切换为 false 前必须完成情绪固件的实机验证。

## 休眠、数据与限制

- 从服务就绪或声音唤醒开始计时，只有新的、非空 STT 文字重置 600 秒期限。重连、心跳、重复文字和本地推理不延长期限；满 10 分钟主动关闭 WebSocket，停止云端音频发送。
- 休眠期间 Mac 保留本地采集与声音门限检测，安静 300 ms 后重新待命，连续约 80 ms 的明显声音触发连接。保留最近 1 秒音频用于连接后的前导；网络建立较慢时仍可能损失开头。这里是声音活动检测，不是语音或说话人识别；持续噪声会影响唤醒。
- 相机约 1 fps；每轮推理结束后间隔 3 秒。无脸时仅使用文字，多脸时返回不确定。自述优先于表情，普通请求和否定句不强行判为平静；不确定和过期结果明确显示。
- AIHubMix 没有在此适配器提供逐字采样时间，使用本地音频分段末尾估计文字观察时间，分段最长约 6 秒。结果有 20 秒时效，不能将回包时刻冒充说话时刻。
- 画面和文字仅驻留内存，服务日志不写录音、照片、转写内容或密钥。状态文件只有计数、标签和连接状态。样例测试的输入也只驻留内存。云端的数据政策由提供商决定。
- 本机页面只监听 127.0.0.1，验证 Host/Origin；机器人先通过绑定设备随机挑战，再发送 Bearer。页面不获得 API key。
- 旧固件只有一路音频，启动前检查空闲；Mac 测试占用该路，暂不支持与手机采集同时运行。准备中的 0.4.9 将提供两路，但尚未部署或实机验证。

## 验证

```sh
.tools/emotion-venv/bin/python -B -m unittest discover -s tests -p 'test_emotion*.py' -v
python3 -B -m unittest discover -s tests -v
```

前一组在本机使用假网络验证 600 秒关闭、声音重开、AIHubMix 分句/增量文字、听悟清理、重采样和固件过期/乱序。真实 STT、真实模型与现场采集的结果分别记录在 [验证记录](../docs/collector-validation.md)，单元测试不替代实机验收。

机器人首页角落与 `/v1/emotion` 接口已经编译、做了本机 LVGL 验证；用户要求先测 Mac，因此当前没有写入设备。该后续步骤不包含舵机、声音表情或 NVS 设置修改。
