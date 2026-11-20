"""soul ear server (grew out of spike v2): the robot's "ear" as a WebSocket service.

Protocol (v1):
  client -> server  binary: PCM16 little-endian, 16 kHz, mono, any chunk size (default), or one Opus
                            packet per message after {"type":"audio","format":"opus",...}
  client -> server  text  : {"type":"audio","format":"opus","sample_rate":16000,"channels":1,"frame_ms":60}
                            (same audio parameters as xiaozhi-esp32: Opus, 16 kHz mono, 60 ms frames)
                            {"type":"ping","id":n}
                            {"type":"enroll","name":"主人","count":5}
                            {"type":"config","speaker_hi":0.6,"speaker_lo":0.4,
                             "sound_min_db":15,"voice_hi":0.4,"voice_lo":0.2}
                            {"type":"self","sounding":false,"moving":false}
                            {"type":"forget","name":"..."}   (delete a voiceprint; answered by a "speakers" event)
                            {"type":"vision","event":"face_found",...}   (0.4.73 only; 0.4.74+ uses ws://.../vision, vision_link.py)
  server -> client  text  : exactly one frame every 100 ms, a "tick":
      {"v":1,"type":"tick","seq":n,"t":<server epoch ms>,
       "rx":<samples received so far>,"rx_idle_ms":..,"rx_gap_ms":..,"rx_bytes":<audio bytes so far>,
       "level_db":..,"noise_db":..,"heard_noise_db":..,"speaking":bool,"events":[...]}
    The tick is the heartbeat; events (speech / sound / enroll / pong / hello) ride on it,
    so the server never sends more than 10 frames a second.
    level_db / noise_db describe the raw microphone. For robot sources ("source":"stackchan...")
    the audio goes through a streaming GTCRN denoiser before VAD/ASR/tags (the audio ack says
    "denoise":"gtcrn"); heard_noise_db is the floor of that denoised audio, which the gates use.

The ear reports facts plus confidence bands; it never decides what they mean.
"""
import argparse
import asyncio
import bisect
import collections
import json
import math
import os
import re
import threading
import traceback
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import numpy as np
import sherpa_onnx as so
from websockets.asyncio.server import serve

import recorder
import ear_log
import hello_db
import vision_link
import body_sense
import signal_link
import tingwu as tingwu_rt
import tingwu_switch
import presence as presence_mod
import mouth_link
import mouth_mode
import realtime_link
import vision_burst
import web_search
import sound_kinds
import hearing_protocol as hp
import kws_link

import ear_hearing as _ear_hearing  # noqa: E402
# every name of ear_hearing (Models, Ring, Ear, the constants, the helpers), so ear_server reads as before
globals().update({_k: _v for _k, _v in vars(_ear_hearing).items() if not _k.startswith("__")})


def _boot(device):
    return (device.get("boot_id") or device.get("boot")) if isinstance(device, dict) else None


def link_of(path):
    """The link a request path is served as (the handler's routing: /mouth, /signal, /vision, anything else hearing).
    Only this name goes into the run log for a refused connection, never the path itself (it could carry a secret)."""
    p = urlsplit(path or "").path
    return {"/mouth": "mouth", "/signal": "signal", "/vision": "vision"}.get(p, "hearing")


async def serve_hearing(ws, peer, number, ctx):
    """One hearing connection (the Ear), with its run-log link.open / link.close: the pair is written even when the
    Ear cannot be set up. ctx: models, ex, rec_root, database, tingwu_keys, tw_switch, write_tingwu_status, mouth,
    mode_hub, rt_hub, ears (live Ears), active (live sockets; ws is in it and is removed here)."""
    conn = f"c{number}"
    print(f"+ {peer} {conn}", flush=True)
    t0 = ear_log.link_open("hearing", conn=conn, peer=peer)
    ear = ticker = None
    why = "error"                                # the Ear failed with the socket still open
    try:
        ear = Ear(ctx.models, ws, ctx.ex)
        ear.link_conn = conn
        ear.rec = recorder.Recorder(ctx.rec_root, number, f"{peer[0]}:{peer[1]}", ear.hello)
        ear.db = ctx.database
        ear.tingwu_keys = ctx.tingwu_keys
        ear.tingwu_switch, ear.on_tingwu_change = ctx.tw_switch, ctx.write_tingwu_status
        mode_hub = ctx.mode_hub
        ear.mouth_mode = ctx.mouth
        ear.chat = mode_hub
        ear.realtime = ctx.rt_hub
        ear.presence = getattr(ctx, "presence", None)
        ear.keywords = getattr(ctx, "kws", None)
        ctx.ears.add(ear)
        ticker = asyncio.create_task(ear.ticker())
        async for data in ws:
            if isinstance(data, bytes):
                await ear.on_audio(data)
            else:
                await ear.on_text(data)
        why = "closed"
    finally:
        code = getattr(ws, "close_code", None)
        if code is not None:                     # a normal end, or a dropped link (1006): its close code either way
            why = f"closed {code}"
        try:
            if ticker is not None:
                ticker.cancel()
            if ear is not None:
                await ear.close()
            if ticker is not None:
                await asyncio.gather(ticker, return_exceptions=True)
            if ear is not None and ear.rec is not None:
                ear.rec.close()
        finally:
            ctx.active.discard(ws)
            if ear is not None:
                ctx.ears.discard(ear)
            ear_log.link_close("hearing", t0, conn=conn, boot=_boot(ear.device) if ear else None, peer=peer, why=why,
                               counts={"utterances": ear.sid, "sounds": ear.aid, "rx_bytes": ear.rx_bytes,
                                       "session": ear.session} if ear is not None else None)
            if ear is not None and ear.device_profile:
                ear_log.robot_link("hearing", ear.source, conn, False)
                if ear.db is not None:          # the body's registry row follows the live links (a reconnect overlaps)
                    ear.db.body_seen(ear.body, "hearing", ear_log.live_links("hearing", ear.source), ear.device)
            if ear is not None and ear.rec is not None:
                print(f"- {peer} ({ear.sid} utterances, {ear.aid} sounds) recorded in {ear.rec.dir}", flush=True)


def _frame_hooks(burst, live_eye):
    """A stored camera frame: the burst's snap photo, or a photo for the instant eye (each ignores the other's)."""
    hooks = [h.on_frame for h in (burst, live_eye) if h is not None]
    if not hooks:
        return None

    def on_frame(head, jpeg, path, session):
        for h in hooks:
            try:
                h(head, jpeg, path, session)
            except Exception:                      # noqa: BLE001 - one consumer's bug never drops the frame for the other
                traceback.print_exc()
    return on_frame


async def serve_vision(ws, peer, number, database, rec_root, rt_hub=None, burst=None, presence=None, live_eye=None,
                       body=None):
    """One /vision connection (vision_link.py), with its run-log link.open / link.close. -> the VisionLink.
    Every M12 event goes to presence first (a face lifts an away pause here at once), then to 围观."""
    print(f"+ {peer} vision v{number}", flush=True)
    t0 = ear_log.link_open("vision", conn=f"v{number}", peer=peer)
    link = None

    def on_event(m):
        if presence is not None:
            presence.vision(number, m)
        if rt_hub is not None:
            rt_hub.on_vision(m)
    try:
        link = await vision_link.serve_link(ws, database, number, f"{peer[0]}:{peer[1]}", rec_root,
                                            on_event=on_event if rt_hub or presence is not None else None,
                                            on_frame=_frame_hooks(burst, live_eye),
                                            on_body=body.on_link if body is not None else None)
    finally:
        if presence is not None:
            presence.vision_closed(number)
        ear_log.link_close("vision", t0, conn=f"v{number}", peer=peer, boot=_boot(getattr(link, "device", None)),
                           why=getattr(link, "why", None) or "error",
                           counts={"events": link.events, "frames": link.frames} if link else None)
    print(f"- {peer} vision v{number}: {link.events} events, {link.frames} frames ({link.session})", flush=True)
    return link


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--ask-port", type=int, default=52743, help="local request API for mind's tools (0 = off)")
    ap.add_argument("--models", required=True)
    ap.add_argument("--speakers", default=None, help="speakers.npz (default: next to this file)")
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--hubmix-env", default=os.path.expanduser("~/soul-spike/hello.env"),
                    help="env file with MONGO_URI and DASHSCOPE_API_KEY / DASHSCOPE_APP_ID (server only)")
    ap.add_argument("--db", choices=["on", "off"], default="on", help="write sessions/speech/sounds/tingwu to MongoDB 'hello'")
    ap.add_argument("--tingwu", choices=["on", "off"], default="on",
                    help="听悟 realtime transcription for robot sources (billed per audio minute)")
    ap.add_argument("--denoise-mode", choices=["stream", "asr", "off"], default="stream",
                    help="robot sources only: denoise what VAD+ASR hear, only ASR segments, or nothing")
    ap.add_argument("--token-file", default=os.environ.get("SOUL_TOKEN_FILE", ""))
    ap.add_argument("--realtime", choices=["on", "off"], default="on",
                    help="Docking (was 围观) realtime voice session (qwen-audio-3.1-realtime-flash); runs only while the hello.switches "
                         "realtime switch is on (default off) and hello is in crowd")
    ap.add_argument("--realtime-voices", default=os.environ.get("SOUL_RT_VOICES", os.path.expanduser("~/soul-tts/soul_tts/voices.json")),
                    help="soul-tts voices.json that holds the cloned voice (private; the id is never logged)")
    # owner 2026-11-15 night: 围观 speaks in 鹿野 (ly01, Qwen clone; fillers in ~/soul-spike/fillers/ly01); was mm01, before that yy01
    ap.add_argument("--realtime-voice", default=os.environ.get("SOUL_RT_VOICE", "ly01"), help="soul-tts voice key")
    ap.add_argument("--realtime-silence-ms", type=int, default=realtime_link.Config.silence_ms,
                    help="server VAD silence that ends a turn (crowd: longer than DashScope's 800 ms)")
    ap.add_argument("--realtime-vad-threshold", type=float, default=realtime_link.Config.vad_threshold)
    ap.add_argument("--recordings", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "recordings"),
                    help="where every connection is recorded (default: recordings/ next to this file); never pruned")
    a = ap.parse_args()
    token_value = open(a.token_file).read().strip() if a.token_file else ""
    db_path = a.speakers or os.path.join(os.path.dirname(os.path.abspath(__file__)), "speakers.npz")
    rec_root = os.path.abspath(a.recordings)
    os.makedirs(rec_root, exist_ok=True)
    st = os.statvfs(rec_root)
    print(f"recording to {rec_root} ({st.f_bavail * st.f_frsize / 1e9:.0f} GB free)", flush=True)
    conn_no = [0]

    t0 = time.monotonic()
    models = Models(a.models, db_path, a.threads, a.denoise_mode)
    secrets = tingwu_rt.load_env(a.hubmix_env)
    database = None
    if a.db == "on" and secrets.get("MONGO_URI"):
        try:
            import pymongo  # noqa: F401
            database = hello_db.HelloDB(secrets["MONGO_URI"])
            database.bodies_reset()             # no body is linked to a new ear yet (bodies registry)
        except ImportError:
            print("hello db off: pymongo not installed", flush=True)
    tingwu_keys = (secrets["DASHSCOPE_API_KEY"], secrets["DASHSCOPE_APP_ID"]) \
        if a.tingwu == "on" and secrets.get("DASHSCOPE_API_KEY") and secrets.get("DASHSCOPE_APP_ID") else None
    print(f"hello db: {'on' if database else 'off'}; tingwu realtime: {'on' if tingwu_keys else 'off'}", flush=True)
    print(f"models loaded in {time.monotonic() - t0:.1f}s; speakers: {sorted(models.speakers)}", flush=True)
    ex = ThreadPoolExecutor(max_workers=1)

    active = set()
    ears = set()                     # live Ear objects (for the 听悟 status document)
    vision_active = set()
    mouth_active = set()
    mouth_no = [0]                                   # /mouth connection number (conn)
    tw_switch = tingwu_switch.SwitchReader((lambda: database.read_switch("tingwu")) if database else (lambda: None))
    # The pause (2026-11-17, mind/pause.py; presence.py): the ear reports who is there and obeys mind's pause in its
    # paid paths (听悟, 围观, 视觉爆发) without touching the owner's switches.
    presence = presence_mod.Presence()
    pause = presence_mod.Pause((lambda: database.read_switch("pause")) if database else (lambda: None), presence)
    presence_writer = presence_mod.Writer(presence, lambda d: database.switch_status("presence", d)) if database else None
    tw_paid = presence_mod.Gated(tw_switch, pause)
    mouth = mouth_mode.ModeReader((lambda: database.read_switch("mouth")) if database else (lambda: None))

    def write_tingwu_status():
        if database is not None:
            database.switch_status("tingwu", {"running": sorted(e.session or "?" for e in ears if e.tingwu is not None),
                                              "available": tingwu_keys is not None, "on": tw_switch.on,
                                              "paused": tw_paid.paused, "at": int(time.time() * 1000)})

    async def watch_tingwu_switch():
        """Read the server switch every 5 s (off the model thread); status on change and every 30 s."""
        last, n, held = None, 0, []
        while True:
            await asyncio.to_thread(tw_switch.update)
            await asyncio.to_thread(rt_switch.update)
            await asyncio.to_thread(eye_switch.update)
            await asyncio.to_thread(tools_switch.update)
            if rt_hub is not None and database is not None:   # 全答 (realtime_answer_all); no document = off
                try:
                    doc, read_ok = await asyncio.to_thread(database.read_switch, "realtime_answer_all"), True
                except Exception:                      # noqa: BLE001 - a failed read keeps the current setting
                    doc, read_ok = None, False
                if read_ok:                            # no document = on (owner 2026-11-19 night: 暂时退回全手动)
                    rt_hub.set_answer_all(doc["on"] if doc and isinstance(doc.get("on"), bool) else True)
                try:                                   # 智能断句 (realtime_smart_turn); no document = cfg default
                    doc = await asyncio.to_thread(database.read_switch, "realtime_smart_turn")
                except Exception:                      # noqa: BLE001 - a failed read keeps the current setting
                    doc = None
                if doc and isinstance(doc.get("on"), bool):
                    rt_hub.set_turn(doc["on"])
                try:                                   # 工具分类器 (realtime_intent); no document = on
                    doc = await asyncio.to_thread(database.read_switch, "realtime_intent")
                except Exception:                      # noqa: BLE001
                    doc = None
                if doc and isinstance(doc.get("on"), bool):
                    rt_hub.set_intent(doc["on"])
            await asyncio.to_thread(pause.update)
            if pause.reasons() != held:
                held = pause.reasons()
                print(f"pause: {'/'.join(held) if held else 'off'}", flush=True)
                write_tingwu_status()
            if presence_writer is not None:
                presence_writer.maybe()
            before = mouth.mode
            await asyncio.to_thread(mouth.update)
            if mouth.mode != before:
                print(f"mouth mode: {mouth.mode}", flush=True)
            if tw_switch.on != last:
                print(f"tingwu switch: {'on' if tw_switch.on else 'off'}", flush=True)
            if tw_switch.on != last or n % 6 == 0:
                write_tingwu_status()
            last, n = tw_switch.on, n + 1
            await asyncio.sleep(5)
    vision_no = [0]
    signal_active = set()
    signal_post = signal_link.mind_poster(token_value)
    # hello's mode reports -> mind (step 1b); place frames for the robot's info page every 5 s (2026-11-15)
    mode_hub = mouth_link.ModeRelay(mouth_link.mind_client(token_value), place_poll_s=5.0)
    rt_switch = tingwu_switch.SwitchReader((lambda: database.read_switch("realtime")) if database else (lambda: None))
    rt_paid = presence_mod.Gated(rt_switch, pause)        # 围观 and 视觉爆发 see the switch through the pause
    rt_hub = None
    if a.realtime == "on":
        rt_voice = os.environ.get("SOUL_RT_VOICE_ID") or realtime_link.load_voice(a.realtime_voices, a.realtime_voice)
        if secrets.get("DASHSCOPE_API_KEY") and rt_voice:
            cfg = realtime_link.Config()
            cfg.silence_ms, cfg.vad_threshold = a.realtime_silence_ms, a.realtime_vad_threshold
            fillers = realtime_link.load_fillers(os.path.expanduser(f"~/soul-spike/fillers/{a.realtime_voice}"))
            rt_hub = realtime_link.RealtimeHub(key=secrets["DASHSCOPE_API_KEY"], voice=rt_voice, modes=mode_hub,
                                               call=mouth_link.mind_client(token_value), switch=rt_paid, db=database,
                                               cfg=cfg, fillers=fillers)
            print(f"realtime (Docking): {len(fillers)} look fillers", flush=True)
            # 联网搜索 (owner 2026-11-16): search words make the ear search itself (web_search.py), "我查查哈" meanwhile
            rt_hub.searcher = web_search.dashscope_search(secrets["DASHSCOPE_API_KEY"])
            rt_hub.search_fillers = realtime_link.load_fillers(
                os.path.expanduser(f"~/soul-spike/fillers/{a.realtime_voice}-search"))
            print(f"realtime (Docking): web search on search words, {len(rt_hub.search_fillers)} search fillers", flush=True)
            rt_hub.filler_base = os.path.expanduser("~/soul-spike/fillers")      # tool fillers by situation (11-19)
            rt_hub.tool_fillers = realtime_link.load_tool_fillers(rt_hub.filler_base, a.realtime_voice)
            print(f"realtime (Docking): tool fillers {({g: len(c) for g, c in rt_hub.tool_fillers.items()})}", flush=True)
    print(f"realtime (Docking): {'ready, follows the switch' if rt_hub else 'off (no key, no voice or --realtime off)'}", flush=True)
    # 身体感觉 (collector-0.5.12, owner 2026-11-19): the robot's IMU / proximity / light summary and events, on /vision
    body = body_sense.BodySense(db=database, hub=rt_hub, recordings_root=rec_root)
    if rt_hub is not None:
        rt_hub.body = body
    burst = None                                   # 视觉爆发 (2026-11-15): "注意看" in crowd, only with the realtime hub
    if rt_hub is not None:
        burst = vision_burst.VisionBurst(rt_hub, vision_burst.dashscope_look(secrets["DASHSCOPE_API_KEY"]), db=database)
        rt_hub.burst = burst

    # 即时画面 (owner 2026-11-18 "改成上传即刻理解"): every uploaded camera photo recognised at once, under 识别 (eye)
    eye_switch = tingwu_switch.SwitchReader((lambda: database.read_switch("eye")) if database else (lambda: None))
    # 千问用工具 (L6 2026-11-18): Qwen calls hello's tools in Docking; read at each session open (default off)
    tools_switch = tingwu_switch.SwitchReader((lambda: database.read_switch("realtime_tools")) if database else (lambda: None))
    if rt_hub is not None:
        rt_hub.tools_switch = tools_switch
        # all = every live tool in the session; skills = only open_skill first (owner 2026-11-18), read at session open
        rt_hub.tools_mode_fn = lambda: ((database.read_switch("realtime_tools") or {}).get("mode") or "all") \
            if database else "all"
        try:                                       # 自动档 (2026-11-18): the local classifier, if its model is there
            import intent
            rt_hub.intent = intent.Auto(intent.Embedder())
            print(f"realtime (Docking): auto head {'ready ' + str(rt_hub.intent.version) if rt_hub.intent.ready else 'not trained yet'}", flush=True)
        except Exception as e:                     # noqa: BLE001 - no onnxruntime / no model: auto = skills
            print(f"realtime (Docking): auto head off ({type(e).__name__})", flush=True)
    live_eye = None
    if secrets.get("DASHSCOPE_API_KEY"):
        live_eye = vision_burst.LiveEye(vision_burst.dashscope_look(secrets["DASHSCOPE_API_KEY"]),
                                        presence_mod.Gated(eye_switch, pause), hub=rt_hub, db=database)
    print(f"live eye: {'follows the 识别 switch' if live_eye else 'off (no key)'}", flush=True)

    kws = None                                     # 关键词耳朵 (2026-11-17, server/kws): SOUL_KWS_URL or kws.url
    kws_url = kws_link.configured_url(os.environ, os.path.join(os.path.dirname(os.path.abspath(__file__)), "kws.url"))
    if kws_url:
        kws = kws_link.KeywordLink(kws_url, emit=ear_log.emit)
        if rt_hub is not None:             # 2026-11-18: the name heard = hello was called (围观)
            kws.on_hit = lambda out: rt_hub.called("name")
    print(f"keyword ear: {kws.url if kws else 'off (no SOUL_KWS_URL, no kws.url)'}", flush=True)

    import ask_api                                 # mind's tools ask the ear (L1 2026-11-18), 127.0.0.1 only
    robots = mouth_link.RobotLinks()               # the robot command line (collector-0.5.10): newest /mouth link
    if rt_hub is not None:                         # 安静中 / 少插话 under the eyes while the mode lasts
        def _mode_word(text, hold_s):
            async def send():
                try:
                    await robots.send_cmd("word", {"text": text, "hold_ms": min(max(hold_s, 0), 3600) * 1000}, 5.0)
                except Exception:                  # noqa: BLE001 - old firmware / no link: no word
                    pass
            asyncio.get_running_loop().create_task(send())
        rt_hub.screen_word = _mode_word
        if database is not None:                   # 安静 / 少插话 / 角色 of the last run, still running (2026-11-19)
            try:
                kept = rt_hub.restore_modes(database.read_switch("rt_modes_status"))
                print(f"realtime (Docking): modes restored {kept}" if kept else "realtime (Docking): no modes to restore",
                      flush=True)
            except Exception as e:                 # noqa: BLE001 - a restart without them, as before
                print(f"realtime (Docking): modes not restored ({type(e).__name__})", flush=True)
    asks = ask_api.Asks(hub=rt_hub, burst=burst, presence=presence, robot=robots,
                        searcher=rt_hub.searcher if rt_hub is not None and getattr(rt_hub, "searcher", None) else
                        (web_search.dashscope_search(secrets["DASHSCOPE_API_KEY"]) if secrets.get("DASHSCOPE_API_KEY") else None),
                        paused=pause.reasons,
                        voices=ask_api.VoiceBook(os.path.expanduser("~/soul-spike/personas"), a.realtime_voices,
                                                 os.path.expanduser("~/soul-spike/fillers")))

    hearing_ctx = argparse.Namespace(models=models, ex=ex, rec_root=rec_root, database=database,
                                     tingwu_keys=tingwu_keys, tw_switch=tw_paid,
                                     write_tingwu_status=write_tingwu_status, mouth=mouth, mode_hub=mode_hub,
                                     rt_hub=rt_hub, ears=ears, active=active, presence=presence, kws=kws)

    async def handler(ws):
        path = ws.request.path if getattr(ws, "request", None) else ""
        header = ws.request.headers.get("Authorization", "") if getattr(ws, "request", None) else ""
        if not hp.authenticate(path, header, token_value):
            ear_log.link_refused(link_of(path), peer=ws.remote_address, why="bad_token")
            await ws.close(code=4401, reason="bad token")
            return
        if urlsplit(path).path == "/mouth":            # speaker downlink: ear ends it, soul-tts behind (2026-11-14)
            if len(mouth_active) >= 2:
                ear_log.link_open("mouth", peer=ws.remote_address, ok=False, why="busy")
                await ws.close(code=1013, reason="server busy")
                return
            mouth_active.add(ws)
            mouth_no[0] += 1
            peer = ws.remote_address
            print(f"+ {peer} mouth m{mouth_no[0]}", flush=True)
            t0 = ear_log.link_open("mouth", conn=f"m{mouth_no[0]}", peer=peer)
            link = mouth_link.MouthLink(ws, upstream_url=mouth_link.UPSTREAM, token=token_value, conn=mouth_no[0],
                                        modes=mode_hub, realtime=rt_hub, burst=burst, presence=presence,
                                        robots=robots)
            why = "error"
            try:
                why = await link.run()
            finally:
                mouth_active.discard(ws)
                ear_log.link_close("mouth", t0, conn=f"m{link.conn}", boot=link.boot, peer=peer, why=why,
                                   counts={"upstream_drops": link.upstream_drops})
            print(f"- {peer} mouth m{link.conn} boot={link.boot}: {why}, {link.upstream_drops} soul-tts drops", flush=True)
            return
        if urlsplit(path).path == "/signal":           # DualKey owner signals, relayed to mind (2026-11-13)
            if len(signal_active) >= 4:
                ear_log.link_open("signal", peer=ws.remote_address, ok=False, why="busy")
                await ws.close(code=1013, reason="server busy")
                return
            signal_active.add(ws)
            peer = ws.remote_address
            print(f"+ {peer} signal", flush=True)
            t0 = ear_log.link_open("signal", peer=peer)
            link = None
            try:
                link = await signal_link.serve_link(ws, signal_post, f"{peer[0]}:{peer[1]}")
            finally:
                signal_active.discard(ws)
                ear_log.link_close("signal", t0, conn=getattr(link, "device", None), boot=getattr(link, "boot", None),
                                   peer=peer, why="closed" if link is not None else "error",
                                   counts={"signals": link.signals} if link is not None else None)
            print(f"- {peer} signal {link.device}: {link.signals} signals", flush=True)
            return
        if urlsplit(path).path == "/vision":           # vision uplink: its own connection (collector-0.4.74+)
            if len(vision_active) >= 4:
                ear_log.link_open("vision", peer=ws.remote_address, ok=False, why="busy")
                await ws.close(code=1013, reason="server busy")
                return
            vision_active.add(ws)
            vision_no[0] += 1
            try:
                await serve_vision(ws, ws.remote_address, vision_no[0], database, rec_root, rt_hub, burst, presence,
                                   live_eye, body)
            finally:
                vision_active.discard(ws)
            return
        if len(active) >= 4:
            ear_log.link_open("hearing", peer=ws.remote_address, ok=False, why="busy")
            await ws.close(code=1013, reason="server busy")
            return
        active.add(ws)
        conn_no[0] += 1
        await serve_hearing(ws, ws.remote_address, conn_no[0], hearing_ctx)

    def beat_state():
        """The run log's svc.beat state (beat thread; a race with the loop is caught and recorded by hello_log)."""
        return {"tingwu": tw_switch.on, "tingwu_running": sum(1 for e in list(ears) if e.tingwu is not None),
                "realtime": rt_switch.on, "realtime_state": rt_hub.state if rt_hub is not None else None,
                "paused": pause.reasons(),
                "mouth": mouth.mode, "robot_mode": mode_hub.robot_mode,
                "burst": burst.running() if burst is not None else None,
                "live_eye": live_eye.status() if live_eye is not None else None,
                "kws": kws.status() if kws is not None else None,
                "links": {"hearing": len(active), "vision": len(vision_active), "mouth": len(mouth_active),
                          "signal": len(signal_active)}}

    async def serve_forever():
        # SIGTERM on this loop first, then the run log (rulings R5 / R12: hello_log must not take the ear's SIGTERM)
        stopper = ear_log.Stopper(asyncio.get_running_loop())
        ear_log.start(secrets, a)
        ear_log.watch(beat_state)
        async with serve(handler, a.host, a.port, max_size=65536):
            print(f"listening on {a.host}:{a.port}", flush=True)
            watcher = asyncio.create_task(watch_tingwu_switch())
            ask_server = None
            if a.ask_port:
                try:
                    ask_server = await ask_api.serve(asks, token_value, "127.0.0.1", a.ask_port)
                    print(f"ask api: 127.0.0.1:{a.ask_port}", flush=True)
                except OSError as e:
                    print(f"ask api: off ({e})", flush=True)
            if rt_hub is not None:
                rt_hub.start()

                async def kept_voice():            # set_voice's choice survives a restart (switches rt_voice)
                    try:
                        doc = await asyncio.to_thread(database.read_switch, "rt_voice") if database else None
                        if doc and doc.get("voice") and doc["voice"] != a.realtime_voice:
                            out = await asks.set_voice({"voice": doc["voice"], "style": doc.get("style")})
                            print(f"realtime (Docking): kept voice {out['voice']}", flush=True)
                    except Exception as e:         # noqa: BLE001 - the default voice stays
                        print(f"realtime (Docking): kept voice not applied ({type(e).__name__})", flush=True)
                asyncio.create_task(kept_voice())
            try:
                await stopper.wait()
            finally:
                watcher.cancel()
                if ask_server is not None:
                    ask_server.close()
                if burst is not None:
                    await burst.shutdown()
                if live_eye is not None:
                    await live_eye.shutdown()
                if rt_hub is not None:
                    await rt_hub.shutdown()
                mode_hub.close()
        await stopper.finish("sigterm")

    asyncio.run(serve_forever())


if __name__ == "__main__":
    main()
