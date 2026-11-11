# hello 开口：/mouth 流式播放，页面点一下说一句

日期：2026-11-11。用户在 `voice/` 里已经做好了 TTS 服务（`voice/tts_service`，百炼 Qwen-Audio 3.1 复刻音色，含 `/mouth` 协议的服务端和设备模拟器）。本文写怎么把它接进 hello。

## 1. 已定事项（用户 2026-11-11）

1. **直接做固件的 `/mouth` 流式播放**，不走 Mac 中转。协议沿用 `voice/docs/tts-streaming.zh-CN.md`：Opus，24 kHz 单声道，每帧 60 ms，`SPK1` 包头，消息有 `tts.start / end / stop / playing / progress / done`。
2. **不让 hello 自己决定什么时候插嘴**。由页面触发：页面上有几个风格按钮（如"吐槽""鼓励"），点一下，就根据**当前情景、最新的视觉、最新的听觉**生成一句话并播放。
3. **风格靠角色文件定义**，例如"毒舌的脱口秀演员""程序员安慰师小姐姐"。

## 2. 结构

```
页面按钮 ──POST /api/mind/say {role}──▶ mind 接口 /say
   mind 读：环境说明 + 此刻情景(mind_scenes) + 最新 eye 结果 + 当前对话纪要 c1 + 最近几句原话
   luna 写一句口语 ──▶ soul-tts  POST 127.0.0.1:52750/v1/say {text, voice, instruction, rate}
soul-tts 合成（流式）──Opus──▶ /mouth WebSocket ──(ear 按路径转发)──▶ 本体
本体：PSRAM 抖动缓冲 → Opus 解码 → I²S 播放；占用播放闸门，ear 收到的是静音
```

- **soul-tts**：放到服务器上，作为新的运行单元（`soul-tts`），`player=stream`。接口只开在服务器本机（127.0.0.1:52750），mouth 监听 127.0.0.1:52751。代码来自 `voice/tts_service`，部署时复制过去。
- **ear 转发 `/mouth`**：本体连 `ws://<听觉服务的主机:端口>/mouth`，带设备令牌（就是听觉用的那个）。ear 校验令牌以后，把连接原样双向转发到 127.0.0.1:52751。所以不开新端口，云服务器的安全组不用动。
- **mind `/say`**：按角色和当前情景生成一句话，调用 soul-tts，返回这句话的文字、编号、状态，然后记下来（`hello.utterances`，并在 `mind_runs` 里写一条 `step: "say"`）。
- **页面**：情景页加"让 hello 说一句"区域，每个角色一个按钮。旁边显示刚说的那句、状态（合成中、播放中、说完了）和花了多少钱。再加一个"别说了"按钮（调用 `/v1/stop`）。

## 3. 角色文件

放在 `config/roles/<id>.json`，进仓库：

```json
{"id": "roast", "button": "吐槽", "name": "毒舌脱口秀演员",
 "persona": "你是一个毒舌但不伤人的脱口秀演员，借眼前的场景抖一个包袱……",
 "rules": ["一句话，不超过 30 字，口语", "吐槽事和场面，不嘲笑人的长相、身材、能力", "不点名同事"],
 "voice": "tang", "instruction": "用傲娇、蔫儿坏、一本正经地逗乐的语气说。", "rate": 1.15}
```

先做两个角色：
- **吐槽**：毒舌脱口秀演员，音色用汤师爷。
- **鼓励**：程序员安慰师小姐姐，音色用无限，语气温柔。

以后加角色，只要加一个文件，不用改代码。

## 4. 生成一句话

- 模型：luna，`reasoning_effort: low`，严格 json_schema，输出 `{"text": "..."}`。
- 输入：
  - 环境说明；
  - 现在的时间；
  - 此刻情景的"在干什么"和摘要；
  - 最新的 eye 结果（5 分钟以内的才用）；
  - 当前对话纪要 c1（正在进行、或刚结束不到 5 分钟的才用）；
  - 最近 2 分钟别人说的原话（最多 10 句）；
  - hello 最近说过的 5 句（避免重复）。
- 约束：一句话，不超过 30 字，口语，不加引号和括号；不复述原话；内容要和此刻的情景有关。

## 5. 回声和自声

- 播放期间，本体的播放闸门被占用，ear 把听觉输入（包括听悟）换成静音。这是现有机制。
- hello 说过的每一句，都加进 `selfvoice` 的比对列表（保留 1 小时）。如果有回声漏进来，就按 hello 自己的声音处理，不写进纪要、也不进情景。

## 6. 固件（collector-0.4.77）

照 `voice/docs/tts-streaming.zh-CN.md` 第 3、5 节改：

- **连接**：新开 `mouth_link`，独立的 WebSocket 任务，PSRAM 栈。URL 由听觉服务的地址换成 `/mouth` 路径得到，和 `vision_url` 的做法一样。
- **socket 上限**：`CONFIG_LWIP_MAX_SOCKETS` 从 16 调到 20。
- **播放**：
  - 播放闸门新增"流"这种声源；
  - PSRAM 抖动缓冲（上限 64 KB），先缓冲 180 ms 再出声；缓冲不够时补零，不关功放；
  - 收到 `tts.stop` 立刻清空缓冲；
  - 按协议回报 `playing / progress / done / stopped / error`。
- **解码**：`esp_opus_dec_*`（esp_audio_codec 里已经有，开了 `CONFIG_AUDIO_DECODER_OPUS_SUPPORT`），解码状态和解码任务的栈都放在 PSRAM。
- **PSRAM 优先**（用户规矩），只在必须的地方用内部 RAM。
- **刷机**：只写 ota_1，并做备份和独立校验（流程同 0.4.76）。

**风险**：今天已经看到听觉频繁掉线和 3% 的画面花屏，怀疑是 PSRAM 和 CPU 负担过重。`/mouth` 会再加一路负担。所以：
- 固件里，只有在流播放的时候才启动 Opus 解码，平时连接空着，不占多少资源；
- 验收时，同时看掉线次数和花屏比例有没有变差。

## 7. 开关与花费

- 没有"自动说话"，所以不需要常开的开关。每点一次按钮，花一次 luna（约 $0.0005），加上一次 TTS 合成（百炼按字数计费，单价待查）。
- 花费记在 `mind_runs`（`step: "say"`），TTS 的字数记在 `hello.utterances`。页面上显示今天说了几句、花了多少钱。

## 8. 分步

1. **服务器端**：部署 soul-tts，ear 转发 `/mouth`，角色文件，mind `/say`，页面按钮。用 `voice/tts_service/soul_tts/devsim.py` 冒充本体连 `/mouth`，把整条链路验证一遍。
2. **固件 0.4.77**：`/mouth`、Opus 播放。刷机，在实机上验证开口延迟、打断，以及回声是否被屏蔽。
3. 实测以后再调：角色的文字、句子长度、音量。

## 9. 待定

- 百炼 `qwen-audio-3.1-tts-flash` 的单价。
- 服务器上 soul-tts 用哪个 Python：沿用 `~/soul-spike/.venv`，它已经有 websockets 和 opuslib_next。
- 角色文件里的人设文字：先由 Claude 起草，用户来改。
