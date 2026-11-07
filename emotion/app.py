"""Mac worker: robot capture, cloud transcription and local emotion inference."""
import argparse
import asyncio
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import contextlib
import functools
import fcntl
import json
import os
from pathlib import Path
import signal
import threading
import time
import uuid

import numpy as np
import soxr

from .core import EVIDENCE_TTL, OutputWatchdog, SoundGate
from .inference import LocalInference
from .robot import Robot
from .tingwu import Tingwu
from .aihubmix import AIHubMixRealtime


def log(event, **fields):
    # Never log exception messages (may contain signed URLs), audio or text.
    print(json.dumps({"at": time.time(), "event": event, **fields}, ensure_ascii=False), flush=True)


def replace_queue(queue, item):
    if queue.full():
        queue.get_nowait()
    queue.put_nowait(item)


class App:
    def __init__(self, config, vision_only=False):
        self.config = config
        self.robot = Robot(config)
        self.stop = asyncio.Event()
        self.audio_stop = threading.Event()
        self.raw = asyncio.Queue(maxsize=100)
        self.outgoing = asyncio.Queue(maxsize=20)  # 2 seconds, including pre-roll.
        self.pre_roll = deque(maxlen=10)  # 1 second at 100 ms/block.
        self.watchdog = OutputWatchdog(time.monotonic())
        self.gate = SoundGate()
        self.sound_event = False
        self.stt_state = "error"
        self.paused = False
        self.rms = 0.0
        self.engine = None
        self.demo = None
        self.audio_at = self.audio_us = 0
        self.audio_generation = 0
        self.transcript = None
        self.camera = None
        self.result = None
        self.dropped_audio = self.inferences = self.stt_outputs = 0
        self.model_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="emotion-model")
        self.credentials = None
        self.pending = Path(config["state_dir"]).expanduser() / "tingwu-pending.json"
        self.status_file = self.pending.parent / "status.json"
        self.pending.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.instance_lock = (self.pending.parent / "service.lock").open("a")
        fcntl.flock(self.instance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        key_path = Path(config.get("tingwu_credentials_file", "/nonexistent")).expanduser()
        if config.get("stt_provider", "tingwu") == "tingwu" and key_path.is_file() and not vision_only:
            candidate = json.loads(key_path.read_text())
            if all(isinstance(candidate.get(k), str) and candidate[k].strip()
                   for k in ("access_key_id", "access_key_secret", "app_key")):
                self.credentials = candidate
        self.cloud_type = Tingwu
        self.sample_rate = 16000
        if config.get("stt_provider") == "aihubmix":
            self.cloud_type = AIHubMixRealtime
            self.sample_rate = 24000
            if not vision_only:
                key = Path(config["aihubmix_key_file"]).expanduser().read_text().strip()
                self.credentials = {"api_key": key} if key else None
        if not self.credentials:
            log("stt_unconfigured", vision_only=vision_only)

    def capture(self, loop):
        try:
            for block in self.robot.audio(self.audio_stop):
                loop.call_soon_threadsafe(replace_queue, self.raw, block)
        except Exception as error:
            log("audio_disconnected", error=type(error).__name__)

    async def audio_loop(self):
        converter = soxr.ResampleStream(24000, self.sample_rate, 1, dtype="int16", quality="HQ") if self.sample_rate != 24000 else None
        packet_bytes = self.sample_rate // 5
        last_seq, pcm = None, bytearray()
        while not self.stop.is_set() and not self.audio_stop.is_set():
            try:
                block = await asyncio.wait_for(self.raw.get(), 0.3)
            except asyncio.TimeoutError:
                continue
            if block.boot != self.robot.boot:
                continue
            if last_seq is not None and block.seq != (last_seq + 1) & 0xffffffff:
                self.dropped_audio += 1
                self.audio_generation += 1
                self.pre_roll.clear()
                pcm.clear()
                if converter: converter.clear()
            last_seq = block.seq
            self.audio_at, self.audio_us = time.monotonic(), block.timestamp_us
            mono = np.frombuffer(block.pcm, dtype="<i2").reshape(-1, 2)[:, 0].copy()
            rms = float(np.sqrt(np.mean((mono.astype(np.float32) / 32768.0) ** 2)))
            self.rms = rms
            if self.gate.feed(rms):
                self.sound_event = True
            converted = converter.resample_chunk(mono) if converter else mono
            pcm.extend(converted.astype("<i2", copy=False).tobytes())
            while len(pcm) >= packet_bytes:
                packet = (self.audio_generation, block.timestamp_us, bytes(pcm[:packet_bytes]))
                del pcm[:packet_bytes]
                self.pre_roll.append(packet)
                replace_queue(self.outgoing, packet)

    async def stt_loop(self):
        cloud = None
        retry_at, failures, generation = 0.0, 0, 0
        stream_audio_generation = -1
        stream_origin_us = last_sent_us = 0
        receive_generation = 0

        def on_text(index, text, final, offset_ms):
            if receive_generation != generation:
                return
            now = time.monotonic()
            if self.watchdog.output(now, generation, index, text):
                self.stt_outputs += 1
                # STT time refers to the sent audio, not network callback time.
                observed = min(last_sent_us, stream_origin_us + int(offset_ms * 1000)) if offset_ms is not None else 0
                if observed > 0:
                    self.transcript = (now, observed, text, generation)

        async def close_cloud():
            nonlocal cloud, generation
            generation += 1  # Ignore late results arriving during shutdown.
            self.transcript = None
            if cloud:
                try:
                    await cloud.close()
                    log("stt_stopped")
                except Exception as error:
                    log("tingwu_stop_pending", error=type(error).__name__)
                cloud = None

        try:
            while not self.stop.is_set():
                now = time.monotonic()
                if self.paused:
                    await close_cloud()
                    self.stt_state = "offline"
                    await asyncio.sleep(0.2)
                    continue
                if self.watchdog.expire(now):
                    self.gate.disarm()
                    self.sound_event = False
                    log("stt_idle_timeout", seconds=600)
                    await close_cloud()
                edge, self.sound_event = self.sound_event, False
                if self.watchdog.wake(now, edge):
                    log("stt_sound_wake")
                    retry_at = 0
                if self.watchdog.sleeping:
                    self.stt_state = "sleeping" if self.credentials else "error"
                    # Failed task-stop is retried even during idle; no audio sent.
                    if self.credentials and self.pending.exists() and now >= retry_at:
                        try:
                            await self.cloud_type(self.credentials, self.pending).cleanup_pending()
                        except Exception as error:
                            log("tingwu_stop_pending", error=type(error).__name__)
                        retry_at = now + 30
                    await asyncio.sleep(0.1)
                    continue
                if not self.credentials:
                    self.stt_state = "error"
                    await asyncio.sleep(0.5)
                    continue
                if now - self.audio_at > 3:
                    self.stt_state = "error"
                    await close_cloud()
                    await asyncio.sleep(0.2)
                    continue
                if cloud and stream_audio_generation != self.audio_generation:
                    log("stt_audio_gap")
                    await close_cloud()
                if cloud is None:
                    if now < retry_at:
                        await asyncio.sleep(0.1)
                        continue
                    self.stt_state = "connecting"
                    # Connection setup is bounded to <40 s. Do not start it
                    # just before the independent no-output deadline expires.
                    if 600 - (now - self.watchdog.last_output) < 40:
                        await asyncio.sleep(0.1)
                        continue
                    candidate = self.cloud_type(self.credentials, self.pending)
                    cloud = candidate
                    generation += 1
                    receive_generation = generation
                    stream_audio_generation = self.audio_generation
                    try:
                        await candidate.open(on_text)
                        # Discard connection-time backlog. Keep the latest 1 s.
                        while not self.outgoing.empty():
                            self.outgoing.get_nowait()
                        for packet in self.pre_roll:
                            replace_queue(self.outgoing, packet)
                        stream_origin_us = self.pre_roll[0][1] - 100000 if self.pre_roll else self.audio_us
                        last_sent_us = stream_origin_us
                        self.stt_state = "listening"
                        failures = 0
                        log("stt_started", provider=self.config.get("stt_provider", "tingwu"))
                    except Exception as error:
                        self.stt_state = "error"
                        log("stt_connect_failed", error=type(error).__name__)
                        await close_cloud()
                        failures += 1
                        retry_at = time.monotonic() + min(60, 2 ** min(failures, 6))
                        continue
                try:
                    gen, stamp, data = await asyncio.wait_for(self.outgoing.get(), 0.2)
                    if gen != stream_audio_generation:
                        continue
                    if last_sent_us > stream_origin_us and abs(stamp - last_sent_us - 100000) > 150000:
                        raise RuntimeError("Audio queue gap")
                    await cloud.send(data)
                    last_sent_us = stamp
                    # Pre-roll is sent at real time; the queue is bounded.
                    await asyncio.sleep(0.095)
                except asyncio.TimeoutError:
                    continue
                except Exception as error:
                    self.stt_state = "error"
                    log("stt_stream_failed", error=type(error).__name__)
                    await close_cloud()
                    retry_at = time.monotonic() + 2
        finally:
            await close_cloud()

    async def camera_loop(self):
        while not self.stop.is_set() and not self.audio_stop.is_set():
            try:
                seq, stamp, jpeg = await asyncio.to_thread(self.robot.frame)
                if not self.camera or seq != self.camera[0]:
                    self.camera = (seq, time.monotonic(), stamp, jpeg)
            except Exception:
                self.camera = None
            await asyncio.sleep(1)

    async def inference_loop(self, engine):
        loop = asyncio.get_running_loop()
        while not self.stop.is_set():
            now = time.monotonic()
            speech = self.transcript if self.transcript and now - self.transcript[0] < 12 else None
            camera = self.camera if self.camera and now - self.camera[1] < 3 else None
            boot = self.robot.boot
            if boot and not self.paused and (speech or camera):
                try:
                    label, source = await loop.run_in_executor(self.model_pool, engine.infer,
                        speech[2] if speech else "", camera[3] if camera else None)
                    stamps, times = [], []
                    if source in ("text", "both") and speech:
                        stamps.append(speech[1]); times.append(speech[0])
                    if source in ("vision", "both") and camera:
                        stamps.append(camera[2]); times.append(camera[1])
                    if self.robot.boot == boot and not self.paused:
                        self.result = (min(times) if times else now, min(stamps) if stamps else 0,
                                       label, source, boot)
                        self.inferences += 1
                except Exception as error:
                    self.result = None
                    log("inference_failed", error=type(error).__name__)
            await asyncio.sleep(3)

    async def publish_loop(self):
        session, seq = uuid.uuid4().hex, 0
        while not self.stop.is_set() and not self.audio_stop.is_set():
            seq += 1
            now = time.monotonic()
            result = self.result
            latest_robot_us = max(self.audio_us, self.camera[2] if self.camera else 0)
            fresh = (result and result[4] == self.robot.boot and now - result[0] < EVIDENCE_TTL
                     and 0 <= latest_robot_us - result[1] < EVIDENCE_TTL * 1000000)
            label, source, stamp = (result[2], result[3], result[1]) if fresh else ("unknown", "none", 0)
            payload = {"boot_id": self.robot.boot, "session_id": session, "seq": seq,
                       "observed_at_us": stamp, "emotion": label, "evidence": source,
                       "stt_state": self.stt_state, "inferred": True}
            try:
                if not self.config.get("mac_preview", False):
                    await asyncio.to_thread(self.robot.publish, payload)
                self.status_file.write_text(json.dumps({"at": time.time(), "connected": True,
                    "emotion": label, "evidence": source, "stt_state": self.stt_state,
                    "stt_outputs": self.stt_outputs, "inferences": self.inferences,
                    "audio_gaps": self.dropped_audio, "stop_pending": self.pending.exists()}))
            except Exception as error:
                log("publish_failed", error=type(error).__name__)
            await asyncio.sleep(2)

    async def run(self):
        loop = asyncio.get_running_loop()
        engine = await loop.run_in_executor(self.model_pool, functools.partial(LocalInference,
            self.config["model_path"], self.config["face_model_path"]))
        log("local_model_ready")
        self.engine = engine
        self.watchdog = OutputWatchdog(time.monotonic())
        long_tasks = [asyncio.create_task(self.stt_loop()), asyncio.create_task(self.inference_loop(engine))]
        try:
            while not self.stop.is_set():
                try:
                    await asyncio.to_thread(self.robot.verify, not self.config.get("mac_preview", False))
                    log("robot_verified")
                    self.audio_stop.clear()
                    capture = asyncio.create_task(asyncio.to_thread(self.capture, loop))
                    tasks = [asyncio.create_task(self.audio_loop()), asyncio.create_task(self.camera_loop()),
                             asyncio.create_task(self.publish_loop())]
                    while not self.stop.is_set() and not capture.done():
                        await asyncio.sleep(0.2)
                    self.audio_stop.set()
                    await asyncio.gather(capture, *tasks, return_exceptions=True)
                except Exception as error:
                    log("robot_unavailable", error=type(error).__name__)
                self.robot.boot = None
                self.camera = self.transcript = self.result = None
                self.audio_at = 0
                self.audio_generation += 1
                self.pre_roll.clear()
                while not self.raw.empty():
                    self.raw.get_nowait()
                if not self.stop.is_set():
                    await asyncio.sleep(3)
        finally:
            self.stop.set()
            self.audio_stop.set()
            await asyncio.gather(*long_tasks, return_exceptions=True)
            self.model_pool.shutdown(wait=True)
            self.instance_lock.close()
            self.status_file.write_text(json.dumps({"at": time.time(), "connected": False,
                                                   "stop_pending": self.pending.exists()}))
            log("service_stopped")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--vision-only", action="store_true", help="explicitly run without cloud STT")
    parser.add_argument("--preview-port", type=int, default=8765)
    args = parser.parse_args()
    os.umask(0o077)
    config = json.loads(args.config.expanduser().read_text())
    app = App(config, args.vision_only)
    from .preview import serve
    runner = await serve(app, args.preview_port)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, app.stop.set)
    try:
        await app.run()
    except Exception as error:
        log("service_failed", error=type(error).__name__)
        raise SystemExit(1) from None
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
