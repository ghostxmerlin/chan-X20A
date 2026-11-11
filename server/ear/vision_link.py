"""Vision uplink from the robot (2026-11-10): ws://<ear>/vision, same token as hearing.

The robot opens this connection on its own, independent of continuous hearing, whenever its M12
is online or vision events are waiting. Messages:
  {"type":"hello","source":"stackchan-<mac>","device":{...}}   first message; opens a session
  {"type":"vision","event":"face_found","seq":N,"t_us":...,"now_us":...,...}   one change event
  {"type":"ping"}
  binary "SVF1" + u16le header length + JSON {"type":"frame","seq","t_us","now_us","w","h","diff","bytes"}
         + JPEG: one body-camera frame (collector-0.4.75+), saved under <recordings>/frames/<session>/
Each connection is one row in hello.sessions (kind "vision", key "YYYYMMDD/HHMMSS-source-vN") and
its events go to hello.vision. Nothing is answered except errors. A link silent for 60 s is closed.
"""
import asyncio
import json
import os
import re
import struct
import time

IDLE_TIMEOUT = 60
FRAME_MAGIC = b"SVF1"
FRAME_HEAD_MAX = 512
FRAME_MAX = 32768


def safe_source(value):
    text = re.sub(r"[^A-Za-z0-9_-]", "", str(value or ""))[:64]
    return text or "unknown"


def session_key(source, number, epoch=None):
    t = time.localtime(epoch if epoch is not None else time.time())
    return f"{time.strftime('%Y%m%d', t)}/{time.strftime('%H%M%S', t)}-{safe_source(source)}-v{number}"


class VisionLink:
    def __init__(self, db, number, peer, recordings_root=None):
        self.db, self.number, self.peer = db, number, peer
        self.recordings_root = recordings_root
        self.session = None
        self.events = 0
        self.frames = 0
        self.start_ms = int(time.time() * 1000)

    def on_text(self, text):
        """Handle one message; returns an error dict to send back, or None."""
        try:
            msg = json.loads(text)
        except ValueError:
            return {"type": "error", "message": "bad json"}
        if not isinstance(msg, dict):
            return {"type": "error", "message": "bad message"}
        kind = msg.get("type")
        if kind == "hello":
            if self.session is None:
                self.session = session_key(msg.get("source"), self.number, self.start_ms / 1000)
                if self.db is not None:
                    self.db.session_open(self.session, {
                        "kind": "vision", "source": msg.get("source"), "peer": self.peer, "start_ms": self.start_ms,
                        "device": msg.get("device") if isinstance(msg.get("device"), dict) else None})
            return None
        if kind == "vision":
            if self.session is None:
                return {"type": "error", "message": "hello first"}
            if not isinstance(msg.get("event"), str) or len(msg["event"]) > 32:
                return {"type": "error", "message": "vision needs an event name"}
            self.events += 1
            if self.db is not None:
                self.db.vision(self.session, msg, int(time.time() * 1000))
            return None
        if kind == "ping":
            return None
        return {"type": "error", "message": f"unknown type {str(kind)[:32]}"}

    def on_binary(self, data):
        """One body-camera frame; returns an error dict to send back, or None. Nothing is written on error."""
        if self.session is None:
            return {"type": "error", "message": "hello first"}
        if self.recordings_root is None:
            return {"type": "error", "message": "frames not stored here"}
        if len(data) < 6 or len(data) > FRAME_MAX or data[:4] != FRAME_MAGIC:
            return {"type": "error", "message": "bad frame"}
        (head_len,) = struct.unpack_from("<H", data, 4)
        if head_len > FRAME_HEAD_MAX or 6 + head_len > len(data):
            return {"type": "error", "message": "bad frame header length"}
        try:
            head = json.loads(data[6:6 + head_len])
        except ValueError:
            return {"type": "error", "message": "bad frame header"}
        ints = ("seq", "t_us", "now_us")
        if not isinstance(head, dict) or head.get("type") != "frame" or \
                not all(isinstance(head.get(k), int) and not isinstance(head.get(k), bool) and head[k] >= 0 for k in ints):
            return {"type": "error", "message": "frame header needs integer seq, t_us, now_us"}
        jpeg = data[6 + head_len:]
        if not jpeg.startswith(b"\xff\xd8"):
            return {"type": "error", "message": "frame is not a JPEG"}
        rel = f"frames/{self.session}/{head['t_us']}-{head['seq']}.jpg"
        path = os.path.join(self.recordings_root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            f.write(jpeg)
        os.replace(tmp, path)
        received = int(time.time() * 1000)
        wait_ms = max(0, (head["now_us"] - head["t_us"]) // 1000)
        num = lambda k: head.get(k) if isinstance(head.get(k), int) and not isinstance(head.get(k), bool) else None  # noqa: E731
        self.frames += 1
        if self.db is not None:
            self.db.frame({"session": self.session, "seq": head["seq"], "t_us": head["t_us"], "at": received - wait_ms,
                           "received": received, "path": rel, "bytes": len(jpeg), "w": num("w"), "h": num("h"),
                           "diff": num("diff")})
        return None

    def close(self):
        if self.db is not None and self.session:
            self.db.session_update(self.session, {"end_ms": int(time.time() * 1000), "vision_events": self.events,
                                                  "frames": self.frames})


async def serve_link(ws, db, number, peer, recordings_root=None):
    link = VisionLink(db, number, peer, recordings_root)
    try:
        while True:
            try:
                data = await asyncio.wait_for(ws.recv(), IDLE_TIMEOUT)
            except asyncio.TimeoutError:
                await ws.close(code=1000, reason="idle")
                break
            reply = link.on_binary(data) if isinstance(data, bytes) else link.on_text(data)
            if reply:
                await ws.send(json.dumps(reply))
    except Exception:                                   # noqa: BLE001 - connection closed or broken
        pass
    finally:
        link.close()
    return link
