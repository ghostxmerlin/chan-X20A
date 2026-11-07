# Voice 候选到眼睛动画的建议映射

来源为 `soul/voice/outputs/20261106-stackchan-300/stackchan_candidates_300.json`，schema 2，300 条电影原声候选。该目录当前没有 SQLite 数据库或正式情绪枚举；实际数据由 JSON 与筛选表组成。

来源包含 **20 个表达主题、205 种 intent 原始字符串、199 种 emotion 原始字符串**。20 个主题是扁平编辑分类，规则位于 `outputs/20261106-stackchan-catalog/_build/expand300.mjs` 第 20–45 行；第一个匹配规则及五个人工覆盖项决定主题。主题不是情绪：例如“道歉／感谢”涵盖两种交际目的。`emotion` 中的多个词是组合语气，不是正式别名或数值强度。

这里采用 30 个视觉状态。它们是本次 UI 的设计选择，**不是给 voice 数据库新增定义**。全部 300 个候选均有主动画和备选动画；原始编号、theme、emotion、intent 保持原样。具名情绪先匹配，交际意图补充，再用主题兜底。`mapping-rules.json` 记录了所有规则与优先顺序。

`determined` 同时承担专注、认真、严肃等较收敛的表达。`worried` 覆盖焦急、紧张、不安、担忧、警觉；`shy` 覆盖拘谨、窘迫、回避；`sleepy` 覆盖疲惫、懒散、虚弱。`scared` 主要是额外覆盖害怕／慌乱的视觉能力，原数据没有独立“恐惧”枚举。嫌弃、冷静讽刺可映射 `sarcastic`；“阴森／威慑”等电影语气不意味着需要恐怖造型。

来源的 `audio_acquired=false`，300 条候选均为“待用户截取／待原声核对”。其中 **72 条没有具名情绪**（60 条“依场景试听确定”，12 条“待试听判断”），另外 68 条具名情绪含“待试听”注记。所有映射均保留 `needs_audio_review=true`；72 条占位情绪明确标为 `emotion_unspecified`，使用意图或主题兜底。100% 查询覆盖不等于情绪已获原声验证。

文件：

- `source-snapshot.json`：来源路径、SHA-256、修改时间、schema、计数和依据行号。
- `theme-coverage.json`：完整 20 主题、候选数和建议动画。
- `candidate-animation-map.json`：完整 300 候选逐条映射与匹配依据。
- `raw-emotion-inventory.json`：199 种原始情绪字符串及出现次数。
- `mapping-rules.json`：可审阅的视觉别名和意图匹配规则。
- `coverage-validation.json`：覆盖检查结果及状态分布。

重新读取来源并验证：

```sh
python3 build_coverage.py
python3 -m unittest test_coverage.py -v
```

脚本只读取 voice 文件，所有输出均写入本目录。
