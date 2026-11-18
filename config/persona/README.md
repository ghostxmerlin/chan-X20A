# hello 的人设（设定文件）

`active` 里写的文件就是在用的人设（现在：`luye.md`，鹿野，2026-11-18 主人给的设定）。

- 部署时 `server/mind/deploy.sh` 把它拷成服务器上的 `~/soul-mind/persona.md`（对话模式读），
  `server/ear/deploy.sh` 拷成 `~/soul-spike/persona.md`（围观模式读）。
- 改人设：改这个文件（或换一个文件、改 `active`），再跑两个 deploy。对话模式 5 分钟内读到新文件；
  围观在耳朵重启后生效（deploy 会重启）。
- 人设只管性格和语气；各模式自己的规则（一两句、不给医疗建议、只回「嗯」等）仍在代码里。
- 设备事实（机器人叫 Dynames、有麦克风和 M12 摄像头）在 mind 设置的 `self_facts` 里。
