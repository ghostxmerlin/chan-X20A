"""Perception prompts for the per-minute fusion call: versioned system text, input rendering and the
strict output schema (spec 4.1, 4.2, 5). The system part never changes inside a version, so the
provider's prefix cache can hold it; the user part carries settings, segments, the previous minute
and this minute, from the least to the most changing.
"""
import json

from . import settings as settings_mod

CONFIDENCE = {"type": "string", "enum": ["high", "mid", "low"]}
ANSWER = {"type": "object", "additionalProperties": False, "required": ["text", "confidence"],
          "properties": {"text": {"type": "string"}, "confidence": CONFIDENCE}}
DIMENSIONS = ["people", "activity", "sound_scene", "vision", "self", "time_place", "topic", "contradiction"]
ENTRY = {"type": "object", "additionalProperties": False,
         "required": ["key", "dimension", "op", "text", "basis", "confidence", "evidence"],
         "properties": {"key": {"type": "string"}, "dimension": {"type": "string", "enum": DIMENSIONS},
                        "op": {"type": "string", "enum": ["keep", "update", "add", "end"]},
                        "text": {"type": "string"},
                        "basis": {"type": "string", "enum": ["seen", "heard", "inferred", "assumed"]},
                        "confidence": CONFIDENCE, "evidence": {"type": "array", "items": {"type": "string"}}}}
SCENE_SCHEMA = {"type": "object", "additionalProperties": False,
                "required": ["who_am_i", "where_am_i", "people_doing", "owner_view", "entries", "changes", "summary", "want_to_know"],
                "properties": {"who_am_i": ANSWER, "where_am_i": ANSWER, "people_doing": ANSWER,
                               "owner_view": {"type": "string", "enum": ["present", "absent", "unknown"]},
                               "entries": {"type": "array", "items": ENTRY},
                               "changes": {"type": "array", "items": {"type": "string"}},
                               "summary": {"type": "string"}, "want_to_know": {"type": "string"}}}
RULE_CODES = {"rule:owner", "rule:coverage", "rule:time", "rule:place", "rule:self"}

# Only v3 is kept (2026-11-11, user: "有了v3，我们不必再管v2 v1了"); old runs keep their prompt name.
# v3: positive, most-likely wording (v2) + the eye result e1 as the main visual evidence, shorter output.
V3_SYSTEM = """你是桌面宠物机器人 hello 的感知整理者。每分钟你收到 hello 这一分钟的感知（已由规则程序整理好），写出此刻的情景：我是谁、我在哪、旁边的人在干什么。你只描述，不提建议，不替 hello 说话，不编故事。

【怎么写】
- 用正向的话说出最可能的情况，再用把握高低表示有多确定。例如：主人在画面里、又有打字声 → “主人大概在桌前工作（在打字）”，把握中；画面里没人、也没人说话 → “附近大概没人”，把握中；有人来回说话 → “旁边有人在聊天”；音乐声 → “有人在放音乐”。
- 不要写否定式、免责式的句子，例如“无法确认……”“不能据此判断……”“画面外看不到……”“没有提供……”。感官的局限你心里有数就行，用把握的高低体现，不要写进条目。真正缺的关键信息只写进 want_to_know，一句话。
- 证据弱就用“可能”“大概”，把握标 low；证据多、互相印证就标 high。
- 情景要全面，按维度写：people 人（谁在、远近）、activity 在做什么、sound_scene 声音环境、vision 画面、self hello 自身、time_place 时间地点、topic 话题、contradiction 矛盾。话题只是其中一项，活动不等于在说什么；有语言以外的证据时必须引用（e 视觉识别、v 脸、a 声音事件、rule: 规则行）。几句话不等于几个人。
- 条目要少而准：每个维度最多 1–2 条，总共不超过 6 条，每条不超过 40 字，没有内容的维度不写。self 只在感官关了、断了或 hello 自己出声时写；time_place 一条即可。
- 规则算出的主人状态是底稿（rule:owner），你不能改它；owner_view 写你自己的看法，与规则不同时另写一条 contradiction。

【感官的已知毛病（据此掂量把握，不要写进条目）】
- 远处的话常被听错或漏掉；响度低的句子内容不可靠。
- 声纹多半认不出人；“不确定，最像某人”不等于就是那个人。
- 两个转写常给出不同的字；一致的部分才可信。
- 人脸认出名字很稳；没认出的脸也可能是熟人（角度、光线不好），按上下文掂量；年龄、性别不要引用。
- 脸离开画面 1.5 秒就报离开，不代表人走了；头会追着脸转，画面只是 hello 正看着的方向。
- 感官关着的时候，“没看到、没听到”不是证据；标为“hello 自己出的声”的不是别人说话。

【证据和输出】
- basis：seen 看到 / heard 听到 / inferred 推断 / assumed 猜测；confidence：high / mid / low；要把把握调高，必须引用本分钟的新证据。evidence 只写给出的代号；seen、heard 必须引用本分钟的 s/t/a/v/e 代号。
- entries 每条带稳定的 key（如 people.owner、people.others、activity.desk、sound_scene.room、time_place.now、topic.1），op 相对上一分钟：keep 不变、update 改写、add 新增、end 结束。没变就写 keep，不要换个说法重写。
- who_am_i 写 hello 自己（我是谁、此刻感官怎样），不要写别人；where_am_i 写 hello 在哪；people_doing 写旁边的人在干什么。各一句正向陈述，带把握。
- 有人说话时写 topic：两个转写里一致出现的词说明了最可能的话题，就写出来（把握可以是 low）；完全听不清时才不写。
- summary 不超过 20 个字。changes 写这一分钟变了什么（没变留空）。

【画面】
- e1 是视觉识别：另一个模型看过这一分钟的画面后写的结果（几个人、谁、在哪、在做什么、环境）。画面以 e1 为主要依据：people、activity、vision 条目引用 e1，basis 可以是 seen；M12 的脸记录只用来补充名字。
- e1 里“很可能是主人”的人，按主人在场来写，把握中。
- 没有 e1 时，按脸记录（v 代号）来写。
- who_am_i、where_am_i、people_doing 各一句，越短越好。"""
VERSIONS = {"v3": {"system": V3_SYSTEM}}
MAX_TOKENS = {"v3": 4000}
# luna reasons before answering and the reasoning counts as output (live 2026-11-11: 940 of 1739 tokens);
# v3 asks for low effort: 216 reasoning / 776 output tokens, complete answer, about 12.7 s.
REASONING_EFFORT = {"v3": "low"}
OFFERED = sorted(VERSIONS)
STATE_TEXT = {"seen": "看到主人", "just_left": "主人刚离开画面", "likely_present": "主人很可能在",
              "unknown": "不知道主人在不在", "likely_absent": "主人很可能不在", "sensors_off": "感官全关，无法判断"}


def _hhmm(ms, tz):
    return settings_mod.local_time(ms, tz).strftime("%H:%M")


def _hhmmss(ms, tz):
    return settings_mod.local_time(ms, tz).strftime("%H:%M:%S")


def _who(w, owner_names, people):
    if w["kind"] == "known":
        return "认出：" + _display(w["name"], owner_names, people)
    if w["kind"] == "unsure":
        return f"不确定，最像{_display(w['best'], owner_names, people)}（{(w.get('score') or 0):.2f}）"
    return "不认识的声音"


def _display(name, owner, people):
    if name in owner["voice_names"] or name in owner["face_names"]:
        return f"主人（{owner['display']}）"
    for p in people:
        if name in p.get("voice_names", []) or name in p.get("face_names", []):
            return p["display"]
    return str(name)


def _overlaps(a, b):
    return isinstance(a, list) and isinstance(b, list) and len(a) == 2 and len(b) == 2 and min(a[1], b[1]) > max(a[0], b[0])


def render(version, s, pkg, strong, prev_scene, segments, neighbours):
    tz, people, src = s["tz"], s.get("people", []), s["sources"]
    owner = {**s["owner"], "display": settings_mod.owner_name(s, pkg["minute"])}
    system = VERSIONS[version]["system"]
    L = ["【设置】", f"关于我：{s['self_facts']}", f"默认地点：{s['place']['default']}", f"主人：{owner['display']}"]
    if (s.get("environment") or "").strip():
        L.append(f"环境：{s['environment'].strip()}")
    if people:
        L.append("认识的人：" + "、".join(p["display"] for p in people))
    L.append("")
    L.append("【今天的情景段】")
    if segments:
        for g in segments:
            end = _hhmm(g["end"], tz) if g.get("end") else "现在"
            L.append(f"{_hhmm(g['start'], tz)}–{end} {g.get('title') or g.get('cls', '')}")
    else:
        L.append("（还没有）")
    L.append("")
    if prev_scene:
        gap = round((pkg["minute"] - prev_scene.get("minute", pkg["minute"])) / 60000)
        L.append("【上一分钟的判断，可能有错】" if gap <= 1 else f"【上一次的判断（{gap} 分钟前），可能有错】")
        for e in (prev_scene.get("scene") or {}).get("entries", []):
            age = max(0, round((pkg["minute"] - e.get("last_evidence", pkg["minute"])) / 60000))
            L.append(f"{e['key']}（{e.get('dimension', '')}，{e.get('basis', '')}，{e.get('confidence', '')}，最后有证据：{age} 分钟前{'，已陈旧' if e.get('stale') else ''}）：{e['text']}")
        L.append("")
    st, cov = strong, strong["coverage"]
    L.append("【本分钟】")
    L.append(f"rule:time {st['time']['label']}，星期{st['time']['weekday']}，{'工作时段' if st['time']['work_hours'] else '非工作时段'}")
    pl = st["place"]
    if pl.get("label") and pl.get("source") == "owner_report":
        L.append(f"rule:place 地点：{pl['label']}（主人报告，{_hhmm(pl['at'], tz)}）")
    elif pl.get("label"):
        L.append(f"rule:place 地点：{pl['label']}（来自 Wi-Fi）")
    else:
        L.append(f"rule:place 地点：{s['place']['default']}（默认，没有报告也没有 Wi-Fi 数据）")
    L.append(f"rule:coverage 感官覆盖：听觉 {cov['hearing_s']} 秒，视觉 {cov['vision_s']} 秒，M12 {cov['m12_s']} 秒{'（估计）' if cov['estimated'] else ''}")
    L.append(f"rule:owner 主人状态（规则）：{STATE_TEXT.get(st['owner']['state'], st['owner']['state'])}——{st['owner']['why']}")
    L.append(f"rule:self hello 这一分钟自己出声 {len(pkg['self_speech'])} 次")
    prev_speech = (neighbours.get("prev") or {}).get("speech", []) if neighbours else []
    for x in pkg["speech"]:
        who = _who(x["who"], owner, people)
        loud = f"，响度 {x['loud_db']}" if x.get("loud_db") is not None else ""
        if src["speech_text"]:
            alt = "".join(f"（{t['code']} 听悟听成：{t['text']}）" for t in x["tingwu"]) if src["tingwu"] else ""
            also = "（说这句时 hello 也在出声）" if x.get("hello_sounding") else ""
            L.append(f"{x['code']} {_hhmmss(x['at'], tz)} 说话 [{who}{loud}]：{x['text']}{alt}{also}")
        else:
            L.append(f"{x['code']} {_hhmmss(x['at'], tz)} 有人说了一句话 [{who}{loud}]")
    if src["speech_text"] and src["tingwu"]:
        for t in pkg["tingwu_only"]:
            dup = any(p.get("ref", {}).get("session") == t["ref"]["session"] and _overlaps(p.get("pos"), t.get("pos"))
                      for p in prev_speech)
            if not dup:
                L.append(f"{t['code']} {_hhmmss(t['at'], tz)} 只有听悟听到：{t['text']}")
    for x in pkg["self_speech"]:
        L.append(f"{x['code']} hello 自己出的声{('：' + x['text']) if src['speech_text'] else ''}")
    if src["sounds"]:
        for g in pkg["sounds"]:
            loud = f"，最响 {g['max_loud_db']}" if g.get("max_loud_db") is not None else ""
            L.append(f"{','.join(g['codes'])} 声音事件：{g['label'] or g['kind']} × {g['count']}{loud}")
    eye = pkg.get("eye")
    if src["vision"] and eye:
        r = eye["result"]
        who = "；".join("·".join(x for x in (p.get("who"), p.get("where"), p.get("doing")) if x) for p in r.get("people", []))
        L.append(f"{eye['code']} 视觉识别（{eye['frames']} 张画面）：{r.get('people_count', 0)} 人" + (f"；{who}" if who else "") +
                 f"；活动：{r.get('activity', '')}；环境：{r.get('scene', '')}；变化：{r.get('changes') or '—'}")
        named = sorted({_display(f["name"], owner, people) for f in pkg["faces"] if f["name"]})
        unknown = sum(1 for f in pkg["faces"] if not f["name"])
        L.append(f"M12：{'认出 ' + '、'.join(named) if named else '没认出名字'}；未认出的脸 {unknown} 张；人形 {pkg['persons']['seconds']} 秒")
    elif src["vision"]:
        for f in pkg["faces"]:
            name = _display(f["name"], owner, people) if f["name"] else "未认出的脸"
            to = "仍在画面里" if f["open"] else f"{_hhmmss(f['to'], tz)} 离开画面"
            L.append(f"{f['code']} 脸：{name}，{_hhmmss(f['from'], tz)} 起，{to}")
        L.append(f"人形检测：本分钟 {pkg['persons']['seconds']} 秒，{pkg['persons']['ids']} 个编号")
    for c in st.get("contradictions", []):
        L.append(f"规则发现的矛盾：{c['kind']}（{','.join(c['codes'])}）")
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(L)}]


def schema_format():
    return {"type": "json_schema", "json_schema": {"name": "scene", "strict": True, "schema": SCENE_SCHEMA}}


def as_text(messages):
    return json.dumps(messages, ensure_ascii=False)
