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
