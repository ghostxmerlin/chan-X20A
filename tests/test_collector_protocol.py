"""纯合成协议与 loopback HTTP 测试；从不接入真实设备、媒体或串口。"""

import argparse
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


SPEC = importlib.util.spec_from_file_location("collector_check", Path(__file__).resolve().parents[1] / "scripts/check-collector.py")
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)
TOKEN = "0" * 32  # 仅用于本测试内的 loopback server，非设备凭据。


def info(port=8081):
    return {"protocol_version": 1, "device_id": "synthetic", "boot_id": "synthetic-boot",
            "firmware": "synthetic-test", "audio_port": port,
            "camera": {"width": 320, "height": 240, "format": "jpeg"},
            "audio": {"sample_rate": 24000, "channels": 2, "bits": 16,
                      "channel_roles": ["mic", "reference"]}}


def telemetry(seq=1):
    stamp = 1000000 + seq * 50000
    return {"boot_id": "synthetic-boot", "seq": seq, "timestamp_us": stamp,
            "imu": {"valid": True, "timestamp_us": stamp, "accel": [0, 0, 9.81],
                    "gyro": [0, 0, 0], "accel_unit": "m/s^2", "gyro_unit": "deg/s"},
            "head_touch": {"valid": True, "timestamp_us": stamp, "values": [0, 0, 0]},
            "touch": {"valid": True, "count": 0, "x": -1, "y": -1},
            "power": {"valid": True, "battery_percent": 80, "charging": True},
            "health": {"heap_free": 180000, "psram_free": 6000000}}


def audio(seq=1, stamp=None, **changes):
    fields = dict(magic=b"SCA1", seq=seq, stamp=1000000 + seq * 20000 if stamp is None else stamp,
                  rate=24000, frames=480, channels=2, bits=16, payload=1920)
    fields.update(changes)
    header = collector.AUDIO_HEADER.pack(*(fields[key] for key in
        ("magic", "seq", "stamp", "rate", "frames", "channels", "bits", "payload")))
    return header + b"\0" * min(fields["payload"], 1920)


def jpeg():
    # 仅合成结构，不伪称可解码照片；工具本就只检查段结构及尺寸。
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 17, 8, 240, 320, 3) + b"\x01\x11\0\x02\x11\0\x03\x11\0"
    sos = b"\xff\xda\x00\x0c\x03\x01\0\x02\0\x03\0\0\x3f\0"
    return b"\xff\xd8" + sof + sos + b"\x12\x34" + b"\xff\xd9"


class ProtocolTests(unittest.TestCase):
    def test_info_shape_format_and_channel_roles(self):
        self.assertEqual(collector.validate_info(info())["audio"]["channel_roles"], ["mic", "reference"])
        for field, bad in (("protocol_version", True), ("boot_id", ""), ("audio_port", 0)):
            value = info()
            value[field] = bad
            with self.subTest(field=field), self.assertRaises(collector.ProtocolError):
                collector.validate_info(value)
        value = info()
        value["audio"]["channel_roles"] = ["mic"]
        with self.assertRaises(collector.ProtocolError):
            collector.validate_info(value)

    def test_audio_header_offsets_and_fragmented_coalesced_packets(self):
        parser = collector.AudioParser(info()["audio"])
        raw = audio(10) + audio(11)
        result = []
        for cut in (raw[:3], raw[3:29], raw[29:33], raw[33:1900], raw[1900:]):
            result.extend(parser.feed(cut))
        parser.finish()
        self.assertEqual([header["seq"] for header, _ in result], [10, 11])
        self.assertEqual(result[0][0]["timestamp_us"], 1200000)
        self.assertEqual(len(result[0][1]), 1920)
        self.assertEqual(collector.AUDIO_HEADER.size, 32)

    def test_audio_truncated_header_and_payload(self):
        for raw in (audio()[:31], audio()[:-1]):
            with self.subTest(length=len(raw)):
                parser = collector.AudioParser()
                parser.feed(raw)
                with self.assertRaisesRegex(collector.ProtocolError, "truncated"):
                    parser.finish()
        with self.assertRaisesRegex(collector.ProtocolError, "header"):
            collector.parse_audio_header(b"SCA1")

    def test_audio_rejects_invalid_lengths_formats_magic(self):
        for changes in ({"magic": b"BAD!"}, {"rate": 0}, {"frames": 0}, {"channels": 0},
                        {"bits": 8}, {"payload": 1919}, {"frames": 192000, "channels": 8, "payload": 3072000},
                        {"rate": 16000}):
            with self.subTest(changes=changes), self.assertRaises(collector.ProtocolError):
                collector.parse_audio_header(audio(**changes)[:32], info()["audio"])

    def test_sequence_gap_wrap_and_duplicate(self):
        tracker = collector.SequenceTracker()
        self.assertEqual(tracker.observe(0xfffffffe, 10), 0)
        self.assertIsNone(tracker.observe(0xfffffffe, 10))
        self.assertEqual(tracker.observe(1, 40), 2)
        self.assertEqual(tracker.summary()["sequence_gaps"], 2)
        self.assertEqual(tracker.summary()["duplicates"], 1)
        self.assertEqual(tracker.summary()["unique"], 2)

    def test_sequence_timestamp_discontinuities(self):
        for seq, stamp in ((9, 200), (11, 100), (11, 99), (10, 101)):
            tracker = collector.SequenceTracker()
            tracker.observe(10, 100)
            with self.subTest(seq=seq, stamp=stamp), self.assertRaises(collector.ProtocolError):
                tracker.observe(seq, stamp)

    def test_telemetry_validity_and_bad_payloads(self):
        collector.validate_telemetry(telemetry(), "synthetic-boot")
        invalid_sensor = telemetry()
        invalid_sensor["imu"]["valid"] = False
        collector.validate_telemetry(invalid_sensor, "synthetic-boot")
        cases = [
            ("boot_id", None, "wrong"),
            ("seq", None, True),
            ("imu", "accel", [float("nan"), 0, 0]),
            ("imu", "gyro", [0, 0]),
            ("imu", "valid", 1),
            ("imu", "timestamp_us", 9000000),
            ("imu", "accel_unit", "g"),
            ("head_touch", "values", [True, 0, 0]),
            ("power", "battery_percent", 101),
            ("health", "heap_free", -1),
        ]
        for key, subkey, value in cases:
            candidate = telemetry()
            if subkey:
                candidate[key][subkey] = value
            else:
                candidate[key] = value
            with self.subTest(key=key, subkey=subkey), self.assertRaises(collector.ProtocolError):
                collector.validate_telemetry(candidate, "synthetic-boot")

    def test_jpeg_dimensions_and_truncation(self):
        self.assertEqual(collector.jpeg_dimensions(jpeg()), (320, 240))
        for value in (b"", jpeg()[:-1], jpeg()[:10] + b"\xff\xd9", b"\xff\xd8\xff\xd9"):
            with self.subTest(size=len(value)), self.assertRaises(collector.ProtocolError):
                collector.jpeg_dimensions(value)
        malformed = bytearray(jpeg())
        malformed[4:6] = b"\xff\xff"
        with self.assertRaises(collector.ProtocolError):
            collector.jpeg_dimensions(malformed)

    def test_json_invalid_encoding_and_nonfinite(self):
        with self.assertRaises(collector.ProtocolError):
            collector.decode_json(b"\xff")
        value = telemetry()
        value["imu"]["accel"][0] = float("nan")
        with self.assertRaises(collector.ProtocolError):
            collector.validate_telemetry(collector.decode_json(json.dumps(value).encode()), "synthetic-boot")


class SyntheticServer:
    def __init__(self, gap=False, timestamp_jump=False, reboot=False, delayed_delivery=False):
        self.gap, self.timestamp_jump, self.reboot = gap, timestamp_jump, reboot
        self.delayed_delivery = delayed_delivery
        self.stop = threading.Event()
        self.info_calls = 0
        self.requests = []
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_):
                pass

            def do_GET(self):
                if self.headers.get("Authorization") != "Bearer " + TOKEN:
                    self.send_error(401)
                    return
                fixture.requests.append(self.path)
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "http://192.0.2.1/no")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self.path == "/truncated":
                    self.send_response(200)
                    self.send_header("Content-Length", "20")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(b"short")
                    return
                if self.path == "/stall":
                    self.send_response(200)
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    self.wfile.flush()
                    fixture.stop.wait(2)
                    return
                if self.path == "/v1/info":
                    value = info(fixture.server.server_port)
                    fixture.info_calls += 1
                    if fixture.reboot and fixture.info_calls > 1:
                        value["boot_id"] = "rebooted"
                    body = json.dumps(value).encode()
                elif self.path == "/v1/telemetry":
                    body = json.dumps(telemetry(int((time.monotonic() - fixture.start) * 20))).encode()
                elif self.path == "/v1/frame":
                    body = jpeg()
                elif self.path == "/v1/audio":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.send_header("X-Boot-Id", "synthetic-boot")
                    self.end_headers()
                    counter = 0
                    next_packet = time.monotonic()
                    try:
                        while not fixture.stop.is_set():
                            if fixture.delayed_delivery and counter == 10:
                                fixture.stop.wait(.12)
                            seq = counter + int(fixture.gap and counter >= 10)
                            stamp = 1000000 + seq * 20000 + (15000 if fixture.timestamp_jump and counter >= 10 else 0)
                            packet = audio(seq, stamp)
                            for part in (packet[:7], packet[7:35], packet[35:]):
                                self.wfile.write(("{:x}\r\n".format(len(part))).encode() + part + b"\r\n")
                            self.wfile.flush()
                            counter += 1
                            next_packet += .02
                            fixture.stop.wait(max(0, next_packet - time.monotonic()))
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    return
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                if self.path == "/v1/frame":
                    seq = int((time.monotonic() - fixture.start) * 2)
                    for key, value in {"X-Boot-Id": "synthetic-boot", "X-Seq": seq,
                                       "X-Timestamp-Us": 1000000 + seq * 500000,
                                       "X-Width": 320, "X-Height": 240}.items():
                        self.send_header(key, str(value))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.start = time.monotonic()
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(1)

    def arguments(self, **overrides):
        args = dict(host="127.0.0.1", port=self.server.server_port, timeout=.3, duration=1.1, camera_poll_hz=2,
                    save_samples=False, max_audio_gaps=0, max_audio_timestamp_error_ms=5, min_audio_coverage=.9)
        args.update(overrides)
        return argparse.Namespace(**args)


class HttpTests(unittest.TestCase):
    def test_chunked_concurrent_streams_and_report(self):
        with SyntheticServer() as server, tempfile.TemporaryDirectory() as directory:
            report = collector.run_check(server.arguments(), TOKEN, Path(directory))
        self.assertTrue(report["passed"], report["errors"])
        self.assertGreater(report["streams"]["audio"]["unique"], 30)
        self.assertEqual(report["streams"]["audio"]["sequence_gaps"], 0)
        self.assertGreater(report["streams"]["telemetry"]["unique"], 10)
        self.assertGreaterEqual(report["streams"]["camera"]["unique"], 2)
        self.assertEqual(report["final_health"]["heap_free"], 180000)
        self.assertNotIn(TOKEN, json.dumps(report))

    def test_delayed_but_complete_audio_is_not_a_capture_gap(self):
        with SyntheticServer(delayed_delivery=True) as server, tempfile.TemporaryDirectory() as directory:
            report = collector.run_check(server.arguments(), TOKEN, Path(directory))
        self.assertTrue(report["passed"], report["errors"])
        audio_report = report["streams"]["audio"]
        self.assertEqual(audio_report["sequence_gaps"], 0)
        self.assertGreaterEqual(audio_report["audio_seconds_per_wall_second"], .9)
        self.assertGreater(audio_report["packet_arrival_intervals"]["max_ms"], 100)

    def test_audio_gaps_fail_by_default_and_threshold_is_explicit(self):
        for allowed in (0, 1):
            with SyntheticServer(gap=True) as server, tempfile.TemporaryDirectory() as directory:
                report = collector.run_check(server.arguments(max_audio_gaps=allowed), TOKEN, Path(directory))
            self.assertEqual(report["streams"]["audio"]["sequence_gaps"], 1)
            self.assertEqual(report["passed"], allowed == 1, report["errors"])

    def test_audio_timestamp_jump_and_device_reboot_fail(self):
        with SyntheticServer(timestamp_jump=True, reboot=True) as server, tempfile.TemporaryDirectory() as directory:
            report = collector.run_check(server.arguments(), TOKEN, Path(directory))
        self.assertFalse(report["passed"])
        self.assertGreater(report["streams"]["audio"]["timestamp_discontinuities"], 0)
        self.assertTrue(any("boot_id" in error for error in report["errors"]))

    def test_stalled_read_is_bounded_and_disconnects(self):
        with SyntheticServer() as server:
            client = collector.LocalClient("127.0.0.1", TOKEN, .5)
            start = time.monotonic()
            with self.assertRaises(collector.DeadlineExpired):
                collector.bounded(lambda: client.fetch(server.server.server_port, "/stall",
                                                       time.monotonic() + 1, 1024), .15, client)
            self.assertLess(time.monotonic() - start, .6)

    def test_redirect_rejected_and_truncated_http_detected(self):
        with SyntheticServer() as server:
            client = collector.LocalClient("127.0.0.1", TOKEN, .3)
            for path in ("/redirect", "/truncated"):
                with self.subTest(path=path), self.assertRaises(collector.ProtocolError):
                    client.fetch(server.server.server_port, path, time.monotonic() + 1, 1024)
            self.assertEqual(server.requests, ["/redirect", "/truncated"])


if __name__ == "__main__":
    unittest.main()
