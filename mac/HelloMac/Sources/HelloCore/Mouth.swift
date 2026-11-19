import Foundation

/// Where hello's speech plays (AVAudioEngine in the app, a clock in the probe / tests).
public enum SpeechEvent {
    case playing(stream: UInt32, bufferedMs: Int)
    case progress(stream: UInt32, playedMs: Int, bufferedMs: Int)
    case done(stream: UInt32, playedMs: Int)
    case stopped(stream: UInt32, playedMs: Int)
}

public protocol SpeechOutput: AnyObject {
    var onEvent: ((SpeechEvent) -> Void)? { get set }
    func begin(stream: UInt32)                 // a new stream (an older one is stopped first: stopped event)
    func push(stream: UInt32, pcm: [Int16])    // 24 kHz mono
    func finish(stream: UInt32)                // no more packets: done once everything played
    func stop(stream: UInt32)                  // stop now: stopped event
    func stopAll()
}

/// The robot's mode, owned here (hello owns the mode, docs/mac-hello/robot-links.md §6): transitions + reports.
public final class ModeMachine {
    public private(set) var state = ModeRules.State()
    public private(set) var seq = 0
    private var lastBy = "boot"
    private var unreported: [ModeRules.Effect] = []
    private var bootPending = true

    public init() {
        let o = ModeRules.transition(state, .boot, ModeRules.Value(), speaking: false)
        state = o.state
        unreported = o.effects
    }

    /// One input -> the outcome and the mode_report to send (nil: nothing to report).
    public func apply(_ input: ModeRules.Input, value: ModeRules.Value = ModeRules.Value(), by: String,
                      speaking: Bool) -> (ModeRules.Outcome, [String: Any]?) {
        let old = state
        let o = ModeRules.transition(state, input, value, speaking: speaking)
        state = o.state
        let touch = old.mode == .crowd && (input == .tap || input == .doubleTap) && o.code == .ok
        guard o.state != old || !o.effects.isEmpty || touch else { return (o, nil) }
        lastBy = by
        seq += 1
        unreported = []
        return (o, report(by: by, input: input.rawValue, effects: o.effects.map(\.rawValue)))
    }

    /// After every handshake: the current mode; the first after the start says boot.
    public func syncReport() -> [String: Any] {
        seq += 1
        let first = bootPending
        bootPending = false
        defer { unreported = [] }
        return report(by: first ? "boot" : lastBy, input: first ? "boot" : nil, effects: unreported.map(\.rawValue))
    }

    private func report(by: String, input: String?, effects: [String]) -> [String: Any] {
        ["type": "mode_report", "seq": seq, "mode": state.mode.id, "dir": state.dir?.rawValue ?? NSNull(),
         "host": state.hosted ? "event" : NSNull(), "return_to": state.returnTo?.id ?? NSNull(), "by": by,
         "input": input ?? NSNull(), "effects": effects]
    }

    public var label: String { ModeRules.label(state) }
    public var colour: UInt32 { ModeRules.def(state.mode).color }
}

/// What the app does for the ear's screen / command messages.
public protocol MouthDelegate: AnyObject {
    func command(_ verb: String, args: [String: Any]) -> (ok: Bool, error: String?, data: [String: Any])
    func stage(_ stage: String)
    func burst(_ m: [String: Any])
    func toast(_ text: String, code: String)
    func snap(id: Int)
    func localDigit(_ value: Int)            // the robot's own half of 0 / 9 (hearing, camera, faces)
}

/// The mouth link (§5): hello, mode reports, speech down (SPK1 Opus -> SpeechOutput), reports up, cmd / stage / burst.
public final class MouthLogic: LinkLogic {
    public weak var transport: Transport?
    public weak var delegate: MouthDelegate?
    public let id: DeviceIdentity
    public let modes = ModeMachine()
    public let output: SpeechOutput
    private let decoder = OpusDecoder()!
    private var handshaken = false
    private var current: UInt32? = nil
    private var lastSpeechEndUs: UInt64 = 0
    private var inputSeq = 0
    private var wakeCount = 0, orderCount = 0
    private var progressAt: [UInt32: UInt64] = [:]
    public var now: () -> UInt64 = monoUs
    /// owner choice: the robot sends a power-on 9 (= all paid on) to mind; off by default on the Mac
    public var powerOnNine = false
    private var nineOwed = false, nineTries = 0, nineAt: UInt64 = 0, nineSeq = 0
    public private(set) var streams = 0
    public var onSounding: ((Bool) -> Void)?
    private var lastSounding = false

    public init(id: DeviceIdentity, output: SpeechOutput) {
        self.id = id
        self.output = output
        output.onEvent = { [weak self] e in self?.speechEvent(e) }
        nineOwed = false
    }

    public var speaking: Bool { current != nil }

    public func opened() {
        handshaken = false
        transport?.send(text: Wire.json(["type": "hello", "device_id": id.deviceId, "boot_id": id.bootId, "codecs": ["opus"],
                                         "sample_rate": 24000, "buffer_ms": 10080, "fw": id.firmware, "cmd": 1]))
    }

    public func closed(_ why: String) {
        handshaken = false
        if let c = current { output.stop(stream: c) }
    }

    public func received(text: String) {
        guard let m = Wire.parse(text), let type = m["type"] as? String else { return }
        switch type {
        case "hello":
            guard m["codec"] as? String == "opus", m["frame_ms"] as? Int == 60, m["sample_rate"] as? Int == 24000 else {
                transport?.reconnect("ear hello mismatch"); return
            }
            handshaken = true
            transport?.send(text: Wire.json(modes.syncReport()))
            if powerOnNine && nineTries == 0 { nineOwed = true }
            sendNineIfOwed(force: true)
        case "tts.start":
            guard let s = (m["stream"] as? NSNumber)?.uint32Value else { return }
            if m["codec"] as? String != "opus" || m["frame_ms"] as? Int != 60 {
                transport?.send(text: Wire.json(["type": "tts.error", "stream": Int(s), "error": "unsupported_codec"])); return
            }
            current = s
            streams += 1
            output.begin(stream: s)
            updateSounding()
        case "tts.end":
            if let s = (m["stream"] as? NSNumber)?.uint32Value, s == current { output.finish(stream: s) }
        case "tts.stop":
            if let s = (m["stream"] as? NSNumber)?.uint32Value, s == current { output.stop(stream: s) }
        case "mode_result":
            let code = m["code"] as? String ?? ""
            if code == "ok", let seq = m["seq"] as? Int, seq == nineSeq { nineOwed = false }
            delegate?.toast(m["toast"] as? String ?? code, code: code)
        case "chat_stage": delegate?.stage(m["stage"] as? String ?? "")
        case "burst": delegate?.burst(m)
        case "snap": if let i = m["id"] as? Int { delegate?.snap(id: i) }
        case "cmd":
            guard let cid = m["id"] as? Int, let verb = m["do"] as? String else { return }
            let r = delegate?.command(verb, args: m["args"] as? [String: Any] ?? [:]) ?? (false, "failed", [:])
            transport?.send(text: Wire.json(["type": "cmd_result", "id": cid, "ok": r.ok, "error": r.error ?? NSNull(),
                                             "data": r.data]))
        default: break                                   // place and anything newer: ignored, as the robot does
        }
    }

    public func received(data: Data) {
        guard let p = Wire.spk1(data), p.stream == current, let pcm = decoder.decode(p.payload) else { return }
        output.push(stream: p.stream, pcm: pcm)
    }

    // ---- the robot's own clips (SD voice library; FW/hearing.cpp playback_hearing): a local stream, never reported ----
    public static let localBase: UInt32 = 0xFFFF_0000
    private var localSeq: UInt32 = 0

    /// Play 24 kHz PCM locally (a response clip, the clip verb). Refused while a stream plays (the robot: speaking).
    @discardableResult
    public func playLocal(_ pcm: [Int16]) -> Bool {
        guard current == nil, !pcm.isEmpty else { return false }
        localSeq = (localSeq + 1) & 0xFFFF
        let s = Self.localBase + localSeq
        current = s
        output.begin(stream: s)
        var i = 0
        while i < pcm.count { let n = min(1440, pcm.count - i); output.push(stream: s, pcm: Array(pcm[i..<i + n])); i += n }
        output.finish(stream: s)
        updateSounding()
        return true
    }

    private func isLocal(_ s: UInt32) -> Bool { s >= Self.localBase }

    private func speechEvent(_ e: SpeechEvent) {
        switch e {
        case .playing(let s, _) where isLocal(s): return
        case .progress(let s, _, _) where isLocal(s): return
        case .done(let s, _) where isLocal(s): ended(s); return
        case .stopped(let s, _) where isLocal(s): ended(s); return
        default: break
        }
        switch e {
        case .playing(let s, let b):
            transport?.send(text: Wire.json(["type": "tts.playing", "stream": Int(s), "buffered_ms": b]))
        case .progress(let s, let p, let b):
            let t = now()
            if t - (progressAt[s] ?? 0) >= 250_000 {     // every >= 250 ms while playing (FW/mouth_stream.h:17)
                progressAt[s] = t
                transport?.send(text: Wire.json(["type": "tts.progress", "stream": Int(s), "played_ms": p, "buffered_ms": b,
                                                 "underruns": 0]))
            }
        case .done(let s, let p):
            transport?.send(text: Wire.json(["type": "tts.done", "stream": Int(s), "played_ms": p, "underruns": 0,
                                             "underrun_ms": 0]))
            ended(s)
        case .stopped(let s, let p):
            transport?.send(text: Wire.json(["type": "tts.stopped", "stream": Int(s), "played_ms": p]))
            ended(s)
        }
    }

    private func ended(_ s: UInt32) {
        progressAt[s] = nil
        if s == current { current = nil; lastSpeechEndUs = now() }
        updateSounding()
    }

    /// The robot's self flag: its own stream, then 450 ms after the last frame (FW/playback.cpp:59).
    public var sounding: Bool { current != nil || now() - lastSpeechEndUs < 450_000 }

    /// Call every ~100 ms: the sounding tail and the owed power-on 9.
    public func tick() {
        updateSounding()
        sendNineIfOwed(force: false)
    }

    private func updateSounding() {
        let s = sounding
        if s != lastSounding { lastSounding = s; onSounding?(s) }
    }

    private func sendNineIfOwed(force: Bool) {
        guard nineOwed, handshaken, nineTries < 30, force || now() - nineAt >= 10_000_000 else { return }
        nineTries += 1
        nineAt = now()
        nineSeq = sendDigit(9, source: "quick", local: nineTries == 1)   // review: the local half once, not every resend
    }

    // ---- inputs from the Mac (the robot's touch screen / voice module stand-ins) ----

    /// A tap or double tap on hello's face (the robot's home screen).
    @discardableResult
    public func touch(double: Bool) -> ModeRules.Outcome {
        let (o, rep) = modes.apply(double ? .doubleTap : .tap, by: "touch", speaking: speaking)
        if o.effects.contains(.stopSpeech) { output.stopAll() }   // the robot stops its stream locally too
        if let rep { transport?.send(text: Wire.json(rep)) }
        return o
    }

    @discardableResult
    public func input(_ input: ModeRules.Input, value: ModeRules.Value = ModeRules.Value(), by: String) -> ModeRules.Outcome {
        let (o, rep) = modes.apply(input, value: value, by: by, speaking: speaking)
        if o.effects.contains(.stopSpeech) { output.stopAll() }
        if let rep { transport?.send(text: Wire.json(rep)) }
        return o
    }

    /// Digits 0 / 9 / 1 are mind's (mode_input); 4-8 are local modes; the robot runs its own half of 0 / 9.
    @discardableResult
    public func sendDigit(_ v: Int, source: String, local: Bool = true) -> Int {
        if local && (v == 0 || v == 9) { delegate?.localDigit(v) }
        if (4...8).contains(v) {
            var val = ModeRules.Value(); val.digit = v
            input(.digit, value: val, by: source)
            return 0
        }
        inputSeq += 1
        transport?.send(text: Wire.json(["type": "mode_input", "seq": inputSeq, "age_ms": 0, "source": source,
                                         "input": "digit", "value": v]))
        return inputSeq
    }

    /// The voice module's wake word ("hello"): the robot stops its own speech and tells the ear.
    public func asrWake() {
        let interrupted = speaking
        output.stopAll()
        wakeCount += 1
        transport?.send(text: Wire.json(["type": "asr_wake", "word": "hello", "n": wakeCount, "interrupted": interrupted]))
    }

    public func asrOrder(_ word: String) {
        orderCount += 1
        transport?.send(text: Wire.json(["type": "asr_order", "word": word, "n": orderCount]))
    }

    public func snapResult(id: Int, ok: Bool, why: String? = nil, seq: Int = 0, bytes: Int = 0, ms: Int = 0) {
        var m: [String: Any] = ["type": "snap_result", "id": id, "ok": ok]
        if ok { m["seq"] = seq; m["bytes"] = bytes; m["ms"] = ms } else { m["why"] = why ?? "busy" }
        transport?.send(text: Wire.json(m))
    }
}

/// A SpeechOutput that "plays" in real time without a speaker (the probe, the self-test): prebuffer 180 ms, then the
/// played time follows a clock; done when everything pushed has played after finish.
public final class ClockSpeechOutput: SpeechOutput {
    public var onEvent: ((SpeechEvent) -> Void)?
    public var now: () -> UInt64 = monoUs
    private var stream: UInt32? = nil
    private var queuedMs = 0
    private var startedUs: UInt64? = nil
    private var finished = false
    public private(set) var samples: [Int16] = []
    public init() {}

    public func begin(stream s: UInt32) {
        if let old = stream { onEvent?(.stopped(stream: old, playedMs: played())) }
        stream = s; queuedMs = 0; startedUs = nil; finished = false
    }

    public func push(stream s: UInt32, pcm: [Int16]) {
        guard s == stream else { return }
        queuedMs += pcm.count * 1000 / 24000
        samples.append(contentsOf: pcm)
        if startedUs == nil && queuedMs >= 180 { startedUs = now(); onEvent?(.playing(stream: s, bufferedMs: queuedMs)) }
    }

    public func finish(stream s: UInt32) {
        guard s == stream else { return }
        finished = true
        if startedUs == nil { startedUs = now(); onEvent?(.playing(stream: s, bufferedMs: queuedMs)) }
    }

    public func stop(stream s: UInt32) {
        guard s == stream else { return }
        let p = played()
        stream = nil
        onEvent?(.stopped(stream: s, playedMs: p))
    }

    public func stopAll() { if let s = stream { stop(stream: s) } }

    private func played() -> Int {
        guard let t = startedUs else { return 0 }
        return min(queuedMs, Int((now() - t) / 1000))
    }

    /// Call every ~50-100 ms.
    public func tick() {
        guard let s = stream, startedUs != nil else { return }
        let p = played()
        if finished && p >= queuedMs { stream = nil; onEvent?(.done(stream: s, playedMs: p)) }
        else { onEvent?(.progress(stream: s, playedMs: p, bufferedMs: queuedMs - p)) }
    }
}
