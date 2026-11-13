"""Identify, back up, write and verify the Chain DualKey — and only the DualKey.

Every esptool call re-finds the port by USB serial number right before it runs and aborts on any mismatch
(see tools/dualkey_usb.py). The robot's port is never opened.

    python3 tools/dualkey_flash.py identify          # flash_id + read_mac + partition table + security info
    python3 tools/dualkey_flash.py backup            # full 8 MB read to backups/, then verify_flash
    python3 tools/dualkey_flash.py write  [app.bin]  # factory app partition only (offset from the read table)
    python3 tools/dualkey_flash.py verify [app.bin]  # independent verify_flash of the app partition
    python3 tools/dualkey_flash.py restore BACKUP    # full image back to 0x0, then verify_flash

Logs go to logs/dualkey-<date>/ (git-ignored).
"""
import datetime
import hashlib
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import dualkey_usb  # noqa: E402

PY = ROOT / ".tools/espressif/python_env/idf5.5_py3.13_env/bin/python"
MAC = "3c:0f:02:e8:ae:1c"
MAC6 = MAC.replace(":", "")[-6:]
FLASH_SIZE = 8 * 1024 * 1024
TODAY = datetime.date.today().strftime("%Y%m%d")
LOGS = ROOT / "logs" / f"dualkey-{TODAY}"
DEFAULT_APP = ROOT / "dualkey/build/dualkey.bin"
STATE = LOGS / "identity.json"


def esptool(args, label, after="no_reset", before="default_reset", timeout=600):
    port = dualkey_usb.require_port()
    cmd = [str(PY), "-m", "esptool", "--chip", "esp32s3", "--port", port, "--baud", "921600",
           "--before", before, "--after", after] + args
    LOGS.mkdir(parents=True, exist_ok=True)
    log = LOGS / f"{label}.log"
    with log.open("a") as out:
        out.write(f"# {datetime.datetime.now().isoformat()} port={port} serial={dualkey_usb.DUALKEY_SERIAL}\n")
        out.write("# " + " ".join(cmd[1:]) + "\n")
        out.flush()
        proc = subprocess.run(cmd, stdout=out, stderr=subprocess.STDOUT, timeout=timeout)
    text = log.read_text()
    if proc.returncode != 0:
        tail = "\n".join(text.splitlines()[-15:])
        raise SystemExit(f"esptool {args[0]} failed (see {log}):\n{tail}")
    return text


def check_mac(text):
    m = re.findall(r"MAC: ([0-9a-f:]{17})", text, re.I)
    if not m or m[-1].lower() != MAC:
        raise SystemExit(f"MAC mismatch: expected {MAC}, got {m}")


def partitions():
    raw = LOGS / "partitions.bin"
    esptool(["read_flash", "0x8000", "0xc00", str(raw)], "partitions")
    sys.path.insert(0, str(ROOT / ".tools/esp-idf-v5.5.4/components/partition_table"))
    import gen_esp32part
    table = gen_esp32part.PartitionTable.from_binary(raw.read_bytes())
    table.verify()
    return table


def identify():
    text = esptool(["flash_id"], "identify")
    check_mac(text)
    if "ESP32-S3" not in text or "Detected flash size: 8MB" not in text:
        raise SystemExit("unexpected chip or flash size; see identify.log")
    text_mac = esptool(["read_mac"], "identify")
    check_mac(text_mac)
    sec = esptool(["get_security_info"], "identify")
    flash_crypt = re.search(r"Flash Encryption: (\w+)", sec)
    secure_boot = re.search(r"Secure Boot: (\w+)", sec)
    table = partitions()
    rows = [{"name": p.name, "type": p.type, "subtype": p.subtype, "offset": hex(p.offset), "size": hex(p.size),
             "encrypted": bool(p.encrypted)} for p in table]
    factory = next((p for p in table if p.type == 0 and p.name == "factory"), None)
    if not factory:
        raise SystemExit("no factory app partition in the table")
    chip = re.search(r"Chip is (.+)", text)
    report = {"port": dualkey_usb.require_port(), "usb_serial": dualkey_usb.DUALKEY_SERIAL, "mac": MAC,
              "chip": chip.group(1).strip() if chip else None, "flash_size": "8MB",
              "flash_encryption": flash_crypt.group(1) if flash_crypt else None,
              "secure_boot": secure_boot.group(1) if secure_boot else None,
              "partitions": rows, "app": {"name": factory.name, "offset": hex(factory.offset), "size": hex(factory.size)}}
    STATE.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    if report["flash_encryption"] not in (None, "Disabled") or report["secure_boot"] not in (None, "Disabled"):
        raise SystemExit("flash encryption or secure boot is enabled: stop, do not write")
    return report


def backup():
    identify()
    out = ROOT / "backups" / f"dualkey-{MAC6}-factory-{TODAY}.bin"
    if out.exists():
        raise SystemExit(f"refusing to overwrite existing backup {out}")
    out.parent.mkdir(exist_ok=True)
    esptool(["read_flash", "0x0", hex(FLASH_SIZE), str(out)], "backup", timeout=900)
    if out.stat().st_size != FLASH_SIZE:
        raise SystemExit(f"backup has wrong size {out.stat().st_size}")
    esptool(["verify_flash", "0x0", str(out)], "backup-verify", timeout=900)
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    (out.with_suffix(".bin.sha256")).write_text(f"{digest}  {out.name}\n")
    print(json.dumps({"backup": str(out.relative_to(ROOT)), "bytes": FLASH_SIZE, "sha256": digest, "verify_flash": "ok"}))


def app_partition():
    report = json.loads(STATE.read_text()) if STATE.exists() else identify()
    return int(report["app"]["offset"], 16), int(report["app"]["size"], 16)


def write(app):
    backups = list((ROOT / "backups").glob(f"dualkey-{MAC6}-factory-*.bin"))
    if not backups:
        raise SystemExit("no full factory backup yet: run `backup` first")
    identify()
    offset, size = app_partition()
    data = pathlib.Path(app).read_bytes()
    if len(data) > size:
        raise SystemExit(f"app {len(data)} B does not fit partition {size} B")
    if data[0] != 0xE9:
        raise SystemExit("not an ESP app image")
    esptool(["write_flash", "--flash_mode", "keep", "--flash_freq", "keep", "--flash_size", "keep",
             hex(offset), str(app)], "write", timeout=600)
    verify(app)


def verify(app, after="hard_reset"):
    offset, _ = app_partition()
    esptool(["verify_flash", hex(offset), str(app)], "verify", after=after)
    digest = hashlib.sha256(pathlib.Path(app).read_bytes()).hexdigest()
    print(json.dumps({"written": str(app), "offset": hex(offset), "bytes": pathlib.Path(app).stat().st_size,
                      "sha256": digest, "verify_flash": "ok"}))


def restore(image):
    image = pathlib.Path(image)
    if image.stat().st_size != FLASH_SIZE:
        raise SystemExit("restore expects the full 8 MB backup")
    identify()
    esptool(["write_flash", "0x0", str(image)], "restore", timeout=900)
    esptool(["verify_flash", "0x0", str(image)], "restore-verify", after="hard_reset", timeout=900)
    print("restored and verified", image)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "identify"
    arg = sys.argv[2] if len(sys.argv) > 2 else str(DEFAULT_APP)
    {"identify": lambda: identify(), "backup": backup, "write": lambda: write(arg), "verify": lambda: verify(arg),
     "restore": lambda: restore(sys.argv[2])}[cmd]()
