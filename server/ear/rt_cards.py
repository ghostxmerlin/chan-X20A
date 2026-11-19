"""Docking hub: one card per sentence the ear heard (Fable's audit step 0 "先看见", 2026-11-19): the evidence at that
moment, and after CARD_WAIT_S what became of it (answered / muted:<verdict> / ambient / none). One run-log row
gate.addressed each, with a shadow score (rt_rules.address_score) that decides nothing. The sentence's text is not in it
(heard.asr with the same trace has it). The 漏答漏斗 page counts these rows."""
import rt_base as _rt_base  # noqa: E402
# every name of rt_base (also the underscore ones), so the code resolves as in realtime_link
globals().update({_k: _v for _k, _v in vars(_rt_base).items() if not _k.startswith("__")})
import rt_rules  # noqa: E402

CARD_WAIT_S = 10.0          # a sentence's outcome is read this long after the ear's text arrived
CARD_BEFORE_S = 4.0         # Qwen's turn for the same sentence often comes before the ear's own text


class CardsMixin:
    def _card_new(self, text, trace, speaker, now):
        band = (speaker or {}).get("band") if isinstance(speaker, dict) else None
        name = (speaker or {}).get("name") if isinstance(speaker, dict) else None
        last_reply = self._recent[-1][0] if self._recent else None
        t = str(text)
        ev = {"touch": self.in_called(now) and self._called_source == "touch", "called": self.in_called(now),
              "called_source": self._called_source if self.in_called(now) else None,
              "call_word": call_rest(t) is not None, "follow_s": None if last_reply is None else round(now - last_reply, 1),
              "question": is_question(t), "you": "你" in t, "addressed": addressed(t),
              "owner": band == "known" and self._owner_voice(name) or band == "unsure" and self._owner_voice(name),
              "band": band, "faces": len(self.watch.recent(now)),
              "quiet": self.quiet_on(now), "low": self.low_on(now), "debug": self.debug_on(now),
              "session": self.state, "busy": [k for k, on in (("sounding", self._sounding), ("output", self.output_busy()),
                                                              ("tool", self._tool_wait is not None),
                                                              ("guard", now < self._quiet_until)) if on],
              "chars": len(t)}
        score, level = rt_rules.address_score(ev)
        self._cards.append({"at": now, "trace": trace, "ev": ev, "score": score, "level": level})

    def _card_note(self, kind, state=None):
        self._card_events.append((self.clock(), kind, state))

    def _cards_settle(self, now):
        while self._cards and now - self._cards[0]["at"] >= CARD_WAIT_S:
            c = self._cards.popleft()
            # its window: from CARD_BEFORE_S before it (Qwen's turn often comes before the ear's own text) to
            # CARD_WAIT_S after it; an event within CARD_BEFORE_S before the NEXT sentence is that sentence's
            # (by time only: two sentences close together can still swap an event; Fable's audit 2.2 point 4)
            lo = max(c["at"] - CARD_BEFORE_S, getattr(self, "_card_prev_hi", -1e18))
            hi = min(c["at"] + CARD_WAIT_S, self._cards[0]["at"] - CARD_BEFORE_S if self._cards else 1e18)
            self._card_prev_hi = hi
            evs = [(k, s) for at, k, s in self._card_events if lo <= at < hi]
            self._log_card(c, rt_rules.card_outcome(evs))

    @_run_log
    def _log_card(self, c, outcome):
        self.stats["cards"] += 1
        ear_log.emit("gate.addressed", trace=c["trace"] or ear_log.new_trace(), by="ear",
                     sum=f"{c['level']} {c['score']} → {outcome}", ref={"session": self.session_no},
                     extra={**c["ev"], "score": c["score"], "level": c["level"], "outcome": outcome})
