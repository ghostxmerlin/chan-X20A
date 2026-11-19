"""Docking hub: 看和搜 — 人脸、即时画面、注视（看一眼的问题等下一张照片）、视觉爆发词、身体感觉一行、联网搜索的问题（等耳朵自己搜）（2026-11-19 refactor 4.2.7, moved from realtime_link.py unchanged)."""
import rt_base as _rt_base  # noqa: E402
# every name of rt_base (also the underscore ones), so the moved code resolves exactly as before
globals().update({_k: _v for _k, _v in vars(_rt_base).items() if not _k.startswith("__")})


class LooksMixin:

    def _seen(self):
        owner = self.ctx.get("owner") or {}
        return seen_text(self.watch.recent(self.clock()), self.ctx.get("names") or {},
                         owner.get("face_names") or [], owner.get("call") or "主人")

    def _body_line(self):
        try:
            return self.body.line(self.wall_ms()) if self.body is not None else None
        except Exception:                              # noqa: BLE001 - a body-sense bug never costs the instructions
            return None

    def faces_now(self):
        owner = self.ctx.get("owner") or {}
        names = self.ctx.get("names") or {}
        out = []
        for f in self.watch.recent(self.clock()):
            n = f.get("name")
            who = (owner.get("call") or "主人") if n and n in (owner.get("face_names") or []) else \
                (names.get(n) if isinstance(names.get(n), str) else n) if n else "不认识的人"
            out.append({"who": who, "known": bool(n), "gender": f.get("gender"), "age": f.get("age_group")})
        return out

    def scene_now(self):
        """(at_ms, text) of the newest instantly recognised photo, or None."""
        return self._scene

    # ---- 即时画面 (vision_burst.LiveEye) ----
    def add_scene(self, text, at_ms):
        """The newest uploaded camera photo, recognised at once: goes out with the instructions (debounced)."""
        if not text:
            return
        self._scene = (at_ms, str(text)[:200])
        self._looks_dirty = True
        self._push_looks(self.clock())

    def _scene_line(self):
        if self._scene is None:
            return None
        age = max(0, (self.wall_ms() - self._scene[0]) / 1000)
        if age > SCENE_FRESH_S:
            return None
        return f"{int(age)} 秒前：{self._scene[1]}"

    # ---- vision burst (vision_burst.py) ----
    def add_look(self, text, at_ms, snapped=None):
        """One recognised burst photo: kept in memory (last LOOKS_KEEP) and pushed with the instructions (debounced).
        snapped: the burst clock when its photo was asked for (a pending look question takes only a later one)."""
        if not text:
            return
        self._looks.append((at_ms, str(text)[:200]))
        self._looks_dirty = True
        for mark, fut in list(self.look_waiters):      # look_now tool (ask_api.py)
            if snapped is not None and snapped >= mark and not fut.done():
                fut.set_result(str(text)[:200])
        self.look_waiters = [(m, f) for m, f in self.look_waiters if not f.done()]
        w = self._look_wait
        if w is not None and not w["sent"] and w.get("look") is None and snapped is not None and snapped >= w["mark"]:
            now = self.clock()
            # 2026-11-18: the first photo after the question often comes before the thing is held up; an unclear look
            # waits for the next one while there is time (at most LOOK_UNCLEAR_SKIP, never past the last 2 s)
            if str(text).startswith("看不清") and w.get("skipped", 0) < LOOK_UNCLEAR_SKIP and now < w["deadline"] - 2:
                w["skipped"] = w.get("skipped", 0) + 1
                self.stats["look_unclear_skipped"] += 1
            else:
                w["look"] = str(text)[:200]
                self._try_look_answer(now)
        self._push_looks(self.clock())

    def look_question(self):
        """The look question waiting for a photo (vision_burst asks the photo this question), or None."""
        w = self._look_wait
        return w["question"] if w is not None and not w["sent"] and w.get("look") is None else None

    # ---- look questions (see LOOK_ASK) ----
    def _start_look_wait(self, question, now, trace=None):
        w = self._look_wait
        if w is not None and not w["sent"] and now <= w["deadline"]:
            w["question"] = str(question)[:80]         # the same wait: keep its mark, take the newest wording
            return
        if self.burst is None or not self.active():
            return
        try:
            with ear_log.scope(trace=trace):
                mark = self.burst.ask("ask")
        except Exception:                              # noqa: BLE001 - a burst never costs the session
            log(f"realtime: look ask failed\n{traceback.format_exc().rstrip()}", flush=True)
            mark = None
        if mark is None:
            return                                     # no burst can run (switch / crowd / link): reply as usual
        self._look_wait = {"at": now, "mark": mark, "question": str(question)[:80], "deadline": now + LOOK_WAIT_S,
                           "sent": False, "look": None, "filler": not self.fillers, "trace": trace}
        self.stats["look_asks"] += 1
        log("realtime: look question: waiting for a fresh look", flush=True)
        self._stage("think")
        self._try_filler(now)

    # ---- search questions (see web_search.py) ----
    def _start_search_wait(self, question, now, trace=None, query=None, task=None):
        w = self._search_wait
        if w is not None and not w["sent"] and now <= w["deadline"]:
            return False                               # already searching (both ASRs heard it): one search
        if self.searcher is None or not self.active():
            return False
        w = {"at": now, "question": str(question)[:80], "deadline": now + SEARCH_WAIT_S, "sent": False,
             "done": False, "result": None, "filler": not self.search_fillers, "trace": trace,
             "query": str(query or question)[:160], "task": task}
        self._search_wait = w
        self.stats["search_asks"] += 1
        log("realtime: search question: searching", flush=True)
        self._stage("think")
        self.spawn(self._run_search(w))
        self._try_search_filler(now)
        return True

    async def _run_search(self, w):
        loop = asyncio.get_running_loop()
        try:
            with ear_log.scope(trace=w.get("trace")):  # call.search carries the question's trace
                ctx = contextvars.copy_context()
                result = await loop.run_in_executor(None, functools.partial(ctx.run, self.searcher, w["query"]))
        except Exception:                              # noqa: BLE001 - a failed search: hello says so
            log(f"realtime: search failed\n{traceback.format_exc().rstrip()}", flush=True)
            result = None
        w["result"] = result if isinstance(result, str) and result.strip() else None
        w["done"] = True
        if w.get("task") is not None:
            w["task"]["results"].append(w["result"])
        self.stats["search_found" if w["result"] else "search_none"] += 1
        self._try_search_answer(self.clock())

    # ---- search tasks (see TASK_S) ----
    def _task_live(self, now):
        t = self._qtask
        return t if t is not None and not t.get("closed") and now - t["at"] <= TASK_S else None

    def _task_open(self, topic, now, trace):
        t = self._task_live(now)
        if t is not None and now - t.get("last", -1e9) < 3:
            return                                     # the other ASR heard the same question
        self._qtask = {"topic": str(topic)[:80], "at": now, "tries": 1, "results": [], "answered": False,
                       "trace": trace, "last": now}
        self._task_log("开始", self._qtask, trace)

    def _task_follow(self, text, now, trace):
        """A follow-up on the open task (核实 / 查错了 / 结果呢, or 算了). -> True when it was one."""
        t = self._task_live(now)
        if t is None:
            return False
        if GIVEUP_RE.search(text):
            t["closed"] = "owner"
            self._task_log("主人说不用查了", t, trace)
            return True
        if not VERIFY_RE.search(text):
            return False
        if now - t.get("last", -1e9) < 3:
            return True                                # both ASRs: once
        self._task_search(now, trace, f"主人追问：{text[:30]}")
        return True

    def _task_promise(self, reply, turn, now):
        """hello said it would look something up: make it true (the open task, or what was just asked)."""
        w = self._search_wait
        if w is not None and not w["sent"]:
            return                                     # a search is already on its way
        t = self._task_live(now)
        if t is None:
            topic = turn.get("heard") or next((x for at, x in reversed(self._local_texts) if now - at <= 15), None)
            if not topic:
                return
            self._task_open(topic, now, turn.get("trace"))
            t = self._qtask
            t["tries"] = 0
        elif now - t.get("last", -1e9) < 3:
            return                                     # the owner's follow-up already started this search
        self._task_search(now, turn.get("trace"), f"它说了要去查：{reply[:30]}")

    def _task_search(self, now, trace, why):
        t = self._qtask
        t["last"] = now
        t["tries"] += 1
        if t["tries"] > TASK_MAX_TRIES:
            t["closed"] = "unresolved"
            self._task_log("查不准，如实告诉主人", t, trace)
            w = {"at": now, "question": t["topic"], "deadline": now, "sent": False, "done": True, "result": None,
                 "filler": True, "trace": trace, "query": t["topic"], "task": t,
                 "prompt": task_give_up_text(t["topic"], t["results"])}
            self._search_wait = w
            self._try_search_answer(now)
            return
        query = f"{t['topic']}（要最新的实时数据，写明数据的时间和来源网站）"
        if self._start_search_wait(t["topic"], now, trace, query=query, task=t):
            self._task_log(f"再查第 {t['tries']} 次（{why}）", t, trace)

    @_run_log
    def _task_log(self, what, t, trace):
        ear_log.emit("trigger.task", trace=trace or ear_log.new_trace(), sum=f"查询任务：{what} · {t['topic'][:30]}",
                     ref={"session": self.session_no},
                     extra={"topic": t["topic"], "tries": t["tries"], "closed": t.get("closed"),
                            "answered": t.get("answered"), "results": [str(r)[:80] for r in t["results"]]})

    def _try_search_filler(self, now):
        """One "我查查哈" per search question, at once when hello is quiet; not when the result is already in."""
        w = self._search_wait
        if w is None or w["filler"] or w["sent"] or w["done"]:
            return
        if now - w["at"] > FILLER_MAX_S:
            w["filler"] = True
            return
        if self.output_busy() or self._sounding or self.link() is None:
            return
        w["filler"] = True
        self._play_filler(self.search_fillers, "search_filler", w.get("trace"))

    def _try_search_answer(self, now):
        w = self._search_wait
        if w is None or w["sent"]:
            return
        self._try_search_filler(now)
        if not w["done"]:
            if now <= w["deadline"]:
                return
            w["done"], w["result"] = True, None        # too slow: said as not found
            self.stats["search_timeout"] += 1
        if self._ws is None or not self._ready.is_set() or self._resp is not None or self.output_busy():
            return                                     # step() tries again
        w["sent"], self._search_question, self._next_kind = True, w["question"], "search_answer"
        self._search_wait = None
        self.stats["search_answers"] += 1
        log(f"realtime: session {self.session_no} answering the search question "
            f"({'found' if w['result'] else 'not found'})", flush=True)
        prompt = w.get("prompt") or search_answer_text(w["result"], w["question"])
        if w.get("task") is not None:
            w["task"]["answered"] = True
        self._next_trace, self._next_prompt = w.get("trace"), prompt
        self._send_soon({"type": "conversation.item.create", "item": {"type": "message", "role": "user",
                                                                      "content": [{"type": "input_text",
                                                                                   "text": prompt}]}})
        self._send_soon({"type": "response.create"})

    def _try_look_answer(self, now):
        w = self._look_wait
        if w is None or w["sent"]:
            return
        self._try_filler(now)
        if w["look"] is None:
            if now > w["deadline"]:
                self._look_wait = None
                self.stats["look_timeout"] += 1
                log("realtime: no fresh look in time: hello stays quiet", flush=True)
                self._stage("skip")
            return
        if self._ws is None or not self._ready.is_set() or self._resp is not None or self.output_busy():
            return                                     # step() tries again (the deadline only guards the look itself)
        w["sent"], self._look_question, self._next_kind = True, w["question"], "look_answer"
        self._look_wait = None
        self.stats["look_answers"] += 1
        log(f"realtime: session {self.session_no} answering the look question from a fresh look", flush=True)
        prompt = look_answer_text(w["look"], w["question"])
        self._next_trace, self._next_prompt = w.get("trace"), prompt
        self._send_soon({"type": "conversation.item.create", "item": {"type": "message", "role": "user",
                                                                      "content": [{"type": "input_text",
                                                                                   "text": prompt}]}})
        self._send_soon({"type": "response.create"})

    def _push_looks(self, now):
        if not self._looks_dirty or self._ws is None or not self._ready.is_set():
            return
        if self._looks_sent_at is not None and now - self._looks_sent_at < LOOKS_PUSH_S:
            return                                     # step() sends it once LOOKS_PUSH_S have passed
        self._send_instructions("looks")

    def seen_for_look(self):
        """The live M12 line for the burst's recognition prompt (names as hello calls them), or None."""
        return self._seen() if self.ctx else None

    def _push_seen(self, now):
        """Re-send the instructions when what hello sees changed (≤ once per seen_min_s), so 'can you see me' is
        answered from the live M12 view, not the 60 s context."""
        if self._ws is None or not self._ready.is_set() or now - (self._seen_at or 0) < self.cfg.seen_min_s:
            return
        if self._seen() != self._seen_sent:
            self._send_instructions("seen")

    @_run_log
    def _log_search(self, rid, turn, usage):
        """call.realtime.search when the response searched the web (usage count, else a search event)."""
        n = ear_log.search_count(usage)
        events = (turn or {}).get("search_events", 0)
        if not n and events:
            n = 1
        if not n:
            return
        ear_log.emit("call.realtime.search", trace=(turn or {}).get("trace"), model=self.cfg.model, key=self.key,
                     usage={"count": n}, cost=ear_log.search_cost(n), sum=f"联网搜索 {n} 次",
                     ref={"rid": rid, "session": (turn or {}).get("session")},
                     extra={"source": "usage" if ear_log.search_count(usage) else "events", "events": events})

    def _vision_from(self, text, now, trace, look, key):
        """A look question starts the look wait; a burst word starts / stops the burst (once for both sources)."""
        if look:
            self._start_look_wait(text, now, trace)
        if self.burst is not None and key and not (self._cmd_last.get(key, -1e9) > now - 4):
            self._cmd_last[key] = now
            try:
                with ear_log.scope(trace=trace):        # the burst (and its looks) carry this sentence's trace
                    self.burst.heard(text)
            except Exception:                          # noqa: BLE001 - a burst never costs the session
                log(f"realtime: burst trigger failed\n{traceback.format_exc().rstrip()}", flush=True)

    def _release_vision(self, now, accepted, why=None):
        """The realtime model's verdict on the sentence a pending burst / look / search waits for."""
        q, self._pending_search = self._pending_search, None
        if q is not None:
            if accepted and now - q["at"] <= VISION_GATE_S:
                self._task_open(q["text"], now, q["trace"])
                self._start_search_wait(q["text"], now, q["trace"], task=self._qtask)
            else:
                self.stats["search_gated"] += 1
                ear_log.emit("gate.realtime", trace=q["trace"] or ear_log.new_trace(),
                             sum=f"没去搜：{why or ('千问判不是跟我说' if accepted is False else '没等到千问判定')} {q['text'][:30]}",
                             extra={"verdict": "search_gated", "heard": q["text"][:300]})
        p, self._pending_vision = self._pending_vision, None
        if p is None:
            return
        if accepted and now - p["at"] <= VISION_GATE_S:
            self._vision_from(p["text"], now, p["trace"], is_look_ask(p["text"]), self._burst_key(p["text"]))
            return
        why = why or ("千问判不是跟我说" if accepted is False else "没等到千问判定")
        self.stats["burst_gated"] += 1
        ear_log.emit("gate.realtime", trace=p["trace"] or ear_log.new_trace(), sum=f"视觉爆发没开：{why} {p['text'][:30]}",
                     extra={"verdict": "burst_gated", "reason": why, "heard": p["text"][:300]})

    @staticmethod
    def _burst_key(text):
        import vision_burst                             # its words (it never imports this module)
        stop, start = vision_burst.STOP_WORDS, vision_burst.START_WORDS
        if any(w in text for w in stop):
            return "burst_stop"
        if any(w in text for w in start):
            return "burst_start"
        return None
