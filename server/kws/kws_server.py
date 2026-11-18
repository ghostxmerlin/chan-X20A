"""Keyword ear service (关键词耳朵): a local process on the server that scores each speech segment the ear hands
it against the keyword table (spotter.py). Audio in, scores out: no text, no API, no cost.

  POST /feed?stream=<s>&pos=<n>     the live way: the ear's raw mic, in order, from absolute sample n
                                    (int16 LE PCM, 16 kHz mono; the robot's own sound as zeros)
                                    -> {"reports": [{keyword, label, score, hit, near, sample, start, end}], ...}
                                    No sentence cutting: a look at the last 1.5 s every 0.5 s (spotter.Stream).
  POST /score?session=<s>&id=<id>   one clip (tests, by hand)  -> {"keywords": {...}, "ms": n}
  POST /reload                      re-read the keyword table (after adding samples or changing a threshold)
  GET  /status                      keywords, sample counts, thresholds, counters, last hits

Listens on 127.0.0.1 only. One request at a time (one model, CPU threads capped): the ear never waits on it
(it sends the next chunk only when the last one is answered, and drops audio it cannot send in time).
It only reports; what a hit means (being called, a command) is decided by whoever receives it.

usage: kws_server.py --table <dir> [--model <onnx>] [--port 52760] [--threads 2]
"""
import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spotter  # noqa: E402

MAX_SECONDS = 15          # longer segments: only the first 15 s are searched
MAX_BODY = MAX_SECONDS * spotter.SR * 2 + 4096


class Service:
    def __init__(self, sp):
        self.sp = sp
        self.lock = threading.Lock()
        self.scored = 0
        self.hits = 0
        self.errors = 0
        self.last = []            # last hits/nears, newest first
        self.streams = {}         # stream id -> [spotter.Stream, last fed (monotonic)]
        self.fed_s = 0.0
        self.started = time.time()

    def score(self, pcm, session, seg_id):
        x = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        x = x[:MAX_SECONDS * spotter.SR]
        if len(x) < spotter.SR * 0.2:
            return {"keywords": {}, "ms": 0, "skipped": "short"}
        t0 = time.monotonic()
        with self.lock:
            res = self.sp.score(x)
        ms = int((time.monotonic() - t0) * 1000)
        self.scored += 1
        return {"keywords": res, "ms": ms}

    def _remember(self, stream, r):
        self.hits += int(r["hit"])
        self.last.insert(0, {"at": int(time.time() * 1000), "session": stream, "id": f"{r['start']}", **r})
        del self.last[20:]

    def feed(self, pcm, stream, pos):
        x = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        now = time.monotonic()
        for k in [k for k, v in self.streams.items() if now - v[1] > 120]:
            del self.streams[k]
        t0 = time.monotonic()
        with self.lock:
            entry = self.streams.get(stream)
            if entry is None:
                entry = self.streams[stream] = [spotter.Stream(self.sp), now]
            entry[1] = now
            reps = entry[0].feed(x, pos)
        self.fed_s += len(x) / spotter.SR
        for r in reps:
            self._remember(stream, r)
        return {"reports": reps, "ms": int((time.monotonic() - t0) * 1000)}

    def reload(self):
        with self.lock:
            kws = self.sp.reload()
        return self.status(kws)

    def status(self, kws=None):
        kws = self.sp.keywords if kws is None else kws
        return {"keywords": [{"name": k.name, "label": k.label, "samples": len(k.samples), "threshold": k.threshold,
                              "near": k.near} for k in kws],
                "scored": self.scored, "hits": self.hits, "errors": self.errors, "last": self.last,
                "fed_s": round(self.fed_s, 1),
                "streams": {k: {"looks": v[0].looks, "gated": v[0].gated, "floor_db": v[0].floor()}
                            for k, v in self.streams.items()},
                "up_s": int(time.time() - self.started)}


def handler_for(svc):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if urlparse(self.path).path == "/status":
                return self.reply(200, svc.status())
            self.reply(404, {"error": "not found"})

        def do_POST(self):
            u = urlparse(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            if n < 0 or n > MAX_BODY:
                return self.reply(413, {"error": "too long"})
            body = self.rfile.read(n) if n else b""
            try:
                if u.path == "/feed":
                    q = parse_qs(u.query)
                    return self.reply(200, svc.feed(body[:n - n % 2], q.get("stream", [""])[0][:80],
                                                    int(q.get("pos", ["0"])[0])))
                if u.path == "/score":
                    q = parse_qs(u.query)
                    return self.reply(200, svc.score(body[:n - n % 2], q.get("session", [""])[0][:80],
                                                     q.get("id", [""])[0][:40]))
                if u.path == "/reload":
                    return self.reply(200, svc.reload())
            except Exception as error:      # noqa: BLE001 - one bad segment must not stop the service
                svc.errors += 1
                print(f"kws: {u.path} failed: {type(error).__name__}: {error}", flush=True)
                return self.reply(500, {"error": type(error).__name__})
            self.reply(404, {"error": "not found"})
    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", required=True)
    ap.add_argument("--model", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "hubert-zh-l9.onnx"))
    ap.add_argument("--port", type=int, default=52760)
    ap.add_argument("--threads", type=int, default=2)
    a = ap.parse_args()
    sp = spotter.Spotter(spotter.Model(a.model, threads=a.threads), a.table)
    svc = Service(sp)
    print("keywords:", ", ".join(f"{k.name}({len(k.samples)} samples, < {k.threshold})" for k in sp.keywords) or "none",
          flush=True)
    srv = HTTPServer(("127.0.0.1", a.port), handler_for(svc))
    print(f"kws listening on 127.0.0.1:{a.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
