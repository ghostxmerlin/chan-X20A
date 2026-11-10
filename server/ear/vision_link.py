"""Vision uplink from the robot (2026-11-10): ws://<ear>/vision, same token as hearing.

The robot opens this connection on its own, independent of continuous hearing, whenever its M12
is online or vision events are waiting. Messages:
  {"type":"hello","source":"stackchan-<mac>","device":{...}}   first message; opens a session
  {"type":"vision","event":"face_found","seq":N,"t_us":...,"now_us":...,...}   one change event
  {"type":"ping"}
Each connection is one row in hello.sessions (kind "vision", key "YYYYMMDD/HHMMSS-source-vN") and
its events go to hello.vision. Nothing is answered except errors. A link silent for 60 s is closed.
"""
import asyncio
import json
import re
import time

IDLE_TIMEOUT = 60


def safe_source(value):
    text = re.sub(r"[^A-Za-z0-9_-]", "", str(value or ""))[:64]
    return text or "unknown"


def session_key(source, number, epoch=None):
    t = time.localtime(epoch if epoch is not None else time.time())
    return f"{time.strftime('%Y%m%d', t)}/{time.strftime('%H%M%S', t)}-{safe_source(source)}-v{number}"


class VisionLink:
    def __init__(self, db, number, peer):
        self.db, self.number, self.peer = db, number, peer
        self.session = None
        self.events = 0
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

    def close(self):
        if self.db is not None and self.session:
            self.db.session_update(self.session, {"end_ms": int(time.time() * 1000), "vision_events": self.events})


async def serve_link(ws, db, number, peer):
    link = VisionLink(db, number, peer)
    try:
        while True:
            try:
                data = await asyncio.wait_for(ws.recv(), IDLE_TIMEOUT)
            except asyncio.TimeoutError:
                await ws.close(code=1000, reason="idle")
                break
            if isinstance(data, bytes):
                continue
            reply = link.on_text(data)
            if reply:
                await ws.send(json.dumps(reply))
    except Exception:                                   # noqa: BLE001 - connection closed or broken
        pass
    finally:
        link.close()
    return link
