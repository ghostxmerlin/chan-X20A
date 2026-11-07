"""Mac test client for the soul ear: stream the Mac mic (or a WAV) to the ear server, keep a local
recording, measure latency from the 100 ms heartbeat, and show it all on a local web page.

  server/ear/mac-client.sh                         # live mic, opens http://127.0.0.1:8770
  server/ear/mac-client.sh --enroll 主人            # enroll (or re-enroll) a voice, then keep listening
  server/ear/mac-client.sh --file some.wav --stay  # stream a 16 kHz wav at real-time pace
  server/ear/mac-client.sh --verbose               # detailed lines in the terminal too

mac-client.sh loads the repo's .env (server URL, token, recordings dir) and runs this file.

Audio goes up as Opus, 16 kHz mono, 60 ms frames, with StackChan's (xiaozhi-esp32) encoder settings;
--codec pcm sends raw PCM16 instead.

Every run writes $SOUL_RECORDINGS_DIR/mac/<time>-<source>/: sent.wav (what the server hears: the
decoded Opus), mic.wav (the raw mic, Opus only), ticks.jsonl, events.jsonl. The server keeps its own
copy of every connection (see recorder.py). Nothing is ever deleted automatically.
"""
import argparse
import asyncio
import bisect
import collections
import io
import json
import os
import re
import signal
import statistics
import time
import urllib.parse
import wave
import webbrowser
from datetime import datetime

import numpy as np
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Response

SR = 16000
OPUS_FRAME = SR * 60 // 1000      # 60 ms, as xiaozhi-esp32 sends
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
# never pruned (user, 2026-11-07); set SOUL_RECORDINGS_DIR in .env, the fallback is git-ignored
RECORDINGS = os.path.join(os.environ.get("SOUL_RECORDINGS_DIR") or os.path.join(REPO, ".private", "recordings"),
                          "mac")
EMO_ZH = {"happy": "开心", "sad": "难过", "angry": "生气", "neutral": "平静", "fearful": "害怕",
          "disgusted": "厌恶", "surprised": "惊讶", "unknown": "未知"}


def who(sp, voice=None):
    if voice and voice.get("band") == "none":
        return "不像人声"
    if not sp or sp.get("band") == "none":
        return "未登记"
    if sp["band"] == "known":
        return sp["name"]
    if sp["band"] == "unsure":
        return f"{sp['name']}?"
    return "陌生人"


def hhmmss(epoch_ms):
    return datetime.fromtimestamp(epoch_ms / 1000).strftime("%H:%M:%S")


class Session:
    """Local recording + capture-time index, so any server sample position maps back to a moment."""

    def __init__(self, source, keep_raw=False):
        self.dir = os.path.join(RECORDINGS, f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{source}")
        os.makedirs(self.dir, exist_ok=True)
        self.wav = self.open_wav("sent.wav")
        # with Opus, sent.wav is what the server decodes; mic.wav keeps the raw mic for comparison
        self.raw = self.open_wav("mic.wav") if keep_raw else None
        self.pcm = bytearray()
        self.cap_end, self.cap_t = [], []
        self.sent = 0
        self.mono0, self.epoch0 = time.monotonic(), time.time()
        self.ticks_f = open(os.path.join(self.dir, "ticks.jsonl"), "w", encoding="utf-8")
        self.events_f = open(os.path.join(self.dir, "events.jsonl"), "w", encoding="utf-8")

    def open_wav(self, name):
        w = wave.open(os.path.join(self.dir, name), "wb")
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        return w

    def epoch_ms(self, mono):
        return int((self.epoch0 + mono - self.mono0) * 1000)

    def add(self, pcm16, t_cap, raw=None):
        b = pcm16.tobytes()
        self.pcm += b
        self.wav.writeframes(b)
        if self.raw is not None and raw is not None:
            self.raw.writeframes(raw.tobytes())
        self.sent += len(pcm16)
        self.cap_end.append(self.sent)
        self.cap_t.append(t_cap)

    def cap_time(self, sample):
        """Monotonic time at which absolute sample index `sample` was captured."""
        if sample is None:
            return None
        i = bisect.bisect_left(self.cap_end, sample)
        if i >= len(self.cap_end):
            return None
        return self.cap_t[i] - (self.cap_end[i] - sample) / SR

    def slice_wav(self, a, b):
        a, b = max(0, a), min(b, self.sent)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(bytes(self.pcm[a * 2:b * 2]))
        return buf.getvalue()

    def close(self):
        for f in (self.wav, self.raw, self.ticks_f, self.events_f):
            if f is None:
                continue
            try:
                f.close()
            except Exception:
                pass


class Client:
    def __init__(self, a):
        self.a = a
        name = os.path.splitext(os.path.basename(a.file))[0] if a.file else ""
        self.source = "mac-file-" + re.sub(r"[^0-9A-Za-z._-]+", "_", name)[:30] if a.file else "mac-mic"
        self.s = Session(self.source, keep_raw=a.codec == "opus")
        self.enc = self.dec = None
        if a.codec == "opus":
            # the same encoder settings as xiaozhi-esp32 v2.5.2 (StackChan's firmware), audio_service.h
            import opuslib_next as op
            self.enc = op.Encoder(SR, 1, op.APPLICATION_AUDIO)
            self.enc.complexity, self.enc.vbr, self.enc.dtx, self.enc.inband_fec = 0, 1, 1, 0
            self.enc.bitrate = -1000                      # OPUS_AUTO
            self.dec = op.Decoder(SR, 1)                  # to record exactly what the server will hear
        self.frames = OPUS_FRAME if self.enc else int(SR * a.chunk_ms / 1000)
        self.sent_bytes = 0
        self.server_ws = None
        self.browsers = set()
        self.ticks = collections.deque(maxlen=600)       # last 60 s, for the page
        self.events = collections.deque(maxlen=500)
        self.hello = {}
        self.last_tick = None
        self.gaps = 0
        self.pings = {}
        self.ping_id = 0
        self.rtts = collections.deque(maxlen=100)
        self.e2e = []
        self.novoice = 0
        self.done = asyncio.Event()

    # ---------- audio out ----------
    async def send(self, pcm16, t_cap):
        if self.enc is None:
            data = pcm16.tobytes()
            await self.server_ws.send(data)
            self.s.add(pcm16, t_cap)
        else:
            if len(pcm16) < OPUS_FRAME:                   # last piece of a file
                pcm16 = np.concatenate([pcm16, np.zeros(OPUS_FRAME - len(pcm16), dtype=np.int16)])
            data = self.enc.encode(pcm16.tobytes(), OPUS_FRAME)
            await self.server_ws.send(data)
            heard = np.frombuffer(self.dec.decode(data, OPUS_FRAME), dtype=np.int16)
            self.s.add(heard, t_cap, raw=pcm16)
        self.sent_bytes += len(data)

    async def mic(self):
        import sounddevice as sd
        frames = self.frames
        loop = asyncio.get_running_loop()
        with sd.InputStream(samplerate=SR, channels=1, dtype="int16", blocksize=frames) as m:
            print("麦克风已打开，开始说话（Ctrl+C 退出）", flush=True)
            while not self.done.is_set():
                x, _ = await loop.run_in_executor(None, m.read, frames)
                await self.send(x[:, 0].copy(), time.monotonic())

    async def file(self):
        with wave.open(self.a.file) as w:
            assert w.getsampwidth() == 2 and w.getframerate() == SR, "need 16 kHz PCM16 wav"
            x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, w.getnchannels())[:, 0]
        frames = self.frames
        x = np.concatenate([x, np.zeros(int(2.0 * SR), dtype=np.int16)])     # trailing silence ends speech
        print(f"按真实速度发送 {os.path.basename(self.a.file)}（{len(x) / SR:.1f}s，含 2 s 静音）", flush=True)
        t_next = time.monotonic()
        for i in range(0, len(x), frames):
            t_next += frames / SR
            await asyncio.sleep(max(0.0, t_next - time.monotonic()))
            await self.send(x[i:i + frames].copy(), time.monotonic())
        # keep sending silence afterwards, like a live mic would, so the latency numbers stay honest
        if self.a.stay:
            print("文件发完。网页仍可查看，Ctrl+C 退出。", flush=True)
        t_end = time.monotonic() + 2.0
        while not self.done.is_set() and (self.a.stay or time.monotonic() < t_end):
            t_next += frames / SR
            await asyncio.sleep(max(0.0, t_next - time.monotonic()))
            await self.send(np.zeros(frames, dtype=np.int16), time.monotonic())
        self.done.set()

    async def pinger(self):
        while not self.done.is_set():
            self.ping_id += 1
            self.pings[self.ping_id] = time.monotonic()
            await self.server_ws.send(json.dumps({"type": "ping", "id": self.ping_id}))
            await asyncio.sleep(2)

    # ---------- heartbeat in ----------
    async def receiver(self):
        async for raw in self.server_ws:
            now = time.monotonic()
            m = json.loads(raw)
            if m.get("type") != "tick":
                continue
            interval = round((now - self.last_tick) * 1000) if self.last_tick else None
            self.last_tick = now
            if interval and interval > 300:
                self.gaps += 1
            ct = self.s.cap_time(m["rx"])
            tick = {"seq": m["seq"], "recv": self.s.epoch_ms(now), "server_t": m["t"], "rx": m["rx"],
                    "interval_ms": interval, "lag_ms": round((now - ct) * 1000) if ct else None,
                    "rx_idle_ms": m.get("rx_idle_ms"), "rx_gap_ms": m.get("rx_gap_ms"),
                    "rx_bytes": m.get("rx_bytes"),
                    "level_db": m.get("level_db"), "noise_db": m.get("noise_db"), "speaking": m.get("speaking"),
                    "n_events": len(m["events"])}
            self.s.ticks_f.write(json.dumps(tick) + "\n")
            self.ticks.append(tick)
            await self.broadcast({"k": "tick", "tick": tick})
            for ev in m["events"]:
                await self.on_event(ev, now)

    async def on_event(self, ev, now):
        t = ev.get("type")
        ev["_recv"] = self.s.epoch_ms(now)
        pos = ev.get("pos") or [None, None]
        c0, c1 = self.s.cap_time(pos[0]), self.s.cap_time(pos[1])
        if c0:
            ev["_cap_start"] = self.s.epoch_ms(c0)
        if c1:
            ev["_cap_end"] = self.s.epoch_ms(c1)
        if t == "pong":
            sent = self.pings.pop(ev.get("id"), None)
            if sent:
                self.rtts.append(round((now - sent) * 1000 - ev.get("held_ms", 0)))
                await self.broadcast({"k": "rtt", "rtt": self.rtts[-1]})
            return
        if t == "hello":
            self.hello = ev
            print(f"已连接。已登记：{', '.join(ev.get('speakers') or []) or '（无）'}", flush=True)
            if self.a.enroll:
                await self.enroll(self.a.enroll, self.a.count)
        if t == "speech" and ev["phase"] == "end" and c1:
            d = ev.get("debug", {})
            e2e = round((now - c1) * 1000)
            ev["_lat"] = {"e2e": e2e, "vad_wait": d.get("vad_wait_ms"), "server": d.get("server_ms"),
                          "tick_wait": d.get("tick_wait_ms"),
                          "net": e2e - d.get("vad_wait_ms", 0) - d.get("server_ms", 0) - d.get("tick_wait_ms", 0)}
            self.e2e.append(e2e)
            self.novoice += ev.get("voice", {}).get("band") == "none"
        if t == "speech" and ev["phase"] == "start" and c0:
            ev["_since_onset_ms"] = round((now - c0) * 1000)
        if t == "enroll" and ev.get("phase") == "done":
            self.hello["speakers"] = ev.get("speakers")
        self.s.events_f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        self.s.events_f.flush()
        self.events.append(ev)
        self.print_event(ev)
        await self.broadcast({"k": "event", "event": ev})

    def print_event(self, ev):
        t, ph, v = ev.get("type"), ev.get("phase"), self.a.verbose
        if t == "speech" and ph == "end":
            voice = ev.get("voice", {})
            if voice.get("band") == "none" and not v:
                return                                   # not a voice: only shown with -v
            text = ev["text"] or "（无文字）"
            look = ""
            kinds = ev.get("tags", {}).get("kinds") or []
            if kinds and voice.get("band") != "voice":
                look = f"  像{kinds[0][2]}"
            if voice.get("band") == "unclear":
                look += "  人声?"
            print(f"{hhmmss(ev['t'])}  {who(ev['speaker'], voice):<6} 「{text}」 {ev['dur']:.1f}s "
                  f"{EMO_ZH.get(ev['emotion'], ev['emotion'])}{look}", flush=True)
            if v:
                sp, la, vo = ev["speaker"], ev.get("_lat", {}), ev.get("voice", {})
                ru = sp.get("runner_up")
                print(f"          相似度 {sp.get('score')}" + (f"（第二像 {ru['name']} {ru['score']}）" if ru else "")
                      + f"｜像人声 {vo.get('p')}（{vo.get('band')}）"
                      + f"｜{ev['lang']}｜高出底噪 {vo.get('loud_db')} dB｜{vo.get('rate')} 字/秒"
                      + f"｜说完到结果 {la.get('e2e')} ms = 等静音 {la.get('vad_wait')} + 服务器 {la.get('server')}"
                      + f" + 等心跳 {la.get('tick_wait')} + 网络 {la.get('net')}", flush=True)
        elif t == "speech" and ph == "start" and v:
            vo = ev.get("voice", {})
            print(f"          …有声音开始：像 {who(ev['speaker'])} {ev['speaker'].get('score')}，"
                  f"像人声 {vo.get('p')}（{vo.get('band')}）（开口后 {ev.get('_since_onset_ms')} ms）", flush=True)
        elif t == "sound" and ph == "end":
            print(f"{hhmmss(ev['t'])}  [声音] {ev['label']} {ev['dur']:.1f}s", flush=True)
        elif t == "enroll":
            if ph == "started":
                print(f"登记 {ev['name']}：用平常的语气说 {ev['count']} 句话，每句至少 1 秒，句间停一下。", flush=True)
            elif ph == "progress":
                print(f"  收到第 {ev['n']}/{ev['count']} 句", flush=True)
            elif ph == "done":
                print(f"已保存。现在登记了：{', '.join(ev['speakers'])}", flush=True)
        elif t == "error":
            print("服务器错误:", ev.get("error"), flush=True)

    async def enroll(self, name, count):
        await self.server_ws.send(json.dumps({"type": "enroll", "name": name, "count": count}))

    # ---------- local web page ----------
    async def broadcast(self, msg):
        if not self.browsers:
            return
        data = json.dumps(msg, ensure_ascii=False)
        for b in list(self.browsers):
            try:
                await b.send(data)
            except Exception:
                self.browsers.discard(b)

    async def browser(self, ws):
        self.browsers.add(ws)
        try:
            await ws.send(json.dumps({"k": "init", "server": self.a.url, "session": self.s.dir,
                                      "hello": self.hello, "ticks": list(self.ticks), "events": list(self.events),
                                      "rtts": list(self.rtts)}, ensure_ascii=False))
            async for raw in ws:
                m = json.loads(raw)
                if m.get("cmd") == "enroll" and self.server_ws:
                    await self.enroll(str(m.get("name") or "主人"), int(m.get("count") or 5))
        finally:
            self.browsers.discard(ws)

    def http(self, conn, req):
        path, _, query = req.path.partition("?")
        if path == "/live":
            return None
        if path == "/":
            body = open(os.path.join(HERE, "debug.html"), "rb").read()
            return self.resp(200, body, "text/html; charset=utf-8")
        if path == "/audio":
            q = urllib.parse.parse_qs(query)
            return self.resp(200, self.s.slice_wav(int(q["a"][0]), int(q["b"][0])), "audio/wav")
        return self.resp(404, b"not found", "text/plain")

    @staticmethod
    def resp(code, body, ctype):
        return Response(code, "OK" if code == 200 else "Not Found",
                        Headers([("Content-Type", ctype), ("Content-Length", str(len(body))),
                                 ("Cache-Control", "no-store")]), body)

    # ---------- run ----------
    async def run(self):
        web = None
        if not self.a.no_web:
            web = await serve(self.browser, "127.0.0.1", self.a.web_port, process_request=self.http)
            url = f"http://127.0.0.1:{self.a.web_port}/"
            print(f"调试网页：{url}", flush=True)
            if not self.a.no_open:
                webbrowser.open(url)
        tok = os.environ.get("SOUL_EAR_TOKEN", "").strip()
        if self.a.token_file:
            tok = open(self.a.token_file).read().strip()
        url = self.a.url + (("&" if "?" in self.a.url else "?") + "token=" + tok if tok else "")
        # proxy=None: ignore the Mac's system proxy; the robot connects directly
        async with connect(url, max_size=2 ** 20, proxy=None) as ws:
            self.server_ws = ws
            # always declare the audio, so the server knows the format and records under our name
            await ws.send(json.dumps({"type": "audio", "format": "opus" if self.enc else "pcm16", "sample_rate": SR,
                                      "channels": 1, "frame_ms": self.frames * 1000 // SR, "source": self.source,
                                      "local_recording": os.path.basename(self.s.dir)}))
            tasks = [asyncio.create_task(self.receiver()), asyncio.create_task(self.pinger()),
                     asyncio.create_task(self.file() if self.a.file else self.mic())]
            try:
                await self.done.wait()
            finally:
                for t in tasks:
                    t.cancel()
                if web:
                    web.close()

    def summary(self):
        self.s.close()
        lags = [t["lag_ms"] for t in self.ticks if t["lag_ms"] is not None]
        ivs = [t["interval_ms"] for t in self.ticks if t["interval_ms"]]
        print("\n—— 小结 ——")
        if self.e2e:
            hidden = f"（其中 {self.novoice} 句不像人声，已隐藏，加 -v 可看）" if self.novoice else ""
            print(f"说话 {len(self.e2e)} 句{hidden}，说完到结果中位数 {statistics.median(self.e2e):.0f} ms")
        if ivs:
            ivs.sort()
            print(f"心跳间隔中位数 {statistics.median(ivs):.0f} ms，95% {ivs[int(len(ivs) * .95) - 1]} ms，"
                  f"中断（>300 ms）{self.gaps} 次")
        if lags:
            print(f"回执延迟中位数 {statistics.median(lags):.0f} ms，最大 {max(lags)} ms")
        if self.rtts:
            print(f"RTT 中位数 {statistics.median(self.rtts):.0f} ms")
        if self.s.sent:
            kbps = self.sent_bytes * 8 / (self.s.sent / SR) / 1000
            how = "Opus 60 ms 一帧" if self.enc else "原始 PCM"
            print(f"上行 {how}，平均 {kbps:.1f} kbps（原始音频 256 kbps），共 {self.sent_bytes / 1e6:.2f} MB")
        print(f"本地记录：{self.s.dir}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("SOUL_EAR_URL") or "ws://127.0.0.1:8765",
                    help="ear server (default: SOUL_EAR_URL from .env, else a local server)")
    ap.add_argument("--token-file", help="read the token from this file instead of SOUL_EAR_TOKEN")
    ap.add_argument("--file", help="16 kHz PCM16 wav to stream instead of the mic")
    ap.add_argument("--stay", action="store_true", help="with --file: keep running after the file ends")
    ap.add_argument("--enroll", metavar="名字")
    ap.add_argument("--count", type=int, default=5)
    ap.add_argument("--codec", choices=["opus", "pcm"], default="opus",
                    help="opus: 16 kHz mono 60 ms frames, StackChan's settings (default); pcm: raw PCM16")
    ap.add_argument("--chunk-ms", type=int, default=100, help="chunk size for --codec pcm only")
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--web-port", type=int, default=8770)
    ap.add_argument("--no-web", action="store_true")
    ap.add_argument("--no-open", action="store_true", help="don't open the browser automatically")
    c = Client(ap.parse_args())

    def on_term(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, on_term)          # `kill` also ends cleanly and prints the summary
    try:
        asyncio.run(c.run())
    except KeyboardInterrupt:
        pass
    finally:
        c.summary()
