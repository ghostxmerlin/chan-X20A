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
        self.stages = []

    def crowd_stage(self, stage):
        self.stages.append(stage)
        return True


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
    async def test_a_pause_closes_the_session_and_a_face_reopens_it(self):
        """mind/pause.py (2026-11-17): paused = the switch seen through presence.Gated is off (the owner's switch stays
        on); a face this ear sees lifts an away pause here at once."""
        import presence as pr
        ws = await self.open_ready()
        seen = pr.Presence(clock_ms=lambda: int(self.t[0] * 1000))
        doc = {"on": True, "why": "away", "reasons": ["away"], "since": 5, "away_since": 5}
        pause = pr.Pause(lambda: doc, seen, clock_ms=lambda: int(self.t[0] * 1000))
        pause.update()
        self.hub.switch = pr.Gated(self.switch, pause)
        whys, close = [], self.hub.close
        self.hub.close = lambda why, **kw: (whys.append(why), close(why, **kw))[1]
        self.hub.step()
        await settle()
        self.assertEqual((self.hub.state, ws.closed, whys, self.switch.on), ("closed", True, ["paused"], True))
        self.hub.step()
        await settle(20)
        self.assertEqual(len(self.connects), 1)                  # no re-open on new speech while away
        seen.vision(1, {"event": "face_found", "face": 3})
        self.hub.step()
        await settle(20)
        self.assertEqual(len(self.connects), 2)

    async def test_recent_replies_go_into_the_instructions(self):
        # owner 2026-11-15: meet kept repeating one line; its recent replies are in the instructions, across sessions
        ws = await self.open_ready()
        await self.play_one(ws, "r1")
        ups = [m for m in ws.sent if m.get("type") == "session.update"]
        self.assertIn("【刚才的对话】", ups[-1]["session"]["instructions"])
        self.assertIn("你说：你好呀", ups[-1]["session"]["instructions"])
        self.assertNotIn("我看看……咦，没看到人呀", self.rl.PERSONA)
        self.assertNotIn("办公桌上的桌面宠物", self.rl.PERSONA)
        self.t[0] += self.hub.cfg.recent_s + 1
        self.assertEqual(self.hub._recent_pairs(), [])          # old replies drop out

    async def test_open_follows_crowd_and_switch(self):
        ws = await self.open_ready()
        upd = ws.sent[0]
        self.assertEqual(upd["type"], "session.update")
        s = upd["session"]
        # owner 2026-11-18: smart_turn_v2 (addressee judgement), threshold only; server_vad keeps its silence
        self.assertEqual((s["voice"], s["turn_detection"]), ("v-secret", {"type": "smart_turn_v2", "threshold": 0.6}))
        self.assertEqual(self.rl.session_config("v", "i", 1000, 0.6)["turn_detection"],
                         {"type": "server_vad", "threshold": 0.6, "silence_duration_ms": 1000})
        self.assertIn("此刻的情景：大家在聊午饭", s["instructions"])
        self.assertIs(s.get("enable_search"), True)               # owner 2026-11-15: web search on
        self.assertEqual(self.rl.session_config("v", "i").get("enable_search"), None)
        self.assertGreaterEqual(self.rl.Config.hour_cap_s, 3600)       # no hourly cap any more
        self.assertIn("qwen-audio-3.1-realtime-flash", self.connects[0][0])
        self.assertEqual(self.connects[0][1]["additional_headers"], {"Authorization": "Bearer k"})
        # crowd's dots (owner 2026-11-15): think at the server VAD's end; a response without sound clears them
        ws.push({"type": "input_audio_buffer.speech_stopped"})
        ws.push({"type": "response.created", "response": {"id": "r0"}})
        ws.push({"type": "response.done", "response": {"id": "r0", "status": "completed"}})
        await settle(10)
        self.assertEqual(self.modes.stages, ["think", "skip"])
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


class TurnSwitchTest(HubBase):
    """智能断句 switch (owner 2026-11-19 night): set_turn changes the turn detection; an open, idle session reopens with it."""
    async def test_switching_off_reopens_the_idle_session_with_server_vad(self):
        ws = await self.open_ready()
        self.assertEqual(ws.sent[0]["session"]["turn_detection"]["type"], "smart_turn_v2")
        self.assertTrue(self.hub.set_turn(False))
        self.hub.step()
        await settle(20)
        self.assertGreaterEqual(len(self.wss), 2)
        self.assertEqual(self.wss[-1].sent[0]["session"]["turn_detection"]["type"], "server_vad")
        self.assertFalse(self.hub.set_turn(False))     # same value: nothing happens

    async def test_a_reply_in_flight_is_not_cut_off(self):
        ws = await self.open_ready()
        ws.push({"type": "response.created", "response": {"id": "r1"}})
        await settle()
        self.hub.set_turn(False)
        self.hub.step()
        await settle(10)
        self.assertEqual(len(self.wss), 1)             # waits for the reply to finish
        self.assertEqual(self.hub.state, "open")

    async def test_the_status_says_which_turn_detection_runs(self):
        await self.open_ready()
        self.hub.set_turn(False)
        self.hub.step()
        await settle(20)
        self.assertEqual(self.recs.status[-1]["turn"], "server_vad")


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

    async def test_the_robots_silent_tail_after_our_reply_is_heard(self):
        ws = await self.open_ready()
        await self.play_one(ws)
        pkt = b"\x01\x00" * 960
        self.hub.feed(pkt, False, True)                           # inside guard_s: still nothing
        await settle()
        self.assertEqual(ws.appended(), b"")
        self.t[0] += 0.7                                          # past the guard, the robot still flags sounding
        for _ in range(3):
            self.hub.feed(pkt, False, True)                       # (the ear's VAD only gets zeros there)
        await settle()
        self.assertEqual(len(ws.appended()), 3 * len(pkt))
        self.t[0] += 0.5                                          # the flag clears: the tail keeps the server VAD fed
        self.hub.feed(pkt, False, False)
        await settle()
        self.assertEqual(len(ws.appended()), 4 * len(pkt))
        self.t[0] += 3.0                                          # long after our reply: sounding is a real sound
        self.hub.feed(pkt, True, True)
        await settle()
        self.assertEqual(len(ws.appended()), 4 * len(pkt))

    async def test_no_tail_while_something_else_plays(self):
        ws = await self.open_ready()
        await self.play_one(ws)
        self.t[0] += 0.7
        self.link.tts_active = {1}                                # soul-tts took the speaker
        self.hub.feed(b"\x01\x00" * 960, False, True)
        await settle()
        self.assertEqual(ws.appended(), b"")

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
        self.assertEqual(turn["reply"], "你好呀")                      # text in the DB record (as chat_turns) ...
        self.assertFalse(any("你好" in line for line in self.logs))   # ... never in the log
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


class GateTest(HubBase):
    """Owner 2026-11-15: a TV drama got ~100 replies, mostly one "didn't hear you" line; 闭嘴 did not stop it."""
    def test_verdicts(self):
        v = self.rl.reply_verdict
        self.assertEqual([v("嗯", False), v("嗯。", True), v("嗯嗯！", True)], [None, "quiet", "quiet"])
        self.assertEqual(v("俺可没听见恁说啥，别拿俺寻开心。", False), "unclear")
        self.assertEqual(v("你说啥？", True), "unclear")
        self.assertEqual(v("好呀", False), None)                         # too short to judge yet
        self.assertEqual(v("好呀！", True), "play")
        self.assertEqual(v("嗯，好呀，我们一起去公园", False), "play")        # 嗯 at the start is fine
        said = [self.rl.norm_text("我就在这儿坐着呢，啥也没干呀！")]
        self.assertEqual(v("我就在这儿坐着呢，", False, said), None)            # could become the same line: wait
        self.assertEqual(v("我就在这儿坐着呢，啥也没干呀。", True, said), "repeat")
        self.assertEqual(v("我就在这儿坐着呢，看电视呢", True, said), "play")
        self.assertEqual(v("", True), "play")
        self.assertEqual(v("你好呀！", True, [self.rl.norm_text("你好呀")]), "play")   # a short greeting may repeat
        self.assertIn("说话用普通话", self.rl.PERSONA)
        self.assertNotIn("山东", self.rl.PERSONA)
        self.assertIn("只回一个字「嗯」", self.rl.PERSONA)

    def test_hush_words(self):
        h = self.rl.is_hush
        self.assertEqual([h("别说话了。"), h("请你闭嘴"), h("他们都去上课了，不跟你聊天了，请你闭嘴。"), h("安静点！")],
                         [True, True, True, True])
        self.assertEqual([h("你别说了，我知道他是谁，这个事情就这么定了"), h("说话"), h("")], [False, False, False])
        # 2026-11-19 13:53: a negated hush turned Docking off
        self.assertEqual([h("没叫你安静点儿。"), h("我没叫你闭嘴"), h("不是让你别说话"), h("谁让你安静了？"), h("不用安静")],
                         [False] * 5)
        self.assertEqual([h("闭嘴吧你"), h("没事，你闭嘴"), h("安静一点")], [True, True, True])

    def test_repeated_replies_show_once_with_a_count(self):
        out = self.rl.instructions("", None, [("a", "你好呀！"), ("b", "我在呢"), ("c", "你好呀")])
        self.assertEqual(out.count("你说：你好呀"), 1)
        self.assertIn("有人说：c → 你说：你好呀（这句已经说过 2 次，不许再说）", out)
        self.assertIn("有人说：b → 你说：我在呢\n", out)

    async def reply(self, ws, rid, text, ms=150):
        ws.push({"type": "input_audio_buffer.speech_stopped"})
        ws.push({"type": "response.created", "response": {"id": rid}})
        ws.push({"type": "response.audio.delta", "delta": base64.b64encode(pcm24(ms)).decode()})   # before the text
        ws.push({"type": "response.audio_transcript.done", "transcript": text})
        ws.push({"type": "response.done", "response": {"id": rid, "status": "completed", "usage": {}}})
        await settle(20)

    async def test_quiet_and_unclear_replies_never_play(self):
        ws = await self.open_ready()
        await self.reply(ws, "r1", "嗯。")
        await self.reply(ws, "r2", "俺可没听见恁说啥，别拿俺寻开心。")
        self.assertEqual(self.link.sent, [])
        self.assertEqual([t["state"] for t in self.recs.turns], ["muted_quiet", "muted_unclear"])
        self.assertEqual(self.hub._recent_pairs(), [])                    # not in 【刚才的对话】 either
        self.assertFalse(self.hub.output_busy())

    async def test_a_word_for_word_repeat_never_plays(self):
        ws = await self.open_ready()
        await self.reply(ws, "r1", "我就在这儿坐着呢，啥也没干呀！")
        sid = self.link.texts()[-1]["stream"]
        self.hub.on_report(self.link, {"type": "tts.playing", "stream": sid})
        self.hub.on_report(self.link, {"type": "tts.done", "stream": sid, "played_ms": 150})
        await settle()
        before = len(self.link.sent)
        await self.reply(ws, "r2", "我就在这儿坐着呢，啥也没干呀。")
        self.assertEqual(len(self.link.sent), before)
        self.assertEqual(self.recs.turns[-1]["state"], "muted_repeat")

    async def test_a_real_answer_plays_once_its_text_is_in(self):
        ws = await self.open_ready()
        ws.push({"type": "response.created", "response": {"id": "r1"}})
        ws.push({"type": "response.audio.delta", "delta": base64.b64encode(pcm24(120)).decode()})
        await settle(20)
        self.assertEqual(self.link.sent, [])                              # held: no text yet
        ws.push({"type": "response.audio_transcript.delta", "delta": "你好呀！今天真开心"})
        await settle(20)
        self.assertEqual(self.link.texts()[0]["type"], "tts.start")

    async def test_hush_turns_the_switch_off_through_mind(self):
        calls = []
        base = self.hub.call
        self.hub.call = lambda m, p, b=None, t=5: (calls.append((m, p, b)), base(m, p, b, t))[1]
        ws = await self.open_ready()
        ws.push({"type": "conversation.item.input_audio_transcription.completed", "transcript": "别说话了。"})
        await settle(20)
        self.assertIn(("POST", "/switches", {"name": "realtime", "on": False, "by": "hush"}), calls)
        self.assertEqual(self.hub.stats["hush"], 1)

    async def test_a_turn_smart_turn_rejects_is_dropped(self):
        # smart_turn_v2: speech that is not for hello comes back as an ambient transcript and no response
        ws = await self.open_ready()
        ws.push({"type": "input_audio_buffer.speech_started"})
        ws.push({"type": "input_audio_buffer.speech_stopped"})
        await settle(10)
        self.assertIsNotNone(self.hub._heard_turn)
        ws.push({"type": "conversation.item.ambient_audio_transcription.completed", "transcript": "他们在聊午饭。"})
        await settle(10)
        self.assertIsNone(self.hub._heard_turn)
        self.assertIsNone(self.hub._turn)
        self.assertEqual(self.hub.stats["ambient"], 1)
        self.assertEqual(self.link.sent, [])
        self.assertEqual(self.modes.stages[-1:], ["aside"])                  # the robot's blocks flash blue


class RescueTest(HubBase):
    """Live 2026-11-15 evening: from audio the model said 「嗯」 to the owner's "你看看我手里拿的是什么东西" and "Hello"."""
    def test_cues(self):
        a = self.rl.addressed
        self.assertEqual([a("你看看我手里拿的是什么东西。"), a("Hello."), a("你看到了什么？"), a("你怎么不说话？"), a("你注意看看。")],
                         [True] * 5)
        self.assertEqual([a("从农民到进城打工，他们还没有学会如何成为。"), a("社区。"), a("儿子来把脸往前伸。")], [False] * 3)
        self.assertEqual([self.rl.is_question(t) for t in ("确定吗？", "找到了吗", "社区。")], [True, True, False])

    async def quiet(self, ws, rid, heard=None):
        ws.push({"type": "input_audio_buffer.speech_started"})
        ws.push({"type": "input_audio_buffer.speech_stopped"})
        if heard is not None:
            ws.push({"type": "conversation.item.input_audio_transcription.completed", "transcript": heard})
        ws.push({"type": "response.created", "response": {"id": rid}})
        ws.push({"type": "response.audio_transcript.done", "transcript": "嗯"})
        ws.push({"type": "response.done", "response": {"id": rid, "status": "completed", "usage": {}}})
        await settle(20)

    def hints(self, ws):
        return [m["item"]["content"][0]["text"] for m in ws.sent if m["type"] == "conversation.item.create"]

    async def test_an_addressed_quiet_reply_is_asked_again_and_plays(self):
        ws = await self.open_ready()
        await self.quiet(ws, "r1", "你看看我手里拿的是什么东西？")
        self.assertEqual(self.hints(ws), [self.rl.rescue_text("你看看我手里拿的是什么东西？")])
        self.assertEqual(ws.kinds()[-1], "response.create")
        ws.push({"type": "response.created", "response": {"id": "r2"}})
        ws.push({"type": "response.audio.delta", "delta": base64.b64encode(pcm24(120)).decode()})
        ws.push({"type": "response.audio_transcript.done", "transcript": "是个黑色的遥控器吧！"})
        ws.push({"type": "response.done", "response": {"id": "r2", "status": "completed", "usage": {}}})
        await settle(20)
        self.assertEqual(self.link.texts()[0]["type"], "tts.start")
        sid = self.link.texts()[0]["stream"]
        self.hub.on_report(self.link, {"type": "tts.playing", "stream": sid})
        self.hub.on_report(self.link, {"type": "tts.done", "stream": sid, "played_ms": 120})
        await settle()
        self.assertEqual((self.recs.turns[-1]["kind"], self.recs.turns[-1]["state"]), ("rescue", "done"))
        self.t[0] += 1
        self.assertEqual(self.hub._recent_pairs()[-1][0], "你看看我手里拿的是什么东西？")
        await self.quiet(ws, "r3", "那个。")                              # nothing to rescue: stays quiet
        self.assertEqual(len(self.hints(ws)), 1)

    async def test_the_ears_own_words_rescue_when_the_model_heard_nothing(self):
        ws = await self.open_ready()
        await self.quiet(ws, "r1")
        self.assertEqual(self.hints(ws), [])
        self.hub.local_heard("你看到了什么？")
        await settle()
        self.assertEqual(self.hints(ws), [self.rl.rescue_text("你看到了什么？")])

    async def test_tv_lines_and_late_words_are_not_rescued(self):
        ws = await self.open_ready()
        await self.quiet(ws, "r1", "从农民到进城打工，他们还没有学会如何成为。")
        self.assertEqual(self.hints(ws), [])
        self.t[0] += self.rl.RESCUE_S + 1
        self.hub.local_heard("hello你看到了什么")                          # too late for that 「嗯」
        await settle()
        self.assertEqual(self.hints(ws), [])

    async def test_a_burst_word_heard_by_both_counts_once(self):
        self.hub.cfg.turn = "server_vad"     # the two sources' dedupe; the smart_turn gate itself: VisionGateTest
        got = []

        class Burst:
            def heard(self, text):
                got.append(text)
        self.hub.burst = Burst()
        ws = await self.open_ready()
        ws.push({"type": "conversation.item.input_audio_transcription.completed", "transcript": "你注意看看。"})
        await settle()
        self.hub.local_heard("你注意看看。")
        self.assertEqual(got, ["你注意看看。"])
        self.t[0] += 5
        self.hub.local_heard("别看了")
        self.assertEqual(got, ["你注意看看。", "别看了"])


class CalledTest(HubBase):
    """Owner 2026-11-18: "hello" (the ASR unit's wake word) is a strong signal; what follows is taken seriously; hello may
    interrupt hello's own speech. Also Dynames (keyword ear) and a sentence starting with hello / Dynames."""
    def hints(self, ws):
        return [m["item"]["content"][0]["text"] for m in ws.sent if m["type"] == "conversation.item.create"]

    async def playing(self, ws, rid):
        ws.push({"type": "input_audio_buffer.speech_stopped"})
        ws.push({"type": "response.created", "response": {"id": rid}})
        ws.push({"type": "response.audio.delta", "delta": base64.b64encode(pcm24(120)).decode()})
        ws.push({"type": "response.audio_transcript.delta", "delta": "今天北京晴，最高六度"})
        await settle(20)

    def test_call_words(self):
        r = self.rl.call_rest
        self.assertEqual([r("hello,今天天气怎么样？"), r("Hello."), r("哈喽 你在吗"), r("Dynames 过来"), r("我说 hello")],
                         ["今天天气怎么样？", "", "你在吗", "过来", None])
        self.assertEqual(r("Hello. I'm here. 查一下比特币"), "查一下比特币")       # the ASR unit's own reply is dropped

    async def test_the_asr_hello_stops_the_reply_and_opens_a_window(self):
        ws = await self.open_ready()
        await self.playing(ws, "r1")
        self.assertTrue(self.hub.output_busy())
        self.hub.on_wake(self.link, {"type": "asr_wake", "word": "hello", "interrupted": True})
        await settle(10)
        self.assertIn("response.cancel", ws.kinds())
        self.assertIn("tts.stop", [m["type"] for m in self.link.texts()])
        self.assertTrue(self.hub.in_called())
        self.assertEqual(self.modes.stages[-1], "listen")
        self.assertEqual(self.hub.stats["called_asr"], 1)
        self.assertEqual(self.hub.stats["interrupted"], 1)

    async def test_in_a_call_smart_turn_cannot_silence_the_sentence(self):
        ws = await self.open_ready()
        self.hub.called("asr")
        ws.push({"type": "input_audio_buffer.speech_started"})
        ws.push({"type": "input_audio_buffer.speech_stopped"})
        ws.push({"type": "conversation.item.ambient_audio_transcription.completed", "transcript": "今天天气怎么样"})
        await settle(20)
        self.assertEqual(self.hints(ws), [self.rl.rescue_text("今天天气怎么样")])
        self.assertEqual(ws.kinds()[-1], "response.create")
        self.assertNotIn("aside", self.modes.stages)                  # no 不是跟我说的 when it answers anyway

    async def test_the_next_sentence_joins_the_calls_chain(self):
        # 2026-11-18: the owner could not see the hello that led to a search in that search's chain
        ws = await self.open_ready()
        self.hub.called("asr")
        call_trace = self.hub._called_trace
        self.assertIsNotNone(call_trace)
        ws.push({"type": "input_audio_buffer.speech_started"})
        await settle(10)
        self.assertEqual(self.hub._seg["trace"], call_trace)
        ws.push({"type": "input_audio_buffer.speech_stopped"})
        ws.push({"type": "input_audio_buffer.speech_started"})
        await settle(10)
        self.assertNotEqual(self.hub._seg["trace"], call_trace)           # only the first sentence

    def test_order_words(self):
        o = self.rl.order_word
        self.assertEqual([o("Turn left."), o("Hello. I'm here. Next."), o("Five"), o("左转"), o("turn left please")],
                         ["turn left", "next", "five", None, None])

    async def test_a_direct_order_is_not_answered(self):
        # owner 2026-11-18: hello + a Unit ASR command word is an order for the robot, not a question for 围观
        ws = await self.open_ready()
        self.hub.called("asr")
        self.hub.on_order(self.link, {"type": "asr_order", "word": "turn left", "n": 1})
        self.assertFalse(self.hub.in_called())
        ws.push({"type": "input_audio_buffer.speech_stopped"})
        ws.push({"type": "response.created", "response": {"id": "r1"}})
        ws.push({"type": "response.audio.delta", "delta": base64.b64encode(pcm24(120)).decode()})
        ws.push({"type": "response.audio_transcript.done", "transcript": "好的，我向左转啦！"})
        ws.push({"type": "response.done", "response": {"id": "r1", "status": "completed", "usage": {}}})
        await settle(20)
        self.assertEqual(self.link.sent, [])
        self.assertEqual(self.recs.turns[-1]["state"], "muted_order")
        ws.push({"type": "conversation.item.ambient_audio_transcription.completed", "transcript": "turn left"})
        await settle(10)
        self.assertEqual(self.hints(ws), [])                           # no called answer either

    async def test_a_bare_ok_in_talk_is_not_an_order(self):
        ws = await self.open_ready()
        self.hub.local_heard("OK.")
        self.assertIsNone(self.hub._order_at)
        self.hub.local_heard("hello, OK.")
        self.assertIsNotNone(self.hub._order_at)

    async def test_outside_a_call_ambient_stays_silent(self):
        ws = await self.open_ready()
        ws.push({"type": "conversation.item.ambient_audio_transcription.completed", "transcript": "今天天气怎么样"})
        await settle(20)
        self.assertEqual(self.hints(ws), [])

    async def test_a_called_sentence_said_while_hello_spoke_is_answered_from_the_ears_text(self):
        ws = await self.open_ready()
        await self.playing(ws, "r1")
        self.hub.local_heard("hello，你在北京找那个药看看")          # the model never got this audio
        await settle(10)
        self.assertIn("response.cancel", ws.kinds())                    # the text call interrupts too
        ws.push({"type": "response.done", "response": {"id": "r1", "status": "cancelled", "usage": {}}})
        await settle(10)
        self.t[0] += self.rl.CALLED_ANSWER_WAIT_S + 0.5
        self.hub.step()
        await settle(10)
        self.assertEqual(self.hints(ws)[-1], self.rl.rescue_text("你在北京找那个药看看"))

    async def test_a_response_for_the_sentence_clears_the_pending_answer(self):
        ws = await self.open_ready()
        self.hub.local_heard("hello，几点了")
        ws.push({"type": "input_audio_buffer.speech_stopped"})
        ws.push({"type": "response.created", "response": {"id": "r1"}})
        await settle(10)
        self.assertIsNone(self.hub._called_text)


class SearchTaskTest(HubBase):
    """Owner 2026-11-18: BTC price wrong; "核实" was promised twice and never done. A search stays a task until answered,
    given up by the owner, or honestly unresolved; refused answers are sent again."""
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.hub.cfg.turn = "server_vad"     # the search logic itself; the smart_turn search gate: SearchGateTest

    def searcher(self, answers):
        self.asked = []
        it = iter(answers)

        def search(q):
            self.asked.append(q)
            return next(it, None)
        return search

    def hints(self, ws):
        return [m["item"]["content"][0]["text"] for m in ws.sent if m["type"] == "conversation.item.create"]

    async def answer(self, ws, rid, text):
        ws.push({"type": "response.created", "response": {"id": rid}})
        ws.push({"type": "response.audio.delta", "delta": base64.b64encode(pcm24(120)).decode()})
        ws.push({"type": "response.audio_transcript.done", "transcript": text})
        ws.push({"type": "response.done", "response": {"id": rid, "status": "completed", "usage": {}}})
        await settle(20)
        for m in self.link.texts():
            if m.get("type") == "tts.start":
                self.hub.on_report(self.link, {"type": "tts.done", "stream": m["stream"], "played_ms": 120})
        await settle()

    def test_words(self):
        rl = self.rl
        self.assertTrue(rl.PROMISE_RE.search("查的是实时行情，刚才那笔数据确实不准，我重新核实一下。"))
        self.assertTrue(rl.PROMISE_RE.search("核实完了，确实是我之前查错了，现在重新查一下。") is None)   # no 我: not hers
        self.assertIsNone(rl.PROMISE_RE.search("我查到的是六万五"))
        self.assertTrue(rl.VERIFY_RE.search("我问你从哪查的BTC的价格，你好像查错了。"))
        self.assertTrue(rl.VERIFY_RE.search("哎，你核实的结果呢？"))
        self.assertTrue(rl.GIVEUP_RE.search("算了不用查了"))

    async def test_a_follow_up_searches_again_until_unresolved(self):
        self.hub.searcher = self.searcher(["比特币约6.5万美元（11月17日）", "比特币约9.2万美元", None, None])
        ws = await self.open_ready()
        self.hub.local_heard("查一下比特币的价格。")
        await settle(20)
        self.hub.step()
        await settle(20)
        self.assertEqual(len(self.asked), 1)
        await self.answer(ws, "r1", "查到了，比特币现在大概六万五。")
        self.t[0] += 10
        self.hub.local_heard("我问你从哪查的BTC的价格，你好像查错了。")      # follow-up: search again, asking for time / source
        await settle(20)
        self.assertEqual(len(self.asked), 2)
        self.assertIn("最新的实时数据", self.asked[1])
        self.hub.step()
        await settle(20)
        await self.answer(ws, "r2", "重新查了，九万二。")
        self.t[0] += 10
        self.hub.local_heard("你核实的结果呢？")
        await settle(20)
        self.hub.step()
        await settle(20)
        await self.answer(ws, "r3", "还是没查到。")
        self.t[0] += 10
        self.hub.local_heard("再查一下吧，核实一下")                      # the 4th: hello says honestly it cannot confirm
        await settle(20)
        self.hub.step()
        await settle(20)
        self.assertEqual(len(self.asked), 3)
        self.assertTrue(self.hints(ws)[-1].startswith("（你已经联网查了好几次「查一下比特币的价格。」"))
        self.assertIn("6.5万", self.hints(ws)[-1])
        self.assertEqual(self.hub._qtask["closed"], "unresolved")

    async def test_her_own_promise_is_kept(self):
        self.hub.searcher = self.searcher(["比特币约9.2万美元，来源某网站"])
        ws = await self.open_ready()
        ws.push({"type": "input_audio_buffer.speech_stopped"})
        ws.push({"type": "conversation.item.input_audio_transcription.completed", "transcript": "比特币多少钱了"})
        await self.answer(ws, "r1", "我重新核实一下比特币的价格。")
        self.hub.step()
        await settle(20)
        self.assertEqual(len(self.asked), 1)                           # her promise ran a real search
        self.assertIn("比特币多少钱了", self.asked[0])

    async def test_the_owner_can_give_up(self):
        self.hub.searcher = self.searcher(["x"])
        ws = await self.open_ready()
        self.hub.local_heard("查一下比特币的价格。")
        await settle(20)
        self.t[0] += 5
        self.hub.local_heard("算了不用查了")
        self.assertEqual(self.hub._qtask["closed"], "owner")
        self.t[0] += 5
        self.hub.local_heard("你核实的结果呢？")                            # no task any more: no search
        await settle(20)
        self.assertEqual(len(self.asked), 1)

    async def test_a_refused_answer_is_sent_again_when_quiet(self):
        self.hub.searcher = self.searcher(["比特币约9.2万美元"])
        ws = await self.open_ready()
        self.hub.local_heard("查一下比特币的价格。")
        await settle(20)
        self.hub.step()
        await settle(20)
        n = len(self.hints(ws))
        self.assertEqual(self.hub._next_kind, "search_answer")
        ws.push({"type": "error", "error": {"code": "invalid_value", "message": "Cannot create response while user is speaking."}})
        await settle(20)
        self.assertIsNotNone(self.hub._redo)
        self.t[0] += 1
        self.hub.step()
        await settle(20)
        self.assertEqual(len(self.hints(ws)), n + 1)                    # the same answer again
        self.assertEqual(self.hints(ws)[-1], self.hints(ws)[n - 1])
        self.assertEqual(self.hub._next_kind, "search_answer")


class LengthTest(HubBase):
    """Owner 2026-11-18: reply length by mechanism, not only by the persona's rule."""
    def last_instr(self, ws):
        return [m["session"]["instructions"] for m in ws.sent if m["type"] == "session.update"][-1]

    def test_kinds_and_hints(self):
        k = self.rl.ask_kind
        self.assertEqual([k("为什么天是蓝的？"), k("几点了？"), k("今天好累啊")], ["explain", "question", "chat"])
        self.assertIn("三到五句", self.rl.rescue_text("这个原理是什么"))
        self.assertIn("一两句", self.rl.rescue_text("哈哈好的"))

    async def test_detail_and_brief_modes_are_kept_and_pushed(self):
        ws = await self.open_ready()
        self.assertNotIn("【这会儿的回答长短】", self.last_instr(ws))
        self.hub.local_heard("你详细点说说这个")
        await settle(10)
        self.assertIn("主人刚让你说详细点", self.last_instr(ws))
        self.t[0] += 60
        self.hub.local_heard("太长了，说重点")
        await settle(10)
        self.assertIn("主人刚嫌你说得长", self.last_instr(ws))
        self.t[0] += self.rl.LENGTH_S + 1
        self.assertEqual(self.hub._length_mode(), "normal")

    async def test_many_faces_ask_for_short_answers(self):
        ws = await self.open_ready()
        for i in range(3):
            self.hub.on_vision({"event": "face_found", "face": 10 + i})
        self.assertIn("眼前有 3 个人", self.hub._length_line())


class ToolGateTest(HubBase):
    """Owner 2026-11-18 "我不想让闲聊触发了工具调用": smart_turn let it through, the tool gate decides."""
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.calls = []

        def call(method, path, body=None, timeout=5):
            self.calls.append(path)
            if path == "/tools":
                return 200, {"tools": [{"type": "function", "function": {"name": "live_data", "description": "x",
                                                                         "parameters": {"type": "object", "properties": {}}}}]}
            if path == "/tool/call":
                return 200, {"ok": True, "data": {"usd": 1}, "error": None}
            return 200, json.loads(json.dumps(CTX))
        self.hub.call = call
        sw = Switch()
        sw.on = True
        self.hub.tools_switch = sw
        self.hub.search_fillers = [("我查查哈", b"\x00\x00" * 2400)]

    def fc(self, ws, rid, heard):
        self.hub.local_heard(heard)                                                     # the ear's own ASR
        ws.push({"type": "conversation.item.input_audio_transcription.completed", "transcript": heard})
        ws.push({"type": "response.created", "response": {"id": rid}})
        ws.push({"type": "response.function_call_arguments.done", "response_id": rid, "call_id": "c" + rid,
                 "name": "live_data", "arguments": "{}"})
        ws.push({"type": "response.done", "response": {"id": rid}})

    async def test_chit_chat_tool_call_goes_back_quietly(self):
        ws = await self.open_ready()
        self.fc(ws, "r1", "你今天开心吗")
        await settle(30)
        self.assertNotIn("/tool/call", self.calls)
        self.assertEqual(self.hub.stats["search_fillers"], 0)                          # no 我查查
        out = [m for m in ws.sent if m["type"] == "conversation.item.create" and m["item"]["type"] == "function_call_output"]
        self.assertEqual(json.loads(out[-1]["item"]["output"])["error"]["code"], "refused")
        ws.push({"type": "response.created", "response": {"id": "r2"}})
        await settle(5)
        self.assertEqual((self.hub._turns["r2"]["kind"], self.hub._turns["r2"]["gate"]), ("tool_refused", None))
        ws.push({"type": "response.audio_transcript.done", "transcript": "嗯"})
        await settle(5)
        self.assertEqual(self.hub._turns["r2"]["gate"], "quiet")                       # its 「嗯」 is not played

    async def test_a_called_or_certain_sentence_passes(self):
        ws = await self.open_ready()
        self.fc(ws, "r1", "明天北京天气怎么样")                                          # a certain word
        await settle(30)
        self.assertIn("/tool/call", self.calls)
        self.calls.clear()
        self.t[0] += 30
        self.hub.called("asr")
        self.fc(ws, "r3", "那个呢")                                                      # inside a call window
        await settle(30)
        self.assertIn("/tool/call", self.calls)


class OwnerAndBodyTest(HubBase):
    """Owner 2026-11-18: 亲口算本人 (owner's voice -> owner: true); the body only for people hello knows."""
    async def test_owner_flag_and_body_gate(self):
        posted = []

        def call(method, path, body=None, timeout=5):
            if path == "/tools":
                return 200, {"tools": [{"type": "function", "function": {"name": n, "description": n, "parameters": {"type": "object", "properties": {}}}}
                                       for n in ("nod", "switch_propose")]}
            if path == "/tool/call":
                posted.append(body)
                return 200, {"ok": True, "data": {}, "error": None}
            return 200, {**json.loads(json.dumps(CTX)), "owner": {"face_names": ["me"], "call": "主人", "voice_names": ["me01v"]}}
        self.hub.call = call
        sw = Switch()
        sw.on = True
        self.hub.tools_switch = sw
        ws = await self.open_ready()
        self.hub.local_heard("点点头", speaker={"band": "unknown", "name": None})
        self.push_fc(ws, "r1", "nod")
        await settle(30)
        self.assertEqual(posted, [])                                                    # a stranger: no nod
        self.assertEqual(self.hub.stats["body_gated"], 1)
        self.t[0] += 10
        self.hub.local_heard("帮我把听悟打开", speaker={"band": "known", "name": "me01v"})
        self.hub.called("asr")
        self.push_fc(ws, "r2", "switch_propose")
        await settle(30)
        self.assertEqual((posted[-1]["name"], posted[-1]["owner"]), ("switch_propose", True))  # 亲口 = in person
        self.push_fc(ws, "r3", "nod")
        await settle(30)
        self.assertEqual(posted[-1]["name"], "nod")                                     # a known voice: it nods

    def push_fc(self, ws, rid, name):
        ws.push({"type": "response.created", "response": {"id": rid}})
        ws.push({"type": "response.function_call_arguments.done", "response_id": rid, "call_id": "c" + rid,
                 "name": name, "arguments": "{}"})
        ws.push({"type": "response.done", "response": {"id": rid}})


class SkillModeTest(HubBase):
    """Owner 2026-11-18: the model finds a skill first, then its tools (5668 vs ~300 input tokens a turn, measured)."""
    async def test_open_skill_then_its_tool(self):
        calls = []

        def call(method, path, body=None, timeout=5):
            calls.append((path, body))
            if path == "/tools":
                return 200, {"tools": [{"type": "function", "function": {"name": n, "description": n, "parameters": {"type": "object", "properties": {}}}}
                                       for n in ("live_data", "web_search", "nod", "reminder_set")]}
            if path == "/tool/call":
                return 200, {"ok": True, "data": {"usd": 64143}, "error": None}
            return 200, json.loads(json.dumps(CTX))
        self.hub.call = call
        sw = Switch()
        sw.on = True
        self.hub.tools_switch = sw
        self.hub.tools_mode_fn = lambda: "skills"
        self.hub.search_fillers = [("我查查哈", b"\x00\x00" * 2400)]
        ws = await self.open_ready()
        self.hub.local_heard("比特币现在多少钱")                                          # a sentence that earns tools
        first = [m for m in ws.sent if m["type"] == "session.update"][0]["session"]["tools"]
        self.assertEqual([t["name"] for t in first], ["open_skill"])
        self.assertIn("realtime=实时数据", first[0]["description"])
        ws.push({"type": "response.created", "response": {"id": "r1"}})
        ws.push({"type": "response.function_call_arguments.done", "response_id": "r1", "call_id": "c1",
                 "name": "open_skill", "arguments": '{"skill": "realtime"}'})
        ws.push({"type": "response.done", "response": {"id": "r1"}})
        await settle(30)
        upd = [m for m in ws.sent if m["type"] == "session.update" and "tools" in m["session"]][-1]["session"]["tools"]
        self.assertEqual([t["name"] for t in upd], ["open_skill", "live_data", "web_search"])
        out = [m for m in ws.sent if m["type"] == "conversation.item.create" and m["item"]["type"] == "function_call_output"]
        self.assertNotIn("guide", json.loads(out[-1]["item"]["output"])["data"])       # not in the history
        instr = [m for m in ws.sent if m["type"] == "session.update" and "tools" in m["session"]][-1]["session"]["instructions"]
        self.assertIn("【此刻打开的技能】", instr)
        self.assertIn("来源", instr)                                                   # the skill's text is there
        self.assertNotIn("/tool/call", [p for p, _ in calls])                       # opening is local
        ws.push({"type": "response.created", "response": {"id": "r2"}})
        ws.push({"type": "response.function_call_arguments.done", "response_id": "r2", "call_id": "c2",
                 "name": "live_data", "arguments": '{"kind": "price"}'})
        ws.push({"type": "response.done", "response": {"id": "r2"}})
        await settle(30)
        self.assertEqual([b["name"] for p, b in calls if p == "/tool/call"], ["live_data"])
        self.assertEqual(self.hub.stats["search_fillers"], 1)                       # one 我查查 for the chain
        ws.push({"type": "response.created", "response": {"id": "r3"}})
        ws.push({"type": "response.function_call_arguments.done", "response_id": "r3", "call_id": "c3",
                 "name": "nod", "arguments": "{}"})                                  # body not open: opened now,
        ws.push({"type": "response.done", "response": {"id": "r3"}})                      # then the body gate (a
        await settle(30)                                                                 # stranger) refuses it
        self.assertIn("body", self.hub._open_skills)
        out = [m for m in ws.sent if m["type"] == "conversation.item.create" and m["item"]["type"] == "function_call_output"]
        self.assertEqual(json.loads(out[-1]["item"]["output"])["error"]["code"], "refused")
        self.assertEqual(self.rl.reply_verdict("[事件] 主人（劳埃德）出现在你面前了，打个招呼", True), "marker")


class AutoChainTest(HubBase):
    """Owner 2026-11-19: auto allows 5 real tool calls a sentence (open_skill not counted); 查证 waits longer; the
    skill process is in the log on the sentence's trace."""
    async def test_five_calls_slow_verify_and_skill_log(self):
        posted, events = [], []
        self.rl.ear_log.emit, old = (lambda kind, **f: events.append((kind, f))), self.rl.ear_log.emit
        self.addCleanup(setattr, self.rl.ear_log, "emit", old)

        def call(method, path, body=None, timeout=5):
            if path == "/tools":
                return 200, {"tools": [{"type": "function", "function": {"name": n, "description": n, "parameters": {"type": "object", "properties": {}}}}
                                       for n in ("live_data", "web_search", "search_verify")]}
            if path == "/tool/call":
                posted.append((body["name"], timeout))
                return 200, {"ok": True, "data": {"usd": 1}, "error": None}
            return 200, json.loads(json.dumps(CTX))
        self.hub.call = call
        sw = Switch()
        sw.on = True
        self.hub.tools_switch = sw
        self.hub.tools_mode_fn = lambda: "auto"
        ws = await self.open_ready()
        self.hub.local_heard("比特币现在多少钱")
        calls = [("open_skill", '{"skill": "realtime"}')] + [("live_data", "{}")] * 6
        for i, (name, a) in enumerate(calls):
            rid = f"r{i}"
            ws.push({"type": "response.created", "response": {"id": rid}})
            ws.push({"type": "response.function_call_arguments.done", "response_id": rid, "call_id": "c" + rid,
                     "name": name, "arguments": a})
            ws.push({"type": "response.done", "response": {"id": rid}})
            await settle(30)
        self.assertEqual([n for n, _ in posted], ["live_data"] * 5)                   # was 2 (open_skill + 3 cap)
        out = [m for m in ws.sent if m["type"] == "conversation.item.create" and m["item"]["type"] == "function_call_output"]
        self.assertEqual(json.loads(out[-1]["item"]["output"])["error"]["code"], "refused")
        tool_rows = [f for k, f in events if k == "call.realtime.tool"]
        traces = {f["trace"] for f in tool_rows}
        skill_rows = [f for k, f in events if k == "trigger.skill"]
        self.assertTrue(any(f["trace"] in traces and f["extra"]["action"] == "model" for f in skill_rows))
        self.assertIn("result", tool_rows[0]["extra"])
        self.assertIn("result", tool_rows[0]["body"])
        self.assertIn("speaker", tool_rows[0]["extra"])
        ws.push({"type": "response.created", "response": {"id": "end"}})               # the chain's spoken answer
        ws.push({"type": "response.done", "response": {"id": "end"}})
        await settle(30)
        self.t[0] += 30                                                                  # a new sentence
        self.hub.local_heard("自由发挥，帮我查一下这条新闻")
        for i, (name, a) in enumerate([("open_skill", '{"skill": "free"}'), ("search_verify", "{}")]):
            rid = f"v{i}"
            ws.push({"type": "response.created", "response": {"id": rid}})
            ws.push({"type": "response.function_call_arguments.done", "response_id": rid, "call_id": "c" + rid,
                     "name": name, "arguments": a})
            ws.push({"type": "response.done", "response": {"id": rid}})
            await settle(30)
        self.assertEqual(posted[-1], ("search_verify", self.rl.SLOW_TOOL_TIMEOUT_S["search_verify"]))
        self.t[0] += 3600                                                                # everything expires
        self.hub._prune_skills(self.t[0])
        self.assertTrue(any(f["extra"]["action"] in ("close", "unload_unused") for k, f in events if k == "trigger.skill"))


class ToolFillerTest(HubBase):
    """Owner 2026-11-19: 「我查查」 picked by the situation (the tool), at random, never twice in a row; moving: none."""
    async def test_filler_by_situation(self):
        events = []
        self.rl.ear_log.emit, old = (lambda kind, **f: events.append((kind, f))), self.rl.ear_log.emit
        self.addCleanup(setattr, self.rl.ear_log, "emit", old)
        names = ("look_now", "reminder_set", "memory_search", "live_data", "nod")

        def call(method, path, body=None, timeout=5):
            if path == "/tools":
                return 200, {"tools": [{"type": "function", "function": {"name": n, "description": n, "parameters": {"type": "object", "properties": {}}}}
                                       for n in names]}
            if path == "/tool/call":
                return 200, {"ok": True, "data": {}, "error": None}
            return 200, {**json.loads(json.dumps(CTX)), "owner": {"face_names": [], "call": "主人", "voice_names": ["me"]}}
        self.hub.call = call
        sw = Switch()
        sw.on = True
        self.hub.tools_switch = sw
        pcm = b"\x00\x00" * 2400
        self.hub.fillers = [("我看看哈", pcm)]
        self.hub.tool_fillers = {"search": [("我查查哈", pcm), ("我找找看", pcm)],
                                 "recall": [("我想想啊", pcm), ("我翻翻记录", pcm)], "daily": [("好，我记一下", pcm)]}
        ws = await self.open_ready()
        played = []
        for i, name in enumerate(names + ("live_data",)):
            self.t[0] += 30                                                          # each its own sentence
            self.hub.local_heard("hello 帮我弄一下", speaker={"band": "known", "name": "me", "score": 0.8, "runner_up": None})
            self.hub.called("asr")
            rid = f"f{i}"
            ws.push({"type": "response.created", "response": {"id": rid}})
            ws.push({"type": "response.function_call_arguments.done", "response_id": rid, "call_id": "c" + rid,
                     "name": name, "arguments": "{}"})
            ws.push({"type": "response.done", "response": {"id": rid}})
            await settle(30)
            ws.push({"type": "response.created", "response": {"id": rid + "e"}})
            ws.push({"type": "response.done", "response": {"id": rid + "e"}})
            await settle(10)
            got = [f for k, f in events if k == "filler.play"]
            played.append((got[-1]["extra"]["group"], got[-1]["sum"]) if len(got) > len(played) - played.count(None) else None)
        groups = [p[0] if p else None for p in played]
        self.assertEqual(groups, ["look", "daily", "recall", "search", None, "search"])   # nod: no filler
        self.assertNotEqual(played[3][1], played[5][1])                               # never twice in a row


class SpeakerAndPlaceholderTest(HubBase):
    """2026-11-19 09:52: the owner's face in view, the voiceprint only "unsure" -> the speaker line said 不认识的声音,
    the model refused 「转过身去」 itself; 【刚才的对话】 carried [tool_refused], which the model then said as a reply."""
    async def test_unsure_voice_is_not_a_stranger_and_a_named_face_counts(self):
        self.hub.ctx = json.loads(json.dumps(CTX))
        now = self.hub.clock()
        self.hub._speakers.append((now, "unsure", None))
        self.assertNotIn("不认识", self.hub._speaker_text(now))
        self.hub.watch.event({"event": "face_named", "face": 1, "name": "boss_face"}, now)
        said = self.hub._speaker_text(now)
        self.assertIn("阿大", said)                                           # the owner's call, from the face
        self.hub._speakers.append((now, "unknown", None))
        self.assertIn("不认识的声音", self.hub._speaker_text(now))
        self.assertIn("阿大", self.hub._speaker_text(now))

    def test_unsure_owner_voice_moves_the_body_but_is_not_in_person(self):
        """2026-11-19 10:09: the owner's voice scored 0.43-0.56 on his print (known = 0.6), runner-up ~0.25: unsure.
        Owner 2026-11-18 "认人策略可以考虑多种": a clear lead on the owner's own print counts for the body only."""
        self.hub.ctx = {**json.loads(json.dumps(CTX)), "owner": {"face_names": ["me"], "call": "主人", "voice_names": ["me"]}}
        self.hub.active = lambda: True
        self.hub.in_crowd = lambda: True
        self.hub.local_heard("转过身去", speaker={"band": "unsure", "name": "me", "score": 0.48,
                                              "runner_up": {"name": "主人", "score": 0.22}})
        self.assertTrue(self.hub.known_person())
        self.assertFalse(self.hub.owner_in_person())                        # a C tool still needs the full bar
        self.assertIn("像主人", self.hub._speaker_text())
        self.t[0] += 10
        self.hub.local_heard("转过身去", speaker={"band": "unsure", "name": "me", "score": 0.42,
                                              "runner_up": {"name": "小王", "score": 0.38}})
        self.assertFalse(self.hub.known_person())                           # no clear lead: not the owner
        self.t[0] += 10
        self.hub.ctx["owner"]["voice_names"] = ["me", "主人"]                 # live: both prints are the owner's
        self.hub.local_heard("转过身去", speaker={"band": "unsure", "name": "me", "score": 0.44,
                                              "runner_up": {"name": "主人", "score": 0.41}})
        self.assertTrue(self.hub.known_person())
        self.t[0] += 10
        self.hub.local_heard("转过身去", speaker={"band": "unsure", "name": "guest", "score": 0.5, "runner_up": None})
        self.assertFalse(self.hub.known_person())                           # someone else's print
        self.t[0] += 10
        self.hub.ctx["owner"]["voice_names"] = ["me"]
        self.hub.local_heard("转过身去", speaker={"band": "unsure", "name": "me", "score": 0.5, "runner_up": None})
        self.assertFalse(self.hub.known_person())                           # no runner-up: no lead shown (fail closed)

    def test_placeholders_never_play_or_enter_recent_talk(self):
        v = self.rl.reply_verdict
        for t in ("[tool_refused]", "[tool_answer] 好", " [speech]"):
            self.assertEqual(v(t, True), "marker", t)
        self.assertEqual(v("[笑] 好呀", True), "play")
        now = self.hub.clock()
        first = {"kind": "speech", "heard": "转过身去。", "trace": "t1"}
        self.hub._turns["a"], self.hub._turns["b"] = first, {"kind": "tool_refused", "trace": "t1"}
        self.hub._recent.append((now, self.hub._turns["b"], "我还不认识你呢。"))
        self.assertEqual(self.hub._recent_pairs(), [("转过身去。", "我还不认识你呢。")])
        self.hub._recent.append((now, {"kind": "tool_answer", "trace": "x", "chain_heard": "报告机身状态。"}, "电量满格。"))
        self.assertEqual(self.hub._recent_pairs()[-1][0], "报告机身状态。")
        self.hub._recent.append((now, {"kind": "tool_answer", "trace": "nothing"}, "音量四十。"))
        self.assertNotIn("[", self.hub._recent_pairs()[-1][0])


class SkillResetTest(HubBase):
    async def test_skills_do_not_outlive_their_session(self):
        self.hub._tools_mode, self.hub._skills = "skills", {"world": {"id": "world", "tools": ["live_data"]}}
        self.hub._all_specs = {"live_data": {"type": "function", "name": "live_data"}}
        ws = await self.open_ready()                                   # tools off: a new session resets them
        self.assertEqual((self.hub._skills, self.hub._all_specs), ({}, {}))
        ws.push({"type": "response.created", "response": {"id": "r1"}})
        ws.push({"type": "response.function_call_arguments.done", "response_id": "r1", "call_id": "c1",
                 "name": "open_skill", "arguments": '{"skill": "world"}'})
        ws.push({"type": "response.done", "response": {"id": "r1"}})
        await settle(30)
        out = [m for m in ws.sent if m["type"] == "conversation.item.create" and m["item"]["type"] == "function_call_output"]
        self.assertEqual(json.loads(out[-1]["item"]["output"])["error"]["code"], "not_available")
        self.assertFalse(any(m["type"] == "session.update" and "tools" in m["session"] for m in ws.sent))


class VoiceTest(HubBase):
    async def test_set_voice_reopens_with_the_voice_and_persona(self):
        ws = await self.open_ready()
        self.hub.set_voice("me01", "vid-me", "【人设】借用主人的声音")
        self.assertEqual(self.hub.state, "closed")
        self.assertEqual(self.hub.voice, "vid-me")
        self.assertTrue(self.hub._instructions().startswith("【人设】借用主人的声音"))


class ToolCallTest(HubBase):
    """L6 2026-11-18: Qwen calls hello's tools in Docking (switch realtime_tools); probe: supported by the model."""
    async def test_a_tool_call_round_trip(self):
        calls = []

        def call(method, path, body=None, timeout=5):
            calls.append((method, path, body))
            if path == "/tools":
                return 200, {"tools": [{"type": "function", "function": {"name": "live_data", "description": "币价",
                                                                         "parameters": {"type": "object", "properties": {}}}},
                                       {"type": "function", "function": {"name": "say", "description": "x", "parameters": {}}}]}
            if path == "/tool/call":
                return 200, {"ok": True, "data": {"usd": 64143}, "error": None}
            return 200, json.loads(json.dumps(CTX))
        self.hub.call = call
        sw = Switch()
        sw.on = True
        self.hub.tools_switch = sw
        ws = await self.open_ready()
        upd = [m for m in ws.sent if m["type"] == "session.update"][0]["session"]
        self.assertEqual([t["name"] for t in upd["tools"]], ["live_data"])            # say is hello's own
        ws.push({"type": "input_audio_buffer.speech_stopped"})
        ws.push({"type": "conversation.item.input_audio_transcription.completed", "transcript": "比特币多少钱"})
        ws.push({"type": "response.created", "response": {"id": "r1"}})
        ws.push({"type": "response.function_call_arguments.done", "response_id": "r1", "call_id": "c1",
                 "name": "live_data", "arguments": '{"kind": "price", "symbol": "BTC"}'})
        ws.push({"type": "response.done", "response": {"id": "r1", "status": "completed"}})
        await settle(30)
        self.assertIn(("POST", "/tool/call", {"name": "live_data", "args": {"kind": "price", "symbol": "BTC"},
                                              "trace": calls[-1][2]["trace"], "caller": "qwen", "owner": False}), calls)
        out = [m for m in ws.sent if m["type"] == "conversation.item.create" and m["item"]["type"] == "function_call_output"]
        self.assertEqual((out[0]["item"]["call_id"], json.loads(out[0]["item"]["output"])["data"]), ("c1", {"usd": 64143}))
        self.assertEqual(ws.sent[-1]["type"], "response.create")
        self.assertEqual(self.hub.stats["muted_quiet"], 0)                               # no rescue of an empty reply
        ws.push({"type": "response.created", "response": {"id": "r2"}})
        await settle(5)
        self.assertEqual((self.hub._turns["r2"]["kind"], self.hub._turns["r2"]["gate"]), ("tool_answer", "play"))

    async def test_a_tool_not_given_is_refused(self):
        calls = []
        self.hub.call = lambda m, p, b=None, t=5: (calls.append(p), (200, {"tools": []} if p == "/tools" else json.loads(json.dumps(CTX))))[1]
        sw = Switch()
        sw.on = True
        self.hub.tools_switch = sw
        ws = await self.open_ready()
        ws.push({"type": "response.created", "response": {"id": "r1"}})
        ws.push({"type": "response.function_call_arguments.done", "response_id": "r1", "call_id": "c9",
                 "name": "db_query", "arguments": "{}"})
        ws.push({"type": "response.done", "response": {"id": "r1"}})
        await settle(30)
        self.assertNotIn("/tool/call", calls)
        out = [m for m in ws.sent if m["type"] == "conversation.item.create" and m["item"]["type"] == "function_call_output"]
        self.assertEqual(json.loads(out[0]["item"]["output"])["error"]["code"], "not_available")

    def test_no_builtin_search_with_tools(self):
        cfg = self.rl.session_config("v", "i", search=True, tools=[{"type": "function", "name": "x"}])
        self.assertNotIn("enable_search", cfg)                         # probe: both together = every reply fails
        self.assertTrue(self.rl.session_config("v", "i", search=True)["enable_search"])

    async def test_tools_off_by_default(self):
        ws = await self.open_ready()
        self.assertNotIn("tools", [m for m in ws.sent if m["type"] == "session.update"][0]["session"])


class QuietLowRoleTest(HubBase):
    """Owner 2026-11-18: 安静（直到被叫）, 少插话 ("我们在聊天 / 我在看电视"), a temporary role ("当个大姐姐")."""
    def last_instr(self, ws):
        return [m["session"]["instructions"] for m in ws.sent if m["type"] == "session.update"][-1]

    def turn(self, ws, rid, heard, reply):
        ws.push({"type": "input_audio_buffer.speech_stopped"})
        ws.push({"type": "conversation.item.input_audio_transcription.completed", "transcript": heard})
        ws.push({"type": "response.created", "response": {"id": rid}})
        ws.push({"type": "response.audio_transcript.delta", "delta": reply})

    async def test_quiet_mutes_until_called(self):
        ws = await self.open_ready()
        self.hub.set_quiet("quiet")
        await settle(10)
        self.assertIn("主人让你先安静", self.last_instr(ws))
        self.turn(ws, "r1", "今天天气怎么样", "今天天气挺好的，出去走走吧")
        await settle(10)
        self.assertEqual(self.hub.stats["muted_quiet_mode"], 1)
        self.hub.called("asr")
        self.assertFalse(self.hub.quiet_on())
        ws.push({"type": "response.done", "response": {"id": "r1", "status": "completed"}})
        await settle(10)
        self.assertEqual(self.hub.say("我在呢"), "sent")
        await settle(10)
        self.assertTrue(any(m["type"] == "conversation.item.create" and "我在呢" in json.dumps(m, ensure_ascii=False)
                            for m in ws.sent))

    async def test_quiet_from_natural_words_hush_still_off(self):
        await self.open_ready()
        self.hub.local_heard("你先安静一会儿")
        self.assertTrue(self.hub.quiet_on())
        self.assertEqual(self.hub.stats["hush"], 0)                       # not Docking off
        self.hub.called("asr")
        self.assertFalse(self.hub.quiet_on())

    async def test_low_mode_from_natural_words(self):
        ws = await self.open_ready()
        self.hub.local_heard("我们在聊天呢")
        await settle(10)
        self.assertTrue(self.hub.low_on())
        self.assertIn("别插话", self.last_instr(ws))
        self.t[0] += 5
        self.turn(ws, "r2", "这个电视剧挺好看", "是呀，我也觉得剧情不错")
        await settle(10)
        self.assertEqual(self.hub.stats["muted_low_mode"], 1)
        self.t[0] += 61
        self.hub.local_heard("好了你可以说话了")
        self.assertFalse(self.hub.low_on())

    async def test_role_only_when_said_to_hello(self):
        ws = await self.open_ready()
        self.hub.local_heard("他想当个医生")                                 # not to hello: nothing
        self.assertIsNone(self.hub.role_text())
        self.hub.local_heard("hello，当个大姐姐，陪陪老人")
        await settle(10)
        self.assertEqual(self.hub.role_text(), "当个大姐姐，陪陪老人")
        self.assertIn("【临时角色】主人刚让你：当个大姐姐，陪陪老人", self.last_instr(ws))
        self.hub.local_heard("hello，做回你自己吧")
        self.assertIsNone(self.hub.role_text())
        self.hub.set_role("像个老师一样耐心", 30)
        self.assertEqual(self.hub.role_text(), "像个老师一样耐心")
        self.t[0] += 31 * 60
        self.assertIsNone(self.hub.role_text())

    async def test_look_waiters_take_a_fresh_look(self):
        await self.open_ready()
        fut = asyncio.get_running_loop().create_future()
        self.hub.look_waiters.append((5.0, fut))
        self.hub.add_look("旧照片", self.hub.wall_ms(), snapped=4.0)
        self.assertFalse(fut.done())
        self.hub.add_look("主人手里拿着一个杯子", self.hub.wall_ms(), snapped=6.0)
        self.assertEqual(fut.result(), "主人手里拿着一个杯子")
        self.assertEqual(self.hub.look_waiters, [])


class SceneTest(HubBase):
    async def test_an_instant_photo_goes_into_the_eyes(self):
        ws = await self.open_ready()
        self.hub.add_scene("主人举着一个红色的小盒子。", self.hub.wall_ms())
        self.t[0] += 5
        self.hub.step()
        await settle(10)
        instr = [m["session"]["instructions"] for m in ws.sent if m["type"] == "session.update"][-1]
        self.assertIn("摄像头刚拍到的（", instr)
        self.assertIn("主人举着一个红色的小盒子。", instr)
        self.assertNotIn("大约一分钟前", instr)


class SearchGateTest(HubBase):
    """2026-11-18 18:12: "别扔了就行，那只能去网上搜搜看有没有" said to a colleague started a search hello read out."""
    async def test_a_search_word_waits_for_a_real_reply(self):
        asked = []
        self.hub.searcher = lambda q: (asked.append(q), "二手市场数据")[1]
        ws = await self.open_ready()
        self.hub.local_heard("别扔了就行，那只能去网上搜搜看有没有")
        await settle(10)
        self.assertEqual(asked, [])                                   # not yet
        ws.push({"type": "conversation.item.ambient_audio_transcription.completed", "transcript": "去网上搜搜看"})
        await settle(20)
        self.assertEqual(asked, [])                                   # not for hello: no search
        self.assertEqual(self.hub.stats["search_gated"], 1)
        self.t[0] += 5
        self.hub.local_heard("帮我搜一下明天的天气")
        ws.push({"type": "input_audio_buffer.speech_stopped"})
        ws.push({"type": "response.created", "response": {"id": "r1"}})
        ws.push({"type": "response.audio_transcript.delta", "delta": "好的，我来查一下明天的天气"})
        await settle(20)
        self.assertEqual(asked, ["帮我搜一下明天的天气"])                 # a real reply: the search runs
        self.hub._search_wait = None                                   # that search is answered (one at a time)
        self.t[0] += 5
        self.hub.called("asr")
        self.hub.local_heard("搜一下比特币")                               # inside a call: at once
        await settle(20)
        self.assertEqual(asked[-1], "搜一下比特币")


class VisionGateTest(HubBase):
    """Owner 2026-11-18: "决定视觉爆发的前提是过了qwen的拒识闸门，别一有人说话就爆发" (smart_turn_v2)."""
    class Burst:
        def __init__(self):
            self.heards, self.asks = [], []

        def heard(self, text):
            self.heards.append(text)

        def ask(self, trigger="ask"):
            self.asks.append(trigger)
            return 1.0

    async def test_a_burst_word_waits_for_the_models_verdict(self):
        self.hub.burst = burst = self.Burst()
        ws = await self.open_ready()
        self.hub.local_heard("你注意看看那边")                             # the ear's ASR: no burst yet
        self.assertEqual(burst.heards, [])
        ws.push({"type": "conversation.item.ambient_audio_transcription.completed", "transcript": "你注意看看那边"})
        await settle(10)
        self.assertEqual(burst.heards, [])                              # rejected: none
        self.assertEqual(self.hub.stats["burst_gated"], 1)
        self.t[0] += 5
        self.hub.local_heard("你看看我手里的东西")                         # a look question: waits too
        self.assertEqual(burst.asks, [])
        ws.push({"type": "input_audio_buffer.speech_stopped"})
        ws.push({"type": "conversation.item.input_audio_transcription.completed", "transcript": "你看看我手里的东西"})
        ws.push({"type": "response.created", "response": {"id": "r1"}})
        await settle(10)
        self.assertEqual(burst.asks, [])                                # a transcript / a response alone: not yet
        ws.push({"type": "response.audio_transcript.delta", "delta": "哇，这是一个红色的小盒子吧"})
        await settle(10)
        self.assertEqual(burst.asks, ["ask"])                           # a real reply: the model took it
        self.assertEqual(burst.heards, ["你看看我手里的东西"])                # ... and its burst word counts
        self.t[0] += 5
        self.hub.local_heard("仔细看一下")
        self.t[0] += self.rl.VISION_GATE_S + 1
        self.hub.step()                                                # no verdict in time: dropped
        self.assertEqual(burst.heards, ["你看看我手里的东西"])
        self.assertEqual(self.hub.stats["burst_gated"], 2)
        # a transcript alone (the model ignores the sentence) or a 「嗯」 reply: no burst (2026-11-18 15:09 / 15:14)
        self.t[0] += 5
        ws.push({"type": "conversation.item.input_audio_transcription.completed", "transcript": "还得看一下表啊去"})
        await settle(10)
        self.t[0] += 1
        ws.push({"type": "input_audio_buffer.speech_stopped"})
        ws.push({"type": "response.created", "response": {"id": "r2"}})
        ws.push({"type": "response.audio_transcript.done", "transcript": "嗯"})
        ws.push({"type": "response.done", "response": {"id": "r2", "status": "completed", "usage": {}}})
        await settle(20)
        self.assertEqual(burst.heards, ["你看看我手里的东西"])
        self.assertEqual(self.hub.stats["burst_gated"], 3)
        self.hub.local_heard("别看了")                                     # stopping never waits
        self.assertEqual(burst.heards, ["你看看我手里的东西", "别看了"])


class SearchAnswerTest(HubBase):
    """Owner 2026-11-16: "让其搜索新闻，效果不好" (the model never searched) and "搜索需要触发……你查查，查一下，帮我找之类的"."""
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.hub.cfg.turn = "server_vad"     # the search logic itself; the smart_turn search gate: SearchGateTest

    def hints(self, ws):
        return [m["item"]["content"][0]["text"] for m in ws.sent if m["type"] == "conversation.item.create"]

    async def instant(self, ws, rid, reply, heard=None):
        ws.push({"type": "input_audio_buffer.speech_stopped"})
        if heard is not None:
            ws.push({"type": "conversation.item.input_audio_transcription.completed", "transcript": heard})
        ws.push({"type": "response.created", "response": {"id": rid}})
        ws.push({"type": "response.audio.delta", "delta": base64.b64encode(pcm24(120)).decode()})
        ws.push({"type": "response.audio_transcript.done", "transcript": reply})
        ws.push({"type": "response.done", "response": {"id": rid, "status": "completed", "usage": {}}})
        await settle(20)

    def searcher(self, answer):
        self.asked = []

        def search(q):
            self.asked.append(q)
            return answer
        return search

    def test_words(self):
        q = self.rl.is_search_ask
        self.assertEqual([q("你查查明天天气"), q("帮我查一下黄仁勋"), q("帮我找一下最近的电影"), q("今天黄仁勋有什么新闻吗？"),
                          q("联机搜索一下英伟达前CEO今天的新闻。"), q("上海明天天气怎么样")], [True] * 6)
        self.assertEqual([q("你好呀"), q("我去检查一下代码"), q("你看看我拿着什么"), q("外面下雨了"), q("我来找你玩")],
                         [False] * 5)                          # a look question is a look, not a search

    async def test_the_answer_comes_from_the_ears_search(self):
        self.hub.searcher = self.searcher("黄仁勋11月12日宣布辞去英伟达CEO。")
        ws = await self.open_ready()
        self.hub.local_heard("今天黄仁勋有什么新闻吗？")
        await self.instant(ws, "r1", "黄仁勋是啥呀")             # the model's own instant reply: never plays
        self.assertEqual(self.link.sent, [])
        self.assertEqual(self.recs.turns[-1]["state"], "muted_wait_search")
        self.hub.step()
        await settle(20)
        self.assertEqual(self.asked, ["今天黄仁勋有什么新闻吗？"])   # one search, though both ASRs heard it
        self.assertEqual(self.hints(ws), [self.rl.search_answer_text("黄仁勋11月12日宣布辞去英伟达CEO。",
                                                                     "今天黄仁勋有什么新闻吗？")])
        self.assertEqual(ws.kinds()[-1], "response.create")
        ws.push({"type": "response.created", "response": {"id": "r2"}})
        ws.push({"type": "response.audio.delta", "delta": base64.b64encode(pcm24(120)).decode()})
        ws.push({"type": "response.audio_transcript.done", "transcript": "黄仁勋前几天辞职啦！"})
        ws.push({"type": "response.done", "response": {"id": "r2", "status": "completed", "usage": {}}})
        await settle(20)
        sid = self.link.texts()[0]["stream"]
        self.hub.on_report(self.link, {"type": "tts.playing", "stream": sid})
        self.hub.on_report(self.link, {"type": "tts.done", "stream": sid, "played_ms": 120})
        await settle()
        self.assertEqual((self.recs.turns[-1]["kind"], self.recs.turns[-1]["state"]), ("search_answer", "done"))
        self.assertEqual(self.hub._recent_pairs()[-1], ("今天黄仁勋有什么新闻吗？", "黄仁勋前几天辞职啦！"))

    async def test_nothing_found_is_said_not_made_up(self):
        self.hub.searcher = self.searcher(None)
        ws = await self.open_ready()
        await self.instant(ws, "r1", "查啥呀", heard="帮我查一下那个东西")
        self.hub.step()
        await settle(20)
        self.assertEqual(self.hints(ws), [self.rl.search_answer_text(None, "帮我查一下那个东西")])
        self.assertEqual(self.hub.stats["search_none"], 1)

    async def test_a_slow_search_counts_as_not_found(self):
        gate = asyncio.Event()
        self.hub.searcher = lambda q: None
        ws = await self.open_ready()
        self.hub._search_wait = None

        async def never(w):
            await gate.wait()
        self.hub._run_search = never
        self.hub.local_heard("你查查明天天气")
        self.t[0] += self.rl.SEARCH_WAIT_S + 1
        self.hub.step()
        await settle()
        self.assertEqual(self.hints(ws), [self.rl.search_answer_text(None, "你查查明天天气")])
        self.assertEqual(self.hub.stats["search_timeout"], 1)
        gate.set()

    async def test_a_search_filler_plays_at_once(self):
        gate = asyncio.Event()
        self.hub.searcher = lambda q: "明天上海多云，11到17度。"
        self.hub.search_fillers = [("我查查哈", pcm24(100))]
        ws = await self.open_ready()

        async def slow(w):
            await gate.wait()
        self.hub._run_search = slow
        self.hub.local_heard("你查查明天天气")
        await settle(20)
        starts = [m for m in self.link.texts() if m["type"] == "tts.start"]
        self.assertEqual(len(starts), 1)
        self.assertEqual([t["kind"] for t in self.hub._turns.values()], ["search_filler"])
        self.assertEqual(self.hub.stats["search_fillers"], 1)
        gate.set()

    async def test_no_searcher_means_the_usual_reply(self):
        ws = await self.open_ready()
        await self.instant(ws, "r1", "我不会查呀", heard="帮我查一下天气")
        self.assertEqual(self.hub.stats["muted_wait_search"], 0)
        self.assertTrue(self.link.texts())                     # the reply plays as before


class LookAnswerTest(HubBase):
    """Owner 2026-11-15 evening: "你看看我手里拿的是什么" got 看不清 / 全白 / 全黑 at once, before any photo; wait for a
    fresh look instead, or stay quiet."""
    class Burst:
        def __init__(self):
            self.mark, self.asks = 100.0, []

        def ask(self, trigger="ask"):
            self.asks.append(trigger)
            return self.mark

        def heard(self, text):
            return None

    def hints(self, ws):
        return [m["item"]["content"][0]["text"] for m in ws.sent if m["type"] == "conversation.item.create"]

    async def asyncSetUp(self):
        await super().asyncSetUp()
        # the look logic itself, with the ear's ASR acting at once (server_vad); the smart_turn gate: VisionGateTest
        self.hub.cfg.turn = "server_vad"

    async def instant(self, ws, rid, reply, heard=None):
        ws.push({"type": "input_audio_buffer.speech_stopped"})
        if heard is not None:
            ws.push({"type": "conversation.item.input_audio_transcription.completed", "transcript": heard})
        ws.push({"type": "response.created", "response": {"id": rid}})
        ws.push({"type": "response.audio.delta", "delta": base64.b64encode(pcm24(120)).decode()})
        ws.push({"type": "response.audio_transcript.done", "transcript": reply})
        ws.push({"type": "response.done", "response": {"id": rid, "status": "completed", "usage": {}}})
        await settle(20)

    def test_words(self):
        q = self.rl.is_look_ask
        self.assertEqual([q("你看看我手里拿的是什么东西"), q("这是什么？"), q("你看到了什么？"), q("你看得见我吗")], [True] * 4)
        self.assertEqual([q("你好呀"), q("社区。"), q("我们去吃饭吧")], [False] * 3)
        v = self.rl.reply_verdict
        self.assertEqual(v("真的呀，黑乎乎的啥也看不清！", True, blind=True), "blind")
        self.assertEqual(v("我看不到呀，你举近一点", False, blind=True), "blind")
        self.assertEqual(v("我看看……", False, blind=True), None)              # mentions 看: judged a bit later
        self.assertEqual(v("真的呀，黑乎乎的啥也看不清！", True), "play")        # with a fresh look it may say so

    async def test_the_answer_waits_for_a_photo_taken_after_the_question(self):
        self.hub.burst = burst = self.Burst()
        ws = await self.open_ready()
        self.hub.local_heard("你看看我手里拿的是什么东西")                       # the ear's ASR comes first
        self.assertEqual(burst.asks, ["ask"])
        await self.instant(ws, "r1", "哇，是个黑色的圆球球！")                   # a guess from no data: never plays
        self.assertEqual(self.link.sent, [])
        self.assertEqual(self.recs.turns[-1]["state"], "muted_wait_look")
        self.hub.add_look("主人坐在书架前", self.hub.wall_ms(), snapped=99.0)     # asked for before the question
        self.assertEqual(self.hints(ws), [])
        self.hub.add_look("主人手里拿着一个黑色的遥控器", self.hub.wall_ms(), snapped=100.5)
        await settle()
        self.assertEqual(self.hints(ws), [self.rl.look_answer_text("主人手里拿着一个黑色的遥控器", "你看看我手里拿的是什么东西")])
        self.assertEqual(ws.kinds()[-1], "response.create")
        ws.push({"type": "response.created", "response": {"id": "r2"}})
        ws.push({"type": "response.audio.delta", "delta": base64.b64encode(pcm24(120)).decode()})
        ws.push({"type": "response.audio_transcript.done", "transcript": "是个黑色的遥控器！"})
        ws.push({"type": "response.done", "response": {"id": "r2", "status": "completed", "usage": {}}})
        await settle(20)
        sid = self.link.texts()[0]["stream"]
        self.hub.on_report(self.link, {"type": "tts.playing", "stream": sid})
        self.hub.on_report(self.link, {"type": "tts.done", "stream": sid, "played_ms": 120})
        await settle()
        self.assertEqual((self.recs.turns[-1]["kind"], self.recs.turns[-1]["state"]), ("look_answer", "done"))
        self.assertEqual(self.hub._recent_pairs()[-1], ("你看看我手里拿的是什么东西", "是个黑色的遥控器！"))

    async def test_an_unclear_photo_waits_for_the_next_one_and_the_photo_gets_the_question(self):
        # 2026-11-18: the first photo after "你看看我手里拿的是什么" came before the thing was held up
        self.hub.burst = self.Burst()
        ws = await self.open_ready()
        self.hub.local_heard("你看看我手里拿的是什么")
        self.assertEqual(self.hub.look_question(), "你看看我手里拿的是什么")
        self.hub.add_look("看不清，手挡住了镜头。主人坐在椅子上。", self.hub.wall_ms(), snapped=100.5)
        await settle()
        self.assertEqual(self.hints(ws), [])                               # passed over
        self.hub.add_look("红色的运动相机，侧面有白色字。主人举着它。", self.hub.wall_ms(), snapped=101.0)
        await settle()
        self.assertEqual(self.hints(ws), [self.rl.look_answer_text("红色的运动相机，侧面有白色字。主人举着它。", "你看看我手里拿的是什么")])
        self.assertIsNone(self.hub.look_question())
        import vision_burst
        text = vision_burst.look_messages(b"x", None, "你看看我手里拿的是什么")[1]["content"][0]["text"]
        self.assertTrue(text.startswith("有人刚刚问 hello：「你看看我手里拿的是什么」"))
        self.assertIn("不要叫它 hello", vision_burst.LOOK_SYSTEM)

    async def test_a_filler_plays_at_once_and_the_answer_follows_it(self):
        # owner 2026-11-15: "我看看哈" while waiting
        self.hub.burst = self.Burst()
        self.hub.fillers = [("我看看哈", pcm24(100)), ("等一下，我瞅瞅", pcm24(100))]
        ws = await self.open_ready()
        self.hub.local_heard("这是什么？")
        await settle(20)
        starts = [m for m in self.link.texts() if m["type"] == "tts.start"]
        self.assertEqual(len(starts), 1)                               # the filler, no model
        self.hub.add_look("主人手里拿着一个苹果", self.hub.wall_ms(), snapped=100.5)
        await settle()
        self.assertEqual(self.hints(ws), [])                           # the filler is still playing
        sid = starts[0]["stream"]
        self.hub.on_report(self.link, {"type": "tts.playing", "stream": sid})
        self.hub.on_report(self.link, {"type": "tts.done", "stream": sid, "played_ms": 100})
        self.t[0] += 1
        self.hub.step()
        await settle()
        self.assertEqual(self.hints(ws), [self.rl.look_answer_text("主人手里拿着一个苹果", "这是什么？")])
        self.assertEqual([t["kind"] for t in self.recs.turns], ["look_filler"])
        self.assertEqual(self.recs.turns[0]["reply"] in ("我看看哈", "等一下，我瞅瞅"), True)

    async def test_no_filler_when_the_look_is_already_in(self):
        self.hub.burst = self.Burst()
        self.hub.fillers = [("我看看哈", pcm24(100))]
        ws = await self.open_ready()
        self.hub._resp = "busy"                                        # hello is mid-reply: the filler waits
        self.hub.local_heard("你看到了什么？")
        self.hub.add_look("主人在笑", self.hub.wall_ms(), snapped=101.0)
        self.hub._resp = None
        self.hub.step()
        await settle()
        self.assertEqual([m for m in self.link.texts() if m["type"] == "tts.start"], [])
        self.assertEqual(len(self.hints(ws)), 1)

    def test_load_fillers(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.rl.load_fillers(d), [])
            Path(d, "1.pcm").write_bytes(b"\x01\x00" * 2400)
            Path(d, "fillers.json").write_text(json.dumps({"sample_rate": 24000, "clips": [{"file": "1.pcm", "text": "我看看哈"}]}))
            self.assertEqual(self.rl.load_fillers(d), [("我看看哈", b"\x01\x00" * 2400)])
            Path(d, "fillers.json").write_text(json.dumps({"sample_rate": 16000, "clips": []}))
            self.assertEqual(self.rl.load_fillers(d), [])

    async def test_no_fresh_look_in_time_means_quiet(self):
        self.hub.burst = self.Burst()
        ws = await self.open_ready()
        await self.instant(ws, "r1", "我看不到呀，你举近一点", heard="这是什么？")
        self.assertEqual(self.link.sent, [])
        self.t[0] += self.rl.LOOK_WAIT_S + 1
        self.hub.step()
        await settle()
        self.assertEqual((self.hints(ws), self.hub.stats["look_timeout"]), ([], 1))
        self.hub.add_look("主人手里拿着遥控器", self.hub.wall_ms(), snapped=200.0)   # too late: nothing is said
        await settle()
        self.assertEqual(self.hints(ws), [])

    async def test_a_blind_reply_needs_a_fresh_look(self):
        ws = await self.open_ready()                                   # no burst at all: no wait either
        await self.instant(ws, "r1", "真的呀，黑乎乎的啥也看不清！", heard="确定吗？")
        self.assertEqual((self.link.sent, self.recs.turns[-1]["state"]), ([], "muted_blind"))
        self.hub._looks.append((self.hub.wall_ms(), "画面很暗，几乎全黑"))       # a fresh look says so: then it may
        await self.instant(ws, "r2", "好黑呀，啥也看不清，开个灯吧！", heard="现在呢？")
        self.assertEqual(self.link.texts()[0]["type"], "tts.start")


class CarriedNameTest(unittest.TestCase):
    """Security review 2026-11-15 + owner: a carried (guessed) name is display only; no owner greeting, no 【你的眼睛】 name."""
    def test_face_carried_names_nobody(self):
        import realtime_link as rl
        fv = rl.FaceView(60)
        fv.event({"event": "face_found", "face": 2, "box": [100, 100, 100, 120]}, 0.0)
        self.assertIsNone(fv.event({"event": "face_carried", "face": 2, "name": "boss_face", "carried": True}, 0.5))
        self.assertFalse(fv.present(["boss_face"], 0.6))
        self.assertEqual([f["name"] for f in fv.recent(0.6)], [None])
        self.assertEqual(fv.event({"event": "face_named", "face": 2, "name": "boss_face"}, 1.0), "boss_face")   # M12's own


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
        ws.push({"type": "response.audio_transcript.delta", "delta": "今天我们一起去公园玩吧"})   # text comes first (live)
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
    async def test_a_refused_greeting_is_taken_back_and_waits_for_quiet(self):
        # 2026-11-18 13:08:40: response.create refused ("user is speaking"); the [事件] item stayed and the next answer
        # greeted. Now: no greeting while the server hears speech; a refusal deletes the item and resets the turn kind.
        self.hub.cfg.max_session_s = self.hub.cfg.idle_s = self.hub.cfg.hour_cap_s = 10 ** 6
        ws = await self.open_ready()
        creates = lambda: [m for m in ws.sent if m["type"] == "conversation.item.create"]
        ws.push({"type": "input_audio_buffer.speech_started"})
        await settle(10)
        self.hub.on_vision({"event": "face_named", "face": 1, "name": "boss_face"})
        self.speak(1, speaking=False)
        self.t[0] += 0.1
        self.hub.step()
        await settle()
        self.assertEqual(creates(), [])                                   # the server hears speech: wait
        ws.push({"type": "input_audio_buffer.speech_stopped"})
        await settle(10)
        self.t[0] += 0.1
        self.hub.step()
        await settle()
        self.assertEqual(len(creates()), 1)
        self.assertEqual(self.hub._next_kind, "greet")
        ws.push({"type": "conversation.item.created", "item": {"id": "item_g1", "role": "user"}})
        ws.push({"type": "error", "error": {"code": "invalid_value", "message": "Cannot create response while user is speaking."}})
        await settle(20)
        self.assertIn({"type": "conversation.item.delete", "item_id": "item_g1"}, ws.sent)
        self.assertEqual(self.hub._next_kind, "speech")
        self.assertEqual(self.hub.stats["refused_busy"], 1)
        self.assertEqual(self.modes.stages[-1:], ["busy"])

    async def test_owner_appearing_is_greeted_once(self):
        # owner 2026-11-18: a face greeting at most once per 20 min, only after the face was gone 10 min; no short hellos
        self.hub.cfg.max_session_s = self.hub.cfg.idle_s = self.hub.cfg.hour_cap_s = 10 ** 6   # one session throughout
        ws = await self.open_ready()
        items = lambda: [m["item"]["content"][0]["text"] for m in ws.sent if m["type"] == "conversation.item.create"]

        async def appear(face):
            self.hub.on_vision({"event": "face_named", "face": face, "name": "boss_face"})
            self.speak(1, speaking=False)
            self.t[0] += 0.1
            self.hub.step()
            await settle()

        async def reply(text):
            rid = f"g{len(items())}"
            ws.push({"type": "response.created", "response": {"id": rid}})
            ws.push({"type": "response.audio_transcript.done", "transcript": text})
            ws.push({"type": "response.done", "response": {"id": rid, "status": "completed", "usage": {}}})
            await settle(20)

        def leave(face, s):
            self.hub.on_vision({"event": "face_lost", "face": face})
            self.t[0] += s

        await appear(1)                                   # first sight: one named greeting, nothing else in it
        self.assertEqual(items(), ["[事件] 主人（阿大）出现在你面前了，打个招呼。"])
        self.assertEqual(ws.kinds()[-1], "response.create")
        await reply("阿大，来了。")
        leave(1, 30)
        await appear(2)                                   # gone only 30 s: not a new recognition
        self.assertEqual(len(items()), 1)
        leave(2, 300)
        await appear(3)                                   # gone 5 min: still not (10 min away needed)
        self.assertEqual(len(items()), 1)
        leave(3, 700)
        await appear(4)                                   # gone 11 min, but only ~17 min since the greeting: too soon
        self.assertEqual(len(items()), 1)
        self.assertGreaterEqual(self.hub.stats["greet_too_soon"], 1)
        leave(4, 650)
        await appear(5)                                   # gone 11 min and 28 min since the greeting: a new one
        self.assertEqual(items()[-1], "[事件] 主人（阿大）出现在你面前了，打个招呼。之前说过：「阿大，来了。」。"
                                      "这次换个完全不同的说法。")
        self.assertEqual(len(items()), 2)
        self.assertFalse(any("阿大" in line for line in self.logs))
        self.assertIn("不提背景里的情景", self.rl.PERSONA)

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
        self.assertIn("【你的眼睛】\n人脸识别（实时）：眼前没有人脸。", first)
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


class PlaceToldTest(HubBase):
    """Place (2026-11-15): a heard sentence that may say where we are goes to mind /place/told; a change re-sends the
    instructions at once. The text is never logged."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.told = []
        inner = self.hub.call

        def call(method, path, body=None, timeout=5):
            if path == "/place/told":
                self.told.append(body)
                return 200, {"changed": "饭店" in body["text"], "label": "饭店", "state": {}}
            return inner(method, path, body, timeout)
        self.hub.call = call

    async def test_told_goes_to_mind_and_a_change_refreshes_the_instructions(self):
        ws = await self.open_ready()
        ws.push({"type": "conversation.item.input_audio_transcription.completed", "transcript": "今天天气不错呀"})
        await settle(20)
        self.assertEqual(self.told, [])                               # no 在 / 到 / 这里: not asked
        before = sum(1 for m in ws.sent if m["type"] == "session.update")
        ctx_before = self.ctx_calls
        ws.push({"type": "conversation.item.input_audio_transcription.completed", "transcript": "我们现在在饭店"})
        await settle(30)
        self.assertEqual(self.told, [{"text": "我们现在在饭店", "source": "crowd"}])
        self.assertGreater(self.ctx_calls, ctx_before)                # the context again, at once
        self.assertGreater(sum(1 for m in ws.sent if m["type"] == "session.update"), before)
        ws.push({"type": "conversation.item.input_audio_transcription.completed", "transcript": "我在想事情"})
        await settle(30)
        self.assertEqual(len(self.told), 2)                           # mind decides; unchanged: nothing else
        self.assertFalse(any("饭店" in line for line in self.logs))
        self.assertIn("现在的地点", self.rl.PERSONA)


class EyesTest(unittest.TestCase):
    def test_eye_lines_move_to_the_end(self):
        import realtime_link as rl
        ctx = "现在：11-14 22:40\n画面：主人在喝水。书架前。\n看到的人（M12 认出）：主人（阿大）\n最近的情景（半小时内，按时间）：22:10 主人在调电路"
        out = rl.instructions(ctx, "眼前有 1 个人：主人（阿大）。")
        bg, eyes = out.split("【背景】")[1].split("【你的眼睛】")
        self.assertNotIn("画面：", bg)
        self.assertIn("最近的情景", bg)
        self.assertIn("人脸识别（实时）：眼前有 1 个人：主人（阿大）。", eyes)
        self.assertIn("人脸识别（最近 3 分钟认出过）：主人（阿大）", eyes)
        self.assertIn("摄像头照片识图（大约一分钟前）：主人在喝水。书架前。", eyes)
        self.assertTrue(out.rstrip().endswith("书架前。"))


class BeepTest(unittest.TestCase):
    def test_two_chirps_vary(self):
        import random
        import realtime_link as rl
        a, b = rl.beep_pcm(random.Random(1)), rl.beep_pcm(random.Random(2))
        self.assertNotEqual(a, b)
        for pcm in (a, b):
            self.assertEqual(len(pcm) % 2, 0)
            self.assertTrue(0.2 < len(pcm) / 2 / rl.SR_OUT < 0.4)


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

            def touched(self, stop=False):
                test.order.append(("touch", stop))

            def touch_quiet(self):
                test.order.append(("quiet",))

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
        await link._mode_message({"type": "mode_report", "seq": 3, "mode": "crowd", "input": "tap", "effects": ["stop_speech"]})
        await link._mode_message({"type": "mode_report", "seq": 4, "mode": "crowd", "input": "double_tap", "effects": []})
        self.assertEqual(self.order, [("touch", False), ("touch", True), ("quiet",)])


if __name__ == "__main__":
    unittest.main()


class AutoModeTest(HubBase):
    """自动档 (owner 2026-11-18): the ear's own ASR text preloads the skill before the model answers."""
    class Head:
        version = "test"
        def reload(self):
            return False
        def decide(self, text):
            return {"need": 0.9, "skills": ["world"], "via": "head"} if "天气" in text else {"need": 0.1, "skills": [], "via": "head"}

    async def test_preload_then_expire(self):
        def call(method, path, body=None, timeout=5):
            if path == "/tools":
                return 200, {"tools": [{"type": "function", "function": {"name": n, "description": n, "parameters": {"type": "object", "properties": {}}}}
                                       for n in ("live_data", "web_search", "nod")]}
            return 200, json.loads(json.dumps(CTX))
        self.hub.call = call
        sw = Switch()
        sw.on = True
        self.hub.tools_switch, self.hub.tools_mode_fn, self.hub.intent = sw, (lambda: "auto"), self.Head()
        ws = await self.open_ready()
        self.assertEqual([t["name"] for t in [m for m in ws.sent if m["type"] == "session.update"][0]["session"]["tools"]],
                         ["open_skill"])
        self.hub.local_heard("你好呀")                                         # chit-chat: nothing loaded
        await settle(5)
        self.assertEqual(self.hub._open_skills, {})
        self.hub.local_heard("明天北京天气怎么样")
        await settle(5)
        upd = [m for m in ws.sent if m["type"] == "session.update" and "tools" in m["session"]][-1]["session"]["tools"]
        self.assertEqual([t["name"] for t in upd], ["open_skill", "live_data", "web_search"])
        self.assertEqual(set(self.hub._preloaded), {"realtime", "verify"})          # the world group's skills
        self.t[0] += self.rl.PRELOAD_KEEP_S + 1                                 # never used: it goes again
        self.hub.step()
        await settle(5)
        upd = [m for m in ws.sent if m["type"] == "session.update" and "tools" in m["session"]][-1]["session"]["tools"]
        self.assertEqual([t["name"] for t in upd], ["open_skill"])

    async def test_a_used_preload_stays(self):
        self.hub._skills = {"world": {"id": "world", "tools": ["live_data"]}}
        self.hub._open_skills, self.hub._preloaded = {"world": 1000.0}, {"world": 1000.0}
        self.hub._used_skill("live_data", 1001.0)
        self.assertEqual(self.hub._preloaded, {})
        self.hub._skill_cfg = {"keep_min": 10}
        self.t[0] = 1001.0 + self.rl.PRELOAD_KEEP_S + 1
        self.assertFalse(self.hub._prune_skills(self.t[0]))                   # normal keep (10 min) now


class FreeSkillTest(HubBase):
    """自由发挥 (owner 2026-11-18): one skill that hands every tool, past the 16-tool cap, kept 5 minutes."""
    async def test_free_gives_every_tool_then_goes(self):
        await self.open_ready()
        self.hub._all_specs = {f"t{i}": {"type": "function", "name": f"t{i}"} for i in range(30)}
        self.hub._skills = {"free": {"id": "free", "title": "自由发挥", "when": "x", "tools": ["*"], "keep_min": 5},
                            "a": {"id": "a", "title": "A", "when": "y", "tools": ["t1", "t2"]}}
        self.hub._skill_cfg = {"keep_min": 10, "max_open": 4, "max_tools": 16}
        self.hub._open_skill("a", self.t[0])
        self.hub._open_skill("free", self.t[0])
        self.assertEqual(len(self.hub._tool_specs), 31)                    # open_skill + all 30
        self.t[0] += 5 * 60 + 1
        self.assertTrue(self.hub._prune_skills(self.t[0]))
        self.assertEqual(set(self.hub._open_skills), {"a"})               # free goes after 5 min, a stays


class BodyLineTest(HubBase):
    """collector-0.5.12 (owner 2026-11-19): the body sense's line goes in 【你的眼睛】; an event re-sends the instructions."""
    async def test_body_line_in_the_eyes_and_a_broken_body_costs_nothing(self):
        text = self.rl.instructions("", body="身体感觉：被拿起来了（3 秒前）", skills="技能")
        eyes = text.index("【你的眼睛】")
        self.assertGreater(text.index("身体感觉：被拿起来了"), eyes)
        self.assertLess(text.index("身体感觉：被拿起来了"), text.index("【此刻打开的技能】"))
        self.assertNotIn("\n身体感觉：", self.rl.instructions("", body=None))   # the eyes line, not the rule

        class Body:
            def __init__(self, line=None, boom=False):
                self.l, self.boom = line, boom

            def line(self, now_ms):
                if self.boom:
                    raise RuntimeError("x")
                return self.l
        self.hub.body = Body("身体感觉：有东西靠得很近（1 秒前）")
        self.assertEqual(self.hub._body_line(), "身体感觉：有东西靠得很近（1 秒前）")
        self.hub.body = Body(boom=True)
        self.assertIsNone(self.hub._body_line())
        self.hub.body_changed()
        self.assertTrue(self.hub._looks_dirty or self.hub._ws is None)


class TouchTest(HubBase):
    """Owner 2026-11-19 (collector-0.5.15): in Docking a tap while hello is quiet = a must-answer call (stronger and
    longer than her name: TOUCH_S, wakes 安静, 在听 under the eyes until she is on it); a tap while she speaks stops
    her; a double tap = 安静一下."""
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.words = []
        self.hub.screen_word = lambda text, hold: self.words.append((text, hold))

    async def test_a_tap_opens_a_long_call_window_and_wakes_quiet(self):
        await self.open_ready()
        self.hub.set_quiet("quiet")
        self.assertTrue(self.hub.touched())
        now = self.hub.clock()
        self.assertFalse(self.hub.quiet_on())
        self.assertTrue(self.hub.in_called(now + self.rl.CALLED_S + 5))            # longer than a call by name
        self.assertFalse(self.hub.in_called(now + self.rl.TOUCH_S + 0.1))
        self.assertIn(("在听", int(self.rl.TOUCH_S)), self.words)
        self.assertEqual(self.hub.stats["called_touch"], 1)
        self.hub._stage("think")                                                  # she is on it: 在听 goes
        self.assertEqual(self.words[-1][0], "")

    async def test_a_tap_while_speaking_stops_her_and_closes_the_window(self):
        ws = await self.open_ready()
        ws.push({"type": "input_audio_buffer.speech_stopped"})
        ws.push({"type": "response.created", "response": {"id": "r1"}})
        ws.push({"type": "response.audio.delta", "delta": base64.b64encode(pcm24(120)).decode()})
        await settle(20)
        self.hub.called("asr")
        self.assertTrue(self.hub.touched(stop=True))
        await settle(10)
        self.assertTrue(any(m["type"] == "response.cancel" for m in ws.sent))
        self.assertFalse(self.hub.in_called())
        self.assertEqual(self.hub.stats["touch_stop"], 1)

    async def test_a_double_tap_is_quiet(self):
        await self.open_ready()
        self.hub.touched()
        self.assertTrue(self.hub.touch_quiet())
        self.assertTrue(self.hub.quiet_on())
        self.assertFalse(self.hub.in_called())
        self.assertEqual(self.words[-1][0], "安静中")


class ToolBusyTest(HubBase):
    """Owner 2026-11-19: while a tool runs hello is busy as if speaking (amber dots, a word, no new speech up); a call or
    a tap cancels it and its result is dropped."""
    async def start(self, release):
        import threading
        self.words = []
        self.hub.screen_word = lambda text, hold: self.words.append(text)

        def call(method, path, body=None, timeout=5):
            if path == "/tools":
                return 200, {"tools": [{"type": "function", "function": {"name": "doc_answer", "description": "资料",
                                                                         "parameters": {"type": "object", "properties": {}}}}]}
            if path == "/tool/call":
                release.wait(5)
                return 200, {"ok": True, "data": {"answer": "x"}, "error": None}
            return 200, json.loads(json.dumps(CTX))
        self.hub.call = call
        sw = Switch()
        sw.on = True
        self.hub.tools_switch = sw
        ws = await self.open_ready()
        self.hub.called("text")                                   # past the tool gate
        ws.push({"type": "response.created", "response": {"id": "r1"}})
        ws.push({"type": "response.function_call_arguments.done", "response_id": "r1", "call_id": "c1",
                 "name": "doc_answer", "arguments": '{"question": "ICN7332 录几次"}'})
        ws.push({"type": "response.done", "response": {"id": "r1", "status": "completed"}})
        await settle(30)
        return ws

    def outputs(self, ws):
        return [m for m in ws.sent if m["type"] == "conversation.item.create" and m["item"]["type"] == "function_call_output"]

    async def test_busy_while_the_tool_runs_then_answered(self):
        import threading
        release = threading.Event()
        ws = await self.start(release)
        self.assertEqual(self.hub._tool_wait["name"], "doc_answer")
        self.assertTrue(self.hub.busy())
        self.assertIn("查资料中", self.words)
        release.set()
        await settle(30)
        self.assertIsNone(self.hub._tool_wait)
        self.assertEqual(json.loads(self.outputs(ws)[0]["item"]["output"])["data"], {"answer": "x"})
        self.assertEqual(ws.sent[-1]["type"], "response.create")

    async def test_a_call_cancels_the_running_tool(self):
        import threading
        release = threading.Event()
        ws = await self.start(release)
        self.hub.called("asr")                                    # "hello" while it runs
        self.assertEqual(self.hub.stats["tool_interrupted"], 1)
        release.set()
        await settle(30)
        out = self.outputs(ws)
        self.assertEqual(json.loads(out[-1]["item"]["output"])["error"]["code"], "cancelled")
        self.assertNotEqual(ws.sent[-1]["type"], "response.create")
        self.assertEqual(self.hub.stats["tool_dropped"], 1)

    async def test_a_tap_while_the_tool_runs_is_a_stop(self):
        import threading
        release = threading.Event()
        await self.start(release)
        self.hub.touched()                                        # older firmware: no stop_speech on the report
        self.assertEqual(self.hub.stats["touch_stop"], 1)
        self.assertEqual(self.hub.stats["tool_interrupted"], 1)
        release.set()
        await settle(30)


class ShakenSayTest(HubBase):
    """Owner 2026-11-19 "使劲摇晃了机器人，没有任何反应": being shaken is an event hello answers in one line, once a minute."""
    async def test_shaken_once_a_minute_and_not_in_quiet(self):
        ws = await self.open_ready()
        self.assertTrue(self.hub.body_event("shaken", {"peak_g": 1.1}))
        self.assertFalse(self.hub.body_event("picked_up"))
        self.hub.step()
        await settle(10)
        sent = [m for m in ws.sent if m["type"] == "conversation.item.create"]
        self.assertIn("用力晃了几下", json.dumps(sent[-1], ensure_ascii=False))
        self.assertEqual(ws.sent[-1]["type"], "response.create")
        self.assertFalse(self.hub.body_event("shaken"))                    # within SHAKEN_SAY_S
        self.t[0] += self.rl.SHAKEN_SAY_S + 1
        self.hub.set_quiet("quiet")
        self.assertTrue(self.hub.body_event("shaken"))
        n = len(ws.sent)
        self.hub.step()
        await settle(10)
        self.assertFalse(any(m["type"] == "response.create" for m in ws.sent[n:]))   # 安静: not said


class ModeSpanTest(HubBase):
    """Owner 2026-11-19: how long a mode lasts is said (30 分钟 / 直到我说退出 / 直到我叫你); a said length is sticky
    (a call is answered, the mode goes on); modes are kept for an ear restart."""
    def test_parse(self):
        import mode_span as ms
        self.assertEqual([ms.parse(t) for t in ("安静30分钟", "安静半个小时", "安静一个半小时", "两个钟头", "一刻钟",
                                                 "安静一会儿")], [1800.0, 1800.0, 5400.0, 7200.0, 900.0, None])
        self.assertEqual([ms.parse(t) for t in ("直到我说退出", "等我通知", "一直安静", "直到我叫你", "等我叫你再说")],
                         [ms.OPEN, ms.OPEN, ms.OPEN, ms.CALLED, ms.CALLED])
        self.assertEqual([ms.ended("退出安静模式"), ms.ended("取消吧"), ms.ended("取消吧", loose=True),
                          ms.ended("取消订单", loose=True), ms.ended("可以说话了")], [True, False, True, False, True])

    async def test_said_length_is_sticky_and_said_exit_ends(self):
        ws = await self.open_ready()
        now = self.hub.clock()
        self.hub.local_heard("你先安静三十分钟")
        self.assertTrue(self.hub.quiet_on(now + 29 * 60))
        self.assertFalse(self.hub.quiet_on(now + 31 * 60))
        self.hub.called("asr")
        self.assertTrue(self.hub.quiet_on())                                # sticky: the call is answered, quiet stays
        self.assertTrue(self.hub.in_called())
        self.hub.local_heard("hello，退出安静模式")
        self.assertFalse(self.hub.quiet_on())

    async def test_open_ended_and_default(self):
        await self.open_ready()
        self.hub.local_heard("你先安静，直到我说退出")
        self.assertTrue(self.hub.quiet_on(self.hub.clock() + 10 * 3600))
        self.assertIn("直到主人说退出", self.hub._manner_line())
        self.hub.set_quiet("off")
        self.hub.local_heard("你先安静一会儿")                              # nothing said: until called (as before)
        self.hub.called("asr")
        self.assertFalse(self.hub.quiet_on())

    async def test_tool_until_exit_and_role_minutes(self):
        await self.open_ready()
        self.assertEqual(self.hub.set_quiet("low", until="exit"), self.rl.mode_span.OPEN)
        end = self.hub.set_role("当个老师", minutes=45)
        self.assertAlmostEqual(end - self.hub.clock(), 45 * 60, delta=1)

    async def test_modes_survive_a_restart(self):
        rows = {}

        class Db:
            def switch_status(self, name, fields):
                rows[name] = fields

            def __getattr__(self, n):
                return lambda *a, **k: None
        self.hub.db = Db()
        await self.open_ready()
        self.hub.set_quiet("quiet", until="exit")
        self.hub.set_role("当个老师", minutes=60)
        doc = rows["rt_modes"]
        self.assertTrue(doc["quiet"]["open"] and doc["quiet"]["sticky"])
        self.hub._quiet_mode = self.hub._role = None
        self.assertEqual(self.hub.restore_modes(doc), ["quiet", "role"])
        self.assertTrue(self.hub.quiet_on() and self.hub._quiet_sticky)
        self.assertEqual(self.hub.role_text(), "当个老师")
        self.assertEqual(self.hub.restore_modes({"low": {"until_ms": 1, "open": False}}), [])      # long over


class DebugModeTest(HubBase):
    turn = QuietLowRoleTest.turn
    """Owner 2026-11-19 (task 3): 调试模式 by a word, until the owner ends it unless a length is said; hello stays out of
    the talk (a call is answered), says mind's reminders, and asks at the end whether to sum up."""
    async def test_enter_mute_alert_and_end(self):
        words = []
        self.hub.screen_word = lambda text, hold: words.append(text)
        ws = await self.open_ready()
        self.hub.local_heard("hello，进入调试模式")
        self.assertTrue(self.hub.debug_on(self.hub.clock() + 8 * 3600))       # no length: until ended
        self.assertEqual(words[-1], "调试中")
        self.assertIn("主人在调试", self.hub._manner_line())
        self.t[0] += self.rl.CALLED_S + 1                                     # "hello，…" was a call: let its window pass
        self.turn(ws, "r1", "电流一百二十毫安", "电流挺正常的")
        await settle(10)
        self.assertEqual(self.hub.stats["muted_debug_mode"], 1)              # not answered: not called
        self.assertEqual(self.hub.debug_alert("NACK 第三次了"), "queued")
        self.assertEqual(self.hub.debug_alert("又一件"), "too_soon")                 # DEBUG_ALERT_S
        ws.push({"type": "response.done", "response": {"id": "r1", "status": "completed"}})
        await settle(10)
        self.hub.step()
        await settle(10)
        self.assertIn("NACK 第三次了", json.dumps([m for m in ws.sent if m["type"] == "conversation.item.create"][-1],
                                                ensure_ascii=False))
        self.hub._greet = None
        self.hub.local_heard("好了，退出调试模式")
        self.assertFalse(self.hub.debug_on())
        self.assertEqual(self.hub._greet["kind"], "debug_end")
        self.assertIn("要不要把这次调试总结", self.hub._greet["text"])

    async def test_length_and_restore(self):
        await self.open_ready()
        self.hub.local_heard("帮我调试一个小时")
        now = self.hub.clock()
        self.assertTrue(self.hub.debug_on(now + 59 * 60))
        self.assertFalse(self.hub.debug_on(now + 61 * 60))
        since = self.hub._debug_since_ms
        self.hub._debug = None
        self.assertEqual(self.hub.restore_modes({"debug": {"since_ms": since, "until_ms": None, "open": True}}), ["debug"])
        self.assertTrue(self.hub.debug_on())
        self.assertEqual(self.hub.set_debug(False, by="tool"), None)
        self.assertIsNone(self.hub._greet)                                      # the model answers a tool exit itself


class ModeWordTest(HubBase):
    """Owner 2026-11-19 "处于什么特殊模式的，stackchan上要有提示": one word for the most important mode, every word drawable
    by the robot's font (collector-0.5.17), put back when the robot reconnects."""
    async def test_words_priority_and_font(self):
        import re
        words = []
        self.hub.screen_word = lambda text, hold: words.append((text, hold))
        await self.open_ready()
        self.hub.set_role("当个老师")
        self.assertEqual(words[-1][0], "角色中")
        self.hub.set_quiet("low")
        self.assertEqual(words[-1][0], "少插话")
        self.hub.set_quiet("quiet", until="exit")
        self.assertEqual(words[-1], ("安静中", 3600))
        self.hub.set_debug(True)
        self.assertEqual(words[-1][0], "调试中")
        n = len(words)
        self.hub.robot_back()
        self.assertEqual(words[n:], [("调试中", 3600)])
        h = (Path(__file__).resolve().parents[1] / "upstream/StackChan/firmware/main/collector/m12_words.h")
        if h.exists():
            glyphs = re.search(r'kGlyphs = "([^"]*)"', h.read_text(encoding="utf-8")).group(1)
            shown = {"调试中", "安静中", "少插话", "角色中", "在听", *self.rl.TOOL_WORD.values(), *self.rl.TOOL_WORD_GROUP.values()}
            self.assertEqual([w for w in shown if any(c not in glyphs for c in w)], [])


class MustAnswerPinTest(CalledTest):
    """PIN, 2026-11-19 night (Fable's audit 4.3, docs/audits/2026-11-19-fable-architecture.md): what happens today after a
    call or a tap, written down before the must-answer guard (Fable step 1) changes it. Asserts marked GAP are today's
    missed answers: the guard is meant to turn them around, and its change will show up exactly there."""
    async def silent_sentence(self, ws, text):
        ws.push({"type": "input_audio_buffer.speech_started"})
        ws.push({"type": "input_audio_buffer.speech_stopped"})
        await settle(10)
        self.hub.local_heard(text)                    # the ear's own text; Qwen sends nothing for it
        self.t[0] += 3.0
        self.hub.step()
        await settle(20)

    async def test_gap_a_call_then_qwen_silent_gets_no_answer(self):
        ws = await self.open_ready()
        self.hub.called("asr")
        await self.silent_sentence(ws, "今天天气怎么样")
        self.assertEqual(self.hints(ws), [])          # GAP: no fallback for an ASR / name / tap call

    async def test_gap_a_tap_then_qwen_silent_gets_no_answer(self):
        ws = await self.open_ready()
        self.hub.touched()
        await self.silent_sentence(ws, "今天天气怎么样")
        self.assertEqual(self.hints(ws), [])          # GAP: the tap's must-answer has no fallback either

    async def test_a_call_word_in_the_text_then_qwen_silent_is_answered(self):
        ws = await self.open_ready()
        self.hub.local_heard("hello，今天天气怎么样")
        self.t[0] += self.rl.CALLED_ANSWER_WAIT_S + 0.5
        self.hub.step()
        await settle(20)
        self.assertEqual(len(self.hints(ws)), 1)      # today the only fallback: a sentence that starts with the name

    async def test_a_call_then_only_en_is_rescued(self):
        ws = await self.open_ready()
        self.hub.called("asr")
        ws.push({"type": "input_audio_buffer.speech_started"})
        ws.push({"type": "input_audio_buffer.speech_stopped"})
        ws.push({"type": "conversation.item.input_audio_transcription.completed", "transcript": "今天天气怎么样"})
        ws.push({"type": "response.created", "response": {"id": "r1"}})
        ws.push({"type": "response.audio_transcript.done", "transcript": "嗯。"})
        ws.push({"type": "response.done", "response": {"id": "r1", "status": "completed", "usage": {}}})
        await settle(20)
        self.assertEqual(self.recs.turns[-1]["state"], "muted_quiet")
        self.assertEqual(self.hints(ws), [self.rl.rescue_text("今天天气怎么样")])

    async def test_a_call_then_the_same_short_answer_as_before_plays(self):
        ws = await self.open_ready()
        await self.play_one(ws, "r0")                 # hello said 你好呀😀 a moment ago
        self.hub.called("asr")
        ws.push({"type": "input_audio_buffer.speech_started"})
        ws.push({"type": "input_audio_buffer.speech_stopped"})
        ws.push({"type": "response.created", "response": {"id": "r1"}})
        ws.push({"type": "response.audio.delta", "delta": base64.b64encode(pcm24(120)).decode()})
        ws.push({"type": "response.audio_transcript.done", "transcript": "你好呀😀"})
        ws.push({"type": "response.done", "response": {"id": "r1", "status": "completed", "usage": {}}})
        await settle(20)
        # measured 2026-11-19: this short repeat plays; Fable's repeat-gate misses (23 / 113 follow-ups) are longer
        # answers repeated word for word within recent_s: reproduce from the run log before calling it a gap
        self.assertEqual(self.recs.turns[-1]["state"], "done")

    async def test_gap_a_call_inside_the_retry_wait_does_not_reopen(self):
        await self.open_ready()
        self.hub.close("upstream")
        self.hub._retry_at = self.hub.clock() + 30     # as after an upstream failure (retry_s)
        self.hub.called("asr")
        self.hub.step()
        await settle(10)
        self.assertEqual(self.hub.state, "closed")     # GAP: deaf until the retry wait is over


class SentenceCardTest(HubBase):
    """Fable's step 0 (2026-11-19): every sentence the ear hears gets a card (evidence at that moment) and, after
    CARD_WAIT_S, one gate.addressed row with its outcome and a shadow score that decides nothing."""
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.rows = []
        import ear_log
        self._emit = ear_log.emit
        ear_log.emit = lambda kind, **f: (self.rows.append((kind, f)), self._emit(kind, **f))[1]

    async def asyncTearDown(self):
        import ear_log
        ear_log.emit = self._emit
        await super().asyncTearDown()

    def cards(self):
        return [f for k, f in self.rows if k == "gate.addressed"]

    async def test_a_called_sentence_qwen_never_judged_is_must_and_none(self):
        ws = await self.open_ready()
        self.hub.called("asr")
        self.hub.local_heard("今天天气怎么样")
        self.t[0] += self.rl.CARD_WAIT_S if hasattr(self.rl, "CARD_WAIT_S") else 10.0
        self.hub.step()
        await settle(10)
        c = self.cards()[-1]["extra"]
        self.assertEqual((c["level"], c["outcome"], c["called_source"], c["question"]), ("must", "none", "asr", False))
        self.assertNotIn("text", c)                                   # the words stay in heard.asr only

    async def test_an_ambient_sentence_and_an_answered_one(self):
        ws = await self.open_ready()
        ws.push({"type": "conversation.item.ambient_audio_transcription.completed", "transcript": "他们在聊午饭"})
        await settle(10)
        self.hub.local_heard("他们在聊午饭")
        self.t[0] += 8.0
        await self.play_one(ws, "r1")                                  # a reply that played, before the ear's text
        self.t[0] += 1.0
        self.hub.local_heard("你好呀")
        self.t[0] += 10.5
        self.hub.step()
        await settle(10)
        outs = [c["extra"]["outcome"] for c in self.cards()]
        self.assertIn("ambient", outs)
        self.assertIn("answered", outs)

    def test_score(self):
        import rt_rules
        self.assertEqual(rt_rules.address_score({"touch": True, "called": True}), (80, "must"))
        self.assertEqual(rt_rules.address_score({"follow_s": 5.0, "question": True}), (40, "should"))
        self.assertEqual(rt_rules.address_score({"you": True, "faces": 1}), (20, "maybe"))
        self.assertEqual(rt_rules.address_score({"follow_s": 60.0}), (0, "low"))
        self.assertEqual(rt_rules.card_outcome([("ambient", None), ("turn", "muted_quiet")]), "muted:quiet")
        self.assertEqual(rt_rules.card_outcome([]), "none")
