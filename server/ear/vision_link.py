"""Vision uplink from the robot (2026-11-10): ws://<ear>/vision, same token as hearing.

The robot opens this connection on its own, independent of continuous hearing, whenever its M12
is online or vision events are waiting. Messages:
  {"type":"hello","source":"stackchan-<mac>","device":{...}}   first message; opens a session
  {"type":"vision","event":"face_found","seq":N,"t_us":...,"now_us":...,...}   one change event
  {"type":"vision","event":"m12_ev","ev":"nod","seq":N,"t_us":...,"now_us":...,"f":8542,"face":3,"n":2}
         one m12d 0.2.0 event line (collector-0.5.02+), its fields flat beside "ev" (the m12d type; only what the line
         had): face / person (ids; enter/leave/near/far keep the enter id), n (nod / shake count), side ("l"/"r", tilt),
         dur (s: uncover / leave / look_end), d ([before, after] m, near / far of a face), lum ([before, after] 0-255:
         cover / uncover / light), on (light), and for wake (standby only): area [x,y,w,h], amt (0..1), cd (cooldown
         s), pan / hot (true when set), faces [{box, name?, sim?}] and persons [[x,y,w,h]] (at most 3 each;
         faces_seen / persons_seen when m12d saw more). Boxes are 640x480. "ev" may be missing (the robot found no
         memory for it). Types: wake cover uncover light dark bright enter leave near far look look_end nod shake tilt
         smile; the robot drops any other. Stored like every vision event and one run-log row seen.m12 each; nothing
         reacts to them yet (owner 2026-11-17: 先呈现事件出来).
  {"type":"body",...} / {"type":"body_ev","ev":...}   the body sense (collector-0.5.12, body_sense.py): to on_body(kind,
         msg, session, source) when given; nothing stored here
  {"type":"ping"}
  binary "SVF1" + u16le header length + JSON {"type":"frame","seq","t_us","now_us","w","h","diff","bytes"}
         + JPEG: one body-camera frame (collector-0.4.75+), saved under <recordings>/frames/<session>/
Each connection is one row in hello.sessions (kind "vision", key "YYYYMMDD/HHMMSS-source-vN") and
its events go to hello.vision (and, when given, to on_event: the 围观 realtime hub's face view, 2026-11-14).
A stored frame goes to on_frame(head, jpeg, path, session) when given (视觉爆发 2026-11-15: a snap photo carries
"snap":<id> in its header, also kept in hello.frames as `snap`).
Nothing is answered except errors. A link silent for 60 s is closed.
"""
import asyncio
import json
import os
import re
import struct
import time

import ear_log
import bodies                  # server/common (ear_log puts it on sys.path in the repository)

IDLE_TIMEOUT = 60
# m12d event types -> the run log's sum (Chinese, like the page's labels in web/monitor/public/log-ui.js)
M12_EV_NAMES = {"wake": "待机唤醒", "cover": "捂住镜头", "uncover": "拿开遮挡", "light": "灯光变化", "dark": "变暗",
                "bright": "变亮", "enter": "有人来了", "leave": "有人走了", "near": "靠近", "far": "离远",
                "look": "脸朝着我", "look_end": "脸转开", "nod": "点头", "shake": "摇头", "tilt": "歪头", "smile": "笑了"}
M12_EV_EXTRA = ("ev", "f", "face", "person", "n", "side", "dur", "d", "lum", "on", "area", "amt", "cd", "pan", "hot",
                "faces", "persons", "faces_seen", "persons_seen")


def m12_ev_sum(m):
    """One m12_ev message -> a short Chinese line for the run log ("点头 脸#3 2 下")."""
    ev = m.get("ev")
    name = M12_EV_NAMES.get(ev, f"M12 {str(ev)[:16]}" if ev else "M12 事件")
    if ev == "light" and isinstance(m.get("on"), bool):
        name = "开灯" if m["on"] else "关灯"
    parts = [name]
    if isinstance(m.get("face"), int):
        parts.append(f"脸#{m['face']}")
    elif isinstance(m.get("person"), int):
        parts.append(f"人形#{m['person']}")
    num = lambda k: m.get(k) if isinstance(m.get(k), (int, float)) and not isinstance(m.get(k), bool) else None  # noqa: E731
    if num("n") is not None:
        parts.append(f"{num('n')} 下")
    if m.get("side") in ("l", "r"):
        parts.append("向画面左" if m["side"] == "l" else "向画面右")
    d = m.get("d")
    if isinstance(d, list) and len(d) == 2 and all(isinstance(v, (int, float)) for v in d):
        parts.append(f"{d[0]}→{d[1]} 米")
    if num("dur") is not None:
        parts.append(f"{num('dur')} 秒")
    if ev == "wake":
        faces, persons = m.get("faces"), m.get("persons")
        if m.get("pan") is True:
            parts.append("转动")
        if m.get("hot") is True:
            parts.append("过热未分析")
        elif isinstance(faces, list) or isinstance(persons, list):
            names = [f.get("name") for f in faces or [] if isinstance(f, dict) and isinstance(f.get("name"), str)]
            nf = m.get("faces_seen") if isinstance(m.get("faces_seen"), int) else len(faces or [])
            np_ = m.get("persons_seen") if isinstance(m.get("persons_seen"), int) else len(persons or [])
            parts.append(f"脸 {nf} 人形 {np_}" + (f"（{'、'.join(names)}）" if names else ""))
    return " ".join(str(p) for p in parts)[:120]
# the robot's camera / M12 state as its vision events report it (run log robot.state, only on a change)
STATE_EVENTS = {"m12_online": ("m12", "online"), "m12_offline": ("m12", "offline"),
                "vision_on": ("vision", "on"), "vision_off": ("vision", "off")}
# Name carry-over (owner 2026-11-15 "同一位置 2 秒内又冒出一张新脸，就沿用原来的名字。只改服务器"; and "宁可叫不出名字，也不要叫错").
CARRY_US = 2_000_000          # a new face this soon after a named face was lost ...
CARRY_DIST = 0.5              # ... with its box centre within this × the old box size ...
CARRY_SIZE = (0.67, 1.5)      # ... and a box this much the same size (width ratio) gets the name


class NameCarry:
    """Per vision session. M12 sometimes ends a track when the head turns or the light changes and starts a new face id
    at the same place, which is then asked `who` again at a bad moment and stays unknown. feed(event) -> a synthetic
    face_carried {name, carried: true, from_face} for such a new face, or None. Strict on purpose: exactly one recently
    lost named face fits, no other new face competes for it, and the name is not on a face still in view.
    Display only (owner 2026-11-15, after a security review: a guess must never be taken as a recognition, "宁可叫不出
    名字，也不要叫错"): it is stored, but it is NOT a face_named, so nothing that keys on face_named (the meet owner
    greeting and 【你的眼睛】, mind's 认出 / owner presence / scene) uses it. Only the M12's own face_named for that face
    (its who re-ask) names it."""

    def __init__(self):
        self.named = {}               # face id -> (name, box) while in view
        self.lost = []                # (t_us, name, box, face) of named faces that just left
        self.fresh = {}               # new face id -> (t_us, box): found, not yet named

    @staticmethod
    def _fits(old, new):
        try:
            ox, oy, ow, oh = (float(v) for v in old)
            nx, ny, nw, nh = (float(v) for v in new)
        except (TypeError, ValueError):
            return False
        if ow <= 0 or oh <= 0 or nw <= 0:
            return False
        dist = ((ox + ow / 2 - nx - nw / 2) ** 2 + (oy + oh / 2 - ny - nh / 2) ** 2) ** 0.5
        return dist <= CARRY_DIST * max(ow, oh) and CARRY_SIZE[0] <= nw / ow <= CARRY_SIZE[1]

    def feed(self, e):
        ev, face, t, box = e.get("event"), e.get("face"), e.get("t_us"), e.get("box")
        if ev in ("vision_off", "m12_online", "vision_on"):
            self.named.clear(); self.lost.clear(); self.fresh.clear()
            return None
        if not isinstance(t, int) or face is None:
            return None
        self.lost = [x for x in self.lost if 0 <= t - x[0] <= CARRY_US]
        if ev == "face_named" and isinstance(e.get("name"), str):
            self.named[face] = (e["name"], box)
            self.fresh.pop(face, None)
        elif ev == "face_lost":
            self.fresh.pop(face, None)
            got = self.named.pop(face, None)
            if got:
                self.lost.append((t, got[0], box if box else got[1], face))
        elif ev == "face_found" and face not in self.named:
            self.fresh[face] = (t, box)
            fits = [x for x in self.lost if self._fits(x[2], box)]
            if len(fits) != 1:
                return None
            lt, name, old_box, old_face = fits[0]
            if name in (n for n, _ in self.named.values()):
                return None
            rivals = [f for f, (ft, fb) in self.fresh.items() if f != face and 0 <= t - ft <= CARRY_US and self._fits(old_box, fb)]
            if rivals:
                return None
            self.lost.remove(fits[0])
            self.named[face] = (name, box)
            self.fresh.pop(face, None)
            out = {k: e[k] for k in ("t_us", "f", "face", "box", "prob", "gender", "age", "age_group") if k in e}
            return {**out, "type": "vision", "event": "face_carried", "name": name, "carried": True, "from_face": old_face}
        return None
_live = {}                                   # body id -> live vision link numbers (a reconnect overlaps the old one)
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
    def __init__(self, db, number, peer, recordings_root=None, on_event=None, on_frame=None, on_body=None):
        self.db, self.number, self.peer = db, number, peer
        self.on_event, self.on_frame, self.on_body = on_event, on_frame, on_body
        self.body = 0
        self.recordings_root = recordings_root
        self.session = None
        self.device = None                       # the hello's device dict (boot_id)
        self.source = None                       # the hello's source (the robot, for robot.state)
        self.body_of = None                      # which of hello's bodies (bodies.py), from the hello
        self.why = None                          # why the link ended: idle / closed
        self.events = 0
        self.frames = 0
        self.start_ms = int(time.time() * 1000)
        self.carry = NameCarry()

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
                self.device = msg.get("device") if isinstance(msg.get("device"), dict) else None
                self.source = safe_source(msg.get("source"))
                self.session = session_key(msg.get("source"), self.number, self.start_ms / 1000)
                if self.db is not None:
                    self.db.session_open(self.session, {
                        "kind": "vision", "source": msg.get("source"), "peer": self.peer, "start_ms": self.start_ms,
                        "device": msg.get("device") if isinstance(msg.get("device"), dict) else None})
                self.body_of = bodies.body(msg.get("source"), msg.get("body"))
                if self.body_of is not None:
                    _live.setdefault(self.body_of["id"], set()).add(self.number)
                    if self.db is not None:
                        self.db.body_seen(self.body_of, "vision", len(_live[self.body_of["id"]]), self.device)
            return None
        if kind == "vision":
            if self.session is None:
                return {"type": "error", "message": "hello first"}
            if not isinstance(msg.get("event"), str) or len(msg["event"]) > 32:
                return {"type": "error", "message": "vision needs an event name"}
            try:
                carried = self.carry.feed(msg)
            except Exception:                     # noqa: BLE001 - carry-over is a bonus, never costs the event
                carried = None
            if msg["event"] in STATE_EVENTS:
                ear_log.robot_state(*STATE_EVENTS[msg["event"]], device=self.source, session=self.session)
            elif msg["event"] == "m12_ev":
                ear_log.emit("seen.m12", by="m12", sum=m12_ev_sum(msg), ref={"session": self.session},
                             extra={"device": self.source, **{k: msg[k] for k in M12_EV_EXTRA if msg.get(k) is not None}})
            for m in (msg, carried) if carried else (msg,):
                self.events += 1
                if self.db is not None:
                    self.db.vision(self.session, m, int(time.time() * 1000))
                if self.on_event is not None:
                    try:
                        self.on_event(m)
                    except Exception as e:        # noqa: BLE001 - the hook never costs the link
                        print(f"vision hook failed: {type(e).__name__}", flush=True)
            return None
        if kind in ("body", "body_ev"):
            if self.session is None:
                return {"type": "error", "message": "hello first"}
            self.body += 1
            if self.on_body is not None:
                try:
                    self.on_body(kind, msg, self.session, self.source)
                except Exception as e:            # noqa: BLE001 - the hook never costs the link
                    print(f"body hook failed: {type(e).__name__}", flush=True)
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
        tmp = path + ".part"
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(tmp, "wb") as f:
                f.write(jpeg)
            os.replace(tmp, path)
        except OSError as e:                   # disk full, permissions: answer, keep the link (events still flow)
            try:
                os.remove(tmp)
            except OSError:
                pass
            print(f"vision frame not stored ({self.session}): {type(e).__name__}: {e}", flush=True)
            return {"type": "error", "message": f"frame not stored: {type(e).__name__}"}
        received = int(time.time() * 1000)
        wait_ms = max(0, (head["now_us"] - head["t_us"]) // 1000)
        num = lambda k: head.get(k) if isinstance(head.get(k), int) and not isinstance(head.get(k), bool) else None  # noqa: E731
        self.frames += 1
        if self.db is not None:
            doc = {"session": self.session, "seq": head["seq"], "t_us": head["t_us"], "at": received - wait_ms,
                   "received": received, "path": rel, "bytes": len(jpeg), "w": num("w"), "h": num("h"),
                   "diff": num("diff")}
            if num("snap") is not None:
                doc["snap"] = head["snap"]
            self.db.frame(doc)
        if self.on_frame is not None:
            try:
                self.on_frame(head, jpeg, rel, self.session)
            except Exception as e:                # noqa: BLE001 - the hook never costs the link
                print(f"vision frame hook failed: {type(e).__name__}", flush=True)
        return None

    def close(self):
        if self.body_of is not None:
            live = _live.get(self.body_of["id"], set())
            live.discard(self.number)
            if self.db is not None:
                self.db.body_seen(self.body_of, "vision", len(live), self.device)
            self.body_of = None
        if self.db is not None and self.session:
            self.db.session_update(self.session, {"end_ms": int(time.time() * 1000), "vision_events": self.events,
                                                  "frames": self.frames})


async def serve_link(ws, db, number, peer, recordings_root=None, on_event=None, on_frame=None, on_body=None):
    link = VisionLink(db, number, peer, recordings_root, on_event, on_frame, on_body)
    try:
        while True:
            try:
                data = await asyncio.wait_for(ws.recv(), IDLE_TIMEOUT)
            except asyncio.TimeoutError:
                link.why = "idle"
                await ws.close(code=1000, reason="idle")
                break
            reply = link.on_binary(data) if isinstance(data, bytes) else link.on_text(data)
            if reply:
                await ws.send(json.dumps(reply))
    except Exception:                                   # noqa: BLE001 - connection closed or broken
        link.why = link.why or "closed"
    finally:
        link.why = link.why or "cancelled"
        link.close()
    return link
