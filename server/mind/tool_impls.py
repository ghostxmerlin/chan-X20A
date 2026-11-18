"""mind's tool implementations (B1, 2026-11-18; framework: tools.py, rules: docs/hello-tools.md).

B1 wires the three tools that already had an interface: switches_status, mode_status, cost_today. Each returns only
what a model needs, in short keys with Chinese titles, and never a key, token or internal address.
"""
import time
from datetime import datetime, timedelta, timezone

from . import mode_table
from .tools import ToolError

BJ = timezone(timedelta(hours=8))
SWITCH_TITLES = {"tingwu": "听悟（第二路转写）", "eye": "识别（看画面）", "notes": "纪要", "realtime": "Docking（实时对话）",
                 "mind": "情景"}
MOUTH_TITLES = {"off": "不出声", "clips": "只放录好的台词", "tts": "合成说话"}
PAUSE_WHY = {"away": "没人在，暂停花钱的功能", "budget": "今天的钱用到上限了"}
DIGITS = {"idle": "4", "crowd": "5", "companion": "6", "subtitle": "7（中译英）/ 8（英译中）"}   # firmware mode_rules.h


def mind_tools(*, switches, store, mode_store=None, ledger=None, clock_ms=None):
    """name -> fn(args): switches = api's switches(); store.enabled() = the 情景 switch; ledger(db_day) = the run log's
    day ledger (hello_log_query.ledger bound to the db), None when the log query is not deployed."""
    now_ms = clock_ms or (lambda: int(time.time() * 1000))

    def switches_status(args):
        sw = switches()
        out = {name: {"title": SWITCH_TITLES[name], "on": bool((sw.get(name) or {}).get("on"))}
               for name in ("tingwu", "eye", "notes", "realtime")}
        out["mind"] = {"title": SWITCH_TITLES["mind"], "on": bool(store.enabled())}
        mouth = (sw.get("mouth") or {}).get("mode")
        p = sw.get("pause") or {}
        data = {"switches": out,
                "mouth": {"mode": mouth, "title": MOUTH_TITLES.get(mouth, mouth)},
                "pause": {"paused": bool(p.get("paused")), "why": PAUSE_WHY.get(p.get("why"), p.get("why")),
                          "spent_cny": p.get("spent_cny"), "cap_cny": p.get("cap_cny")}}
        if "billing" in sw:
            data["billing_on"] = bool(sw["billing"].get("on"))
        return data

    def mode_status(args):
        if mode_store is None:
            raise ToolError("not_available", "这里没有接模式")
        snap = mode_store.current()
        mode = snap.get("mode")
        robot = mode_store.robot()
        m = mode_table.MODES.get(mode, {})
        modes = []
        for mid in mode_table._IDS:
            d = mode_table.MODES[mid]
            how = []
            if d.get("enter"):
                how.append("对语音模块说 " + " / ".join(d["enter"]))
            if mid in DIGITS:
                how.append("说数字 " + DIGITS[mid])
            modes.append({"id": mid, "name": d["name"], "label": d["look"]["label"], "how_to_enter": "；".join(how) or "屏幕上选"})
        since = snap.get("since")
        return {"mode": mode, "name": m.get("name"), "label": (snap.get("look") or m.get("look") or {}).get("label"),
                "since": datetime.fromtimestamp(since / 1000, BJ).strftime("%Y-%m-%d %H:%M") if isinstance(since, int) else None,
                # mind learns the robot link from the ear's /mode/robot on a (re)connect: unknown right after mind restarts
                "robot_online": bool(robot.get("connected")) if robot.get("at") else None, "modes": modes}

    def cost_today(args):
        if ledger is None:
            raise ToolError("not_available", "账单查询没有部署")
        day = args.get("day") or datetime.fromtimestamp(now_ms() / 1000, BJ).strftime("%Y-%m-%d")
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            raise ToolError("bad_args", "日期要写成 2026-11-18 这样") from None
        led = ledger(day)
        by = sorted(led.get("by", []), key=lambda r: -(r.get("amount") or 0))[:8]
        sw = switches()
        p = sw.get("pause") or {}
        return {"day": day, "total": led.get("total", {}),
                "top": [{"svc": r.get("svc"), "model": r.get("model"), "amount": round(r.get("amount") or 0, 4),
                         "cur": r.get("cur"), "calls": r.get("n")} for r in by],
                "unknown_calls": led.get("unknown_n", 0),
                "cap_cny": p.get("cap_cny"), "spent_cny_counted": p.get("spent_cny")}

    return {"switches_status": switches_status, "mode_status": mode_status, "cost_today": cost_today}
