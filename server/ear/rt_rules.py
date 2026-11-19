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
