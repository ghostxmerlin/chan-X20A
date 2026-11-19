"""Docking hub: 技能和说话人 — 工具清单、技能目录、预装 / 打开 / 收起、说话人是谁（声纹、脸）、认不认识（2026-11-19 refactor 4.2.5, moved from realtime_link.py unchanged)."""
import rt_base as _rt_base  # noqa: E402
# every name of rt_base (also the underscore ones), so the moved code resolves exactly as before
globals().update({_k: _v for _k, _v in vars(_rt_base).items() if not _k.startswith("__")})


class SkillsMixin:

    # ---- 千问用工具 (L6) ----
    def tools_on(self):
        return bool(self.tools_switch is not None and self.tools_switch.on)

    async def _load_tools(self):
        loop = asyncio.get_running_loop()
        try:
            self._tools_mode = await loop.run_in_executor(self._tool_pool, self.tools_mode_fn)
        except Exception:                              # noqa: BLE001
            self._tools_mode = "all"
        if self._tools_mode == "auto" and self.intent is not None:
            try:                                       # a new night's head (tools/intent/train.py)
                if await loop.run_in_executor(self._tool_pool, self.intent.reload):
                    log(f"realtime: auto head {getattr(self.intent, 'version', None)}", flush=True)
            except Exception:                          # noqa: BLE001
                pass
        try:
            status, body = await asyncio.wait_for(loop.run_in_executor(self._pool, self.call, "GET", "/tools", None,
                                                                       self.cfg.context_timeout_s),
                                                  self.cfg.context_timeout_s + 0.5)
        except Exception:                              # noqa: BLE001 - no tools this session
            return []
        ok = status == 200 and isinstance(body, dict)
        specs = realtime_tools((body or {}).get("tools")) if ok else []
        if self._tools_mode in ("skills", "auto"):
            cfg = load_skills()
            self._all_specs = {t["name"]: t for t in (realtime_tools(body.get("tools"), SKILL_TOOL_SKIP) if ok else [])}
            self._skills = {sk["id"]: sk for sk in cfg.get("skills", [])
                            if "*" in sk.get("tools", []) or any(n in self._all_specs for n in sk.get("tools", []))}
            self._skill_cfg, self._open_skills = cfg, {}
            if self._skills:
                log(f"realtime: session skills: {len(self._skills)} (of {len(specs)} tools)", flush=True)
                return [open_skill_spec(list(self._skills.values()))]
        log(f"realtime: session tools: {len(specs)}", flush=True)
        return specs

    def _reset_skills(self):
        self._tools_mode, self._all_specs, self._skills, self._skill_cfg, self._open_skills = "all", {}, {}, {}, {}
        self._preloaded = {}

    def _prune_skills(self, now):
        """Drop skills past their keep (a preloaded one never used: PRELOAD_KEEP_S) -> True when the set changed."""
        keep = float(self._skill_cfg.get("keep_min") or 10) * 60
        before, was_preloaded = set(self._open_skills), dict(self._preloaded)
        own = lambda k: float((self._skills.get(k) or {}).get("keep_min") or 0) * 60 or keep   # noqa: E731
        self._open_skills = {k: at for k, at in self._open_skills.items()
                             if now - at < (PRELOAD_KEEP_S if k in self._preloaded else own(k))}
        self._preloaded = {k: at for k, at in self._preloaded.items() if k in self._open_skills}
        cap = int(self._skill_cfg.get("max_open") or 3)
        for k in sorted(self._open_skills, key=self._open_skills.get)[:-cap]:
            del self._open_skills[k]
            self._preloaded.pop(k, None)
        # at most MAX_SKILL_TOOLS tools at once (owner 2026-11-18 "skill 加载太多也很沉重"): the oldest skill goes first
        free = any("*" in (self._skills.get(k) or {}).get("tools", []) for k in self._open_skills)
        while not free and len(self._open_skills) > 1 and \
                len(self._skill_tool_names(list(self._open_skills))) > int(self._skill_cfg.get("max_tools") or MAX_SKILL_TOOLS):
            oldest = min(self._open_skills, key=self._open_skills.get)
            del self._open_skills[oldest]
            self._preloaded.pop(oldest, None)
        for k in before - set(self._open_skills):      # 2026-11-19 owner: the skill process belongs in the log
            sk = self._skills.get(k) or {}
            pre = k in was_preloaded
            ear_log.emit("trigger.skill", trace=self._preload_trace.pop(k, None) if pre else ear_log.new_trace(),
                         sum=f"{'撤掉没用上的预装' if pre else '收起技能'}：{sk.get('title', k)}",
                         ref={"session": self.session_no},
                         extra={"skill": k, "action": "unload_unused" if pre else "close", "open": list(self._open_skills)})
        return set(self._open_skills) != before

    def _skill_tool_names(self, keys):
        names = []
        for k in keys:
            want = self._skills[k].get("tools", [])
            if "*" in want:                            # 自由发挥: every tool (owner 2026-11-18: room for creativity)
                want = list(self._all_specs)
            names += [n for n in want if n in self._all_specs and n not in names]
        return names

    def _skills_block(self):
        return "\n\n".join(self._skills[k].get("guide") or "" for k in self._open_skills if k in self._skills) or None

    def _push_skill_tools(self):
        """Tools of the open skills + their full text in the instructions, in one session.update."""
        names = self._skill_tool_names(list(self._open_skills))
        self._tool_specs = [open_skill_spec(list(self._skills.values()))] + [self._all_specs[n] for n in names]
        instr = self._instructions()
        self._send_soon({"type": "session.update", "session": {"tools": self._tool_specs, "instructions": instr}})
        self._live_instr = instr

    def _used_skill(self, tool_name, now, trace=None):
        """A preloaded skill whose tool the model called counts as opened (normal keep)."""
        for k in list(self._preloaded):
            if tool_name in (self._skills.get(k) or {}).get("tools", []):
                self._preloaded.pop(k, None)
                self._preload_trace.pop(k, None)
                self._open_skills[k] = now
                ear_log.emit("trigger.skill", trace=trace or ear_log.new_trace(),
                             sum=f"用上预装的技能：{(self._skills.get(k) or {}).get('title', k)}（{tool_name}）",
                             ref={"session": self.session_no},
                             extra={"skill": k, "action": "use_preloaded", "tool": tool_name, "open": list(self._open_skills)})

    def _preload(self, text, now, trace=None):
        """自动档: the ear's own ASR text, before the model answers -> the skill(s) it likely needs, loaded now."""
        if self.intent is None or not self.intent_on or self._tools_mode != "auto" or not self._skills or not self.tools_on() or \
                self._ws is None or not self._ready.is_set():
            return None
        try:
            d = self.intent.decide(text)
        except Exception:                              # noqa: BLE001 - no preload, the directory still works
            return None
        groups = d.get("skills") or []                 # the classifier's classes = skill groups (skills.json group)
        new = [sid for g in groups for sid, sk in self._skills.items()
               if (sk.get("group") or sid) == g and sid not in self._open_skills]
        self.stats["intent_checked"] += 1
        trace = trace or ear_log.new_trace()
        if new:
            self.stats["intent_preloaded"] += 1
            for sid in new:
                self._open_skills[sid], self._preloaded[sid] = now, now
                self._preload_trace[sid] = trace
            self._prune_skills(now)
            self._push_skill_tools()
        ear_log.emit("trigger.intent", trace=trace,
                     sum=f"自动档：要工具 {d.get('need', 0):.2f}" + (f"，先装 {'、'.join(self._skills[s]['title'] for s in new)}" if new else ""),
                     ref={"session": self.session_no}, extra={"need": d.get("need"), "skills": d.get("skills"),
                                                              "loaded": new, "heard": str(text)[:120]})
        return new

    def _open_skill(self, sid, now, trace=None, via="model"):
        """Open a skill: its tools join the session (session.update), kept keep_min, at most max_open at once.
        via: model (open_skill), auto_open (the model called a tool of a skill not open)."""
        sk = self._skills.get(sid)
        if sk is None:
            return {"ok": False, "error": {"code": "bad_args", "message": f"没有 {str(sid)[:20]} 这个技能"}}
        was = "preloaded" if sid in self._preloaded else "open" if sid in self._open_skills else None
        self._preloaded.pop(sid, None)
        self._preload_trace.pop(sid, None)
        self._open_skills[sid] = now
        self._prune_skills(now)
        self._push_skill_tools()
        self.stats["skills_opened"] += 1
        ear_log.emit("trigger.skill", trace=trace or ear_log.new_trace(),
                     sum=f"{'自动打开' if via == 'auto_open' else '打开'}技能：{sk['title']}" + ("（已装着）" if was else ""),
                     ref={"session": self.session_no},
                     extra={"skill": sid, "action": via, "was": was, "free": "*" in sk.get("tools", []),
                            "tools": [n for n in self._skill_tool_names([sid])][:40], "open": list(self._open_skills)})
        mine = [n for n in sk.get("tools", []) if n in self._all_specs]
        return {"ok": True, "data": {"skill": sk["title"], "tools": mine,
                                     "next": "做法已经放进说明的【此刻打开的技能】里，照着做，直接调用这些工具来回答"},
                "error": None}

    def _speaker_now(self, now):
        """The voiceprint of the newest sentence the ear heard in the last TOOL_GATE_TEXT_S, or (None, None)."""
        for at, band, name in reversed(self._speakers):
            if now - at <= TOOL_GATE_TEXT_S:
                return band, name
        return None, None

    def _speaker_text(self, now=None):
        band, name = self._speaker_now(self.clock() if now is None else now)
        if band is None:
            return None
        owner = self.ctx.get("owner") or {}
        if band == "known" and name and name in (owner.get("voice_names") or []):
            return f"{owner.get('call') or '主人'}（按声纹认出，是主人本人）"
        if band == "known":
            return "认识的家里人（按声纹认出）"
        # 2026-11-19 09:52: the owner's face in view, the voiceprint only "unsure" -> 不认识的声音 made the model refuse
        # 「转过身去」 itself. Unsure is not a stranger; a face M12 names in view says who is most likely speaking.
        voice = ("听着像主人（声纹没完全确定）" if self._owner_voice(name) else "声纹没听准是谁") if band == "unsure" \
            else "不认识的声音"
        try:
            faces = [f.get("name") for f in self.watch.recent(self.clock() if now is None else now) if f.get("name")]
        except Exception:                              # noqa: BLE001
            faces = []
        if faces:
            names = self.ctx.get("names") or {}
            who = "、".join(dict.fromkeys(f"主人（{owner.get('call') or '主人'}）" if n in (owner.get("face_names") or [])
                                          else (names.get(n) or n) for n in faces))
            # security review 2026-11-19: who is in view, not who is speaking (only_for_owner stays the voice's call)
            return f"{voice}；（另外，人脸识别看到眼前有{who}，不代表就是他在说话）"
        return voice

    def owner_in_person(self, now=None):
        """Owner 2026-11-18 "亲口算本人": the sentence was in the owner's own voice (voiceprint band known, one of the
        owner's voice names) -> a C tool runs at once."""
        band, name = self._speaker_now(self.clock() if now is None else now)
        owner = self.ctx.get("owner") or {}
        return band == "known" and bool(name) and name in (owner.get("voice_names") or [])

    def _owner_voice(self, name):
        return bool(name) and name in ((self.ctx.get("owner") or {}).get("voice_names") or [])

    def known_person(self, now=None):
        """Owner 2026-11-18 "身体可以只是认识的人才响应，认人策略可以考虑多种": a known voice (any enrolled voiceprint:
        owner, family) on the newest sentence, or a face M12 named in view right now."""
        now = self.clock() if now is None else now
        band, name = self._speaker_now(now)
        if band == "known":
            return True
        if band == "unsure" and self._owner_voice(name):
            # 2026-11-19 10:09: in the office the owner's voice scored 0.43-0.56 on his own print (known = 0.6) with
            # the runner-up ~0.25 behind -> unsure every time. For the body only, a clear lead on the owner's own print
            # counts (亲口 / C tools still need "known": owner_in_person).
            return True
        try:
            return any(f.get("name") for f in self.watch.recent(now))
        except Exception:                              # noqa: BLE001
            return False
