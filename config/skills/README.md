# Docking 的技能（2026-11-18）

主人：模型先找技能，再找工具；一个技能要完整（提示词 + 怎么调什么工具）。

- `skills.json`：技能目录。每个技能有 `id`、`group`（本地分类器认的 8 大类之一）、`title`、`when`（一句话，进目录）、`tools`（打开后交给模型的工具，必须是工具注册表里 live 的）。
- `skills/<id>.md`：技能全文：什么时候用 / 不用、步骤、每个工具怎么调（带参数例子）、怎么回答、例子。模型调用 `open_skill` 打开技能时，耳朵把这份全文和它的工具一起交给模型（`server/ear/realtime_link.py` `load_skills`）。
- `examples.json`：分类器的手写例句（旧做法，现在分类器用 `server/ear/intent.py` + 每晚重新训练的头）。

## 现在的 14 个技能

| 大类 | 技能 |
|---|---|
| look 看和听 | describe 看图说话、people 认人 |
| recall 回忆 | recall 回忆往事、review 回顾一段时间 |
| world 查外面 | realtime 实时数据、verify 查证到底、research 深入研究 |
| daily 日常 | remind 提醒和日程、watch 帮忙盯着 |
| body 身体 | body 身体互动（只对认识的人：认出的声音或脸） |
| manner 说话方式 | manner 说话方式、companion 陪伴角色 |
| self 解释自己 | self 解释自己 |
| note 记下来 | note 记下来 |

自动档：分类器认出大类，就把这一类下面所有技能的工具提前装上，模型自己选用哪个技能；没认出来也能自己打开。

## 改技能

改 `.md` 或 `skills.json`，跑耳朵的部署（`server/ear/deploy.sh` 把它们拷到 `~/soul-spike/skills.json` 和 `~/soul-spike/skills/`），下一次 Docking 会话生效。改完跑验收：

```
ssh <server> '~/soul-spike/.venv/bin/python ~/soul-spike/skills-tools/eval.py auto'      # 或 skills（只给目录）
```

测试话在 `tools/skills/cases.json`（每句写应该用到的技能、工具、回答要点；闲聊写 `none`）。跑完会清掉测试留下的提醒、盯着、待确认，并把安静 / 角色 / 长短 / 头的朝向复原。
