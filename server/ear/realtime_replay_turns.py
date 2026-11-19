"""Which turn detection gives Qwen a verdict on the most sentences? (Fable's audit step 2 "弄清没判定", 2026-11-19:
under smart_turn_v2 about 80% of the sentences got no verdict at all.) The ear's real RealtimeHub, a fake robot and a
real DashScope session, one recording, three settings, one after another:

  smart        cfg.turn smart_turn_v2, cfg.gate vad   (live today: audio only around the ear's own speech + tail)
  smart_all    cfg.turn smart_turn_v2, cfg.gate all   (the silence goes up too: does smart_turn then decide?)
  server_vad   cfg.turn server_vad,    cfg.gate vad   (before 2026-11-18)

For every speech segment of the ear's VAD (Silero, as live) it notes whether Qwen gave a verdict within VERDICT_S of
the segment's end: an answer (response.created), "not for me" (ambient transcript), or nothing, and how long it took.
Costs a few cents per setting (audio in real time, the replies). Prints counts only, no transcript text.

    .private/venv-ear/bin/python server/ear/realtime_replay_turns.py --clip <16 kHz wav> --start 300 --dur 180 \\
        --voices <soul-tts voices.json> --voice-key ly01 --out <folder>
"""
import argparse
import asyncio
import json
import os
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import realtime_link as rl  # noqa: E402
from realtime_offline import CONTEXT, FakeRobot, Modes, Records, Switch, vad_flags  # noqa: E402

SETTINGS = {"smart": ("smart_turn_v2", "vad"), "smart_all": ("smart_turn_v2", "all"), "server_vad": ("server_vad", "vad")}
VERDICT_S = 6.0
FRAME = 960                       # 60 ms at 16 kHz, as the ear feeds


def segments(flags):
    """[(start_frame, end_frame)] of the VAD's speech runs."""
    out, start = [], None
    for i, sp in enumerate(flags):
        if sp and start is None:
            start = i
        elif not sp and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(flags)))
    return out


async def run_one(name, clip, flags, key, voice):
    turn, gate = SETTINGS[name]
    hub_ref = [None]
    robot = FakeRobot(hub_ref)
    hub = rl.RealtimeHub(key=key, voice=voice, modes=Modes(robot), call=lambda *x: (200, CONTEXT), switch=Switch(),
                         db=Records())
    hub.cfg.turn, hub.cfg.gate = turn, gate
    hub_ref[0] = hub
    rl.log = lambda *x, **k: None
    events = []                                   # (t, kind) from Qwen
    orig = hub._event

    def spy(e, n):
        k = e.get("type", "")
        if k == "response.created":
            events.append((time.monotonic(), "answer"))
        elif k.endswith("ambient_audio_transcription.completed"):
            events.append((time.monotonic(), "ambient"))
        elif k == "input_audio_buffer.speech_stopped":
            events.append((time.monotonic(), "stopped"))
        return orig(e, n)
    hub._event = spy
    hub.start()
    t_open = time.monotonic()
    while not hub._ready.is_set() and time.monotonic() - t_open < 10:
        await asyncio.sleep(0.05)
    t0 = time.monotonic()
    for i, sp in enumerate(flags):
        hub.feed(clip[i * FRAME:(i + 1) * FRAME].tobytes(), sp, bool(robot.cur is not None and hub.output_busy()))
        target = t0 + (i + 1) * 0.06
        await asyncio.sleep(max(0.0, target - time.monotonic()))
    for _ in range(int(VERDICT_S / 0.06) + 10):  # the room goes quiet; the last segment gets its time
        hub.feed(bytes(FRAME * 2), False, False)
        await asyncio.sleep(0.06)
    await hub.shutdown()
    rows = []
    for s, e in segments(flags):
        if (e - s) * 0.06 < 0.4:
            continue
        end_t = t0 + e * 0.06
        hit = next(((t, k) for t, k in events if end_t - 1.0 <= t <= end_t + VERDICT_S and k in ("answer", "ambient")), None)
        rows.append({"dur_s": round((e - s) * 0.06, 2), "verdict": hit[1] if hit else "none",
                     "after_s": round(hit[0] - end_t, 2) if hit else None})
    n = len(rows)
    lat = sorted(r["after_s"] for r in rows if r["after_s"] is not None)
    return {"setting": name, "turn": turn, "gate": gate, "segments": n,
            "answer": sum(r["verdict"] == "answer" for r in rows), "ambient": sum(r["verdict"] == "ambient" for r in rows),
            "none": sum(r["verdict"] == "none" for r in rows),
            "covered_pct": round(100 * sum(r["verdict"] != "none" for r in rows) / n, 1) if n else None,
            "median_after_s": lat[len(lat) // 2] if lat else None,
            "speech_stopped_events": sum(1 for _, k in events if k == "stopped"),
            "cost_cny": round(hub.stats["cost_micro_cny"] / 1e6, 4), "turns": hub.stats["turns"], "rows": rows}


async def main(a):
    key = next(l.split("=", 1)[1].strip().strip("'\"") for l in Path(a.env).expanduser().read_text().splitlines()
               if l.startswith("DASHSCOPE_API_KEY="))
    voice = rl.load_voice(a.voices, a.voice_key)
    if not voice:
        raise SystemExit("voice not found")
    with wave.open(a.clip) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
        w.setpos(int(a.start * 16000))
        clip = np.frombuffer(w.readframes(int(a.dur * 16000)), "<i2")
    flags = vad_flags(clip.astype(np.float32) / 32768, a.models)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for name in a.settings.split(","):
        r = await run_one(name, clip, flags, key, voice)
        results.append(r)
        print(json.dumps({k: v for k, v in r.items() if k != "rows"}, ensure_ascii=False), flush=True)
        (out / f"{name}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1))
        if sum(x["cost_cny"] for x in results) > a.budget_cny:
            print("budget reached, stopping", flush=True)
            break
    (out / "summary.json").write_text(json.dumps([{k: v for k, v in r.items() if k != "rows"} for r in results],
                                                 ensure_ascii=False, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip", required=True)
    ap.add_argument("--start", type=float, default=0)
    ap.add_argument("--dur", type=float, default=180)
    ap.add_argument("--env", default="~/hubmix/idea/agent/.env")
    ap.add_argument("--voices", required=True)
    ap.add_argument("--voice-key", default="ly01")
    ap.add_argument("--models", default=os.path.expanduser("~/soul/spikes/sherpa-onnx/models"))
    ap.add_argument("--settings", default="smart,smart_all,server_vad")
    ap.add_argument("--budget-cny", type=float, default=5.0, help="stop starting new settings past this (≈ $0.7)")
    ap.add_argument("--out", required=True)
    asyncio.run(main(ap.parse_args()))
