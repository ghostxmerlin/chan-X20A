"""Local demo of hello's modes: mind's real API + ModeStore + Billing on an in-memory database.

No Mongo, no robot, no soul-tts, no model, no paid worker: the 提前写 / 连续说 workers are the real classes with their
loops never started (their switches only flip, so the page shows them on / off); write / play functions raise if
anything ever tried. State is lost when this process stops. Steps in docs/mode-demo.md.

Run from the repo root:
    python3 -B scripts/mind-local-demo.py [--port 52790] [--settings-timeout 30] [--monitor-port 8807] [--no-monitor]
It also starts a second monitor (node web/monitor/server.mjs, port 8807, no robot) pointed at this demo, and stops it
again on Ctrl+C. With --no-monitor it only prints the line that starts that monitor. The running monitor on 8787 and
its .private config are never touched.
"""
import argparse
import os
import secrets
import signal
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, 'tests'), os.path.join(ROOT, 'server')]

from mind_fixtures import FakeDB                       # noqa: E402
from mind import api, mode_store, runner, say_auto, say_prefetch   # noqa: E402
from mind.settings_store import SettingsStore          # noqa: E402
from mind.store import Store                           # noqa: E402


def _never(*_a, **_k):
    raise RuntimeError('demo: nothing is written or played')


def _interrupt(*_):
    raise KeyboardInterrupt


def build(settings_timeout_s=300, clock_ms=lambda: int(time.time() * 1000)):
    """-> (store, ctx) for api.make_server; also used by tests."""
    mode_store.SETTINGS_TIMEOUT_MS = int(settings_timeout_s * 1000)
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
    ctx = {'settings_store': ss, 'say_worker': worker, 'say_auto': auto, 'mode_store': ms, 'billing': billing,
           'round_gate': gate}
    return store, ctx


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--port', type=int, default=52790, help='mind demo port on 127.0.0.1 (default 52790)')
    p.add_argument('--settings-timeout', type=float, default=30,
                   help='seconds settings stays open without input (live: 300; default here 30)')
    p.add_argument('--monitor-port', type=int, default=8807, help='port of the second monitor (default 8807)')
    p.add_argument('--no-monitor', action='store_true', help='do not start the second monitor, only print its command')
    a = p.parse_args()
    if not 1024 <= a.port <= 65535 or a.port == 52740:
        sys.exit('pick a port 1024-65535 other than 52740 (the real mind)')
    if not 1024 <= a.monitor_port <= 65535 or a.monitor_port == 8787:
        sys.exit('pick a monitor port 1024-65535 other than 8787 (the running monitor)')
    token = secrets.token_hex(16)
    store, ctx = build(a.settings_timeout)
    server = api.make_server(store, token, host='127.0.0.1', port=a.port, ctx=ctx)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    ms = ctx['mode_store']
    print(f'mind 本机演示：127.0.0.1:{a.port}，现在的模式 {ms.current()["mode"]}，'
          f'设置 {a.settings_timeout:g} 秒没动作自己关', flush=True)
    env = {'SOUL_MIND_LOCAL': f'http://127.0.0.1:{a.port}', 'SOUL_MIND_LOCAL_TOKEN': token,
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
        while True:              # as runner.main: settings closes itself after the timeout without input
            try:
                ms.tick()
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
        print('演示已停止', flush=True)


if __name__ == '__main__':
    main()
