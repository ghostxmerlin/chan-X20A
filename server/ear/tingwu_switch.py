"""听悟 tier switch for the ear (spec docs/superpowers/specs/2026-11-11-senses-split-design.md §3).

The page writes hello.switches {_id: "tingwu", on}; the ear reads it every few seconds and starts or
stops 听悟 inside live connections. No document, or a database unreadable for longer than stale_s,
means off: when in doubt, spend nothing. Standard library only (testable without numpy).
"""
import time


class SwitchReader:
    def __init__(self, read, now=time.monotonic, stale_s=60):
        self.read, self.now, self.stale_s = read, now, stale_s
        self._value = False
        self._good_at = None          # monotonic time of the last successful read

    def update(self):
        try:
            doc = self.read()
        except Exception:             # noqa: BLE001 - a failed read only ages the last value
            return
        self._value = bool(doc) and doc.get("on") is True
        self._good_at = self.now()

    @property
    def on(self):
        if self._good_at is None or self.now() - self._good_at > self.stale_s:
            return False
        return self._value


def decide(want_on, running, available, ready, retry_ok):
    """'start', 'stop' or None for one connection."""
    if running and not want_on:
        return "stop"
    if want_on and not running and available and ready and retry_ok:
        return "start"
    return None


def tingwu_pos(begin_ms, end_ms, offset_samples, sr=16000):
    """听悟 times are relative to the stream start; offset them to the connection's sample position."""
    if not isinstance(begin_ms, (int, float)) or not isinstance(end_ms, (int, float)):
        return None
    return [int(offset_samples + begin_ms * sr / 1000), int(offset_samples + end_ms * sr / 1000)]
