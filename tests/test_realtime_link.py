"""围观 realtime spike (2026-11-14), ear half: server/ear/realtime_link.py RealtimeHub with a fake DashScope socket, a fake
robot link and a fake clock; MouthLink's routing between soul-tts and realtime. Nothing is paid or played. Needs
websockets + opuslib_next (run in .private/venv-ear):

    .private/venv-ear/bin/python -B -m unittest discover -s tests -p 'test_realtime_link.py' -v
"""
import asyncio
import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server" / "ear"))
try:
    import opuslib_next  # noqa: F401
    import websockets  # noqa: F401
    HAVE = True
except ImportError:
    HAVE = False

CTX = {"text": "此刻的情景：大家在聊午饭", "owner": {"face_names": ["boss_face"], "call": "阿大"},
       "names": {"guest_face": "小王"}, "visible": [], "at": 0}


class FakeWS:
    def __init__(self):
        self.sent, self.closed, self.q = [], False, asyncio.Queue()

    async def send(self, m):
        self.sent.append(json.loads(m))

    def push(self, e):
        self.q.put_nowait(json.dumps(e))

    def __aiter__(self):
        return self

    async def __anext__(self):
        m = await self.q.get()
        if m is None:
            raise StopAsyncIteration
        return m

    async def close(self):
        if not self.closed:
            self.closed = True
            self.q.put_nowait(None)

    def kinds(self):
        return [m["type"] for m in self.sent]

    def appended(self):
        return b"".join(base64.b64decode(m["audio"]) for m in self.sent if m["type"] == "input_audio_buffer.append")


class FakeLink:
    def __init__(self):
        self.tts_active, self.sent, self.alive = set(), [], True

    async def send_from_realtime(self, m):
        if not self.alive:
            return False
        self.sent.append(m if isinstance(m, bytes) else json.loads(m))
        return True

    def texts(self):
        return [m for m in self.sent if isinstance(m, dict)]

    def frames(self):
        return [m for m in self.sent if isinstance(m, bytes)]


class Modes:
    def __init__(self, link):
        self.robot_mode, self._mode_conn, self._links = "crowd", 1, {1: link}


class Switch:
    on = True


class Records:
    def __init__(self):
        self.turns, self.status = [], []

    def realtime_turn(self, doc):
        self.turns.append(doc)

    def switch_status(self, name, fields):
        self.status.append(fields)


async def settle(n=8):
    for _ in range(n):
        await asyncio.sleep(0.005)


def pcm24(ms):
    t = np.arange(24 * ms) / 24000
    return (np.sin(2 * np.pi * 440 * t) * 8000).astype("<i2").tobytes()


@unittest.skipUnless(HAVE, "needs websockets and opuslib_next")
class HubBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import realtime_link as rl
        self.rl = rl
        self.logs = []
        rl.log = lambda *a, **k: self.logs.append(" ".join(str(x) for x in a))
        self.t = [1000.0]
        self.wss, self.connects = [], []
        self.link = FakeLink()
        self.modes, self.switch, self.recs = Modes(self.link), Switch(), Records()
        self.switch.on = True
        self.ctx_calls = 0

        async def connect(url, **kw):
            self.connects.append((url, kw))
            ws = FakeWS()
            self.wss.append(ws)
            return ws

        def call(method, path, body=None, timeout=5):
            self.ctx_calls += 1
            return 200, json.loads(json.dumps(CTX))
        self.hub = rl.RealtimeHub(key="k", voice="v-secret", modes=self.modes, call=call, switch=self.switch,
                                  db=self.recs, connect=connect, clock=lambda: self.t[0])

    async def asyncTearDown(self):
        await self.hub.shutdown()

    async def open_ready(self):
        self.hub.step()
        await settle(20)
        ws = self.wss[-1]
        ws.push({"type": "session.updated", "session": {}})
        await settle()
        self.assertEqual(self.hub.state, "open")
        return ws

    def speak(self, n=10, speaking=True):
        for _ in range(n):
            self.hub.feed(b"\x01\x00" * 960, speaking, False)

    async def play_one(self, ws, rid="r1", ms=150):
        ws.push({"type": "input_audio_buffer.speech_stopped"})
        ws.push({"type": "response.created", "response": {"id": rid}})
        ws.push({"type": "response.audio.delta", "delta": base64.b64encode(pcm24(ms)).decode()})
        ws.push({"type": "response.audio_transcript.done", "transcript": "你好呀😀"})
        ws.push({"type": "response.done", "response": {"id": rid, "status": "completed", "usage": {
            "input_tokens_details": {"text_tokens": 400, "audio_tokens": 10},
            "output_tokens_details": {"text_tokens": 5, "audio_tokens": 20}}}})
        await settle(20)
        sid = self.link.texts()[-1]["stream"]
        self.hub.on_report(self.link, {"type": "tts.playing", "stream": sid})
        self.hub.on_report(self.link, {"type": "tts.done", "stream": sid, "played_ms": ms})
        await settle()
        return sid



class SessionTest(HubBase):
    async def test_open_follows_crowd_and_switch(self):
        ws = await self.open_ready()
        upd = ws.sent[0]
        self.assertEqual(upd["type"], "session.update")
        s = upd["session"]
        self.assertEqual((s["voice"], s["turn_detection"]["type"], s["turn_detection"]["silence_duration_ms"],
                          s["turn_detection"]["threshold"]), ("v-secret", "server_vad", 1000, 0.6))
        self.assertIn("此刻的情景：大家在聊午饭", s["instructions"])
        self.assertIn("qwen-audio-3.1-realtime-flash", self.connects[0][0])
        self.assertEqual(self.connects[0][1]["additional_headers"], {"Authorization": "Bearer k"})
        self.switch.on = False
        self.hub.step()
        await settle()
        self.assertEqual((self.hub.state, ws.closed), ("closed", True))
        self.switch.on = True
        self.hub.step()
        await settle(20)
        self.assertEqual(len(self.connects), 2)
        self.modes.robot_mode = "chat"
        self.hub.step()
        await settle()
        self.assertEqual((self.hub.state, self.wss[-1].closed), ("closed", True))
        self.hub.step()
        self.assertEqual(len(self.connects), 2)                  # not in crowd: nothing opens

    async def test_robot_leaving_closes_at_once(self):
        ws = await self.open_ready()
        self.hub.link_closed(self.link)
        await settle()
        self.assertEqual((self.hub.state, ws.closed), ("closed", True))
        self.modes._links = {}
        self.hub.step()
        self.assertEqual(len(self.connects), 1)

    async def test_no_voice_never_connects(self):
        self.hub.voice = None
        self.hub.step()
        self.speak()
        await settle()
        self.assertEqual(self.connects, [])

    async def test_voice_is_never_logged_and_a_setup_error_closes(self):
        self.hub.step()
        await settle(20)
        self.wss[-1].push({"type": "error", "error": {"code": "InvalidParameter", "message": "bad voice v-secret"}})
        await settle()
        self.assertEqual(self.hub.state, "closed")
        self.assertTrue(any("<voice>" in line for line in self.logs))
        self.assertFalse(any("v-secret" in line for line in self.logs))
        self.hub.step()
        self.assertEqual(len(self.connects), 1)                  # retry later, not at once

    async def test_context_refreshes_with_session_update(self):
        ws = await self.open_ready()
        self.t[0] += 61
        self.speak(1)                                            # keeps it from idling
        self.hub.step()
        await settle(20)
        updates = [m for m in ws.sent if m["type"] == "session.update"]
        self.assertEqual(len(updates), 2)
        self.assertNotIn("voice", updates[1]["session"])          # the voice goes only in the first update
        self.assertIn("instructions", updates[1]["session"])


class InputTest(HubBase):
    async def test_silence_is_not_sent_speech_is_with_preroll(self):
        ws = await self.open_ready()
        for _ in range(30):
            self.hub.feed(b"\x00\x00" * 960, False, False)        # 1.8 s silence: only the pre-roll keeps some
        await settle()
        self.assertEqual(ws.appended(), b"")
        self.speak(5)
        await settle()
        sent = ws.appended()
        self.assertGreaterEqual(len(sent), 5 * 1920 + int(0.8 * 16000 * 2) - 1920)
        self.t[0] += 0.5
        self.speak(2, speaking=False)                             # the tail still goes up (server VAD needs it)
        self.t[0] += 3
        self.speak(2, speaking=False)                             # after the tail: nothing
        await settle()
        self.assertEqual(len(ws.appended()), len(sent) + 2 * 1920)

    async def test_nothing_goes_up_while_hello_sounds_or_answers(self):
        ws = await self.open_ready()
        for _ in range(5):
            self.hub.feed(b"\x01\x00" * 960, True, True)           # the robot says it is sounding
        await settle()
        self.assertEqual(ws.appended(), b"")
        ws.push({"type": "response.created", "response": {"id": "r1"}})
        await settle()
        self.speak(5)                                             # a response is being generated: never interrupted
        await settle()
        self.assertEqual(ws.appended(), b"")
        self.assertNotIn("response.cancel", ws.kinds())
        ws.push({"type": "response.done", "response": {"id": "r1", "usage": {}}})
        await settle()
        self.t[0] += 0.1
        self.speak(1)
        await settle()
        self.assertGreater(len(ws.appended()), 0)

    async def test_quiet_guard_after_playback(self):
        ws = await self.open_ready()
        await self.play_one(ws)
        self.speak(3)                                             # right after the robot finished: guard
        await settle()
        self.assertEqual(ws.appended(), b"")
        self.t[0] += 1
        self.speak(3)
        await settle()
        self.assertGreater(len(ws.appended()), 0)

class OutputTest(HubBase):
    async def test_framing_matches_soul_tts(self):
        import opuslib_next
        ws = await self.open_ready()
        self.t[0] += 0.3
        sid = await self.play_one(ws, ms=100)                     # 100 ms -> one whole frame + a padded one
        start, end = self.link.texts()[0], self.link.texts()[1]
        self.assertEqual({k: start[k] for k in ("type", "codec", "sample_rate", "frame_ms")},
                         {"type": "tts.start", "codec": "opus", "sample_rate": 24000, "frame_ms": 60})
        self.assertGreaterEqual(start["stream"], self.rl.RT_STREAM_BASE)
        frames = self.link.frames()
        self.assertEqual(len(frames), 2)
        dec = opuslib_next.Decoder(24000, 1)
        for seq, f in enumerate(frames):
            magic, stream, s, fms, flags = self.rl.HDR.unpack_from(f)
            self.assertEqual((magic, stream, s, fms, flags), (b"SPK1", sid, seq, 60, 0))
            self.assertEqual(len(dec.decode(f[16:], 1440)), 2880)
        self.assertEqual(end, {"type": "tts.end", "stream": sid, "frames": 2, "duration_ms": 120})
        turn = self.recs.turns[-1]
        self.assertEqual((turn["state"], turn["kind"], turn["reply_chars"]), ("done", "speech", 3))
        self.assertGreater(turn["cost_cny"], 0)
        self.assertEqual(turn["usage"], {"in_text": 400, "in_audio": 10, "out_text": 5, "out_audio": 20})
        self.assertFalse(any(isinstance(v, str) and "你好" in v for v in turn.values()))
        self.assertIsNotNone(turn["timings"]["speech_end_to_robot_sound_ms"])

    async def test_next_stream_waits_for_the_robot_done(self):
        ws = await self.open_ready()
        for rid in ("r1", "r2"):
            ws.push({"type": "response.created", "response": {"id": rid}})
            ws.push({"type": "response.audio.delta", "delta": base64.b64encode(pcm24(60)).decode()})
            ws.push({"type": "response.done", "response": {"id": rid, "usage": {}}})
        await settle(20)
        self.assertEqual([m["type"] for m in self.link.texts()], ["tts.start", "tts.end"])
        self.hub.on_report(self.link, {"type": "tts.done", "stream": self.link.texts()[0]["stream"], "played_ms": 60})
        await settle(20)
        self.assertEqual([m["type"] for m in self.link.texts()], ["tts.start", "tts.end", "tts.start", "tts.end"])


class YieldTest(HubBase):
    async def test_soul_tts_busy_cancels_the_response(self):
        ws = await self.open_ready()
        self.link.tts_active.add(3)
        ws.push({"type": "response.created", "response": {"id": "r1"}})
        ws.push({"type": "response.audio.delta", "delta": base64.b64encode(pcm24(200)).decode()})
        await settle(20)
        self.assertIn("response.cancel", ws.kinds())
        self.assertEqual(self.link.sent, [])

    async def test_soul_tts_start_mid_stream_drops_ours(self):
        ws = await self.open_ready()
        ws.push({"type": "response.created", "response": {"id": "r1"}})
        ws.push({"type": "response.audio.delta", "delta": base64.b64encode(pcm24(120)).decode()})
        await settle(20)
        n = len(self.link.sent)
        self.assertGreater(n, 1)
        self.link.tts_active.add(1)
        self.hub.yield_to_tts(self.link)
        ws.push({"type": "response.audio.delta", "delta": base64.b64encode(pcm24(600)).decode()})
        ws.push({"type": "response.done", "response": {"id": "r1", "usage": {}}})
        await settle(20)
        self.assertEqual(len(self.link.sent), n)                  # nothing of ours after soul-tts's start
        self.assertIn("response.cancel", ws.kinds())
        self.assertEqual(self.recs.turns[-1]["state"], "yielded")
        ws.push({"type": "response.created", "response": {"id": "r2"}})
        ws.push({"type": "response.audio.delta", "delta": base64.b64encode(pcm24(120)).decode()})
        await settle(20)
        self.assertEqual(len(self.link.sent), n)                  # soul-tts still playing: no new stream either


class CapTest(HubBase):
    async def test_session_cap_then_only_on_speech(self):
        await self.open_ready()
        self.t[0] += 300
        self.speak(1)
        self.hub.step()
        self.assertEqual(self.hub.state, "open")
        self.t[0] += 301
        self.speak(1, speaking=False)
        self.hub.step()
        self.assertEqual(self.hub.state, "closed")
        self.assertTrue(any("session_cap" in line for line in self.logs))
        self.t[0] += 5
        self.hub.step()
        self.assertEqual(len(self.connects), 1)                  # not again until somebody speaks
        self.speak(1, speaking=False)
        self.speak(1)                                            # onset
        await settle(20)
        self.assertEqual(len(self.connects), 2)

    async def test_idle_close(self):
        await self.open_ready()
        self.t[0] += self.hub.cfg.idle_s + 1
        self.hub.step()
        self.assertEqual(self.hub.state, "closed")
        self.assertTrue(any("(idle)" in line for line in self.logs))

    async def test_hour_cap(self):
        self.hub.cfg.hour_cap_s = 100
        await self.open_ready()
        self.t[0] += 101
        self.speak(1)
        self.hub.step()
        self.assertEqual(self.hub.state, "closed")
        self.speak(1, speaking=False)
        self.speak(1)
        self.hub.step()
        self.assertEqual(len(self.connects), 1)                  # still capped within the hour
        self.t[0] += 3600
        self.speak(1, speaking=False)
        self.speak(1)
        await settle(20)
        self.assertEqual(len(self.connects), 2)


class FaceViewTest(unittest.TestCase):
    def setUp(self):
        import realtime_link as rl
        self.rl = rl
        self.v = rl.FaceView(away_s=300)

    def test_appear_rules(self):
        v = self.v
        self.assertIsNone(v.event({"event": "face_found", "face": 1}, 0))
        self.assertEqual(v.event({"event": "face_named", "face": 1, "name": "boss_face"}, 1), "boss_face")
        self.assertIsNone(v.event({"event": "face_named", "face": 2, "name": "boss_face"}, 100))   # still in view
        v.event({"event": "face_lost", "face": 1}, 110)
        v.event({"event": "face_lost", "face": 2}, 120)
        self.assertIsNone(v.event({"event": "face_named", "face": 3, "name": "boss_face"}, 300))   # away 180 s
        v.event({"event": "face_lost", "face": 3}, 310)
        self.assertEqual(v.event({"event": "face_named", "face": 4, "name": "boss_face"}, 611), "boss_face")
        self.assertTrue(v.present(["boss_face"], 612))
        v.event({"event": "vision_off"}, 620)
        self.assertFalse(v.present(["boss_face"], 621))

    def test_recent_keeps_a_lost_face_two_seconds_largest_first(self):
        v = self.v
        v.event({"event": "face_found", "face": 1, "box": [0, 0, 50, 50]}, 0)
        v.event({"event": "face_found", "face": 2, "box": [0, 0, 100, 100]}, 0)
        v.event({"event": "face_attrs", "face": 2, "gender": "f", "age_group": "adult"}, 0.5)
        v.event({"event": "face_lost", "face": 2}, 1)
        self.assertEqual([f["area"] for f in v.recent(2.5)], [10000, 2500])
        self.assertEqual([f["area"] for f in v.recent(3.5)], [2500])

    def test_address_and_tap_text(self):
        rl = self.rl
        cases = {("f", "adult"): "美女", ("f", "young"): "美女", ("m", "adult"): "帅哥", ("m", "young"): "帅哥",
                 ("m", "child"): "小朋友", ("?", "child"): "小朋友", ("m", "elder"): "爷爷", ("f", "elder"): "奶奶",
                 ("?", "adult"): None, (None, None): None, ("f", None): None}
        for (g, a), want in cases.items():
            self.assertEqual(rl.address(g, a)[0], want, (g, a))
        big = {"name": None, "area": 9, "gender": "f", "age_group": "adult"}
        self.assertIn("“美女”", rl.tap_text(rl.tap_pick([big]), {}, ["boss_face"], "阿大"))
        neutral = rl.tap_text(rl.tap_pick([{"name": None, "area": 9, "gender": "?", "age_group": None}]), {}, [], "x")
        self.assertIn("友好地问声好", neutral)
        self.assertIn("没看到人脸", rl.tap_text(rl.tap_pick([]), {}, [], "x"))
        known = [{"name": "guest_face", "area": 5, "gender": "m", "age_group": "adult"}, big,
                 {"name": "boss_face", "area": 1, "gender": "m", "age_group": "adult"}]
        known.sort(key=lambda f: -f["area"])
        text = rl.tap_text(rl.tap_pick(known), {"guest_face": "小王"}, ["boss_face"], "阿大")
        self.assertIn("面前是小王，旁边还有主人（阿大）", text)     # a known face wins over a bigger unknown one


class GreetTest(HubBase):
    async def test_owner_appearing_is_greeted_once(self):
        ws = await self.open_ready()
        self.hub.on_vision({"event": "face_named", "face": 1, "name": "boss_face"})
        self.hub.step()
        await settle()
        items = [m for m in ws.sent if m["type"] == "conversation.item.create"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["item"]["content"][0]["text"], "[事件] 主人（阿大）出现在你面前了。")
        self.assertEqual(ws.kinds()[-1], "response.create")
        self.assertFalse(any("阿大" in line for line in self.logs))
        self.hub.on_vision({"event": "face_lost", "face": 1})
        self.t[0] += 301
        self.speak(1)
        self.hub.on_vision({"event": "face_named", "face": 2, "name": "boss_face"})
        self.speak(1, speaking=False)
        self.t[0] += 0.1
        self.hub.step()
        await settle()
        self.assertEqual(len([m for m in ws.sent if m["type"] == "conversation.item.create"]), 2)

    async def test_others_and_unknown_are_not_greeted_and_not_outside_crowd(self):
        ws = await self.open_ready()
        self.hub.on_vision({"event": "face_named", "face": 1, "name": "guest_face"})
        self.hub.on_vision({"event": "face_found", "face": 2})
        self.hub.step()
        await settle()
        self.assertNotIn("conversation.item.create", ws.kinds())
        self.modes.robot_mode = "talk"
        self.hub.on_vision({"event": "face_named", "face": 3, "name": "boss_face"})
        self.assertIsNone(self.hub._greet)

    async def test_waits_while_sounding_and_drops_when_old(self):
        ws = await self.open_ready()
        self.hub.feed(b"\0\0" * 960, False, True)                 # hello sounds
        self.hub.on_vision({"event": "face_named", "face": 1, "name": "boss_face"})
        self.hub.step()
        await settle()
        self.assertNotIn("conversation.item.create", ws.kinds())
        self.t[0] += 11
        self.hub.step()
        self.hub.feed(b"\0\0" * 960, False, False)
        self.hub.step()
        await settle()
        self.assertNotIn("conversation.item.create", ws.kinds())
        self.assertTrue(any("too old" in line for line in self.logs))

    async def test_owner_already_in_view_at_session_start(self):
        self.hub.on_vision({"event": "face_named", "face": 1, "name": "boss_face"})   # before crowd: only tracked
        ws = await self.open_ready()
        self.hub.step()
        await settle()
        self.assertIn("conversation.item.create", ws.kinds())

    async def test_tap_greets_after_the_reply_and_has_a_cooldown(self):
        ws = await self.open_ready()
        self.hub.on_vision({"event": "face_found", "face": 9, "box": [0, 0, 200, 200]})
        self.hub.on_vision({"event": "face_attrs", "face": 9, "gender": "m", "age_group": "young"})
        ws.push({"type": "response.created", "response": {"id": "r1"}})
        await settle()
        self.assertTrue(self.hub.tap())
        self.assertFalse(self.hub.tap())                          # 3 s cooldown
        self.hub.step()
        await settle()
        self.assertNotIn("conversation.item.create", ws.kinds())  # hello is answering: never cut, it waits
        self.assertNotIn("response.cancel", ws.kinds())
        ws.push({"type": "response.done", "response": {"id": "r1", "usage": {}}})
        await settle()
        self.hub.step()
        await settle()
        text = [m for m in ws.sent if m["type"] == "conversation.item.create"][0]["item"]["content"][0]["text"]
        self.assertIn("“帅哥”", text)
        self.assertTrue(any("tap greet: unknown" in line for line in self.logs))
        self.t[0] += 3.1
        self.assertTrue(self.hub.tap())


class SeenTest(HubBase):
    async def test_live_view_in_instructions_and_pushed_on_change(self):
        ws = await self.open_ready()
        first = ws.sent[0]["session"]["instructions"]
        self.assertIn("【此刻看到】\n眼前没有人脸。", first)
        self.assertIn("你有眼睛", first)
        self.hub.on_vision({"event": "face_named", "face": 1, "name": "boss_face", "box": [0, 0, 100, 100]})
        self.hub.on_vision({"event": "face_attrs", "face": 2, "gender": "f", "age_group": "adult", "box": [0, 0, 50, 50]})
        self.t[0] += 0.5
        self.hub.step()
        await settle()
        self.assertEqual(sum(1 for m in ws.sent if m["type"] == "session.update"), 1)      # < seen_min_s
        self.t[0] += 3
        self.hub.step()
        await settle()
        ups = [m for m in ws.sent if m["type"] == "session.update"]
        self.assertEqual(len(ups), 2)
        self.assertIn("眼前有 2 个人：主人（阿大）、一位美女。", ups[-1]["session"]["instructions"])
        self.t[0] += 5
        self.hub.step()
        await settle()
        self.assertEqual(sum(1 for m in ws.sent if m["type"] == "session.update"), 2)      # unchanged: no resend
        self.assertFalse(any("阿大" in line or "美女" in line for line in self.logs))

    def test_seen_text(self):
        rl = self.rl
        self.assertEqual(rl.seen_text([], {}, [], "x"), "眼前没有人脸。")
        faces = [{"name": "g1"}, {"name": None, "gender": None, "age_group": None}]
        self.assertEqual(rl.seen_text(faces, {"g1": "人物甲"}, [], "x"), "眼前有 2 个人：人物甲、一个认不出的人。")


class TextTest(unittest.TestCase):
    def test_emoji_cost_voice(self):
        import realtime_link as rl
        self.assertEqual(rl.strip_emoji("哼！生气啦😠 好❤️"), "哼！生气啦 好")
        self.assertEqual(rl.cost_cny({"input_tokens_details": {"text_tokens": 1_000_000},
                                      "output_tokens_details": {"text_tokens": 1_000_000, "audio_tokens": 1_000_000}}),
                         13.5)                                   # text out is free when audio comes out
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "voices.json")
            Path(p).write_text(json.dumps({"voices": {"k1": {"voice": "vid"}}}))
            self.assertEqual((rl.load_voice(p, "k1"), rl.load_voice(p, "k2"), rl.load_voice(d + "/x", "k1")),
                             ("vid", None, None))


@unittest.skipUnless(HAVE, "needs websockets")
class MouthRoutingTest(unittest.IsolatedAsyncioTestCase):
    """MouthLink: realtime reports never reach soul-tts; a soul-tts tts.start makes realtime yield first; crowd tap."""

    async def asyncSetUp(self):
        import mouth_link
        self.ml = mouth_link
        mouth_link.log = lambda *a, **k: None
        self.order = []
        test = self

        class Hub:
            def on_report(self, link, m):
                test.order.append(("hub", m["type"]))

            def yield_to_tts(self, link):
                test.order.append(("yield", sorted(link.tts_active)))

            def tap(self):
                test.order.append(("tap",))

            def link_closed(self, link):
                pass
        self.hub = Hub()

    def make(self, robot_msgs):
        test = self

        class Client:
            def __init__(self):
                self.msgs = list(robot_msgs)

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self.msgs:
                    raise StopAsyncIteration
                return self.msgs.pop(0)

            async def send(self, m):
                test.order.append(("robot", json.loads(m)["type"] if isinstance(m, str) else "frame"))
        link = self.ml.MouthLink(Client(), upstream_url="ws://x", token="t", connect=None, realtime=self.hub)
        return link

    async def test_realtime_reports_go_to_the_hub_not_soul_tts(self):
        base = self.ml.RT_STREAM_BASE
        link = self.make([json.dumps({"type": "tts.done", "stream": base + 1}),
                          json.dumps({"type": "tts.done", "stream": 4})])
        link.tts_active.add(4)

        class Up:
            async def send(s, m):
                self.order.append(("soul-tts", json.loads(m)["stream"]))
        link._up = Up()
        await link._from_robot()
        self.assertEqual(self.order, [("hub", "tts.done"), ("soul-tts", 4)])
        self.assertEqual(link.tts_active, set())

    async def test_soul_tts_start_makes_realtime_yield_before_the_robot_hears_it(self):
        link = self.make([])

        class Up:
            close_code = 1000

            def __init__(s):
                s.msgs = [json.dumps({"type": "tts.start", "stream": 7}), json.dumps({"type": "tts.end", "stream": 7})]

            async def send(s, m):
                pass

            def __aiter__(s):
                return s

            async def __anext__(s):
                if not s.msgs:
                    raise StopAsyncIteration
                return s.msgs.pop(0)

            async def close(s):
                pass

        async def connect(url, **kw):
            return Up()
        link.connect = connect
        link.hello_text = json.dumps({"type": "hello"})
        await link._one_upstream()
        self.assertEqual(self.order[:2], [("yield", [7]), ("robot", "tts.start")])
        self.assertEqual(link.tts_active, {7})                    # tts.end: the robot still plays it

    async def test_crowd_tap_report_asks_the_hub_to_greet(self):
        class Relay:
            report_queue = 4

            def note_report(self, m, conn):
                pass
        link = self.make([])
        link.modes = Relay()
        await link._mode_message({"type": "mode_report", "seq": 1, "mode": "crowd", "input": "tap", "effects": []})
        await link._mode_message({"type": "mode_report", "seq": 2, "mode": "chat", "input": "tap", "effects": []})
        self.assertEqual(self.order, [("tap",)])


if __name__ == "__main__":
    unittest.main()
