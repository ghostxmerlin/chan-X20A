import Foundation

/// The robot's SD voice library on the Mac: PLAY-001..080 as 24 kHz mono PCM16 (the library's reference.pcm) and each
/// clip's emotion (config/voice-emotions.json). In the app bundle under Resources/voices (build-app.sh copies them from
/// the main checkout's .private/voice-library: hello's own lines, never in the repo); `swift run` reads them there.
final class Voices {
    private(set) var tags: [Int: String] = [:]
    private let dir: URL?
    private var cache: [Int: [Int16]] = [:]

    init() {
        let fm = FileManager.default
        var candidates: [URL] = []
        if let r = Bundle.main.resourceURL { candidates.append(r.appendingPathComponent("voices")) }
        let repo = URL(fileURLWithPath: #filePath).deletingLastPathComponent().appendingPathComponent("../../../..").standardized
        candidates.append(repo.appendingPathComponent(".private/voice-library"))
        candidates.append(repo.appendingPathComponent("../chan-X20A/.private/voice-library").standardized)
        dir = candidates.first { fm.fileExists(atPath: $0.appendingPathComponent("PLAY-001.reference.pcm").path) ||
                                 fm.fileExists(atPath: $0.appendingPathComponent("PLAY-001.pcm").path) }
        let emo = [Bundle.main.resourceURL?.appendingPathComponent("voice-emotions.json"),
                   repo.appendingPathComponent("config/voice-emotions.json")].compactMap { $0 }
            .first { fm.fileExists(atPath: $0.path) }
        if let emo, let d = try? Data(contentsOf: emo), let o = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
           let clips = o["clips"] as? [String: String] {
            for (k, v) in clips { if let n = Int(k.replacingOccurrences(of: "PLAY-", with: "")) { tags[n] = v } }
        }
        if dir == nil { tags = [:] }
    }

    var count: Int { tags.count }

    func pcm(_ id: Int) -> [Int16]? {
        if let c = cache[id] { return c }
        guard let dir else { return nil }
        let name = String(format: "PLAY-%03d", id)
        let url = [dir.appendingPathComponent("\(name).reference.pcm"), dir.appendingPathComponent("\(name).pcm")]
            .first { FileManager.default.fileExists(atPath: $0.path) }
        guard let url, let d = try? Data(contentsOf: url) else { return nil }
        let s = d.withUnsafeBytes { Array($0.bindMemory(to: Int16.self)) }.map { Int16(littleEndian: $0) }
        cache[id] = s
        return s
    }
}
