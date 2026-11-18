# hello 的人设（设定文件）

`active` 里写的文件就是在用的人设（现在：`luye.md`，鹿野，2026-11-18 主人给的设定）。

- 部署时 `server/mind/deploy.sh` 把它拷成服务器上的 `~/soul-mind/persona.md`（对话模式读），
  `server/ear/deploy.sh` 拷成 `~/soul-spike/persona.md`（Docking 模式读）。
- 改人设：改这个文件（或换一个文件、改 `active`），再跑两个 deploy。对话模式 5 分钟内读到新文件；
  Docking 在耳朵重启后生效（deploy 会重启）。
- 人设只管性格和语气；各模式自己的规则（一两句、不给医疗建议、只回「嗯」等）仍在代码里。
- 设备事实（机器人叫 Dynames、有麦克风和 M12 摄像头）在 mind 设置的 `self_facts` 里。

## 换声音（2026-11-18）

声音和人设配套（主人：声音要和性格一致）。`voices.json` 写每个能用的声音配哪个人设文件：

- `ly01` 鹿野 → `luye.md`（默认）
- `me01` 主人自己的声音 → `owner.md`（用主人的说话风格，但仍然是 hello，不冒充主人）

工具 `set_voice`（C 级：主人亲口说直接换，别人提的进「待确认」）换 Docking 的声音、人设和垫话（`~/soul-spike/fillers/<声音>`、`<声音>-search`，用 `tools/voice/make_fillers.py <声音>` 在服务器上生成），重开会话生效；选择记在 switches `rt_voice`，耳朵重启后照旧。耳朵部署把整个 `config/persona/` 拷到 `~/soul-spike/personas/`。

