"""Is this sentence hello's own voice? (2026-11-11)

The ear hands VAD/ASR — and since 2026-11-11 听悟 too — silence while hello makes sound, so a sentence that
merely overlaps her sound was said by people. Only text matching one of her voice clips is her own:
  - overlapping her sound and matching a clip (3+ characters): her voice slipped past the silence window;
  - not overlapping but matching a long clip text (6+ characters): an echo or an unflagged playback.
Short words like "Yeah." without the overlap are people's. The clip texts come from the voice library
(`make_clip_texts`); without them nothing is hello's (the silence already keeps her out).
"""
import difflib
import json
import re

CLIPS = []                  # [(clip id, normalized text)]
MIN_OVERLAP, MIN_ECHO, RATIO = 3, 6, 0.6


def _norm(t):
    return re.sub(r"[\W_]", "", str(t or "").lower())


def set_clips(clips):
    global CLIPS
    CLIPS = [(c.get("id"), _norm(t)) for c in clips for t in c.get("texts", []) if len(_norm(t)) >= MIN_OVERLAP] \
        if clips and isinstance(clips[0], dict) else list(clips or [])


def load(path):
    """Load [{"id", "texts"}]; returns how many clips, 0 when the file is missing or unreadable."""
    try:
        with open(path, encoding="utf-8") as f:
            clips = json.load(f)
    except (OSError, ValueError):
        set_clips([])
        return 0
    set_clips(clips)
    return len(clips)


def is_hello(text, overlapped):
    n = _norm(text)
    if len(n) < (MIN_OVERLAP if overlapped else MIN_ECHO):
        return False
    for _, t in CLIPS:
        if (t in n or n in t) and min(len(n), len(t)) >= MIN_OVERLAP:
            return True
        if difflib.SequenceMatcher(None, n, t).ratio() >= RATIO:
            return True
    return False


def make_clip_texts(src, out):
    """voice/outputs/stackchan_playable_80.json → [{"id", "texts"}] (original text + the clip's own ASR)."""
    d = json.load(open(src, encoding="utf-8"))
    clips = []
    for c in d.get("candidates", []):
        texts = {c.get("original_text") or ""}
        asr = c.get("asr")
        if isinstance(asr, dict) and isinstance(asr.get("text"), str):
            texts.add(asr["text"])
        clips.append({"id": c.get("id"), "texts": sorted(t for t in texts if t)})
    with open(out, "w", encoding="utf-8") as f:
        json.dump(clips, f, ensure_ascii=False)
    return len(clips)


if __name__ == "__main__":
    import sys
    print(make_clip_texts(sys.argv[1], sys.argv[2]), "clips")
