"""Set up the Chain DualKey's network over USB: Wi-Fi, server address and token. Never prints the token.

    python3 tools/dualkey_provision.py                       # asks for SSID/password; server+token from .env
    python3 tools/dualkey_provision.py --ssid NAME           # asks only for the password
    python3 tools/dualkey_provision.py --mock                # server = local mock (tools/dualkey_mock_server.py)
    python3 tools/dualkey_provision.py --status              # just print the device status
    python3 tools/dualkey_provision.py --no-wifi             # only server + token, keep the saved Wi-Fi

Server: SOUL_EAR_URL in .env (only scheme://host:port is used; the device appends /signal).
Token:  SOUL_EAR_TOKEN in .env (or the mock's throwaway token with --mock).
The DualKey is found by its USB serial number (tools/dualkey_usb.py); the robot's port is never opened.
Opening the USB console restarts the DualKey; that is expected.
Needs pyserial: run with the ESP-IDF venv python if the system one lacks it:
    .tools/espressif/python_env/idf5.5_py3.13_env/bin/python tools/dualkey_provision.py
"""
import argparse
import getpass
import json
import pathlib
import subprocess
import sys
import time
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import dualkey_usb  # noqa: E402

try:
    import serial
except ImportError:
    sys.exit("pyserial missing: run with .tools/espressif/python_env/idf5.5_py3.13_env/bin/python")


def read_env():
    env = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip().removeprefix("export ").strip()] = v.strip().strip('"').strip("'")
    return env


def server_base(url):
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("ws", "wss") or not u.hostname:
        sys.exit(f"server must be ws://host:port or wss://host:port (got scheme {u.scheme!r})")
    return f"{u.scheme}://{u.hostname}" + (f":{u.port}" if u.port else "")


def lan_ip():
    for iface in ("en0", "en10", "en1", "en2", "en3", "en4", "en5", "en6", "en7", "en8", "en9"):
        out = subprocess.run(["ipconfig", "getifaddr", iface], capture_output=True, text=True).stdout.strip()
        if out:
            return out
    sys.exit("no LAN IP on this Mac")


class Console:
    def __init__(self, verbose=False):
        self.port = dualkey_usb.require_port()
        self.verbose = verbose
        self.ser = serial.Serial()
        self.ser.port = self.port
        self.ser.baudrate = 115200
        self.ser.timeout = 0.1
        self.ser.dtr = False
        self.ser.rts = False
        self.ser.open()
        self.buf = b""

    def lines(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            try:
                chunk = self.ser.read(4096)
            except serial.SerialException:
                time.sleep(0.5)
                self.reopen()
                continue
            self.buf += chunk
            while b"\n" in self.buf:
                line, self.buf = self.buf.split(b"\n", 1)
                text = line.decode("utf-8", "replace").strip("\r")
                if self.verbose and "token" not in text.lower() and "password" not in text.lower():
                    print("  |", text)
                yield text

    def reopen(self):
        try:
            self.ser.close()
        except Exception:
            pass
        for _ in range(20):
            port = dualkey_usb.find_port()
            if port:
                self.ser.port = port
                try:
                    self.ser.open()
                    return
                except serial.SerialException:
                    pass
            time.sleep(0.5)
        sys.exit("DualKey USB console did not come back")

    def request(self, obj, seconds=5):
        data = (json.dumps(obj) + "\n").encode()
        for attempt in range(3):
            self.ser.write(data)
            self.ser.flush()
            for text in self.lines(seconds):
                if text.startswith('{"ok":'):
                    return json.loads(text)
        sys.exit("no reply from the DualKey (is the new firmware running?)")


def show(status):
    s = status.get("status", {})
    w, srv, q, j = s.get("wifi", {}), s.get("server", {}), s.get("queue", {}), s.get("joystick", {})
    print(f"固件 {s.get('fw')}  设备 {s.get('device')}  开机 {s.get('uptime_ms', 0) // 1000} s")
    print(f"Wi-Fi  已存 {[x for x in w.get('saved', []) if x]}  连接={w.get('connected')}  {w.get('ssid')}  IP {w.get('ip')}  RSSI {w.get('rssi')}")
    print(f"服务器 {srv.get('url') or '(未设置)'}  令牌={'已设置' if srv.get('token_set') else '未设置'}  "
          f"连接={srv.get('connected')}  握手={srv.get('hello_ok')}  连接次数 {srv.get('connects')}  失败 {srv.get('failures')}")
    print(f"队列   待确认 {q.get('pending')}  已丢 {q.get('dropped')}  已发 {q.get('sent')}  已确认 {q.get('acked')}  最新 seq {q.get('last_seq')}")
    print(f"摇杆   {'已找到' if j.get('found') else '未找到'}  x={j.get('x')} y={j.get('y')}  旋转 {j.get('rotate')}")
    if s.get("last_error"):
        print(f"最近错误 {s['last_error']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ssid")
    ap.add_argument("--password")
    ap.add_argument("--ssid2")
    ap.add_argument("--password2")
    ap.add_argument("--server", help="ws://host:port (default: SOUL_EAR_URL from .env)")
    ap.add_argument("--mock", action="store_true", help="use the local mock server and its throwaway token")
    ap.add_argument("--mock-port", type=int, default=52790)
    ap.add_argument("--no-wifi", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--joystick-rotate", type=int, choices=[0, 90, 180, 270])
    ap.add_argument("--wait", type=float, default=30, help="seconds to wait for Wi-Fi + server after saving")
    ap.add_argument("-v", "--verbose", action="store_true", help="also show the device log (secrets filtered)")
    a = ap.parse_args()

    con = Console(a.verbose)
    print(f"DualKey on {con.port} (USB serial {dualkey_usb.DUALKEY_SERIAL})")
    for _ in con.lines(2.5):  # opening the port restarts the chip; let it boot
        pass
    if a.status:
        show(con.request({"status": 1}))
        return

    cfg = {}
    if not a.no_wifi:
        ssid = a.ssid or input("Wi-Fi 名称 (SSID): ").strip()
        password = a.password if a.password is not None else getpass.getpass("Wi-Fi 密码（不显示）: ")
        nets = [{"ssid": ssid, "password": password}]
        if a.ssid2:
            p2 = a.password2 if a.password2 is not None else getpass.getpass(f"{a.ssid2} 的密码（不显示）: ")
            nets.append({"ssid": a.ssid2, "password": p2})
        cfg["wifi"] = nets
    env = read_env()
    if a.mock:
        cfg["server"] = f"ws://{lan_ip()}:{a.mock_port}"
        tok = ROOT / ".private" / "dualkey-mock-token"
        if not tok.exists():
            sys.exit("start tools/dualkey_mock_server.py first (it creates the mock token)")
        cfg["token"] = tok.read_text().strip()
    else:
        url = a.server or env.get("SOUL_EAR_URL")
        token = env.get("SOUL_EAR_TOKEN")
        if not url or not token:
            sys.exit("SOUL_EAR_URL / SOUL_EAR_TOKEN missing in .env")
        cfg["server"] = server_base(url)
        cfg["token"] = token
    if a.joystick_rotate is not None:
        cfg["joystick_rotate"] = a.joystick_rotate

    r = con.request(cfg)
    cfg.clear()
    if not r.get("ok"):
        sys.exit(f"device refused: {r.get('error')}")
    print("已保存。等待连网……")
    deadline = time.time() + a.wait
    status = None
    while time.time() < deadline:
        status = con.request({"status": 1})
        srv = status.get("status", {}).get("server", {})
        if srv.get("hello_ok"):
            break
        time.sleep(2)
    show(status)


if __name__ == "__main__":
    main()
