"""/mouth link (2026-11-11, ear terminates it since 2026-11-14): the robot's speaker downlink.

The robot connects to the ear's public port at /mouth (like /vision); the ear has already checked the
device token. The ear now ends the robot's link itself and keeps its own link to soul-tts's mouth
server on the loopback (127.0.0.1:52751) behind it, so later steps can add mode messages on /mouth:

- The robot's first message must be its hello (device_id, boot_id, codecs, ...). The ear answers it
  itself with {"type":"hello","codec":"opus","frame_ms":60,"sample_rate":24000} (the robot checks only
  those three), then sends the robot's hello verbatim to soul-tts and drops soul-tts's own hello.
- After that, frames pass both ways unchanged: binary audio packets and tts.* text down, the robot's
  tts.playing / progress / done / stopped / error reports up.
- soul-tts drops or is not running: every stream it had started and not ended or stopped gets a
  {"type":"tts.stop","stream":N} to the robot (it clears the stream and hearing un-mutes); the robot's
  side stays open; the ear dials soul-tts again every retry_s seconds and replays the robot's hello.
  Robot reports meant for soul-tts while it is away are dropped (it has forgotten those streams).
- soul-tts closes with 4000 (it keeps one device and a newer link replaced this one): this robot side
  is stale, so it is closed and run() returns "upstream_replaced" (no reconnect, or the two links
  would take soul-tts from each other every retry_s).
- The robot leaves: the soul-tts link is closed at once (soul-tts never speaks to an absent robot) and
  run() returns "device_closed".
"""
import asyncio
import json

UPSTREAM = "ws://127.0.0.1:52751/mouth"
EAR_HELLO = json.dumps({"type": "hello", "codec": "opus", "frame_ms": 60, "sample_rate": 24000})
HELLO_TIMEOUT_S = 10
REPLACED = 4000                                     # soul-tts: "replaced by new connection"
log = print                                         # tests silence it


def _parse(message):
    """A text frame's JSON object, else None (binary, not JSON, not an object)."""
    if not isinstance(message, str):
        return None
    try:
        m = json.loads(message)
    except ValueError:
        return None
    return m if isinstance(m, dict) else None


def _stream(m):
    sid = m.get("stream")
    return sid if isinstance(sid, int) and not isinstance(sid, bool) else None


class _RobotGone(Exception):
    pass


class MouthLink:
    def __init__(self, client, *, upstream_url, token, connect=None, retry_s=5, conn=0):
        if connect is None:
            from websockets.asyncio.client import connect
        self.client, self.upstream_url, self.token, self.connect = client, upstream_url, token, connect
        self.retry_s, self.conn = retry_s, conn
        self.boot = None                    # the robot's boot_id from its hello
        self.device = None
        self.hello_text = None              # the robot's hello as received, replayed on every upstream dial
        self.playing = set()                # streams soul-tts started that are neither ended nor stopped
        self.upstream_drops = 0
        self._up = None
        self._lock = asyncio.Lock()         # robot reports wait while a fresh upstream is being greeted

    @property
    def upstream_up(self):
        return self._up is not None

    async def run(self):
        """Serve the robot until it leaves; returns why ("device_closed", "upstream_replaced", "no_hello", "bad_hello")."""
        why = await self._hello()
        if why:
            return why
        reader = asyncio.create_task(self._from_robot())
        upper = asyncio.create_task(self._upstream_loop())
        try:
            await asyncio.wait({reader, upper}, return_when=asyncio.FIRST_COMPLETED)
        finally:                                        # also when the ear cancels this handler
            first_upper = upper.done() and not reader.done()
            for task in (reader, upper):
                task.cancel()
            await asyncio.gather(reader, upper, return_exceptions=True)   # a dropped link ends one with ConnectionClosed
            up, self._up = self._up, None
            if up is not None:
                await up.close()
        why = "device_closed"
        if first_upper and not upper.cancelled() and upper.exception() is None:
            why = upper.result()
        if why == "upstream_replaced":
            await self.client.close(code=REPLACED, reason="replaced by new connection")
        else:
            await self.client.close()
        return why

    async def _hello(self):
        try:
            first = await asyncio.wait_for(self.client.recv(), HELLO_TIMEOUT_S)
        except asyncio.TimeoutError:
            await self.client.close(code=4408, reason="no hello")
            return "no_hello"
        except Exception:                               # noqa: BLE001 - robot left before its hello
            return "device_closed"
        m = _parse(first)
        if m is None or m.get("type") != "hello":
            await self.client.close(code=4400, reason="first message must be hello")
            return "bad_hello"
        self.hello_text, self.boot, self.device = first, m.get("boot_id"), m.get("device_id")
        try:
            await self.client.send(EAR_HELLO)
        except Exception:                               # noqa: BLE001 - robot left right after its hello
            return "device_closed"
        return None

    async def _from_robot(self):
        async for message in self.client:
            m = _parse(message)
            if m is not None and m.get("type") in ("tts.done", "tts.stopped", "tts.error"):
                self.playing.discard(_stream(m))
            async with self._lock:
                up = self._up
                if up is None:
                    continue                            # soul-tts away: it has forgotten these streams
                try:
                    await up.send(message)
                except Exception:                       # noqa: BLE001 - the upstream loop sees the drop
                    pass

    async def _to_robot(self, message):
        try:
            await self.client.send(message)
        except Exception as e:                          # noqa: BLE001 - robot gone: run() ends via the reader
            raise _RobotGone() from e

    async def _upstream_loop(self):
        """Keep a soul-tts link while the robot is here; returns "upstream_replaced" or "device_closed"."""
        while True:
            code = await self._one_upstream()
            for sid in sorted(self.playing):
                try:
                    await self._to_robot(json.dumps({"type": "tts.stop", "stream": sid}))
                except _RobotGone:
                    return "device_closed"
            self.playing.clear()
            if code == REPLACED:
                return "upstream_replaced"
            await asyncio.sleep(self.retry_s)

    async def _one_upstream(self):
        """One dial and session; returns soul-tts's close code (None: could not connect)."""
        try:
            up = await self.connect(self.upstream_url, additional_headers={"Authorization": f"Bearer {self.token}"},
                                    max_size=64 * 1024, open_timeout=5)
        except Exception:                               # noqa: BLE001 - soul-tts down or refusing
            return None
        try:
            async with self._lock:
                await up.send(self.hello_text)
                self._up = up
            log(f"mouth m{self.conn}: soul-tts up", flush=True)
            async for message in up:
                m = _parse(message)
                if m is not None:
                    kind, sid = m.get("type"), _stream(m)
                    if kind == "hello":
                        continue                        # the robot already has the ear's hello
                    if kind == "tts.start" and sid is not None:
                        self.playing.add(sid)
                    elif kind in ("tts.end", "tts.stop"):
                        self.playing.discard(sid)
                await self._to_robot(message)
        except _RobotGone:
            raise
        except Exception:                               # noqa: BLE001 - soul-tts dropped
            pass
        finally:
            if self._up is up:
                self._up = None
            await up.close()
        self.upstream_drops += 1
        log(f"mouth m{self.conn}: soul-tts down ({up.close_code})", flush=True)
        return up.close_code
