"""Offline check of the 围观 realtime session (spike 2026-11-14): the ear's own RealtimeHub, a fake robot, a real
DashScope session. Costs a few cents; nothing is deployed, nothing touches the robot.

    .private/venv-ear/bin/python server/ear/realtime_offline.py --clip <16 kHz mono wav> --start 382.4 --dur 5 \
        --env ~/hubmix/idea/agent/.env --voices <soul-tts voices.json> --voice-key yy01 --out <folder>

What it does, in one session (the hub's real supervisor, gate, encoder, flow control and records):
  1. speech: the clip chunk goes in at real time with the ear's Silero VAD as the `speaking` flag, then silence;
     the reply is played by a fake robot (it decodes the Opus frames and reports playing / progress / done in real
     time) and saved as WAV + M4A.
  2. owner greeting: an M12 face_named event for the owner's face name -> the hub's greeting.
  3. tap greeting: an unknown adult-female face (face_found + face_attrs) and a tap -> the hub's tap greeting.
The context block is synthetic (no real names). Prints timings and token usage per turn, never the key or the voice.
Voice similarity (CAM++, as research/20261114-rt-toolcall): --sample <the clone's sample wav>.
"""
import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import realtime_link as rl  # noqa: E402
from mouth_link import RT_STREAM_BASE  # noqa: E402

CONTEXT = {"text": "现在：11-14 12:05\n此刻的情景：几个人围在办公桌旁边聊天（午饭前，大家在商量吃什么）\n"
                   "在聊：午饭 / 面条\n看到的人（M12 认出）：主人（老大）\n你记得（只在相关时提）：主人爱吃面",
           "owner": {"face_names": ["owner_face"], "call": "老大"}, "names": {"guest_face": "小王"}, "visible": []}


class Switch:
    on = True


class Modes:
    def __init__(self, link):
        self.robot_mode, self._mode_conn, self._links = "crowd", 1, {1: link}


class FakeRobot:
    """Plays our /mouth stream like the robot: 180 ms prebuffer, real-time played_ms, done after tts.end."""
    def __init__(self, hub_ref):
        import opuslib_next
        self.dec = opuslib_next.Decoder(24000, 1)
        self.hub_ref, self.tts_active = hub_ref, set()
        self.streams, self.cur = {}, None
        self.task = None

    async def send_from_realtime(self, message):
        if isinstance(message, bytes):
            _, sid, seq, fms, _ = rl.HDR.unpack_from(message)
            st = self.streams.get(sid)
            if st is not None and not st["stopped"]:
                st["pcm"] += self.dec.decode(message[rl.HDR.size:], rl.FRAME_SAMPLES)
                st["frames"] += 1
            return True
        m = json.loads(message)
        if m["type"] == "tts.start":
            assert m["stream"] >= RT_STREAM_BASE and m["codec"] == "opus" and m["frame_ms"] == 60
            self.streams[m["stream"]] = {"pcm": b"", "frames": 0, "ended": False, "stopped": False, "t0": time.monotonic(),
                                         "sound_at": None}
            self.cur = m["stream"]
            self.task = asyncio.create_task(self._play(m["stream"]))
        elif m["type"] == "tts.end":
            self.streams[m["stream"]]["ended"] = True
        elif m["type"] == "tts.stop":
            self.streams[m["stream"]]["stopped"] = True
        return True

    async def _play(self, sid):
        st, hub = self.streams[sid], self.hub_ref[0]
        while st["frames"] * 60 < 180 and not st["ended"] and not st["stopped"]:
            await asyncio.sleep(0.02)
        start = st["sound_at"] = time.monotonic()
        hub.on_report(self, {"type": "tts.playing", "stream": sid})
        while not st["stopped"]:
            played = min(int((time.monotonic() - start) * 1000), st["frames"] * 60)
            if st["ended"] and played >= st["frames"] * 60:
                hub.on_report(self, {"type": "tts.done", "stream": sid, "played_ms": played})
                return
            hub.on_report(self, {"type": "tts.progress", "stream": sid, "played_ms": played})
            await asyncio.sleep(0.25)


class Records:
    def __init__(self):
        self.turns = []

    def realtime_turn(self, doc):
        self.turns.append(doc)

    def switch_status(self, name, fields):
        pass


def save(pcm24, path):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(24000), w.writeframes(pcm24)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-c:a", "aac", "-b:a", "96k",
                    str(path.with_suffix(".m4a"))], check=False)


def vad_flags(x, models):
    import sherpa_onnx as so
    cfg = so.VadModelConfig(silero_vad=so.SileroVadModelConfig(model=f"{models}/silero_vad.onnx", threshold=0.5,
                                                                min_silence_duration=0.4, min_speech_duration=0.3,
                                                                max_speech_duration=20), sample_rate=16000)
    vad, flags = so.VoiceActivityDetector(cfg, buffer_size_in_seconds=60), []
    for i in range(0, len(x) // 960 * 960, 960):
        vad.accept_waveform(x[i:i + 960])
        flags.append(vad.is_speech_detected())
        while not vad.empty():
            vad.pop()
    return flags


async def wait_turn(hub, before, timeout):
    """Until one more turn is recorded (the robot reported its reply done) and hello is quiet again."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if hub.stats["turns"] > before and not hub.output_busy() and time.monotonic() >= hub._quiet_until:
            return True
        await asyncio.sleep(0.1)
    print(f"timeout waiting for a turn ({timeout}s)", flush=True)
    return False


async def main(a):
    key = next(l.split("=", 1)[1].strip().strip("'\"") for l in Path(a.env).expanduser().read_text().splitlines()
               if l.startswith("DASHSCOPE_API_KEY="))
    voice = rl.load_voice(a.voices, a.voice_key)
    if not voice:
        raise SystemExit("voice not found")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    hub_ref = [None]
    robot = FakeRobot(hub_ref)
    recs = Records()
    hub = rl.RealtimeHub(key=key, voice=voice, modes=Modes(robot), call=lambda *x: (200, CONTEXT), switch=Switch(),
                         db=recs)
    hub_ref[0] = hub
    said = []
    hub.on_transcript = lambda who, text: said.append([who, text])      # offline only: for the owner to read
    rl.log = lambda *x, **k: print(*x, flush=True)

    with wave.open(a.clip) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
        w.setpos(int(a.start * 16000))
        clip = np.frombuffer(w.readframes(int(a.dur * 16000)), "<i2")
    with wave.open(str(out / "q_clip.wav"), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000), w.writeframes(clip.tobytes())
    flags = vad_flags(clip.astype(np.float32) / 32768, a.models)
    hub.start()
    await asyncio.sleep(0.3)                       # the supervisor opens the session for crowd
    t_open = time.monotonic()
    while not hub._ready.is_set() and time.monotonic() - t_open < 8:
        await asyncio.sleep(0.05)
    print(f"session ready in {time.monotonic() - t_open:.2f}s", flush=True)

    # 1. speech at real time, then silence until the reply has played
    local_end = None
    t_clip0 = time.monotonic()
    for i, sp in enumerate(flags):
        hub.feed(clip[i * 960:(i + 1) * 960].tobytes(), sp, False)    # the hub gates on its own output
        if sp:
            local_end = time.monotonic()
        await asyncio.sleep(0.06)
    for _ in range(int(20 / 0.06)):                # silence (the mic keeps going) until the reply has played
        hub.feed(bytes(1920), False, False)
        await asyncio.sleep(0.06)
        if hub.stats["turns"] >= 1 and not hub.output_busy():
            break
    await wait_turn(hub, 0, 10)
    print(f"clip: local VAD speech end at +{local_end - t_open:.2f}s after open" if local_end else "clip: no speech", flush=True)

    # 2. owner greeting: the owner's face is named
    n = hub.stats["turns"]
    hub.on_vision({"event": "face_found", "face": 7, "box": [200, 120, 180, 200]})
    hub.on_vision({"event": "face_named", "face": 7, "name": "owner_face", "box": [200, 120, 180, 200]})
    await wait_turn(hub, n, 15)

    # 3. tap: an unknown adult woman is the largest face (the owner face is gone)
    hub.on_vision({"event": "face_lost", "face": 7})
    await asyncio.sleep(2.5)
    hub.on_vision({"event": "face_found", "face": 9, "box": [150, 100, 220, 240]})
    hub.on_vision({"event": "face_attrs", "face": 9, "gender": "f", "age_group": "adult", "box": [150, 100, 220, 240]})
    hub.on_vision({"event": "face_found", "face": 10, "box": [500, 100, 60, 70]})
    n = hub.stats["turns"]
    hub.tap()
    await wait_turn(hub, n, 15)
    await hub.shutdown()

    names = ["a1_speech", "a2_owner_greet", "a3_tap_greet"]
    files = []
    for name, (sid, st) in zip(names, sorted(robot.streams.items())):
        save(st["pcm"], out / f"{name}.wav")
        files.append(out / f"{name}.wav")
    first = sorted(robot.streams.items())
    if first and a.speech_end is not None:
        st = first[0][1]
        summary_lat = {"speech_end_to_tts_start_s": round(st["t0"] - (t_clip0 + a.speech_end), 3),
                       "speech_end_to_robot_sound_s": round(st["sound_at"] - (t_clip0 + a.speech_end), 3) if st["sound_at"] else None,
                       "note": "speech end = the clip's speech end (Silero segment end); robot sound = fake robot after its 180 ms prebuffer"}
    else:
        summary_lat = None
    summary = {"latency": summary_lat, "transcripts": said, "turns": recs.turns, "streams": len(robot.streams), "stats": dict(hub.stats)}
    if a.sample:
        import sherpa_onnx as so
        ex = so.SpeakerEmbeddingExtractor(so.SpeakerEmbeddingExtractorConfig(
            model=f"{a.models}/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx", num_threads=2))

        def emb(p):
            with wave.open(str(p)) as w:
                sr, x = w.getframerate(), np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768
            s = ex.create_stream(); s.accept_waveform(sr, x); s.input_finished()
            e = np.array(ex.compute(s)); return e / np.linalg.norm(e)
        ref = emb(a.sample)
        summary["similarity"] = {f.stem: round(float(ref @ emb(f)), 3) for f in files}
        summary["similarity"]["q_clip(control)"] = round(float(ref @ emb(out / "q_clip.wav")), 3)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip", required=True)
    ap.add_argument("--start", type=float, default=0)
    ap.add_argument("--dur", type=float, default=6)
    ap.add_argument("--env", default="~/hubmix/idea/agent/.env")
    ap.add_argument("--voices", required=True)
    ap.add_argument("--voice-key", default="yy01")
    ap.add_argument("--models", default=os.path.expanduser("~/soul/spikes/sherpa-onnx/models"))
    ap.add_argument("--speech-end", type=float, help="seconds into the chunk where the question ends (for latency)")
    ap.add_argument("--sample", help="the clone's sample wav, for CAM++ similarity")
    ap.add_argument("--out", required=True)
    asyncio.run(main(ap.parse_args()))
