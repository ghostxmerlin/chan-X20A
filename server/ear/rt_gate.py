"""Docking hub: 回答闸门和补救 — 一个回答播不播（口令、安静 / 调试 / 少插话、等照片 / 等搜索、嗯 / 重复 / 没听清 / 标记 / 看不清），被挡的「嗯」怎么补救，【刚才的对话】的来源（2026-11-19 refactor 4.2.8, moved from realtime_link.py unchanged)."""
import rt_base as _rt_base  # noqa: E402
# every name of rt_base (also the underscore ones), so the moved code resolves exactly as before
globals().update({_k: _v for _k, _v in vars(_rt_base).items() if not _k.startswith("__")})


import rt_rules  # noqa: E402


class GateMixin:

    def _chain_heard(self, turn):
        """A tool / event reply's line in 【刚才的对话】: the sentence that started its chain (same trace), never a
        "[tool_refused]" placeholder (2026-11-19 09:52: the model copied it as its own reply)."""
        if turn.get("chain_heard"):
            return turn["chain_heard"]
        tr = turn.get("trace")
        for t in list(self._turns.values())[::-1]:
            if tr and t.get("trace") == tr and t.get("heard") and t.get("kind") in ("speech", "rescue"):
                return t["heard"]
        return "（接着上一句）" if turn.get("kind") in ("tool_answer", "tool_refused") else "（hello 自己开口）"

    def _recent_pairs(self):
        now = self.clock()
        out = []
        for at, turn, reply in self._recent:
            if now - at <= self.cfg.recent_s:
                heard = turn.get("heard") if turn.get("kind") in ("speech", "rescue", "look_answer", "search_answer") \
                    else self._chain_heard(turn)
                out.append(((heard or "")[:60], reply))
        return out

    def _judge(self, rid, turn, done):
        """The reply gate (see reply_verdict): plays the held audio, or drops the reply for good."""
        if turn.get("gate") is not None:
            return
        now = self.clock()
        # the decisions are rt_rules' pure functions (refactor 4.2.9); the side effects stay here, in the same order
        v = rt_rules.mode_mute(turn.get("kind"), None if self._order_at is None else now - self._order_at, ORDER_S,
                               self.in_called(now), self.debug_on(now), self.quiet_on(now), self.low_on(now),
                               addressed(turn.get("heard") or ""))
        if v == "order":                               # the sentence was a direct order to the robot (see ORDER_S)
            turn["gate"], turn["held"], turn["muted"] = v, [], f"muted_{v}"
            self._dropped.add(rid)
            self.stats["muted_order"] += 1
            self._log_gate(rid, turn, v, done)
            return
        if v is not None:                              # 调试 / 安静 / 少插话
            turn["gate"], turn["held"], turn["muted"] = v, [], f"muted_{v}"
            self._dropped.add(rid)
            self.stats[f"muted_{v}"] += 1
            if self._pending_vision is not None or self._pending_search is not None:
                self._release_vision(now, False, {"quiet_mode": "安静中", "debug_mode": "调试中"}.get(v, "少插话中"))
            self._log_gate(rid, turn, v, done)
            return
        if turn.get("kind") == "speech" and (self._pending_vision is not None or self._pending_search is not None):
            q = reply_verdict(turn.get("tx"), done)
            if q is None:
                return                                 # too little of the reply yet to tell 「嗯」 from an answer
            self._release_vision(now, q != "quiet", "千问只回了「嗯」")   # a real reply: taken; 「嗯」: not for hello
        if turn.get("kind") == "speech":
            asked = None if self.cfg.turn != "server_vad" else \
                next((t for t in [turn.get("heard")] + [t for at, t in self._local_texts if now - at <= 3]
                      if t and is_look_ask(t)), None)  # smart_turn: only through the pending gate above
            if asked is not None:
                self._start_look_wait(asked, now, turn.get("trace"))
            w = self._look_wait
            if rt_rules.look_wait_holds(w, now):
                v = "wait_look"                        # the answer comes from the next fresh look
                turn["gate"], turn["held"], turn["muted"] = v, [], f"muted_{v}"
                self._dropped.add(rid)
                self.stats["muted_wait_look"] += 1
                log("realtime: reply muted (wait_look)", flush=True)
                self._log_gate(rid, turn, v, done)
                return
            asked = None if self.cfg.turn != "server_vad" else \
                next((t for t in [turn.get("heard")] + [t for at, t in self._local_texts if now - at <= 3]
                      if t and is_search_ask(t)), None)  # smart_turn: only through the pending gate (_release_vision)
            if asked is not None:
                self._start_search_wait(asked, now, turn.get("trace"))
            w = self._search_wait
            redo = self._redo is not None and self._redo["kind"] == "search_answer"
            if rt_rules.search_wait_holds(w, now, redo):
                v = "wait_search"                      # the answer comes from the ear's own search
                turn["gate"], turn["held"], turn["muted"] = v, [], f"muted_{v}"
                self._dropped.add(rid)
                self.stats["muted_wait_search"] += 1
                log("realtime: reply muted (wait_search)", flush=True)
                self._log_gate(rid, turn, v, done)
                return
        recent = [norm_text(r) for at, _, r in self._recent if now - at <= self.cfg.recent_s]
        fresh = bool(self._looks) and self.wall_ms() - self._looks[-1][0] <= FRESH_LOOK_S * 1000
        v = reply_verdict(turn.get("tx"), done, recent,
                          blind=not fresh and turn.get("kind") not in ("look_answer", "search_answer"))
        if v is None:
            return
        turn["gate"], held, turn["held"] = v, turn["held"], []
        self._log_gate(rid, turn, v, done, fresh_look=fresh)
        if v == "play":
            for pcm in held:
                if rid not in self._dropped:
                    self._out_put(("pcm", rid, pcm))
            return
        turn["muted"] = f"muted_{v}"
        self._dropped.add(rid)
        if v == "quiet" and turn.get("kind") == "speech":
            end = turn.get("speech_end") or now
            texts = [turn["heard"]] if turn.get("heard") else []
            texts += [t for at, t in self._local_texts if end - 3 <= at <= now + 0.5]
            self._rescue = {"at": now, "texts": texts, "sent": False, "trace": turn.get("trace")}
            self._try_rescue(now)
        self.stats[f"muted_{v}"] += 1
        log(f"realtime: reply muted ({v})", flush=True)

    @_run_log
    def _log_gate(self, rid, turn, v, done, **extra):
        """gate.realtime: one verdict per reply (emit only: this runs on the voice loop)."""
        if not turn.get("trace"):
            turn["trace"] = ear_log.new_trace()        # nothing heard by the ear: the reply starts its own trace
        verdict = "play" if v == "play" else f"muted_{v}"
        ear_log.emit("gate.realtime", trace=turn["trace"], sum=f"{verdict} {(turn.get('tx') or '')[:40]}".strip(),
                     ref={"rid": rid, "session": turn.get("session")},
                     extra={"verdict": verdict, "reason": GATE_REASONS.get(v, v), "kind": turn.get("kind"),
                            "done": done, "heard": self._heard_in(turn, self.clock()),
                            # owner 2026-11-18: smart_turn_v2's verdict beside ours (a reply = it took the turn)
                            **({"smart_turn": "accepted"} if turn.get("kind") == "speech" and self.cfg.turn != "server_vad" else {}),
                            **extra})

    def _try_rescue(self, now):
        r = self._rescue
        if r is None or r["sent"]:
            return
        if now - r["at"] > RESCUE_S or (not r.get("called") and self._last_speech is not None and self._last_speech > r["at"]):
            self._rescue = None                        # too late, or someone is already talking again
            return
        follow = bool(self._recent) and now - self._recent[-1][0] <= FOLLOW_S      # hello just spoke
        called = r.get("called") or self.in_called(now) or r["at"] < self._called_until
        heard = next((t for t in r["texts"] if addressed(t) or (follow and is_question(t)) or
                      (called and len(norm_text(t)) >= 2)), None)
        if heard is not None and is_look_ask(heard):
            self._start_look_wait(heard, now, r.get("trace"))
        if heard is not None and self._look_wait is not None:
            r["sent"] = True                           # a look question is answered from the next fresh look
            self._log_rescue(r, "look_wait", heard)
            return
        if heard is not None and is_search_ask(heard):
            self._start_search_wait(heard, now, r.get("trace"))
        if heard is not None and self._search_wait is not None:
            r["sent"] = True                           # a search question is answered from the ear's search
            self._log_rescue(r, "search_wait", heard)
            return
        if heard is None or self._ws is None or not self._ready.is_set() or self._resp is not None \
                or self.output_busy():
            return                                     # step() tries again
        r["sent"], self._rescue_heard, self._next_kind = True, heard, "rescue"
        self.stats["rescued"] += 1
        log(f"realtime: session {self.session_no} muted 嗯 was addressed: asked again", flush=True)
        self._log_rescue(r, "asked", heard)
        self._next_trace, self._next_prompt = r.get("trace"), rescue_text(heard)
        self._send_soon({"type": "conversation.item.create", "item": {"type": "message", "role": "user",
                                                                      "content": [{"type": "input_text",
                                                                                   "text": rescue_text(heard)}]}})
        self._send_soon({"type": "response.create"})

    @_run_log
    def _log_rescue(self, r, outcome, heard):
        ear_log.emit("gate.rescue", trace=r.get("trace"), sum=f"{outcome} {str(heard)[:40]}",
                     ref={"session": self.session_no}, extra={"outcome": outcome, "texts": len(r["texts"])})
