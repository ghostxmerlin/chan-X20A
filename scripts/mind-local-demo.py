"""Local demo of hello's modes (step 1b: hello owns the mode): mind's real API + ModeStore + Billing on an in-memory
database, and a stand-in hello that runs the reference rules (mind/modes.py transition, the same rules the firmware
replays as test vectors) and reports every change to mind the way hello does over /mouth.

No Mongo, no robot, no soul-tts, no model, no paid worker: the 提前写 / 连续说 workers are the real classes with their
loops never started (their switches only flip, so the page shows them on / off); write / play functions raise if
anything ever tried. State is lost when this process stops. Steps in docs/mode-demo.md.

Run from the repo root:
    python3 -B scripts/mind-local-demo.py [--port 52790] [--settings-timeout 30] [--monitor-port 8807] [--no-monitor]
It also starts a second monitor (node web/monitor/server.mjs, port 8807) pointed at this demo, with the stand-in hello
as its robot (port = demo port + 1), and stops it again on Ctrl+C. With --no-monitor it only prints the line that starts that monitor. The running monitor on 8787 and
its .private config are never touched.
"""
import argparse
import hmac
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, 'tests'), os.path.join(ROOT, 'server')]

from mind_fixtures import FakeDB                       # noqa: E402
from mind import api, mode_store, modes, runner, say_auto, say_prefetch   # noqa: E402
from mind.settings_store import SettingsStore          # noqa: E402
from mind.store import Store                           # noqa: E402


def _never(*_a, **_k):
    raise RuntimeError('demo: nothing is written or played')


def _interrupt(*_):
    raise KeyboardInterrupt


class FakeHello:
    """A stand-in for hello's mode side (0.4.92): the state lives here, the rules are mind/modes.py's transition, every
    change is reported to mind (ModeStore.report, as ear forwards hello's mode_report). It has just booted: the first
    report is by boot (stop_speech + talk_enter, an entry into talk). "Speaking" is never true here (nothing plays).
    The settings timer runs here too, as on hello. 4-8 as a digit enter that mode (coordinator ruling 2026-11-14:
    never toggle off; already there = already)."""
    DIGIT_SELECT = {4: {'mode': 'idle'}, 5: {'mode': 'crowd'}, 6: {'mode': 'companion'},
                    7: {'mode': 'subtitle', 'dir': 'zh_en'}, 8: {'mode': 'subtitle', 'dir': 'en_zh'}}

    def __init__(self, ms, *, settings_timeout_ms, clock_ms=lambda: int(time.time() * 1000)):
        self.ms, self.timeout_ms, self.clock_ms = ms, settings_timeout_ms, clock_ms
        self.boot = 'demo-' + secrets.token_hex(4)
        self.lock = threading.Lock()
        self.state = dict(mode_store.BOOT_STATE)
        self.seq = self.tseq = 0
        self.by, self.since = 'boot', clock_ms()
        self.last_input = self.since
        self.report(None, ['stop_speech', 'talk_enter'], 'boot')

    def report(self, inp, effects, by):
        self.seq += 1
        rep = {'seq': self.seq, **self.state, 'by': by, 'input': inp, 'effects': list(effects)}
        return self.ms.report(rep, boot=self.boot, conn=1)

    def apply(self, inp, value, source):
        """One input on hello -> (result, report reply or None)."""
        with self.lock:
            now = self.clock_ms()
            self.last_input = now
            if inp == 'digit' and value in self.DIGIT_SELECT:
                target = self.DIGIT_SELECT[value]
                if (self.state['mode'], self.state['dir']) == (target['mode'], target.get('dir')):
                    return modes.result('already'), None              # a digit only enters, never leaves
                inp, value = 'select', target
            new, res, effects = modes.transition(self.state, inp, value, source=source, speaking=False)
            changed = new != self.state
            if changed:
                self.state, self.by, self.since = new, source, now
            reply = self.report(inp, effects, source) if changed or effects else None
            return res, reply

    def tick(self):
        with self.lock:
            due = self.state['mode'] == 'settings' and self.clock_ms() - self.last_input > self.timeout_ms
        if due:
            self.apply('timeout', None, 'timeout')

    def info(self):
        return {'boot_id': self.boot, 'firmware': 'demo-hello', 'capabilities': {'web_control': 'supported',
                                                                               'unified_control': 'supported'}}

    def telemetry(self):
        with self.lock:
            self.tseq += 1
            return {'boot_id': self.boot, 'seq': self.tseq, 'timestamp_us': self.tseq * 1000,
                    'mode': {**self.state, 'seq': self.seq, 'by': self.by, 'since': self.since}}


def hello_server(hello, token, port):
    """The stand-in hello on loopback: GET /v1/info, GET /v1/telemetry, POST /v1/control {target: mode, input, value}."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, payload):
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _ok(self):
            got = self.headers.get('Authorization', '')
            if not hmac.compare_digest(got.encode(), f'Bearer {token}'.encode()):
                self._send(401, {'error': 'bad token'})
                return False
            return True

        def do_GET(self):
            if not self._ok():
                return
            path = self.path.split('?')[0]
            if path == '/v1/info':
                return self._send(200, hello.info())
            if path == '/v1/telemetry':
                return self._send(200, hello.telemetry())
            self._send(404, {'error': 'not found'})

        def do_POST(self):
            if not self._ok():
                return
            try:
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
            except ValueError:
                return self._send(400, {'error': 'bad json'})
            if self.path != '/v1/control' or not isinstance(body, dict) or body.get('target') != 'mode':
                return self._send(404, {'error': 'demo hello only knows target mode'})
            if body.get('input') not in modes.INPUTS or body['input'] in ('billing', 'boot', 'timeout'):
                return self._send(400, {'error': 'unknown input'})
            res, _ = hello.apply(body['input'], body.get('value'), 'page')
            self._send(200, {'accepted': True, 'result': res})
    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def build(settings_timeout_s=300, clock_ms=lambda: int(time.time() * 1000)):
    """-> (store, ctx) for api.make_server (ctx['hello'] = the stand-in FakeHello); also used by tests."""
    db = FakeDB()
    store, ss = Store(db), SettingsStore(db, os.path.join(ROOT, '.nonexistent-demo-settings.json'))
    mouth = lambda: (db['switches'].find_one({'_id': 'mouth'}) or {}).get('mode', 'clips')
    worker = say_prefetch.Worker(store, settings_fn=lambda: ss.current()[0], mouth_fn=mouth, ctx_fn=_never,
                                 write_fn=_never)
    auto = say_auto.Worker(store, prefetch=worker, settings_fn=lambda: ss.current()[0], mouth_fn=mouth,
                           play_fn=_never)
    ms, billing, gate = runner.wire_modes(db, store, settings_store=ss, tts=None, say_worker=worker, say_auto=auto,
                                          say_round_fn=None, clock_ms=clock_ms)
    worker.mode_fn = auto.mode_fn = lambda: ms.current()['mode']
    hello = FakeHello(ms, settings_timeout_ms=int(settings_timeout_s * 1000), clock_ms=clock_ms)
    ctx = {'settings_store': ss, 'say_worker': worker, 'say_auto': auto, 'mode_store': ms, 'billing': billing,
           'round_gate': gate, 'hello': hello}
    return store, ctx


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--port', type=int, default=52790, help='mind demo port on 127.0.0.1 (default 52790)')
    p.add_argument('--settings-timeout', type=float, default=30,
                   help='seconds settings stays open without input (live: 300; default here 30)')
    p.add_argument('--monitor-port', type=int, default=8807, help='port of the second monitor (default 8807)')
    p.add_argument('--no-monitor', action='store_true', help='do not start the second monitor, only print its command')
    a = p.parse_args()
    if not 1024 <= a.port <= 65534 or a.port in (52739, 52740):
        sys.exit('pick a port 1024-65534 other than 52740 (the real mind); the stand-in hello takes the next one')
    if not 1024 <= a.monitor_port <= 65535 or a.monitor_port == 8787:
        sys.exit('pick a monitor port 1024-65535 other than 8787 (the running monitor)')
    token, hello_token = secrets.token_hex(16), secrets.token_hex(16)
    store, ctx = build(a.settings_timeout)
    server = api.make_server(store, token, host='127.0.0.1', port=a.port, ctx=ctx)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    hello = ctx['hello']
    hserver = hello_server(hello, hello_token, a.port + 1)
    threading.Thread(target=hserver.serve_forever, daemon=True).start()
    ms = ctx['mode_store']
    print(f'mind 本机演示：127.0.0.1:{a.port}，假 hello：127.0.0.1:{a.port + 1}，现在的模式 {ms.current()["mode"]}，'
          f'设置 {a.settings_timeout:g} 秒没动作自己关', flush=True)
    env = {'SOUL_MIND_LOCAL': f'http://127.0.0.1:{a.port}', 'SOUL_MIND_LOCAL_TOKEN': token,
           'SOUL_DEMO_ROBOT': f'http://127.0.0.1:{a.port + 1}', 'SOUL_DEMO_ROBOT_TOKEN': hello_token,
           'SOUL_MONITOR_PORT': str(a.monitor_port)}
    monitor = None
    if a.no_monitor:
        print('另开一个终端窗口，在仓库根目录运行：', flush=True)
        print(' '.join(f'{k}={v}' for k, v in env.items()) + ' node web/monitor/server.mjs', flush=True)
    else:
        monitor = subprocess.Popen(['node', 'web/monitor/server.mjs'], cwd=ROOT, env={**os.environ, **env})
    print(f'打开 http://127.0.0.1:{a.monitor_port}/ ；按 Ctrl+C 停止演示', flush=True)
    signal.signal(signal.SIGTERM, _interrupt)
    try:
        while True:              # as on hello: settings closes itself after the timeout without input
            try:
                hello.tick()
            except Exception as e:                    # noqa: BLE001 - keep the demo alive
                print(f'tick: {type(e).__name__}', flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        if monitor is not None and monitor.poll() is None:
            monitor.terminate()
            try:
                monitor.wait(5)
            except subprocess.TimeoutExpired:
                monitor.kill()
        server.shutdown()
        hserver.shutdown()
        print('演示已停止', flush=True)


if __name__ == '__main__':
    main()
