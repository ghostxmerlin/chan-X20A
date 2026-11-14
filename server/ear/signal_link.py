"""Owner-signal uplink from the DualKey (2026-11-13): ws://<ear>/signal, same token as hearing.

The ear only relays: each signal is forwarded to mind (POST /signal on the loopback) and acknowledged to
the device only after mind stored it, so a signal is never lost silently; the device resends unacked ones.
  {"type":"hello","device":"dualkey-<mac6>","fw":"…","boot":"<hex>"}          first message
  {"type":"signal","seq":N,"age_ms":A,"kind":"rating|mode|click","value":{…}}
  {"type":"ping"}
Replies: {"type":"hello","ok":true}, {"type":"ack","seq":N}, {"type":"error",…}. A link silent for 60 s closes.
"""
import asyncio
import json
import re
import urllib.error
import urllib.request

IDLE_TIMEOUT = 60
MIND_URL = "http://127.0.0.1:52740/signal"


def mind_poster(token, url=MIND_URL, timeout=5):
    """post(body) -> (status, reply dict); never raises. Used from a worker thread."""
    def post(body):
        req = urllib.request.Request(url, method="POST", data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            with e:
                try:
                    return e.code, json.loads(e.read() or b"{}")
                except ValueError:
                    return e.code, {}
        except Exception as e:                       # noqa: BLE001 - mind down: no ack, the device resends
            return 503, {"error": type(e).__name__}
    return post


def _clean(value, n=64):
    return re.sub(r"[^A-Za-z0-9_.-]", "", str(value or ""))[:n]


class SignalLink:
    SEND_TIMEOUT = 2.0                  # a stuck DualKey must not hold the mode hub (ratings use their own path)

    def __init__(self, post, peer, ws=None):
        self.post, self.peer, self.ws = post, peer, ws
        self.device = self.boot = self.fw = None
        self.signals = 0
        self.mode_seq = None            # seq of the last snapshot pushed to this DualKey

    # ---- mode light (A6): the ModeHub pushes through these ----
    @staticmethod
    def mode_message(snap):
        """{"type":"mode","current":<id>} for the DualKey's joystick light; idle = light off (firmware decides)."""
        mode = snap.get("mode") if isinstance(snap, dict) else None
        return {"type": "mode", "current": mode} if isinstance(mode, str) and mode else None

    @property
    def mode_wants_first(self):
        return bool(self.device) and self.mode_seq is None

    async def push_mode(self, snap, force=False):
        if not isinstance(snap, dict) or self.ws is None or not self.device:
            return
        if not force and snap.get("seq") == self.mode_seq:
            return
        msg = self.mode_message(snap)
        if msg is None:
            return
        try:
            await asyncio.wait_for(self.ws.send(json.dumps(msg)), self.SEND_TIMEOUT)
        except Exception:                               # noqa: BLE001 - gone or stuck: the serve loop ends the link
            return
        self.mode_seq = snap.get("seq")

    async def on_text(self, text):
        try:
            msg = json.loads(text)
        except ValueError:
            return {"type": "error", "message": "bad json"}
        if not isinstance(msg, dict):
            return {"type": "error", "message": "bad message"}
        kind = msg.get("type")
        if kind == "hello":
            self.device, self.boot, self.fw = _clean(msg.get("device")), _clean(msg.get("boot")), _clean(msg.get("fw"))
            if not self.device.startswith("dualkey-"):
                return {"type": "error", "message": "device must be dualkey-<id>"}
            return {"type": "hello", "ok": True}
        if kind == "ping":
            return None
        if kind != "signal":
            return {"type": "error", "message": f"unknown type {str(kind)[:32]}"}
        if not self.device:
            return {"type": "error", "message": "hello first"}
        seq = msg.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
            return {"type": "error", "message": "signal needs integer seq"}
        body = {"kind": msg.get("kind"), "value": msg.get("value"), "age_ms": msg.get("age_ms"),
                "source": {"device": self.device, "boot": self.boot, "seq": seq, "fw": self.fw}}
        status, reply = await asyncio.to_thread(self.post, body)
        if status == 200:
            self.signals += 1
            return {"type": "ack", "seq": seq}
        if status == 400:
            return {"type": "error", "seq": seq, "message": str(reply.get("error", "rejected"))[:120]}
        return {"type": "error", "seq": seq, "message": "mind unavailable"}


async def serve_link(ws, post, peer, hub=None):
    """hub: the ear's ModeHub; the link joins it after its hello reply, so the light follows the mode."""
    link = SignalLink(post, peer, ws=ws)
    joined = False
    try:
        while True:
            try:
                data = await asyncio.wait_for(ws.recv(), IDLE_TIMEOUT)
            except asyncio.TimeoutError:
                await ws.close(code=1000, reason="idle")
                break
            if isinstance(data, bytes):
                await ws.send(json.dumps({"type": "error", "message": "text only"}))
                continue
            reply = await link.on_text(data)
            if reply:
                await ws.send(json.dumps(reply))
            if hub is not None and not joined and link.device and reply and reply.get("type") == "hello":
                joined = True
                hub.join(link)
    except Exception:                                   # noqa: BLE001 - connection closed or broken
        pass
    finally:
        if joined:
            hub.leave(link)
    return link
