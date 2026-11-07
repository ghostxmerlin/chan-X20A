#!/usr/bin/env python3
"""Manage the Mac background process; no login item or shell profile changes."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import time

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('action', choices=('start', 'stop', 'status'))
args = parser.parse_args()
os.umask(0o077)
pidfile = root/'.private/emotion-service.pid'
config = root/'.private/emotion-config.json'
pid = int(pidfile.read_text()) if pidfile.exists() else 0
command = subprocess.run(['ps','-p',str(pid),'-o','command='],capture_output=True,text=True).stdout if pid else ''
running = '-m emotion.app' in command and str(config) in command
if args.action == 'start':
    if not running:
        assert config.is_file(), 'Missing private emotion config'
        (root/'logs').mkdir(exist_ok=True, mode=0o700)
        env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false')
        with (root/'logs/emotion-service.log').open('a') as out, (root/'logs/emotion-service-errors.log').open('a') as err:
            process = subprocess.Popen([str(root/'.tools/emotion-venv/bin/python'),'-m','emotion.app','--config',str(config)],
                cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err, start_new_session=True)
        pidfile.write_text(str(process.pid))
    print('Mac test page: http://127.0.0.1:8765')
elif args.action == 'stop':
    if running:
        os.kill(pid, signal.SIGTERM)
        for _ in range(150):
            try: os.kill(pid,0)
            except ProcessLookupError: break
            state = subprocess.run(['ps','-p',str(pid),'-o','stat='],capture_output=True,text=True).stdout.strip()
            if not state or state.startswith('Z'): break
            time.sleep(.2)
        else: raise SystemExit('Service has not exited; inspect local logs before restarting')
        pidfile.unlink(missing_ok=True)
    print('Emotion service stopped' if running else 'No matching emotion service is running')
else:
    print('running' if running else 'stopped')
