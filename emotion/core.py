"""Pure lifecycle and result validation, independent of network/model libraries."""
from dataclasses import dataclass
import json
import math

EMOTIONS = {"unknown", "neutral", "happy", "sad", "angry", "anxious", "surprised"}
EVIDENCE_TTL = 20.0
IDLE_SECONDS = 600.0


@dataclass
class OutputWatchdog:
    # Network retries NEVER extend this deadline; only new nonempty STT does.
    last_output: float
    sleeping: bool = False
    last_key: tuple = ()

    def output(self, now, generation, index, text):
        text = text.strip() if isinstance(text, str) else ""
        key = (generation, index, text)
        if self.sleeping or not text or key == self.last_key:
            return False
        self.last_key = key
        self.last_output = now
        return True

    def expire(self, now):
        if not self.sleeping and now - self.last_output >= IDLE_SECONDS:
            self.sleeping = True
            return True
        return False

    def wake(self, now, sound_edge):
        if self.sleeping and sound_edge:
            self.sleeping = False
            self.last_output = now
            self.last_key = ()
            return True
        return False


class SoundGate:
    """Local energy onset: 80 ms above floor, rearmed by 300 ms quiet.

    This is sound activity, not an emotion or speaker classifier. A steady fan
    must not immediately reopen a task which just hit its 10 minute limit.
    """
    def __init__(self):
        self.floor = 0.002
        self.loud = self.quiet = 0.0
        self.armed = True

    def disarm(self):
        self.armed = False
        self.loud = self.quiet = 0.0

    def feed(self, rms, seconds=0.02):
        if not math.isfinite(rms) or seconds <= 0:
            return False
        threshold = max(0.008, self.floor * 3.0)
        if rms < threshold:
            self.quiet += seconds
            self.loud = 0.0
            self.floor = 0.99 * self.floor + 0.01 * min(rms, 0.02)
            if self.quiet >= 0.3:
                self.armed = True
        else:
            self.quiet = 0.0
            self.loud += seconds
            if self.armed and self.loud >= 0.08:
                self.armed = False
                return True
        return False


def transcript_event(message):
    """Ignore keepalives/empty results and never treat translations as STT."""
    header, payload = message.get("header", {}), message.get("payload", {})
    name = header.get("name")
    if name not in ("TranscriptionResultChanged", "SentenceEnd"):
        return None
    text = payload.get("result", "")
    if not isinstance(text, str) or not text.strip():
        return None
    stamp = payload.get("time")
    if not isinstance(stamp, (int, float)) or isinstance(stamp, bool) or not math.isfinite(stamp) or stamp < 0:
        stamp = None
    return payload.get("index", 0), text.strip()[:1500], name == "SentenceEnd", stamp


def validate_inference(raw, has_text, face_count):
    """Fail closed on hallucinated schemas, absent evidence or low certainty."""
    try:
        # Permit a single JSON fence, not arbitrary explanatory prose.
        text = raw.strip()
        if text.startswith("```json") and text.endswith("```"):
            text = text[7:-3].strip()
        data = json.loads(text)
        emotion = data["emotion"]
        source = data["evidence"]
        certainty = data["certainty"]
        if emotion not in EMOTIONS or certainty not in {"low", "medium", "high"}:
            raise ValueError("invalid inference")
        if source not in {"none", "text", "vision", "both"}:
            raise ValueError("invalid evidence")
        if emotion == "unknown" or certainty == "low" or source == "none":
            return "unknown", "none"
        if source in {"text", "both"} and not has_text:
            return "unknown", "none"
        if source in {"vision", "both"} and face_count != 1:
            return "unknown", "none"
        # Multiple visible people cannot be attributed to the wearer/user.
        if face_count > 1:
            return "unknown", "none"
        return emotion, source
    except (ValueError, KeyError, TypeError):
        return "unknown", "none"
