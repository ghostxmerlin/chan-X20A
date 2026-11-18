"""Keyword ear, offline tool (runs on the server, next to the recordings).

  enrol <table> <keyword> <session dir> HH:MM:SS ...   cut those speech segments of a session into samples
  enrol-pos <table> <keyword> <session dir> <start> ...  samples from stream reports (start = absolute sample)
  eval  <table> <out.jsonl> <day dir> ...              score every speech segment of those days; then a report
  report <out.jsonl> [keyword]                         report again from a saved run (no recompute)
  stream <table> <out.jsonl> <day dir> ...             the live way: whole sessions through spotter.Stream
  stream-report <out.jsonl>                            reports per hour of audio, the lowest scores

Segments come from the ear's own events.jsonl (speech end, pos in 16 kHz samples) and audio.opus. Text in the
report is only for a human to read; matching never uses it.
"""
import json
import os
import struct
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spotter  # noqa: E402

SR = spotter.SR
MODEL = os.environ.get("KWS_MODEL", os.path.expanduser("~/soul-spike/kws/hubert-zh-l9.onnx"))


def ogg_packets(path):
    """Packets of an Ogg Opus file, headers (OpusHead, OpusTags) dropped."""
    data = open(path, "rb").read()
    i, pkt, n = 0, b"", 0
    while i + 27 <= len(data) and data[i:i + 4] == b"OggS":
        nseg = data[i + 26]
        lace = data[i + 27:i + 27 + nseg]
        j = i + 27 + nseg
        for L in lace:
            pkt += data[j:j + L]
            j += L
            if L < 255:
                if n >= 2:
                    yield pkt
                n += 1
                pkt = b""
        i = j


def decode_opus(path):
    import opuslib_next
    dec = opuslib_next.Decoder(SR, 1)
    out = []
    for p in ogg_packets(path):
        try:
            out.append(np.frombuffer(dec.decode(p, 1920), dtype=np.int16))
        except Exception:
            continue
    return (np.concatenate(out).astype(np.float32) / 32768.0) if out else np.zeros(0, np.float32)


def speech_ends(session_dir):
    rows = []
    try:
        f = open(os.path.join(session_dir, "events.jsonl"))
    except OSError:
        return rows
    for line in f:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") == "speech" and e.get("phase") == "end" and e.get("pos") and e["pos"][1]:
            rows.append(e)
    return rows


def hms(t_ms):
    return time.strftime("%H:%M:%S", time.localtime(t_ms / 1000))


def enrol(table, keyword, session_dir, times):
    import soundfile as sf
    kdir = os.path.join(table, keyword)
    os.makedirs(kdir, exist_ok=True)
    conf = os.path.join(kdir, "keyword.json")
    if not os.path.exists(conf):
        json.dump({"label": keyword, **spotter.DEFAULTS}, open(conf, "w"), ensure_ascii=False, indent=1)
    audio = decode_opus(os.path.join(session_dir, "audio.opus"))
    sess = os.path.basename(session_dir.rstrip("/"))
    for e in speech_ends(session_dir):
        if hms(e["t"]) in times:
            a, b = e["pos"]
            name = f"{sess.split('-')[0]}-{e['id']}.wav"
            sf.write(os.path.join(kdir, name), audio[a:b], SR)
            print("sample", name, hms(e["t"]), round((b - a) / SR, 2), "s", repr(e.get("text", "")))


def enrol_pos(table, keyword, session_dir, starts):
    """Samples from stream reports (no sentence): the 1.5 s look at each start, cut to its voiced part."""
    import librosa
    import soundfile as sf
    kdir = os.path.join(table, keyword)
    audio = decode_opus(os.path.join(session_dir, "audio.opus"))
    sess = os.path.basename(session_dir.rstrip("/")).split("-")[0]
    win = int(spotter.WINDOW_S * SR)
    for st in starts:
        x = audio[st:st + win]
        _, (a, b) = librosa.effects.trim(x, top_db=18, frame_length=400, hop_length=160)
        y = x[a:b]
        if not 0.25 * SR <= len(y) <= 1.2 * SR:
            print("skip", st, round(len(y) / SR, 2), "s (voiced part not name-sized)")
            continue
        name = f"{sess}-p{st}.wav"
        sf.write(os.path.join(kdir, name), y, SR)
        print("sample", name, round(len(y) / SR, 2), "s")


def evaluate(table, out_path, days):
    sp = spotter.Spotter(spotter.Model(MODEL, threads=int(os.environ.get("KWS_THREADS", "2"))), table)
    samples = {name for kw in sp.keywords for name, _ in kw.samples}
    print("keywords:", [(k.name, len(k.samples)) for k in sp.keywords], flush=True)
    done = set()
    if os.path.exists(out_path):
        done = {(r["session"], r["id"]) for r in map(json.loads, open(out_path))}
    out = open(out_path, "a")
    t0, n = time.time(), 0
    for day in days:
        for sess in sorted(os.listdir(day)):
            sdir = os.path.join(day, sess)
            segs = [e for e in speech_ends(sdir) if (sess, e["id"]) not in done]
            if not segs or not os.path.exists(os.path.join(sdir, "audio.opus")):
                continue
            audio = decode_opus(os.path.join(sdir, "audio.opus"))
            for e in segs:
                a, b = int(e["pos"][0]), int(e["pos"][1])
                if b > len(audio) or b - a < SR * 0.2:
                    continue
                own = f"{sess.split('-')[0]}-{e['id']}.wav"
                res = sp.score(audio[a:b], skip={own})
                sp_ = e.get("speaker") or {}
                out.write(json.dumps({"session": sess, "id": e["id"], "day": os.path.basename(day.rstrip("/")),
                                      "hms": hms(e["t"]), "dur": round((b - a) / SR, 2), "sample": own in samples,
                                      "speaker": sp_.get("name") if sp_.get("band") == "known" else None,
                                      "text": e.get("text", ""), "kw": res}, ensure_ascii=False) + "\n")
                n += 1
            out.flush()
            print(f"{sess}: {len(segs)} segs, total {n}, {time.time() - t0:.0f}s", flush=True)
    out.close()


def tick_clock(session_dir):
    """Absolute sample -> wall time (ms) from the session's ticks (rx = samples received by then)."""
    pts = []
    try:
        for line in open(os.path.join(session_dir, "ticks.jsonl")):
            if line.startswith("{"):
                e = json.loads(line)
                if "rx" in e and "t" in e:
                    pts.append((e["rx"], e["t"]))
    except OSError:
        pass
    if not pts:
        return lambda s: None
    rx = np.array([p[0] for p in pts], dtype=np.float64)
    t = np.array([p[1] for p in pts], dtype=np.float64)
    return lambda s: int(np.interp(s, rx, t))


def stream_eval(table, out_path, days):
    """Like the live keyword ear (spotter.Stream, no sentence cutting) over whole sessions. Samples recorded in the
    same session are left out, so every report is scored by other sessions' samples only."""
    sp = spotter.Spotter(spotter.Model(MODEL, threads=int(os.environ.get("KWS_THREADS", "2"))), table)
    print("keywords:", [(k.name, len(k.samples)) for k in sp.keywords], flush=True)
    done = set()
    if os.path.exists(out_path):
        done = {r["session"] for r in map(json.loads, open(out_path)) if r.get("type") == "session"}
    out = open(out_path, "a")
    t0 = time.time()
    for day in days:
        for sess in sorted(os.listdir(day)):
            sdir = os.path.join(day, sess)
            if sess in done or "-c" not in sess or not os.path.exists(os.path.join(sdir, "audio.opus")):
                continue
            audio = decode_opus(os.path.join(sdir, "audio.opus"))
            prefix = sess.split("-")[0]
            same = {n for kw in sp.keywords for n, _ in kw.samples if n.startswith(prefix + "-")}
            st = spotter.Stream(sp, skip=lambda a, b: same)
            clock = tick_clock(sdir)
            hop = spotter.SR // 2
            reps = []
            for i in range(0, len(audio), hop):
                reps += st.feed(audio[i:i + hop], i)
            reps += st.flush()
            for r in reps:
                t = clock(r["start"])
                out.write(json.dumps({"type": "report", "session": sess, "day": os.path.basename(day.rstrip("/")),
                                      "hms": hms(t) if t else None, **r}, ensure_ascii=False) + "\n")
            out.write(json.dumps({"type": "session", "session": sess, "day": os.path.basename(day.rstrip("/")),
                                  "audio_s": round(len(audio) / spotter.SR, 1), "looks": st.looks,
                                  "gated": st.gated}) + "\n")
            out.flush()
            print(f"{sess}: {len(audio) / spotter.SR / 60:.1f} min, {st.looks} looks, {len(reps)} reports, "
                  f"{time.time() - t0:.0f}s", flush=True)
    out.close()


def stream_report(out_path):
    rows = list(map(json.loads, open(out_path)))
    sess = [r for r in rows if r["type"] == "session"]
    reps = sorted((r for r in rows if r["type"] == "report"), key=lambda r: r["score"])
    hours = sum(r["audio_s"] for r in sess) / 3600
    looks = sum(r["looks"] for r in sess)
    print(f"{len(sess)} sessions, {hours:.2f} h of audio, {looks} looks ({looks / max(hours, 1e-9) / 3600:.2f}/s), "
          f"{sum(r['gated'] for r in sess)} gated")
    for t in (0.30, 0.35, 0.40, 0.45, 0.48, 0.50, 0.52, 0.55):
        c = sum(r["score"] < t for r in reps)
        print(f"  below {t:.2f}: {c:4d} reports ({c / hours if hours else 0:.2f}/h of audio)")
    for r in reps[:40]:
        print(f"   {r['score']:.3f} {r['day']} {r['hms']} {'HIT ' if r['hit'] else 'near'} {r['session'][:6]} "
              f"{(r['end'] - r['start']) / spotter.SR:.1f}s {r['sample']}")


def report(out_path, keyword=None):
    rows = list(map(json.loads, open(out_path)))
    kws = sorted({k for r in rows for k in r["kw"]}) if keyword is None else [keyword]
    hours = sum(r["dur"] for r in rows) / 3600
    print(f"{len(rows)} segments, {hours:.2f} h of speech, {len({r['session'] for r in rows})} sessions")
    for k in kws:
        rs = sorted((r for r in rows if k in r["kw"]), key=lambda r: r["kw"][k]["score"])
        thr = rs[0]["kw"][k].get("threshold") if rs else None
        print(f"\n== {k}")
        for t in (0.30, 0.35, 0.40, 0.45, 0.48, 0.50, 0.52, 0.55):
            c = sum(r["kw"][k]["score"] < t for r in rs)
            print(f"  below {t:.2f}: {c:4d} segments ({c / hours if hours else 0:.1f}/h of speech)")
        print("  lowest 30 (sample = one of the keyword's own samples, scored without itself):")
        for r in rs[:30]:
            print(f"   {r['kw'][k]['score']:.3f} {r['day']} {r['hms']} {r['dur']:5.2f}s {'S' if r['sample'] else ' '} "
                  f"{r['speaker'] or '-':6s} {r['text'][:36]!r}")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "enrol":
        enrol(sys.argv[2], sys.argv[3], sys.argv[4], set(sys.argv[5:]))
    elif cmd == "enrol-pos":
        enrol_pos(sys.argv[2], sys.argv[3], sys.argv[4], [int(v) for v in sys.argv[5:]])
    elif cmd == "eval":
        evaluate(sys.argv[2], sys.argv[3], sys.argv[4:])
    elif cmd == "stream":
        stream_eval(sys.argv[2], sys.argv[3], sys.argv[4:])
    elif cmd == "stream-report":
        stream_report(sys.argv[2])
    elif cmd == "report":
        report(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
