"""Body sense from the robot (collector-0.5.12, owner 2026-11-19 "姿态、加速度、距离…汇总到服务器"), on the /vision link.

The robot's own sensors, which used to reach only the Mac monitor:
  {"type":"body","seq":N,"t_us":...,"now_us":...,"n":50,"pitch":-3.2,"roll":1.1,"acc_g":1.0,"dev_g":0.02,"gyro":3,
   "moved":0.0,"still":true,"held":false,"self":false,"prox":41,"prox_base":40,"near":false,"lux":120.5,
   "mag":[x,y,z]}                                   once a second: the window's summary (collector/body_sense.h)
       pitch / roll: gravity in the head frame (deg; the IMU sits on the moving head, not calibrated to the body);
       acc_g: mean |a| in g; dev_g / gyro: the strongest |a|-1g / |gyro| (dps) of the window; moved: share of samples
       that moved; still: nothing moved for 1.5 s; held: picked up and not yet put down; self: the robot's own servos
       moved in the window (those samples count neither way); prox: the LTR553 count (0..2047, bigger = nearer) and
       its slow baseline, prox_max the window's largest (0.5.13); lux; mag: the BMM150 field (uT). Fields missing when the sensor had nothing.
  {"type":"body_ev","ev":"picked_up|put_down|shaken|near|far","seq":N,"t_us":...,"now_us":...,
   "held_ms"?, "prox"?, "peak_g"?, "peak_dps"?}     one event
Stored in hello.body (Mongo): every event (kind "ev") and a summary every STORE_EVERY_S or when held / near / still
changed (kind "sum"); each event is one run-log row seen.body. The hub's 【你的眼睛】 gets line() (身体感觉：...).
"""
import collections
import time

import ear_log

EV_ZH = {"picked_up": "被拿起来了", "put_down": "被放下了", "shaken": "被晃了几下", "near": "有东西靠得很近",
         "far": "靠近的东西走开了"}
SUM_FIELDS = ("n", "pitch", "roll", "acc_g", "dev_g", "gyro", "moved", "prox", "prox_base", "prox_max", "lux")
FLAGS = ("still", "held", "self", "near")
EV_FIELDS = ("held_ms", "prox", "peak_g", "peak_dps")
STORE_EVERY_S = 10
FRESH_S = 5                    # a summary older than this says nothing about now
EVENT_WINDOW_S = 90            # events shown in 【你的眼睛】
EVENTS_SHOWN = 3


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _wall_ms(msg, received_ms):
    wait = (msg["now_us"] - msg["t_us"]) // 1000 if _num(msg.get("now_us")) and _num(msg.get("t_us")) else 0
    return received_ms - max(0, int(wait))


class BodySense:
    def __init__(self, db=None, clock_ms=None, hub=None):
        self.db = db
        self.hub = hub                              # the realtime hub: an event refreshes its instructions
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self.latest = None                          # the newest summary {fields..., at}
        self.events = collections.deque(maxlen=32)  # (at_ms, ev, fields)
        self._stored_at = 0
        self._stored_flags = None
        self.stats = collections.Counter()

    # ---- from the /vision link ----
    def on_link(self, kind, msg, session=None, device=None):
        (self.on_summary if kind == "body" else self.on_event)(msg, session, device)

    def on_summary(self, msg, session=None, device=None):
        now = self.clock_ms()
        doc = {k: msg[k] for k in SUM_FIELDS if _num(msg.get(k))}
        doc.update({k: msg[k] for k in FLAGS if isinstance(msg.get(k), bool)})
        mag = msg.get("mag")
        if isinstance(mag, list) and len(mag) == 3 and all(_num(v) for v in mag):
            doc["mag"] = mag
        doc["at"] = _wall_ms(msg, now)
        self.latest = doc
        self.stats["summaries"] += 1
        flags = tuple(doc.get(k) for k in ("held", "near", "still"))
        if self.db is not None and (now - self._stored_at >= STORE_EVERY_S * 1000 or flags != self._stored_flags):
            self._stored_at, self._stored_flags = now, flags
            self.db.body({**doc, "kind": "sum", "session": session, "device": device, "seq": msg.get("seq"),
                          "received": now})
            self.stats["stored"] += 1

    def on_event(self, msg, session=None, device=None):
        ev = msg.get("ev")
        if ev not in EV_ZH:
            return
        now = self.clock_ms()
        fields = {k: msg[k] for k in EV_FIELDS if _num(msg.get(k))}
        at = _wall_ms(msg, now)
        self.events.append((at, ev, fields))
        self.stats["events"] += 1
        if self.db is not None:
            self.db.body({"kind": "ev", "ev": ev, **fields, "at": at, "received": now, "session": session,
                          "device": device, "seq": msg.get("seq")})
        ear_log.emit("seen.body", by="body", sum=event_sum(ev, fields), ref={"session": session} if session else None,
                     extra={"ev": ev, **fields, "device": device})
        if self.hub is not None:
            try:
                self.hub.body_changed()
            except Exception as e:                  # noqa: BLE001 - the hook never costs the link
                print(f"body hook failed: {type(e).__name__}", flush=True)

    # ---- for the model ----
    def line(self, now_ms=None):
        """身体感觉 for 【你的眼睛】: the recent events (newest first) and what the body feels now; None when nothing."""
        now = self.clock_ms() if now_ms is None else now_ms
        parts = []
        for at, ev, fields in list(self.events)[::-1]:
            ago = (now - at) // 1000
            if ago > EVENT_WINDOW_S:
                break
            extra = f"，拿了 {fields['held_ms'] // 1000} 秒" if ev == "put_down" and fields.get("held_ms", 0) >= 1000 else ""
            parts.append(f"{EV_ZH[ev]}（{max(0, ago)} 秒前{extra}）")
            if len(parts) >= EVENTS_SHOWN:
                break
        s = self.latest
        if s is not None and now - s["at"] <= FRESH_S * 1000:
            if s.get("held"):
                parts.insert(0, "这会儿正被人拿着或挪动")
            elif s.get("near"):
                parts.insert(0, "这会儿有东西离身体很近")
        return "身体感觉：" + "；".join(parts) if parts else None

    def view(self, now_ms=None):
        """The monitor's / tools' view: the newest summary and the recent events."""
        now = self.clock_ms() if now_ms is None else now_ms
        s = dict(self.latest) if self.latest else None
        if s is not None:
            s["age_s"] = round((now - s["at"]) / 1000, 1)
        return {"latest": s, "events": [{"at": at, "ev": ev, "zh": EV_ZH[ev], **f} for at, ev, f in list(self.events)[-10:]],
                "line": self.line(now), "stats": dict(self.stats)}


def event_sum(ev, fields):
    """One event -> the run log's sum."""
    s = EV_ZH.get(ev, ev)
    if ev == "put_down" and _num(fields.get("held_ms")):
        s += f"（拿了 {fields['held_ms'] / 1000:.0f} 秒）"
    if ev in ("near", "far") and _num(fields.get("prox")):
        s += f"（距离读数 {fields['prox']}）"
    if ev == "shaken" and _num(fields.get("peak_g")):
        s += f"（最猛 {fields['peak_g']:.1f} g）"
    return s
