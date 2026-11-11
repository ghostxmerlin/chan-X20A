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
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import numpy as np
import sherpa_onnx as so
from websockets.asyncio.server import serve

import recorder
import hello_db
import vision_link
import tingwu as tingwu_rt
import tingwu_switch
import mouth_link
import mouth_mode
import sound_kinds
import hearing_protocol as hp

SR = 16000
TICK = 0.1
PRE_ROLL = int(0.25 * SR)          # audio added before the VAD start so first syllables survive
RING_SECONDS = 60
EARLY_SPK = int(1.0 * SR)          # guess the speaker after this much speech
TAG_WIN, TAG_HOP = SR, SR // 2     # sound tagging: 1 s window every 0.5 s
SOUND_START, SOUND_KEEP, SOUND_MISS = 0.40, 0.25, 2
SOUND_START_DURING_SPEECH = 0.65
QUIET_RMS = 0.003                  # windows quieter than this are not tagged (only until the floor is known)
# A window is tagged only if its loudest 100 ms stands this far above the background (the floor).
# Measured on a Mac mic (2026-11-06): ticking, pen, breathing, distant birds +4..+13 dB; real typing,
# snaps, claps +15..+30; speech +18..+26. The brain can change it with {"type":"config","sound_min_db":..}.
SOUND_MIN_DB = 15.0
# Kinds that must be heard in this many windows in a row (hop 0.5 s) before an event starts.
# CED calls single clicks/taps "Music / Drum machine / Percussion"; on the Mac mic those false
# "music" events spanned at most 3 windows (2 s). Real music and singing go on. (2026-11-06)
SUSTAIN = {"music": 5}             # 5 windows = 3 s of audio
# Quiet sounds on the robot's own mic ("stackchan-*" sources only; the Mac mic keeps the gate above).
# StackChan, 2026-11-09: mic ADC already at max gain, floor -42..-45 dBFS with a 100 Hz harmonic hum,
# speech only +7..+19 dB above it. Over 127 min of its recordings CED heard typing at a median +6.7 dB
# (p 0.2-0.66), never above the 15 dB gate; the hum itself reads as insect/appliance/bird/heartbeat,
# so only these labels get the lower gate. Offline replay: +9 typing events, all in the two sessions
# where the user typed, no new events elsewhere.
DEVICE_QUIET_LABELS = {"Typing", "Typewriter", "Computer keyboard", "Clicking", "Clickety-clack",
                       "Sigh", "Breathing", "Gasp", "Pant", "Sniff",
                       "Cough", "Throat clearing", "Sneeze"}
DEVICE_QUIET_MIN_DB, DEVICE_QUIET_START = 5.0, 0.30
# Voice-likeness bands for a speech segment (CED-mini voice-label probability). User's recordings
# (2026-11-06): real speech 0.53-0.85, misfires (clipped claps, breathing, keyboard) <= 0.06; the
# exceptions were two sub-second Bluetooth-headset phrases (0.27, 0.09). A voiceprint match (known
# band) also counts as voice: misfires never scored above 0.33 against anyone.
VOICE_HI, VOICE_LO = 0.4, 0.2
# Noise floor added to the VAD's copy only. Bluetooth headsets (and any source that mutes to exact
# zeros) gate silence to digital zero; Silero then fires on every click that breaks the silence.
# -70 dBFS is below any real mic's own floor, so on a normal mic it changes nothing.
VAD_DITHER = 10 ** (-70 / 20)
OPUS_MAX_FRAME = SR * 120 // 1000          # largest Opus frame (120 ms); xiaozhi sends 60 ms

EMOTION = {"HAPPY": "happy", "SAD": "sad", "ANGRY": "angry", "NEUTRAL": "neutral", "FEARFUL": "fearful",
           "DISGUSTED": "disgusted", "SURPRISED": "surprised", "EMO_UNKNOWN": "unknown"}
SV_EVENT = {"Speech": "speech", "BGM": "bgm", "Applause": "applause", "Laughter": "laughter", "Cry": "cry",
            "Sneeze": "sneeze", "Cough": "cough", "Breath": "breath"}
CJK = re.compile(r"[㐀-鿿豈-﫿]")
WORD = re.compile(r"[A-Za-z0-9']+")


def token(s):
    return (s or "").strip().strip("<|>").strip()


def db(rms):
    return round(20 * math.log10(max(rms, 1e-9)), 1)


def rms(x):
    return float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0


def ms(seconds):
    return round(seconds * 1000)


def loudest_100ms(x):
    n = len(x) // 1600 * 1600
    if n == 0:
        return db(rms(x))
    return db(float(np.sqrt((x[:n].reshape(-1, 1600) ** 2).mean(axis=1)).max()))


class Models:
    """Inference objects, shared by all connections; one worker thread uses them."""

    def __init__(self, mdir, db_path, threads, denoise_mode="stream"):
        sv = f"{mdir}/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
        # CED-mini: tiny called a speaking second "Mantra/Music"; mini costs ~1 ms more per window
        ced = f"{mdir}/sherpa-onnx-ced-mini-audio-tagging-2024-04-19"
        self.vad_cfg = so.VadModelConfig(
            silero_vad=so.SileroVadModelConfig(
                model=f"{mdir}/silero_vad.onnx", threshold=0.5,
                min_silence_duration=0.4, min_speech_duration=0.3, max_speech_duration=20),
            sample_rate=SR)
        self.asr = so.OfflineRecognizer.from_sense_voice(
            model=f"{sv}/model.int8.onnx", tokens=f"{sv}/tokens.txt", use_itn=True, num_threads=threads)
        self.spk = so.SpeakerEmbeddingExtractor(so.SpeakerEmbeddingExtractorConfig(
            model=f"{mdir}/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx", num_threads=threads))
        self.tagger = so.AudioTagging(so.AudioTaggingConfig(
            model=so.AudioTaggingModelConfig(ced=f"{ced}/model.int8.onnx", num_threads=1),
            labels=f"{ced}/class_labels_indices.csv", top_k=25))    # wide enough to see voice labels
        self.kind_of = sound_kinds.build(f"{ced}/class_labels_indices.csv")
        # Robot mic denoiser (2026-11-10): the StackChan mic picks up crackle from the robot's own
        # digital activity; GTCRN removed it and, on the user's 3 min office clip, SenseVoice went
        # from 15 to 24 sentences. Optional: without the model file the ear runs as before.
        # denoise_mode: "stream" = VAD and ASR hear the denoised audio; "asr" = VAD, speaker and tags
        # hear the raw mic and only the speech segment handed to SenseVoice is denoised; "off".
        gtcrn = f"{mdir}/speech-enhancement/gtcrn_simple.onnx"
        self.denoise_mode = denoise_mode
        self.denoise_cfg = so.OnlineSpeechDenoiserConfig(model=so.OfflineSpeechDenoiserModelConfig(
            gtcrn=so.OfflineSpeechDenoiserGtcrnModelConfig(model=gtcrn), num_threads=1)) \
            if os.path.exists(gtcrn) and denoise_mode != "off" else None
        self.lock = threading.Lock()
        self.db_path = db_path
        self.speakers = {}
        if os.path.exists(db_path):
            d = np.load(db_path)
            self.speakers = {k: d[k] for k in d.files}

    def new_denoiser(self):
        """A fresh streaming denoiser (it keeps state, so one per connection), or None."""
        return so.OnlineSpeechDenoiser(self.denoise_cfg) if self.denoise_cfg else None

    def embed(self, x):
        s = self.spk.create_stream()
        s.accept_waveform(SR, x)
        s.input_finished()
        e = np.array(self.spk.compute(s), dtype=np.float32)
        return e / (np.linalg.norm(e) + 1e-9)

    def transcribe(self, x):
        s = self.asr.create_stream()
        s.accept_waveform(SR, x)
        self.asr.decode_stream(s)
        return s.result

    def tags(self, x):
        """[(label, prob)] top 25 from CED."""
        s = self.tagger.create_stream()
        s.accept_waveform(SR, x)
        return [(e.name, float(e.prob)) for e in self.tagger.compute(s)]

    @staticmethod
    def voice_p(tags):
        """How much this sounds like a human voice: the highest probability among voice labels."""
        return max([p for name, p in tags if name in sound_kinds.VOICE] or [0.0])

    def kinds(self, tags):
        """Collapse labels to kinds: {kind: (max prob, [labels])}, speech prob separately."""
        out, speech = {}, 0.0
        for name, p in tags:
            k = self.kind_of.get(name, "other")
            if k == "speech":
                speech = max(speech, p)
            elif k != "ignore":
                best, labs = out.get(k, (0.0, []))
                out[k] = (max(best, p), labs + [[name, round(p, 2)]])
        if "animal" in out and out.keys() & {"cat", "dog", "bird"}:
            del out["animal"]                       # "Animal" is just the parent label of the specific one
        return out, speech

    def save_speaker(self, name, embs, replace=False):
        self.speakers = hp.save_speaker(self.db_path, self.speakers, name, embs, replace)

    def forget_speaker(self, name):
        self.speakers = hp.forget_speaker(self.db_path, self.speakers, name)


class Ring:
    """Fixed-size history addressed by absolute sample index."""

    def __init__(self, n):
        self.buf = np.zeros(n, dtype=np.float32)
        self.n = n
        self.end = 0

    def write(self, x):
        x = x[-self.n:]
        pos = self.end % self.n
        first = min(len(x), self.n - pos)
        self.buf[pos:pos + first] = x[:first]
        if first < len(x):
            self.buf[:len(x) - first] = x[first:]
        self.end += len(x)

    def read(self, a, b):
        a, b = max(a, self.end - self.n, 0), min(b, self.end)
        if b <= a:
            return np.zeros(0, dtype=np.float32)
        return self.buf[np.arange(a, b) % self.n]


class Ear:
    """State of one audio stream (one robot / one client connection)."""

    def __init__(self, m, ws, ex):
        self.m, self.ws, self.ex = m, ws, ex
        self.vad = so.VoiceActivityDetector(m.vad_cfg, buffer_size_in_seconds=60)
        self.ring = Ring(RING_SECONDS * SR)
        # Sound tags read the raw mic (2026-11-10): GTCRN is a speech enhancer and removed typing
        # (14 → 0 events on the user's test) while helping speech. Without a denoiser both rings
        # are the same object, so the Mac path is unchanged.
        self.tag_ring = self.ring
        self.clean_ring = self.ring   # what SenseVoice reads ("asr" mode: the denoised copy)
        self.carry = np.zeros(0, dtype=np.float32)
        self.opus = None                          # set by {"type":"audio","format":"opus"}; default PCM16
        self.rx_bytes = 0
        self.bad_packets = 0
        self.rng = np.random.default_rng(0)       # fixed: replaying a recording gives the same segments
        self.arr_end, self.arr_mono, self.arr_epoch = [], [], []   # arrival time of each audio chunk
        self.last_rx = None
        self.tick_gap = 0.0
        self.tick_sq, self.tick_n = 0.0, 0
        # last 60 s of tick levels; the floor is their 10th percentile, so a steady sound (TV, siren)
        # only becomes "background" after it has filled ~54 s, not after a dozen seconds
        self.levels = collections.deque(maxlen=600)
        # the same for what VAD/ASR/tags hear (after the denoiser); gates compare against this floor
        self.heard_sq, self.heard_n = 0.0, 0
        self.heard_levels = collections.deque(maxlen=600)
        self.denoiser = None          # streaming GTCRN for robot sources (set by the audio message)
        self.outbox = []
        self.hi, self.lo = 0.6, 0.4
        self.sound_min_db = SOUND_MIN_DB
        self.device_profile = False   # robot mic: quiet labels use DEVICE_QUIET_* (set by the audio message)
        self.voice_hi, self.voice_lo = VOICE_HI, VOICE_LO
        self.self_flags = {"sounding": False, "moving": False}
        # speech state
        self.speaking = False
        self.sid = 0
        self.cur = None            # {"id","onset","t","early"}
        # sound state
        self.next_tag = TAG_WIN
        self.aid = 0
        self.sounds = {}           # kind -> state
        self.pending = {}          # kind -> candidate that must last (SUSTAIN) before it becomes an event
        self.enroll = hp.Enrollment()
        self.closed = False
        self.tasks = set()
        self.self_history = hp.SelfHistory()
        self.framing = 'raw'
        self.wire_seq = self.wire_us = None
        self.vad_origin = 0
        self.stream_generation = 0
        self.rec = None            # recorder.Recorder, set by the connection handler
        self.db = None             # hello_db.HelloDB, set by the connection handler (optional)
        self.session = None        # "YYYYMMDD/HHMMSS-source-cN": recording folder = database key
        self.tingwu_keys = None    # (api key, app id) when 听悟 realtime is on for robot sources
        self.tingwu = None         # tingwu_rt.TingwuStream for this connection (at most one at a time)
        self.tw_sentences = 0
        self.tw_paragraphs = 0     # paragraph numbers keep counting across streams of this connection
        self.tw_streams = 0
        self.tw_retry_at = 0.0     # monotonic: no new stream before this (60 s after a start error)
        self.tingwu_switch = tingwu_switch.SwitchReader(lambda: None)   # the server switch; off unless main injects one
        self.mouth_mode = mouth_mode.ModeReader(lambda: None)          # 嘴 mode; clips unless main injects one
        self.on_tingwu_change = None   # main: called when a stream starts or stops (status document)
        self.audio_declared = False
        self._tw_box = None
        self.hello = {"type": "hello", "sr": SR, "tick_ms": ms(TICK), "speakers": sorted(m.speakers)[:64],
                      "speaker_hi": self.hi, "speaker_lo": self.lo, "sound_min_db": self.sound_min_db,
                      "voice_hi": self.voice_hi, "voice_lo": self.voice_lo,
                      "models": {"asr": "sense-voice int8 2024-07-17", "spk": "3dspeaker campplus zh",
                                 "vad": "silero (min_silence 0.4 s)", "tags": "ced-mini → sound_kinds",
                                 "denoise": "gtcrn_simple (stackchan sources)" if m.denoise_cfg else None}}
        self.post(dict(self.hello))

    # ---------- helpers ----------
    def post(self, ev):
        if self.closed:
            return
        if self.db is not None and self.session:
            self.db.event(self.session, ev)
        if len(self.outbox) >= 24:
            self.outbox.pop(0)
        ev.setdefault("v", 1)
        ev["_ready"] = time.monotonic()
        self.outbox.append(ev)

    def spawn(self, coroutine):
        if self.closed or len(self.tasks) >= 8:
            coroutine.close()
            self.post({"type":"overload", "error":"inference queue full; audio event dropped"})
            return
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        def done(future):
            self.tasks.discard(future)
            if not future.cancelled() and future.exception():
                self.post({"type":"error", "error":"inference failed"})
        task.add_done_callback(done)

    def segment_context(self, cur, start, end):
        return {**cur, "self":self.self_history.overlap(max(start, self.vad_origin), end)}

    def segment_audio(self, start, end):
        return self.ring.read(max(start - PRE_ROLL, self.vad_origin), end)

    def reset_segment(self):
        self.vad.reset()
        self.outbox = [ev for ev in self.outbox if ev.get("type") != "speech"]
        self.vad_origin = self.ring.end
        self.stream_generation += 1
        self.cur, self.speaking = None, False
        self.carry = np.zeros(0, dtype=np.float32)
        self.sounds.clear()
        self.pending.clear()
        self.next_tag = self.tag_ring.end + TAG_WIN

    async def close(self):
        if self.tingwu is not None:
            self.stop_tingwu()                # finishes the task and fetches the diarized result later
        if self.db is not None and self.session:
            self.db.session_update(self.session, {"end_ms": int(time.time() * 1000), "utterances": self.sid,
                                                  "sounds": self.aid, "rx_bytes": self.rx_bytes})
        self.closed = True
        self.enroll.cancel()
        for task in list(self.tasks): task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    # ---------- 听悟 realtime (robot sources) ----------
    def tingwu_tick(self):
        """Start or stop 听悟 so it follows the server switch (called every tick)."""
        action = tingwu_switch.decide(self.tingwu_switch.on, self.tingwu is not None, self.tingwu_keys is not None,
                                      self.device_profile and self.audio_declared and not self.closed,
                                      time.monotonic() >= self.tw_retry_at)
        if action == "start":
            self.start_tingwu()
        elif action == "stop":
            self.stop_tingwu()

    def _tingwu_changed(self):
        if self.on_tingwu_change:
            self.on_tingwu_change()

    def stop_tingwu(self):
        stream, self.tingwu = self.tingwu, None
        if stream is not None:
            self._tw_box["stopped_ms"] = int(time.time() * 1000)
            stream.close()
            self._tingwu_changed()

    def start_tingwu(self):
        key, app = self.tingwu_keys
        self.tw_streams += 1
        k, offset = self.tw_streams, self.ring.end       # 听悟 times count from here
        start_ms = int(time.time() * 1000)
        box = {"stream": None, "sentences": 0, "stopped_ms": None}

        def sentence(s):
            if not s["final"]:
                return
            self.tw_sentences += 1
            box["sentences"] += 1
            pos = tingwu_switch.tingwu_pos(s.get("begin_ms"), s.get("end_ms"), offset)
            row = {"text": s["text"], "begin_ms": s.get("begin_ms"), "end_ms": s.get("end_ms"), "pos": pos, "sentence_id": s.get("id"),
                   "self": self.self_history.overlap(*pos) if pos else None, "at": int(time.time() * 1000),
                   "data_id": box["stream"].data_id if box["stream"] else None, "stream": k}
            if self.db is not None and self.session:
                self.db.tingwu(self.session, "sentence", self.tw_sentences, row)
            self.post({"type": "tingwu", "phase": "sentence", "n": self.tw_sentences, "text": s["text"][:300],
                       "pos": pos, "self": row["self"]})

        def final(paragraphs):
            print(f"tingwu final {self.session} #{k}: {len(paragraphs)} paragraphs", flush=True)
            if self.db is not None and self.session:
                for p in paragraphs:
                    self.tw_paragraphs += 1
                    self.db.tingwu(self.session, "paragraph", self.tw_paragraphs, {**p, "stream": k, "at": int(time.time() * 1000)})
                stream = box["stream"]
                summary = {"sentences": box["sentences"], "paragraphs": len(paragraphs),
                           "audio_s": round(stream.audio_seconds(), 1) if stream else None,
                           "data_id": stream.data_id if stream else None, "started_ms": start_ms,
                           "stopped_ms": box["stopped_ms"] or int(time.time() * 1000), "offset_samples": offset}
                self.db.session_update(self.session, {"tingwu": {**summary, "streams": k}, f"tingwu_streams.{k}": summary})

        def error(message):
            print(f"tingwu error {self.session} #{k}: {message}", flush=True)
            self.post({"type": "tingwu", "phase": "error", "error": message[:160]})
            if self.db is not None and self.session:
                self.db.session_update(self.session, {"tingwu_error": message[:300]})
            self.tw_retry_at = time.monotonic() + 60
            if self.tingwu is box["stream"] and self.tingwu is not None:
                self.stop_tingwu()

        stream = tingwu_rt.TingwuStream(key, app, sentence, final, error)
        box["stream"] = stream
        self.tingwu, self._tw_box = stream, box
        stream.start()
        print(f"tingwu start {self.session} #{k} at sample {offset}", flush=True)
        self._tingwu_changed()

    def arrival(self, sample):
        """(mono, epoch ms) when the chunk holding `sample` arrived."""
        i = bisect.bisect_left(self.arr_end, sample)
        if i >= len(self.arr_end):
            return time.monotonic(), int(time.time() * 1000)
        return self.arr_mono[i], self.arr_epoch[i]

    @staticmethod
    def _floor_of(levels):
        # ignore digital silence (exact zeros, e.g. padding in test files): a real mic never gets there
        lv = [x for x in levels if x > -100]
        return float(np.percentile(lv, 10)) if len(lv) >= 5 else None

    def floor(self):
        """Background level of what the ear hears (after the denoiser): what the gates compare with."""
        return self._floor_of(self.heard_levels)

    def mic_floor(self):
        """Background level of the raw microphone, as reported to the robot (noise_db)."""
        return self._floor_of(self.levels)

    def loud_tag(self, x):
        f = self.tag_floor()
        return None if f is None or not len(x) else round(db(rms(x)) - f, 1)

    def loud(self, x):
        f = self.floor()
        return None if f is None or not len(x) else round(db(rms(x)) - f, 1)

    def speaker(self, e):
        if not self.m.speakers:
            return {"band": "none", "name": None, "score": None, "runner_up": None}
        ranked = sorted(((float(e @ v), n) for n, v in self.m.speakers.items()), reverse=True)
        score, name = ranked[0]
        band = "known" if score >= self.hi else "unsure" if score >= self.lo else "unknown"
        ru = {"name": ranked[1][1], "score": round(ranked[1][0], 3)} if len(ranked) > 1 else None
        return {"band": band, "name": name, "score": round(score, 3), "runner_up": ru}

    def voice(self, tags, speaker):
        """Is this a human voice? Facts plus a band; the reader decides what to do with it."""
        p = self.m.voice_p(tags)
        if p >= self.voice_hi or speaker["band"] == "known":
            band = "voice"
        else:
            band = "unclear" if p >= self.voice_lo else "none"
        acoustic = "voice" if p >= self.voice_hi else "unclear" if p >= self.voice_lo else "none"
        return {"p": round(p, 2), "band": band, "acoustic_band": acoustic}

    async def run(self, fn):
        def locked():
            with self.m.lock:
                return fn()
        return await asyncio.get_running_loop().run_in_executor(self.ex, locked)

    # ---------- input ----------
    async def on_audio(self, data):
        now = time.monotonic()
        if self.last_rx is not None:
            self.tick_gap = max(self.tick_gap, now - self.last_rx)
        self.last_rx = now
        self.rx_bytes += len(data)
        try:
            raw, seq, capture_us = hp.parse_audio_packet(data, self.framing)
            if seq is not None:
                if self.wire_seq is not None:
                    gap = (seq - self.wire_seq - 1) & 0xffffffff
                    if gap > 1000 or capture_us <= self.wire_us:
                        raise ValueError('out of order SEA1 packet')
                    if gap or abs(capture_us - self.wire_us - 60000) > 30000:
                        self.reset_segment()
                        self.post({'type':'audio_gap','missing_packets':gap,'seq':seq,'capture_us':capture_us})
                elif seq != 0:
                    raise ValueError('SEA1 must begin at sequence zero')
                self.wire_seq, self.wire_us = seq, capture_us
            data = raw
        except ValueError as error:
            self.bad_packets += 1
            self.reset_segment()
            self.post({'type':'error','error':str(error)})
            return
        if self.opus is not None:
            try:
                data = self.opus.decode(data, OPUS_MAX_FRAME)
            except Exception as e:                       # one bad packet must not kill the stream
                self.bad_packets += 1
                self.reset_segment()
                if self.bad_packets == 1:
                    self.post({"type": "error", "error": "opus decode failed"})
                return
        if len(data) % 2:
            self.bad_packets += 1
            self.reset_segment()
            return
        if self.rec:
            self.rec.audio_in(raw, data, self.opus is not None)
        if self.tingwu is not None:
            # raw 16 kHz mic (no denoise), but silence while hello makes sound, like VAD/ASR: 听悟 never hears
            # her own voice (user 2026-11-11: "hello发声时不收音"; it used to get everything, then be flagged)
            self.tingwu.feed(bytes(len(data)) if self.self_flags.get("sounding") else data)
        x = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
        self.tick_sq += float(np.sum(x ** 2))      # level meter reports what the mic really heard
        self.tick_n += len(x)
        if self.tag_ring is not self.ring:
            # raw audio for sound tags; her own voice is silenced here too
            self.tag_ring.write(np.zeros_like(x) if self.self_flags.get("sounding") else x)
        if self.denoiser is not None:
            # Streaming GTCRN keeps sample alignment (output k = input k) but hands back whole
            # 16 ms hops, so a few ms may come out with the next packet; positions stay exact.
            y = np.asarray(self.denoiser.run(x, SR).samples, dtype=np.float32)
            if self.rec:
                self.rec.heard_in(y)
            if self.clean_ring is not self.ring:            # "asr": only SenseVoice gets the denoised copy
                self.clean_ring.write(np.zeros_like(y) if self.self_flags.get("sounding") else y)
            else:
                x = y
                if not len(x):
                    self.schedule_tags()
                    return
        self.self_history.add(self.ring.end, self.ring.end + len(x), self.self_flags)
        self.heard_sq += float(np.sum(x ** 2))
        self.heard_n += len(x)
        if self.self_flags.get("sounding"):
            # While she is making sound (playback plus the robot's 1.2 s tail) the mic hears her own
            # voice; there is no echo cancellation, so VAD, ASR and sound tags get silence instead
            # (user's choice A, 2026-11-09). The recording above keeps the original audio.
            x = np.zeros_like(x)
        self.ring.write(x)
        self.arr_end.append(self.ring.end)
        self.arr_mono.append(now)
        self.arr_epoch.append(int(time.time() * 1000))
        if len(self.arr_end) > 20000:                       # keep ~30 min of arrival history
            del self.arr_end[:5000], self.arr_mono[:5000], self.arr_epoch[:5000]

        dithered = x + self.rng.normal(0, VAD_DITHER, len(x)).astype(np.float32)
        buf = np.concatenate([self.carry, dithered])
        n = (len(buf) // 512) * 512
        for i in range(0, n, 512):
            self.vad.accept_waveform(buf[i:i + 512])
        self.carry = buf[n:]
        self.track_speech()
        self.schedule_tags()

    def schedule_tags(self):
        while self.tag_ring.end >= self.next_tag:
            b = self.next_tag
            self.next_tag += TAG_HOP
            self.spawn(self.tag_window(b - TAG_WIN, b))

    def track_speech(self):
        detected = self.vad.is_speech_detected()
        if detected and not self.speaking:
            self.speaking = True
            self.open_utterance(self.vad_origin + int(self.vad.current_segment.start))
        if self.speaking and self.cur and not self.cur["early"] and self.ring.end - self.cur["onset"] >= EARLY_SPK:
            self.cur["early"] = True
            self.spawn(self.early_speaker(self.segment_context(self.cur, self.cur['onset'] - PRE_ROLL, self.ring.end)))
        while not self.vad.empty():
            seg = self.vad.front
            start = self.vad_origin + int(seg.start)
            end = start + len(seg.samples)
            self.vad.pop()
            t_pop = time.monotonic()
            cur = self.cur if self.cur and abs(self.cur["onset"] - start) < SR else None
            if cur is None:
                cur = self.new_utterance(start)
            audio = self.segment_audio(start, end)          # original audio, not the VAD's dithered copy
            clean = self.clean_ring.read(max(start - PRE_ROLL, self.vad_origin), end) if self.clean_ring is not self.ring else audio
            self.spawn(self.end_speech(self.segment_context(cur, start - PRE_ROLL, end), audio, start, end, t_pop, clean))
            self.cur = None
            self.speaking = self.vad.is_speech_detected()
            if self.speaking:                                   # max_speech_duration split: keeps going
                self.open_utterance(self.vad_origin + int(self.vad.current_segment.start))

    def new_utterance(self, onset):
        self.sid += 1
        return {"id": f"s-{self.sid}", "onset": onset, "t": self.arrival(onset)[1], "early": False,
                "enroll_epoch": self.enroll.epoch, "generation": self.stream_generation}

    def open_utterance(self, onset):
        self.cur = self.new_utterance(onset)

    # ---------- speech ----------
    async def early_speaker(self, cur):
        audio = self.segment_audio(cur["onset"], cur["onset"] + EARLY_SPK)
        t0 = time.monotonic()
        e, tags = await self.run(lambda: (self.m.embed(audio), self.m.tags(audio)))
        if self.closed or cur["generation"] != self.stream_generation:
            return
        sp = self.speaker(e)
        self.post({"type": "speech", "phase": "start", "id": cur["id"], "pos": [cur["onset"], None],
                   "t": cur["t"], "speaker": sp, "voice": self.voice(tags, sp), "self": cur["self"],
                   "debug": {"spk_tag_ms": ms(time.monotonic() - t0), "audio_s": round(len(audio) / SR, 2)}})

    async def end_speech(self, cur, audio, start, end, t_pop, clean=None):
        t_q = time.monotonic()
        arr_mono, _ = self.arrival(end)

        def work():
            t0 = time.monotonic()
            e = self.m.embed(audio)
            t1 = time.monotonic()
            r = self.m.transcribe(audio if clean is None or not len(clean) else clean)
            t2 = time.monotonic()
            tags = self.m.tags(audio)
            t3 = time.monotonic()
            return e, r, tags, (t0, t1, t2, t3)

        e, r, tags, (t0, t1, t2, t3) = await self.run(work)
        if self.closed or cur["generation"] != self.stream_generation:
            return
        kinds, speech_p = self.m.kinds(tags)
        dur = (end - start) / SR
        text = r.text.strip()[:512]
        units = len(CJK.findall(text)) + len(WORD.findall(CJK.sub(" ", text)))
        sp = self.speaker(e)
        ev = {"type": "speech", "phase": "end", "id": cur["id"], "pos": [start, end], "t": cur["t"],
              "dur": round(dur, 2), "speaker": sp, "text": text, "lang": token(r.lang),
              "emotion": EMOTION.get(token(r.emotion), token(r.emotion).lower() or "unknown"),
              "emotion_reliable": False, "event": SV_EVENT.get(token(r.event), token(r.event).lower()),
              "voice": {**self.voice(tags, sp), "loud_db": self.loud(audio),
                        "rate": round(units / dur, 1) if dur > 0 else None},
              "tags": {"speech_p": round(speech_p, 2),
                       "kinds": sorted(([k, round(p, 2), sound_kinds.LABEL_ZH.get(k, k)]
                                        for k, (p, _) in kinds.items()), key=lambda t: -t[1])[:3]},
              "self": cur["self"], "respond": self.mouth_mode.respond,
              "debug": {"vad_wait_ms": ms(t_pop - arr_mono), "queue_ms": ms(t0 - t_q), "spk_ms": ms(t1 - t0),
                        "asr_ms": ms(t2 - t1), "tag_ms": ms(t3 - t2), "server_ms": ms(time.monotonic() - t_pop)}}
        self.post(ev)
        # The robot mic's CED voice probability runs low (office noise, hum): the user's clear
        # sentences scored 0.17-0.26 on 2026-11-10, so robot sources also accept "unclear" (>= voice_lo).
        voiced = ev["voice"]["acoustic_band"] == "voice" or (self.device_profile and ev["voice"]["acoustic_band"] == "unclear")
        ready = self.enroll.accept(cur["enroll_epoch"], e, dur,
                                   voiced and ev["event"] == "speech" and bool(text),
                                   cur["self"]["sounding"], time.monotonic())
        if self.enroll.active and cur["enroll_epoch"] == self.enroll.epoch:
            progress = {**self.enroll.progress(), "phase":"progress", "dur": round(dur, 2)}
            if getattr(self.enroll, "rejected", ""):
                progress["rejected"] = self.enroll.rejected
            self.post(progress)
        if ready:
            result = self.enroll.progress()
            try:
                # Same event loop: no await between cancellation check and atomic commit.
                self.m.save_speaker(self.enroll.name, self.enroll.embs, self.enroll.replace)
                self.post({**result, "phase":"done", "speakers":sorted(self.m.speakers)[:64]})
            except (OSError, ValueError):
                self.post({**result, "phase":"error", "error":"voiceprint save failed"})
            self.enroll.cancel()

    # ---------- sound ----------
    def tag_floor(self):
        """Floor for sound gates: the raw mic when tags read the raw ring, else what VAD hears."""
        return self.mic_floor() if self.tag_ring is not self.ring else self.floor()

    async def tag_window(self, a, b):
        x = self.tag_ring.read(a, b)
        f = self.tag_floor()
        lift = None if f is None else loudest_100ms(x) - f
        faint = rms(x) < QUIET_RMS if f is None else lift < self.sound_min_db
        # robot mic: quiet labels (typing, breathing...) may start an event from a lower lift
        quiet_ok = self.device_profile and lift is not None and lift >= DEVICE_QUIET_MIN_DB
        # a faint window can't start an event, but still feeds ongoing ones: a steady sound (TV, rain)
        # slowly becomes the floor itself and must not be cut off when it does
        need = not faint or quiet_ok or bool(self.sounds) or bool(self.pending)
        # always go through the single worker, even when skipping, so windows finish in order
        tags = await self.run(lambda: self.m.tags(x) if need else [])
        kinds, _ = self.m.kinds(tags)
        # while someone is talking the 1 s window is mostly voice, and CED tends to call it music/TV;
        # only a clearly separate sound may start an event then
        start_at = SOUND_START_DURING_SPEECH if self.speaking else SOUND_START
        for k, (p, labs) in kinds.items():
            s = self.sounds.get(k)
            if s is None:
                c = self.pending.get(k)
                quiet = (quiet_ok and not self.speaking and
                         any(name in DEVICE_QUIET_LABELS and q >= DEVICE_QUIET_START for name, q in labs))
                if c is None and (p >= start_at and not faint or quiet):
                    c = self.pending[k] = {"start": a, "n": 0, "peak": 0.0, "raw": labs}
                if c is None or p < SOUND_KEEP:
                    continue
                c["n"], c["seen"] = c["n"] + 1, b
                if p > c["peak"]:
                    c["peak"], c["raw"] = p, labs
                if c["n"] < SUSTAIN.get(k, 1):
                    continue
                del self.pending[k]
                self.aid += 1
                s = self.sounds[k] = {"id": f"a-{self.aid}", "start": c["start"], "t": self.arrival(c["start"])[1],
                                      "last": b, "peak": c["peak"], "raw": c["raw"], "miss": 0}
                self.post({"type": "sound", "phase": "start", "id": s["id"], "kind": k,
                           "label": sound_kinds.LABEL_ZH.get(k, k), "pos": [c["start"], None], "t": s["t"],
                           "p": round(c["peak"], 2), "raw": c["raw"], "self": dict(self.self_flags)})
            elif p >= SOUND_KEEP:
                s["last"], s["miss"] = b, 0
                if p > s["peak"]:
                    s["peak"], s["raw"] = p, labs
        for k in [k for k, c in self.pending.items() if c.get("seen") != b]:
            del self.pending[k]                 # broke off before it lasted: a one-off, not reported
        for k in list(self.sounds):
            s = self.sounds[k]
            if s["last"] == b:
                continue
            s["miss"] += 1
            if s["miss"] >= SOUND_MISS:
                del self.sounds[k]
                self.post({"type": "sound", "phase": "end", "id": s["id"], "kind": k,
                           "label": sound_kinds.LABEL_ZH.get(k, k), "pos": [s["start"], s["last"]], "t": s["t"],
                           "dur": round((s["last"] - s["start"]) / SR, 2), "peak": round(s["peak"], 2),
                           "loud_db": self.loud_tag(self.tag_ring.read(s["start"], s["last"])), "raw": s["raw"],
                           "self": dict(self.self_flags)})

    # ---------- control ----------
    async def on_text(self, text):
        try:
            if len(text) > 4096:
                raise ValueError("control too large")
            msg = json.loads(text)
            if not isinstance(msg, dict):
                raise ValueError("expected JSON object")
            k = msg.get("type")
            if self.rec:
                self.rec.client_msg(msg)
            if k == "ping":
                self.post({"type":"pong", "id":msg.get("id"), "_held_from":time.monotonic()})
            elif k == "enroll":
                self.enroll.start(msg, self.m.speakers, time.monotonic())
                # Discard an utterance already in progress before this explicit enrollment.
                self.reset_segment()
                self.post({**self.enroll.progress(), "phase":"started"})
            elif k == "vision":
                # M12 change event from the robot (collector-0.4.73+): stored, never answered.
                if not isinstance(msg.get("event"), str) or len(msg["event"]) > 32:
                    raise ValueError("vision needs an event name")
                if self.db is not None and self.session:
                    self.db.vision(self.session, msg, int(time.time() * 1000))
            elif k == "forget":
                # {"type":"forget","name":..}: delete one voiceprint; the reply lists what is left.
                name = msg.get("name")
                if not isinstance(name, str) or not name:
                    raise ValueError("forget needs a speaker name")
                try:
                    self.m.forget_speaker(name)
                    self.post({"type":"speakers", "op":"forget", "name":name, "ok":True, "speakers":sorted(self.m.speakers)[:64]})
                except KeyError:
                    self.post({"type":"speakers", "op":"forget", "name":name, "ok":False, "error":"unknown_speaker",
                               "speakers":sorted(self.m.speakers)[:64]})
                except OSError:
                    self.post({"type":"speakers", "op":"forget", "name":name, "ok":False, "error":"save_failed",
                               "speakers":sorted(self.m.speakers)[:64]})
            elif k == "enroll_cancel":
                result = self.enroll.progress() if self.enroll.active else {"type":"enroll"}
                self.enroll.cancel()
                self.post({**result, "phase":"cancelled"})
            elif k == "audio":
                if self.ring.end:
                    raise ValueError("audio format cannot change during a stream")
                fmt, rate, framing = hp.audio_format(msg)
                if fmt == "opus":
                    import opuslib_next
                    self.opus = opuslib_next.Decoder(SR, 1)
                else:
                    self.opus = None
                self.framing = framing
                self.device_profile = str(msg.get("source") or "").startswith("stackchan")
                self.denoiser = self.m.new_denoiser() if self.device_profile else None
                stream = self.denoiser is not None and getattr(self.m, "denoise_mode", "stream") == "stream"
                asr_only = self.denoiser is not None and not stream
                self.tag_ring = Ring(RING_SECONDS * SR) if stream else self.ring
                self.clean_ring = Ring(RING_SECONDS * SR) if asr_only else self.ring
                if self.rec:
                    self.rec.declare(msg)
                    if self.db is not None and self.rec.dir:
                        self.session = hello_db.session_key(self.rec.dir)
                        self.db.session_open(self.session, {
                            "source": msg.get("source"), "peer": self.rec.peer, "start_ms": self.rec.meta.get("start_ms"),
                            "start": self.rec.meta.get("start"), "audio": {k: msg.get(k) for k in ("format", "sample_rate", "frame_ms", "framing")},
                            "device": msg.get("device"), "recording": self.rec.dir, "models": self.hello.get("models")})
                self.audio_declared = True       # 听悟 starts from the ticker when the server switch is on
                self.post({"type":"audio", "format":fmt, "sample_rate":SR,
                           "input_sample_rate":rate, "framing":framing, "frame_ms":msg.get("frame_ms"),
                           "sound_profile": "device" if self.device_profile else "default",
                           "denoise": "gtcrn" if self.denoiser is not None else None})
            elif k == "config":
                hi, lo = float(msg.get("speaker_hi", self.hi)), float(msg.get("speaker_lo", self.lo))
                vh, vl = float(msg.get("voice_hi", self.voice_hi)), float(msg.get("voice_lo", self.voice_lo))
                noise = float(msg.get("sound_min_db", self.sound_min_db))
                if not (0 <= lo <= hi <= 1 and 0 <= vl <= vh <= 1 and 0 <= noise <= 80):
                    raise ValueError("invalid thresholds")
                self.hi, self.lo, self.voice_hi, self.voice_lo, self.sound_min_db = hi, lo, vh, vl, noise
                self.post({"type":"config", "speaker_hi":hi, "speaker_lo":lo,
                           "voice_hi":vh, "voice_lo":vl, "sound_min_db":noise})
            elif k == "self":
                if any(type(msg.get(key, False)) is not bool for key in ("sounding", "moving")):
                    raise ValueError("self flags must be boolean")
                self.self_flags = {key:msg.get(key, False) for key in ("sounding", "moving")}
            else:
                raise ValueError("unknown control type")
        except (ValueError, TypeError, OverflowError) as error:
            self.post({"type":"error", "error":str(error)[:160]})

    # ---------- output: one frame per 100 ms ----------
    async def ticker(self):
        t0 = time.monotonic()
        seq = 0
        while True:
            seq += 1
            target = t0 + seq * TICK
            await asyncio.sleep(max(0.0, target - time.monotonic()))
            now = time.monotonic()
            if now - target > TICK:                      # fell behind (GC, CPU): skip, don't burst
                seq = int((now - t0) / TICK)
            if self.enroll.expired(now):
                self.post({**self.enroll.progress(), 'phase':'error', 'error':'enrollment timed out'})
                self.enroll.cancel()
            self.tingwu_tick()
            level = db(math.sqrt(self.tick_sq / self.tick_n)) if self.tick_n else None
            if level is not None:
                self.levels.append(level)
            heard = db(math.sqrt(self.heard_sq / self.heard_n)) if self.heard_n else None
            if heard is not None:
                self.heard_levels.append(heard)
            f = self.mic_floor()
            hf = self.floor()
            events = []
            budget = 0
            remaining = []
            for ev in self.outbox:
                size = len(json.dumps(ev, ensure_ascii=False).encode()) + 100
                if size > 6000:
                    continue
                if budget + size > 7000:
                    remaining.append(ev)
                    continue
                budget += size
                ev.setdefault("debug", {})["tick_wait_ms"] = ms(now - ev.pop("_ready"))
                if "_held_from" in ev:
                    ev["held_ms"] = ms(now - ev.pop("_held_from"))
                events.append(ev)
            self.outbox = remaining
            tick = {"v": 1, "type": "tick", "seq": seq, "t": int(time.time() * 1000), "rx": self.ring.end,
                    "rx_idle_ms": ms(now - self.last_rx) if self.last_rx else None,
                    "rx_gap_ms": ms(self.tick_gap), "rx_bytes": self.rx_bytes, "level_db": level,
                    "noise_db": round(f, 1) if f is not None else None,
                    "heard_noise_db": round(hf, 1) if hf is not None else None, "speaking": self.speaking,
                    "events": events}
            self.tick_gap, self.tick_sq, self.tick_n = 0.0, 0.0, 0
            self.heard_sq, self.heard_n = 0.0, 0
            if self.rec:
                self.rec.tick(tick, events)
            await self.ws.send(json.dumps(tick, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--models", required=True)
    ap.add_argument("--speakers", default=None, help="speakers.npz (default: next to this file)")
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--hubmix-env", default=os.path.expanduser("~/aidea/agent/.env"),
                    help="env file with MONGO_URI and DASHSCOPE_API_KEY / DASHSCOPE_APP_ID (server only)")
    ap.add_argument("--db", choices=["on", "off"], default="on", help="write sessions/speech/sounds/tingwu to MongoDB 'hello'")
    ap.add_argument("--tingwu", choices=["on", "off"], default="on",
                    help="听悟 realtime transcription for robot sources (billed per audio minute)")
    ap.add_argument("--denoise-mode", choices=["stream", "asr", "off"], default="stream",
                    help="robot sources only: denoise what VAD+ASR hear, only ASR segments, or nothing")
    ap.add_argument("--token-file", default=os.environ.get("SOUL_TOKEN_FILE", ""))
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
    tw_switch = tingwu_switch.SwitchReader((lambda: database.read_switch("tingwu")) if database else (lambda: None))
    mouth = mouth_mode.ModeReader((lambda: database.read_switch("mouth")) if database else (lambda: None))

    def write_tingwu_status():
        if database is not None:
            database.switch_status("tingwu", {"running": sorted(e.session or "?" for e in ears if e.tingwu is not None),
                                              "available": tingwu_keys is not None, "on": tw_switch.on,
                                              "at": int(time.time() * 1000)})

    async def watch_tingwu_switch():
        """Read the server switch every 5 s (off the model thread); status on change and every 30 s."""
        last, n = None, 0
        while True:
            await asyncio.to_thread(tw_switch.update)
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

    async def handler(ws):
        path = ws.request.path if getattr(ws, "request", None) else ""
        header = ws.request.headers.get("Authorization", "") if getattr(ws, "request", None) else ""
        if not hp.authenticate(path, header, token_value):
            await ws.close(code=4401, reason="bad token")
            return
        if urlsplit(path).path == "/mouth":            # speaker downlink: relayed to soul-tts (2026-11-11)
            if len(mouth_active) >= 2:
                await ws.close(code=1013, reason="server busy")
                return
            mouth_active.add(ws)
            peer = ws.remote_address
            print(f"+ {peer} mouth", flush=True)
            try:
                why = await mouth_link.relay(ws, mouth_link.UPSTREAM, token_value)
            finally:
                mouth_active.discard(ws)
            print(f"- {peer} mouth: {why}", flush=True)
            return
        if urlsplit(path).path == "/vision":           # vision uplink: its own connection (collector-0.4.74+)
            if len(vision_active) >= 4:
                await ws.close(code=1013, reason="server busy")
                return
            vision_active.add(ws)
            vision_no[0] += 1
            peer = ws.remote_address
            print(f"+ {peer} vision v{vision_no[0]}", flush=True)
            try:
                link = await vision_link.serve_link(ws, database, vision_no[0], f"{peer[0]}:{peer[1]}", rec_root)
            finally:
                vision_active.discard(ws)
            print(f"- {peer} vision v{vision_no[0]}: {link.events} events, {link.frames} frames ({link.session})", flush=True)
            return
        if len(active) >= 4:
            await ws.close(code=1013, reason="server busy")
            return
        active.add(ws)
        peer = ws.remote_address
        conn_no[0] += 1
        print(f"+ {peer} c{conn_no[0]}", flush=True)
        ear = Ear(models, ws, ex)
        ear.rec = recorder.Recorder(rec_root, conn_no[0], f"{peer[0]}:{peer[1]}", ear.hello)
        ear.db = database
        ear.tingwu_keys = tingwu_keys
        ear.tingwu_switch, ear.on_tingwu_change = tw_switch, write_tingwu_status
        ear.mouth_mode = mouth
        ears.add(ear)
        ticker = asyncio.create_task(ear.ticker())
        try:
            async for data in ws:
                if isinstance(data, bytes):
                    await ear.on_audio(data)
                else:
                    await ear.on_text(data)
        finally:
            ticker.cancel()
            await ear.close()
            await asyncio.gather(ticker, return_exceptions=True)
            ear.rec.close()
            active.discard(ws)
            ears.discard(ear)
            print(f"- {peer} ({ear.sid} utterances, {ear.aid} sounds) recorded in {ear.rec.dir}", flush=True)

    async def serve_forever():
        async with serve(handler, a.host, a.port, max_size=65536):
            print(f"listening on {a.host}:{a.port}", flush=True)
            watcher = asyncio.create_task(watch_tingwu_switch())
            try:
                await asyncio.Future()
            finally:
                watcher.cancel()

    asyncio.run(serve_forever())


if __name__ == "__main__":
    main()
