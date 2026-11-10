"""Bounded wire validation and transactional speaker enrollment (no model dependency)."""
import collections
import hmac
import os
from pathlib import Path
import re
import shutil
import struct
import tempfile
import time
from urllib.parse import parse_qs, urlsplit
import uuid

import numpy as np


def authenticate(path, header, token):
    if not token:
        return True
    values = parse_qs(urlsplit(path).query).get('token', [])
    supplied = header[7:] if header.startswith('Bearer ') else values[0] if len(values) == 1 else ''
    return hmac.compare_digest(supplied.encode(), token.encode())


def audio_format(msg):
    fmt, rate, channels = msg.get('format', 'pcm16'), msg.get('sample_rate', 16000), msg.get('channels', 1)
    framing = msg.get('framing', 'raw')
    if type(rate) is not int or type(channels) is not int or channels != 1:
        raise ValueError('audio must be mono with integer sample_rate')
    if fmt not in ('opus', 'pcm16') or rate not in (16000, 24000) or (fmt == 'pcm16' and rate != 16000):
        raise ValueError('expected 16k PCM16 or 16k/24k Opus')
    if framing not in ('raw', 'sea1') or (framing == 'sea1' and (fmt != 'opus' or msg.get('frame_ms') != 60)):
        raise ValueError('SEA1 requires 60ms Opus')
    return fmt, rate, framing


def parse_audio_packet(data, framing):
    if not data or len(data) > 65536:
        raise ValueError('invalid audio size')
    if framing == 'raw':
        return data, None, None
    if len(data) <= 16 or data[:4] != b'SEA1' or len(data) > 4096:
        raise ValueError('invalid SEA1 packet')
    seq, timestamp = struct.unpack_from('<IQ', data, 4)
    return data[16:], seq, timestamp


class SelfHistory:
    def __init__(self):
        self.spans = collections.deque(maxlen=4000)

    def add(self, start, end, flags):
        self.spans.append((start, end, dict(flags)))

    def overlap(self, start, end):
        return {k: any(a < end and b > start and f.get(k, False) for a, b, f in self.spans)
                for k in ('sounding', 'moving')}


class Enrollment:
    def __init__(self):
        self.epoch = 0
        self.active = False
        self.embs = []
        self.rejected = ''

    def start(self, msg, speakers, now):
        name = msg.get('name', '')
        replace = msg.get('replace', False)
        request_id = msg.get('request_id', uuid.uuid4().hex)
        if not isinstance(name, str) or not re.fullmatch(r'[\w\-\u3400-\u9fff ]{1,48}', name) or not name.strip():
            raise ValueError('invalid speaker name')
        if type(replace) is not bool or msg.get('count', 5) != 5:
            raise ValueError('enrollment needs five phrases and boolean replace')
        if not isinstance(request_id, str) or not re.fullmatch(r'[\w-]{1,64}', request_id):
            raise ValueError('invalid request_id')
        if name in speakers and not replace:
            raise ValueError('speaker exists; explicit replace required')
        if self.active:
            raise ValueError('enrollment already active')
        self.epoch += 1
        self.name, self.replace, self.request_id = name, replace, request_id
        self.deadline, self.active, self.embs = now + 120, True, []

    def cancel(self):
        self.active = False
        self.epoch += 1
        self.embs = []

    def expired(self, now):
        return self.active and now >= self.deadline

    def progress(self):
        return {'type':'enroll', 'name':self.name, 'request_id':self.request_id, 'n':len(self.embs), 'count':5}

    def accept(self, epoch, emb, duration, voice, sounding, now):
        """True once five sentences are in. self.rejected says why the last one did not count
        ('' when it did): too_short / too_long / not_voice / self_voice / bad_embedding."""
        self.rejected = ''
        if not self.active or epoch != self.epoch or self.expired(now):
            return False
        reason = ('self_voice' if sounding else 'too_short' if duration < 1 else 'too_long' if duration > 12 else
                  'not_voice' if not voice else
                  'bad_embedding' if not np.isfinite(emb).all() or np.linalg.norm(emb) < 1e-6 else '')
        if reason:
            self.rejected = reason
            return False
        self.embs.append(emb.copy())
        return len(self.embs) >= 5


def _write_speakers(path, updated):
    """Durable atomic replacement of speakers.npz; the previous file is kept as a .bak copy."""
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.speakers-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            np.savez(output, **updated)
            output.flush()
            os.fsync(output.fileno())
        if path.exists():
            backup = path.with_name(path.name + '.bak-' + str(time.time_ns()))
            shutil.copyfile(path, backup)
            os.chmod(backup, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def save_speaker(path, speakers, name, embs, replace):
    """No shared state mutation until the durable atomic replacement succeeds."""
    if name in speakers and not replace:
        raise ValueError('speaker exists; explicit replace required')
    vector = np.mean(embs, axis=0).astype(np.float32)
    norm = np.linalg.norm(vector)
    if not np.isfinite(vector).all() or norm < 1e-6:
        raise ValueError('invalid voiceprint')
    updated = {**speakers, name: vector / norm}
    _write_speakers(path, updated)
    return updated


def forget_speaker(path, speakers, name):
    """Remove one voiceprint (2026-11-10). The old file stays as a .bak copy; nothing else changes."""
    if name not in speakers:
        raise KeyError('unknown speaker')
    updated = {k: v for k, v in speakers.items() if k != name}
    _write_speakers(path, updated)
    return updated
