import Foundation

/// The robot's recent hearing events as its telemetry carries them (FW hearing events -> web/monitor core.mjs
/// hearingEvent), for the Mac deck's log: speech (label = the text, detail = emotion/band/speaker, p = voice
/// probability), response (label = the sentence answered, detail = emotion/clip/error/waited ms), link
/// (label connected / disconnected). The last 32, seq from 1, any thread.
public final class DeckEvents {
    public static let keep = 32
    private let lock = NSLock()
    private var seq = 0
    private var items: [(seq: Int, us: UInt64, kind: String, label: String, detail: String, p: Double?)] = []

    public init() {}

    public func add(_ kind: String, label: String, detail: String = "", p: Double? = nil, atUs: UInt64 = monoUs()) {
        lock.lock(); defer { lock.unlock() }
        seq += 1
        items.append((seq, atUs, kind, label, detail, p))
        if items.count > Self.keep { items.removeFirst(items.count - Self.keep) }
    }

    /// The events as telemetry `hearing.events`, age_ms counted to `nowUs`.
    public func snapshot(nowUs: UInt64 = monoUs()) -> [[String: Any]] {
        lock.lock(); defer { lock.unlock() }
        return items.map { e in
            var o: [String: Any] = ["seq": e.seq, "kind": e.kind, "label": e.label, "detail": e.detail,
                                    "age_ms": Int(nowUs >= e.us ? (nowUs - e.us) / 1000 : 0)]
            if let p = e.p { o["p"] = p }
            return o
        }
    }

    /// A finished sentence from the ear -> (label, detail, p); the speaker's name only when the voiceprint knows them.
    public static func speech(_ e: [String: Any]) -> (label: String, detail: String, p: Double?) {
        let sp = e["speaker"] as? [String: Any] ?? [:]
        let band = sp["band"] as? String ?? ""
        let name = band == "known" ? (sp["name"] as? String ?? "") : ""
        let p = ((e["voice"] as? [String: Any])?["p"] as? NSNumber)?.doubleValue
        return (e["text"] as? String ?? "", "\(e["emotion"] as? String ?? "")/\(band)/\(name)", p)
    }
}
