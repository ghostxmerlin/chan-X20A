"""视觉爆发 vision burst (2026-11-15), ear side: server/ear/vision_burst.py with a fake realtime hub, a fake robot that
answers snaps the way collector-0.4.99 does (result and frame in either order, busy / camera_off / no_uplink / timeout,
or no reply at all), and a fake recogniser. Nothing is paid. RealtimeHub's 【你的眼睛】 looks are tested with the real
realtime_link.instructions(). Needs websockets + opuslib_next for the hub part (run in .private/venv-ear):

    .private/venv-ear/bin/python -B -m unittest discover -s tests -p 'test_vision_burst.py' -v
"""
import asyncio
import json
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server" / "ear"))
import vision_burst as vb  # noqa: E402

try:
    import opuslib_next  # noqa: F401
    import websockets  # noqa: F401
    HAVE = True
except ImportError:
    HAVE = False

JPEG = b"\xff\xd8\xff\xe0fake-jpeg"


class Switch:
    on = True


class Robot:
    """The /mouth link: answers each snap with the next scripted behaviour (default: ok, result first)."""

    def __init__(self, burst_ref, plan=()):
        self.burst_ref, self.plan = burst_ref, list(plan)
        self.snaps, self.alive, self.tells = [], True, []
        self.seq = 100
        self.in_flight = 0
        self.max_in_flight = 0

    async def send_from_realtime(self, m):
        if not self.alive:
            return False
        m = json.loads(m)
        if m["type"] == "burst":                     # the screen indicator (collector-0.5.00)
            self.tells.append(m)
            return True
        assert m["type"] == "snap" and set(m) == {"type", "id"}, m
        self.snaps.append(m["id"])
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight + self.burst_ref.recognising)
        how = self.plan.pop(0) if self.plan else "result_first"
        asyncio.get_running_loop().call_later(0.01, self._answer, m["id"], how)
        return True

    def _answer(self, sid, how):
        b = self.burst_ref.burst
        self.seq += 1
        seq = self.seq
        result = {"type": "snap_result", "id": sid, "ok": True, "seq": seq, "bytes": len(JPEG), "ms": 120}
        head = {"type": "frame", "seq": seq, "t_us": seq * 1000, "now_us": seq * 1000 + 50, "snap": sid}
        self.in_flight -= 1
        if how == "result_first":
            b.on_snap_result(self, result)
            asyncio.get_running_loop().call_later(0.02, b.on_frame, head, JPEG, f"frames/s/{seq}.jpg", "s")
        elif how == "frame_first":
            b.on_frame(head, JPEG, f"frames/s/{seq}.jpg", "s")
            asyncio.get_running_loop().call_later(0.02, b.on_snap_result, self, result)
        elif how == "no_frame":
            b.on_snap_result(self, result)
        elif how == "seq_mismatch":
            b.on_frame(dict(head, seq=seq + 50), JPEG, "frames/s/x.jpg", "s")
            b.on_snap_result(self, result)
        elif how == "silent":
            pass                                            # a bad request / old firmware: no reply at all
        else:                                               # busy / camera_off / no_uplink / timeout
            b.on_snap_result(self, {"type": "snap_result", "id": sid, "ok": False, "why": how})


class Hub:
    def __init__(self):
        self.switch, self.mode, self.robot = Switch(), "crowd", None
        self.looks = []
        self.snapped = []                        # the burst clock when each look's photo was asked for

    def in_crowd(self):
        return self.mode == "crowd"

    def link(self):
        return self.robot

    def add_look(self, text, at_ms, snapped=None):
        self.looks.append((at_ms, text))
        self.snapped.append(snapped)

    def seen_for_look(self):
        return "眼前有 1 个人：一个认不出的人。"


class DB:
    def __init__(self):
        self.rows = []

    def vision_look(self, doc):
        self.rows.append(doc)

    def looks(self):
        return [r for r in self.rows if r["kind"] == "look"]

    def bursts(self):
        return [r for r in self.rows if r["kind"] == "burst"]


def fast_cfg(**kw):
    cfg = vb.Config()
    cfg.burst_s, cfg.snap_timeout_s, cfg.frame_wait_s, cfg.retry_s, cfg.look_timeout_s, cfg.poll_s = \
        30.0, 0.15, 0.1, 0.03, 1.0, 0.01
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


class BurstBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.logs = []
        vb.log = lambda *a, **k: self.logs.append(" ".join(str(x) for x in a))
        self.hub, self.db = Hub(), DB()
        self.recognising = 0
        self.threads, self.calls = set(), []
        self.look_result = ("桌前坐着一个人在看手机。", {"prompt_tokens": 418, "completion_tokens": 38}, 1600, None)
        self.make()

    def make(self, plan=(), **cfg):
        self.burst = vb.VisionBurst(self.hub, self.recognise, db=self.db, cfg=fast_cfg(**cfg))
        self.hub.robot = Robot(self, plan)
        return self.burst

    def recognise(self, jpeg, seen):
        self.recognising += 1
        try:
            self.threads.add(threading.current_thread().name)
            self.calls.append((jpeg, seen))
            time.sleep(0.01)
            r = self.look_result
            return r(jpeg) if callable(r) else r
        finally:
            self.recognising -= 1

    async def asyncTearDown(self):
        await self.burst.shutdown()

    async def until(self, cond, timeout=3.0):
        end = time.monotonic() + timeout
        while not cond():
            if time.monotonic() > end:
                self.fail("condition not reached")
            await asyncio.sleep(0.01)

    async def stopped(self):
        await self.until(lambda: not self.burst.running())
        return self.db.bursts()[-1]


class TriggerTest(BurstBase):
    async def test_trigger_words_start_and_extend(self):
        self.assertIsNone(self.burst.heard("这是什么"))
        self.assertEqual(self.burst.heard("hello 注意看！"), "start")
        until = self.burst.until
        await asyncio.sleep(0.05)
        self.assertEqual(self.burst.heard("仔细看看这个"), "extend")
        self.assertGreater(self.burst.until, until)
        self.assertEqual(self.burst.heard("好好看看"), "extend")
        self.assertTrue(self.burst.running())
        self.assertEqual(self.burst.heard("好了别看了"), "stop")
        row = await self.stopped()
        self.assertEqual((row["reason"], row["trigger"], row["extended"]), ("heard_stop", "注意看", 2))
        self.assertTrue(any("start (注意看)" in line for line in self.logs))
        self.assertTrue(any("stop (heard_stop)" in line for line in self.logs))
        self.assertFalse(any("别看了" in line or "这个" in line for line in self.logs))      # heard text never logged

    async def test_every_stop_word(self):
        for word in vb.STOP_WORDS:
            self.make()
            self.assertEqual(self.burst.heard("注意看"), "start")
            self.assertEqual(self.burst.heard(f"嗯{word}"), "stop")
            self.assertEqual((await self.stopped())["reason"], "heard_stop")
        self.assertIsNone(self.burst.heard("不用看了"))                  # nothing running: nothing to stop

    async def test_only_in_crowd_with_the_switch_on_and_a_link(self):
        self.hub.mode = "chat"
        self.assertIsNone(self.burst.heard("注意看"))
        self.hub.mode, self.hub.switch.on = "crowd", False
        self.assertIsNone(self.burst.heard("注意看"))
        self.hub.switch.on, robot, self.hub.robot = True, self.hub.robot, None
        self.assertIsNone(self.burst.heard("注意看"))
        self.hub.robot = robot
        self.assertFalse(self.burst.running())
        self.assertEqual(robot.snaps, [])

    async def test_owner_words_of_the_first_try_start_one(self):
        # 2026-11-15 evening: the owner's own first try ("你看看我手里拿的是什么东西") started nothing
        self.assertEqual(self.burst.heard("你看看我手里拿的是什么东西"), "start")

    async def test_ask_starts_or_extends_and_gives_a_mark(self):
        mark = self.burst.ask()
        self.assertIsInstance(mark, float)
        self.assertTrue(self.burst.running())
        self.assertIsInstance(self.burst.ask(), float)                  # extended
        self.hub.switch.on = False
        self.burst.stop("switch_off")
        await self.stopped()
        self.assertIsNone(self.burst.ask())                             # no burst can run: no mark

    async def test_one_burst_at_a_time(self):
        self.burst.start("注意看")
        first = self.burst._task
        self.assertEqual(self.burst.start("仔细看"), "extend")
        self.assertIs(self.burst._task, first)
        self.assertEqual(self.burst.stats["bursts"], 1)


class LoopTest(BurstBase):
    async def test_sequential_loop_in_both_orders(self):
        self.make(plan=["result_first", "frame_first"] * 3)
        self.burst.start("注意看")
        await self.until(lambda: len(self.db.looks()) >= 6)
        self.burst.stop("heard_stop")
        row = await self.stopped()
        looks = self.db.looks()[:6]
        robot = self.hub.robot
        self.assertEqual([d["snap_id"] for d in looks], robot.snaps[:6])
        self.assertEqual(robot.snaps[:6], list(range(1, 7)))           # ids increase
        self.assertEqual(robot.max_in_flight, 1)                       # exactly one snap or recognition at a time
        for d in looks:
            self.assertEqual((d["text"], d["session"], d["burst_id"], d["model"]), (self.look_result[0], "s",
                                                                                  row["burst_id"], "qwen3.8-flash"))
            self.assertEqual(d["usage"], {"prompt_tokens": 418, "completion_tokens": 38})
            self.assertAlmostEqual(d["cost_usd"], 6.150775e-05, places=10)        # = the bake-off's measured cost
            self.assertEqual((d["latency_ms"], d["snap_ms"]), (1600, 120))
            self.assertTrue(d["path"].startswith("frames/s/"))
        self.assertEqual(len(self.hub.looks), len(self.db.looks()))
        self.assertEqual(self.calls[0], (JPEG, "眼前有 1 个人：一个认不出的人。"))
        self.assertTrue(all(name.startswith("vision-burst") for name in self.threads))   # never on the loop
        self.assertEqual(row["looks"], len(self.db.looks()))
        self.assertAlmostEqual(row["cost_usd"], 6.150775e-05 * row["looks"], places=9)
        self.assertEqual(row["failures"], {})
        self.assertFalse(any("看手机" in line for line in self.logs))       # the description is never logged
        self.assertTrue(all(isinstance(x, float) for x in self.hub.snapped))           # look answers need it
        self.assertEqual(self.hub.snapped, sorted(self.hub.snapped))
        tells = robot.tells                                            # the screen indicator: on, after every look, off
        self.assertEqual((tells[0]["on"], tells[0]["looks"], tells[-1]), (True, 0, {"type": "burst", "on": False,
                                                                                  "looks": row["looks"]}))
        self.assertGreaterEqual(len(tells), row["looks"] + 2)
        self.assertTrue(all(0 <= t["left_s"] <= 120 for t in tells if t["on"]))

    async def test_busy_no_uplink_timeout_and_silence_are_retried(self):
        self.make(plan=["busy", "result_first", "no_uplink", "timeout", "frame_first", "silent", "no_frame",
                        "result_first", "seq_mismatch", "result_first"])
        self.burst.start("注意看")
        await self.until(lambda: len(self.db.looks()) >= 4)
        self.burst.stop("heard_stop")
        row = await self.stopped()
        self.assertEqual(row["reason"], "heard_stop")                  # two failures in a row never stop it
        self.assertEqual(row["failures"], {"busy": 1, "no_uplink": 1, "timeout": 1, "no_reply": 1, "no_frame": 1,
                                           "seq_mismatch": 1})

    async def test_no_uplink_never_stops_it(self):
        self.make(plan=["no_uplink"] * 6 + ["result_first"])     # vision link reconnecting for a while
        self.burst.start("注意看")
        await self.until(lambda: len(self.db.looks()) >= 1)
        self.burst.stop("heard_stop")
        row = await self.stopped()
        self.assertEqual(row["reason"], "heard_stop")
        self.assertEqual(row["failures"], {"no_uplink": 6})

    async def test_no_uplink_burst_still_ends_on_time(self):
        self.make(plan=["no_uplink"] * 200, burst_s=0.4)
        self.burst.start("注意看")
        row = await self.stopped()
        self.assertEqual(row["reason"], "elapsed")

    async def test_three_failures_in_a_row_stop_it(self):
        self.make(plan=["camera_off"] * 5)
        self.burst.start("注意看")
        row = await self.stopped()
        self.assertEqual(row["reason"], "failures:camera_off")
        self.assertEqual(len(self.hub.robot.snaps), 3)
        self.assertEqual(self.db.looks(), [])

    async def test_failed_recognitions_count_as_failures(self):
        self.look_result = (None, {"prompt_tokens": 400, "completion_tokens": 0}, 900, "HTTP 500")
        self.burst.start("注意看")
        row = await self.stopped()
        self.assertEqual(row["reason"], "failures:look_HTTP_500")
        self.assertEqual(len(self.db.looks()), 3)                      # each paid try is still a row
        self.assertTrue(all(d["text"] is None and d["error"] == "HTTP 500" for d in self.db.looks()))
        self.assertEqual(self.hub.looks, [])

    async def test_late_results_and_old_frames_are_ignored(self):
        self.make(plan=["silent", "result_first"])
        self.burst.start("注意看")
        await self.until(lambda: len(self.hub.robot.snaps) >= 2)
        other = Robot(self)
        self.burst.on_snap_result(other, {"type": "snap_result", "id": 2, "ok": False, "why": "busy"})   # wrong link
        self.burst.on_snap_result(self.hub.robot, {"type": "snap_result", "id": 1, "ok": True, "seq": 1})
        self.burst.on_frame({"type": "frame", "seq": 1, "snap": 1}, JPEG, "frames/s/old.jpg", "s")
        self.burst.on_frame({"type": "frame", "seq": 1}, JPEG, "frames/s/plain.jpg", "s")      # not a snap frame
        await self.until(lambda: len(self.db.looks()) >= 1)
        self.assertEqual(self.db.looks()[0]["snap_id"], 2)
        self.assertNotIn("old", self.db.looks()[0]["path"])


class StopTest(BurstBase):
    async def check(self, change, reason):
        self.burst.start("注意看")
        await self.until(lambda: len(self.db.looks()) >= 1)
        change()
        row = await self.stopped()
        self.assertEqual(row["reason"], reason)
        self.assertTrue(any(f"stop ({reason})" in line for line in self.logs))
        return row

    async def test_leaving_crowd(self):
        await self.check(lambda: setattr(self.hub, "mode", "chat"), "left_crowd")

    async def test_the_switch_going_off(self):
        await self.check(lambda: setattr(self.hub.switch, "on", False), "switch_off")

    async def test_the_mouth_link_dropping(self):
        await self.check(lambda: setattr(self.hub, "robot", None), "mouth_dropped")

    async def test_a_new_mouth_link(self):
        await self.check(lambda: setattr(self.hub, "robot", Robot(self)), "mouth_dropped")

    async def test_a_dead_robot_socket(self):
        await self.check(lambda: setattr(self.hub.robot, "alive", False), "mouth_dropped")

    async def test_time_is_up(self):
        self.make(burst_s=0.3)
        self.burst.start("注意看")
        row = await self.stopped()
        self.assertEqual(row["reason"], "elapsed")
        self.assertGreaterEqual(row["looks"], 1)
        self.assertLess(row["duration_s"], 1.5)

    async def test_cap_on_recognitions(self):
        self.make(max_looks=3)
        self.burst.start("注意看")
        row = await self.stopped()
        self.assertEqual((row["reason"], row["looks"], len(self.calls)), ("cap", 3, 3))

    async def test_shutdown_writes_the_summary(self):
        self.burst.start("注意看")
        await self.until(lambda: len(self.db.looks()) >= 1)
        await self.burst.shutdown()
        self.assertEqual(self.db.bursts()[-1]["reason"], "shutdown")


class CostTest(unittest.TestCase):
    def test_price_is_eyes(self):
        self.assertAlmostEqual(vb.cost_usd({"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}),
                               0.1126 + 0.380025)
        self.assertEqual(vb.cost_usd({}), 0)
        self.assertIsNone(vb.cost_usd(None))

    def test_request_body(self):
        msgs = vb.look_messages(JPEG, "眼前没有人脸。")
        self.assertEqual(msgs[0]["role"], "system")
        parts = msgs[1]["content"]
        self.assertIn("人脸识别此刻：眼前没有人脸。", parts[0]["text"])
        self.assertTrue(parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))


@unittest.skipUnless(HAVE, "needs websockets and opuslib_next")
class HubLooksTest(unittest.IsolatedAsyncioTestCase):
    """RealtimeHub: looks in 【你的眼睛】, the heard text goes to the burst, instructions re-sent (debounced)."""

    async def asyncSetUp(self):
        import realtime_link as rl
        self.rl = rl
        rl.log = lambda *a, **k: None
        self.t, self.wall = [1000.0], [1794729943000]

        class Modes:
            robot_mode, _mode_conn, _links = "crowd", 1, {1: object()}

            def crowd_stage(self, stage):
                return True
        self.hub = rl.RealtimeHub(key="k", voice="v", modes=Modes(), call=lambda *a, **k: (503, {}), switch=Switch(),
                                  clock=lambda: self.t[0], wall_ms=lambda: self.wall[0])

    async def asyncTearDown(self):
        await self.hub.shutdown()

    def test_lines_newest_first_within_three_minutes(self):
        now = self.wall[0]
        looks = [(now - 200_000, "太旧"), (now - 5_000, "主人在笑"), (now - 65_000, "桌上有杯子")]
        lines = self.rl.look_lines(looks, now)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("注视（") and lines[0].endswith("5 秒前）：主人在笑"))
        self.assertIn("65 秒前）：桌上有杯子", lines[1])
        hms = time.strftime("%H:%M:%S", time.localtime((now - 5_000) / 1000))
        self.assertEqual(lines[0], f"注视（{hms}，5 秒前）：主人在笑")

    def test_instructions_show_looks_in_the_eyes_section(self):
        text = self.rl.instructions("此刻的情景：聊天\n注视：12:00:01 旧的\n画面：两个人", "眼前没有人脸。", None,
                                    ["注视（12:00:09，3 秒前）：新的"])
        eyes = text.split("【你的眼睛】")[-1]
        self.assertIn("注视（12:00:09，3 秒前）：新的", eyes)
        self.assertNotIn("旧的", text)                                  # the ear's own looks win over mind's line
        self.assertNotIn("注视：", text.split("【背景】")[1].split("【你的眼睛】")[0])
        fallback = self.rl.instructions("注视：12:00:01 mind 的", None)
        self.assertIn("注视（最近 3 分钟，新的在前）：12:00:01 mind 的", fallback.split("【你的眼睛】")[-1])

    async def test_add_look_resends_instructions_debounced(self):
        sent = []

        class WS:
            async def send(self, m):
                sent.append(json.loads(m))
        self.hub._ws = WS()
        self.hub._ready.set()
        self.hub.add_look("第一张：主人在看手机", self.wall[0])
        await asyncio.sleep(0.01)
        self.assertEqual(len(sent), 1)
        self.assertIn("注视（", sent[0]["session"]["instructions"])
        self.assertIn("第一张：主人在看手机", sent[0]["session"]["instructions"])
        self.t[0] += 0.4
        self.hub.add_look("第二张", self.wall[0] + 400)
        await asyncio.sleep(0.01)
        self.assertEqual(len(sent), 1)                                 # within LOOKS_PUSH_S (3 s): waits
        self.t[0] += 2.7
        self.hub._push_looks(self.t[0])                                # what step() does every 0.2 s
        await asyncio.sleep(0.01)
        self.assertEqual(len(sent), 2)
        instr = sent[1]["session"]["instructions"]
        self.assertLess(instr.index("第二张"), instr.index("第一张"))     # newest first
        self.hub._push_looks(self.t[0] + 5)
        await asyncio.sleep(0.01)
        self.assertEqual(len(sent), 2)                                 # nothing new: nothing sent

    def test_heard_text_goes_to_the_burst(self):
        got = []

        class Burst:
            def heard(self, text):
                got.append(text)
        self.hub.burst = Burst()
        self.hub.cfg.turn = "server_vad"   # straight through; with smart_turn_v2 it waits for a real reply (test_realtime_link)
        self.hub._event({"type": "conversation.item.input_audio_transcription.completed", "transcript": "注意看"}, 1)
        self.assertEqual(got, ["注意看"])

    def test_a_burst_bug_never_costs_the_session(self):
        class Burst:
            def heard(self, text):
                raise RuntimeError("x")
        self.hub.burst = Burst()
        self.hub._event({"type": "conversation.item.input_audio_transcription.completed", "transcript": "注意看"}, 1)


if __name__ == "__main__":
    unittest.main()


class LiveEyeTest(unittest.IsolatedAsyncioTestCase):
    """即时画面 (owner 2026-11-18 "改成上传即刻理解"): each uploaded photo at once, one in flight, the newest waits."""
    class Hub:
        def __init__(self):
            self.scenes = []

        def seen_for_look(self):
            return "眼前有 1 个人：主人（阿大）。"

        def add_scene(self, text, at_ms):
            self.scenes.append(text)

    class On:
        on = True

    async def test_each_photo_is_recognised_at_once_newest_wins(self):
        seen = []

        def recognise(jpeg, seen_line=None):
            seen.append((jpeg, seen_line))
            time.sleep(0.05)
            return f"画面{jpeg.decode()}", {"prompt_tokens": 10}, 50, None
        hub, rows = self.Hub(), []

        class DB:
            def vision_look(self, doc):
                rows.append(doc)
        eye = vb.LiveEye(recognise, self.On(), hub=hub, db=DB(), gap_s=0.2)
        eye.on_frame({"seq": 1}, b"1", "p1", "s")
        await asyncio.sleep(0.01)
        eye.on_frame({"seq": 2}, b"2", "p2", "s")                     # while 1 is in flight: waits
        eye.on_frame({"seq": 3}, b"3", "p3", "s")                     # newer: 2 is skipped
        eye.on_frame({"seq": 9, "snap": 4}, b"x", "px", "s")           # a burst snap: not ours
        await asyncio.sleep(0.6)
        self.assertEqual([j for j, _ in seen], [b"1", b"3"])
        self.assertEqual(seen[0][1], "眼前有 1 个人：主人（阿大）。")
        self.assertEqual(hub.scenes, ["画面1", "画面3"])
        self.assertEqual([r["kind"] for r in rows], ["scene", "scene"])
        self.assertEqual(eye.stats["skipped"], 1)
        await eye.shutdown()

    async def test_switch_off_nothing(self):
        class Off:
            on = False
        calls = []
        eye = vb.LiveEye(lambda *a: calls.append(a), Off())
        eye.on_frame({"seq": 1}, b"1", "p", "s")
        await asyncio.sleep(0.05)
        self.assertEqual(calls, [])
        await eye.shutdown()
