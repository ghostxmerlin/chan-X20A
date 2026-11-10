"""MongoDB database "hello" for everything the ear hears (2026-11-10).

Collections (one document per thing; `session` = the recording folder name, e.g.
"20261110/154417-stackchan-7c4fadaf90b4-c75", so every row points back at its audio):
  sessions  one per connection: source, peer, start/end, audio file, counts, models
  speech    SenseVoice sentences: text, pos (16 kHz samples in the session audio), emotion,
            speaker, voice, self flags; unique (session, id)
  sounds    sound events (typing, clap ...): kind, label, pos, peak; unique (session, id)
  vision    M12 change events from the robot (face/person found, named, attrs, unknown, lost,
            vision on/off, M12 online/offline); `at` = wall time of the event; unique
            (session, t_us, event, face, person)
  tingwu    听悟 realtime sentences (kind "sentence", final only) and the diarized result after
            the session (kind "paragraph", speaker); unique (session, kind, n)
Audio stays in the recording files (never deleted); the database stores paths and positions.

Writes go through one background thread with a bounded queue: the ear never waits on the
database, and a database outage only drops rows (counted), never audio or events.
Connection: MONGO_URI from the hubmix env file on the server; nothing secret in this repo.
"""
import logging
import queue
import threading
import time

DB_NAME = "hello"
log = logging.getLogger("hello_db")


def session_key(recording_dir):
    """'.../recordings/20261110/154417-stackchan-...-c75' → '20261110/154417-stackchan-...-c75'."""
    parts = str(recording_dir).rstrip("/").split("/")
    return "/".join(parts[-2:])


def speech_doc(session, ev, epoch_ms=None):
    sp = ev.get("speaker") or {}
    return {"session": session, "id": ev.get("id"), "t": ev.get("t"), "pos": ev.get("pos"), "dur": ev.get("dur"),
            "text": ev.get("text", ""), "lang": ev.get("lang"), "emotion": ev.get("emotion"), "event": ev.get("event"),
            "speaker": {"band": sp.get("band"), "name": sp.get("name") if sp.get("band") == "known" else None,
                        "best": sp.get("name"), "score": sp.get("score")},
            "voice": ev.get("voice"), "self": ev.get("self"), "engine": "sensevoice",
            "at": epoch_ms or ev.get("t")}


def sound_doc(session, ev):
    return {"session": session, "id": ev.get("id"), "t": ev.get("t"), "kind": ev.get("kind"), "label": ev.get("label"),
            "pos": ev.get("pos"), "dur": ev.get("dur"), "peak": ev.get("peak"), "loud_db": ev.get("loud_db"),
            "self": ev.get("self")}


def open_db(uri):
    """Database handle with the indexes in place (idempotent)."""
    from pymongo import MongoClient, ASCENDING
    client = MongoClient(uri, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000)
    db = client[DB_NAME]
    db.sessions.create_index([("session", ASCENDING)], unique=True)
    db.speech.create_index([("session", ASCENDING), ("id", ASCENDING)], unique=True)
    db.speech.create_index([("t", ASCENDING)])
    db.sounds.create_index([("session", ASCENDING), ("id", ASCENDING)], unique=True)
    db.tingwu.create_index([("session", ASCENDING), ("kind", ASCENDING), ("n", ASCENDING)], unique=True)
    db.tingwu.create_index([("at", ASCENDING)])
    db.vision.create_index([("session", ASCENDING), ("t_us", ASCENDING), ("event", ASCENDING), ("face", ASCENDING),
                            ("person", ASCENDING)], unique=True)
    db.vision.create_index([("at", ASCENDING)])
    return db


class HelloDB:
    def __init__(self, uri, maxsize=5000, client_factory=None):
        self.queue = queue.Queue(maxsize=maxsize)
        self.dropped = 0
        self.written = 0
        self.error = ""
        self._uri = uri
        self._factory = client_factory
        self._thread = threading.Thread(target=self._run, name="hello-db", daemon=True)
        self._thread.start()

    # ---- producers (event loop) ----
    def _put(self, op, collection, key, doc):
        try:
            self.queue.put_nowait((op, collection, key, doc))
        except queue.Full:
            self.dropped += 1

    def session_open(self, session, meta):
        self._put("upsert", "sessions", {"session": session}, {**meta, "session": session})

    def session_update(self, session, fields):
        self._put("set", "sessions", {"session": session}, fields)

    def speech(self, session, ev):
        self._put("upsert", "speech", {"session": session, "id": ev.get("id")}, speech_doc(session, ev))

    def sound(self, session, ev):
        self._put("upsert", "sounds", {"session": session, "id": ev.get("id")}, sound_doc(session, ev))

    def tingwu(self, session, kind, n, doc):
        self._put("upsert", "tingwu", {"session": session, "kind": kind, "n": n},
                  {**doc, "session": session, "kind": kind, "n": n, "engine": "tingwu"})

    def vision(self, session, msg, received_ms):
        """One robot vision event; its wall time is the arrival time minus how long it waited."""
        wait_ms = max(0, (msg.get("now_us", 0) - msg.get("t_us", 0)) // 1000) if isinstance(msg.get("now_us"), int) else 0
        doc = {k: msg.get(k) for k in ("event", "t_us", "f", "face", "person", "box", "prob", "sim", "gender", "age",
                                        "age_group", "name", "reason") if msg.get(k) is not None}
        doc.update({"session": session, "at": received_ms - wait_ms, "received": received_ms})
        key = {"session": session, "t_us": doc.get("t_us"), "event": doc.get("event"), "face": doc.get("face"),
               "person": doc.get("person")}
        self._put("upsert", "vision", key, doc)

    def event(self, session, ev):
        """Route one ear event; speech/sound 'end' rows only."""
        if not session:
            return
        if ev.get("type") == "speech" and ev.get("phase") == "end":
            self.speech(session, ev)
        elif ev.get("type") == "sound" and ev.get("phase") == "end":
            self.sound(session, ev)

    # ---- writer thread ----
    def _connect(self):
        return self._factory() if self._factory else open_db(self._uri)

    def _run(self):
        db = None
        while True:
            op, collection, key, doc = self.queue.get()
            for attempt in range(3):
                try:
                    if db is None:
                        db = self._connect()
                    if op == "upsert":
                        db[collection].replace_one(key, doc, upsert=True)
                    else:
                        db[collection].update_one(key, {"$set": doc}, upsert=True)
                    self.written += 1
                    self.error = ""
                    break
                except Exception as e:                   # noqa: BLE001 - keep the ear running
                    self.error = f"{type(e).__name__}: {str(e)[:120]}"
                    db = None
                    if attempt == 2:
                        self.dropped += 1
                        log.warning("hello db write dropped: %s", self.error)
                    else:
                        time.sleep(1 + attempt)
            self.queue.task_done()

    def flush(self, timeout=5.0):
        """Wait until every queued row has been written or dropped (tests and shutdown)."""
        end = time.time() + timeout
        while self.queue.unfinished_tasks and time.time() < end:
            threading.Event().wait(0.02)          # not time.sleep: tests patch it
