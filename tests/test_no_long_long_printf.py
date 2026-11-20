"""The firmware uses newlib nano formatting (CONFIG_NEWLIB_NANO_FORMAT=y): %lld / %llu are not supported and shift
every argument after them. 2026-11-20: "head touch up after %lld ms%s" read a code address as the %s string and
crashed the sensor task each time a head touch ended (collector-0.5.22-0.5.24). No 64-bit printf format in the
collector's own code."""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "upstream/StackChan/firmware/main/collector"
BAD = re.compile(r'%[-+ #0-9.]*ll[diuxX]|PRI[diuxX]64')


class NoLongLongPrintf(unittest.TestCase):
    def test_nano_format_is_what_the_firmware_builds_with(self):
        cfg = (ROOT / "upstream/StackChan/firmware/sdkconfig").read_text()
        self.assertIn("CONFIG_NEWLIB_NANO_FORMAT=y", cfg)

    def test_no_64_bit_format_in_collector_code(self):
        hits = []
        for f in sorted(COLLECTOR.rglob("*")):
            if f.suffix not in (".cpp", ".h", ".c"):
                continue
            for n, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
                code = line.split("//")[0]
                if BAD.search(code):
                    hits.append(f"{f.relative_to(ROOT)}:{n}")
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
