"""Owner 2026-11-20: the M12 and the voice module swapped plugs on the body. M12 = UART0 on Port B (TX GPIO9, RX GPIO8);
the StickS3 voice module = UART2 on Port C (RX GPIO18 = the Stick's TX, TX GPIO17 = the Stick's RX). Both move in
one build: a build with only one moved would leave a device on the wrong pins."""
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
    def test_m12_is_on_port_b(self):
        self.assertEqual(consts(COLLECTOR / "m12_unit.cpp"), {"kUart": "UART_NUM_0", "kTx": "GPIO_NUM_9", "kRx": "GPIO_NUM_8"})

    def test_the_voice_module_is_on_port_c(self):
        self.assertEqual(consts(COLLECTOR / "asr_unit.cpp"), {"kUart": "UART_NUM_2", "kTx": "GPIO_NUM_17", "kRx": "GPIO_NUM_18"})

    def test_no_other_code_drives_the_four_pins(self):
        pins = re.compile(r"\bGPIO_NUM_(8|9|17|18)\b")
        for p in FW.rglob("*"):
            if p.suffix in (".c", ".cc", ".cpp", ".h") and p.name not in ("m12_unit.cpp", "asr_unit.cpp"):
                self.assertIsNone(pins.search(p.read_text(encoding="utf-8", errors="replace")), str(p))


if __name__ == "__main__":
    unittest.main()
