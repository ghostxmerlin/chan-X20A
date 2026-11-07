#!/usr/bin/env python3
"""Convert the user's 20 verified PCM files into local, embedded IMA blocks."""
import argparse
import hashlib
import json
import pathlib
import struct
import subprocess

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
    args = parser.parse_args()
    source, out = args.source.resolve(), args.output.resolve()
    data = json.loads((source / 'stackchan_playable_20.json').read_text())['candidates']
    assert len(data) == 20 and [x['id'] for x in data] == [f'PLAY-{i:03}' for i in range(1, 21)]
    out.mkdir(parents=True, exist_ok=True)
    bundle, entries, rows = bytearray(), [], []
    for i, clip in enumerate(data, 1):
        pcm_path = (source / clip['device_audio_file']).resolve()
        assert pcm_path.is_relative_to(source)
        pcm = pcm_path.read_bytes()
        assert 0 < len(pcm) <= 384000 and len(pcm) % 2 == 0
        assert hashlib.sha256(pcm).hexdigest() == clip['sha256']['pcm']
        assert clip['audio_format']['sample_rate_hz'] == 24000 and clip['audio_format']['channels'] == 1
        wav = out / f'{clip["id"]}.ima.wav'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 's16le', '-ar', '24000', '-ac', '1',
                        '-i', str(pcm_path), '-c:a', 'adpcm_ima_wav', '-block_size', '256', str(wav)], check=True)
        parts = dict(chunks(wav.read_bytes()))
        fmt = parts[b'fmt ']
        assert struct.unpack_from('<HHI', fmt) == (0x11, 1, 24000)
        assert struct.unpack_from('<HH', fmt, 12) == (256, 4)
        encoded = parts[b'data']
        assert len(encoded) % 256 == 0
        decoded = out / f'{clip["id"]}.reference.pcm'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(wav), '-f', 's16le', str(decoded)], check=True)
        assert decoded.stat().st_size >= len(pcm)
        (out / f'{clip["id"]}.adpcm').write_bytes(encoded)
        row = {'id': i, 'source_id': clip['id'], 'title': clip['title'], 'text': clip['original_text'],
               'offset': len(bundle), 'bytes': len(encoded), 'frames': len(pcm)//2,
               'source_sha256': clip['sha256']['pcm'], 'encoded_sha256': hashlib.sha256(encoded).hexdigest()}
        rows.append(row)
        strings = ', '.join(json.dumps(row[k], ensure_ascii=False) for k in ('source_id', 'title', 'text'))
        entries.append(f'    {{{i}, {strings}, {row["offset"]}, {len(encoded)}, {len(pcm)//2}}},')
        bundle.extend(encoded)
    assert len(bundle) < 1300000, 'Voice bundle exceeds reserved application budget'
    (out / 'voice-library.bin').write_bytes(bundle)
    (out / 'voice_assets.h').write_text('// Generated locally by scripts/prepare-voice-library.py\n#pragma once\n'
        'inline constexpr VoiceClip kVoiceClips[] = {\n' + '\n'.join(entries) + '\n};\n')
    (out / 'manifest.json').write_text(json.dumps({'format': 'ima_wav_mono_blocks', 'block_bytes': 256,
        'sample_rate': 24000, 'bundle_sha256': hashlib.sha256(bundle).hexdigest(), 'clips': rows}, ensure_ascii=False, indent=2))
    print(f'Prepared {len(rows)} clips, {len(bundle)} encoded bytes; source hashes verified')


if __name__ == '__main__':
    main()
