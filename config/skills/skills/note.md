# 记下来

## 什么时候用
让你记住一件事、记住某人的喜好习惯关系；让你打开或关掉某个功能（听悟、识别、纪要、情景、Docking）。
不用：介绍新朋友（那是「认人」）；到点提醒（那是「提醒和日程」）。

## 怎么做
1. 记一件事：`memory_propose {"statement": "主人喜欢喝美式咖啡", "about": ["主人"]}`，statement 写成完整的一句。
2. 记某人：`person_note_propose {"who": "小王", "kind": "preference", "text": "不吃辣"}`；kind：preference 喜好、fact 事实、trait 性格、relation 关系、boundary 别提的事。
3. 开关：`switch_propose {"name": "tingwu", "on": true, "why": "主人要开"}`（tingwu 听悟、eye 识别、notes 纪要、mind 情景、realtime Docking）。
4. 主人亲口说的直接生效（返回里 saved 或 switch）：说「记下了」「打开了」。别人说的会变成待确认（返回里 state 是 pending）：告诉对方「我先记下了，等主人确认」。

## 回答
一句：「记下了，等主人确认。」

## 例子
- 「记住我喜欢喝美式咖啡」→ memory_propose → 「好，记下了，等主人确认一下。」
- 「帮我把听悟打开」→ switch_propose tingwu on → 「好，我提给主人了，确认了就打开。」
