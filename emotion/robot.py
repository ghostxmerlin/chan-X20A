"""Bound robot transport: proof before Bearer, separate bounded audio stream."""
from dataclasses import dataclass
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import socket
import struct
import time
import threading
import functools
from urllib.parse import urlsplit

import requests


def serialized(method):
    @functools.wraps(method)
    def call(self, *args, **kwargs):
        with self.http_lock:
            return method(self, *args, **kwargs)
    return call


@dataclass(frozen=True)
class AudioBlock:
    boot: str
    seq: int
    timestamp_us: int
    pcm: bytes


def read_exact(stream, count):
    result = bytearray()
    while len(result) < count:
        data = stream.read(count - len(result))
        if not data:
            raise EOFError("Robot audio stream ended")
        result.extend(data)
    return bytes(result)


def decode_header(data):
    magic, seq, stamp, rate, frames, channels, bits, size = struct.unpack("<4sIQIIHHI", data)
    if (magic, rate, frames, channels, bits, size) != (b"SCA1", 24000, 480, 2, 16, 1920):
        raise ValueError("Unsupported robot audio format")
    return seq, stamp, size


class Robot:
    def __init__(self, config):
        self.config = config
        self.token = Path(config["token_file"]).expanduser().read_text().strip()
        self.device_id = config["device_id"]
        self.base = Path(config["address_file"]).expanduser().read_text().strip().rstrip("/")
        self.boot = None
        self.session = requests.Session()
        self.session.trust_env = False
        self.http_lock = threading.RLock()  # At most one Mac main-HTTP socket.

    def proof_ok(self, data, nonce):
        digest = hmac.new(self.token.encode(),
            f"chan-discovery-v1\n{self.device_id}\n{nonce}".encode(), hashlib.sha256).hexdigest()
        return (data.get("device_id") == self.device_id and data.get("nonce") == nonce
                and hmac.compare_digest(str(data.get("proof", "")), digest))

    def discover(self):
        nonce = secrets.token_hex(16)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
            peer.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            peer.settimeout(0.3)
            wire = json.dumps({"protocol": 1, "device_id": self.device_id, "nonce": nonce}).encode()
            peer.sendto(wire, ("255.255.255.255", 8082))
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                try:
                    body, address = peer.recvfrom(513)
                    data = json.loads(body)
                    if data.get("protocol") == 1 and data.get("port") == 8080 and self.proof_ok(data, nonce):
                        self.base = f"http://{address[0]}:8080"
                        return
                except (socket.timeout, ValueError):
                    continue
        raise ConnectionError("Bound robot not discovered")

    @serialized
    def verify(self, require_emotion=True):
        self.boot = None
        for attempt in range(2):
            try:
                parts = urlsplit(self.base)
                if parts.scheme != "http" or parts.port != 8080 or parts.username or parts.path not in ("", "/"):
                    raise ValueError("Invalid robot base address")
                nonce = secrets.token_hex(16)
                with self.session.get(self.base + "/v1/discovery", params={"nonce": nonce},
                                      timeout=3, allow_redirects=False) as response:
                    response.raise_for_status()
                    if not self.proof_ok(response.json(), nonce):
                        raise ValueError("Robot proof mismatch")
                info = self.get_json("/v1/info")
                if info["device_id"] != self.device_id:
                    raise ValueError("Robot identity changed")
                if require_emotion and (info.get("audio_max_clients", 1) < 2 or info.get("emotion", {}).get("endpoint") != "/v1/emotion"):
                    raise RuntimeError("Install emotion firmware before starting the service")
                if not require_emotion and info.get("audio_max_clients", 1) < 2:
                    if self.get_json("/v1/telemetry").get("health", {}).get("audio_client"):
                        raise RuntimeError("Legacy robot audio is already in use")
                self.boot = info["boot_id"]
                return info
            except (requests.RequestException, ValueError):
                if attempt:
                    raise
                self.discover()

    @property
    def headers(self):
        return {"Authorization": "Bearer " + self.token}

    @serialized
    def get_json(self, path):
        with self.session.get(self.base + path, headers=self.headers, timeout=3, allow_redirects=False) as response:
            response.raise_for_status()
            return response.json()

    @serialized
    def frame(self):
        with self.session.get(self.base + "/v1/frame", headers=self.headers, timeout=3,
                              allow_redirects=False) as response:
            response.raise_for_status()
            if response.headers.get("X-Boot-Id") != self.boot:
                raise ConnectionError("Robot restarted")
            return (int(response.headers["X-Seq"]), int(response.headers["X-Timestamp-Us"]), response.content)

    @serialized
    def publish(self, data):
        with self.session.post(self.base + "/v1/emotion", headers=self.headers, json=data,
                               timeout=3, allow_redirects=False) as response:
            response.raise_for_status()
            return response.json()

    def audio(self, stop):
        # requests.Session is kept thread-local: camera/status use the other one.
        with requests.Session() as session:
            session.trust_env = False
            host = urlsplit(self.base).hostname
            with session.get(f"http://{host}:8081/v1/audio", headers=self.headers,
                             timeout=(3, 3), stream=True, allow_redirects=False) as response:
                response.raise_for_status()
                if response.headers.get("X-Boot-Id") != self.boot:
                    raise ConnectionError("Robot restarted")
                while not stop.is_set():
                    seq, stamp, size = decode_header(read_exact(response.raw, 32))
                    yield AudioBlock(self.boot, seq, stamp, read_exact(response.raw, size))
