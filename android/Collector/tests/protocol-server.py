#!/usr/bin/env python3
"""Synthetic local protocol fixture. Never reads a camera, microphone or real sensor."""
import argparse
import io
import json
import math
from pathlib import Path
import secrets
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from PIL import Image, ImageDraw

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=18080)
parser.add_argument("--audio-port", type=int, default=18081)
parser.add_argument("--token-file", type=Path, required=True)
parser.add_argument("--reboot-after", type=float, default=0)
parser.add_argument("--drop-audio-every", type=int, default=0)
args = parser.parse_args()
args.token_file.parent.mkdir(parents=True, exist_ok=True)
if not args.token_file.exists():
    args.token_file.write_text(secrets.token_hex(16))
    args.token_file.chmod(0o600)
token = args.token_file.read_text().strip()
assert len(token) == 32 and all(c in "0123456789abcdefABCDEF" for c in token)
state_lock = threading.Lock()
audio_lock = threading.Lock()
state = {"start": time.monotonic(), "boot": "synthetic-boot-1", "reboots": 0}


def clock():
    with state_lock:
        return state["boot"], time.monotonic() - state["start"]


def reboot():
    with state_lock:
        state["reboots"] += 1
        state["boot"] = f"synthetic-boot-{state['reboots']+1}"
        state["start"] = time.monotonic()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def response(self, status, payload, mime="application/json", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(payload)))
        for key, value in (headers or {}).items():
            self.send_header(key, str(value))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.headers.get("Authorization") != f"Bearer {token}":
            self.response(401, b'{"error":"unauthorized"}')
            return
        boot, elapsed = clock()
        timestamp = int(elapsed * 1e6)
        if self.path == "/v1/info":
            value = dict(protocol_version=1, device_id="synthetic-test-device", boot_id=boot,
                         firmware="synthetic-fixture", capabilities=["camera", "audio", "imu", "touch"],
                         audio_port=args.audio_port, audio=dict(sample_rate=24000, channels=2, bits=16,
                         channel_roles=["microphone", "reference"]), camera=dict(width=320, height=240))
            self.response(200, json.dumps(value).encode())
        elif self.path == "/v1/telemetry":
            value = dict(boot_id=boot, seq=int(elapsed * 10), timestamp_us=timestamp,
                         imu=dict(valid=True, timestamp_us=timestamp, accel=[math.sin(elapsed), 0.03, 1],
                                  gyro=[0, 0, 0], accel_unit="g", gyro_unit="deg/s"),
                         head_touch=dict(valid=True, timestamp_us=timestamp, values=[0, int(elapsed) % 2, 0]),
                         touch=dict(valid=True, count=0, x=0, y=0),
                         power=dict(valid=True, battery_percent=87, charging=True),
                         health=dict(heap_free=231456, psram_free=5500000))
            self.response(200, json.dumps(value).encode())
        elif self.path == "/v1/frame":
            seq = int(elapsed * 2)
            picture = Image.new("RGB", (320, 240), "#193F59")
            draw = ImageDraw.Draw(picture)
            draw.rectangle((16, 16, 304, 224), outline="#8FCED4", width=3)
            draw.text((30, 40), "SYNTHETIC TEST FRAME", fill="white")
            draw.text((30, 70), f"{boot} / frame {seq}", fill="white")
            x = 30 + int(elapsed * 20) % 230
            draw.ellipse((x, 120, x + 30, 150), fill="#F5C564")
            buffer = io.BytesIO()
            picture.save(buffer, format="JPEG", quality=75)
            self.response(200, buffer.getvalue(), "image/jpeg", {"X-Boot-Id": boot, "X-Seq": seq,
                          "X-Timestamp-Us": timestamp, "X-Width": 320, "X-Height": 240})
        elif self.path == "/v1/audio":
            if not audio_lock.acquire(blocking=False):
                self.response(503, b'{"error":"audio_busy"}')
                return
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.send_header("X-Boot-Id", boot)
                self.end_headers()
                seq = int(elapsed * 50)
                start = time.monotonic()
                count = 0
                while clock()[0] == boot:
                    count += 1
                    seq += 1
                    ts = int(clock()[1] * 1e6)
                    if not args.drop_audio_every or seq % args.drop_audio_every:
                        pcm = b"".join(struct.pack("<hh", int(1800 * math.sin((seq * 480 + i) * 2 * math.pi * 440 / 24000)), 0) for i in range(480))
                        packet = struct.pack("<4sIQIIHHI", b"SCA1", seq, ts, 24000, 480, 2, 16, len(pcm)) + pcm
                        # Split every frame into multiple HTTP chunks to exercise readFully.
                        for part in (packet[:11], packet[11:47], packet[47:]):
                            self.wfile.write(f"{len(part):x}\r\n".encode() + part + b"\r\n")
                        self.wfile.flush()
                    time.sleep(max(0, start + count * .02 - time.monotonic()))
                self.wfile.write(b"0\r\n\r\n")
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                audio_lock.release()
        elif self.path == "/test/reboot":
            reboot()
            self.response(200, b'{"ok":true}')
        else:
            self.response(404, b'{"error":"not_found"}')


if args.reboot_after:
    timer = threading.Timer(args.reboot_after, reboot)
    timer.daemon = True
    timer.start()
servers = [ThreadingHTTPServer((args.host, port), Handler) for port in (args.port, args.audio_port)]
for server in servers:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
print(f"Synthetic collector fixture on {args.host}:{args.port}; audio :{args.audio_port}. Token read from local file.", flush=True)
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    for server in servers:
        server.shutdown()
