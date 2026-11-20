"""Docking hub: hello 自己开口的事件 — 主人出现打招呼、旧的单击打招呼、被晃说一句、调试提醒 / 结束的投递、say 工具、两声提示音（2026-11-19 refactor 4.2.3, moved from realtime_link.py unchanged)."""
import rt_base as _rt_base  # noqa: E402
# every name of rt_base (also the underscore ones), so the moved code resolves exactly as before
globals().update({_k: _v for _k, _v in vars(_rt_base).items() if not _k.startswith("__")})


class EventsMixin:

    def on_vision(self, msg):
        """Every robot M12 event (VisionLink). Owner appears after away_s unseen in crowd -> a greeting."""
        if not isinstance(msg, dict):
            return
        now = self.clock()
        name = self.watch.event(msg, now)
        if name is None or not self.armed():
            return
        owners = (self.ctx.get("owner") or {}).get("face_names")
        if owners is None:
            self._appear = (name, now)                 # decided when mind's owner names arrive
        elif name in owners:
            self._queue_greet(now)

    def _queue_greet(self, now):
        if self.cfg.answer_all:                        # 点屏幕聊天: no greeting of its own (owner: 全手动)
            return
        if self._last_greet is not None and now - self._last_greet < self.cfg.greet_gap_s:
            self.stats["greet_too_soon"] += 1
            return
        size = "long"
        if self._greet is None:
            self._greet = {"at": now, "kind": "owner", "pick": None, "size": size}
            self.stats["greet_queued"] += 1
        if self.state == "closed" and self.want():
            self._open(now, "owner")

    def tap(self):
        """A tap on hello's home screen in crowd (MouthLink sees the robot's mode_report with input tap): look at the
        M12 faces of the last face_recent_s and greet: a known person by name, else the largest face by its apparent
        gender / age group, else say nobody is in view. Never interrupts: it waits until hello is quiet. -> True if
        queued."""
        now = self.clock()
        if not self.armed() or (self._last_tap is not None and now - self._last_tap < self.cfg.tap_cooldown_s):
            return False
        self._last_tap = now
        pick = tap_pick(self.watch.recent(now))
        self._greet = {"at": now, "kind": "tap", "pick": pick}          # a tap replaces a waiting owner greeting
        self.stats["taps"] += 1
        log(f"realtime: tap greet: {pick['kind']}", flush=True)
        if self.state == "closed" and self.want():
            self._open(now, "tap")
        return True

    def body_event(self, ev, fields=None):
        """A body-sense event (body_sense.py). shaken / patted (a head-pat burst, 0.5.21): hello says one line about it,
        both sharing SHAKEN_SAY_S. Owner 2026-11-20: not held by 点屏幕聊天 (the pending line opens the session). -> True
        if queued."""
        if ev not in ("shaken", "patted") or not self.armed():
            return False
        now = self.clock()
        if self._last_shaken_say is not None and now - self._last_shaken_say < SHAKEN_SAY_S:
            return False
        if self._greet is not None and self._greet.get("kind") != "body":
            return False                               # a greeting waiting goes first
        self._last_shaken_say = now
        self._greet = {"at": now, "kind": "body", "pick": None, "text": PATTED_TEXT if ev == "patted" else SHAKEN_TEXT}
        self.stats["shaken_said"] += 1
        if self.state == "closed" and self.want():
            self._open(now, "body")
        return True

    def body_changed(self):
        """A body event (picked up, put down, shaken, near / far): re-send the instructions (debounced like a look)."""
        self._looks_dirty = True
        self._push_looks(self.clock())

    # ---- tools (ask_api.py) ----
    def say(self, text, trace=None):
        """Speak one line now (Docking). -> "sent" / "not_ready" / "busy" / "quiet"."""
        if not self._ready.is_set() or self._ws is None:
            return "not_ready"
        if self.busy() or self._seg is not None:
            return "busy"
        self._next_kind, self._next_trace = "say", trace or ear_log.new_trace()
        self._next_prompt = f"（系统：请用你平常的语气把下面这句话说出来，意思不变，不要加别的话）{str(text)[:200]}"
        ear_log.emit("trigger.say", trace=self._next_trace, by="tool", sum=f"让它说：{str(text)[:40]}",
                     ref={"session": self.session_no}, extra={"text": str(text)[:200]})
        self._send_soon({"type": "conversation.item.create", "item": {"type": "message", "role": "user",
                                                                      "content": [{"type": "input_text",
                                                                                   "text": self._next_prompt}]}})
        self._send_soon({"type": "response.create"})
        return "sent"

    # ---- greeting ----
    def _deliver_greet(self, now):
        g = self._greet
        owner = self.ctx.get("owner") or {}
        call = owner.get("call")
        if g is None or not self._ready.is_set() or self.busy() or self.clock() < self._quiet_until:
            return
        if g is not None and g["kind"] in ("debug", "debug_end"):
            pass                                       # the debug reminder / end question: said in 调试 too
        elif self.quiet_on(now) or self.low_on(now) or self.debug_on(now):   # no greetings (step() drops an old one)
            return
        if self._seg is not None:                      # the server hears someone speaking: it would refuse (2026-11-18)
            return
        if now - g["at"] > self.cfg.greet_max_age_s:
            return                                     # step() drops it
        if g["kind"] == "owner":
            if not call:
                return
            if g.get("size") == "short":
                pick = self.rng.randrange(len(SHORT_WORDS) + 1)       # a word, or (last slot) two beeps
                if pick == len(SHORT_WORDS):
                    self._greet = None
                    self._last_greet = now
                    self.stats["greeted"] += 1
                    ear_log.emit("trigger.greet", trace=ear_log.new_trace(), by="m12", sum="招呼 greet_beep",
                                 ref={"session": self.session_no}, extra={"kind": "greet_beep"})
                    self._beep(now)
                    return
                text = greet_text(call, "short", word=SHORT_WORDS[pick])
            else:
                text = greet_text(call, "long", tuple(self._said))
        elif g["kind"] in ("body", "debug", "debug_end"):
            text = g["text"]
        else:
            text = tap_text(g["pick"], self.ctx.get("names") or {}, owner.get("face_names") or [], call or "主人")
        self._greet = None
        if g["kind"] == "owner" or (g["kind"] == "tap" and g["pick"]["kind"] == "known" and g["pick"]["face"]["name"] in
                                    (owner.get("face_names") or [])):
            self._last_greet = now
        if g["kind"] == "owner" and g.get("size", "long") == "long":
            self._last_long = now
        self._next_kind = ("greet" if g.get("size", "long") == "long" else "greet_short") if g["kind"] == "owner" \
            else g["kind"] if g["kind"] in ("body", "debug", "debug_end") else "tap"
        self._next_trace, self._next_prompt = ear_log.new_trace(), text
        ear_log.emit("trigger.greet", trace=self._next_trace,
                     by={"tap": "tap", "body": "body", "debug": "mind", "debug_end": "debug"}.get(g["kind"], "m12"),
                     sum=f"招呼 {self._next_kind}", ref={"session": self.session_no},
                     extra={"kind": self._next_kind, "pick": (g.get("pick") or {}).get("kind")})
        self.stats["greeted"] += 1
        log(f"realtime: session {self.session_no} {self._next_kind} greeting", flush=True)
        self._send_soon({"type": "conversation.item.create", "item": {"type": "message", "role": "user",
                                                                      "content": [{"type": "input_text", "text": text}]}})
        self._send_soon({"type": "response.create"})

    def _beep(self, now):
        """A short hello as two local chirps: played like a reply (same /mouth stream path), no model, no cost."""
        rid = f"beep{next(self._beeps)}"
        pcm = BEEP                                     # owner: "都差不多啊，随便用一个吧" — one fixed beep (sample 1)
        self._turns[rid] = {"kind": "greet_beep", "speech_end": None, "heard_chars": None, "rid": rid,
                            "session": self.session_no, "response_at": now, "first_delta": now, "sound_at": None,
                            "reply_chars": 0, "audio_ms": len(pcm) * 1000 // (SR_OUT * 2), "usage": None,
                            "at": self.wall_ms()}
        log(f"realtime: session {self.session_no} greet_short beep", flush=True)
        self._out_put(("pcm", rid, pcm))
        self._out_put(("end", rid, b""))
