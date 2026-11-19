import Foundation

/// The hearing uplink (docs/mac-hello/robot-links.md §3): Opus 24 kHz 60 ms in SEA1 packets, the self flag, the ear's
/// 100 ms ticks (3 s without one = reconnect, as the robot), events on the ticks.
public final class HearingLogic: LinkLogic {
    public weak var transport: Transport?
    public let id: DeviceIdentity
    private let encoder = OpusEncoder()!
    private var pending: [Int16] = []
    private var pendingStartUs: UInt64 = 0
    private var seq: UInt32 = 0
    private var open = false
    private var acked = false
    private var sentSelf: Bool? = nil
    public private(set) var sounding = false
    public private(set) var lastTickUs: UInt64 = 0
    public private(set) var ticks = 0
    public private(set) var packets = 0
    public private(set) var levelDb: Double? = nil
    /// the ear's VAD on every tick: someone is talking (the response clip waits for quiet)
    public private(set) var serverSpeaking = false
    public private(set) var lastSpeakingUs: UInt64 = 0
    public var now: () -> UInt64 = monoUs
    /// Every event the ear posts on the ticks (speech, sound, enroll, speakers, keyword ...), plus "audio" (the ack).
    public var onEvent: (([String: Any]) -> Void)?
    public var onTick: (([String: Any]) -> Void)?

    public init(id: DeviceIdentity) { self.id = id }

    public func opened() {
        open = true; acked = false; seq = 0; sentSelf = nil; pending = []; lastTickUs = now()
        transport?.send(text: Wire.json([
            "type": "audio", "format": "opus", "sample_rate": 24000, "channels": 1, "frame_ms": 60, "framing": "sea1",
            "source": id.source,
            "device": ["device_id": id.deviceId, "boot_id": id.bootId, "firmware": id.firmware,
                       "protocol": "soul-ear/1", "channel_roles": ["mic"]]]))
    }

    public func closed(_ why: String) { open = false; acked = false }

    public func received(data: Data) {}

    public func received(text: String) {
        guard let m = Wire.parse(text) else { return }
        if m["type"] as? String == "tick" {
            ticks += 1
            lastTickUs = now()
            levelDb = m["level_db"] as? Double
            onTick?(m)
            if let s = m["speaking"] as? Bool { serverSpeaking = s; if s { lastSpeakingUs = now() } }
            for var e in (m["events"] as? [[String: Any]]) ?? [] {
                if e["type"] as? String == "audio" { acked = true }
                e["_rx"] = m["rx"]                    // the tick's received samples: a speech result's age (FW/hearing.cpp)
                onEvent?(e)
            }
        }
    }

    /// The robot drops the session after 3 s without a tick (FW/hearing.cpp:63-64). Call about once a second.
    public func watchdog() {
        if open && now() - lastTickUs > 3_000_000 { transport?.reconnect("no tick for 3 s") }
    }

    /// hello's own voice is playing (or just stopped): the ear mutes its hearing and Docking sends nothing up.
    public func setSounding(_ on: Bool) { sounding = on }

    /// 24 kHz mono PCM16 from the mic; firstUs = capture time of its first sample.
    public func feed(_ pcm: [Int16], firstUs: UInt64) {
        guard open else { return }
        if pending.isEmpty { pendingStartUs = firstUs }
        pending.append(contentsOf: pcm)
        let n = OpusEncoder.frameSamples
        while pending.count >= n {
            let frame = Array(pending.prefix(n))
            pending.removeFirst(n)
            if sentSelf != sounding {                 // once per session, then on change, before the packet
                sentSelf = sounding
                transport?.send(text: Wire.json(["type": "self", "sounding": sounding, "moving": false, "t_us": Int(pendingStartUs)]))
            }
            if let pkt = encoder.encode(frame) {
                transport?.send(data: Wire.sea1(seq: seq, captureUs: pendingStartUs, opus: pkt))
                packets += 1
            }
            seq &+= 1
            pendingStartUs += 60_000
        }
    }

    public var isAcked: Bool { acked }

    /// Voiceprint enrollment through the ear (as the robot's: five sentences, 120 s). The ear answers with enroll events.
    public func enroll(name: String, replace: Bool = false) {
        transport?.send(text: Wire.json(["type": "enroll", "name": name, "request_id": UUID().uuidString.lowercased(),
                                         "count": 5, "replace": replace]))
    }

    public func cancelEnroll() { transport?.send(text: Wire.json(["type": "enroll_cancel"])) }
    public func forget(name: String) { transport?.send(text: Wire.json(["type": "forget", "name": name])) }
}
