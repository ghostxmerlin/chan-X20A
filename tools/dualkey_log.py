"""Record the DualKey's USB console for N seconds (opening it restarts the chip). Secrets are filtered.

    .tools/espressif/python_env/idf5.5_py3.13_env/bin/python tools/dualkey_log.py 20 logs/dualkey-<date>/boot.log
"""
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import dualkey_usb  # noqa: E402
import serial  # noqa: E402

seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 15
out = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else None
port = dualkey_usb.require_port()
ser = serial.Serial()
ser.port, ser.baudrate, ser.timeout, ser.dtr, ser.rts = port, 115200, 0.2, False, False
ser.open()
buf = b""
end = time.time() + seconds
log = out.open("a") if out else None
if log:
    log.write(f"# capture {time.strftime('%Y-%m-%dT%H:%M:%S')} port={port}\n")
while time.time() < end:
    try:
        chunk = ser.read(4096)
    except serial.SerialException as e:
        print("serial ended:", e, flush=True)
        time.sleep(1)
        port = dualkey_usb.find_port()
        if not port:
            continue
        ser = serial.Serial()
        ser.port, ser.baudrate, ser.timeout, ser.dtr, ser.rts = port, 115200, 0.2, False, False
        try:
            ser.open()
        except serial.SerialException:
            pass
        continue
    buf += chunk
    while b"\n" in buf:
        line, buf = buf.split(b"\n", 1)
        text = line.decode("utf-8", "replace").rstrip("\r")
        low = text.lower()
        if "bearer" in low or "password" in low or '"token"' in low:
            continue
        print(text, flush=True)
        if log:
            log.write(text + "\n")
            log.flush()
