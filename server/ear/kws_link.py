"""Ear -> keyword ear (server/kws/kws_server.py, 127.0.0.1): the robot's raw mic goes to the local keyword
spotter as a continuous stream (POST /feed every ~0.25 s), without waiting. It does not use the ear's sentence
cutting: on 2026-11-17 16:56 the owner's quiet 「デュナメス」 never became a sentence (VAD skipped it) but the
stream found them. Reports come back as ear events ({"type": "keyword"}, in the recording and on the debug page)
and run-log rows (heard.keyword).

The robot's own sound is sent as zeros (it must not call itself). If the spotter is slow or down, audio it could
not take is dropped (the spotter starts over at the gap); a sentence, the tick or the audio path never waits.
Off unless SOUL_KWS_URL or a kws.url file next to the ear is set. Free (local CPU), so not behind the paid
switches or the pause.
"""
import asyncio
import concurrent.futures
import json
import time
import urllib.parse
import urllib.request

import numpy as np

SR = 16000
SEND_S = 0.25           # send when this much audio is waiting (the spotter looks every 0.25 s)
MAX_PENDING_S = 4.0     # more than this waiting (spotter slow/down): drop the oldest


def ear_url(env):
    """The keyword ear's URL for the ear (owner 2026-11-20: Dynames is gone, the service is off): only an explicit
    SOUL_KWS_URL (local addresses only), never the kws.url file next to the ear."""
    url = (env.get("SOUL_KWS_URL") or "").strip()
    return configured_url({"SOUL_KWS_URL": url}, None) if url else None


def configured_url(env, path):
    """The keyword ear's URL: SOUL_KWS_URL, else the first line of the kws.url file next to the ear (written by
    server/kws/deploy.sh, so the ear's unit needs no new environment), else None (off). Local addresses only."""
    url = (env.get("SOUL_KWS_URL") or "").strip()
    if not url:
        try:
            with open(path) as f:
                url = f.readline().strip()
        except OSError:
            return None
    host = urllib.parse.urlsplit(url).hostname
    return url if host in ("127.0.0.1", "localhost", "::1") else None


def http_feed(url, pcm, stream, pos, timeout):
    q = urllib.parse.urlencode({"stream": stream or "", "pos": int(pos)})
    req = urllib.request.Request(f"{url.rstrip('/')}/feed?{q}", data=pcm, method="POST",
                                 headers={"Content-Type": "application/octet-stream"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


class KeywordLink:
    def __init__(self, url, *, timeout_s=4.0, call=http_feed, log=print, emit=None, clock=time.time):
        self.url = url
        self.timeout_s = timeout_s
        self.call = call
        self.log = log
        self.emit = emit                   # ear_log.emit (run log), or None
        self.on_hit = None                 # callable(report) on a hit, on the event loop (ear_server: 围观 called)
        self.clock = clock
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="kws")
        self.streams = {}                  # session -> {"buf": [arrays], "start": pos, "n": samples, "busy": bool}
        self.sent_s = 0.0
        self.dropped_s = 0.0
        self.failed = self.hits = self.reports = 0
        self._tasks = set()

    def feed(self, x, pos, session, sounding, post):
        """On the event loop, for every audio packet of a robot: x = raw float mic samples starting at absolute
        sample pos of this session. Returns at once; post (the ear's event poster) gets the reports later."""
        if not session or x is None or not len(x):
            return
        st = self.streams.get(session)
        if st is None:
            for k in [k for k, v in self.streams.items() if not v["busy"]]:   # one robot: old sessions go
                del self.streams[k]
            st = self.streams[session] = {"buf": [], "start": pos, "n": 0, "busy": False}
        if pos != st["start"] + st["n"]:           # a gap: start the pending audio here
            st["buf"], st["start"], st["n"] = [], pos, 0
        x = np.zeros(len(x), np.float32) if sounding else np.asarray(x, dtype=np.float32)
        st["buf"].append(x)
        st["n"] += len(x)
        while st["n"] > MAX_PENDING_S * SR and len(st["buf"]) > 1:
            old = st["buf"].pop(0)
            st["start"] += len(old)
            st["n"] -= len(old)
            self.dropped_s += len(old) / SR
        if st["busy"] or st["n"] < SEND_S * SR:
            return
        audio = np.concatenate(st["buf"])
        start = st["start"]
        st["buf"], st["start"], st["n"], st["busy"] = [], start + len(audio), 0, True
        pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        task = asyncio.get_running_loop().create_task(self._send(session, st, pcm, start, start + len(audio), post))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _send(self, session, st, pcm, start, end, post):
        loop = asyncio.get_running_loop()
        sent_at = self.clock()
        try:
            res = await asyncio.wait_for(
                loop.run_in_executor(self.pool, self.call, self.url, pcm, session, start, self.timeout_s),
                self.timeout_s + 1)
            self.sent_s += (end - start) / SR
        except Exception as error:         # noqa: BLE001 - the spotter being down never costs the ear anything
            self.failed += 1
            self.dropped_s += (end - start) / SR
            if self.failed in (1, 10) or self.failed % 100 == 0:
                self.log(f"kws: feed failed ({self.failed}x): {type(error).__name__}", flush=True)
            return
        finally:
            st["busy"] = False
        for r in res.get("reports") or []:
            self.report(r, session, end, sent_at, post)

    def report(self, r, session, end, sent_at, post):
        self.reports += 1
        self.hits += int(bool(r.get("hit")))
        t = int((sent_at - (end - int(r.get("start", end))) / SR) * 1000)     # wall time of the look's start
        out = {"type": "keyword", "id": f"k-{r.get('start')}", "pos": [r.get("start"), r.get("end")], "t": t,
               "keyword": r.get("keyword"), "label": r.get("label", r.get("keyword")), "score": r.get("score"),
               "hit": bool(r.get("hit")), "near": bool(r.get("near")), "sample": r.get("sample")}
        try:
            post(out)
        except Exception as error:         # noqa: BLE001
            self.log(f"kws: post failed: {type(error).__name__}", flush=True)
        if out["hit"] and self.on_hit is not None:      # 2026-11-18: a name hit is a call (围观: RealtimeHub.called)
            try:
                self.on_hit(out)
            except Exception as error:     # noqa: BLE001
                self.log(f"kws: on_hit failed: {type(error).__name__}", flush=True)
        if self.emit is not None:
            self.emit("heard.keyword", by="kws",
                      sum=f"{out['label']}{'' if out['hit'] else '（差一点）'} {out['score']}",
                      ref={"session": session, "pos": out["pos"]},
                      extra={"keyword": out["keyword"], "score": out["score"], "hit": out["hit"],
                             "sample": out["sample"], "at": t})

    def status(self):
        return {"url": self.url, "sent_s": round(self.sent_s, 1), "dropped_s": round(self.dropped_s, 1),
                "failed": self.failed, "reports": self.reports, "hits": self.hits}

    def close(self):
        self.pool.shutdown(wait=False, cancel_futures=True)
