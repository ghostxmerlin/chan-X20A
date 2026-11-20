"""The ear's local request API for mind's tools (L1, 2026-11-18; docs/hello-tools.md §6).

POST http://127.0.0.1:<port>/ask  {"name": ..., "args": {...}}  ->  {"ok", "data", "error": {code, message}}
Authorization: Bearer <the ear's token>. Bound to 127.0.0.1 only; mind (same server) is the only caller.
Asks: who_is_here, look_now, web_search, say, stop_speaking, set_length, set_quiet, set_role.
Robot asks (collector-0.5.10, the /mouth command line, mouth_link.RobotLinks.send_cmd): express_eyes, show_word,
play_clip, turn_head, face_front, turn_around, tracking, nod, shake_head, set_volume (not saved on the robot),
face_learn_propose (M12 learn), robot_status. Errors: robot_offline, timeout, refused (busy / too often / speaking),
bad_args, not_available (old firmware, motor off, M12 offline), failed.
A small asyncio HTTP/1.1 reader (no new dependency); one request per connection, body <= 16 KiB.
"""
import asyncio
import hmac
import json
import re
import time

import ear_log
import mouth_link

MAX_BODY = 16384
LOOK_WAIT_S = 14.0
SEARCH_TIMEOUT_S = 25.0
SCENE_FRESH_MS = 90_000
ROBOT_TIMEOUT_S = 3.0
LEARN_TIMEOUT_S = 7.0                # m12d answers learn within 3 s, the robot gives up after 4 s
TURN_HEAD_GAP_S = 5.0                # registry: turn_head at most once per 5 s
YAW_TO = ("left90", "left45", "front", "right45", "right90")
# The robot's cmd_result error codes (firmware robot_cmd.h) -> (code for mind, what hello can say).
ROBOT_ERRORS = {
    "bad_args": ("bad_args", "参数不对"),
    "unknown_verb": ("not_available", "机器人固件不认识这个命令（要升级）"),
    "no_glyph": ("bad_args", "有的字机器人屏幕上显示不了（字库里没有）"),
    "rate_limited": ("refused", "太频繁了，等几秒再来"),
    "busy": ("refused", "正在做别的动作，等它做完"),
    "queue_full": ("refused", "机器人手上的命令太多了，等一下"),
    "speaking": ("refused", "hello 正在说话，不放片段"),
    "motor_off": ("not_available", "舵机没开"),
    "no_feedback": ("not_available", "读不到头的角度，不敢动"),
    "facing_back": ("refused", "现在背对着，先转回来"),
    "facing_unknown": ("not_available", "不知道头朝哪，先回正"),
    "at_limit": ("refused", "已经转到头了"),
    "no_room": ("refused", "头的位置太靠边，做不了这个动作"),
    "m12_offline": ("not_available", "M12 摄像头不在线"),
    "eyes_off": ("not_available", "现在不是眼睛模式，做不了表情"),
    "no_clip": ("not_available", "没有这个情绪的片段"),
}


class VoiceBook:
    """personas/voices.json (config/persona/voices.json): voice key -> persona file + the names people say."""
    def __init__(self, persona_dir, voices_path, fillers_dir):
        self.persona_dir, self.voices_path, self.fillers_dir = persona_dir, voices_path, fillers_dir

    def _map(self):
        try:
            with open(f"{self.persona_dir}/voices.json", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"voices": {}}

    def resolve(self, name):
        m = self._map()
        n = str(name or "").strip().lower()
        for key, v in (m.get("voices") or {}).items():
            if n == key.lower() or n in [str(x).lower() for x in v.get("names") or []]:
                return key, v
        return None, None

    def names(self):
        return [((v.get("names") or [k])[0]) for k, v in (self._map().get("voices") or {}).items()]

    def load(self, key, v):
        import realtime_link
        vid = realtime_link.load_voice(self.voices_path, key)
        persona = realtime_link.build_persona(realtime_link.load_persona(f"{self.persona_dir}/{v['persona']}"))
        fillers = realtime_link.load_fillers(f"{self.fillers_dir}/{key}")
        search = realtime_link.load_fillers(f"{self.fillers_dir}/{key}-search")
        return vid, persona, fillers, search


class AskError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _ago(ms, now_ms):
    s = max(0, (now_ms - ms) // 1000)
    return f"{s} 秒前" if s < 120 else f"{s // 60} 分钟前"


class Asks:
    """The handlers; hub = RealtimeHub (or None), burst = VisionBurst, presence = presence.Presence, searcher =
    web_search.dashscope_search(...) result, paused() -> list of pause reasons (empty = not paused)."""
    def __init__(self, hub=None, burst=None, presence=None, searcher=None, paused=None, wall_ms=None, pool=None,
                 voices=None, robot=None, clock=None):
        self.hub, self.burst, self.presence, self.searcher = hub, burst, presence, searcher
        self.robot = robot               # mouth_link.RobotLinks (send_cmd); None = no command line
        self.clock = clock or time.monotonic
        self._last = {}                  # ear-side gaps (turn_head)
        self.paused = paused or (lambda: [])
        self.wall_ms = wall_ms or (lambda: int(time.time() * 1000))
        self.pool = pool
        self.voices = voices                             # VoiceBook (set_voice)

    def _hub(self):
        if self.hub is None:
            raise AskError("not_available", "Docking 没有部署")
        return self.hub

    def _docking(self):
        hub = self._hub()
        if not hub.switch.on:
            raise AskError("switch_off", "Docking（实时对话）开关关着")
        if not hub.in_crowd():
            raise AskError("not_available", "机器人现在不在 Docking 模式")
        return hub

    async def who_is_here(self, args):
        now = self.wall_ms()
        people = self.hub.faces_now() if self.hub is not None else []
        doc = self.presence.doc(now) if self.presence is not None else {}
        out = {"faces": people, "faces_in_view": len(people)}
        if doc.get("voice_at"):
            out["last_voice"] = _ago(doc["voice_at"], now)
        if doc.get("owner_at"):
            out["owner_last"] = _ago(doc["owner_at"], now)
        sc = self.hub.scene_now() if self.hub is not None else None
        if sc and now - sc[0] <= SCENE_FRESH_MS:
            out["scene"] = {"text": sc[1], "ago": _ago(sc[0], now)}
        return out

    async def look_now(self, args):
        now = self.wall_ms()
        if self.paused():
            raise AskError("paused", "现在暂停着（没人在），不拍照")
        hub = self._hub()
        mark = None
        if self.burst is not None and self.burst.allowed():
            with ear_log.scope(trace=args.get("_trace")):
                mark = self.burst.ask("tool")
        if mark is None:
            sc = hub.scene_now()
            if sc and now - sc[0] <= SCENE_FRESH_MS:
                return {"answer": sc[1], "fresh": False, "photo_ago": _ago(sc[0], now),
                        "note": "现在拍不了新照片（不在 Docking），这是最近一张"}
            raise AskError("not_available", "现在拍不了照片：要在 Docking 模式、开关开着、机器人在线")
        fut = asyncio.get_running_loop().create_future()
        hub.look_waiters.append((mark, fut))
        try:
            text = await asyncio.wait_for(fut, LOOK_WAIT_S)
        except asyncio.TimeoutError:
            raise AskError("timeout", "等了十几秒没等到照片") from None
        return {"answer": text, "fresh": True, "photo_ago": "刚刚"}

    async def web_search(self, args):
        if self.searcher is None:
            raise AskError("not_available", "联网搜索没有配置")
        if self.paused():
            raise AskError("paused", "现在暂停着，不联网搜")
        q = str(args.get("query") or "").strip()
        if not q:
            raise AskError("bad_args", "要搜什么？")
        loop = asyncio.get_running_loop()
        with ear_log.scope(trace=args.get("_trace")):
            import contextvars
            ctx = contextvars.copy_context()
        try:
            text = await asyncio.wait_for(loop.run_in_executor(self.pool, ctx.run, self.searcher, q), SEARCH_TIMEOUT_S)
        except asyncio.TimeoutError:
            raise AskError("timeout", "搜索超时了") from None
        if not text:
            raise AskError("failed", "没搜到")
        return {"answer": text}

    async def say(self, args):
        hub = self._docking()
        if hub.quiet_on() and not args.get("interrupt"):
            raise AskError("refused", "主人让它安静着")
        if args.get("interrupt"):
            hub.stop_speaking()
            await asyncio.sleep(0.3)
        r = hub.say(args.get("text") or "", trace=args.get("_trace"))
        if r == "not_ready":
            raise AskError("not_available", "Docking 会话没开（没人或暂停中）")
        if r == "busy":
            raise AskError("refused", "它正在说话或有人在说话，等一下再说")
        return {"spoken": True}

    async def stop_speaking(self, args):
        return {"stopped": bool(self._hub().stop_speaking())}

    async def set_length(self, args):
        until = self._hub().set_length(args["mode"], trace=args.get("_trace"))
        return {"mode": args["mode"], "minutes": 10 if until else None}

    async def set_quiet(self, args):
        level = args.get("level") or "quiet"
        hub = self._hub()
        until = hub.set_quiet(level, args.get("minutes"), trace=args.get("_trace"), until=args.get("until"))
        open_ = until == float("inf")
        sticky = level == "quiet" and getattr(hub, "_quiet_sticky", False)
        return {"level": level, "minutes": None if until is None or open_ else round((until - hub.clock()) / 60),
                "open": open_,
                "ends": ("直到主人说退出" if open_ else "到时间或主人说退出（被叫时只答那一句）" if sticky
                         else "被叫 Providence 就结束") if level == "quiet"
                else ("直到主人说退出" if open_ else "说「可以说话了」或到时间") if level == "low" else None}

    async def set_debug(self, args):
        """调试模式 on / off (2026-11-19); no length = until the owner ends it."""
        hub = self._hub()
        on = args.get("on", True) is not False
        until = hub.set_debug(on, args.get("minutes"), until=args.get("until"), trace=args.get("_trace"))
        open_ = until == float("inf")
        return {"on": on, "open": open_, "minutes": None if until is None or open_ else round((until - hub.clock()) / 60),
                "ends": ("直到主人说退出" if open_ else "到时间或主人说退出") if on else None}

    async def debug_alert(self, args):
        """mind's debug reminder (mind/debug_mode.py), said when hello is quiet."""
        text = str(args.get("text") or "").strip()
        if not text:
            raise AskError("bad_args", "要说提醒什么（text）")
        return {"result": self._hub().debug_alert(text, trace=args.get("_trace"))}

    async def set_role(self, args):
        hub = self._hub()
        until = hub.set_role(args.get("role") or None, args.get("minutes"), trace=args.get("_trace"), until=args.get("until"))
        open_ = until == float("inf")
        return {"role": args.get("role") or None, "open": open_,
                "minutes": None if until is None or open_ else round((until - hub.clock()) / 60)}

    async def set_voice(self, args):
        hub = self._hub()
        if self.voices is None:
            raise AskError("not_available", "声音表没有部署")
        key, v = self.voices.resolve(args.get("voice") or "")
        if key is None:
            raise AskError("bad_args", f"能换的声音：{'、'.join(self.voices.names())}（声音要和人设配套）")
        try:
            vid, persona, fillers, search = await asyncio.get_running_loop().run_in_executor(
                self.pool, self.voices.load, key, v)
        except Exception as e:                           # noqa: BLE001
            raise AskError("failed", f"读不到这个声音（{type(e).__name__}）") from None
        if not vid:
            raise AskError("not_available", f"声音 {key} 在 soul-tts 里没有")
        if args.get("style"):
            persona = persona + f"\n\n【这会儿的语气】{str(args['style'])[:40]}"
        hub.set_voice(key, vid, persona, fillers, search, trace=args.get("_trace"))
        return {"voice": key, "persona": v["persona"], "style": args.get("style"), "applies": "下一句话起（重开会话）"}

    # ---- robot tools (collector-0.5.10 command line on /mouth) ----
    async def _robot(self, do, args, trace=None, timeout=ROBOT_TIMEOUT_S):
        if self.robot is None:
            raise AskError("not_available", "耳朵没有接机器人的命令线")
        try:
            with ear_log.scope(trace=trace):
                r = await self.robot.send_cmd(do, args, timeout)
        except mouth_link.CmdError as e:
            if e.code == "robot_offline":
                raise AskError("robot_offline", "机器人不在线") from None
            if e.code == "timeout":
                raise AskError("timeout", "机器人没回话（超时）") from None
            if e.code == "busy":
                raise AskError("refused", "机器人手上的命令太多了，等一下") from None
            raise AskError("not_available", "机器人固件还没有命令线（要 collector-0.5.10 以上）") from None
        if not r["ok"]:
            if str(r["error"]).startswith("m12_"):              # m12d's own answer to a learn (no face in view, ...)
                raise AskError("refused", f"M12 没学成（{str(r['error'])[4:28]}），让他正对着摄像头再试")
            code, msg = ROBOT_ERRORS.get(r["error"] or "", ("failed", f"机器人没做成（{str(r['error'])[:24]}）"))
            raise AskError(code, msg)
        return r["data"]

    def _gap(self, name, gap_s):
        now = self.clock()
        if name in self._last and now - self._last[name] < gap_s:
            raise AskError("refused", f"{gap_s:g} 秒里只能做一次")
        self._last[name] = now

    @staticmethod
    def _int(args, key, lo, hi, default=None):
        v = args.get(key, default)
        if v is None:
            return None
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            raise AskError("bad_args", f"{key} 要在 {lo}..{hi} 之间")
        return v

    async def express_eyes(self, args):
        state = str(args.get("state") or "").strip()
        if not state or len(state) > 20:
            raise AskError("bad_args", "要做什么表情？（state，比如 happy）")
        return await self._robot("eyes", {"state": state}, args.get("_trace"))

    async def show_word(self, args):
        """text (<= 8 characters, from the robot's word font), hold_s (default 2; up to 3600 = a word that stays,
        e.g. 安静中); clear: true with no text removes a word that stays."""
        text = str(args.get("text") or "").strip()
        if len(text) > 8:
            raise AskError("bad_args", "最多 8 个字")
        hold = self._int(args, "hold_s", 0, 3600, 2)
        if not text and not args.get("clear"):
            raise AskError("bad_args", "要显示什么字？（清掉常驻的字用 clear）")
        return await self._robot("word", {"text": text, "hold_ms": hold * 1000}, args.get("_trace"))

    async def play_clip(self, args):
        out = {}
        clip = self._int(args, "clip_id", 1, 80)
        if clip is not None:
            out["clip_id"] = clip
        elif args.get("emotion"):
            out["emotion"] = str(args["emotion"])[:20]
        return await self._robot("clip", out, args.get("_trace"))

    async def turn_head(self, args):
        to = args.get("to")
        if to not in YAW_TO:
            raise AskError("bad_args", "to 只能是 " + " / ".join(YAW_TO))
        self._gap("turn_head", TURN_HEAD_GAP_S)
        if to == "front":
            return await self._robot("front", {}, args.get("_trace"))
        return await self._robot("yaw", {"to": to}, args.get("_trace"))

    async def face_front(self, args):
        return await self._robot("front", {}, args.get("_trace"))

    async def turn_around(self, args):
        return await self._robot("turn_around", {}, args.get("_trace"))

    async def tracking(self, args):
        if not isinstance(args.get("on"), bool):
            raise AskError("bad_args", "on 要是 true / false")
        return await self._robot("tracking", {"on": args["on"]}, args.get("_trace"))

    async def nod(self, args):
        return await self._robot("nod", {"times": self._int(args, "times", 1, 2, 2)}, args.get("_trace"))

    async def shake_head(self, args):
        return await self._robot("shake", {"times": self._int(args, "times", 1, 2, 2)}, args.get("_trace"))

    async def set_volume(self, args):
        level, step = self._int(args, "level", 10, 100), self._int(args, "step", -30, 30)
        if (level is None) == (step is None):
            raise AskError("bad_args", "给 level（10..100）或 step（-30..30）其中一个")
        return await self._robot("volume", {"level": level} if level is not None else {"step": step}, args.get("_trace"))

    # 0.5.21 (owner 2026-11-20): the body's 12 RGB LEDs. color: "#RRGGBB", a few Chinese / English colour names, or off.
    LIGHT_NAMES = {"off": (0, 0, 0), "关": (0, 0, 0), "关灯": (0, 0, 0), "红": (255, 0, 0), "红色": (255, 0, 0),
                   "red": (255, 0, 0), "绿": (0, 255, 0), "绿色": (0, 255, 0), "green": (0, 255, 0), "蓝": (0, 0, 255),
                   "蓝色": (0, 0, 255), "blue": (0, 0, 255), "白": (255, 255, 255), "白色": (255, 255, 255),
                   "white": (255, 255, 255), "黄": (255, 200, 0), "黄色": (255, 200, 0), "yellow": (255, 200, 0),
                   "紫": (160, 0, 255), "紫色": (160, 0, 255), "purple": (160, 0, 255), "粉": (255, 80, 160),
                   "粉色": (255, 80, 160), "pink": (255, 80, 160), "橙": (255, 100, 0), "橙色": (255, 100, 0),
                   "orange": (255, 100, 0), "青": (0, 200, 200), "青色": (0, 200, 200), "cyan": (0, 200, 200)}

    async def set_light(self, args):
        c = str(args.get("color") or "").strip().lower()
        rgb = self.LIGHT_NAMES.get(c)
        if rgb is None and re.fullmatch(r"#?[0-9a-f]{6}", c):
            h = c.lstrip("#")
            rgb = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        if rgb is None:
            raise AskError("bad_args", "color 给 #RRGGBB、颜色名（红 / 绿 / 蓝 / 白 / 黄 / 紫 / 粉 / 橙 / 青）或 off")
        return await self._robot("light", {"r": rgb[0], "g": rgb[1], "b": rgb[2]}, args.get("_trace"))

    async def face_learn_propose(self, args):
        name = str(args.get("name") or "").strip()
        if not name or len(name.encode()) > 32:
            raise AskError("bad_args", "名字要 1..32 字节")
        return await self._robot("face_learn", {"name": name}, args.get("_trace"), LEARN_TIMEOUT_S)

    async def robot_status(self, args):
        return status_view(await self._robot("status", {}, args.get("_trace")))

    async def test_turn(self, args):
        """Long-run check only (never a model tool: not in the registry): a sentence through the real Docking path."""
        hub = self._docking()
        sp = {"band": args.get("band"), "name": args.get("voice")} if args.get("band") else None
        r = await hub.test_turn(str(args.get("text") or "")[:200], speaker=sp, called=bool(args.get("called")))
        if r != "sent":
            raise AskError("not_available", f"测试没送出（{r}）")
        return {"sent": True}

    NAMES = ("who_is_here", "look_now", "web_search", "say", "stop_speaking", "set_length", "set_quiet", "set_role",
             "set_debug", "debug_alert",
             "express_eyes", "show_word", "play_clip", "turn_head", "face_front", "turn_around", "tracking", "nod",
             "shake_head", "set_volume", "face_learn_propose", "robot_status", "set_voice", "test_turn", "set_light")

    async def handle(self, name, args):
        if name not in self.NAMES:
            raise AskError("not_available", f"耳朵没有 {str(name)[:40]}")
        if not isinstance(args, dict):
            raise AskError("bad_args", "参数应该是一个对象")
        return await getattr(self, name)(args)


FACING = {"front": "前面", "back": "背对（转过身了）", "unknown": "不确定"}


def head_direction(facing, yaw):
    """facing is only front / back (turned round); left / right comes from yaw (negative = left, 2026-11-18 test:
    left45 -> -42°)."""
    if facing == "back":
        return FACING["back"]
    if not isinstance(yaw, (int, float)):
        return FACING.get(facing, facing)
    if abs(yaw) < 12:
        return "正前方"
    side = "左" if yaw < 0 else "右"
    return f"偏{side} {abs(yaw):.0f}°"


def status_view(d):
    """The robot's status data (robot_cmd.cpp) -> robot_status for mind: the registry's fields, values a model can say."""
    d = d if isinstance(d, dict) else {}
    num = lambda k: d[k] if isinstance(d.get(k), (int, float)) and not isinstance(d.get(k), bool) else None  # noqa: E731
    out = {"firmware": d.get("fw"), "mode": d.get("mode"), "volume": num("volume"),
           "yaw": num("yaw"), "pitch": num("pitch"), "facing": head_direction(d.get("facing"), num("yaw")),
           "tracking": d.get("tracking"), "motor_on": d.get("motor"), "moving": d.get("moving"),
           "m12_online": d.get("m12"), "camera_on": d.get("camera"), "speaking": d.get("speaking"),
           "battery": num("battery"), "charging": d.get("charging"), "chip_c": num("chip_c"), "lux": num("lux"),
           "wifi_rssi": num("rssi"), "uptime_min": int(num("up_s") // 60) if num("up_s") is not None else None,
           "free_heap_kb": int(num("heap") // 1024) if num("heap") is not None else None,
           "free_psram_kb": int(num("psram") // 1024) if num("psram") is not None else None}
    bits = []
    if out["battery"] is not None:
        bits.append(f"电量 {out['battery']:g}%" + ("（在充电）" if out["charging"] else ""))
    if out["volume"] is not None:
        bits.append(f"音量 {out['volume']:g}")
    if out["yaw"] is not None:
        pitch = f"{out['pitch']:g}°" if out["pitch"] is not None else "?"
        bits.append(f"头朝{out['facing'] or '?'}（左右 {out['yaw']:g}°，抬头 {pitch}）")
    bits.append("在跟着人转" if out["tracking"] else "没在跟着人转")
    bits.append("M12 在线" if out["m12_online"] else "M12 不在线")
    if out["chip_c"] is not None:
        bits.append(f"芯片 {out['chip_c']:.0f}°C")
    out["summary"] = "，".join(bits)
    return {k: v for k, v in out.items() if v is not None}


def _response(status, obj):
    body = json.dumps(obj, ensure_ascii=False).encode()
    reason = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found", 413: "Too Large"}.get(status, "OK")
    return (f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode() + body


async def serve(asks, token, host="127.0.0.1", port=52743):
    async def client(reader, writer):
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            lines = head.decode("latin-1").split("\r\n")
            method, path = (lines[0].split(" ") + ["", ""])[:2]
            hdr = {k.strip().lower(): v.strip() for k, v in (l.split(":", 1) for l in lines[1:] if ":" in l)}
            supplied = hdr.get("authorization", "")[len("Bearer "):] if hdr.get("authorization", "").startswith("Bearer ") else ""
            if not token or not hmac.compare_digest(supplied.encode(), token.encode()):
                writer.write(_response(401, {"error": "bad token"}))
                return
            if method != "POST" or path != "/ask":
                writer.write(_response(404, {"error": "POST /ask only"}))
                return
            n = int(hdr.get("content-length") or 0)
            if n > MAX_BODY:
                writer.write(_response(413, {"error": "too large"}))
                return
            req = json.loads((await asyncio.wait_for(reader.readexactly(n), 5)).decode() or "{}")
            name, args = req.get("name"), dict(req.get("args") or {})
            if req.get("trace"):
                args["_trace"] = str(req["trace"])[:64]
            try:
                data = await asks.handle(name, args)
                out = {"ok": True, "data": data, "error": None}
            except AskError as e:
                out = {"ok": False, "data": None, "error": {"code": e.code, "message": e.message}}
            except Exception as e:                       # noqa: BLE001 - one failed ask, never the ear
                out = {"ok": False, "data": None, "error": {"code": "failed", "message": f"耳朵出错了（{type(e).__name__}）"}}
            writer.write(_response(200, out))
        except Exception:                                # noqa: BLE001 - a broken request
            try:
                writer.write(_response(400, {"error": "bad request"}))
            except Exception:                            # noqa: BLE001
                pass
        finally:
            try:
                await writer.drain()
                writer.close()
            except Exception:                            # noqa: BLE001
                pass
    return await asyncio.start_server(client, host, port)
