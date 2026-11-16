# hello 运行 / 审计日志：格式与查询

hello 的六个服务（ear / mind / eye / notes / memory / tts）把每件事记进 Mongo 库 `hello`。写入模块是 `server/common/hello_log.py`，读取模块是 `server/common/hello_log_query.py`（只用标准库，传入 pymongo 的 Database 即可）。

## 表

| 表 | 内容 |
| --- | --- |
| `log_YYYYMM` | 流水：一件事一条小记录 |
| `logbody_YYYYMM` | 正文：`{_id: 内容的 sha256, text}`，提示词、输入、输出全文，同一个月内按哈希去重 |

- 月份按北京时间（Asia/Shanghai）划分：11-30 23:59:59.999 在 `log_202611`，12-01 00:00:00.000 在 `log_202612`。正文表和它的流水同月。
- 永久保留，不删、不归档。
- 流水表索引：`at`；`(kind, at)`；`trace`；带 `partialFilterExpression {cost: {$exists: true}}` 的 `at`（总账用）。正文表只有 `_id`。其余筛选（服务、模型、by、模式）在时间范围里扫描。
- 流水 `_id` = `"{svc}:{run}:{seq}"`，补写重复写入会被忽略。

## 流水字段

| 字段 | 含义 |
| --- | --- |
| `at` | 毫秒时间戳（服务器墙钟） |
| `svc` | 哪个服务：ear / mind / eye / notes / memory / tts |
| `kind` | 类型，点分层级，如 `call.realtime.turn`、`gate.realtime`、`switch.set`、`link.open`（清单见下） |
| `trace` | 链路编号：同一件事的各步共用 |
| `seq` | 本服务进程内递增序号；配合 `run` 判断有没有断档 |
| `run` | 服务进程启动编号（每次启动新生成） |
| `ok` | 成功 / 失败；失败时 `err` 一句话 |
| `ms` | 耗时（有的话） |
| `model`、`usage` | 模型名、服务商原样返回的用量（token、音频秒数、字符数……） |
| `cost` | `{amount, cur: "USD"\|"CNY", est: true, basis}`：估算金额、币种、依据（价格表版本）；不花钱的事件没有这个字段；价格未知时 `amount: null`，用量照记 |
| `key` | 这次调用用的密钥指纹（开头 7 个字符，如 `sk-xTVl`），用于和服务商账单对照；从不记完整密钥 |
| `by` | 谁触发的：asr / digit / page / boot / rule / robot / mind / timer…… |
| `mode` | 当时 hello 的模式 |
| `sum` | 一行中文摘要（听到的话前 60 字、回答前 60 字、开关"听悟 开→关"） |
| `body` | 正文引用：`{system, context, input, output, …}` → 正文编号（用 `body()` 取全文）；没有大段内容时省略 |
| `ref` | 指向原件或业务表：录音 `session` + `pos`、照片路径、`realtime_turns.tid`、`mind_runs` 的 id 等 |
| `extra` | 该类型自己的字段。`svc.start`：`code_version`、`pid`、`args`；`svc.stop`：`reason`（sigterm / exit）；`svc.beat`：`counts`、`state`、`dropped`、`spool_errors`、`db_errors`、`backlog_lines` |

小内容（几十字的听到的话、一句回答）直接放 `sum` 也放正文，脚本只靠日志表就能拿到全文。

脱敏：键名含 key / token / secret / password / authorization 的值写成 `***`，URL 查询串里的 `token=`、`key=` 同样处理；图片、音频不存（`data:` URL 换成 `[image omitted, N bytes]`，原件用 `ref` 指向）；单条正文超过 4 MB 截断并带标记。

## kind 清单

- **服务**：`svc.start`、`svc.stop`、`svc.beat`（每分钟一次，带本分钟各类计数）、`svc.error`。
- **连接**：`link.open` / `link.close`（ear 每条机器人链路）、`link.upstream`（ear 到听悟、realtime、soul-tts 的上游连接）。
- **开关 / 模式 / 预设**：`switch.set`、`mode.change`、`preset.apply`、`billing.set`、`robot.state`（机器人上报的开关）。
- **触发与闸门**：`trigger.*`、`gate.realtime`（放行 / muted_quiet / muted_unclear / muted_repeat / muted_blind / muted_wait_look / rescue……及理由）、`filler.play`。
- **业务调用**（正文里存系统输入 / 背景 / 用户输入 / 输出全文）：
  - `call.eye`、`call.look`；
  - `call.fusion`、`call.segment`、`call.notes`；
  - `call.realtime.session`、`call.realtime.turn`、`call.realtime.search`；
  - `call.chat`、`call.say.*`；
  - `call.memory`；
  - `call.tingwu`；
  - `call.tts`。
- **听到的话**：`heard.asr`（文字、session、pos），链路从它开始。

## 判断断档

- 每个 `run` 从 `svc.start`（seq 1）开始，`seq` 连续；跳号只会因为真丢了。
- 每分钟一条 `svc.beat`。心跳缺失 = 服务没了或库断了；有心跳没有业务调用 = 开关关着、服务闲着。
- 故意停服务会记 `svc.stop`，它到下一次 `svc.start` 之间不算断档。

## 查询模块

```python
import sys; sys.path.insert(0, "server/common")
import hello_log_query as q
from pymongo import MongoClient
db = MongoClient(URI)["hello"]          # URI 见 hubmix 环境里的 Mongo 连接串
```

时间一律毫秒时间戳；月份和"天"按北京时间。

| 函数 | 作用 |
| --- | --- |
| `flows(db, start_ms, end_ms, *, kinds, kind_prefix, svc, trace, has_cost, limit=200, before_id)` | 区间 `[start, end)` 内的流水，按 `at` 倒序，可跨月；`limit` 最大 1000。翻页：把上一页最后一条的 `_id` 作为 `before_id` |
| `body(db, at_ms, body_id)` | 取正文全文，找不到返回 `None`；`at_ms` 用来定月份 |
| `chain(db, trace, around_ms)` | 同一 `trace` 前后各 1 天的全部记录，按 `at` 正序 |
| `ledger(db, "YYYY-MM-DD")` | 北京时间一天的花费，按 (svc, model, cur) 汇总：`{"by": [{svc, model, cur, amount, n, unknown_n}], "total": {"USD": x, "CNY": y}, "unknown_n": k}`；`amount` 为 `None` 的记进 `unknown_n` |
| `gaps(db, start_ms, end_ms, *, beat_ms=60000, now_ms=None)` | 断档：`{svc, run, from_ms, to_ms, why}`，`why` 是 `no_beat`（相邻心跳间隔超过 2 倍 `beat_ms`）或 `seq_jump`（`seq` 跳号）。边界规则见下 |

### gaps 的边界规则

- 起点前多读 2 倍 `beat_ms`：断档在区间前开始、在区间内结束的会报（`to_ms >= start_ms`）；区间前就已启动的 run 不算断档。
- 某服务最后一条心跳 / 启动记录不是 `svc.stop`，且离区间终点（`end_ms`，或更早的 `now_ms`）超过 2 倍 `beat_ms`：报一条 `no_beat`，`from_ms` 是那条记录，`to_ms` 是终点，即"服务挂了"。查询未来的区间时传 `now_ms` 以免把还没发生的时间算进去。
- 窗口里一条记录都没有的服务无从判断，不报。
- 区间内某 run 第一条记录若 `seq` 大于 1，只有同一窗口里能看到它前面的记录时才会按 `seq` 跳号处理。
- 时间跨度最多 60 个月，只查实际存在的月表；`flows` 的 `before_id` 找不到时返回空列表；`ledger` 里币种缺失的记在 `"?"` 下。

### 示例

```python
import time
now = int(time.time() * 1000)

# 最近一小时所有调用，带花费的
rows = q.flows(db, now - 3600_000, now, kind_prefix="call.", has_cost=True, limit=50)

# 某条的输出全文
r = rows[0]
text = q.body(db, r["at"], r["body"]["output"])

# 整条链
q.chain(db, r["trace"], r["at"])

# 今天花了多少
q.ledger(db, "2026-11-16")["total"]

# 过去一天有没有断档
q.gaps(db, now - 86400_000, now)
```

直接用 mongo shell 也行，例如某天的失败记录：`db.log_202611.find({ok: false, at: {$gte: 1794240000000}}).sort({at: -1})`。
