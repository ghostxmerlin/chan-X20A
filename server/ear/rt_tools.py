"""Docking hub: 工具执行 — 工具门、跑工具（工具运行时算在说话、可被打断）、垫话（2026-11-19 refactor 4.2.6, moved from realtime_link.py unchanged)."""
import rt_base as _rt_base  # noqa: E402
# every name of rt_base (also the underscore ones), so the moved code resolves exactly as before
globals().update({_k: _v for _k, _v in vars(_rt_base).items() if not _k.startswith("__")})


class ToolsMixin:

    def _tool_allowed(self, turn, trace):
        """The tool gate: True for a call window, a chain already allowed (same trace), or a sentence the classifier
        (or a certain word) says needs tools. The sentence: the turn's transcript, else the ear's own ASR of the last
        few seconds."""
        now = self.clock()
        if trace in self._tool_ok_traces:
            return True
        ok = self.in_called(now) or turn.get("kind") == "tool_answer" and turn.get("tool_depth", 0) > 0 and \
            trace in self._tool_ok_traces
        texts = [turn.get("heard")] + [t for at, t in list(self._local_texts) if now - at <= TOOL_GATE_TEXT_S]
        texts = [str(t) for t in texts if t]
        if not ok:
            import intent as intent_mod                # rules only: no model needed
            ok = any(intent_mod.rule(t) for t in texts)
        if not ok and self.intent is not None and getattr(self.intent, "ready", False):
            try:
                ok = any(self.intent.decide(t).get("need", 0) >= self.intent.threshold for t in texts)
            except Exception:                          # noqa: BLE001
                ok = False
        if ok:
            self._tool_ok_traces.append(trace)
        return ok

    def _filler_in_chain(self, trace):
        """One "我查查" per sentence: a tool chain (open_skill, then the tool) shares the sentence's trace."""
        if trace in self._filler_traces:
            return True
        self._filler_traces.append(trace)
        return False

    async def _run_tool(self, turn):
        fc = turn["fc"]
        trace = turn.get("trace") or ear_log.new_trace()
        depth = int(turn.get("tool_depth") or 0) + 1
        try:
            args = json.loads(fc["arguments"] or "{}")
        except ValueError:
            args = None
        self.stats["tool_calls"] += 1
        # the sentence this chain answers, carried to the follow-up reply (its line in 【刚才的对话】)
        heard = turn.get("chain_heard") or turn.get("heard")
        if not heard:
            now = self.clock()
            heard = next((t for at, t in reversed(self._local_texts) if now - at <= TOOL_GATE_TEXT_S), None)
        self._next_heard = str(heard)[:120] if heard else None
        # 工具门 (owner 2026-11-18 "我不想让闲聊触发了工具调用，这个太伤"): smart_turn let the sentence through, but
        # a tool (and its 我查查) only for a sentence that earns it: a call window, the classifier / a certain word, or
        # a chain already let through. Otherwise the call goes back quietly and the model answers as in chat (its
        # reply then passes the 嗯 gate like any speech turn).
        if fc["name"] in self._skills and fc["name"] != "open_skill":      # a skill id called as a tool: open it
            args, fc = {"skill": fc["name"]}, {**fc, "name": "open_skill"}
        given = fc["name"] == "open_skill" or fc["name"] in {t["name"] for t in self._tool_specs}
        owner_skill = None
        if not given and self._skills and fc["name"] in self._all_specs and self.tools_on():
            # a tool of a skill not open yet (2026-11-19 acceptance: the model called review_period, was refused and
            # gave up): open that skill (its tools and text join the session), then run the call as usual
            owner_skill = next((k for k, sk in self._skills.items() if fc["name"] in sk.get("tools", [])), None)
            if owner_skill is not None:
                self._open_skill(owner_skill, self.clock(), trace=trace, via="auto_open")
                given = True
        if given and fc["name"] in BODY_TOOLS and not self.known_person():
            # owner 2026-11-18: the body moves only for people hello knows (voice or face)
            self.stats["body_gated"] += 1
            self._log_gate(turn.get("rid"), turn, "body_gated", True, tool=fc["name"])
            if self._ws is not None and self._ready.is_set():
                self._next_kind, self._next_trace, self._next_prompt, self._next_depth = "tool_refused", trace, None, depth
                self._send_soon({"type": "conversation.item.create", "item": {
                    "type": "function_call_output", "call_id": fc["call_id"],
                    "output": json.dumps({"ok": False, "error": {"code": "refused", "message":
                        "只有认识的人叫我动我才动：不做这个动作，可以友好地说一句还不认识他。"}}, ensure_ascii=False)}})
                self._send_soon({"type": "response.create"})
            return
        if given and fc["name"] != "open_skill" and not self._tool_allowed(turn, trace):
            self.stats["tool_gated"] += 1
            self._log_gate(turn.get("rid"), turn, "tool_gated", True, tool=fc["name"])
            if self._ws is not None and self._ready.is_set():
                self._next_kind, self._next_trace, self._next_prompt, self._next_depth = "tool_refused", trace, None, depth
                self._send_soon({"type": "conversation.item.create", "item": {
                    "type": "function_call_output", "call_id": fc["call_id"],
                    "output": json.dumps({"ok": False, "error": {"code": "refused", "message":
                        "这句不需要用工具：像平常聊天一样回答（不是跟你说的就只回「嗯」）。"}}, ensure_ascii=False)}})
                self._send_soon({"type": "response.create"})
            return
        quick = ("open_skill", "set_length", "set_quiet", "set_role", "who_is_here", "switches_status", "mode_status",
                 "capabilities", "robot_status", "set_voice") + tuple(BODY_TOOLS)     # moving needs no 我查查
        if given and fc["name"] not in quick and not self._filler_in_chain(trace):
            group = FILLER_GROUP.get(fc["name"], "search")
            clips = self.fillers if group == "look" else \
                self.tool_fillers.get(group) or self.tool_fillers.get("search") or self.search_fillers or self.fillers
            self._play_filler(clips, "search_filler", trace, group=group)     # by the situation, at random
        t0 = self.clock()
        # owner 2026-11-19 "修": skills and auto allow SKILL_DEPTH real tool calls a sentence (open_skill not counted,
        # as tools/skills/eval.py measured); before, auto used TOOL_DEPTH 3 with open_skill counted
        skilled = self._tools_mode in ("skills", "auto") and bool(self._skills)
        calls = self._chain_calls.get(trace, 0)
        over = (depth > SKILL_DEPTH * 2 or fc["name"] != "open_skill" and calls >= SKILL_DEPTH) if skilled \
            else depth > TOOL_DEPTH
        if fc["name"] != "open_skill":
            self._used_skill(fc["name"], self.clock(), trace)
        if fc["name"] == "open_skill" and self._tools_mode in ("skills", "auto") and self._skills and self.tools_on() and \
                any(t["name"] == "open_skill" for t in self._tool_specs):
            env = self._open_skill((args or {}).get("skill") if isinstance(args, dict) else None, self.clock(), trace) \
                if not over else {"ok": False, "error": {"code": "refused", "message": TOOL_ENOUGH}}
        elif fc["name"] not in {t["name"] for t in self._tool_specs}:     # only what this session was given
            env = {"ok": False, "error": {"code": "not_available", "message": f"没有给你 {fc['name'][:30]} 这个工具"}}
        elif args is None or not isinstance(args, dict):
            env = {"ok": False, "error": {"code": "bad_args", "message": "参数不是合法的 JSON"}}
        elif over:
            env = {"ok": False, "error": {"code": "refused", "message": TOOL_ENOUGH}}
        else:
            self._chain_calls[trace] = calls + 1
            while len(self._chain_calls) > 64:
                self._chain_calls.popitem(last=False)
            wait_s = SLOW_TOOL_TIMEOUT_S.get(fc["name"], TOOL_TIMEOUT_S)
            loop = asyncio.get_running_loop()
            shown = fc["name"] not in quick
            self._tool_wait = {"trace": trace, "name": fc["name"], "since": self.clock()}
            if shown:
                self._stage("tool")
                self._word(TOOL_WORD.get(fc["name"]) or TOOL_WORD_GROUP.get(FILLER_GROUP.get(fc["name"], "search"), "在查"),
                           int(wait_s))
            try:
                status, body = await asyncio.wait_for(loop.run_in_executor(
                    self._tool_pool, self.call, "POST", "/tool/call",
                    {"name": fc["name"], "args": args, "trace": trace, "caller": "qwen",
                     "owner": self.owner_in_person()}, wait_s),
                    wait_s + 1)
                env = body if status == 200 and isinstance(body, dict) else \
                    {"ok": False, "error": {"code": "failed", "message": f"工具服务回了 {status}"}}
            except Exception as e:                     # noqa: BLE001 - mind slow / down
                env = {"ok": False, "error": {"code": "timeout", "message": f"工具没回应（{type(e).__name__}）"}}
            finally:
                self._tool_wait = None
                if shown:
                    self._word("", 0)
        ms = int((self.clock() - t0) * 1000)
        out = {"ok": env.get("ok"), "data": env.get("data"), "error": env.get("error")}
        text = json.dumps(out, ensure_ascii=False)
        if len(text) > TOOL_OUTPUT_CHARS:
            text = text[:TOOL_OUTPUT_CHARS] + "…（截断）"
        band, vname = self._speaker_now(self.clock())
        # 2026-11-19 owner: review needs what came back and who asked (result summary here, the full text as a body)
        ear_log.emit("call.realtime.tool", trace=trace, ok=bool(env.get("ok")), ms=ms,
                     sum=f"千问用工具：{fc['name']}" + ("" if env.get("ok") else f"（{(env.get('error') or {}).get('message', '')[:30]}）"),
                     ref={"session": self.session_no}, body={"result": text},
                     extra={"tool": fc["name"], "args": args, "depth": depth, "calls": self._chain_calls.get(trace, 0),
                            "mode": self._tools_mode, "open": list(self._open_skills),
                            "result": text[:300], "speaker": {"band": band, "name": vname},
                            "known": self.known_person(), "owner": self.owner_in_person()})
        if self._ws is None or not self._ready.is_set():
            return
        if trace in self._tool_cancelled:             # called / tapped meanwhile: the answer is not wanted any more
            self._tool_cancelled.discard(trace)
            self.stats["tool_dropped"] += 1
            self._send_soon({"type": "conversation.item.create", "item": {
                "type": "function_call_output", "call_id": fc["call_id"],
                "output": json.dumps({"ok": False, "error": {"code": "cancelled", "message":
                    "主人打断了，这个结果不用了：不要再提它，等主人接着说。"}}, ensure_ascii=False)}})
            return
        self._stage("think")
        self._next_kind, self._next_trace, self._next_prompt, self._next_depth = "tool_answer", trace, None, depth
        self._send_soon({"type": "conversation.item.create", "item": {"type": "function_call_output",
                                                                      "call_id": fc["call_id"], "output": text}})
        self._send_soon({"type": "response.create"})

    def _try_filler(self, now):
        """One "我看看哈" per look question, at once when hello is quiet (no model, no cost); not when the look is
        already in or the question is FILLER_MAX_S old."""
        w = self._look_wait
        if w is None or w["filler"] or w["sent"] or w["look"] is not None:
            return
        if now - w["at"] > FILLER_MAX_S:
            w["filler"] = True
            return
        if self.output_busy() or self._sounding or self.link() is None:
            return
        w["filler"] = True
        self._play_filler(self.fillers, "look_filler", w.get("trace"))

    def _play_filler(self, clips, kind, trace, group=None):
        """One pre-made clip (look_filler / search_filler), never the one played last of that kind (or group)."""
        if not clips:
            return
        now = self.clock()
        if group is not None:
            last = self._filler_last_by.get(group)
        else:
            last = self._filler_last if kind == "look_filler" else self._search_filler_last
        picks = [i for i in range(len(clips)) if i != last] or [0]
        i = picks[self.rng.randrange(len(picks))]
        if group is not None:
            self._filler_last_by[group] = i
        elif kind == "look_filler":
            self._filler_last = i
        else:
            self._search_filler_last = i
        rid = f"filler{next(self._beeps)}"
        pcm = clips[i][1]
        self._turns[rid] = {"kind": kind, "speech_end": None, "heard_chars": None, "rid": rid,
                            "session": self.session_no, "response_at": now, "first_delta": now, "sound_at": None,
                            "reply_chars": len(clips[i][0]), "reply": clips[i][0],
                            "audio_ms": len(pcm) * 1000 // (SR_OUT * 2), "usage": None, "at": self.wall_ms()}
        self.stats[kind + "s"] += 1
        log(f"realtime: session {self.session_no} {kind.replace('_', ' ')}", flush=True)
        ear_log.emit("filler.play", trace=trace, sum=clips[i][0][:60] or None,
                     ref={"rid": rid, "session": self.session_no},
                     extra={"kind": kind, "clip": i, "group": group, "audio_ms": self._turns[rid]["audio_ms"]})
        self._out_put(("pcm", rid, pcm))
        self._out_put(("end", rid, b""))
