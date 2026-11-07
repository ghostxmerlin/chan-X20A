"""Loopback-only dashboard; frames/transcripts stay in memory, key never leaves server."""
import asyncio
from pathlib import Path
import time

from aiohttp import web
from .core import EVIDENCE_TTL

LABELS = {"unknown": "不确定", "neutral": "平静", "happy": "开心", "sad": "低落",
          "angry": "生气", "anxious": "紧张", "surprised": "惊讶"}


async def serve(app, port):
    allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}

    @web.middleware
    async def local_only(request, handler):
        if request.host not in allowed or request.headers.get("Origin", "http://"+request.host) not in {"http://"+x for x in allowed}:
            raise web.HTTPForbidden()
        response = await handler(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; frame-ancestors 'none'"
        return response

    async def index(request):
        return web.Response(text=Path(__file__).with_name("preview.html").read_text(), content_type="text/html")

    async def state(request):
        now = time.monotonic()
        result = app.result
        latest_robot_us = max(app.audio_us, app.camera[2] if app.camera else 0)
        fresh = bool(result and result[4] == app.robot.boot and now-result[0] < EVIDENCE_TTL
                     and 0 <= latest_robot_us-result[1] < EVIDENCE_TTL*1000000 and not app.paused)
        caption = LABELS.get(result[2], "不确定") if fresh else "不确定"
        speech = app.transcript
        return web.json_response({
            "ready": app.engine is not None, "connected": app.robot.boot is not None,
            "paused": app.paused, "caption": caption,
            "evidence": result[3] if fresh else "none", "inferred": True,
            "stt": app.stt_state, "stt_outputs": app.stt_outputs,
            "remaining": max(0, round(600 - (now-app.watchdog.last_output))),
            "transcript": speech[2] if speech and now-speech[0] < 30 else "",
            "transcript_age": round(now-speech[0],1) if speech else None,
            "camera_fresh": bool(app.camera and now-app.camera[1] < 3),
            "audio_fresh": now-app.audio_at < 1, "rms": app.rms if now-app.audio_at < 1 else 0,
            "inferences": app.inferences, "audio_gaps": app.dropped_audio, "demo": app.demo})

    async def frame(request):
        if not app.camera or time.monotonic()-app.camera[1] >= 3:
            raise web.HTTPNoContent()
        return web.Response(body=app.camera[3], content_type="image/jpeg")

    async def pause(request):
        app.paused = not app.paused
        app.result = app.transcript = None
        return web.json_response({"paused": app.paused})

    async def demo(request):
        if not app.engine:
            raise web.HTTPServiceUnavailable(text="模型正在加载")
        if getattr(app, "demo_busy", False):
            raise web.HTTPConflict(text="正在判断上一条样例")
        data = await request.json()
        text = data.get("text", "")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 500:
            raise web.HTTPBadRequest(text="请输入 1–500 字")
        app.demo_busy = True
        try:
            label, source = await asyncio.get_running_loop().run_in_executor(app.model_pool, app.engine.infer, text, None)
            app.demo = {"caption": LABELS[label], "evidence": source, "text": text}
            return web.json_response(app.demo)
        finally:
            app.demo_busy = False

    server = web.Application(middlewares=[local_only], client_max_size=4096)
    server.add_routes([web.get("/", index), web.get("/state", state), web.get("/frame", frame),
                       web.post("/pause", pause), web.post("/demo", demo)])
    runner = web.AppRunner(server, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner
