# Providence: the Stick S3 voice module, Docking only (2026-11-20)

Owner 2026-11-20, approved in chat ("可以"):

1. The M12 and the voice module swapped plugs on the body.
2. The voice module is now the owner's own StickS3 build (`~/exia/stick/docs/voice-protocol.md`).
3. The Dynames keyword service is off (done 2026-11-20: `soul-kws` stopped).
4. The robot is renamed **Providence (神意高达)**; the Stick sends a `PROVIDENCE` word.
5. The robot keeps only Docking mode; every other mode goes.
6. The page and the server drop the other modes' state too.
7. The old ASR direct commands are all off.

## Why

The owner is simplifying hello to the one way it is actually used (Docking) and replacing the factory Unit ASR with
their own voice module, whose vocabulary is a handful of named words rather than motion / digit commands.

## Goals

- Firmware: M12 on Port B (G9 TX / G8 RX, UART0), the Stick on Port C (G18 RX = Stick TX, G17 TX = Stick RX,
  UART2). Both move in one build; a build with only one moved would leave a device on the wrong pins.
- Firmware: the Stick's frames (`AA 55 id 55 AA`, ids `50`–`5F`) are read by the existing parser. `53` PROVIDENCE =
  being called by name (as the old ASR "hello" wake: in Docking it opens a chat). The other seven words are reported
  to the server as heard words (logged, shown on the page), no action.
- Firmware: Docking (crowd) is the only mode. Settings stops being a mode but its page stays reachable (Wi-Fi
  profiles live there).
- Firmware: no handling of the factory ids (motion words, forward / backward, turn on / off, digits, volume).
- Server (mind, ear) and the 8788 page: only Docking; the mode bar and the other modes' state go. The ear's call
  words become hello → Providence; the persona says she lives in Providence (神意高达). The ear stops calling the
  keyword service.
- Mac app afterwards, to match.

## Non-goals

- Speaker mode on the Stick (`E0`–`E3`, the proposed `C0`–`D1` frames): G17 is wired as TX, nothing is sent.
- New actions for the seven other words: they wait for the owner.
- The 收集模式 / 智能模式 redesign (separate, open).

## Constraints

- Flashing the robot and deploying the robot's server stack wait for the owner's go.
- The server change must keep the robot's current firmware working until the new one is flashed (it still reports
  other modes and sends digits).
- Kids' names never go into the repo; secrets never printed.

## Approaches

- **Chosen: server first (tolerant), then firmware, then the Mac app.** The server accepts the old firmware's mode
  reports while showing and steering only Docking, so each side can ship on its own.
- Firmware first: the robot would run Docking-only firmware against a server and page that still offer eight modes
  (buttons that do nothing). Rejected.
- One combined drop: firmware and server must then deploy together; a failed flash leaves the server mismatched.
  Rejected.

## Left to the builder

How each layer removes its mode code (delete vs. reduce the tables), keeping the 2100 mode-rule vectors' generator
consistent across firmware, server and the Mac app; the wording on the page.
