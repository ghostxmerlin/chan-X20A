# mind（服务器端情景把控）

**当前角色说话与长期记忆**：见 [场景、长期记忆与角色说话](../../docs/memory-speech.md)。新增独立 `soul-memory`、版本化记忆、关注筛选、场景与方言设置；部署脚本同时更新四个服务。以下是原始时间线基础设施说明，现行设置由 `mind_settings` 最新版本管理，文件只作首次引导。

设计：`docs/superpowers/specs/2026-11-10-mind-design.md`；第 0 阶段计划：`docs/superpowers/plans/2026-11-10-mind-phase0.md`。

第 0 阶段只用规则：每分钟把 `hello` 库里的原始记录（`speech / tingwu / sounds / vision / sessions`，只读）整理成分钟包和强力元素，写进 `mind_packages`（每次重算产生新版本，旧版本保留）和 `mind_state`。不调用模型。

- **运行**：服务器上的临时单元 `soul-mind`（和耳朵的 `soul-spike` 一样由 `systemd-run` 启动，服务器重启后需重新部署）。用耳朵的 venv（需要 pymongo）。
- **部署**：`server/mind/deploy.sh`（`--dry-run` 只列出要拷的文件）。本机 `.env` 可选 `SOUL_MIND_DIR`（默认 `soul-mind`）、`SOUL_MIND_UNIT`（默认 `soul-mind`）。
- **设置**：`~/soul-mind/settings.json`，只在服务器上，含真实称呼；第一次部署时从 `settings.example.json` 复制。改完不用重启，下一分钟生效；要让历史也按新设置算，用重算。
- **重算**：在服务器 `~/soul-mind` 下 `~/soul-spike/.venv/bin/python -m mind.runner --settings settings.json --rebuild 2026-11-10T00:00 2026-11-11T00:00`（本地时间）。
- **接口**：只监听服务器 `127.0.0.1:52740`，令牌与耳朵相同；`/state`、`/timeline?from=&to=`（毫秒，≤24 小时）、`/minute?t=&version=`。Mac 上由监控页经 ssh 转发访问（`/mind.html`）。
- **密钥**：`MONGO_URI` 运行时从 hubmix 的 `~/aidea/agent/.env` 读，不进仓库。

## 库（第 1 期）

五个集合，全部只追加（每次修改是一条新版本，旧版本不动）：`people_cards`（人物卡）、`people_notes`（人物笔记）、`memory_items`（记忆）、`knowledge_items`（知识），另有 `voice_labels`（声纹标注，第 4 期才写）。

| 方法 路径 | 说明 |
| --- | --- |
| `GET /db/overview` | 各集合数量、状态、来源统计，以及已有设置/记忆的数量 |
| `GET /db/people` | 全部人物卡（含有效笔记数） |
| `GET /db/person?id=` | 一张卡、全部版本、笔记、相关记忆与知识 |
| `GET /db/memories?kind=&person=&topic=&q=&from=&to=&offset=&limit=` | 记忆列表（limit ≤ 100） |
| `GET /db/knowledge?kind=&person=&q=&offset=&limit=` | 知识列表 |
| `GET /db/item?c=&id=` | 任一条的版本历史（最多 50 版） |
| `POST /db/person`、`/db/person/note`、`/db/memory`、`/db/knowledge` | 新建或修改：`{id?, expected_version?, fields, reason?}`；恢复旧版：`{id, expected_version, restore_version}`。`reason` 截到 200 字 |

错误：格式不对 400，没有这条 404，版本已被改过 409。**没有删除接口，删除只由主人手动维护数据库。本期没有任何提示读取这些集合。** 设计见 `docs/superpowers/specs/2026-11-13-backend-people-knowledge-memory-design.md` 第 3 节。
