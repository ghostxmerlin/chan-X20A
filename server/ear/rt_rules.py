"""Docking's reply-gate decisions as pure functions (2026-11-19 refactor 4.2.9, after Fable's audit 4.3: "把判断写成纯函数
（输入一个明确的证据对象，输出判定），mixin 里只留从 hub 收集证据和执行判定"). Rules layer: no hub, no clock, no I/O.
rt_gate.GateMixin._judge collects the evidence, calls these in the same order as before and does the side effects
(start a look wait, release a pending vision / search, rescue) between them. reply_verdict (rt_base) is the last
decision and was already pure. Nothing here imports the hub's modules (rules layer)."""


def mode_mute(kind, order_age_s, order_s, called, debug, quiet, low, addressed):
    """A speech reply muted by the situation, before anything is heard of it -> the gate verdict or None.
    order_age_s: seconds since a direct order (None: none); called: inside a call / tap window; debug / quiet / low:
    调试 / 安静 / 少插话 on; addressed: the turn's sentence is plainly for hello (rt_base.addressed; 少插话 lets it through)."""
    if kind != "speech":
        return None
    if order_age_s is not None and order_age_s <= order_s:
        return "order"                                  # the sentence was a direct order to the robot (ORDER_S)
    if called:
        return None
    if debug:
        return "debug_mode"
    if quiet:
        return "quiet_mode"
    if low and not addressed:
        return "low_mode"
    return None


def look_wait_holds(wait, now):
    """A look question waits for the next fresh photo (its answer comes from the look, not from this reply)."""
    return wait is not None and not wait["sent"] and now - wait["at"] <= 4


def search_wait_holds(wait, now, redo_search):
    """A search question waits for the ear's own search (or a search answer is being redone)."""
    return (wait is not None and not wait["sent"] and (now - wait["at"] <= 4 or not wait["done"])) or redo_search


# ---- 受话判断，影子打分 (Fable's audit step 0, 2026-11-19): only logged with every sentence (gate.addressed), never decides.
# The weights follow the 2026-11-17 addressed-fusion plan as Fable proposed; tuned later from the owner's labels.
ADDRESS_WEIGHTS = {"touch": 80, "called": 60, "call_word": 60, "follow": 30, "question": 10, "you": 10, "owner": 10,
                   "face": 10}
FOLLOW_S = 20.0


def address_score(ev):
    """ev: {touch, called, call_word, follow_s (seconds since hello last spoke, None), question, you, owner, faces}
    -> (score, level): must (a tap / a call / the name in the sentence), should (>= 40), maybe (>= 15), low."""
    w = ADDRESS_WEIGHTS
    follow = ev.get("follow_s") is not None and ev["follow_s"] <= FOLLOW_S
    parts = {"touch": bool(ev.get("touch")), "called": bool(ev.get("called")) and not ev.get("touch"),
             "call_word": bool(ev.get("call_word")), "follow": follow, "question": bool(ev.get("question")),
             "you": bool(ev.get("you")), "owner": bool(ev.get("owner")), "face": (ev.get("faces") or 0) > 0}
    score = sum(w[k] for k, on in parts.items() if on)
    level = "must" if parts["touch"] or parts["called"] or parts["call_word"] else \
        "should" if score >= 40 else "maybe" if score >= 15 else "low"
    return score, level


def card_outcome(events):
    """What became of a sentence, from the hub's events in its window [(kind, state)], best first: answered (a reply
    played), muted:<verdict> (a reply held back), ambient (Qwen said not for me), none (Qwen gave no verdict)."""
    played = [s for k, s in events if k == "turn" and s == "done"]
    if played:
        return "answered"
    muted = [s for k, s in events if k == "turn" and isinstance(s, str) and s.startswith("muted_")]
    if muted:
        return "muted:" + muted[0][len("muted_"):]
    if any(k == "ambient" for k, _ in events):
        return "ambient"
    other = [s for k, s in events if k == "turn"]
    return f"turn:{other[0]}" if other else "none"
