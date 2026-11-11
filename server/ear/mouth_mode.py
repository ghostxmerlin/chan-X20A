"""嘴 mode (2026-11-11): who speaks for hello.

hello.switches {_id: "mouth", mode}:
  clips  the robot answers speech with an SD clip chosen by emotion (default; what it has done since 0.4.56)
  tts    hello speaks through soul-tts (/say from the page); emotion clips stay silent
  off    neither
The ear puts `respond` (clips mode only) into every speech event; the robot (collector-0.4.77+) picks no
clip when it is false. No document means clips; a failed read keeps the last mode (never back to clips
by accident while in TTS). Standard library only.
"""
MODES = ("off", "clips", "tts")
DEFAULT = "clips"


class ModeReader:
    def __init__(self, read):
        self.read = read
        self.mode = DEFAULT

    def update(self):
        try:
            doc = self.read()
        except Exception:             # noqa: BLE001 - keep the last mode
            return
        if not doc:
            self.mode = DEFAULT
        elif doc.get("mode") in MODES:
            self.mode = doc["mode"]

    @property
    def respond(self):
        return self.mode == "clips"
