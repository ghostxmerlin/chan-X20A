"""hello's bodies (owner 2026-11-20: the Mac and the StackChan are not two robots but two bodies of one robot).

One robot, several bodies; each body says what it can do, and the server branches on that, never on a name prefix.
Shipped flat next to the ear and mind (like hello_log.py); no imports beyond the standard library.

- KINDS: every kind of body hello has, with its abilities (what the body can do; the body may override them in its
  first message) and its tuning (how the server treats its signals; the server's own, a body never sets it).
- body(source, declared): the body behind a link. `source` is the link's "source" (the robot: "stackchan-<mac>",
  the Mac: "stackchan-mac-<id>"); `declared` is the message's optional {"kind", "abilities"} (a body that sends
  none, like the robot's firmware today, gets its kind's defaults, found from the source). Not a body (the web
  page's mic, test clients): None, and the server treats it as before (TUNING_NONE).

Kinds and abilities (docs/mac-hello/platform-split.md has the table and the plan):
  hearing      the body sends its mic (SEA1 Opus)
  voice_module "unit-asr" (hardware Unit ASR) | "on-device" (the Mac's system recogniser, may be off) | None
  camera       the body uploads camera frames on change
  faces        the face-print scheme: "m12-128" | "apple-featureprint" (prints of two schemes never compare)
  body_sense   shaken / picked up / near (an IMU)
  motion       "servo" (nod, shake, turn, follow) | "eyes" (gestures drawn on the screen instead)
  screen       "320x240" | "window"
  touch        "screen" | "mouse"
  clips        "sd" (SD-card voice clips) | "bundled" (in the app)
  ota          firmware updates over the air
"""
import re

ABILITY_TYPES = {"hearing": bool, "voice_module": (str, type(None)), "camera": bool, "faces": (str, type(None)),
                 "body_sense": bool, "motion": (str, type(None)), "screen": str, "touch": str, "clips": (str, type(None)),
                 "ota": bool}

# Tuning (the ear's, per kind):
#   quiet_labels      typing / breathing / cough may start a sound event from +5 dB (the robot mic's hum floor,
#                     2026-11-09); otherwise the 15 dB gate measured on a Mac mic (2026-11-06)
#   unclear_is_voice  a sentence with CED voice band "unclear" counts as voice for voiceprint enrolment (the robot
#                     mic's voice probability runs low: clear sentences 0.17-0.26 on 2026-11-10)
#   denoise           the streaming GTCRN in front of VAD / ASR / realtime
TUNING_NONE = {"quiet_labels": False, "unclear_is_voice": False, "denoise": False}

KINDS = {
    "stackchan": {
        "name": "机身",
        "abilities": {"hearing": True, "voice_module": "unit-asr", "camera": True, "faces": "m12-128",
                      "body_sense": True, "motion": "servo", "screen": "320x240", "touch": "screen", "clips": "sd",
                      "ota": True},
        "tuning": {"quiet_labels": True, "unclear_is_voice": True, "denoise": True},
    },
    "mac": {
        "name": "Mac",
        "abilities": {"hearing": True, "voice_module": "on-device", "camera": True, "faces": "apple-featureprint",
                      "body_sense": False, "motion": "eyes", "screen": "window", "touch": "mouse", "clips": "bundled",
                      "ota": False},
        # the Mac mic has no hum floor: the 15 dB gate and the plain voice band (both measured on a Mac mic);
        # the denoiser stays as the 2026-11-19 night's end-to-end test ran it
        "tuning": {"quiet_labels": False, "unclear_is_voice": False, "denoise": True},
    },
}

# source prefixes -> kind, longest first ("stackchan-mac-" before "stackchan")
PREFIXES = (("stackchan-mac-", "mac"), ("stackchan", "stackchan"))


def safe_id(value):
    """The body's id: its source, only [A-Za-z0-9_-], at most 64 (vision_link.safe_source)."""
    text = re.sub(r"[^A-Za-z0-9_-]", "", str(value or ""))[:64]
    return text or "unknown"


def kind_of(source):
    s = str(source or "")
    for prefix, kind in PREFIXES:
        if s.startswith(prefix):
            return kind
    return None


def _abilities(kind, declared):
    out = dict(KINDS[kind]["abilities"])
    given = declared.get("abilities") if isinstance(declared, dict) else None
    if isinstance(given, dict):
        for k, v in given.items():
            # only known abilities of the right type; a bool is never taken for a string
            if k in ABILITY_TYPES and isinstance(v, ABILITY_TYPES[k]) and (type(v) is bool) == (ABILITY_TYPES[k] is bool):
                out[k] = v[:32] if isinstance(v, str) else v
    return out


def body(source, declared=None):
    """{"id", "kind", "name", "abilities", "tuning", "declared"} for a link of one of hello's bodies, else None."""
    d = declared if isinstance(declared, dict) else None
    k = d.get("kind") if d else None
    kind = k if isinstance(k, str) and k in KINDS else kind_of(source)
    if kind is None:
        return None
    return {"id": safe_id(source), "kind": kind, "name": KINDS[kind]["name"], "abilities": _abilities(kind, d),
            "tuning": dict(KINDS[kind]["tuning"]), "declared": bool(d)}


def tuning(b, key):
    """A tuning value for a body (or for a link that is no body: TUNING_NONE)."""
    return (b or {}).get("tuning", TUNING_NONE).get(key, TUNING_NONE[key])


def can(b, ability, value=None):
    """Whether the body has an ability (truthy), or has it with exactly `value` (can(b, "motion", "servo"))."""
    a = (b or {}).get("abilities", {}).get(ability)
    return bool(a) if value is None else a == value


def catalog():
    """Every kind hello has, for pages and the mind: [{"kind", "name", "abilities", "tuning"}]."""
    return [{"kind": k, "name": v["name"], "abilities": dict(v["abilities"]), "tuning": dict(v["tuning"])}
            for k, v in KINDS.items()]
