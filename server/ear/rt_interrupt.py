"""Docking hub: 叫名字、口令、触摸、闭嘴和打断 — 叫到的窗口、点屏幕必答 / 停、双击安静、停下自己的回答和正在跑的工具（2026-11-19 refactor 4.2.4, moved from realtime_link.py unchanged)."""
import rt_base as _rt_base  # noqa: E402
# every name of rt_base (also the underscore ones), so the moved code resolves exactly as before
globals().update({_k: _v for _k, _v in vars(_rt_base).items() if not _k.startswith("__")})


class InterruptMixin:

    # ---- being called (see CALLED_S) ----
    def on_wake(self, link, m):
        """MouthLink: the robot's {"type":"asr_wake","word":"providence",...} (the StickS3's PROVIDENCE since
        collector-0.5.19, the Unit ASR's hello before; the robot already stopped its speech)."""
        self.called("asr", interrupted=bool((m or {}).get("interrupted")))

    def called(self, source, text=None, trace=None, interrupted=False):
        """hello was called (asr / name / text). Opens the window, stops hello's reply, lets the audio up."""
        if not self.armed():
            return
        now = self.clock()
        if self.cfg.answer_all and source not in ("touch", "asr") and not self.chat_on(now):
            return                                     # 点屏幕聊天: only a tap or the robot's name word opens a chat
        if self.cfg.answer_all and source == "asr" and not self.chat_on(now):
            self.start_chat(trace, "name")             # owner 2026-11-20: the StickS3's PROVIDENCE opens a chat
        merged = self._called_at is not None and now - self._called_at < 2.0
        self._called_at, self._called_until = now, now + CALLED_S
        self._called_source = source
        self.stats["called_" + source] += 1
        stopped = self._interrupt(now) or interrupted
        self._quiet_until = 0.0
        if self._quiet_mode is not None and self.quiet_on(now) and self._quiet_sticky:
            # a said length (2026-11-19): the call is answered, 安静 goes on afterwards
            ear_log.emit("trigger.quiet", trace=trace or ear_log.new_trace(), by=source,
                         sum=f"安静中被叫：只答这一句（{mode_span.describe(self._quiet_mode, now, self.wall_ms())}）",
                         extra={"mode": "quiet", "why": "called_sticky"})
        elif self._quiet_mode is not None:             # a call wakes hello from 安静
            self._quiet_mode = None
            ear_log.emit("trigger.quiet", trace=trace or ear_log.new_trace(), by=source, sum="安静结束：被叫醒",
                         extra={"mode": "off", "why": "called"})
            self._write_status(now)
            self._persist_modes(now)
            self._show_mode_word()
        self._send_until = max(self._send_until, now + CALLED_S)
        if not merged:
            self._stage("listen")
            trace = trace or ear_log.new_trace()
            if source != "text":                       # asr / name: what is said next goes on this call's chain
                self._called_trace = trace
            ear_log.emit("heard.called", trace=trace, by=source,
                         sum=f"被叫了：{CALLED_SOURCES.get(source, source)}" + ("，打断了它的话" if stopped else ""),
                         ref={"session": self.session_no}, extra={"source": source, "interrupted": stopped,
                                                                  **({"text": str(text)[:200]} if text else {})})
        if self.state == "closed" and self.want():
            self._open(now, "called")

    def on_order(self, link, m):
        """MouthLink: the robot's {"type":"asr_order","word":...} (collector-0.5.07): a direct order it carries out."""
        self.ordered(str((m or {}).get("word") or "")[:40], "asr")

    def ordered(self, word, source, trace=None):
        """A factory voice command (the robot's asr_order, or a command word heard after the call word). Owner
        2026-11-20 (Providence): those commands are off and the robot no longer carries them out, so a sentence with
        one is an ordinary sentence: the call window stays, the reply is not muted. Counted only."""
        self.stats["order_ignored_" + source] += 1

    def _interrupt(self, now):
        """Stop hello's own reply (generating or playing), and a running tool. -> True if something was stopped."""
        stopped = False
        w = self._tool_wait
        if w is not None and w["trace"] not in self._tool_cancelled:
            self._tool_cancelled.add(w["trace"])
            self.stats["tool_interrupted"] += 1
            stopped = True
            ear_log.emit("trigger.touch", trace=w["trace"], by="interrupt", sum=f"打断了正在用的工具：{w['name']}",
                         ref={"session": self.session_no}, extra={"action": "tool_cancel", "tool": w["name"]})
        if self._resp is not None:
            self._dropped.add(self._resp)
            self._send_soon({"type": "response.cancel"})
            stopped = True
        st = self._stream
        if st is not None and not st["reported"]:
            self._give_up(st, "interrupted")
            stopped = True
        if stopped:
            self.stats["interrupted"] += 1
        return stopped

    def in_called(self, now=None):
        return (self.clock() if now is None else now) < self._called_until

    def _answer_called(self, text, now, trace, why):
        """A sentence in a call window the model did not answer: ask it again through the rescue path."""
        if not text:
            return
        self._called_text = None
        self._rescue = {"at": now, "texts": [str(text)[:300]], "sent": False, "trace": trace, "called": why}
        ear_log.emit("gate.realtime", trace=trace or ear_log.new_trace(), sum=f"被叫了，照样回答（{why}）{str(text)[:30]}",
                     extra={"verdict": "called_answer", "reason": why, "heard": str(text)[:300]})
        self._try_rescue(now)

    def touched(self, stop=False, trace=None):
        """A tap on the screen in Docking (MouthLink: mode_report crowd / tap; owner 2026-11-19). stop (the robot's
        rules attached stop_speech: she was speaking): stop her reply here too, nothing else. Else a must-answer call:
        the call window for TOUCH_S (wakes 安静, lets the audio up, opens the session) and 在听 under the eyes."""
        now = self.clock()
        trace = trace or ear_log.new_trace()
        if self.cfg.answer_all and not self.armed():
            ok = self._touch_open(trace)               # 点屏幕聊天: a tap with Docking off turns it on and starts a chat
            if ok:
                self.start_chat(trace, "docking_off")
            return ok
        if not self.armed():
            return False
        if self.cfg.answer_all and not stop:
            if self.quiet_on(now):
                self.set_quiet("off", trace=trace, by="touch")
            self.start_chat(trace)
        stop = stop or self._tool_wait is not None      # a tool running counts as speaking (older firmware: no stop)
        self.stats["touch_stop" if stop else "touch_listen"] += 1
        if stop:
            stopped = self._interrupt(now)
            self._called_until, self._called_text = 0.0, None
            ear_log.emit("trigger.touch", trace=trace, by="touch", sum="点屏幕：停下" + ("" if stopped else "（已经没在说了）"),
                         ref={"session": self.session_no}, extra={"action": "stop", "stopped": stopped})
            self._word("", 0)
            return True
        self.called("touch", trace=trace)
        self._called_until = max(self._called_until, now + TOUCH_S)
        self._send_until = max(self._send_until, now + TOUCH_S)
        self._touch_word = True
        self._word("在听", int(TOUCH_S))
        return True

    def touch_quiet(self, trace=None):
        """A double tap on the screen in Docking (owner 2026-11-19): 安静一下 (stops a reply; a call or a tap ends it)."""
        if not self.armed():
            return False
        if self.cfg.answer_all:                        # 点屏幕聊天: a double tap ends the chat (stops a reply)
            self._interrupt(self.clock())
            self._called_until, self._called_text = 0.0, None
            self.end_chat("double_tap", trace)
            return True
        self.stats["touch_quiet"] += 1
        self._touch_word = False
        self._called_until, self._called_text = 0.0, None
        self.set_quiet("quiet", trace=trace or ear_log.new_trace(), by="touch")
        return True

    def stop_speaking(self):
        return self._interrupt(self.clock())

    def _hush(self, text=None, trace=None):
        """A spoken 闭嘴: the 围观 switch off through mind (the session closes when the switch reader sees it)."""
        if self._hush_busy:
            return
        self._hush_busy = True
        self.stats["hush"] += 1
        log("realtime: hush heard, switching Docking off", flush=True)
        try:
            ear_log.emit("trigger.hush", trace=trace, by="asr", sum=f"闭嘴 → Docking 关：{str(text or '')[:40]}",
                         ref={"session": self.session_no},
                         extra={"word": "闭嘴" if HUSH_ANY.search(norm_text(text or "")) else "short",
                                "action": "realtime_off"})
        except Exception as e:                      # noqa: BLE001 - the switch-off below must still happen
            _log_failed("hush", e)
        self.spawn(self._post_hush())

    def _touch_open(self, trace):
        """全答 + Docking off: a tap = Docking on (mind's switch; the session opens when the ear reads it, <= 5 s).
        Not while paused (nobody there / today's cap: the pause decides) or without key / voice / Docking mode."""
        if not (self.key and self.voice and self.in_crowd()) or getattr(self.switch, "paused", False) or self._hush_busy:
            return False
        self._hush_busy = True
        self.stats["touch_open"] += 1
        ear_log.emit("trigger.touch", trace=trace, by="touch", sum="点屏幕：打开对话（全答）",
                     ref={"session": self.session_no}, extra={"action": "open"})
        self._word("在听", int(TOUCH_S))
        self.spawn(self._post_switch(True, "touch"))
        return True

    async def _post_switch(self, on, by):
        loop = asyncio.get_running_loop()
        try:
            fut = loop.run_in_executor(self._pool, self.call, "POST", "/switches", {"name": "realtime", "on": on, "by": by},
                                       self.cfg.context_timeout_s)
            status, _ = await asyncio.wait_for(fut, self.cfg.context_timeout_s + 0.5)
        except Exception:                              # noqa: BLE001 - mind slow / down
            status = None
        finally:
            self._hush_busy = False
        if status != 200:
            log(f"realtime: tap: switch on failed ({status})", flush=True)

    async def _post_hush(self):
        loop = asyncio.get_running_loop()
        try:
            # by hush: not an owner's input for the pause (a TV can say 闭嘴; mind/api.py note_owner)
            fut = loop.run_in_executor(self._pool, self.call, "POST", "/switches", {"name": "realtime", "on": False,
                                                                                    "by": "hush"},
                                       self.cfg.context_timeout_s)
            status, _ = await asyncio.wait_for(fut, self.cfg.context_timeout_s + 0.5)
        except Exception:                              # noqa: BLE001 - mind slow / down
            status = None
        finally:
            self._hush_busy = False
        if status != 200:
            log(f"realtime: hush: switch off failed ({status})", flush=True)
