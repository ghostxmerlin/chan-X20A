"""Owner 2026-11-20: the voice module (StickS3) is on Port C, the M12 on the CoreS3's red side Port A.
CoreS3 Grove pins (docs.m5stack.com/en/core/CoreS3): Port A yellow G2 / white G1, Port B yellow G9 / white G8, Port C
yellow G17 / white G18. The Stick sends on its white wire: UART2 RX GPIO18, TX GPIO17. The M12 sends on its white wire
too (it ran on Port C as TX G17 / RX G18): UART0 TX GPIO2 (yellow), RX GPIO1 (white)."""
import pathlib
import re
import unittest

FW = pathlib.Path(__file__).resolve().parents[1] / "upstream/StackChan/firmware/main"
COLLECTOR = FW / "collector"


def consts(path):
    src = path.read_text(encoding="utf-8")
    out = {}
    for name in ("kUart", "kTx", "kRx"):
        m = re.search(rf"\b{name}\s*=\s*(UART_NUM_\d|GPIO_NUM_\d+)", src)
        out[name] = m.group(1) if m else None
    return out


class PortsTest(unittest.TestCase):
    def test_only_collector_runs(self):
        self.assertIn("collector::run();", (FW / "main.cpp").read_text(encoding="utf-8"))

    def test_m12_is_on_port_a(self):
        self.assertEqual(consts(COLLECTOR / "m12_unit.cpp"), {"kUart": "UART_NUM_0", "kTx": "GPIO_NUM_2", "kRx": "GPIO_NUM_1"})

    def test_the_voice_module_is_on_port_c(self):
        self.assertEqual(consts(COLLECTOR / "asr_unit.cpp"), {"kUart": "UART_NUM_2", "kTx": "GPIO_NUM_17", "kRx": "GPIO_NUM_18"})

    def test_no_other_code_drives_the_four_pins(self):
        # hal_espnow.cpp drives GPIO2 as the factory ESP-NOW app's laser; app_main runs only collector::run(), so that
        # app never starts in this firmware.
        pins = re.compile(r"\bGPIO_NUM_(1|2|17|18)\b")
        for p in FW.rglob("*"):
            if p.suffix in (".c", ".cc", ".cpp", ".h") and p.name not in ("m12_unit.cpp", "asr_unit.cpp", "hal_espnow.cpp"):
                self.assertIsNone(pins.search(p.read_text(encoding="utf-8", errors="replace")), str(p))


if __name__ == "__main__":
    unittest.main()
