#!/usr/bin/env python3
"""Prepare the verified SD voice files and their firmware metadata (catalog + emotion labels).

Clip count follows the index (PLAY-001..PLAY-N, N must equal kVoiceCount in voice_policy.h).
Each clip's response emotion comes from config/voice-emotions.json; every clip needs one.
"""
import argparse
import array
import hashlib
import json
import pathlib
import struct
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def chunks(wav):
    assert wav[:4] == b'RIFF' and wav[8:12] == b'WAVE'
    at = 12
    while at + 8 <= len(wav):
        tag, size = wav[at:at+4], struct.unpack_from('<I', wav, at+4)[0]
        assert at + 8 + size <= len(wav)
        yield tag, wav[at+8:at+8+size]
        at += 8 + size + (size & 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=pathlib.Path)
    parser.add_argument('--output', type=pathlib.Path, default=ROOT / '.private/voice-library')
    parser.add_argument('--emotions', type=pathlib.Path, default=ROOT / 'config/voice-emotions.json')
    args = parser.parse_args()
    source, out = args.source.resolve(), args.output.resolve()
    index = source if source.is_file() else source / 'stackchan_playable_80.json'
    source = index.parent
    data = json.loads(index.read_text())['candidates']
    count = len(data)
    assert count and [x['id'] for x in data] == [f'PLAY-{i:03}' for i in range(1, count + 1)]
    policy = (ROOT / 'upstream/StackChan/firmware/main/collector/voice_policy.h').read_text()
    assert f'kVoiceCount={count};' in policy, f'set kVoiceCount={count} in voice_policy.h'
    emotions = json.loads(args.emotions.read_text())
    labels, tagged = emotions['labels'], emotions['clips']
    missing = [x['id'] for x in data if tagged.get(x['id']) not in labels]
    assert not missing and set(tagged) == {x['id'] for x in data}, f'emotion labels missing or extra: {missing}'
    out.mkdir(parents=True, exist_ok=True)
    bundle, entries, rows = bytearray(), [], []
    for i, clip in enumerate(data, 1):
        pcm_path = (source / clip['device_audio_file']).resolve()
        assert pcm_path.is_relative_to(source)
        pcm = pcm_path.read_bytes()
        assert 0 < len(pcm) <= 384000 and len(pcm) % 2 == 0
        assert hashlib.sha256(pcm).hexdigest() == clip['sha256']['pcm']
        assert clip['audio_format']['sample_rate_hz'] == 24000 and clip['audio_format']['channels'] == 1
        frames = len(pcm)//2
        # Resample before block encoding so padding does not change clip duration.
        pcm16 = out / f'{clip["id"]}.16k.pcm'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 's16le', '-ar', '24000', '-ac', '1',
                        '-i', str(pcm_path), '-ar', '16000', '-f', 's16le', str(pcm16)], check=True)
        assert pcm16.stat().st_size == ((frames*2+2)//3)*2
        wav = out / f'{clip["id"]}.ima.wav'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 's16le', '-ar', '16000', '-ac', '1',
                        '-i', str(pcm16), '-c:a', 'adpcm_ima_wav', '-block_size', '256', str(wav)], check=True)
        parts = dict(chunks(wav.read_bytes()))
        fmt = parts[b'fmt ']
        assert struct.unpack_from('<HHI', fmt) == (0x11, 1, 16000)
        assert struct.unpack_from('<HH', fmt, 12) == (256, 4)
        encoded = parts[b'data']
        assert len(encoded) % 256 == 0
        decoded = out / f'{clip["id"]}.reference.pcm'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(wav), '-f', 's16le', str(decoded)], check=True)
        assert decoded.stat().st_size >= pcm16.stat().st_size
        reference16 = array.array('h')
        reference16.frombytes(decoded.read_bytes()[:pcm16.stat().st_size])
        if sys.byteorder != 'little': reference16.byteswap()
        reference24 = array.array('h')
        for frame in range(frames):
            at, fraction = divmod(frame*2, 3)
            value = reference16[at]*(3-fraction) + reference16[min(at+1,len(reference16)-1)]*fraction
            reference24.append((abs(value)//3) * (-1 if value<0 else 1))
        if sys.byteorder != 'little': reference24.byteswap()
        decoded.write_bytes(reference24.tobytes())
        (out / f'{clip["id"]}.adpcm').write_bytes(encoded)
        row = {'id': i, 'source_id': clip['id'], 'title': clip['title'], 'text': clip['original_text'],
               'emotion': tagged[clip['id']],
               'offset': len(bundle), 'bytes': len(encoded), 'frames': frames,
               'source_sha256': clip['sha256']['pcm'], 'encoded_sha256': hashlib.sha256(encoded).hexdigest()}
        rows.append(row)
        strings = ', '.join(json.dumps(row[k], ensure_ascii=False) for k in ('source_id', 'title', 'text'))
        row['sd_path'] = f'/sdcard/soul/voice/{clip["id"]}.adpcm'
        digest = ','.join(f'0x{x:02x}' for x in hashlib.sha256(encoded).digest())
        entries.append(f'    {{{i}, {strings}, {row["offset"]}, {len(encoded)}, {frames}, '
                       f'{json.dumps(row["sd_path"])}, {{{digest}}}, {json.dumps(row["emotion"])}}},')
        bundle.extend(encoded)
    assert len(bundle) < 40000 * count, 'Unexpected voice library size'
    (out / 'voice-library.bin').write_bytes(bundle)
    (out / 'voice_assets.h').write_text('// Generated locally by scripts/prepare-voice-library.py\n#pragma once\n'
        'inline constexpr int kVoiceEncodedRate=16000, kVoicePlaybackRate=24000;\n'
        'inline constexpr VoiceClip kVoiceClips[] = {\n' + '\n'.join(entries) + '\n};\n')
    (out / 'manifest.json').write_text(json.dumps({'format': 'ima_wav_mono_blocks', 'block_bytes': 256,
        'sample_rate': 16000, 'playback_sample_rate': 24000,
        'bundle_sha256': hashlib.sha256(bundle).hexdigest(), 'clips': rows}, ensure_ascii=False, indent=2))
    print(f'Prepared {len(rows)} clips, {len(bundle)} encoded bytes; source hashes verified')


if __name__ == '__main__':
    main()
