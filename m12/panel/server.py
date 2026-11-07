#!/usr/bin/env python3
"""UnitV2 face panel — a local dashboard for face detection / recognition results
plus live system stats (temperature, CPU, ...). It never pulls the video stream.

Run:   python3 server.py        then open http://localhost:8765
Env:   UNITV2_HOST (10.254.239.1)  UNITV2_USER (m5stack)  UNITV2_PASS (12345678)
       PANEL_PORT (8765)  PANEL_NO_BROWSER=1 to skip opening the browser
Stdlib only. Stats are read over SSH (password fed via SSH_ASKPASS).
"""
import atexit
import base64
import json
import os
import queue
import re
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = os.environ.get("UNITV2_HOST", "10.254.239.1")
USER = os.environ.get("UNITV2_USER", "m5stack")
PASS = os.environ.get("UNITV2_PASS", "12345678")
PORT = int(os.environ.get("PANEL_PORT", "8765"))
HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, "static", "index.html")
ASKPASS = os.path.join(HERE, "askpass.sh")

# panel name -> (type_id, device function name)
FUNCS = {
    "face_detector": ("10", "face_detector"),
    "face_recognition": ("9", "face_recognition"),
    # Device has no "stop". audio_fft is the idle state: camera off, ~15% CPU (camera_stream w/o preview
    # still costs ~70%), and its {"meta": ...} output is never written to the serial port.
    "standby": ("13", "audio_fft"),
}
RUNNING = {"face_detector": "Face Detector", "face_recognition": "Face Recognition"}  # result doc "running"
BIN = {name: fname for name, (_, fname) in FUNCS.items()}                              # process name in ps
HISTORY_SECONDS = 30 * 60
MAX_FACES = 10
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,20}$")

# Remote loop: one "STAT ..." line every 2 s (13 tokens).
REMOTE_STATS = r"""
G=/sys/devices/system/cpu/cpufreq/policy0
cpu() { awk '/^cpu /{t=0; for(i=2;i<=NF;i++) t+=$i; print t, $5+$6}' /proc/stat; }
set -- $(cpu); pt=$1; pi=$2
while true; do
  sleep 2
  set -- $(cpu); t=$1; i=$2
  dt=$((t-pt)); di=$((i-pi)); pt=$t; pi=$i
  if [ "$dt" -gt 0 ]; then c=$(( (100*(dt-di))/dt )); else c=0; fi
  up=$(cut -d' ' -f1 /proc/uptime)
  tp=$(cut -d' ' -f2 /sys/class/mstar/msys/TEMP_R)
  fq=$(cat $G/scaling_cur_freq)
  gv=$(cat $G/scaling_governor)
  ld=$(cut -d' ' -f1-3 /proc/loadavg)
  mm=$(awk '/^MemTotal/{t=$2}/^MemAvailable/{a=$2}END{print t, a}' /proc/meminfo)
  # single awk, no early-exit pipeline: /bin/sh is zsh on the device and `| head` made this flaky
  fn=$(ps | awk '!f && /[.]\/bin\// { s=$0; sub(/.*[.]\/bin\//, "", s); sub(/[^a-z_].*/, "", s); print s; f=1 }')
  co=$(ps | grep -c '[s]erver_core')
  echo "STAT $up $tp $fq $gv $c $ld $mm ${fn:-none} $co"
done
"""


# ---------------------------------------------------------------- event hub
class Hub:
    def __init__(self):
        self.lock = threading.Lock()
        self.clients = set()

    def add(self):
        q = queue.Queue(maxsize=600)
        with self.lock:
            self.clients.add(q)
        return q

    def remove(self, q):
        with self.lock:
            self.clients.discard(q)

    def send(self, event, data):
        msg = sse_bytes(event, data)
        with self.lock:
            clients = list(self.clients)
        for q in clients:
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass


def sse_bytes(event, data):
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


hub = Hub()


# ---------------------------------------------------------------- shared state
class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.stats = None
        self.history = deque()          # (t, temp)
        self.ssh = False
        self.ssh_err = ""
        self.result_conn = False
        self.result_err = ""
        self.result = None              # {"t", "rate", "doc"}
        self.rate_times = deque()
        self.faces = []                 # registered names, index == device face_id
        self.accept_device_faces = True
        self.training = None            # {"face_id", "name"}
        self.unsaved = False
        self.switching = None           # panel name the UI is waiting for
        self.switch_t0 = 0.0
        self.want = None                # latest requested target not yet sent/finished on the device
        self.worker = False             # a switch_worker thread is talking to the device

    def conn(self):
        return {"ssh": self.ssh, "ssh_err": self.ssh_err,
                "result": self.result_conn, "result_err": self.result_err}

    def train_state(self):
        return {"faces": list(self.faces), "training": self.training, "unsaved": self.unsaved}

    def snapshot(self):
        with self.lock:
            return {
                "stats": self.stats,
                "history": [[round(t, 1), v] for t, v in self.history],
                "result": self.result,
                "conn": self.conn(),
                "switching": self.switching,
                **self.train_state(),
            }


S = State()
need_reconnect = threading.Event()
children = []


def push_conn():
    with S.lock:
        c = S.conn()
    hub.send("conn", c)


def push_train():
    with S.lock:
        t = S.train_state()
    hub.send("train", t)


# ---------------------------------------------------------------- device HTTP
def device_request(method, path, body=None, timeout=10):
    data, headers = None, {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json;charset=UTF-8"
    elif method == "POST":
        data = b""
    req = urllib.request.Request(f"http://{HOST}{path}", data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


# ---------------------------------------------------------------- result stream
def handle_result(raw):
    raw = raw.strip()
    if not raw:
        return
    try:
        doc = json.loads(raw)
    except ValueError:
        return
    if not isinstance(doc, dict):
        return
    now = time.time()
    with S.lock:
        S.rate_times.append(now)
        while S.rate_times and now - S.rate_times[0] > 2.0:
            S.rate_times.popleft()
        rate = round(len(S.rate_times) / 2.0, 1)
        S.result = {"t": now, "rate": rate, "doc": doc}
        msg = S.result
        ready = None
        sw = S.switching
        if sw in RUNNING and doc.get("running") == RUNNING[sw]:
            ready = round(now - S.switch_t0, 1)   # first result from the new function = switch done
            S.switching = None
    if ready is not None:
        hub.send("switch", {"switching": None, "done": sw, "seconds": ready})
    hub.send("result", msg)


def set_result_conn(ok, err=""):
    with S.lock:
        changed = (S.result_conn != ok) or (S.result_err != err)
        S.result_conn, S.result_err = ok, err
    if changed:
        push_conn()


def result_loop():
    """One long-lived POST /func/result. Reconnect only on error / device reboot:
    every reconnect leaves a polling generator thread on the device until it writes."""
    while True:
        need_reconnect.clear()
        try:
            device_request("GET", "/", timeout=15)  # sets client_is_connected=True on the device
            sock = socket.create_connection((HOST, 80), timeout=5)
        except (OSError, urllib.error.URLError) as e:
            set_result_conn(False, f"连接失败：{e}")
            time.sleep(3)
            continue
        backoff = 2
        try:
            sock.sendall(f"POST /func/result HTTP/1.0\r\nHost: {HOST}\r\nContent-Length: 0\r\n\r\n".encode())
            # Werkzeug only sends the response headers with the first result, so in standby (no results)
            # the headers may never come. Treat "request sent" as connected; a non-200 status flips it back.
            set_result_conn(True)
            buf, header = b"", None
            while not need_reconnect.is_set():
                try:
                    chunk = sock.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buf += chunk
                if header is None:
                    i = buf.find(b"\r\n\r\n")
                    if i < 0:
                        if len(buf) > 65536:
                            break
                        continue
                    header, buf = buf[:i].decode("latin-1"), buf[i + 4:]
                    parts = header.split(" ", 2)
                    status = parts[1] if len(parts) > 1 else "?"
                    if status != "200":
                        set_result_conn(False, f"设备返回 HTTP {status}（可能处于过热保护）")
                        backoff = 10
                        break
                *items, buf = buf.split(b"|")
                for it in items:
                    handle_result(it.decode("utf-8", "replace"))
                if len(buf) > 1_000_000:
                    buf = b""
        except OSError as e:
            set_result_conn(False, f"结果流中断：{e}")
        finally:
            sock.close()
        with S.lock:
            still = S.result_conn
        if still:
            set_result_conn(False, "结果流已断开，正在重连")
        time.sleep(backoff)


# ---------------------------------------------------------------- stats over SSH
def ssh_argv(remote):
    b64 = base64.b64encode(remote.encode()).decode()
    return ["ssh", "-T",
            "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
            "-o", "PubkeyAuthentication=no", "-o", "PreferredAuthentications=password,keyboard-interactive",
            "-o", "NumberOfPasswordPrompts=1", "-o", "ConnectTimeout=8",
            "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2",
            f"{USER}@{HOST}", f"echo {b64} | base64 -d | sh"]


def ssh_env():
    env = dict(os.environ)
    env.update(SSH_ASKPASS=ASKPASS, SSH_ASKPASS_REQUIRE="force", UNITV2_PASS=PASS)
    env.setdefault("DISPLAY", ":0")
    return env


def parse_stat(line):
    tok = line.split()
    if len(tok) != 13 or tok[0] != "STAT":
        return None
    try:
        up, temp, fq, gv, cpu, l1, l5, l15, mt, ma, fn, core = tok[1:]
        core_on = int(core) > 0
        return {
            "uptime": float(up), "temp": int(temp), "freq_mhz": int(fq) // 1000, "governor": gv,
            "cpu": max(0, min(100, int(cpu))), "load": [float(l1), float(l5), float(l15)],
            "mem_total_mb": int(mt) // 1024, "mem_used_mb": (int(mt) - int(ma)) // 1024,
            "func": None if fn == "none" else fn, "core": core_on,
            "overheat": gv == "powersave" and not core_on,
        }
    except ValueError:
        return None


def set_ssh(ok, err=""):
    with S.lock:
        changed = (S.ssh != ok) or (S.ssh_err != err)
        S.ssh, S.ssh_err = ok, err
    if changed:
        push_conn()


def stats_loop():
    last_up = None
    was_offline = False
    while True:
        try:
            p = subprocess.Popen(ssh_argv(REMOTE_STATS), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, bufsize=1, env=ssh_env())
        except OSError as e:
            set_ssh(False, f"无法启动 ssh：{e}")
            time.sleep(5)
            continue
        children.append(p)
        last_err = ""
        for line in p.stdout:
            st = parse_stat(line)
            if st is None:
                if line.strip():
                    last_err = line.strip()[:200]
                continue
            now = time.time()
            st["t"] = now
            if was_offline or (last_up is not None and st["uptime"] + 5 < last_up):
                need_reconnect.set()   # device rebooted / came back: re-open result stream
                was_offline = False
            last_up = st["uptime"]
            prev_func = None
            with S.lock:
                prev_func = S.stats["func"] if S.stats else None
                S.stats = st
                S.history.append((now, st["temp"]))
                while S.history and now - S.history[0][0] > HISTORY_SECONDS:
                    S.history.popleft()
                if st["func"] == "face_recognition" and prev_func != "face_recognition":
                    S.accept_device_faces = True
                if st["func"] != "face_recognition" and S.training:
                    S.training = None
                ready = None
                if S.switching == "standby" and st["func"] == BIN["standby"]:
                    ready = round(now - S.switch_t0, 1)   # standby emits no results; seeing the process is enough
                    S.switching = None
            if ready is not None:
                hub.send("switch", {"switching": None, "done": "standby", "seconds": ready})
            if not S.ssh:
                set_ssh(True)
            hub.send("stats", st)
        p.wait()
        if p in children:
            children.remove(p)
        set_ssh(False, last_err or "SSH 连接断开，正在重连")
        was_offline = True
        time.sleep(3)


# ---------------------------------------------------------------- registered faces
def faces_loop():
    """While face_recognition runs, read the device's face list once after each (re)start."""
    while True:
        time.sleep(2)
        with S.lock:
            running = S.stats and S.stats.get("func") == "face_recognition"
            accept = S.accept_device_faces
        if not running or not accept:
            continue
        try:
            _, body = device_request("POST", "/data_from_device", timeout=5)
            doc = json.loads(body)
        except (OSError, ValueError, urllib.error.URLError):
            continue
        if not isinstance(doc, dict) or doc.get("running") != "Face Recognition":
            continue
        faces = doc.get("faces")
        if not isinstance(faces, list):
            continue
        names = [str(f.get("name", "")) for f in faces if isinstance(f, dict)]
        with S.lock:
            S.faces = names
            S.accept_device_faces = False
            S.unsaved = False
        push_train()


# ---------------------------------------------------------------- API actions
def api_func(body):
    """Return immediately. The UI is told the switch is done as soon as the new function's first result
    arrives (device's /func itself blocks ~4 s, 3 s of which is a fixed sleep)."""
    name = body.get("name")
    if name not in FUNCS:
        return 400, {"ok": False, "error": "未知功能"}
    with S.lock:
        if not S.worker and not S.switching and S.stats and S.stats.get("func") == BIN[name]:
            return 200, {"ok": True, "noop": True}          # already running; don't restart it
        S.want = name
        S.switching = name
        S.switch_t0 = time.time()
        S.training = None
        start = not S.worker
        S.worker = True
    hub.send("switch", {"switching": name})
    if start:
        threading.Thread(target=switch_worker, daemon=True, name="switch_worker").start()
    return 202, {"ok": True, "pending": True}


def switch_worker():
    """Serialize device /func calls; if the user changed their mind meanwhile, apply only the latest."""
    while True:
        with S.lock:
            target = S.want
        type_id, fname = FUNCS[target]
        try:
            status, text = device_request("POST", "/func", {"type_id": type_id, "type_name": fname, "args": ""},
                                          timeout=40)
            ok = status == 200 and text.lstrip().startswith("{")
            err = "" if ok else f"设备返回异常：{text[:120]}"
        except (OSError, urllib.error.URLError) as e:
            ok, err = False, f"请求失败：{e}"
        with S.lock:
            finished = S.want == target
            if finished:
                S.want, S.worker = None, False
            if ok and target == "face_recognition":
                S.accept_device_faces = True
            fallback = finished and S.switching == target     # no early signal came; fall back to HTTP return
            if fallback or (not ok and S.switching == target):
                S.switching = None
            seconds = round(time.time() - S.switch_t0, 1)
        if not ok:
            hub.send("switch", {"switching": None, "error": err})
        elif fallback:
            hub.send("switch", {"switching": None, "done": target, "seconds": seconds})
        push_train()
        if finished:
            return


def api_train(body):
    op = body.get("op")
    with S.lock:
        running = S.stats and S.stats.get("func") == "face_recognition"
        faces = list(S.faces)
        training = S.training
    if not running:
        return 409, {"ok": False, "error": "请先打开「人脸识别」"}

    if op == "train":
        name = str(body.get("name", "")).strip()
        try:
            face_id = int(body.get("face_id"))
        except (TypeError, ValueError):
            return 400, {"ok": False, "error": "face_id 无效"}
        if not NAME_RE.match(name):
            return 400, {"ok": False, "error": "名字只能用英文字母、数字、_ 或 -（最多 20 个字符）"}
        if face_id < 0 or face_id > len(faces) or face_id >= MAX_FACES:
            return 400, {"ok": False, "error": f"最多只能登记 {MAX_FACES} 个人"}
        payload = {"operation": "train", "face_id": face_id, "name": name}
    elif op == "stoptrain":
        if not training:
            return 409, {"ok": False, "error": "当前没有在登记"}
        payload = {"operation": "stoptrain", "face_id": training["face_id"], "name": training["name"]}
    elif op in ("saverun", "reset"):
        payload = {"operation": op}
    else:
        return 400, {"ok": False, "error": "未知操作"}

    try:
        status, text = device_request("POST", "/data_to_device", payload, timeout=10)
        ok = status == 200 and text.strip() == "ok"
        err = "" if ok else f"设备返回异常：{text[:120]}"
    except (OSError, urllib.error.URLError) as e:
        ok, err = False, f"请求失败：{e}"
    if not ok:
        return 502, {"ok": False, "error": err}

    with S.lock:
        if op == "train":
            fid, name = payload["face_id"], payload["name"]
            if fid == len(S.faces):
                S.faces.append(name)
            else:
                S.faces[fid] = name
            S.training = {"face_id": fid, "name": name}
            S.unsaved = True
        elif op == "stoptrain":
            S.training = None
        elif op == "saverun":
            S.unsaved = False
        elif op == "reset":
            S.faces, S.training, S.unsaved = [], None, True
    push_train()
    return 200, {"ok": True}


# ---------------------------------------------------------------- HTTP server
class Handler(BaseHTTPRequestHandler):
    server_version = "UnitV2Panel/1.0"

    def log_message(self, fmt, *args):
        pass

    def send_json(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            try:
                with open(INDEX, "rb") as f:
                    data = f.read()
            except OSError:
                self.send_error(500, "index.html missing")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif path == "/events":
            self.events()
        else:
            self.send_error(404)

    def do_POST(self):
        # JSON content type forces a CORS preflight for cross-site pages, which we never answer.
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            self.send_json(415, {"ok": False, "error": "需要 application/json"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            self.send_json(400, {"ok": False, "error": "JSON 格式错误"})
            return
        if self.path == "/api/func":
            code, obj = api_func(body)
        elif self.path == "/api/train":
            code, obj = api_train(body)
        else:
            code, obj = 404, {"ok": False, "error": "not found"}
        self.send_json(code, obj)

    def events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        q = hub.add()
        try:
            self.wfile.write(sse_bytes("init", S.snapshot()))
            self.wfile.flush()
            while True:
                try:
                    msg = q.get(timeout=15)
                except queue.Empty:
                    msg = b": ping\n\n"
                self.wfile.write(msg)
                self.wfile.flush()
        except OSError:
            pass
        finally:
            hub.remove(q)


def cleanup():
    for p in list(children):
        try:
            p.terminate()
        except OSError:
            pass


def _exit_on_signal(signum, frame):
    raise SystemExit(0)


def main():
    os.chmod(ASKPASS, 0o700)
    atexit.register(cleanup)
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _exit_on_signal)   # closing the terminal / kill → still clean up ssh
    for fn in (stats_loop, result_loop, faces_loop):
        threading.Thread(target=fn, daemon=True, name=fn.__name__).start()
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as e:
        if e.errno == 48:  # EADDRINUSE on macOS
            raise SystemExit(f"端口 {PORT} 已被占用：面板可能已经在另一个终端里运行了。\n"
                             f"先关掉那个（Ctrl+C），或者换个端口：PANEL_PORT=8766 python3 server.py")
        raise
    httpd.daemon_threads = True
    url = f"http://localhost:{PORT}"
    print(f"UnitV2 人脸面板已启动：{url}   （Ctrl+C 退出）", flush=True)
    if not os.environ.get("PANEL_NO_BROWSER"):
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
