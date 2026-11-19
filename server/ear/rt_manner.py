"""Docking hub: 说话方式模式 — 回答长短、安静、少插话、临时角色、调试模式，它们的时长、持久化和屏幕字（2026-11-19 refactor 4.2.2, moved from realtime_link.py unchanged)."""
import rt_base as _rt_base  # noqa: E402
# every name of rt_base (also the underscore ones), so the moved code resolves exactly as before
globals().update({_k: _v for _k, _v in vars(_rt_base).items() if not _k.startswith("__")})


class MannerMixin:

    def robot_back(self):
        """The robot (re)connected its /mouth link (a boot / sync report): put the mode's word back under its eyes."""
        if self.debug_on() or self.quiet_on() or self.low_on() or self.role_text():
            self._show_mode_word()

    def _word(self, text, hold_s):
        if self.screen_word is None:
            return
        if not text:
            self._touch_word = False
            self._show_mode_word()                     # back to 安静中 / 少插话, or nothing
            return
        try:
            self.screen_word(text, hold_s)
        except Exception:                              # noqa: BLE001 - the screen is a courtesy
            pass

    # ---- reply length (see LENGTH_S) ----
    def _length_mode(self, now=None):
        now = self.clock() if now is None else now
        m = self._length
        return m[0] if m is not None and now - m[1] <= LENGTH_S else "normal"

    def _length_line(self):
        mode = self._length_mode()
        if mode == "detail":
            return "主人刚让你说详细点：接下来问事情时多说几句，把来龙去脉讲清楚（闲聊仍然简短），直到他说简单点。"
        if mode == "brief":
            return "主人刚嫌你说得长：接下来每次一两句，只说重点，直到他让你详细说。"
        try:
            faces = len(self.watch.recent(self.clock()))
        except Exception:                              # noqa: BLE001
            faces = 0
        if faces >= CROWD_FACES:
            return f"眼前有 {faces} 个人，场面热闹：回答尽量短，一两句。"
        return None

    def _length_from(self, text, now, trace):
        """The owner's 详细点 / 简单点: kept LENGTH_S, pushed to the model at once."""
        mode = "detail" if DETAIL_RE.search(text) else "brief" if BRIEF_RE.search(text) else None
        if mode is None or (self._length is not None and self._length[0] == mode and now - self._length[1] < 3):
            return
        self._length = (mode, now)
        ear_log.emit("trigger.length", trace=trace or ear_log.new_trace(),
                     sum=f"回答长短：{'详细' if mode == 'detail' else '简短'}（{text[:30]}）",
                     ref={"session": self.session_no}, extra={"mode": mode, "heard": text[:200]})
        if self._ws is not None and self._ready.is_set():
            self._send_instructions("length", trace)

    def set_length(self, mode, trace=None, by="tool"):
        now = self.clock()
        self._length = None if mode == "normal" else (mode, now)
        ear_log.emit("trigger.length", trace=trace or ear_log.new_trace(), by=by,
                     sum=f"回答长短：{ {'detail': '详细', 'brief': '简短', 'normal': '恢复正常'}[mode] }（工具）",
                     ref={"session": self.session_no}, extra={"mode": mode})
        self._refresh_instructions("length", trace)
        return None if mode == "normal" else now + LENGTH_S

    # ---- 安静 / 少插话 / 临时角色 ----
    def quiet_on(self, now=None):
        now = self.clock() if now is None else now
        return self._quiet_mode is not None and now < self._quiet_mode

    def low_on(self, now=None):
        now = self.clock() if now is None else now
        return self._low is not None and now < self._low

    def role_text(self, now=None):
        now = self.clock() if now is None else now
        return self._role[0] if self._role is not None and now < self._role[1] else None

    def _refresh_instructions(self, why, trace=None):
        if self._ws is not None and self._ready.is_set():
            self._send_instructions(why, trace)

    def debug_on(self, now=None):
        now = self.clock() if now is None else now
        return self._debug is not None and now < self._debug

    def set_debug(self, on=True, minutes=None, until=None, span=None, trace=None, by="tool", heard=None):
        """调试模式 on (no length said = until the owner ends it) / off. Ending it (by a word or the clock, not the tool:
        the model answers that one itself) queues a question whether to sum up. -> until (clock / OPEN) or None."""
        now = self.clock()
        trace = trace or ear_log.new_trace()
        if on:
            span = self._span(minutes, until, span)
            if span is None or span == mode_span.CALLED:
                span = mode_span.OPEN
            end, _ = self._span_end(now, span, 0)
            fresh = not self.debug_on(now)
            self._debug = end
            if fresh:
                self._debug_since_ms = self.wall_ms()
            how = mode_span.describe(end, now, self.wall_ms())
            ear_log.emit("trigger.debug", trace=trace, by=by, sum=f"调试模式{'开始' if fresh else '改时间'}（{how}）",
                         ref={"session": self.session_no},
                         extra={"on": True, "open": end == mode_span.OPEN, "since_ms": self._debug_since_ms,
                                **({"heard": str(heard)[:200]} if heard else {})})
            if "debug" in self._skills and self._ws is not None and self._ready.is_set():
                self._open_skill("debug", now, trace=trace, via="auto_open")
        else:
            was = self.debug_on(now)
            self._debug = None
            end = None
            if was:
                ear_log.emit("trigger.debug", trace=trace, by=by, sum="调试模式结束", ref={"session": self.session_no},
                             extra={"on": False, "since_ms": self._debug_since_ms,
                                    **({"heard": str(heard)[:200]} if heard else {})})
                if by != "tool" and self._greet is None:
                    self._greet = {"at": now, "kind": "debug_end", "pick": None,
                                   "text": "[事件] 调试模式结束了：用一句话问主人要不要把这次调试总结一下（只问，不要自己总结）。"}
        self._refresh_instructions("debug", trace)
        self._write_status(now)
        self._persist_modes(now)
        self._show_mode_word()
        return end

    def debug_alert(self, text, trace=None):
        """mind's debug reminder (debug_mode.py): said in one line when hello is quiet. -> queued or why not."""
        now = self.clock()
        if not self.debug_on(now):
            return "not_debug"
        if self._last_debug_alert is not None and now - self._last_debug_alert < DEBUG_ALERT_S:
            return "too_soon"
        if self._greet is not None:
            return "busy"
        self._last_debug_alert = now
        self._greet = {"at": now, "kind": "debug", "pick": None,
                       "text": f"[事件] 调试中你发现了一件事，提醒主人一句：{str(text)[:200]}（用一句话自然地说，别念「提醒」这两个字）"}
        self.stats["debug_alerts"] += 1
        if self.state == "closed" and self.want():
            self._open(now, "debug")
        return "queued"

    def _debug_from(self, text, now, trace):
        to_me = self.in_called(now) or call_rest(text) is not None
        if self.debug_on(now):
            t = re.sub(r"\s+", "", str(text))
            if ("调试" in t and mode_span.ended(t)) or (to_me and mode_span.ended(t, loose=True) and not self.quiet_on(now)):
                self.set_debug(False, trace=trace, by="heard", heard=text)
            elif DEBUG_RE.search(t) and mode_span.parse(t) is not None:
                self.set_debug(True, span=mode_span.parse(t), trace=trace, by="heard", heard=text)   # a new length
        elif DEBUG_RE.search(re.sub(r"\s+", "", str(text))):
            self.set_debug(True, span=mode_span.parse(text), trace=trace, by="heard", heard=text)

    @staticmethod
    def _span(minutes=None, until=None, span=None):
        """The tool's minutes / until (exit | called), or a span from mode_span.parse -> seconds / OPEN / CALLED / None."""
        if until == "exit":
            return mode_span.OPEN
        if until == "called":
            return mode_span.CALLED
        if span is not None:
            return span
        return float(minutes) * 60 if minutes else None

    @staticmethod
    def _span_end(now, span, default_s):
        """-> (until clock or OPEN, sticky). Nothing said / until called: the default, not sticky."""
        if span is None or span == mode_span.CALLED:
            return now + default_s, False
        if span == mode_span.OPEN:
            return mode_span.OPEN, True
        return now + min(float(span), mode_span.MAX_S), True

    def set_quiet(self, level="quiet", minutes=None, trace=None, by="tool", heard=None, until=None, span=None):
        """level quiet / low / off; how long: minutes, until exit / called (the tool) or span (mode_span.parse, said).
        -> until (clock, mode_span.OPEN) or None."""
        now = self.clock()
        span = self._span(minutes, until, span)
        if level == "off":
            self._quiet_mode = self._low = None
            self._quiet_sticky = False
            end = None
        elif level == "quiet":
            end, self._quiet_sticky = self._span_end(now, span, QUIET_MAX_S)
            self._quiet_mode = end
            self._interrupt(now)
        else:
            end, _ = self._span_end(now, span, LOW_S)
            self._low = end
        how = mode_span.describe(end, now, self.wall_ms()) if end is not None else ""
        zh = {"quiet": "安静" + ("（直到被叫）" if level == "quiet" and not self._quiet_sticky else ""), "low": "少插话",
              "off": "恢复正常接话"}[level]
        ear_log.emit("trigger.quiet", trace=trace or ear_log.new_trace(), by=by,
                     sum=f"{zh}" + (f"（{str(heard)[:30]}）" if heard else "") + (f" {how}" if how else ""),
                     ref={"session": self.session_no}, extra={"mode": level, "minutes": minutes,
                                                              "open": end == mode_span.OPEN, "sticky": self._quiet_sticky,
                                                              **({"heard": str(heard)[:200]} if heard else {})})
        self._refresh_instructions("quiet", trace)
        self._write_status(now)
        self._persist_modes(now)
        self._show_mode_word()
        return end

    def _persist_modes(self, now):
        """安静 / 少插话 / 临时角色 into hello.switches {_id: rt_modes_status} (wall ms; null = open-ended)."""
        if self.db is None:
            return
        wall = self.wall_ms()
        ms = lambda u: None if u == mode_span.OPEN else int(wall + (u - now) * 1000)   # noqa: E731
        doc = {"quiet": {"until_ms": ms(self._quiet_mode), "open": self._quiet_mode == mode_span.OPEN,
                         "sticky": self._quiet_sticky} if self.quiet_on(now) else None,
               "low": {"until_ms": ms(self._low), "open": self._low == mode_span.OPEN} if self.low_on(now) else None,
               "role": {"text": self._role[0], "until_ms": ms(self._role[1]), "open": self._role[1] == mode_span.OPEN}
               if self.role_text(now) else None,
               "debug": {"since_ms": self._debug_since_ms, "until_ms": ms(self._debug), "open": self._debug == mode_span.OPEN}
               if self.debug_on(now) else None, "at": wall}
        try:
            self.db.switch_status("rt_modes", doc)
        except Exception:                              # noqa: BLE001 - kept in memory anyway
            pass

    def restore_modes(self, doc):
        """The modes of the last run (rt_modes_status), those still running. -> the names restored."""
        if not isinstance(doc, dict):
            return []
        now, wall, out = self.clock(), self.wall_ms(), []

        def end(m):
            if not isinstance(m, dict):
                return None
            if m.get("open"):
                return mode_span.OPEN
            u = m.get("until_ms")
            return now + (u - wall) / 1000 if isinstance(u, (int, float)) and u > wall else None
        q, lo, r, dbg = end(doc.get("quiet")), end(doc.get("low")), end(doc.get("role")), end(doc.get("debug"))
        if dbg is not None:
            self._debug, self._debug_since_ms = dbg, (doc.get("debug") or {}).get("since_ms") or wall
            out.append("debug")
        if q is not None:
            self._quiet_mode, self._quiet_sticky = q, bool((doc.get("quiet") or {}).get("sticky"))
            out.append("quiet")
        if lo is not None:
            self._low = lo
            out.append("low")
        if r is not None and (doc.get("role") or {}).get("text"):
            self._role = (str(doc["role"]["text"])[:120], r)
            out.append("role")
        if out:
            self._mode_word_at = now - 1800           # step() puts the word under the eyes once the loop runs
        return out

    def _show_mode_word(self):
        """安静中 / 少插话 stay under the eyes while the mode lasts (owner: short words, not states; these two are what
        hello is doing). Cleared when both are off."""
        if self.screen_word is None:
            return
        now = self.clock()
        # owner 2026-11-19 "处于什么特殊模式的，stackchan上要有提示": the most important one (collector-0.5.17 draws all)
        word, until = ("调试中", self._debug) if self.debug_on(now) else ("安静中", self._quiet_mode) if self.quiet_on(now) \
            else ("少插话", self._low) if self.low_on(now) else ("角色中", self._role[1]) if self.role_text(now) \
            else ("", None)
        self._mode_word_at = now if word else None
        try:
            self.screen_word(word, 3600 if until == mode_span.OPEN else int(until - now) if until else 0)
        except Exception:                              # noqa: BLE001 - the screen is a courtesy
            pass

    def set_role(self, text, minutes=None, trace=None, by="tool", until=None, span=None):
        now = self.clock()
        if not text:
            self._role = None
            end = None
        else:
            end, _ = self._span_end(now, self._span(minutes, until, span), ROLE_S)
            self._role = (str(text)[:120], end)
        how = mode_span.describe(end, now, self.wall_ms()) if end is not None else ""
        ear_log.emit("trigger.role", trace=trace or ear_log.new_trace(), by=by,
                     sum=(f"临时角色：{str(text)[:40]}" + (f" {how}" if how else "")) if text else "临时角色结束",
                     ref={"session": self.session_no}, extra={"role": str(text or "")[:120], "minutes": minutes,
                                                              "open": end == mode_span.OPEN})
        self._refresh_instructions("role", trace)
        self._write_status(now)
        self._persist_modes(now)
        self._show_mode_word()
        return end

    def _low_from(self, text, now, trace):
        t = norm_text(text)
        to_me = self.in_called(now) or call_rest(text) is not None
        if (self.low_on(now) or self.quiet_on(now)) and (LOW_END_RE.search(t) or mode_span.ended(text, loose=to_me)):
            self.set_quiet("off", trace=trace, by="heard", heard=text)       # 可以说话了 / 退出安静模式 / (to hello) 结束吧
        elif LOW_RE.search(t):
            span = mode_span.parse(text)
            if not self.low_on(now) or span is not None:
                self.set_quiet("low", trace=trace, by="heard", heard=text, span=span)

    def _role_from(self, text, now, trace):
        """Only a sentence said to hello (inside a call window, or starting with the call word)."""
        rest = call_rest(text)
        if not (self.in_called(now) or rest):
            return
        t = (rest or text).strip()
        if self.role_text(now) and (ROLE_END_RE.search(norm_text(t)) or ("角色" in t and mode_span.ended(t))):
            self.set_role(None, trace=trace, by="heard")
            return
        m = ROLE_RE.search(t)
        if m and m.start() <= 4:                        # "当个大姐姐…" at the start, not "他想当个医生"
            self.set_role(t[m.start():], trace=trace, by="heard", span=mode_span.parse(t))

    def _role_line(self):
        r = self.role_text()
        if not r:
            return None
        how = mode_span.describe(self._role[1], self.clock(), self.wall_ms())
        return (f"主人刚让你：{r}。在不改变你是谁、不违背你的底线的前提下，按这个角色说话（称呼、语气、关心的事都跟着变），"
                f"{how}，或者主人说恢复为止。")

    def _manner_line(self):
        now = self.clock()
        if self.debug_on(now):
            how = mode_span.describe(self._debug, now, self.wall_ms())
            return (f"主人在调试（调试模式，{how}）：你在旁边安静地听、帮他记，不插话、不搭腔；只有叫你的名字、hello 或点屏幕"
                    "时才回答。他问调试的事（刚才的值、第几次了、试过什么、你怎么看）先用 debug_log 看记录再答，技术问题可以再用"
                    " doc_answer 查资料；收到调试提醒的[事件]就照着说一句。")
        if self.quiet_on(now):
            how = mode_span.describe(self._quiet_mode, now, self.wall_ms())
            return ("主人让你先安静（" + how + "）：除非有人叫你的名字或 hello，否则什么都不说；" +
                    ("被叫到时只回答那一句，之后接着安静。" if self._quiet_sticky else "被叫到就恢复正常。"))
        if self.low_on(now) and not self.cfg.answer_all:  # 全答: 少插话 judges nothing
            how = mode_span.describe(self._low, now, self.wall_ms())
            return f"他们在聊天或在看电视（{how}）：别插话，不主动搭腔；只有叫你、或明确问你的时候才回答，回答也短。"
        return None
