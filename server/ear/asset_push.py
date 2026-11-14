"""SD assets over /mouth (collector-0.4.96): the ear pushes catalog files to hello on its live /mouth link.

The robot's HTTP server is not started, so its SD installer (POST /v1/assets) cannot be reached; the font parts and
other SD files go down the /mouth websocket instead. Protocol (docs/collector-protocol.md "0.4.96"; robot side
collector/mouth_assets.h):

    ear   -> robot  {"type":"asset.begin","id":N,"part":"cjk16.0","bytes":262144,"sha256":"<hex>"}
    robot -> ear    {"type":"asset.ack","id":N,"bytes":0,"window":32768}   or at once asset.result (unchanged / refused)
    ear   -> robot  binary b"AST1" | u32 id | u32 offset | payload (<= 1588 B), at most `window` bytes past the last ack
    robot -> ear    {"type":"asset.ack","id":N,"bytes":received,...}         every 8 KiB and at the end
    ear   -> robot  {"type":"asset.end","id":N}                              (or asset.abort)
    robot -> ear    {"type":"asset.result","id":N,"part":..,"ok":..,"unchanged":..,"bytes":..[,"reason":..]}

The robot checks everything against its own firmware catalog (part -> path, size, SHA-256): this side can never pick a
path or a size. Audio comes first: begin waits until hello is quiet (no soul-tts or realtime stream on the link) and
chunks pause while hello speaks (the robot's idle timer pauses too); at most one window (32 KiB) is ever queued ahead
of a TTS frame. One transfer at a time per link.

Control (owner's server only): the ear also listens on 127.0.0.1:52753 (--assets-port, loopback only, the ear's token
as Bearer) at /assets. A client sends {"type":"push","part":..,"bytes":N,"sha256":..} and then the file as one binary
message; it gets {"type":"progress","acked":n,"bytes":N} lines and one {"type":"result",...}. The CLI below does that:

    .venv/bin/python asset_push.py --token-file token --folder <dir> license=LICENSE-OFL.txt cjk16.0=cjk16.0.bin ...

(scripts/push-assets-via-server.sh runs it from the Mac.) Files are read by the CLI from --folder only (plain names,
no paths); the ear process never opens a file for this. No token or file content is ever printed.
"""
import argparse
import asyncio
import hashlib
import hmac
import json
import os
import re
import struct
import sys
import time

MAGIC = b"AST1"
HDR = struct.Struct("<4sII")             # magic, transfer id, offset (little endian)
CHUNK = 1536                             # payload per message (robot takes <= 1588)
MAX_BYTES = 256 * 1024                   # the robot's largest catalog file
CONTROL_PORT = 52753
PART_RE = re.compile(r"^[a-z0-9._-]{1,31}$")
log = print                              # tests silence it


def pack(tid, offset, payload):
    return HDR.pack(MAGIC, tid, offset) + payload


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


class AssetPusher:
    """Pushes one file at a time over one MouthLink.

    send: async (message) -> None (raises when the robot is gone); busy: () -> bool, hello is speaking.
    The link calls on_message() with the robot's asset.ack / asset.result and link_closed() when it ends."""

    def __init__(self, send, busy, *, chunk=CHUNK, reply_timeout_s=30.0, install_timeout_s=90.0, pause_max_s=180.0,
                 poll_s=0.2, clock=time.monotonic):
        self.send, self.busy, self.chunk = send, busy, chunk
        self.reply_timeout_s, self.install_timeout_s, self.pause_max_s = reply_timeout_s, install_timeout_s, pause_max_s
        self.poll_s, self.clock = poll_s, clock
        self._lock = asyncio.Lock()
        self._next_id = int(time.time()) % 0x7FFF0000 + 1
        self._tid = None
        self._inbox = asyncio.Queue()
        self.closed = False
        self.pushed = self.failed = 0

    def on_message(self, m):
        """A robot asset.ack / asset.result; ones for another transfer (late, aborted) are dropped."""
        if self._tid is not None and m.get("id") == self._tid:
            self._inbox.put_nowait(m)

    def link_closed(self):
        self.closed = True
        self._inbox.put_nowait(None)

    async def _get(self, timeout):
        """The next robot message, None on timeout; raises ConnectionError when the link closed."""
        try:
            m = await asyncio.wait_for(self._inbox.get(), max(0.0, timeout))
        except asyncio.TimeoutError:
            return None
        if m is None:
            self._inbox.put_nowait(None)          # stays closed for whoever waits next
            raise ConnectionError("robot_gone")
        return m

    async def _wait_quiet(self, deadline_s):
        """Until hello is not speaking; False when it spoke for longer than pause_max_s."""
        start = self.clock()
        while self.busy():
            if self.closed:
                raise ConnectionError("robot_gone")
            if self.clock() - start > deadline_s:
                return False
            await asyncio.sleep(self.poll_s)
        return True

    async def push(self, part, data, sha256=None, progress=None):
        """Send one catalog file; -> the robot's result dict (ok, unchanged, reason, ...) or an ear-side failure
        {"ok": False, "reason": ...}. progress(acked, total) is called on every ack."""
        if not isinstance(part, str) or not PART_RE.match(part):
            return {"ok": False, "part": "", "reason": "bad_part"}
        if not isinstance(data, (bytes, bytearray)) or not 0 < len(data) <= MAX_BYTES:
            return {"ok": False, "part": part, "reason": "bad_size"}
        digest = hashlib.sha256(data).hexdigest()
        if sha256 is not None and sha256.lower() != digest:
            return {"ok": False, "part": part, "reason": "sha256_mismatch_local"}
        async with self._lock:
            res = await self._push_locked(part, bytes(data), digest, progress)
        if res.get("ok"):
            self.pushed += 1
        else:
            self.failed += 1
        return res

    async def _push_locked(self, part, data, digest, progress):
        if self.closed:
            return {"ok": False, "part": part, "reason": "robot_gone"}
        tid = self._next_id
        self._next_id = self._next_id % 0x7FFFFFFF + 1
        self._tid = tid
        while not self._inbox.empty():               # nothing from an older transfer may be read as ours
            if self._inbox.get_nowait() is None:
                self._inbox.put_nowait(None)
                break
        total = len(data)
        try:
            if not await self._wait_quiet(self.pause_max_s):
                return {"ok": False, "part": part, "reason": "robot_speaking"}
            await self.send(json.dumps({"type": "asset.begin", "id": tid, "part": part, "bytes": total, "sha256": digest}))
            m = await self._get(self.reply_timeout_s)       # the check reads the card: ack 0 or a result
            if m is None:
                return await self._abort(tid, part, "no_reply")
            if m.get("type") == "asset.result":
                return m
            window = m.get("window") if _is_int(m.get("window")) and m.get("window") > 0 else 32768
            acked = sent = 0
            last = self.clock()
            while acked < total:
                if self.busy():                         # hello speaks: hold the chunks, the robot's clock waits too
                    if not await self._wait_quiet(self.pause_max_s):
                        return await self._abort(tid, part, "robot_speaking")
                    last = self.clock()
                while sent < total and not self.busy():
                    n = min(self.chunk, total - sent)
                    if sent - acked + n > window:
                        break
                    await self.send(pack(tid, sent, data[sent:sent + n]))
                    sent += n
                m = await self._get(self.poll_s)
                if m is None:
                    if self.clock() - last > self.reply_timeout_s:
                        return await self._abort(tid, part, "no_ack")
                    continue
                if m.get("type") == "asset.result":     # refused mid-way (timeout, out of order, ...)
                    return m
                if m.get("type") == "asset.ack" and _is_int(m.get("bytes")) and acked <= m["bytes"] <= sent:
                    acked, last = m["bytes"], self.clock()
                    if progress is not None:
                        progress(acked, total)
            await self.send(json.dumps({"type": "asset.end", "id": tid}))
            deadline = self.clock() + self.install_timeout_s
            while True:                                 # the robot writes .part, reads it back, renames
                m = await self._get(deadline - self.clock())
                if m is None:
                    return {"ok": False, "part": part, "reason": "no_result"}
                if m.get("type") == "asset.result":
                    return m
        except ConnectionError:
            return {"ok": False, "part": part, "reason": "robot_gone"}
        except Exception as e:                          # noqa: BLE001 - the robot's socket went away mid-send
            if type(e).__name__.startswith("ConnectionClosed"):
                return {"ok": False, "part": part, "reason": "robot_gone"}
            raise
        finally:
            self._tid = None

    async def _abort(self, tid, part, reason):
        try:
            await self.send(json.dumps({"type": "asset.abort", "id": tid}))
        except Exception:                               # noqa: BLE001 - gone already: nothing to stop
            pass
        return {"ok": False, "part": part, "reason": reason}


# ---- the loopback control (in the ear process) ----
def authorized(header, token):
    supplied = header[7:] if isinstance(header, str) and header.startswith("Bearer ") else ""
    return bool(token) and hmac.compare_digest(supplied.encode(), token.encode())


async def serve_control(ws, pick_link, token):
    """One control client on 127.0.0.1:52753/assets. pick_link() -> the MouthLink to push on (None: no robot)."""
    header = ws.request.headers.get("Authorization", "") if getattr(ws, "request", None) else ""
    path = ws.request.path if getattr(ws, "request", None) else ""
    if path.split("?")[0] != "/assets" or not authorized(header, token):
        await ws.close(code=4401, reason="bad token")
        return
    pending = None
    async for message in ws:
        if isinstance(message, str):
            try:
                m = json.loads(message)
            except ValueError:
                m = None
            if not isinstance(m, dict) or m.get("type") != "push":
                await ws.send(json.dumps({"type": "result", "ok": False, "reason": "bad_request"}))
                continue
            pending = m
            continue
        if pending is None:
            await ws.send(json.dumps({"type": "result", "ok": False, "reason": "bad_request"}))
            continue
        m, pending = pending, None
        if m.get("bytes") != len(message):
            await ws.send(json.dumps({"type": "result", "ok": False, "part": m.get("part"), "reason": "bad_size"}))
            continue
        link = pick_link()
        if link is None:
            res = {"ok": False, "part": m.get("part"), "reason": "no_robot"}
        elif link.assets is None:
            res = {"ok": False, "part": m.get("part"), "reason": "robot_firmware_has_no_assets"}
        else:
            loop = asyncio.get_running_loop()

            def progress(acked, total):
                loop.create_task(_quiet_send(ws, json.dumps({"type": "progress", "part": m.get("part"),
                                                             "acked": acked, "bytes": total})))
            log(f"assets: push {m.get('part')} ({len(message)} B) on mouth m{link.conn}", flush=True)
            res = await link.assets.push(m.get("part"), message, m.get("sha256"), progress)
            log(f"assets: {m.get('part')}: {'ok' if res.get('ok') else 'failed'}"
                f"{' unchanged' if res.get('unchanged') else ''} {res.get('reason') or ''}".rstrip(), flush=True)
        out = {k: v for k, v in res.items() if k != "type"}
        await ws.send(json.dumps({"type": "result", **out}))


async def _quiet_send(ws, text):
    try:
        await ws.send(text)
    except Exception:                                   # noqa: BLE001 - the CLI left: the push goes on
        pass


# ---- CLI (on the server, next to the ear) ----
def read_items(folder, pairs):
    """part=name pairs -> [(part, name, bytes)]; names are plain file names inside folder."""
    items = []
    for pair in pairs:
        part, sep, name = pair.partition("=")
        if not sep or not PART_RE.match(part) or not name or os.path.basename(name) != name or name.startswith("."):
            raise SystemExit(f"bad item {pair!r}: expected part=filename (a plain name inside --folder)")
        with open(os.path.join(folder, name), "rb") as f:
            data = f.read(MAX_BYTES + 1)
        if not 0 < len(data) <= MAX_BYTES:
            raise SystemExit(f"{name}: size {len(data)} B is outside 1..{MAX_BYTES}")
        items.append((part, name, data))
    return items


async def run_cli(url, token, items, out=print):
    from websockets.asyncio.client import connect
    failed = 0
    async with connect(url, additional_headers={"Authorization": f"Bearer {token}"}, max_size=64 * 1024,
                       open_timeout=10) as ws:
        for i, (part, name, data) in enumerate(items, 1):
            digest = hashlib.sha256(data).hexdigest()
            await ws.send(json.dumps({"type": "push", "part": part, "bytes": len(data), "sha256": digest}))
            await ws.send(data)
            shown = -1
            while True:
                m = json.loads(await ws.recv())
                if m.get("type") == "progress":
                    pct = int(100 * m.get("acked", 0) / max(1, m.get("bytes", 1)))
                    if pct // 25 != shown // 25:
                        out(f"  {part}: {pct}%")
                        shown = pct
                    continue
                if m.get("type") == "result":
                    break
            if m.get("ok"):
                out(f"{i}/{len(items)} {part} ({name}, {len(data)} B): "
                    f"{'already on SD (verified)' if m.get('unchanged') else 'installed and verified'}")
            else:
                failed += 1
                out(f"{i}/{len(items)} {part} ({name}): FAILED {m.get('reason')}")
                break                                   # license first: never a font part without it
    return failed


def main(argv=None):
    ap = argparse.ArgumentParser(description="Push SD catalog files to hello over the ear's /mouth link")
    ap.add_argument("--folder", required=True, help="folder holding the files (plain names only)")
    ap.add_argument("--token-file", default=os.environ.get("SOUL_TOKEN_FILE", "token"))
    ap.add_argument("--url", default=f"ws://127.0.0.1:{CONTROL_PORT}/assets")
    ap.add_argument("items", nargs="+", help="part=filename, in order (license first)")
    a = ap.parse_args(argv)
    if not a.url.startswith(("ws://127.0.0.1:", "ws://localhost:")):
        raise SystemExit("the asset control is loopback only")
    with open(a.token_file) as f:
        token = f.read().strip()
    items = read_items(a.folder, a.items)
    failed = asyncio.run(run_cli(a.url, token, items))
    if failed:
        sys.exit(1)
    print("all files verified on the robot's SD card")


if __name__ == "__main__":
    main()
