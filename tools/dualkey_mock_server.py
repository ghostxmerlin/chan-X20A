"""Local stand-in for the ear server's /signal endpoint, for testing the DualKey before the real one exists.

Speaks the DualKey protocol: checks `Authorization: Bearer <token>`, answers hello with {"type":"hello","ok":true},
acks every signal, prints each event in plain words and appends it to logs/dualkey-<date>/mock-events.jsonl.
Open http://<ip>:<port>/ in a browser for a live event page (no auth: local test only).

    python tools/dualkey_mock_server.py                      # binds the Mac's LAN IP, port 52790
    python tools/dualkey_mock_server.py --host <Mac LAN IP> --port 52790 --token-file .private/dualkey-mock-token

The token is a throwaway: generated on first run into .private/dualkey-mock-token (git-ignored) and never printed.
Run it with the ESP-IDF venv python (it has `websockets`):
    .tools/espressif/python_env/idf5.5_py3.13_env/bin/python tools/dualkey_mock_server.py
"""
import argparse
import asyncio
import datetime
import hmac
import json
import pathlib
import secrets
import socket
import subprocess
import time
from http import HTTPStatus

from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Response

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOGS = ROOT / "logs" / f"dualkey-{datetime.date.today().strftime('%Y%m%d')}"
DEFAULT_TOKEN_FILE = ROOT / ".private" / "dualkey-mock-token"
WATCHERS = set()

PAGE = """<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DualKey 测试</title>
<style>
:root{--bg:#fafaf7;--fg:#1d1d1b;--muted:#77756e;--line:#e3e1da;--good:#1d7a3a;--bad:#b3261e;--mode:#2456a6;--click:#7a4d00}
@media (prefers-color-scheme:dark){:root{--bg:#151513;--fg:#ecebe6;--muted:#9a988f;--line:#2c2b28;--good:#5fc77d;--bad:#ff8a80;--mode:#8fb4ff;--click:#f0b860}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,system-ui,sans-serif}
main{max-width:760px;margin:0 auto;padding:16px}
h1{font-size:18px;margin:0 0 4px}#state{color:var(--muted);margin-bottom:12px}
.row{display:flex;gap:12px;padding:8px 0;border-top:1px solid var(--line);align-items:baseline}
.t{color:var(--muted);font-variant-numeric:tabular-nums;min-width:72px}.k{font-weight:600;min-width:64px}
.good{color:var(--good)}.bad{color:var(--bad)}.mode{color:var(--mode)}.click{color:var(--click)}.sys{color:var(--muted)}
code{font-size:12px;color:var(--muted);word-break:break-all}
</style><main><h1>DualKey 测试（本机模拟服务器）</h1><div id="state">连接中…</div><div id="list"></div></main>
<script>
const list=document.getElementById('list'),state=document.getElementById('state');
function add(e){const d=document.createElement('div');d.className='row';
 const t=new Date(e.t*1000).toLocaleTimeString();let k='sys',txt=e.text;
 if(e.kind==='rating'){k=e.value.polarity>0?'good':'bad'}else if(e.kind==='mode'){k='mode'}else if(e.kind==='click'){k='click'}
 d.innerHTML=`<span class="t">${t}</span><span class="k ${k}">${e.label}</span><span>${txt}<br><code>${e.raw||''}</code></span>`;
 list.prepend(d)}
function go(){const ws=new WebSocket(`ws://${location.host}/watch`);
 ws.onopen=()=>state.textContent='已连接，等待 DualKey 事件';ws.onclose=()=>{state.textContent='页面断开，2 秒后重连';setTimeout(go,2000)};
 ws.onmessage=m=>add(JSON.parse(m.data))}
go();
</script></html>"""


def lan_ip():
    for iface in ("en0", "en10", "en1", "en2", "en3", "en4", "en5", "en6", "en7", "en8", "en9"):
        out = subprocess.run(["ipconfig", "getifaddr", iface], capture_output=True, text=True).stdout.strip()
        if out:
            return out
    return socket.gethostbyname(socket.gethostname())


def load_token(path):
    path = pathlib.Path(path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(16) + "\n")
        path.chmod(0o600)
    return path.read_text().strip()


def describe(msg):
    kind = msg.get("kind")
    v = msg.get("value") or {}
    if msg.get("type") == "hello":
        return "hello", "sys", f"设备上线 {msg.get('device')} 固件 {msg.get('fw')} boot {msg.get('boot')}"
    if kind == "rating":
        word = "好" if v.get("polarity", 0) > 0 else "坏"
        cap = "（超过 5 下，已封顶）" if v.get("capped") else ""
        return ("打分", kind, f"{word} × {v.get('count')}{cap}  按键间隔 {v.get('presses_ms')} ms")
    if kind == "mode":
        names = {"up": "上", "down": "下", "left": "左", "right": "右"}
        return "模式", kind, f"摇杆推{names.get(v.get('direction'), v.get('direction'))}（{v.get('direction')}）"
    if kind == "click":
        names = {"single": "单击", "double": "双击", "long": "长按"}
        return "点击", kind, f"摇杆{names.get(v.get('type'), v.get('type'))}"
    return msg.get("type", "?"), "sys", json.dumps(msg, ensure_ascii=False)


def record(entry):
    LOGS.mkdir(parents=True, exist_ok=True)
    with (LOGS / "mock-events.jsonl").open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    for w in list(WATCHERS):
        asyncio.ensure_future(w.send(json.dumps(entry, ensure_ascii=False)))


def make_handlers(token):
    def process_request(connection, request):
        path = request.path.split("?")[0]
        if path == "/" and "upgrade" not in request.headers.get("Connection", "").lower():
            body = PAGE.encode()
            return Response(HTTPStatus.OK, "OK", Headers({"Content-Type": "text/html; charset=utf-8",
                                                           "Content-Length": str(len(body))}), body)
        if path == "/watch":
            return None
        if path != "/signal":
            return connection.respond(HTTPStatus.NOT_FOUND, "not found\n")
        auth = request.headers.get("Authorization", "")
        if not hmac.compare_digest(auth, f"Bearer {token}"):
            print(f"[{time.strftime('%H:%M:%S')}] 拒绝：令牌不对（来自 {connection.remote_address[0]}）", flush=True)
            return connection.respond(HTTPStatus.UNAUTHORIZED, "bad token\n")
        return None

    async def handler(ws):
        path = ws.request.path.split("?")[0]
        if path == "/watch":
            WATCHERS.add(ws)
            try:
                await ws.wait_closed()
            finally:
                WATCHERS.discard(ws)
            return
        peer = ws.remote_address[0]
        print(f"[{time.strftime('%H:%M:%S')}] DualKey 已连接（{peer}）", flush=True)
        device = None
        try:
            async for raw in ws:
                t = time.time()
                try:
                    msg = json.loads(raw)
                except ValueError:
                    print("  非 JSON：", raw[:120], flush=True)
                    continue
                typ = msg.get("type")
                if typ == "ping":
                    continue
                if typ == "hello":
                    device = msg.get("device")
                    await ws.send(json.dumps({"type": "hello", "ok": True}))
                elif typ == "signal":
                    await ws.send(json.dumps({"type": "ack", "seq": msg.get("seq")}))
                label, kind, text = describe(msg)
                extra = f"  seq={msg.get('seq')} age_ms={msg.get('age_ms')}" if typ == "signal" else ""
                print(f"[{time.strftime('%H:%M:%S')}] {label}：{text}{extra}", flush=True)
                record({"t": t, "peer": peer, "device": device, "label": label, "kind": kind,
                        "value": msg.get("value"), "text": text + extra, "raw": raw})
        except Exception as e:  # connection drops are normal during tests
            print(f"  连接结束：{type(e).__name__}", flush=True)
        print(f"[{time.strftime('%H:%M:%S')}] DualKey 断开", flush=True)
        record({"t": time.time(), "peer": peer, "device": device, "label": "断开", "kind": "sys", "text": "DualKey 断开"})

    return process_request, handler


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=52790)
    ap.add_argument("--token-file", default=str(DEFAULT_TOKEN_FILE))
    a = ap.parse_args()
    host = a.host or lan_ip()
    token = load_token(a.token_file)
    process_request, handler = make_handlers(token)
    async with serve(handler, host, a.port, process_request=process_request, ping_interval=20):
        print(f"mock /signal on ws://{host}:{a.port}/signal  (token in {a.token_file}, not shown)", flush=True)
        print(f"event page: http://{host}:{a.port}/", flush=True)
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
