# 地点：一个来源 + /mouth 信息页协议（2026-11-15）

主人原话：“地点要搞对，删除所有可能钉死的地方，hello可以主动询问或我们自然语言输入。在左滑的信息页中显示并可以设置（选择常用的或未知）。”

## 1. 地点模型（服务端，mind/place.py）

- **唯一来源**：`hello.mind_labels` 里最新一条 `{kind: "place"}`（2026-11-14 网页“我在哪”已经在用的那条记录）。`label` 是自由文本（1–40 字），或 `null` = 未知。从没设过 = 未知。
- **没有默认地点**：`settings.place.default`（原来写着“办公室”）不再被任何提示词读取；场景 profile（office / home / friends）只管脱口秀话题，不再代表地点。
- **谁设的** `by`：`page` 网页（旧记录没有 by，也算 page）、`robot` hello 信息页上选的、`voice` 有人跟 hello 说的。
- **常用地点**：`hello.place {_id: "choices"}`，没设过时为 `办公室 / 家里 / 车上 / 饭店 / 户外`。最多 8 个，每个最多 24 字节 UTF-8（8 个汉字）。
- 所有需要地点的提示词都读同一行：知道 → “现在的地点：饭店（主人在 hello 身上选的，19:10 起，以此为准）”；不知道 → “现在的地点：不知道（……不要假定在办公室或桌上）”。主人写的环境说明改称“背景资料（主人平时的情况……不代表 hello 现在在那里）”。视觉（eye）不拿到地点，只描述画面，新增 `place_seen`（画面像什么地方，看不出写“看不出”）。

### 三种设置途径

| 途径 | 怎么走 | 花费 |
|---|---|---|
| 网页 / 接口 | mind `POST /place {label, source?}`；`label: null` = 未知 | 无 |
| hello 信息页 | 机器人 → ear `/mouth` `place_select` → mind `POST /place {source: "robot"}`（必须是常用地点之一，或 null） | 无 |
| 自然语言 | 对话模式里对 hello 说的句子（chat 门控通过、不是远处/自己的声音）；围观模式里 realtime 听写出的句子（含“在/到/这里”才问）→ mind 本地规则 `parse_told`：“我们现在在饭店”“到家了”“这里是湖边”“我在开车”；问句、过去/将来、“如果”句不算 | 无（不调模型） |

### hello 主动问

对话（chat）和围观（crowd）的提示词里，只有在 **地点未知**，或 **最近 3 分钟的视觉 `place_seen` 和当前地点是不同种类**（车 / 户外 / 吃饭的地方 / 家 / 办公，两边都认得出才算）时，才加一行【可以问地点】，而且 **每 10 分钟最多一次**（`hello.place {_id: "ask"}`）。没有这一行就不问。

### mind 接口

- `GET /place` → `{current: 文本|null, by: page|robot|voice|null, at: ms|null, choices: [...], rev: ms}`
- `POST /place {label: 文本|null, source: "page"|"robot"}` → `{ok, at, ...同上}`（网页只发 `{label}`，照旧可用）
- `POST /place/choices {choices: [...]}` 整个替换，或 `{add: 文本}` / `{remove: 文本}` → 同 GET
- `POST /place/told {text, source: "crowd"|"chat"}` → `{changed, label, state}`（ear 用）

## 2. /mouth 协议（firmware 照这个做）

现有帧类型（hello、tts.*、mode_report、mode_input、mode_result、chat_stage）不变。旧固件把 `place` 当未知类型忽略，不影响。

### ear → 机器人：`place`

时机：每次 /mouth 握手后（ear 的 hello 之后，hello 带 boot_id 的连接）发一次；之后 ear 每 5 秒问一次 mind（本机回环，不花钱），**内容变了才发**；机器人选择成功后立即发给所有连接。

```json
{"type":"place","current":"饭店","by":"robot","choices":["办公室","家里","车上","饭店","户外"],"at_ms":1794400000000}
```

| 字段 | 说明 | 上限 |
|---|---|---|
| `current` | 当前地点，`null` = 未知；可能不在 choices 里（网页或语音设的自由文本） | 48 字节 UTF-8（按字符边界截断） |
| `by` | `page` / `robot` / `voice` / `null` | — |
| `choices` | 常用地点，按顺序显示 | 最多 8 个，每个 ≤ 24 字节 UTF-8 |
| `at_ms` | 设置时间（服务器 epoch ms），可不用 | — |

- 紧凑 JSON，键是 ASCII，字符串是原样 UTF-8（不是 `\u` 转义）。整帧按构造 ≤ 约 400 字节，保证 ≤ 1600 字节（机器人 `kMaxMessage`）。
- 机器人收到就整体替换显示；ear 断开时保留最后一帧并标为离线即可。

### 机器人 → ear：`place_select`

```json
{"type":"place_select","seq":12,"choice":"车上"}
{"type":"place_select","seq":13,"choice":null}
```

- `seq`：机器人自己的递增整数（没有 seq 的会被丢弃）。`choice`：choices 里的一项原文，或 `null` = 未知。整帧 ≤ 100 字节，在机器人 `kOutMax 320` 以内。
- 一条连接同一时间只处理一个；处理中再发的直接回 busy。不转发给 soul-tts，不占音频路径。

### ear → 机器人：`place_result`

```json
{"type":"place_result","seq":12,"code":"ok","toast":"PLACE SET"}
```

| code | toast | 含义 |
|---|---|---|
| `ok` | `PLACE SET` | 已设置；随后会收到新的 `place` 帧 |
| `bad_choice` | `NOT IN LIST` / `BAD CHOICE` | 不在常用地点里（列表刚被改过），或 choice 不是文本；地点没变，继续显示上一帧（列表变了的话 5 秒内会来新帧） |
| `busy` | `BUSY` / `MIND SLOW` / `MIND OFFLINE` | 上一个还在处理，或 mind 没回答；地点没变 |

toast 是 ASCII，≤ 32 字节（同 mode_result）。

## 3. 信息页建议（给 firmware）

左滑信息页显示“地点：饭店”（未知显示“地点：未知”，by 为 voice 时可加小字“听到的”），下面列 choices 加一项“未知”，点一下发 `place_select`，等 `place_result`（3 秒没回就当 busy）。
