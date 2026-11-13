"""Find the Chain DualKey's USB serial port by its USB serial number (never by tty name).

The robot "hello" (7C:4F:AD:AF:90:B4) is another ESP32-S3 on the same Mac; opening its port resets it.
Everything in tools/dualkey_*.py goes through find_port(), which refuses the robot's serial number.

    python3 tools/dualkey_usb.py            # prints the DualKey port, exit 1 if absent
"""
import plistlib
import subprocess
import sys

DUALKEY_SERIAL = "3C:0F:02:E8:AE:1C"
ROBOT_SERIAL = "7C:4F:AD:AF:90:B4"


def _walk(node, serial=None):
    """Yield (usb_serial, callout_device) pairs for every serial device in the IORegistry tree."""
    serial = node.get("USB Serial Number", serial)
    callout = node.get("IOCalloutDevice")
    if callout:
        yield serial, callout
    for child in node.get("IORegistryEntryChildren", []):
        yield from _walk(child, serial)


def usb_serial_ports():
    raw = subprocess.run(["ioreg", "-r", "-c", "IOUSBHostDevice", "-l", "-a"], capture_output=True, check=True).stdout
    roots = plistlib.loads(raw) if raw.strip() else []
    pairs = []
    for root in roots:
        pairs.extend(_walk(root))
    return pairs


def find_port(serial=DUALKEY_SERIAL):
    """Return the /dev/cu.* path for the given USB serial number, or None. Never returns the robot's port."""
    if serial.upper() == ROBOT_SERIAL:
        raise SystemExit("refusing: that is the robot's USB serial number")
    hits = sorted({dev for ser, dev in usb_serial_ports() if ser and ser.upper() == serial.upper() and dev.startswith("/dev/cu.")})
    robot = {dev for ser, dev in usb_serial_ports() if ser and ser.upper() == ROBOT_SERIAL}
    hits = [h for h in hits if h not in robot]
    if len(hits) > 1:
        raise SystemExit(f"more than one port for {serial}: {hits}")
    return hits[0] if hits else None


def require_port(expected=None, serial=DUALKEY_SERIAL):
    """Find the port now and abort if it is missing or differs from `expected`."""
    port = find_port(serial)
    if not port:
        raise SystemExit(f"DualKey ({serial}) is not on USB")
    if expected and port != expected:
        raise SystemExit(f"DualKey port changed: {expected} -> {port}; aborting")
    return port


if __name__ == "__main__":
    port = find_port()
    if not port:
        print("DualKey not found", file=sys.stderr)
        sys.exit(1)
    print(port)
