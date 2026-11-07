"""Keep everything the ear receives, for later review. Nothing here ever deletes.

One directory per connection:  <root>/<YYYYMMDD>/<HHMMSS>-<source>-c<n>/
  meta.json      who/what/when: peer, source, audio format, models and thresholds, start/end, counts
  audio.opus     Opus clients: the packets exactly as received, in a standard Ogg Opus file
                 (plays in VLC/ffmpeg/browsers; sample positions = the ear's "pos" sample indexes)
  audio.wav      PCM16 clients: the samples exactly as received
  events.jsonl   every event the ear sent (speech, sound, enroll, errors, ...), with the tick that carried it
  ticks.jsonl    every 100 ms heartbeat (without its events)
  client.jsonl   every text message the client sent except pings (audio declaration, self flags, config, enroll)
Every file is flushed as it grows, so a crash or disconnect loses at most about a second.
"""
import json
import os
import re
import struct
import time
import wave
from datetime import datetime

SR = 16000


def _crc_table():
    t = []
    for i in range(256):
        r = i << 24
        for _ in range(8):
            r = ((r << 1) ^ 0x04C11DB7) if r & 0x80000000 else (r << 1)
        t.append(r & 0xFFFFFFFF)
    return t


_CRC = _crc_table()


def _ogg_crc(data):
    c = 0
    for b in data:
        c = ((c << 8) & 0xFFFFFFFF) ^ _CRC[((c >> 24) & 0xFF) ^ b]
    return c


class OggOpusWriter:
    """Minimal Ogg Opus muxer (RFC 7845), mono. Packets are grouped about one page per second."""

    # 0 on purpose: decoding the file then gives exactly the samples the ear decoded, so the
    # "pos" sample indexes in events.jsonl line up with the audio without any offset
    PRE_SKIP = 0

    def __init__(self, path, input_rate=SR, comments=()):
        self.f = open(path, "wb")
        self.serial = int.from_bytes(os.urandom(4), "little")
        self.seq = 0
        self.granule = 0        # in 48 kHz samples, as Ogg Opus requires
        self.packets = []
        head = b"OpusHead" + struct.pack("<BBHIhB", 1, 1, self.PRE_SKIP, input_rate, 0, 0)
        vendor = b"soul ear recorder"
        tags = b"OpusTags" + struct.pack("<I", len(vendor)) + vendor + struct.pack("<I", len(comments))
        for c in comments:
            c = c.encode("utf-8")
            tags += struct.pack("<I", len(c)) + c
        self._page([head], 0, bos=True)
        self._page([tags], 0)

    def _page(self, packets, granule, bos=False, eos=False):
        lacing = bytearray()
        for p in packets:
            n = len(p)
            lacing += b"\xff" * (n // 255) + bytes([n % 255])
        hdr = struct.pack("<4sBBqIIIB", b"OggS", 0, (2 if bos else 0) | (4 if eos else 0), granule,
                          self.serial, self.seq, 0, len(lacing)) + bytes(lacing)
        body = b"".join(packets)
        crc = _ogg_crc(hdr + body)
        hdr = hdr[:22] + struct.pack("<I", crc) + hdr[26:]
        self.f.write(hdr + body)
        self.f.flush()
        self.seq += 1

    def write(self, packet, samples16k):
        self.packets.append(packet)
        self.granule += samples16k * 3
        lacing = sum(len(p) // 255 + 1 for p in self.packets)
        if len(self.packets) >= 17 or lacing > 200:          # ~1 s of 60 ms packets per page
            self._page(self.packets, self.granule)
            self.packets = []

    def close(self):
        if not self.f.closed:
            self._page(self.packets, self.granule, eos=True)
            self.f.close()


class Recorder:
    def __init__(self, root, conn_no, peer, hello):
        self.root, self.conn_no, self.peer, self.hello = root, conn_no, peer, hello
        self.dir = None
        self.audio = None
        self.kind = None
        self.files = {}
        self.meta = {}
        self.t0 = time.time()
        self.counts = {"packets": 0, "samples": 0, "bytes": 0, "events": 0, "ticks": 0, "client_msgs": 0}
        self.early = []          # ticks sent before the client said anything (e.g. the hello), kept for the files

    def _open(self, source):
        if self.dir:
            return
        self._open_dir(source)
        early, self.early = self.early, []
        for tick, events in early:
            self.tick(tick, events)

    def _open_dir(self, source):
        now = datetime.fromtimestamp(self.t0)
        safe = re.sub(r"[^0-9A-Za-z._-]+", "_", source or "unknown")[:40]
        self.dir = os.path.join(self.root, now.strftime("%Y%m%d"), f"{now.strftime('%H%M%S')}-{safe}-c{self.conn_no}")
        os.makedirs(self.dir, exist_ok=True)
        for name in ("events", "ticks", "client"):
            self.files[name] = open(os.path.join(self.dir, f"{name}.jsonl"), "a", encoding="utf-8")
        self.meta = {"source": source or "unknown", "peer": self.peer, "connection": self.conn_no,
                     "start": now.isoformat(timespec="milliseconds"), "start_ms": int(self.t0 * 1000),
                     "server_hello": self.hello, "audio": None}
        self._write_meta()

    def _write_meta(self, final=False):
        m = dict(self.meta, counts=self.counts)
        if final:
            m["end"] = datetime.now().isoformat(timespec="milliseconds")
            m["end_ms"] = int(time.time() * 1000)
        tmp = os.path.join(self.dir, "meta.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, indent=1)
        os.replace(tmp, os.path.join(self.dir, "meta.json"))

    def declare(self, msg):
        """The client's {"type":"audio",...}: format and source of what follows."""
        self._open(msg.get("source"))
        if self.audio is None:
            self.meta["audio"] = {k: msg.get(k) for k in ("format", "sample_rate", "channels", "frame_ms", "source", "framing", "device")}
            self._write_meta()

    def audio_in(self, raw, pcm16, is_opus):
        """raw: bytes as received; pcm16: the decoded samples (bytes) the ear actually used."""
        self._open(None)
        if self.audio is None:
            if is_opus:
                self.audio = OggOpusWriter(os.path.join(self.dir, "audio.opus"),
                                           input_rate=(self.meta.get("audio") or {}).get("sample_rate", SR),
                                           comments=[f"SOURCE={self.meta['source']}", f"START={self.meta['start']}"])
                self.kind = "opus"
            else:
                self.audio = wave.open(os.path.join(self.dir, "audio.wav"), "wb")
                self.audio.setnchannels(1)
                self.audio.setsampwidth(2)
                self.audio.setframerate(SR)
                self.kind = "pcm"
            if self.meta.get("audio") is None:
                self.meta["audio"] = {"format": self.kind}
            self.meta["audio"]["file"] = os.path.basename(self.audio.f.name if is_opus else "audio.wav")
            self._write_meta()
        n = len(pcm16) // 2
        if self.kind == "opus":
            self.audio.write(raw, n)
        else:
            self.audio.writeframes(pcm16)                     # wave patches the header on every write
        self.counts["packets"] += 1
        self.counts["samples"] += n
        self.counts["bytes"] += len(raw)

    def tick(self, tick, events):
        if not self.dir:
            if len(self.early) < 300:                          # 30 s; a client that never speaks is not recorded
                self.early.append((tick, events))
            return
        seq = tick.get("seq")
        for ev in events:
            self.files["events"].write(json.dumps(dict(ev, _tick=seq), ensure_ascii=False) + "\n")
        self.counts["events"] += len(events)
        t = {k: v for k, v in tick.items() if k != "events"}
        t["n_events"] = len(events)
        self.files["ticks"].write(json.dumps(t, ensure_ascii=False) + "\n")
        self.counts["ticks"] += 1
        for f in self.files.values():
            f.flush()
        if self.counts["ticks"] % 600 == 0:                   # refresh counts in meta once a minute
            self._write_meta()

    def client_msg(self, msg):
        if msg.get("type") == "ping":
            return
        self._open(msg.get("source") if msg.get("type") == "audio" else None)
        self.files["client"].write(json.dumps({"t": int(time.time() * 1000), "msg": msg}, ensure_ascii=False) + "\n")
        self.counts["client_msgs"] += 1

    def close(self):
        if not self.dir:
            return
        try:
            if self.audio is not None:
                self.audio.close()
        finally:
            for f in self.files.values():
                f.close()
            self._write_meta(final=True)
