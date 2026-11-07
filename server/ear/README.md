# 耳朵服务端（server/ear）

这是 soul 的耳朵。机器人把麦克风音频经 WebSocket 推上来（目前由 Mac 测试客户端代替机器人），服务器负责切段、转文字、认人和分类声音，每 100 ms 回一个心跳帧，事件跟着心跳一起回来。耳朵只报告事实和置信档位，不做决定。

**状态（2026-11-07）：**

- 服务已在公网服务器上运行，但用的是临时 systemd 单元，服务器重启后不会自动起来。
- 机身固件还没接入，接口草案见 [soul-ear-interface.md](../../docs/soul-ear-interface.md)。
- 进展和后续计划见 [soul 进展与规划](../../docs/soul-roadmap.md)。

## 文件

| 文件 | 内容 |
| --- | --- |
| `ear_server.py` | 服务端。开头的注释写着协议：消息格式、心跳和事件 |
| `recorder.py` | 为每条连接存档：Ogg Opus 或 WAV 音频，加上事件、心跳、客户端控制消息和 `meta.json` |
| `sound_kinds.py` | 把 CED 的 AudioSet 标签归成约 30 类声音，并列出算作“人声”的标签 |
| `mic_client.py`、`debug.html` | Mac 测试客户端，以及它在 `http://127.0.0.1:8770` 提供的调试网页 |
| `mac-client.sh`、`deploy.sh`、`pull-recordings.sh` | 入口脚本，先读取仓库根目录的 `.env` 再运行；`_env.sh` 是它们共用的读取逻辑 |
| `remote_setup.sh` | 第一次部署时在服务器上安装 venv、模型和令牌 |
| `requirements-mac.txt` | Mac 客户端需要的 Python 包 |

## 处理链

1. **Opus 解码。** 16 kHz 单声道，60 ms 一帧，编码参数照抄 xiaozhi-esp32 v2.5.2。
2. **人声检测：** Silero（sherpa-onnx 导出版）。
   - 送进人声检测的音频先加 −70 dBFS 的底噪，用固定种子，重放结果可复现。这样可以防止被切成数字零的静音误触发。
   - 文字识别和声纹仍然用原始音频。
3. **文字、语言、情绪：** SenseVoice int8 2024-07-17。
4. **是谁：** CAM++ 3D-Speaker 中文声纹。开口约 1 秒先报一次，说完再报一次。档位：`known` ≥0.6，`unsure` 0.4–0.6，其余 `unknown`。
5. **像不像人声，以及声音分类：** CED-mini。人声类概率 ≥0.4 算像人声，0.2–0.4 说不清，<0.2 不像。声纹认出某人时也算像人声。
6. **声音事件：**
   - 1 秒窗口里最响的 100 ms 要比背景高 15 dB 才开新事件。背景取最近 60 秒音量的第 10 百分位。
   - 音乐要持续 3 秒才报；单下的敲击不报。
7. **门槛可调：** 客户端可以用 `config` 消息下发，字段有 `speaker_hi/lo`、`voice_hi/lo`、`sound_min_db`。

## 配置

真实配置放在仓库根目录的 `.env`，这个文件不提交。格式见 [`.env.example`](../../.env.example)。

| 变量 | 用途 |
| --- | --- |
| `SOUL_SERVER_SSH`、`SOUL_SERVER_DIR`、`SOUL_SERVER_UNIT` | 部署和拉录音用：服务器的 ssh 登录、部署目录、systemd 单元名 |
| `SOUL_EAR_URL`、`SOUL_EAR_TOKEN` | Mac 客户端连接的地址和令牌 |
| `SOUL_MAC_PYTHON` | 运行 Mac 客户端的 Python |
| `SOUL_RECORDINGS_DIR` | 录音存档根目录。留空时存到仓库内的 `.private/recordings`，这个目录不提交 |

服务器上还有几样东西只留在服务器，不进 Git：模型（约 300 MB）、`token`、`speakers.npz`（登记的声纹）和 `recordings/`。

## 在 Mac 上使用

以下命令都在仓库根目录运行。

1. **准备（只做一次）：**
   - 复制 `.env.example` 为 `.env`，填上真实值。
   - 安装 Opus 库：`brew install opus`。
   - 安装 Python 包：`uv pip install --python <SOUL_MAC_PYTHON> -r server/ear/requirements-mac.txt`。
2. **测试：**
   - `server/ear/mac-client.sh` 用 Mac 麦克风测试，会自动打开调试网页。
   - `server/ear/mac-client.sh --enroll 主人` 登记声纹。
   - `server/ear/mac-client.sh --file a.wav` 重放一段 16 kHz 录音。
3. **部署：**
   - 先运行 `server/ear/deploy.sh --dry-run`，看哪些文件有变化，这一步不改服务器。
   - 再运行 `server/ear/deploy.sh`，它会复制改动、重启服务，并等到服务重新开始监听。
   - 脚本只复制三个代码文件，不碰服务器上的模型、令牌、声纹和录音。
4. **拉录音：** `server/ear/pull-recordings.sh`。只增加、更新文件，两边都不删除。

## 服务器第一次安装

尖括号里的值换成 `.env` 中的实际值：

```sh
ssh <user@host> "sudo apt-get install -y libopus0"
ssh <user@host> "DIR=<部署目录> bash -s" < server/ear/remote_setup.sh
rsync -a server/ear/ear_server.py server/ear/recorder.py server/ear/sound_kinds.py <user@host>:<部署目录>/
ssh <user@host> "sudo systemd-run --unit <单元名> -p User=<user> -p Group=<user> \
  -p WorkingDirectory=/home/<user>/<部署目录> -p MemoryMax=3G -p CPUQuota=300% \
  -p Restart=on-failure -p RestartSec=3 \
  --setenv SOUL_TOKEN_FILE=/home/<user>/<部署目录>/token \
  /home/<user>/<部署目录>/.venv/bin/python /home/<user>/<部署目录>/ear_server.py \
  --host 0.0.0.0 --port <端口> --models /home/<user>/<部署目录>/models --threads 2"
```

`systemd-run` 创建的是临时单元，服务器重启后就没了。改成正式单元已列入计划。单元建好以后，代码再有改动就用 `deploy.sh` 更新。

- 看日志：`sudo journalctl -u <单元名>`
- 停止服务：`sudo systemctl stop <单元名>`
