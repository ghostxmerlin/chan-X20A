# Head pat, body light, three big modes: Implementation Plan

> **For agentic workers:** executed inline (superpowers:executing-plans). Steps are test-first.

**Goal:** with the hat off, the StackChan's head touch (Si12T, 3 zones) and its 12 body RGB LEDs become usable; pat
= screen tap, two pats = double tap, a pat burst = a protest like being shaken; shake / pat protests speak whatever
点屏幕聊天 says; the LEDs are a tool for 鹿野 and testable from the page; then the page's top becomes the three big
modes 关 / 收集 / 智能 with the small switches folded away.

**Spec (owner 2026-11-20, chat):** pat once = single tap, twice = double tap, continuous = protest (like shaking);
shake / head events are not affected by the chat switch; LEDs: use them, connect to the server, a tool, switches on
the deck. Big modes: 关 (nothing uploads, nothing paid), 收集 (uploads + free processing, she listens only), 智能
(everything paid on); small switches temporary under 详细设置, a restart goes back to the big mode; the big mode is
stored and survives restarts; the Docking switch, the DOCK bar, billing and presets 0-9 go.

## Global constraints
- Firmware collector-0.5.21; flash / deploy only after the owner's go ("做" covers this round); stop the 8787
  monitor (it holds the USB serial port) before flashing.
- Pat detection: a pat = touch released within 700 ms of pressing; a burst ends 450 ms after its last pat; burst of
  1 = tap, 2-3 = double tap; the 4th pat within 3 s of the burst's first = protest at once, the rest of the burst
  ignored. Source word for pats: "head".
- Protest and shake share the ear's 60 s cooldown (SHAKEN_SAY_S); both speak in 点屏幕聊天 too; 安静 / 闭嘴 still
  hold them; speaking needs the paid realtime session (智能 only).
- LEDs: 12 RGB on the base IO expander pin 13; robot cmd verb "light" {r, g, b} (0..255; 0,0,0 = off); page test
  via /v1/control target "light". Mind switch "light" (default on) gates 鹿野's set_light tool.
- Head pat enable: robot-side setting (NVS), /v1/control target "head_pat" {enabled}, default on.

## Tasks
1. Firmware: pure `head_pat.h` detector + `tests/head_pat_test.cpp` / `tests/test_head_pat.py`.
2. Firmware: sensor task feeds it; tap / double tap through the mode rules as source "head"; protest as body_ev
   "patted"; enable switch (NVS + control target + telemetry). Firmware "head" wire source.
3. Firmware: LEDs initialised in collector mode; robot cmd "light"; control target "light"; telemetry.
4. Server: mind / ear accept source "head" (owner input); body_event "patted" (own text) and both events bypass
   点屏幕聊天 (a pending body line opens the session); ear tool set_light -> robot "light"; mind registry + switch.
5. Monitor: head pat switch, light switch, light test colours (under the switch bar for now).
6. Big modes: mind stores big_mode (off / collect / smart) and applies it (server half) on change and at start;
   the page's top shows the three; Docking switch / DOCK bar / billing / presets 0-9 removed; small switches folded.
7. Build 0.5.21, flash, deploy ear + mind + Mac stack; owner tests.
