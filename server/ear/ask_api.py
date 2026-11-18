"""The ear's local request API for mind's tools (L1, 2026-11-18; docs/hello-tools.md §6).

POST http://127.0.0.1:<port>/ask  {"name": ..., "args": {...}}  ->  {"ok", "data", "error": {code, message}}
Authorization: Bearer <the ear's token>. Bound to 127.0.0.1 only; mind (same server) is the only caller.
Asks: who_is_here, look_now, web_search, say, stop_speaking, set_length, set_quiet, set_role.
A small asyncio HTTP/1.1 reader (no new dependency); one request per connection, body <= 16 KiB.
"""
import asyncio
import hmac
import json
import time

import ear_log

MAX_BODY = 16384
LOOK_WAIT_S = 14.0
SEARCH_TIMEOUT_S = 25.0
SCENE_FRESH_MS = 90_000


class AskError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _ago(ms, now_ms):
    s = max(0, (now_ms - ms) // 1000)
    return f"{s} 秒前" if s < 120 else f"{s // 60} 分钟前"


class Asks:
    """The handlers; hub = RealtimeHub (or None), burst = VisionBurst, presence = presence.Presence, searcher =
    web_search.dashscope_search(...) result, paused() -> list of pause reasons (empty = not paused)."""
    def __init__(self, hub=None, burst=None, presence=None, searcher=None, paused=None, wall_ms=None, pool=None):
        self.hub, self.burst, self.presence, self.searcher = hub, burst, presence, searcher
        self.paused = paused or (lambda: [])
        self.wall_ms = wall_ms or (lambda: int(time.time() * 1000))
        self.pool = pool

    def _hub(self):
        if self.hub is None:
            raise AskError("not_available", "Docking 没有部署")
        return self.hub

    def _docking(self):
        hub = self._hub()
        if not hub.switch.on:
            raise AskError("switch_off", "Docking（实时对话）开关关着")
        if not hub.in_crowd():
            raise AskError("not_available", "机器人现在不在 Docking 模式")
        return hub

    async def who_is_here(self, args):
        now = self.wall_ms()
        people = self.hub.faces_now() if self.hub is not None else []
        doc = self.presence.doc(now) if self.presence is not None else {}
        out = {"faces": people, "faces_in_view": len(people)}
        if doc.get("voice_at"):
            out["last_voice"] = _ago(doc["voice_at"], now)
        if doc.get("owner_at"):
            out["owner_last"] = _ago(doc["owner_at"], now)
        sc = self.hub.scene_now() if self.hub is not None else None
        if sc and now - sc[0] <= SCENE_FRESH_MS:
            out["scene"] = {"text": sc[1], "ago": _ago(sc[0], now)}
        return out

    async def look_now(self, args):
        now = self.wall_ms()
        if self.paused():
            raise AskError("paused", "现在暂停着（没人在），不拍照")
        hub = self._hub()
        mark = None
        if self.burst is not None and self.burst.allowed():
            with ear_log.scope(trace=args.get("_trace")):
                mark = self.burst.ask("tool")
        if mark is None:
            sc = hub.scene_now()
            if sc and now - sc[0] <= SCENE_FRESH_MS:
                return {"answer": sc[1], "fresh": False, "photo_ago": _ago(sc[0], now),
                        "note": "现在拍不了新照片（不在 Docking），这是最近一张"}
            raise AskError("not_available", "现在拍不了照片：要在 Docking 模式、开关开着、机器人在线")
        fut = asyncio.get_running_loop().create_future()
        hub.look_waiters.append((mark, fut))
        try:
            text = await asyncio.wait_for(fut, LOOK_WAIT_S)
        except asyncio.TimeoutError:
            raise AskError("timeout", "等了十几秒没等到照片") from None
        return {"answer": text, "fresh": True, "photo_ago": "刚刚"}

    async def web_search(self, args):
        if self.searcher is None:
            raise AskError("not_available", "联网搜索没有配置")
        if self.paused():
            raise AskError("paused", "现在暂停着，不联网搜")
        q = str(args.get("query") or "").strip()
        if not q:
            raise AskError("bad_args", "要搜什么？")
        loop = asyncio.get_running_loop()
        with ear_log.scope(trace=args.get("_trace")):
            import contextvars
            ctx = contextvars.copy_context()
        try:
            text = await asyncio.wait_for(loop.run_in_executor(self.pool, ctx.run, self.searcher, q), SEARCH_TIMEOUT_S)
        except asyncio.TimeoutError:
            raise AskError("timeout", "搜索超时了") from None
        if not text:
            raise AskError("failed", "没搜到")
        return {"answer": text}

    async def say(self, args):
        hub = self._docking()
        if hub.quiet_on() and not args.get("interrupt"):
            raise AskError("refused", "主人让它安静着")
        if args.get("interrupt"):
            hub.stop_speaking()
            await asyncio.sleep(0.3)
        r = hub.say(args.get("text") or "", trace=args.get("_trace"))
        if r == "not_ready":
            raise AskError("not_available", "Docking 会话没开（没人或暂停中）")
        if r == "busy":
            raise AskError("refused", "它正在说话或有人在说话，等一下再说")
        return {"spoken": True}

    async def stop_speaking(self, args):
        return {"stopped": bool(self._hub().stop_speaking())}

    async def set_length(self, args):
        until = self._hub().set_length(args["mode"], trace=args.get("_trace"))
        return {"mode": args["mode"], "minutes": 10 if until else None}

    async def set_quiet(self, args):
        level = args.get("level") or "quiet"
        hub = self._hub()
        until = hub.set_quiet(level, args.get("minutes"), trace=args.get("_trace"))
        return {"level": level, "minutes": int((until - hub.clock()) // 60) if until else None,
                "ends": "被叫 hello / Dynames 就结束" if level == "quiet" else "说「可以说话了」或到时间" if level == "low" else None}

    async def set_role(self, args):
        hub = self._hub()
        until = hub.set_role(args.get("role") or None, args.get("minutes"), trace=args.get("_trace"))
        return {"role": args.get("role") or None, "minutes": int((until - hub.clock()) // 60) if until else None}

    NAMES = ("who_is_here", "look_now", "web_search", "say", "stop_speaking", "set_length", "set_quiet", "set_role")

    async def handle(self, name, args):
        if name not in self.NAMES:
            raise AskError("not_available", f"耳朵没有 {str(name)[:40]}")
        if not isinstance(args, dict):
            raise AskError("bad_args", "参数应该是一个对象")
        return await getattr(self, name)(args)


def _response(status, obj):
    body = json.dumps(obj, ensure_ascii=False).encode()
    reason = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found", 413: "Too Large"}.get(status, "OK")
    return (f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode() + body


async def serve(asks, token, host="127.0.0.1", port=52743):
    async def client(reader, writer):
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            lines = head.decode("latin-1").split("\r\n")
            method, path = (lines[0].split(" ") + ["", ""])[:2]
            hdr = {k.strip().lower(): v.strip() for k, v in (l.split(":", 1) for l in lines[1:] if ":" in l)}
            supplied = hdr.get("authorization", "")[len("Bearer "):] if hdr.get("authorization", "").startswith("Bearer ") else ""
            if not token or not hmac.compare_digest(supplied.encode(), token.encode()):
                writer.write(_response(401, {"error": "bad token"}))
                return
            if method != "POST" or path != "/ask":
                writer.write(_response(404, {"error": "POST /ask only"}))
                return
            n = int(hdr.get("content-length") or 0)
            if n > MAX_BODY:
                writer.write(_response(413, {"error": "too large"}))
                return
            req = json.loads((await asyncio.wait_for(reader.readexactly(n), 5)).decode() or "{}")
            name, args = req.get("name"), dict(req.get("args") or {})
            if req.get("trace"):
                args["_trace"] = str(req["trace"])[:64]
            try:
                data = await asks.handle(name, args)
                out = {"ok": True, "data": data, "error": None}
            except AskError as e:
                out = {"ok": False, "data": None, "error": {"code": e.code, "message": e.message}}
            except Exception as e:                       # noqa: BLE001 - one failed ask, never the ear
                out = {"ok": False, "data": None, "error": {"code": "failed", "message": f"耳朵出错了（{type(e).__name__}）"}}
            writer.write(_response(200, out))
        except Exception:                                # noqa: BLE001 - a broken request
            try:
                writer.write(_response(400, {"error": "bad request"}))
            except Exception:                            # noqa: BLE001
                pass
        finally:
            try:
                await writer.drain()
                writer.close()
            except Exception:                            # noqa: BLE001
                pass
    return await asyncio.start_server(client, host, port)
