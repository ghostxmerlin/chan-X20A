#!/usr/bin/env python3
"""Verify an exported ZIP or pulled session folder, without opening captured media."""
import argparse
import json
from pathlib import Path
import tempfile
import wave
import zipfile


def lines(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def verify(root):
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["format_version"] == 1
    assert manifest["status"] == "complete", f"session status: {manifest['status']}"
    counts = dict(frames=0, telemetry_samples=0, audio_blocks=0)
    all_seen = {name: set() for name in counts}
    for segment in manifest["segments"]:
        folder = root / segment["directory"]
        boot = segment["boot_id"]
        for file_name, key in [("frames.jsonl", "frames"), ("telemetry.jsonl", "telemetry_samples"), ("audio.jsonl", "audio_blocks")]:
            rows = lines(folder / file_name)
            counts[key] += len(rows)
            last_receive = -1
            audio_groups = {}
            for row in rows:
                assert row["boot_id"] == boot
                identifier = (boot, row["seq"])
                assert identifier not in all_seen[key], f"duplicate {key}: {identifier}"
                all_seen[key].add(identifier)
                assert row["timestamp_us"] >= 0
                assert row["receive_elapsed_us"] >= last_receive
                last_receive = row["receive_elapsed_us"]
                if key == "frames":
                    jpeg = (folder / row["file"]).read_bytes()
                    assert jpeg[:2] == b"\xff\xd8" and jpeg[-2:] == b"\xff\xd9"
                elif key == "audio_blocks":
                    audio_groups.setdefault(row["file"], []).append(row)
            for name, group in audio_groups.items():
                with wave.open(str(folder / name), "rb") as wav:
                    assert wav.getnchannels() == group[0]["channels"]
                    assert wav.getframerate() == group[0]["sample_rate"]
                    assert wav.getsampwidth() == 2
                    offset = 0
                    last_seq = None
                    for row in group:
                        assert row["sample_offset"] == offset
                        assert row["byte_offset"] == 44 + offset * row["channels"] * 2
                        assert row["payload_bytes"] == row["frames"] * row["channels"] * 2
                        if last_seq is not None:
                            assert row["seq"] == ((last_seq + 1) & 0xFFFFFFFF), "WAV silently joins missing audio"
                        last_seq = row["seq"]
                        offset += row["frames"]
                    assert wav.getnframes() == offset
    for key, value in counts.items():
        assert manifest[key] == value, (key, manifest[key], value)
    events = lines(root / "events.jsonl")
    assert any(e["type"] == "recording_stopped" for e in events)
    print(json.dumps({"result": "PASS", "segments": len(manifest["segments"]), **counts,
                      "events": len(events), "queue_drops": manifest["dropped_queue_items"]}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    if args.path.is_dir():
        verify(args.path)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            with zipfile.ZipFile(args.path) as archive:
                for name in archive.namelist():
                    assert not Path(name).is_absolute() and ".." not in Path(name).parts
                archive.extractall(tmp)
            verify(Path(tmp))
