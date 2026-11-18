"""Ear -> keyword ear stream (server/ear/kws_link.py): the raw mic goes out in ~0.5 s chunks in order, one request
at a time, never waiting; the robot's own sound goes as zeros; gaps restart; a slow/down spotter drops audio, not
the ear; reports become keyword events and heard.keyword rows.

    .private/venv-ear/bin/python -B -m unittest discover -s tests -p 'test_kws_link.py' -v
"""
import asyncio
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server" / "ear"))
import kws_link  # noqa: E402

SR = 16000
PKT = 960            # 60 ms packets, like the robot's Opus frames
C = -(-int(kws_link.SEND_S * SR) // PKT)    # packets that make one chunk


def report(score=0.22, hit=True, near=False, start=1000, end=25000):
    return {"keyword": "dynames", "label": "Dynames", "score": score, "hit": hit, "near": near, "sample": "a.wav",
            "start": start, "end": end}


class KeywordStreamTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls, self.posted, self.emitted, self.logs = [], [], [], []
        self.gate = threading.Event()
        self.gate.set()
        self.answer = {"reports": []}

        def call(url, pcm, stream, pos, timeout):
            self.calls.append((stream, pos, np.frombuffer(pcm, dtype="<i2").copy()))
            self.gate.wait(2)
            if isinstance(self.answer, Exception):
                raise self.answer
            return self.answer

        self.link = kws_link.KeywordLink("http://127.0.0.1:1", call=call, log=lambda *a, **k: self.logs.append(a),
                                         emit=lambda kind, **f: self.emitted.append((kind, f)), clock=lambda: 100.0)

    async def asyncTearDown(self):
        self.gate.set()
        self.link.close()

    async def settle(self):
        for _ in range(200):
            if not self.link._tasks:
                return
            await asyncio.sleep(0.005)

    def packets(self, n, start=0, value=0.1, sounding=False, session="s1"):
        for i in range(n):
            self.link.feed(np.full(PKT, value, np.float32), start + i * PKT, session, sounding, self.posted.append)

    async def test_audio_goes_out_in_order_in_half_second_chunks(self):
        self.packets(C)                                   # one chunk
        await self.settle()
        self.packets(C, start=C * PKT)
        await self.settle()
        self.assertEqual([(c[0], c[1], len(c[2])) for c in self.calls], [("s1", 0, C * PKT), ("s1", C * PKT, C * PKT)])
        self.assertAlmostEqual(int(self.calls[0][2][0]), 0.1 * 32767, delta=1)

    async def test_robot_own_sound_goes_as_zeros(self):
        self.packets(C, sounding=True)
        await self.settle()
        self.assertFalse(self.calls[0][2].any())

    async def test_one_request_at_a_time_and_the_rest_waits_then_goes_together(self):
        self.gate.clear()
        self.packets(C)                                   # chunk 1 in flight
        self.packets(18, start=C * PKT)                   # waits
        await asyncio.sleep(0.02)
        self.assertEqual(len(self.calls), 1)
        self.gate.set()
        await self.settle()
        self.packets(1, start=(C + 18) * PKT)             # next packet sends what was waiting, in one piece
        await self.settle()
        self.assertEqual([(c[1], len(c[2])) for c in self.calls], [(0, C * PKT), (C * PKT, 19 * PKT)])

    async def test_a_slow_spotter_drops_the_oldest_audio_not_the_ear(self):
        self.gate.clear()
        self.packets(C)
        self.packets(100, start=C * PKT)                  # 6 s waiting > 4 s cap
        self.assertGreater(self.link.dropped_s, 1.5)
        self.gate.set()
        await self.settle()
        self.packets(1, start=(C + 100) * PKT)
        await self.settle()
        last = self.calls[-1]
        self.assertLessEqual(len(last[2]), int(4.1 * SR))
        self.assertEqual(last[1] + len(last[2]), (C + 101) * PKT)  # still ends where the stream is

    async def test_a_gap_restarts_the_pending_audio(self):
        self.packets(C - 1)
        self.packets(C, start=50 * PKT)
        await self.settle()
        self.assertEqual(self.calls[0][1], 50 * PKT)

    async def test_reports_become_events_and_run_log_rows(self):
        self.answer = {"reports": [report(), report(0.37, False, True, start=30000, end=54000)]}
        hits = []
        self.link.on_hit = hits.append                    # 2026-11-18: a hit is a call (only hits, not nears)
        self.packets(C)
        await self.settle()
        self.assertEqual([(e["type"], e["hit"], e["near"]) for e in self.posted],
                         [("keyword", True, False), ("keyword", False, True)])
        self.assertEqual(self.posted[0]["pos"], [1000, 25000])
        self.assertEqual([k for k, _ in self.emitted], ["heard.keyword", "heard.keyword"])
        self.assertIn("差一点", self.emitted[1][1]["sum"])
        self.assertEqual(self.link.hits, 1)
        self.assertEqual([h["pos"] for h in hits], [[1000, 25000]])

    async def test_spotter_down_costs_nothing(self):
        self.answer = ConnectionRefusedError()
        self.packets(C)
        await self.settle()
        self.packets(C, start=C * PKT)
        await self.settle()
        self.assertEqual((self.link.failed, self.posted), (2, []))
        self.assertEqual(len(self.logs), 1)               # logged once, not every chunk

    async def test_no_session_no_send(self):
        self.packets(20, session="")
        await self.settle()
        self.assertEqual(self.calls, [])


class ConfiguredUrlTest(unittest.TestCase):
    def test_env_then_file_then_off_and_local_only(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "kws.url")
            self.assertIsNone(kws_link.configured_url({}, path))
            with open(path, "w") as f:
                f.write("http://127.0.0.1:52760\n")
            self.assertEqual(kws_link.configured_url({}, path), "http://127.0.0.1:52760")
            self.assertEqual(kws_link.configured_url({"SOUL_KWS_URL": "http://localhost:9"}, path), "http://localhost:9")
            self.assertIsNone(kws_link.configured_url({"SOUL_KWS_URL": "http://example.com:9"}, path))


if __name__ == "__main__":
    unittest.main()
