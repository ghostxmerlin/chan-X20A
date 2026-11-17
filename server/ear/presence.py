"""Who is there, as the ear sees it, and the pause it obeys (2026-11-17; mind/pause.py owns the pause).

The ear reports facts, mind decides (as everywhere): Presence collects the three kinds of "someone is here" the ear
sees itself and the ear writes them to hello.switches {_id: "presence_status"} (only on a change, at least once a
minute):
  face_at   the last M12 face event (found / named / unknown / attrs / carried / lost), any face, known or not;
  faces     M12 faces in view now (found and not lost yet; a face that sits still sends no events);
  voice_at  the last sentence from the robot's mic whose speaker band is "known" (owner or family), never while
            hello herself sounds. Ordinary speech is not presence: a TV talks all evening;
  owner_at  the last explicit owner input on the robot the ear relays (a mode report from an ASR word, a tap, a
            select or the quick page), with owner_what.
No names and no text are kept.

Pause reads hello.switches {_id: "pause"} (written by mind) every few seconds; Gated wraps a SwitchReader so a paid
path sees its switch "off" while paused, and the owner's switch itself is never touched. An away pause is lifted
here at once when this ear sees presence after it began (a face, a known voice, an owner input), so 围观's owner
greeting and a tap's greeting are not lost while mind's next tick (≤ 5 s) lifts it for everyone. A budget pause is
only mind's to lift. Standard library only.
"""
import time

FACE_EVENTS = ("face_found", "face_named", "face_unknown", "face_attrs", "face_carried")
CLEAR_EVENTS = ("m12_offline", "vision_off")
IN_VIEW_MAX_MS = 2 * 3600_000      # a face "in view" with no event for this long no longer counts (a lost face_lost)
WRITE_EVERY_MS = 60_000


def _now_ms():
    return int(time.time() * 1000)


class Presence:
    def __init__(self, clock_ms=_now_ms):
        self.clock_ms = clock_ms
        self.face_at = self.voice_at = self.owner_at = None
        self.owner_what = None
        self._faces = {}             # (vision link, M12 face id) -> ms of its last event
        self.version = 0             # bumps on every change; the writer writes when it moved

    def _bump(self):
        self.version += 1

    def vision(self, link, msg):
        """One robot vision event (VisionLink on_event) from vision link `link`."""
        if not isinstance(msg, dict):
            return
        ev, now = msg.get("event"), self.clock_ms()
        if ev in FACE_EVENTS:
            self._faces[(link, msg.get("face"))] = now
            self.face_at = now
            self._bump()
        elif ev == "face_lost":
            self._faces.pop((link, msg.get("face")), None)
            self.face_at = now
            self._bump()
        elif ev in CLEAR_EVENTS:
            self.vision_closed(link)

    def vision_closed(self, link):
        """The vision link ended (or M12 went off): its faces are no longer in view."""
        gone = [k for k in self._faces if k[0] == link]
        for k in gone:
            del self._faces[k]
        if gone:
            self._bump()

    def heard(self, ev):
        """One finished sentence from the robot's mic (the Ear's speech event)."""
        if not isinstance(ev, dict):
            return
        me = ev.get("self")
        if isinstance(me, dict) and me.get("sounding"):
            return                       # hello's own voice (her clones may sound like the family)
        if (ev.get("speaker") or {}).get("band") == "known":
            self.voice_at = self.clock_ms()
            self._bump()

    def owner(self, what):
        """An explicit owner input on the robot (ASR word, tap, select, quick page)."""
        self.owner_at, self.owner_what = self.clock_ms(), str(what)[:32]
        self._bump()

    def faces(self, now_ms=None):
        now = self.clock_ms() if now_ms is None else now_ms
        return sum(1 for t in list(self._faces.values()) if now - t < IN_VIEW_MAX_MS)   # list: the beat thread reads too

    def last_ms(self, now_ms=None):
        """The latest moment someone was there (now while a face is in view); None = never seen."""
        now = self.clock_ms() if now_ms is None else now_ms
        times = [t for t in (self.face_at, self.voice_at, self.owner_at) if t is not None]
        if self.faces(now):
            times.append(now)
        return max(times) if times else None

    def doc(self, now_ms=None):
        now = self.clock_ms() if now_ms is None else now_ms
        return {"face_at": self.face_at, "voice_at": self.voice_at, "owner_at": self.owner_at,
                "owner_what": self.owner_what, "faces": self.faces(now), "at": now}


class Writer:
    """When to write the presence document: on a change, and at least every WRITE_EVERY_MS (faces in view stay fresh
    for mind)."""
    def __init__(self, presence, write, clock_ms=_now_ms):
        self.presence, self.write, self.clock_ms = presence, write, clock_ms
        self._version = self._at = None

    def maybe(self):
        now = self.clock_ms()
        if self._version == self.presence.version and self._at is not None and now - self._at < WRITE_EVERY_MS:
            return False
        self.write(self.presence.doc(now))
        self._version, self._at = self.presence.version, now
        return True


class Pause:
    """hello.switches {_id: "pause"} as read every few seconds. A failed read keeps the last value (the switches go
    off by themselves after 60 s of failed reads)."""
    def __init__(self, read, presence=None, clock_ms=_now_ms):
        self.read, self.presence, self.clock_ms = read, presence, clock_ms
        self.doc = None

    def update(self):
        try:
            self.doc = self.read()
        except Exception:             # noqa: BLE001
            return

    def reasons(self, now_ms=None):
        """The reasons that hold here now: [] = not paused."""
        d = self.doc
        if not isinstance(d, dict) or d.get("on") is not True:
            return []
        now = self.clock_ms() if now_ms is None else now_ms
        out = []
        until = d.get("budget_until")
        if "budget" in (d.get("reasons") or []) and (not isinstance(until, int) or now < until):
            out.append("budget")
        if "away" in (d.get("reasons") or []):
            since = d.get("away_since") if isinstance(d.get("away_since"), int) else d.get("since")
            seen = self.presence.last_ms(now) if self.presence is not None else None
            if not (isinstance(since, int) and seen is not None and seen > since):
                out.append("away")
        return out

    @property
    def active(self):
        return bool(self.reasons())


class Gated:
    """A SwitchReader as a paid path sees it: on only while the owner's switch is on and hello is not paused."""
    def __init__(self, switch, pause):
        self.switch, self.pause = switch, pause

    def update(self):
        self.switch.update()

    @property
    def paused(self):
        return self.switch.on and self.pause.active

    @property
    def on(self):
        return self.switch.on and not self.pause.active
