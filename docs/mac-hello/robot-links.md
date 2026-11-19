# hello robot ↔ servers: every link (for a Mac stand-in)

Read-only inventory, 2026-11-19, from the source in this repo (firmware head `9571b4f`, collector 0.5.18 era; the
server code as checked in). Every claim cites `file:line`. Anything not confirmed in code is marked **UNVERIFIED**.
No secret values are copied here, only where they live.

Path abbreviations:

| short | path |
| --- | --- |
| `FW/` | `upstream/StackChan/firmware/main/collector/` |
| `EAR/` | `server/ear/` |
| `MIND/` | `server/mind/` |
| `MON/` | `web/monitor/` |
| `TTS/` | `../voice/tts_service/soul_tts/` (soul-tts, outside this repo) |

## 0. Topology at a glance

```
 robot (ESP32-S3)                          server (one host)                         Mac
 ───────────────                           ───────────────────────────────           ───────────────
 hearing  ws  ──── ws(s)://<ear>/<any>  ──▶ ear_server.py (one port, default 8765)
 vision   ws  ──── ws(s)://<ear>/vision ──▶   ├─ /vision  → vision_link / body_sense / presence / 围观 hub
 mouth    ws  ──── ws(s)://<ear>/mouth  ◀─▶   ├─ /mouth   → mouth_link ─┬─ soul-tts ws://127.0.0.1:52751/mouth
                                              │                         └─ mind  http://127.0.0.1:52740 (/mode/*, /chat/*, /place)
                                              ├─ /signal  (DualKey, not the robot)
                                              └─ ask api 127.0.0.1:52743 (mind → ear → robot cmd)
 USB Serial/JTAG  ◀───────────────────────────────────────────────────────────────  web/monitor (127.0.0.1:8787)
 (HTTP 8080/8081 + UDP 8082: compiled out in the shipped build)                     ↔ mind via /api/mind/*
 ESP-NOW remote (official joystick), Unit ASR UART, M12 UART: local hardware, no server link
```

The robot opens **three WebSockets** to the ear (hearing, vision, mouth), all derived from one configured URL and
one token. Nothing on the server ever dials the robot. The local monitor reaches the robot over **USB serial**
(or, in the legacy/demo shape, HTTP). The robot never talks to mind directly: the ear relays.

## 1. How the robot learns the address and token

- One NVS key: namespace `collector`, key `ear_config` = JSON `{"url":"ws(s)://...","token":"..."}`
  (`FW/hearing.cpp:374-386`). URL rules: `ws://` or `wss://`, ≤ 256 chars, no `@ ? # \`, host:port
  (`FW/hearing_policy.h:13-34`); token 1..192 printable ASCII (`FW/hearing_policy.h:35-39`).
- Written only by the control `{"target":"hearing","op":"config","url":..,"token":..}` (empty token keeps the old one)
  (`FW/hearing.cpp:508-517`). The monitor sends it as action `hearing_import` (reads `SOUL_EAR_URL`, `SOUL_EAR_TOKEN`
  from repo-root `.env`, `MON/hearing.mjs:11-14`, `MON/server.mjs:32`) or `hearing_config` (typed in the page);
  the monitor requires an IPv4 host (`MON/hearing.mjs:3-10`).
- Vision and mouth reuse it: `hearing::backend(url, token)` (`FW/hearing.cpp:373`); path replaced by `/vision`
  (`FW/vision_uplink.h:27-33`) or `/mouth` (`FW/mouth_stream.h:166-172`). The hearing link uses the configured path
  as is; the ear routes every path other than `/mouth`, `/signal`, `/vision` to hearing (`EAR/ear_server.py:72-76`).
- The mouth task re-reads the backend every 2 s and reconnects if URL or token changed (`FW/mouth_link.cpp:342-346`).
- There is no discovery, no `kws.url`-style file on the robot side. (`kws.url` is the **ear's** file for its own
  keyword spotter, `EAR/ear_server.py:352-358`, `EAR/kws_link.py:27-30`; the robot is not involved.)

Server side of the address/token:

- Ear listens on `--host` (default `127.0.0.1`) / `--port` (default `8765`) (`EAR/ear_server.py:180-181`); the deployed
  unit uses `--host 0.0.0.0 --port <port>` (`EAR/README.md:84`; the real port is in the systemd unit, **UNVERIFIED** here).
- Token: `--token-file` or env `SOUL_TOKEN_FILE` (`EAR/ear_server.py:193`, read at `:214`); the file is `token` in the
  deploy dir, created by `EAR/remote_setup.sh:28`. Empty token = no auth (`EAR/hearing_protocol.py:17-19`).
- WebSocket `max_size=65536` per message (`EAR/ear_server.py:476`).

## 2. Auth (all three robot links)

- Robot sends HTTP upgrade header `Authorization: Bearer <token>` (`FW/hearing.cpp:229-230`,
  `FW/vision_uplink.cpp:200-201`, `FW/mouth_link.cpp:286-287`; set via `esp_transport_ws_set_auth`,
  `FW/hearing_socket.cpp:64`).
- Ear accepts the header, or `?token=` in the query when no header (`EAR/hearing_protocol.py:17-22`); compare with
  `hmac.compare_digest`. Failure: close code `4401 "bad token"` (`EAR/ear_server.py:393-396`).
- Connection caps: hearing ≤ 4, vision ≤ 4, mouth ≤ 2, signal ≤ 4; over the cap close `1013 "server busy"`
  (`EAR/ear_server.py:398-401, 420-423, 439-442, 451-454`).
- Behind the ear, mouth_link dials soul-tts with the **same** ear token as Bearer (`EAR/mouth_link.py:777`) and mind
  with the same token (`EAR/mouth_link.py:198-221`, `EAR/ear_server.py:299`). soul-tts wants its own device token
  file in stream mode (`../voice/docs/tts-streaming.zh-CN.md:76`) — **UNVERIFIED** how the deployed soul-tts is set;
  irrelevant to a stand-in, which only talks to the ear.

Robot transport details a stand-in may mirror (not required by the server): every client frame is one WS frame,
header+payload in one write, masked (`FW/hearing_frame_write.h:9-31`); text ≤ 8 KiB, binary ≤ 32 KiB
(`FW/hearing_socket.cpp:73`); hearing reads text only, ≤ 8 KiB (`FW/hearing_transport_policy.h:25-45`); /mouth also
takes binary ≤ 1600 B (`FW/mouth_stream.h:19`, `FW/mouth_link.cpp:288`).

---

## 3. Hearing uplink (`ws(s)://<ear>/<configured path>`)

Robot: `FW/hearing.cpp` worker `:216-364`. Server: `EAR/ear_server.py:79-132` (`serve_hearing`) →
`EAR/ear_hearing.py` class `Ear` (`:223`).

When it runs: only while listening is requested (`FW/hearing.cpp:95, 222`). Power-on runs "senses 9" which calls
`hearing::set_listening(true)` (`FW/collector.cpp:2339-2346`, queued at boot `:2172`). Reconnect backoff
1,2,4,…,30 s (`FW/hearing.cpp:359-362`).

### 3.1 Handshake

1. Robot → text, first message (`FW/hearing.cpp:245-250`):

```json
{"type":"audio","format":"opus","sample_rate":24000,"channels":1,"frame_ms":60,"framing":"sea1",
 "source":"stackchan-<device_id>",
 "device":{"device_id":"<12 hex mac>","boot_id":"<16 hex>","firmware":"collector-x.y.z",
           "protocol":"soul-ear/1","channel_roles":["mic"]}}
```

2. Ear validates (`EAR/hearing_protocol.py:25-34`, `EAR/ear_hearing.py:836-868`). Accepted formats:
   - `format:"pcm16"`, `sample_rate:16000`, `framing:"raw"` (the default when `type:"audio"` is never sent: PCM16 LE
     16 kHz mono, any chunk size; `EAR/ear_hearing.py:232`, header comment `EAR/ear_server.py:3-7`);
   - `format:"opus"`, `sample_rate` 16000 or 24000, `framing` `raw` (one Opus packet per WS message) or `sea1`
     (requires `frame_ms:60`).
   - `channels` must be 1. The format cannot change once audio has arrived (`EAR/ear_hearing.py:837-838`).
   - **`source` starting with `stackchan` switches on the robot profile** (`EAR/ear_hearing.py:846`): GTCRN denoiser,
     device sound gates, 听悟, the 围观/Docking realtime feed, KWS feed, presence, chat relay, run-log robot link. A
     stand-in must use `"source":"stackchan-<something>"` or none of mind/realtime sees its audio.
   - `device` dict is kept (boot_id for the run log, Mongo session) (`EAR/ear_hearing.py:847`, `:859-863`).
3. Ear → on the next tick, event `{"type":"audio","format","sample_rate":16000,"input_sample_rate":<declared>,
   "framing","frame_ms","sound_profile":"device"|"default","denoise":"gtcrn"|null}` (`EAR/ear_hearing.py:865-868`).
   The robot requires `format=="opus" && framing=="sea1" && input_sample_rate==24000`, else error
   `backend_protocol_mismatch` (`FW/hearing.cpp:154-156`), and gives up after 5 s without it (`FW/hearing.cpp:252, 273`).
   Also posted at connect: `{"type":"hello","sr":16000,"tick_ms":100,"speakers":[..≤64],"speaker_hi","speaker_lo",
   "sound_min_db","voice_hi","voice_lo","models":{..}}` (`EAR/ear_hearing.py:292-298`).

### 3.2 Audio frames (robot → ear, binary)

Robot side (`FW/hearing.cpp:236-243, 301-334`):

- Mic: 24 kHz capture, 20 ms blocks of 480 frames, stereo interleaved; channel 0 is taken (`FW/hearing.cpp:33, 407`).
- Opus encoder: 24 kHz, mono, 16-bit, 40 kbps VBR, 60 ms frames (2880 samples), complexity 0, application AUDIO
  (`FW/hearing.cpp:236-243`). Three 20 ms blocks → one packet.
- SEA1 packet = 16-byte header + one Opus packet (`FW/hearing.cpp:326-329`):

| offset | type | field |
| --- | --- | --- |
| 0 | 4 bytes | `"SEA1"` |
| 4 | u32 LE | wire seq: starts at 0 per connection, +1 per packet, **also +1 for every dropped block run** so the ear sees the gap (`FW/hearing.cpp:304-305`) |
| 8 | u64 LE | capture time of the packet's first sample, µs, robot `esp_timer` clock |
| 16 | … | Opus packet (≤ 4096 − 16 bytes total) |

Ear side (`EAR/hearing_protocol.py:37-45`, `EAR/ear_hearing.py:498-530`): first seq must be 0; a seq jump > 1000 or a
non-increasing timestamp is "out of order" (error event, packet dropped); any gap or a timestamp step outside
60 ms ± 30 ms resets the current utterance and posts `{"type":"audio_gap","missing_packets","seq","capture_us"}`.
Opus is decoded to **16 kHz** whatever was declared (`EAR/ear_hearing.py:41, 842`); max decoded frame 120 ms (`:77`).

**Can the server take raw PCM?** Yes: `format:"pcm16"`, 16 kHz only, `framing:"raw"`, binary messages of PCM16 LE
mono, any even length ≤ 65536 bytes (`EAR/hearing_protocol.py:30, 38-41`, odd length dropped `EAR/ear_hearing.py:531-534`).
This works with a `stackchan-*` source too (the profile is decided by `source`, not by codec). The existing Mac test
client already does this (`EAR/mic_client.py`, `EAR/README.md:63-73`). Note: a robot-firmware clone would reject the
resulting ack (§3.1 step 3); that check is the robot's, not the server's.

### 3.3 Control messages robot → ear (text)

All handled in `EAR/ear_hearing.py:795-886`; anything else → error event `unknown control type`.

| message | sent by robot | ear handling |
| --- | --- | --- |
| `{"type":"self","sounding":bool,"moving":false,"t_us":<µs>}` | once per session, then on change, at a packet boundary before that packet (`FW/hearing.cpp:317-320`, gate `FW/hearing_transport_policy.h:20-24`) | `sounding`/`moving` must be bool; applied to **all following audio** (`t_us` is ignored) (`EAR/ear_hearing.py:878-882`). While sounding, VAD/ASR/tags/听悟/KWS get silence (`EAR/ear_hearing.py:541, 545, 552, 579-584`); realtime is told `sounding` and sends nothing up (`:566-576`). |
| `{"type":"enroll","name","request_id","count":5,"replace":bool}` | when a voiceprint enrollment session is started (`FW/hearing.cpp:261-263, 272`) | name `[\w\-㐀-鿿 ]{1,48}`, count 5, 120 s deadline (`EAR/hearing_protocol.py:67-83`); events below |
| `{"type":"enroll_cancel"}` | **not sent by firmware** (robot just ends the session) | cancels |
| `{"type":"forget","name"}` | on a pending voiceprint delete (`FW/hearing.cpp:322-325`) | answered by event `speakers` (`EAR/ear_hearing.py:818-831`) |
| `{"type":"ping","id"}` | not sent by firmware | event `pong` |
| `{"type":"config",...thresholds}` | not sent by firmware | event `config` |
| `{"type":"vision",...}` | 0.4.73 only; now on /vision | stored only |

**What `sounding` means on the robot** (`FW/hearing.cpp:406`): `playback_self_sounding()` OR within 1.2 s after a
listening session (re)starts (`quiet_until`, `FW/hearing.cpp:101`). `playback_self_sounding()` (`FW/playback.cpp:701-706`):
during a /mouth stream, until 450 ms after the last frame was written (`FW/playback.cpp:59, 397-400`); otherwise an SD
clip playing or its 1.2 s tail (`FW/playback.cpp:264`). `moving` is always `false` (servo motion is not reported here;
the body sense has its own `self` flag, §4.4).

### 3.4 Heartbeat downlink (ear → robot, text, exactly one per 100 ms)

`EAR/ear_hearing.py:888-936`:

```json
{"v":1,"type":"tick","seq":n,"t":<server epoch ms>,"rx":<16 kHz samples received>,"rx_idle_ms":..,"rx_gap_ms":..,
 "rx_bytes":..,"level_db":..,"noise_db":..,"heard_noise_db":..,"speaking":bool,"events":[...]}
```

Events ride on ticks (≤ 7000 B of events per tick, an event > 6000 B is dropped, outbox ≤ 24) (`EAR/ear_hearing.py:301-311, 908-923`).
Each event gets `debug.tick_wait_ms`. The robot ends the session after 3 s without a tick (`FW/hearing.cpp:63-64, 288`).

Event types the ear posts on hearing (and what the robot does with them, `FW/hearing.cpp:127-213`):

| event | fields (main) | robot use |
| --- | --- | --- |
| `hello` | see §3.1 | speakers list |
| `audio` | see §3.1 | handshake ack |
| `speech` phase `start` | `id "s-N"`, `pos [onset,null]` (16 kHz samples), `t`, `speaker{band,name,score}`, `voice`, `self` (`EAR/ear_hearing.py:654-656`) | ignored |
| `speech` phase `end` | `id`, `pos [start,end]`, `t`, `dur`, `speaker`, `text` (≤ 512), `lang`, `emotion`, `emotion_reliable:false`, `event`, `voice{p,band,acoustic_band,loud_db,rate}`, `tags{speech_p,kinds}`, `self{sounding,moving}`, `respond` (bool: 嘴 mode and realtime idle), `debug{…server_ms}` (`EAR/ear_hearing.py:678-692`) | picks an SD voice clip / eyes (`FW/hearing.cpp:185-211`); needs `rx` on the tick, `debug.server_ms`, `debug.tick_wait_ms` |
| `sound` phase `start`/`end` | `id "a-N"`, `kind`, `label` (zh), `pos`, `p`, `raw`, `self` (`EAR/ear_hearing.py:772-794`) | log |
| `enroll` phase `started`/`progress`/`done`/`error`/`cancelled` | `name`, `request_id`, `n`, `count:5`, `rejected?`, `speakers` on done (`EAR/ear_hearing.py:807-813, 715-731`) | enrollment UI |
| `speakers` op `forget` | `name`, `ok`, `error?`, `speakers` | voiceprint list |
| `tingwu` phase `sentence`/`error` | `n`, `text`, `pos`, `self` (`EAR/ear_hearing.py:406-407, 427`) | log |
| `keyword` | `id "k-<start>"`, `pos`, `t`, `keyword`, `label`, `score`, `hit`, `near`, `sample` (`EAR/kws_link.py:113-121`) | ignored (unknown type) |
| `audio_gap`, `error`, `overload`, `pong`, `config` | | log / error |

---

## 4. Vision uplink (`ws(s)://<ear>/vision`)

Robot: `FW/vision_uplink.cpp`. Server: `EAR/ear_server.py:150-175` (`serve_vision`) → `EAR/vision_link.py`
(`VisionLink.on_text :178-233`, `on_binary :235-288`, `serve_link :296-314`).

When it runs: whenever a backend is configured (`wanted()` is always true since 0.5.12, `FW/vision_uplink.cpp:91`),
independent of hearing. Backoff 2..30 s (`FW/vision_uplink.cpp:203-208, 315-320`). The server answers **only errors**
(`{"type":"error","message":..}`) and closes a link silent for 60 s (`EAR/vision_link.py:37, 299-305`); the robot sends
`{"type":"ping"}` after 15 s without traffic (`FW/vision_uplink.cpp:25, 298-301`), but body summaries go every second anyway.

### 4.1 Hello (first message; anything before it gets `"hello first"`)

```json
{"type":"hello","source":"stackchan-<device_id>",
 "device":{"device_id":"..","boot_id":"..","firmware":"..","protocol":"soul-vision/1"}}
```
(`FW/vision_uplink.cpp:212-216`). Opens a Mongo session `YYYYMMDD/HHMMSS-<source>-vN` (`EAR/vision_link.py:187-196, 158-160`).

### 4.2 M12 change events (text)

Built in `FW/vision_uplink.cpp:65-87`; kinds `FW/m12_protocol.h:774-784`; change tracker `:801-...` (a track is lost after 1.5 s unseen).

Common fields: `type:"vision"`, `event`, `seq` (robot vision-event seq), `t_us` (when it happened, robot µs),
`now_us` (send time, robot µs), `f` (m12d frame number, omitted when 0).

| event | extra fields |
| --- | --- |
| `face_found`, `face_named`, `face_attrs`, `face_unknown`, `face_lost` | `face` (m12d track id; changes after the face is gone > 1 s), `box [x,y,w,h]` in **640×480** M12 pixels + `prob`, `sim` (who similarity, face_unknown), `gender` `"m"`/`"f"`/`"?"`, `age`, `age_group` `child|young|adult|elder`, `name` (UTF-8 ≤ 32 B), `reason` (lost: `timeout`, `vision_off`, `evicted`) |
| `person_found`, `person_lost` | `person` id, `face` (paired face id), `box`, `prob`, `reason` |
| `vision_on`, `vision_off`, `m12_online`, `m12_offline` | none (state; the ear logs robot.state, `EAR/vision_link.py:80-81, 206-207`) |
| `m12_ev` | `ev` = m12d event type `wake cover uncover light dark bright enter leave near far look look_end nod shake tilt smile` (`FW/m12_protocol.h:112-122`), plus only what the line had: `face`, `person`, `n`, `side "l"/"r"`, `on`, `dur` (s), `d [before,after]` (m), `lum [before,after]`, `area [x,y,w,h]`, `amt`, `cd`, `pan`, `hot`, `faces [{box,name?,sim?}]`, `persons [[x,y,w,h]]`, `faces_seen`, `persons_seen` (`FW/m12_protocol.h:321-355`, doc `EAR/vision_link.py:7-16`) |

Server: event name required, ≤ 32 chars; stored in `hello.vision`; may add a synthetic `face_carried`
(`EAR/vision_link.py:88-147`); every event goes to `presence.vision()` and `rt_hub.on_vision()` (`EAR/ear_server.py:158-162`).
Faces drive the owner greeting in Docking (`EAR/rt_events.py:9-21`). Events are sent oldest first, at most 8 per pass,
committed only after the socket took them (`FW/vision_uplink.cpp:231-242`).

### 4.3 Camera frames (binary)

`FW/vision_uplink.cpp:153-180`, server `EAR/vision_link.py:235-288`:

```
"SVF1" | u16 LE header length (≤ 512) | JSON header | JPEG (starts FF D8)        whole message ≤ 32768 bytes
header: {"type":"frame","seq":<camera seq>,"t_us":<capture µs>,"now_us":<send µs>,"w":..,"h":..,"diff":<cells>,
         "bytes":<jpeg len>,"snap":<id>?}
```

Rules on the robot: only while the body camera is on (`camera_enabled`, `FW/collector.cpp:2149`); first frame after
connect, then at most 1/s and only when ≥ 6 of 192 luma cells changed (`FW/frame_change.h:16-20, 76-79`); never while
M12 events are queued or a look runs (`FW/vision_uplink.cpp:246-250`); after a failed send, a 60 s hold
(`FW/frame_change.h:90`). Camera target 10 fps (`FW/collector.cpp:102-103`). Frame resolution: from the GC0308 driver
(`FW/collector.cpp:588-589`), **UNVERIFIED** (likely 320×240).
Server: `seq`, `t_us`, `now_us` must be non-negative ints; saved to `<recordings>/frames/<session>/<t_us>-<seq>.jpg`,
row in `hello.frames`, then `on_frame` hooks (视觉爆发 snap, live eye) (`EAR/ear_server.py:135-147`).

Snap (视觉爆发): ear → robot on **/mouth** `{"type":"snap","id":N}` (`EAR/vision_burst.py:343`); robot sends the next
fresh frame on /vision with `"snap":N` in the header, bypassing the change rule, then answers on /mouth
`{"type":"snap_result","id":N,"ok":true,"seq","bytes","ms"}` or `{"type":"snap_result","id":N,"ok":false,
"why":"camera_off|no_uplink|busy|timeout"}`; one in flight, 2 s deadline (`FW/vision_snap.h:3-60`).

### 4.4 Body sense (text, since 0.5.12)

`FW/vision_uplink.cpp:97-148`, `FW/body_sense.h:3-21`; server `EAR/body_sense.py` (`on_link :60`, doc `:1-20`).

- Summary once per 1 s window: `{"type":"body","seq","t_us","now_us","n":<IMU samples>,"pitch","roll" (deg, gravity in
  the head frame),"acc_g","dev_g","gyro" (dps),"moved" (0..1),"still","held","self" (own servos moved),"prox","prox_base",
  "prox_max","near" (LTR553 0..2047),"lux","mag":[x,y,z] (µT)}`; IMU-dependent fields only when the IMU had samples.
- When not still: `"raw_t0_us"`, `"raw":[[dt_ms, ax, ay, az (mg), gx, gy, gz (0.1 dps), self], ...]` ≤ 64 rows, `"raw_over"`.
- Events: `{"type":"body_ev","ev":"picked_up|put_down|shaken|near|far","seq","t_us","now_us","held_ms"?,"prox"?,
  "peak_g"?,"peak_dps"?}` (≤ 4 per pass).
- Server stores in `hello.body` (events always, summaries every 10 s or on flag change) and writes raw rows to
  `<recordings>/body/<session>.jsonl` (`EAR/body_sense.py:63-90`); Docking reads a 身体感觉 line.

---

## 5. Mouth link (`ws(s)://<ear>/mouth`, both directions)

Robot: `FW/mouth_link.cpp` (run `:276-366`), `FW/mouth_modes.h`, `FW/mouth_stream.h`. Server: `EAR/ear_server.py:397-418`
→ `EAR/mouth_link.py` `MouthLink` (`:517`), `ModeRelay` (`:238`), `RobotLinks` (`:128`).

When it runs: whenever a backend is configured; backoff 2..30 s (`FW/mouth_link.cpp:276-366`).

### 5.1 Handshake

1. Robot → `{"type":"hello","device_id","boot_id","codecs":["opus"],"sample_rate":24000,"buffer_ms":10080,
   "fw":"..","cmd":1}` (`cmd` only when the command worker is ready) (`FW/mouth_link.cpp:293-299`; `buffer_ms` =
   168 slots × 60 ms, `:25`). The ear waits ≤ 10 s for it (else close `4408`), non-hello first → `4400`
   (`EAR/mouth_link.py:642-666`). Without `boot_id` the ear runs no modes for this link (`EAR/mouth_link.py:598-604`).
   `cmd == 1` enables robot commands (`EAR/mouth_link.py:657`).
2. Ear → `{"type":"hello","codec":"opus","frame_ms":60,"sample_rate":24000}` (fixed, `EAR/mouth_link.py:98, 660`). Robot
   checks those three; no answer in 5 s = drop (`FW/mouth_link.cpp:27, 147-149, 301-307`).
3. On the ear's hello the robot sends its current mode first (§6.2) (`FW/mouth_link.cpp:167-170`).
4. The ear dials soul-tts `ws://127.0.0.1:52751/mouth` and forwards the robot's hello **verbatim**; it drops soul-tts's
   own hello (`EAR/mouth_link.py:97, 774-797`). soul-tts picks the codec from `codecs` (`TTS/mouth.py:140-141`) — a
   stand-in should send `["opus"]` only, because the ear always tells the robot `opus`.

Liveness: the ear's WebSocket server pings (websockets default, 20 s; **UNVERIFIED** beyond the firmware comment
`FW/mouth_liveness.h:5-8`); the robot drops the link after 45 s without any frame (`FW/mouth_liveness.h:9-15`).

### 5.2 TTS downlink (ear → robot)

Same protocol for soul-tts streams (ids from 1) and Docking realtime streams (ids from `0x40000000`,
`EAR/mouth_link.py:107`). Spec: `../voice/docs/tts-streaming.zh-CN.md:74-124`.

| message | fields |
| --- | --- |
| text `tts.start` | `stream` (u32), `codec:"opus"`, `sample_rate:24000`, `frame_ms:60`, `utterance` (realtime: `"rt<n>"`, `EAR/realtime_link.py:1129-1131`). A new start replaces a running stream (robot reports the old one `tts.stopped`) (`FW/mouth_stream.h:116-127`). Wrong codec/frame_ms → robot `tts.error unsupported_codec` (`FW/mouth_link.cpp:150-156`). |
| binary audio | `"SPK1"` \| u32 LE stream \| u32 LE seq \| u16 LE frame_ms (60) \| u16 LE flags (0) \| one Opus packet (24 kHz mono 60 ms; realtime 32 kbps) (`FW/mouth_stream.h:21-31`, `EAR/rt_base.py:52-56, 736-737`). Packet ≤ 768 B (`FW/mouth_link.h:22`); stale stream ids are dropped (`FW/mouth_stream.h:129-138`). |
| text `tts.end` | `stream`, `frames`, `duration_ms` (`EAR/realtime_link.py:1120-1121`) — no more packets |
| text `tts.stop` | `stream` — stop now, clear buffer (`FW/mouth_stream.h:140-141`); also sent by the ear for every live soul-tts stream when soul-tts drops (`EAR/mouth_link.py:759-772`) |

Player on the robot: prebuffer 180 ms, rebuffer 120 ms after an underrun, speaker held 1.5 s for the next sentence
(`FW/mouth_stream.h:14-18, 144-163`); ring 168 × 768 B (10 s) (`FW/mouth_link.cpp:25`).
Flow control on the server: sent − played ≤ 8 s (soul-tts) / `max_lead_ms` (realtime); 10 s without new `played_ms`
→ the server stops the stream (`../voice/docs/tts-streaming.zh-CN.md:120-124`, `EAR/realtime_link.py:1161-1169`).
The next stream starts only after `tts.done`/`tts.stopped`/`tts.error` of the previous one
(`../voice/docs/tts-streaming.zh-CN.md:114`, `EAR/realtime_link.py` `_await_previous`).

### 5.3 Robot reports (robot → ear, text)

`FW/mouth_link.cpp:173-188`:

| type | fields |
| --- | --- |
| `tts.playing` | `stream`, `buffered_ms` (prebuffer full, sound starts) |
| `tts.progress` | `stream`, `played_ms`, `buffered_ms`, `underruns` — every ≥ 250 ms while playing (`FW/mouth_stream.h:17`, `FW/playback.cpp:423-427`); spec asks ≤ 500 ms |
| `tts.done` | `stream`, `played_ms`, `underruns`, `underrun_ms` |
| `tts.stopped` | `stream`, `played_ms` |
| `tts.error` | `stream`, `error` |

Routing at the ear (`EAR/mouth_link.py:668-725`): stream ≥ `0x40000000` → realtime hub `on_report` (`EAR/realtime_link.py:380-397`);
others → soul-tts (dropped while soul-tts is away). `played_ms` must be a non-negative number.

### 5.4 Other robot → ear messages on /mouth

| type | when | fields | ear handler |
| --- | --- | --- | --- |
| `mode_report` | each mode change / effect / accepted input, and once after every handshake | §6.2 | `EAR/mouth_link.py:833-871` → mind `POST /mode/report` |
| `mode_input` | digits 0 / 9 (and 1) | §6.3 | `EAR/mouth_link.py:872-888` → mind `POST /mode/input` → answers `mode_result` |
| `asr_wake` | Unit ASR wake word (`hello`, `Hi M Five`, `hi A S R`) | `{"type":"asr_wake","word":"hello","n":<count since boot>,"interrupted":bool}` (`FW/mouth_link.cpp:229-239`); the robot already stopped its own speech (`:435-446`) | `rt_hub.on_wake` → `called("asr")` (`EAR/rt_interrupt.py:10-12`) |
| `asr_order` | every other recognised Unit ASR word (mode words too), except 0xFE | `{"type":"asr_order","word":"turn left","n":..}` (`FW/mouth_link.cpp:241-251`, `FW/asr_unit.cpp:182-183`) | `rt_hub.on_order` → Docking will not answer that sentence (`EAR/rt_interrupt.py:52-76`) |
| `snap_result` | §4.3 | | `burst.on_snap_result` (`EAR/mouth_link.py:674-680`) |
| `cmd_result` | answer to `cmd` | §5.6 | resolves the pending future (`EAR/mouth_link.py:573-576, 671-673`) |
| `place_select` | **not sent by this firmware** (no `place` handling in `FW/`) | `{seq, choice}` | mind `POST /place` (`EAR/mouth_link.py:321-344`) |
| `mode_applied` | pre-1b test firmware only | | dropped |

`wake` / `order` counts are sampled at link-up so stale ones are not sent (`FW/mouth_link.cpp:313-314`).

### 5.5 Ear → robot screen / UI messages on /mouth

| type | fields | robot effect |
| --- | --- | --- |
| `mode_result` | `seq` (the robot's mode_input seq), `code` (wire word), `toast` (ASCII ≤ 32) (`EAR/mouth_link.py:826-831`, parse `FW/mode_wire.h:155-169`) | toast; clears the owed power-on digit on `ok` (`FW/mouth_modes.h:266-280`) |
| `chat_stage` | `stage` `listen|think|skip|aside|busy|tool`, `at_ms` (server epoch, unused); ≤ 200 B (`FW/chat_stage.h:26-44`) | stage dots, only in chat / crowd; `tool` counts as speaking for touch rules up to 90 s (`FW/mouth_modes.h:250-253`). Sent by `ModeRelay.send_stage` to the link that reported the mode, only when the relayed mode is chat/crowd (`EAR/mouth_link.py:346-405`) |
| `burst` | `{"on":true,"left_s":0..600,"looks":n}` / `{"on":false}` (`FW/burst_indicator.h:4-12, 36-53`; `EAR/vision_burst.py:430-444`) | eye + count + time bar in the top row; auto-off after max(left_s+10 s, 15 s) |
| `snap` | `{"id":N}` | §4.3 |
| `cmd` | §5.6 | robot command |
| `place` | `{"current","by","choices","at_ms"}` (`EAR/mouth_link.py:171-186`) | **ignored** by this firmware (type `Other`, `FW/mouth_modes.h:46-62`) |
| `tts.*` + binary | §5.2 | speech |

Unknown types are ignored; a text > 1600 B or bad JSON counts as `bad_messages` (`FW/mouth_modes.h:47-54`).

### 5.6 Robot command line (`cmd`, collector 0.5.10+) — eyes, words, motion

Down: `{"type":"cmd","id":<u32 > 0>,"do":<verb>,"args":{..}}` ≤ 512 B. Up: `{"type":"cmd_result","id":N,"ok":bool,
"error":<code>|null,"data":{..}}` ≤ 640 B (`FW/robot_cmd.h:3-38`, `FW/robot_cmd.cpp:85-105`). The ear's `send_cmd` waits
3 s (`EAR/mouth_link.py:116, 547-571`), ≤ 8 pending; a link drop fails them `robot_offline`. Always the **newest**
/mouth link (`EAR/mouth_link.py:128-149`). Callers: mind's tools via the ear's ask API (`EAR/ask_api.py:1-9, 290-355`)
and Docking's screen word (`EAR/ear_server.py:361-370`). Rate limit: 2 per 10 s per seen/heard verb (`FW/robot_cmd.h:36-37, 191-205`).

| verb | args | ask_api name | robot action / `data` |
| --- | --- | --- | --- |
| `eyes` | `state` (eye id `idle happy hurt sad angry affectionate laughing goofy sarcastic helpless greeting grateful apologetic comforting encouraging determined refusing pleading relieved farewell surprised confused thinking expectant curious shy worried scared sleepy proud`, `FW/eyes/catalog.generated.h:14`; or an emotion label `happy sad angry fearful disgusted surprised`, `FW/eye_mode.h:32-42`) | `express_eyes` | plays once; `data.state` |
| `word` | `text` (≤ 32 B, every char in the compiled glyph set `FW/m12_words.h:137`; `""` clears), `hold_ms` 0..3600000 (default 2000; > 2 s sticks) | `show_word` | text in the toast row under the eyes; `data{text,hold_ms,stays}` |
| `clip` | `clip_id` 1..80 \| `emotion` \| none (random) | `play_clip` | SD voice clip; refused `speaking` while a stream plays |
| `yaw` | `to` `left90|left45|right45|right90` (robot's left = negative) | `turn_head` | absolute 45° slot; `data{action,facing,tracking}` |
| `front` | — | `face_front` / `turn_head front` | face front |
| `turn_around` | — | `turn_around` | turn around / back |
| `tracking` | `on` bool | `tracking` | face tracking on/off |
| `nod` / `shake` | `times` 1..2 (default 2) | `nod` / `shake_head` | ±8°, back to start; `data{gesture,times,seq,amp_deg}` |
| `volume` | `level` 10..100 \| `step` −30..30 | `set_volume` | RAM only |
| `face_learn` | `name` | `face_learn_propose` | M12 learn (4.5 s) |
| `status` | — | `robot_status` | `data{fw,up_s,mode,online,volume,yaw,pitch,facing,tracking,motor,moving,m12,speaking,playing,heap,psram,battery,charging,lux,chip_c,camera,rssi}` (`FW/robot_cmd.cpp:213-236`, `FW/collector.cpp:1799-1817`) |

Error codes: `bad_args unknown_verb no_glyph rate_limited queue_full busy speaking motor_off no_feedback facing_back
facing_unknown at_limit no_room m12_offline eyes_off no_clip failed` (`FW/robot_cmd.h:20-21`); the ear maps them for
mind (`EAR/ask_api.py:26-45`). A stand-in that has no motors can answer motion verbs `ok:true` (animate) or
`ok:false,"error":"motor_off"` (mind tells the user "舵机没开").

**What "screen_word" maps to:** `rt_hub.screen_word(text, hold_s)` → `robots.send_cmd("word", {"text": text,
"hold_ms": min(max(hold_s,0),3600)*1000}, 5.0)` (`EAR/ear_server.py:363-370`). Used by Docking for `在听` (tap,
`EAR/rt_interrupt.py:139`), the 安静中 / 少插话 / role / debug mode words (`EAR/rt_manner.py:9-22, 270`), and
`""` to clear. On the robot it is `screen_show_word_for` in the toast row (`FW/screen.h:111-119`).

Not on any server link: emotion/eyes after a heard sentence and SD reply clips are the robot's **own** reaction to
`speech end` events (`FW/hearing.cpp:192-211`).

---

## 6. Modes

### 6.1 Who owns it

hello (the robot) owns the mode; mind only mirrors it from reports and runs the effects (`FW/mouth_modes.h:12-38`,
`MIND/mode_store.py:411-480`). Rules are one pure function `FW/mode_rules.h:246-329`, the twin of
`MIND/modes.py` (transition) + `MIND/mode_table.py` (table), kept identical by the vectors
`tests/data/mode_vectors.json` (`FW/mode_rules.h:9-12`).

- Modes (wire ids): `talk chat settings event subtitle crowd companion idle` (`FW/mode_rules.h:19, 29`). `crowd` is
  shown as **DOCK / Docking**, the boot and main mode (`FW/mode_rules.h:74-76, 86-87, 251-255`).
- Labels/colors/eyes per mode: `FW/mode_rules.h:63-81`. Subtitle `dir` `zh_en|en_zh`.
- Inputs: `forward backward play pause open close start stop next previous digit tap double_tap long direction select boot timeout`
  (`FW/mode_rules.h:31-33`). Effects: `stop_speech talk_leave talk_enter say_round prefetch_on prefetch_off` (`:35`).
- Crowd touch (0.5.15): tap = `LISTEN` (must-answer window) or `STOP` + `stop_speech` while speaking; double tap =
  `QUIET` (+ `stop_speech` while speaking) (`FW/mode_rules.h:43-52, 273-278`). "Speaking" = a /mouth stream playing or
  a `tool` stage within 90 s (`FW/mouth_link.cpp:469-477`); a crowd tap/double tap with `stop_speech` also stops the
  stream locally (`FW/mouth_link.cpp:481-489`).
- Inputs come from: ASR words (`asr`), home-screen tap/double tap (`touch`), tile pick (`select`), quick keys
  (`quick`), the monitor page `/v1/control {target:"mode"}` (`page`), settings 5-min timeout (`timeout`)
  (`FW/collector.cpp:1523-1541, 1917-1927`, `FW/asr_unit.cpp:68-75`, `FW/mouth_modes.h:217-222`). DualKey `direction`
  inputs go to mind, not the robot.

### 6.2 `mode_report` (robot → ear → mind)

Line built in `FW/mouth_modes.h:458-481`:

```json
{"type":"mode_report","seq":<robot mode seq>,"mode":"crowd","dir":null|"zh_en"|"en_zh","host":null|"event",
 "return_to":null|"<mode>","by":"asr|touch|select|quick|page|timeout|boot","input":null|"<input id>","effects":["stop_speech",...]}
```

- Sent for a change, an input with effects, or any accepted input (crowd tap/double tap) (`FW/mouth_modes.h:191-210`).
- After every handshake: one sync report; the first after a real boot has `by:"boot"`, `input:"boot"`, else
  `input:null` and `by` = the last changer; its effects = the mode-change effects since the last report mind got
  (`FW/mouth_modes.h:225-237, 448-457`). Offline changes are not queued (the sync report replaces them).
- Ear: `ModeRelay.note_report` remembers `robot_mode` + which link (`EAR/mouth_link.py:262-268`); owner inputs →
  presence; crowd `tap`/`double_tap` → `rt_hub.touched(stop=...)` / `touch_quiet()` (`EAR/mouth_link.py:840-866`,
  `EAR/rt_interrupt.py:110-157`); then a worker POSTs mind `/mode/report` `{boot, conn, age_ms, seq, mode, dir, host,
  return_to, by, input, effects}` in order, retried every 2 s while mind is away (`EAR/mouth_link.py:890-921`).
- Mind: `MIND/api.py:991-1000` → `MIND/mode_store.py:411-480`: dedup on `('robot.report', boot, seq)`; older seq of the
  same boot = `stale`; `by` must be one of `asr touch select quick dualkey page boot timeout` (`MIND/modes.py:19`);
  state must be valid (`MIND/mode_store.py:93`); stop/paid effects of a report older than 3 s are dropped
  (`MIND/mode_store.py:53, 60`).
- Link close (after mind took ≥ 1 report): ear POSTs `/mode/robot {boot, conn, event:"closed"}` (`EAR/mouth_link.py:620-622`,
  `MIND/api.py:1001-1005`, `MIND/mode_store.py:482-491`).

### 6.3 `mode_input` (digits) and the power-on 9

- Digits 0 (all paid off) / 9 (all on) and 1 (围观 realtime toggle) are mind's: `{"type":"mode_input","seq":<input seq,
  1.. per boot>,"age_ms":<ms queued>,"source":"asr|touch|select|quick|page","input":"digit","value":<0|1|9>}`
  (`FW/mode_wire.h:198-212`, `FW/mouth_modes.h:149-182`). Digits 4–8 are local mode switches (`FW/mode_rules.h:260-270`).
- Power-on: the robot owes mind a `9` (source `quick`) right after the boot report and resends every 10 s up to 30 times
  until a `mode_result` with `code:"ok"` (`FW/mouth_link.cpp:405-416`, `FW/mouth_modes.h:126-134, 236, 310-313, 496-510`).
  The robot also runs its own half of 0/9 locally (hearing listen, camera, M12 detection) (`FW/mouth_link.cpp:490-492`,
  `FW/collector.cpp:2339-2356`).
- Ear → mind `POST /mode/input {device:"robot", boot, seq, age_ms (+queue time), source, input, value}`
  (`EAR/mouth_link.py:875-888`); mind dedups on (device, boot, seq), drops > 3 s old (`MIND/api.py:362-375`,
  `MIND/mode_store.py:53, 249-266`). Answer → `mode_result` (busy `BUSY`/`MIND SLOW`/`MIND OFFLINE`, 400 →
  `unknown_input`/`BAD INPUT`) (`EAR/mouth_link.py:223-235`).

### 6.4 ASR (Unit ASR voice module) words

- Hardware: CI-03T on UART 115200, frames `AA 55 <id> 55 AA` (value form `AA 55 <id> <v> 55 AA`) (`FW/asr_protocol.h:6-49`);
  word table `FW/asr_protocol.h:51-98`. Purely local; a stand-in can emulate words from its own keyboard/ASR.
- Per recognised word (`FW/asr_unit.cpp:177-191`):
  1. wake words `hello` 0x32, `Hi M Five` 0xFF, `hi A S R` 0x31 → `asr_wake` on /mouth (and stop speech);
  2. every other known word (except 0xFE) → `asr_order` on /mouth (so Docking skips that sentence);
  3. mode words → `mode_apply("asr", ...)`: `forward 0x07, backward 0x09, play 0x16, pause 0x17, open 0x10, close 0x11,
     start 0x12, stop 0x13, next 0x1B, previous 0x1A, zero..nine 0x20..0x29 → digit` (`FW/asr_mode_words.h:20-42`);
  4. motion words stay local (up/down tracking, left/right 45, turn left/right 90, front/back) (`FW/asr_mode_words.h:16-18`).
- The ASR event history is also in telemetry `asr` (monitor only).

---

## 7. Telemetry and control (monitor ↔ robot)

### 7.1 Transport

- Shipped build: **USB Serial/JTAG only**. `CONFIG_COLLECTOR_NETWORK_MONITOR` is not set (`upstream/StackChan/firmware/sdkconfig:600`,
  `main/Kconfig.projbuild:2-9`), so `start_server(8080)`, `start_server(8081)` and UDP discovery 8082 are compiled out
  (`FW/collector.cpp:2202-2209`). The serial monitor always runs (`FW/collector.cpp:2194-2201`).
- Monitor picks the client from `.private/stackchan-monitor.json`: `transport:"http"` → `DeviceClient`, else
  `SerialDeviceClient` (`MON/server.mjs:226-231`). Binding fields: `address` (http only, IPv4 origin), `device_id`,
  `token` (32 hex), `port` (monitor, default 8787), optional `usb_serial`, `serial_python`, `m12` (SSH to the M12, not
  the robot) (`MON/core.mjs:10-17`, `MON/serial-client.mjs:11-19`, `MON/README.md:66-95`).
- Robot pairing token: NVS `collector/token`, 32 hex, generated once (`FW/collector.cpp:264-281`). This is **not** the
  ear token. `device_id` = Wi-Fi MAC as 12 hex; `boot_id` = 8 random bytes hex, new every boot (`FW/collector.cpp:264-271`).

### 7.2 USB serial protocol (what a serial stand-in would have to speak)

Frames: `"SCM1"` | kind (1 = JSON, 2 = camera JPEG) | version 1 | 0 | 0 | u32 LE length (≤ 131072) | u32 LE CRC-32 |
payload (`FW/monitor_protocol.h:6-21`, `MON/serial-wire.mjs:2-5`). Bridge: `MON/serial_bridge.py` finds the tty by USB
serial number with VID 0x303A / PID 0x1001 (`:5-21`) — a virtual port would need `serial_python` pointing at a custom
bridge (**UNVERIFIED**, not tried).

Host → robot JSON `{id, op, token, boot_id, ...}` (`FW/serial_monitor.cpp:116-153`):

| op | notes |
| --- | --- |
| `hello` + `nonce` (32 hex) | public; reply `{protocol:1, device_id, nonce, proof, port:8080, boot_id, transport:"usb"}`; `proof` = hex HMAC-SHA256(key = pairing token, msg = `"chan-discovery-v1\n" + device_id + "\n" + nonce`) (`FW/collector.cpp:710-731`, `FW/connection_protocol.h:7-15`; checked `MON/serial-client.mjs:52-57`) |
| `info` | reply = `/v1/info` document (`FW/collector.cpp:894-1040`) |
| `subscribe` / `streams` {`camera`,`servo_diagnostics`} / `snapshot` / `ping` / `unsubscribe` | 6 s lease, the monitor pings every 2 s (`FW/monitor_protocol.h:22-27`, `MON/serial-client.mjs:64-76`) |
| `control` + `body` (≤ 768 B) | one at a time; reply = the control's status + body (`FW/serial_monitor.cpp:128-134, 166-172`) |

Errors: 401 bad token, 409 `robot restarted` (boot_id), 400 unknown op. Robot → host while subscribed
(`FW/serial_monitor.cpp:175-206`): `{"type":"state","boot_id","revision","base_revision","full",data}` every 1 s
(full every 30 s, else a delta, `FW/monitor_delta.h`); `{"type":"events","boot_id","timestamp_us","asr":{events},
"hearing":{events}}` every 50 ms when new; kind-2 camera frames `u32 seq | u64 t_us | JPEG` at ≤ 5/s when the camera
stream is requested; `{"type":"log","boot_id","text"}` (secrets redacted).

### 7.3 HTTP shape (legacy firmware / `transport:"http"` / the demo stand-in)

`MON/core.mjs:66-120, 388-436`; routes `FW/collector.cpp:1755-1770`. The monitor uses system curl, IPv4 only.

| request | auth | response |
| --- | --- | --- |
| `GET /v1/discovery?nonce=<32 hex>` | none | `{protocol:1, device_id, nonce, proof, port}` (as above) |
| `GET /v1/info` | `Authorization: Bearer <pairing token>` | info JSON; must contain `device_id`, `boot_id`, integer `audio_port` (`MON/core.mjs:80-83`), `capabilities` (`web_control`, `unified_control`, `hearing`, `eyes`, `monitor_streams`, … all `"supported"`, `FW/collector.cpp:908-1007`), `firmware`, `eyes{styles,states}`, `voice_library` |
| `GET /v1/telemetry?remote_after=N&hearing_after=N[&servo_diag=1]` | Bearer | telemetry JSON (§7.4), every 1 s (`MON/core.mjs:7, 399`) |
| `GET /v1/frame` | Bearer | JPEG with headers `X-Boot-Id`, `X-Seq` (only when a viewer wants the camera and `health.camera_enabled`) (`MON/core.mjs:410-421`, `FW/collector.cpp:1639-1660`) |
| `POST /v1/control` | Bearer + `X-Boot-Id: <boot_id>` (409 on mismatch), JSON ≤ 768 B | `{accepted:true,...}` (`FW/collector.cpp:1615-1637`) |
| `GET /v1/audio` (port `audio_port`) | Bearer | `SCA1` PCM stream; the monitor never opens it now (`ensureAudio` has no caller, `MON/core.mjs:332`) |

Precedent: `scripts/mind-local-demo.py` already serves a stand-in hello (`/v1/info`, `/v1/telemetry` with `mode`,
`/v1/control {target:"mode"}`) for a demo monitor on port 8807 (`MON/server.mjs:175-218`).

### 7.4 Telemetry document (`fill_telemetry`, `FW/collector.cpp:1043-1222`)

Top level: `boot_id seq timestamp_us imu{valid,timestamp_us,accel[3] m/s²,gyro[3] dps,...} temperature remote wifi
pitch_setting face_detection display head_touch touch{valid,count,x,y} power health playback hearing eyes asr m12
mouth robot_cmd mode interaction` (+ environment fields from `environment_json`).

- `power` (`:1117-1139`): `valid, battery_percent, charging, timestamp_us`, `pmic{vbus_good, vbus_mv, bat_mv, sys_mv,
  battery_current standby|charge|discharge, charge_phase, input_limit_ma, input_limited, vindpm_mv, vindpm_active,
  samples_on_vbus, discharging_on_vbus, limited_on_vbus, vindpm_on_vbus}`.
- `health` (`:1140-1196`): heap/psram, `camera_enabled`, `camera_preview`, camera counters, `audio_ready`,
  `mic_level_dbfs`, `mic_peak_dbfs`, `mic_gain_db`…, `speaker_enabled`, `servo_commands_enabled`, wifi rssi/channel.
- `hearing` (`FW/hearing.cpp:440-491`): `supported ready url token_configured requested connected state error session
  packets dropped_blocks reconnects responses speaker speaker_band text emotion speakers[] heartbeat{...}
  capture_queue{...} disconnects{...} last_end{...} response{...} forget{...} enrollment{...}` and, with
  `hearing_after`, `events[{seq,age_ms,kind,label,detail,p}]` (kinds `speech sound gap error link tingwu response`).
  `url` is shown; the token is not (`token_configured` only).
- `m12` (`m12_json`), plus `m12.words`, `m12.uplink` = vision link state `{state, connected, connects, failures,
  sent, frames_sent, ..., snap{...}}` (`FW/vision_uplink.cpp:379-403`).
- `mouth` (`FW/mouth_link.cpp:534-548`): link state, stream, buffered, counters, `burst{on,left_s,looks,lost,frames}`.
- `mode` (`FW/mouth_modes.h:365-393`): `mode dir host return_to seq label by since_ms online` + counters + `toast` +
  `chat_stage{stage,age_ms,...}`. The monitor's mode bar needs `telemetry.mode.mode` (`MON/control.mjs:86`).
- `robot_cmd` counters.

Telemetry does **not** go to the ear/mind. What the servers see of the robot's state: hearing ticks (levels), vision
`m12_online/offline`, `vision_on/off`, body summaries, mode reports, and on demand the `status` command (§5.6).

### 7.5 How `/api/control` actions reach the robot

`POST http://127.0.0.1:8787/api/control` (same-origin JSON ≤ 1024 B, `MON/server.mjs:62-79`) → `Controls.validate`
(`MON/control.mjs:24-112`) → `client.control('/v1/control', body, boot)` (serial op `control` or HTTP POST) →
`dispatch_control` (`FW/collector.cpp:1597-1603`) → target handler (`FW/collector.cpp:1543-1595`).

| action(s) | robot target / body |
| --- | --- |
| `hearing_import`, `hearing_config` | `{target:"hearing",op:"config",url,token}` (NVS `ear_config`) |
| `hearing_listen` | `{target:"hearing",op:"listen",enabled}` |
| `hearing_enroll` / `hearing_cancel` / `hearing_forget` | `op:"enroll",name,replace` / `op:"cancel"` / `op:"forget",name` (`FW/hearing.cpp:503-543`, `MON/hearing.mjs:15-34`) |
| `m12`, `m12_mode` | `m12_view {enabled}`, `m12_mode {mode standby|face_detector}` |
| `m12_learn/forget/faces/who/ping` | `{target:"m12_request",op,name?}` (202 + id; result in telemetry `m12.commands`) |
| `camera` | `{target:"camera",enabled}` (saved in NVS `camera_on`) |
| `mode` | `{target:"mode",input:"select"|"digit",value}` → `mode_apply("page",…)` (`FW/collector.cpp:1523-1541`) |
| `eyes_configure/play/stop` | `{target:"eyes",op,request_id,...}` |
| `interaction` | `{target:"interaction",mode tracking|recognition|advanced,enabled}` |
| `voice`, `volume`, `mic_gain` | `voice_play {clip_id}`, `volume {value 10..100}`, `mic_gain {value 0..24}` |
| `motor`, `yaw`, `yaw_center`, `yaw_turn`, `yaw_step`, `pitch` | `remote {enabled}`, `yaw_step {delta_deg|slot,...}`, `yaw_turn {duration_ms}`, `pitch {angle_deg 5..85}` |
| `servo_*`, `wifi_*` | `{target:"servo",op,...}`, `{target:"wifi",op,...}` |

The monitor refuses actions unless telemetry is fresh, `boot` matches, and the info capabilities say `supported`
(`MON/control.mjs:62-106`). The monitor also talks to mind directly via `/api/mind/*` (`MON/server.mjs:80-119`) —
not a robot link.

---

## 8. Servo / motion the server can command

Only through `cmd` on /mouth (§5.6): `yaw`, `front`, `turn_around`, `tracking`, `nod`, `shake`; plus `face_learn`
(M12). From the monitor (local, not the server): `motor`, `yaw*`, `pitch`, `servo_*`, `interaction tracking` (§7.5).
Nothing else moves the head remotely; ASR motion words and boot auto-tracking are local
(`FW/asr_unit.cpp:175-195`). A stand-in can ignore them (answer `motor_off`) or animate an on-screen head and answer
`ok` with plausible `data` (`facing` is `front`, `back` or `unknown`, `FW/timed_yaw_turn.h:9`; `tracking` bool).

---

## 9. Minimum for a Mac stand-in

### Required for the ear + mind to treat it as hello

1. **/mouth** with a hello carrying `device_id`, `boot_id` (needed for modes), `codecs:["opus"]`, `sample_rate:24000`,
   `cmd:1` if it answers commands; then the **boot `mode_report`** (`by:"boot"`, `input:"boot"`, mode `crowd`,
   effects `["stop_speech"]`) and every change after. Without a report, `ModeRelay.robot_mode` stays `None`, so
   Docking (`armed()` needs `robot_mode=="crowd"`, `EAR/realtime_link.py:236-248`), chat relay and stage dots are all off.
   Send the power-on `mode_input` digit 9 (source `quick`) only if the stand-in should switch the paid senses on.
2. Play `tts.*` streams and report `tts.playing` / `tts.progress` (≤ 500 ms) / `tts.done` (or `tts.stopped` /
   `tts.error`) with `played_ms`, or every stream stalls after 10 s and the next sentence never starts.
3. **Hearing** with `"source":"stackchan-…"` and continuous audio (PCM16 16 kHz raw is fine; or Opus 24 kHz SEA1 like the
   robot); `self` `sounding:true` while it plays any sound (no echo cancellation on the server: its own voice would
   otherwise be heard, answered and transcribed, `EAR/ear_hearing.py:579-584`).

### Optional

- **/vision**: hello + `face_*` events (owner greeting, presence/away pause, who_is_here), camera frames (snap /
  live eye / look_now need frames; snap also needs `snap_result` on /mouth), body sense.
- `asr_wake` / `asr_order` (only matter in Docking).
- `cmd` verbs: `word` and `eyes` are the visible ones; `status` feeds mind's robot_status; motion can be refused.
- `chat_stage`, `burst`, `mode_result` toasts: display only.
- Monitor: only if the page should show it. Easiest path is the HTTP shape (§7.3) on `127.0.0.1` with
  `transport:"http"` in a separate checkout's `.private/stackchan-monitor.json` (the file path is fixed per repo,
  `MON/server.mjs:226`; non-demo port comes from that file). Emulating USB serial is harder (§7.2).

### How a device is identified, and single-robot assumptions

- Identity on the wire: `device_id` + `boot_id` in each hello; `source` string on hearing/vision (sanitised to
  `[A-Za-z0-9_-]{1,64}`, `EAR/vision_link.py:153-155`). Nothing checks that `device_id` is a real MAC or that the token
  belongs to a device: **any client with the ear token is "the robot"**.
- **Mind mirrors exactly one robot**: `ModeStore._robot = {boot, conn, seq, ...}` (`MIND/mode_store.py:137`); a report
  from another boot overwrites the mirror and the stored `robot_boot` (`MIND/mode_store.py:461, 472-475`); reports from two
  devices interleave into one mode. `mode_input` uses the constant `device:"robot"` (`EAR/mouth_link.py:881`).
- **Ear ModeRelay keeps one `robot_mode`** = whichever link reported last (`EAR/mouth_link.py:262-273`); stage dots and
  the Docking session go to that link only (`EAR/realtime_link.py:236-237`).
- **Robot commands go to the newest /mouth link** (`EAR/mouth_link.py:142-149`): with a stand-in connected after the
  robot, eyes/words/motion go to the stand-in.
- **soul-tts accepts one device** and closes the older upstream with 4000 when a new one connects (`TTS/mouth.py:6,
  146-152`); the ear then closes that robot's /mouth with 4000 (`EAR/mouth_link.py:628-638, 769-770`) and the robot
  reconnects after backoff, replacing the other one in turn: **robot and stand-in on the same ear would keep knocking
  each other's /mouth off.** Mouth links are capped at 2 (`EAR/ear_server.py:398`).
- **One realtime (Docking) hub per ear**, fed by every `stackchan-*` hearing link with no per-device filter
  (`EAR/ear_hearing.py:566-578`, `EAR/realtime_link.py:305`): two mics would be mixed into one Qwen session.
- **KWS keeps one stream**: a new session drops idle older ones ("one robot", `EAR/kws_link.py:69-73`).
- **听悟 runs per hearing link** (`EAR/ear_hearing.py:355-363`): a second robot-profile link doubles the paid minutes.
- Presence and run-log robot.state are per link/source and would merge into "someone is here" (`EAR/presence.py:43-80`,
  **UNVERIFIED** in detail).
- Monitor: one binding per process (`MON/server.mjs:226-231`).

Conclusion: run the stand-in **instead of** the robot (robot powered off or its `ear_config` pointed elsewhere), or
against a second ear + mind + soul-tts instance (own port, own token file). Running both against the production ear at
once is not supported by the current server.
