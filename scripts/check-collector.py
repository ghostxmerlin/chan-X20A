#!/usr/bin/env python3
"""StackChan 局域网采集验收；标准库实现，报告/样本仅写本机 logs/。

python3 scripts/check-collector.py 192.168.1.42 --token-file .env.collector-token \
    --duration 60 --output logs/collector-60s

退出码：0=本次观察符合阈值，1=验收失败，2=参数/本地配置错误。
这是一次有限时长观察，不是完整协议认证；不测未经时钟校准的单向延迟。
"""

import argparse
import contextlib
import datetime
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import socket
import statistics
import struct
import sys
import threading
import time
import urllib.error
import urllib.request

AUDIO_HEADER = struct.Struct("<4sIQIIHHI")
MAX_AUDIO_BYTES = 1024 * 1024
MAX_JPEG_BYTES = 2 * 1024 * 1024
ROOT = Path(__file__).resolve().parents[1]


class ProtocolError(ValueError):
    pass


class DeadlineExpired(TimeoutError):
    pass


def require(condition, message):
    if not condition:
        raise ProtocolError(message)


def integer(value, name, minimum=0, maximum=(1 << 64) - 1):
    require(type(value) is int and minimum <= value <= maximum, "invalid " + name)
    return value


def object_value(value, name):
    require(isinstance(value, dict), "invalid " + name)
    return value


def string(value, name):
    require(isinstance(value, str) and 0 < len(value) <= 256, "invalid " + name)
    return value


def finite_vector(value, name, count=3):
    require(isinstance(value, list) and len(value) == count, "invalid " + name)
    require(all(type(v) in (float, int) and math.isfinite(v) for v in value), "nonfinite " + name)


def decode_json(data):
    try:
        return json.loads(data.decode("utf-8"), parse_constant=lambda _: None)
    except (ValueError, UnicodeError) as exc:
        raise ProtocolError("invalid JSON encoding") from exc


def validate_info(value):
    value = object_value(value, "info")
    require(type(value.get("protocol_version")) is int and value["protocol_version"] == 1,
            "unsupported protocol_version")
    for field in ("device_id", "boot_id", "firmware"):
        string(value.get(field), field)
    audio = object_value(value.get("audio"), "audio")
    integer(audio.get("sample_rate"), "sample_rate", 1, 192000)
    integer(audio.get("channels"), "channels", 1, 8)
    require(audio.get("bits") == 16, "audio must be PCM16")
    roles = audio.get("channel_roles")
    require(isinstance(roles, list) and len(roles) == audio["channels"], "invalid channel_roles")
    for role in roles:
        string(role, "channel_role")
    camera = object_value(value.get("camera"), "camera")
    integer(camera.get("width"), "camera.width", 1, 8192)
    integer(camera.get("height"), "camera.height", 1, 8192)
    require(camera.get("format") == "jpeg", "camera format must be jpeg")
    integer(value.get("audio_port"), "audio_port", 1, 65535)
    # 仅保存已知配置字段；不复制意外返回的服务端配置/凭据。
    return {"protocol_version": 1, "device_id": value["device_id"], "boot_id": value["boot_id"],
            "firmware": value["firmware"], "audio_port": value["audio_port"],
            "audio": {k: audio[k] for k in ("sample_rate", "channels", "bits", "channel_roles")},
            "camera": {k: camera[k] for k in ("width", "height", "format")}}


def validate_telemetry(value, boot_id):
    value = object_value(value, "telemetry")
    require(value.get("boot_id") == boot_id, "telemetry boot_id changed")
    integer(value.get("seq"), "telemetry.seq", 0, 0xffffffff)
    stamp = integer(value.get("timestamp_us"), "telemetry.timestamp_us")
    for name in ("imu", "head_touch", "touch", "power"):
        sensor = object_value(value.get(name), name)
        require(type(sensor.get("valid")) is bool, "invalid " + name + ".valid")
    for name in ("imu", "head_touch"):
        sample_stamp = integer(value[name].get("timestamp_us"), name + ".timestamp_us")
        require(sample_stamp <= stamp + 20000, name + " timestamp is ahead of telemetry")
    imu = value["imu"]
    finite_vector(imu.get("accel"), "imu.accel")
    finite_vector(imu.get("gyro"), "imu.gyro")
    require(imu.get("accel_unit") == "m/s^2", "unexpected accel_unit")
    require(imu.get("gyro_unit") == "deg/s", "unexpected gyro_unit")
    finite_vector(value["head_touch"].get("values"), "head_touch.values")
    touch = value["touch"]
    integer(touch.get("count"), "touch.count", 0, 32)
    for axis in ("x", "y"):
        integer(touch.get(axis), "touch." + axis, -1, 65535)
    power = value["power"]
    integer(power.get("battery_percent"), "battery_percent", -1, 100)
    require(type(power.get("charging")) is bool, "invalid charging")
    health = object_value(value.get("health"), "health")
    for field in ("heap_free", "psram_free"):
        integer(health.get(field), field, 0, 1 << 32)
    return value


def jpeg_dimensions(data):
    """检查 SOI/EOI 与 SOF 段结构；不宣称完成 JPEG 像素解码。"""
    require(len(data) >= 4 and data[:2] == b"\xff\xd8" and data[-2:] == b"\xff\xd9",
            "invalid JPEG magic/truncation")
    pos, dimensions = 2, None
    sof_markers = {0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf}
    while pos < len(data) - 2:
        require(data[pos] == 0xff, "invalid JPEG marker")
        while pos < len(data) and data[pos] == 0xff:
            pos += 1
        require(pos < len(data), "truncated JPEG marker")
        marker = data[pos]
        pos += 1
        require(marker not in (0, 0xd8, 0xd9), "unexpected JPEG marker")
        if marker == 1 or 0xd0 <= marker <= 0xd7:
            continue
        require(pos + 2 <= len(data), "truncated JPEG segment")
        length = int.from_bytes(data[pos:pos + 2], "big")
        require(length >= 2 and pos + length <= len(data) - 2, "invalid JPEG segment length")
        if marker in sof_markers:
            require(length >= 8, "truncated JPEG SOF")
            height, width = struct.unpack_from(">HH", data, pos + 3)
            channels = data[pos + 7]
            require(0 < width <= 8192 and 0 < height <= 8192 and channels in (1, 3, 4),
                    "invalid JPEG dimensions/components")
            require(length == 8 + 3 * channels, "invalid JPEG SOF length")
            dimensions = (width, height)
        if marker == 0xda:
            require(dimensions is not None, "JPEG SOS before SOF")
            return dimensions
        pos += length
    raise ProtocolError("JPEG has no scan")


def parse_audio_header(data, expected=None):
    require(len(data) == AUDIO_HEADER.size, "truncated audio header")
    magic, seq, stamp, rate, frames, channels, bits, payload = AUDIO_HEADER.unpack(data)
    require(magic == b"SCA1", "invalid audio magic")
    integer(rate, "audio sample_rate", 1, 192000)
    integer(frames, "audio frames", 1, 192000)
    integer(channels, "audio channels", 1, 8)
    require(bits == 16, "unsupported audio bits")
    require(payload == frames * channels * 2 and payload <= MAX_AUDIO_BYTES, "invalid audio payload length")
    if expected:
        require((rate, channels, bits) == (expected["sample_rate"], expected["channels"], expected["bits"]),
                "audio format differs from info")
    return {"seq": seq, "timestamp_us": stamp, "sample_rate": rate, "frames": frames,
            "channels": channels, "bits": bits, "payload_bytes": payload}


class AudioParser:
    def __init__(self, expected=None):
        self.expected = expected
        self.buffer = bytearray()
        self.pending = None

    def feed(self, data):
        self.buffer.extend(data)
        result = []
        while True:
            if self.pending is None:
                if len(self.buffer) < AUDIO_HEADER.size:
                    break
                self.pending = parse_audio_header(self.buffer[:AUDIO_HEADER.size], self.expected)
                del self.buffer[:AUDIO_HEADER.size]
            size = self.pending["payload_bytes"]
            if len(self.buffer) < size:
                break
            result.append((self.pending, bytes(self.buffer[:size])))
            del self.buffer[:size]
            self.pending = None
        return result

    def finish(self):
        require(self.pending is None and not self.buffer, "truncated audio stream")


class SequenceTracker:
    def __init__(self):
        self.last_seq = self.last_stamp = self.first_stamp = None
        self.unique = self.duplicates = self.missing = 0

    def observe(self, seq, stamp):
        integer(seq, "sequence", 0, 0xffffffff)
        integer(stamp, "timestamp_us")
        gap = 0
        if self.last_seq is not None:
            delta = (seq - self.last_seq) & 0xffffffff
            if delta == 0:
                require(stamp == self.last_stamp, "same seq with changed timestamp")
                self.duplicates += 1
                return None
            require(delta < 0x80000000, "sequence moved backwards")
            require(stamp > self.last_stamp, "timestamp did not advance")
            gap = delta - 1
            self.missing += gap
        if self.first_stamp is None:
            self.first_stamp = stamp
        self.last_seq, self.last_stamp = seq, stamp
        self.unique += 1
        return gap

    def summary(self):
        span = (self.last_stamp - self.first_stamp) / 1e6 if self.unique > 1 else 0
        return {"unique": self.unique, "duplicates": self.duplicates, "sequence_gaps": self.missing,
                "first_device_timestamp_us": self.first_stamp, "last_device_timestamp_us": self.last_stamp,
                "device_timestamp_span_s": span,
                "observed_unique_per_device_second": (self.unique - 1) / span if span > 0 else None}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProtocolError("HTTP redirect rejected")


class LocalClient:
    def __init__(self, host, token, timeout):
        self.host, self.token, self.timeout = host, token, timeout
        self.active = set()
        self.lock = threading.Lock()

    def close_active(self):
        with self.lock:
            sockets = list(self.active)
        for sock in sockets:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)

    @contextlib.contextmanager
    def open(self, port, path, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DeadlineExpired("global deadline reached")
        host = "[" + self.host + "]" if ":" in self.host else self.host
        request = urllib.request.Request("http://{}:{}{}".format(host, port, path),
                                         headers={"Authorization": "Bearer " + self.token,
                                                  "Accept-Encoding": "identity"})
        # 不采用系统代理或 redirect，token 只能发给显式指定的局域网 IP。
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        response = opener.open(request, timeout=min(self.timeout, remaining))
        sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
        if sock is not None:
            with self.lock:
                self.active.add(sock)
        try:
            require(response.status == 200, "unexpected HTTP status")
            yield response
        finally:
            if sock is not None:
                with self.lock:
                    self.active.discard(sock)
            response.close()

    def fetch(self, port, path, deadline, maximum):
        start = time.monotonic()
        with self.open(port, path, deadline) as response:
            content = bytearray()
            expected_length = response.headers.get("Content-Length")
            if expected_length is not None:
                try:
                    expected_length = int(expected_length)
                except ValueError as exc:
                    raise ProtocolError("invalid Content-Length") from exc
                require(0 <= expected_length <= maximum, "HTTP body exceeds limit")
            while True:
                if time.monotonic() >= deadline:
                    raise DeadlineExpired("global deadline reached")
                block = response.read1(min(65536, maximum + 1 - len(content)))
                if not block:
                    break
                content.extend(block)
                require(len(content) <= maximum, "HTTP body exceeds limit")
            require(expected_length is None or len(content) == expected_length, "truncated HTTP body")
            return bytes(content), dict(response.headers), time.monotonic() - start


def bounded(function, seconds, client):
    """主线程有硬截止；header 慢速滴流也不能令 CLI 永久等待。"""
    result = []

    def worker():
        try:
            result.append((True, function()))
        except Exception as exc:
            result.append((False, exc))

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        client.close_active()
        raise DeadlineExpired("request deadline reached")
    success, value = result[0]
    if not success:
        raise value
    return value


def safe_error(exc):
    # 不打印服务端 body、请求头或任意异常 repr。
    if isinstance(exc, urllib.error.HTTPError):
        return "HTTP " + str(exc.code)
    if isinstance(exc, (ProtocolError, DeadlineExpired)):
        return str(exc)
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "socket timeout"
    if isinstance(exc, urllib.error.URLError):
        return "HTTP connection failed"
    return type(exc).__name__


def duration_summary(values):
    if not values:
        return None
    ordered = sorted(values)
    return {"count": len(values), "min_ms": ordered[0] * 1000,
            "median_ms": statistics.median(ordered) * 1000,
            "p95_ms": ordered[min(len(ordered) - 1, math.ceil(len(ordered) * .95) - 1)] * 1000,
            "max_ms": ordered[-1] * 1000}


def run_check(args, token, output):
    client = LocalClient(args.host, token, args.timeout)
    report = {"schema_version": 1, "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "requested_duration_s": args.duration, "errors": [], "streams": {},
              "wall_timeout_budget_s": args.duration + 3 * (args.timeout + .1) + .5,
              "limits": {"max_audio_gap_packets": args.max_audio_gaps,
                         "camera_poll_hz": args.camera_poll_hz,
                         "sensor_validity_requirement": "at least one valid reading per supported sensor; counts reported",
                         "max_audio_timestamp_error_ms": args.max_audio_timestamp_error_ms},
              "measurement_scope": ["有限时长内三流并发的结构、序号、时间戳与吞吐观察，不是完整协议认证。",
                                    "JPEG 只检查 magic、SOF 尺寸及段边界，不执行像素解码。",
                                    "HTTP 请求耗时包含服务端等待和传输；不是单向或端到端延迟。",
                                    "设备与主机未校时；音频 DMA 缓冲延迟未知，首样本时间仅为设备估计。",
                                    "首次收到前及截止时的音频尾包不计为 seq gap；时间覆盖率另列。",
                                    "遥测后台采样，轮询重复和未观察到的 seq 不代表网络丢包。"]}
    try:
        info = bounded(lambda: validate_info(decode_json(client.fetch(
            args.port, "/v1/info", time.monotonic() + args.timeout, 65536)[0])), args.timeout + .1, client)
    except Exception as exc:
        report["errors"].append("initial info: " + safe_error(exc))
        report["passed"] = False
        return report
    report["initial_info"] = info
    start = time.monotonic()
    deadline = start + args.duration
    stop = threading.Event()
    state_lock = threading.Lock()
    workers = []

    def publish(name, data):
        with state_lock:
            report["streams"][name] = data

    def fail(name, exc):
        with state_lock:
            report["errors"].append(name + ": " + safe_error(exc))

    def polling(name, interval, path, maximum):
        tracker = SequenceTracker()
        latencies, sizes = [], []
        validity = {k: {"valid": 0, "invalid": 0} for k in ("imu", "head_touch", "touch", "power")}
        latest_health, first_health, stale = None, None, 0
        sampled = False
        next_poll = time.monotonic()
        try:
            while not stop.is_set() and time.monotonic() < deadline:
                if stop.wait(max(0, next_poll - time.monotonic())):
                    break
                if time.monotonic() >= deadline:
                    break
                body, headers, elapsed = client.fetch(args.port, path, deadline, maximum)
                headers = {key.lower(): value for key, value in headers.items()}
                if name == "telemetry":
                    value = validate_telemetry(decode_json(body), info["boot_id"])
                    tracker.observe(value["seq"], value["timestamp_us"])
                    for key, counts in validity.items():
                        counts["valid" if value[key]["valid"] else "invalid"] += 1
                    stale += int(any(value[k]["valid"] and value["timestamp_us"] - value[k]["timestamp_us"] > 2000000
                                     for k in ("imu", "head_touch")))
                    latest_health = dict(value["health"])
                    if first_health is None:
                        first_health = latest_health
                else:
                    require(headers.get("x-boot-id") == info["boot_id"], "JPEG boot_id changed/missing")
                    try:
                        seq, stamp, width, height = (int(headers[k]) for k in
                            ("x-seq", "x-timestamp-us", "x-width", "x-height"))
                    except (KeyError, ValueError) as exc:
                        raise ProtocolError("missing/invalid JPEG headers") from exc
                    require(jpeg_dimensions(body) == (width, height), "JPEG SOF/header dimensions differ")
                    require((width, height) == (info["camera"]["width"], info["camera"]["height"]),
                            "JPEG dimensions differ from info")
                    tracker.observe(seq, stamp)
                latencies.append(elapsed)
                sizes.append(len(body))
                if args.save_samples and not sampled:
                    (output / ("sample-telemetry.json" if name == "telemetry" else "sample-frame.jpg")).write_bytes(body)
                    sampled = True
                next_poll = max(next_poll + interval, time.monotonic())
        except Exception as exc:
            if time.monotonic() < deadline and not stop.is_set():
                fail(name, exc)
        finally:
            summary = tracker.summary()
            summary.update({"responses": len(sizes), "bytes": sum(sizes),
                            "responses_per_wall_second": len(sizes) / args.duration,
                            "bytes_per_wall_second": sum(sizes) / args.duration,
                            "http_request_duration": duration_summary(latencies)})
            if name == "telemetry":
                summary.update({"sensor_validity": validity, "stale_sensor_samples": stale,
                                "first_health": first_health, "last_health": latest_health,
                                "health_delta": {k: latest_health[k] - first_health[k] for k in ("heap_free", "psram_free")}
                                if first_health and latest_health else None})
            publish(name, summary)

    def audio_worker():
        tracker = SequenceTracker()
        parser = AudioParser(info["audio"])
        frames = total_bytes = 0
        estimated_missing_seconds = 0
        timestamp_errors, arrival_intervals = [], []
        first_arrival = last_arrival = previous = None
        sample_file = None
        sample_bytes = tail_bytes = 0
        try:
            if args.save_samples:
                sample_file = (output / "sample-audio.pcm").open("wb")
            with client.open(info["audio_port"], "/v1/audio", deadline) as response:
                require(response.headers.get("X-Boot-Id") == info["boot_id"], "audio boot_id changed/missing")
                while not stop.is_set() and time.monotonic() < deadline:
                    block = response.read1(65536)  # 解码 chunked，协议帧可跨 HTTP chunk。
                    if not block:
                        parser.finish()
                        raise ProtocolError("audio stream ended before deadline")
                    for header, payload in parser.feed(block):
                        gap = tracker.observe(header["seq"], header["timestamp_us"])
                        require(gap is not None, "duplicate audio packet")
                        now = time.monotonic()
                        if previous is not None:
                            expected_us = previous["frames"] * 1e6 / previous["sample_rate"]
                            # gap 内帧大小未知，只对连续包检查时间间隔。
                            if gap == 0:
                                timestamp_errors.append(abs(header["timestamp_us"] - previous["timestamp_us"] - expected_us) / 1000)
                            else:
                                estimated_missing_seconds += max(0, header["timestamp_us"] - previous["timestamp_us"] - expected_us) / 1e6
                            arrival_intervals.append(now - last_arrival)
                        if first_arrival is None:
                            first_arrival = now
                        last_arrival, previous = now, header
                        frames += header["frames"]
                        total_bytes += len(payload)
                        if sample_file and sample_bytes < info["audio"]["sample_rate"] * info["audio"]["channels"] * 2 * 5:
                            sample_file.write(payload)
                            sample_bytes += len(payload)
                tail_bytes = len(parser.buffer)
                # 截止主动断开可能位于一包中间，不伪报生产端截断。
        except Exception as exc:
            tail_bytes = len(parser.buffer)
            if time.monotonic() < deadline and not stop.is_set():
                fail("audio", exc)
        finally:
            if sample_file:
                sample_file.close()
            summary = tracker.summary()
            audio_seconds = frames / info["audio"]["sample_rate"]
            summary.update({"pcm_frames": frames, "pcm_bytes": total_bytes,
                            "received_audio_seconds": audio_seconds,
                            "gap_estimated_missing_audio_seconds": estimated_missing_seconds,
                            "audio_seconds_per_wall_second": audio_seconds / args.duration,
                            "packets_per_wall_second": tracker.unique / args.duration,
                            "pcm_bytes_per_wall_second": total_bytes / args.duration,
                            "first_packet_after_start_ms": (first_arrival - start) * 1000 if first_arrival else None,
                            "last_packet_before_end_ms": max(0, deadline - last_arrival) * 1000 if last_arrival else None,
                            "trailing_partial_bytes_at_deadline": tail_bytes,
                            "timestamp_error_max_ms": max(timestamp_errors) if timestamp_errors else None,
                            "timestamp_discontinuities": sum(e > args.max_audio_timestamp_error_ms for e in timestamp_errors),
                            "packet_arrival_intervals": duration_summary(arrival_intervals),
                            "saved_pcm_bytes": sample_bytes})
            publish("audio", summary)

    for name, function in (("telemetry", lambda: polling("telemetry", .05, "/v1/telemetry", 65536)),
                           ("camera", lambda: polling("camera", 1 / args.camera_poll_hz, "/v1/frame", MAX_JPEG_BYTES)),
                           ("audio", audio_worker)):
        thread = threading.Thread(target=function, name=name, daemon=True)
        thread.start()
        workers.append(thread)
    while time.monotonic() < deadline and any(t.is_alive() for t in workers):
        stop.wait(min(.1, max(0, deadline - time.monotonic())))
    stop.set()
    client.close_active()
    cleanup_deadline = time.monotonic() + .5
    for thread in workers:
        thread.join(max(0, cleanup_deadline - time.monotonic()))
        if thread.is_alive():
            report["errors"].append(thread.name + ": worker did not stop within deadline")
    report["elapsed_stream_phase_s"] = time.monotonic() - start
    try:
        final_info = bounded(lambda: validate_info(decode_json(client.fetch(
            args.port, "/v1/info", time.monotonic() + args.timeout, 65536)[0])), args.timeout + .1, client)
        report["final_info"] = final_info
        require(final_info == info, "info/boot_id changed during test")
        final_telemetry = bounded(lambda: validate_telemetry(decode_json(client.fetch(
            args.port, "/v1/telemetry", time.monotonic() + args.timeout, 65536)[0]), info["boot_id"]),
            args.timeout + .1, client)
        report["final_health"] = final_telemetry["health"]
    except Exception as exc:
        report["errors"].append("final state: " + safe_error(exc))
    for name in ("telemetry", "camera", "audio"):
        if report["streams"].get(name, {}).get("unique", 0) < 2:
            report["errors"].append(name + ": fewer than two unique samples")
    audio = report["streams"].get("audio", {})
    if audio.get("sequence_gaps", 0) > args.max_audio_gaps:
        report["errors"].append("audio: sequence gaps exceed configured threshold")
    if audio.get("timestamp_discontinuities", 0):
        report["errors"].append("audio: timestamp discontinuities exceed tolerance")
    if audio.get("audio_seconds_per_wall_second", 0) < args.min_audio_coverage:
        report["errors"].append("audio: received duration below minimum coverage")
    telemetry = report["streams"].get("telemetry", {})
    if telemetry.get("stale_sensor_samples", 0):
        report["errors"].append("telemetry: valid sensor timestamp more than 2 seconds stale")
    for name, counts in telemetry.get("sensor_validity", {}).items():
        if not counts["valid"]:
            report["errors"].append("telemetry: no valid " + name + " sample")
    report["limits"]["min_audio_coverage"] = args.min_audio_coverage
    report["passed"] = not report["errors"]
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("host", help="机器人局域网 IP（不接受域名或 URL）")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--token-file", required=True, type=Path, help="仅包含 32 位十六进制 token 的本地文件")
    parser.add_argument("--duration", type=float, default=10, help="并发观察秒数，默认 10")
    parser.add_argument("--timeout", type=float, default=3, help="单次 I/O / 前后状态请求超时秒数，默认 3")
    parser.add_argument("--camera-poll-hz", type=float, default=2, help="图像轮询频率，默认 2；高帧率验收可设 20")
    parser.add_argument("--output", type=Path, help="报告目录，必须位于本项目 logs/ 下")
    parser.add_argument("--save-samples", action="store_true", help="保存首张 JPEG、首条遥测及最多约 5 秒 PCM；默认不保存")
    parser.add_argument("--max-audio-gaps", type=int, default=0, help="允许的缺失音频包数，默认 0")
    parser.add_argument("--max-audio-timestamp-error-ms", type=float, default=5)
    parser.add_argument("--min-audio-coverage", type=float, default=.9, help="收到音频时长/观察时长下限，默认 0.9")
    args = parser.parse_args(argv)
    try:
        address = ipaddress.ip_address(args.host)
        require((address.is_private or address.is_link_local or address.is_loopback)
                and not address.is_unspecified and not address.is_multicast, "host must be a local IP")
        args.host = str(address)
        require(1 <= args.port <= 65535, "invalid port")
        require(math.isfinite(args.duration) and 1 <= args.duration <= 3600, "duration must be 1..3600 seconds")
        require(math.isfinite(args.timeout) and .1 <= args.timeout <= 30, "timeout must be .1..30 seconds")
        require(math.isfinite(args.camera_poll_hz) and 1 <= args.camera_poll_hz <= 30, "camera-poll-hz must be 1..30")
        require(args.max_audio_gaps >= 0, "invalid max-audio-gaps")
        require(math.isfinite(args.max_audio_timestamp_error_ms) and args.max_audio_timestamp_error_ms >= 0,
                "invalid timestamp tolerance")
        require(math.isfinite(args.min_audio_coverage) and 0 <= args.min_audio_coverage <= 1, "invalid min-audio-coverage")
        token = args.token_file.read_text(encoding="ascii").strip()
        require(re.fullmatch(r"[a-fA-F0-9]{32}", token) is not None, "token file must contain exactly 32 hex characters")
        output = (args.output or (ROOT / "logs" / datetime.datetime.now().strftime("collector-%Y%m%d-%H%M%S"))).resolve()
        require((ROOT / "logs").resolve() in output.parents, "output must be inside project logs/")
        require(not (output / "report.json").exists(), "output already contains report.json; choose a new directory")
        os.umask(0o077)
        output.mkdir(parents=True, exist_ok=True)
    except (OSError, UnicodeError, ValueError) as exc:
        print("配置错误：" + (safe_error(exc) if isinstance(exc, ProtocolError) else type(exc).__name__), file=sys.stderr)
        return 2
    report = run_check(args, token, output)
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("本次观察{}；报告：{}".format("通过" if report["passed"] else "失败", output / "report.json"))
    for error in report["errors"]:
        print("- " + error)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
