import CoreGraphics
import Foundation

/// The vision uplink (docs/mac-hello/robot-links.md §4): hello, M12-style face events, SVF1 camera frames, pings.
public final class VisionLogic: LinkLogic {
    public weak var transport: Transport?
    public let id: DeviceIdentity
    private var seq = 0
    private var frameSeq = 0
    private var lastSentUs: UInt64 = 0
    public private(set) var open = false
    public private(set) var eventsSent = 0, framesSent = 0
    public var now: () -> UInt64 = monoUs
    public var onError: ((String) -> Void)?
    /// each (re)connect: the camera's change rule starts over, so the first frame after it goes (review 2026-11-19)
    public var onOpen: (() -> Void)?

    public init(id: DeviceIdentity) { self.id = id }

    public func opened() {
        open = true
        lastSentUs = now()
        transport?.send(text: Wire.json(["type": "hello", "source": id.source,
                                         "device": ["device_id": id.deviceId, "boot_id": id.bootId, "firmware": id.firmware,
                                                    "protocol": "soul-vision/1"]]))
        onOpen?()
    }

    public func closed(_ why: String) { open = false }
    public func received(data: Data) {}
    public func received(text: String) {
        if let m = Wire.parse(text), m["type"] as? String == "error" { onError?(m["message"] as? String ?? "") }
    }

    /// One M12-style event: event name + its fields (face, box [x,y,w,h] in 640x480, prob, name, sim, reason ...).
    public func event(_ name: String, _ fields: [String: Any] = [:], atUs: UInt64? = nil) {
        guard open else { return }
        seq += 1
        var m = fields
        m["type"] = "vision"; m["event"] = name; m["seq"] = seq
        m["t_us"] = Int(atUs ?? now()); m["now_us"] = Int(now())
        transport?.send(text: Wire.json(m))
        eventsSent += 1
        lastSentUs = now()
    }

    /// One camera JPEG. snap: the ear's look request id (sent at once, outside the change rule).
    @discardableResult
    public func frame(jpeg: Data, width: Int, height: Int, captureUs: UInt64, diff: Int, snap: Int? = nil) -> Int? {
        guard open else { return nil }
        frameSeq += 1
        var h: [String: Any] = ["type": "frame", "seq": frameSeq, "t_us": Int(captureUs), "now_us": Int(now()),
                                "w": width, "h": height, "diff": diff, "bytes": jpeg.count]
        if let snap { h["snap"] = snap }
        guard let d = Wire.svf1(header: h, jpeg: jpeg) else { return nil }
        transport?.send(data: d)
        framesSent += 1
        lastSentUs = now()
        return frameSeq
    }

    /// The robot pings after 15 s without traffic (the ear closes a link silent for 60 s). Call about once a second.
    public func keepAlive() {
        if open && now() - lastSentUs > 15_000_000 {
            transport?.send(text: Wire.json(["type": "ping"]))
            lastSentUs = now()
        }
    }
}

/// Upload size like the robot's body camera (owner 2026-11-19: "视觉要压缩"): its 320x240 JPEGs are ~5.7 KB (median of
/// 2000, max 8 KB). The Mac camera is sharper, so the quality steps down until the frame fits maxBytes.
public enum JpegBudget {
    public static let maxBytes = 8000
    public static let qualities: [Double] = [0.5, 0.38, 0.28, 0.2, 0.14, 0.1]

    /// encode(quality) -> bytes; -> the first that fits (else the smallest), with its quality
    public static func pick(maxBytes: Int = maxBytes, _ encode: (Double) -> Data?) -> (Data, Double)? {
        var best: (Data, Double)? = nil
        for q in qualities {
            guard let d = encode(q) else { continue }
            if d.count <= maxBytes { return (d, q) }
            if best == nil || d.count < best!.0.count { best = (d, q) }
        }
        return best
    }
}

/// The body camera's change rule (FW/frame_change.h): a 16x12 luma signature; a frame goes when >= 6 cells moved by
/// >= 30 after removing the overall brightness change, at most one per second, plus the first after a connect.
public struct FrameChange {
    public static let w = 16, h = 12
    public static let cells = 6, cellChange = 30
    public static let minGapUs: UInt64 = 1_000_000
    public var last: [UInt8]? = nil
    public var lastUs: UInt64 = 0
    public init() {}

    /// luma of each cell's centre pixel from a BGRA buffer
    public static func thumb(bgra: UnsafePointer<UInt8>, width: Int, height: Int, rowBytes: Int) -> [UInt8] {
        var out = [UInt8](repeating: 0, count: w * h)
        for cy in 0..<h { for cx in 0..<w {
            let sx = (2 * cx + 1) * width / (2 * w), sy = (2 * cy + 1) * height / (2 * h)
            let p = bgra + sy * rowBytes + sx * 4
            let b = Int(p[0]), g = Int(p[1]), r = Int(p[2])
            out[cy * w + cx] = UInt8((77 * r + 150 * g + 29 * b) >> 8)
        } }
        return out
    }

    public static func changedCells(_ a: [UInt8], _ b: [UInt8]) -> Int {
        let n = a.count
        let sa = a.reduce(0) { $0 + Int($1) }, sb = b.reduce(0) { $0 + Int($1) }
        var changed = 0
        for i in 0..<n where abs((Int(a[i]) * n - sa) - (Int(b[i]) * n - sb)) >= cellChange * n { changed += 1 }
        return changed
    }

    /// -> the changed-cell count when this frame should go (and remembers it as sent), else nil.
    public mutating func shouldSend(_ t: [UInt8], atUs: UInt64) -> Int? {
        guard let l = last else { last = t; lastUs = atUs; return 255 }
        let c = Self.changedCells(l, t)
        guard atUs - lastUs >= Self.minGapUs, c >= Self.cells else { return nil }
        last = t; lastUs = atUs
        return c
    }

    public mutating func reset() { last = nil }
}

/// One face the camera found this frame: a box in 640x480 pixels (top-left origin, as M12) and, sometimes, its print.
public struct FaceSeen {
    public var box: CGRect
    public var prob: Double
    public var print: [Float]?
    public init(box: CGRect, prob: Double, print: [Float]? = nil) { self.box = box; self.prob = prob; self.print = print }
}

/// M12-style face tracks (FW/m12_protocol.h change tracker): face_found when a new track starts, face_named /
/// face_unknown once a print was matched, face_lost after 1.5 s unseen. Track ids change after a face is gone.
public final class FaceTracker {
    public struct Track { public var id: Int; public var box: CGRect; public var lastUs: UInt64; public var name: String?
                          public var decided = false; public var tries = 0 }
    public private(set) var tracks: [Track] = []
    private var nextId = 1
    public static let lostUs: UInt64 = 1_500_000
    public let book: FaceBook

    public init(book: FaceBook) { self.book = book }

    public static func iouPublic(_ a: CGRect, _ b: CGRect) -> Double { iou(a, b) }

    static func iou(_ a: CGRect, _ b: CGRect) -> Double {
        let i = a.intersection(b)
        guard !i.isNull else { return 0 }
        let ia = Double(i.width * i.height)
        return ia / (Double(a.width * a.height + b.width * b.height) - ia)
    }

    /// -> the events of this frame, as (event, fields).
    public func update(_ faces: [FaceSeen], atUs t: UInt64) -> [(String, [String: Any])] {
        var out: [(String, [String: Any])] = []
        var used = Set<Int>()
        for f in faces {
            var best = -1, bestIou = 0.25
            for (i, tr) in tracks.enumerated() where !used.contains(i) {
                let v = Self.iou(tr.box, f.box)
                if v > bestIou { bestIou = v; best = i }
            }
            if best < 0 {
                tracks.append(Track(id: nextId, box: f.box, lastUs: t, name: nil))
                nextId += 1
                best = tracks.count - 1
                out.append(("face_found", fields(tracks[best], prob: f.prob)))
            }
            used.insert(best)
            tracks[best].box = f.box
            tracks[best].lastUs = t
            if !tracks[best].decided, let p = f.print {
                tracks[best].tries += 1
                let (name, sim) = book.match(p)
                if let name {
                    tracks[best].name = name; tracks[best].decided = true
                    var m = fields(tracks[best], prob: f.prob); m["name"] = name; m["sim"] = round(sim * 1000) / 1000
                    out.append(("face_named", m))
                } else if tracks[best].tries >= 3 {            // three looks without a match: a stranger
                    tracks[best].decided = true
                    var m = fields(tracks[best], prob: f.prob); m["sim"] = round(sim * 1000) / 1000
                    out.append(("face_unknown", m))
                }
            }
        }
        for tr in tracks where t - tr.lastUs > Self.lostUs {
            var m = fields(tr, prob: 0); m["reason"] = "timeout"; m.removeValue(forKey: "prob")
            out.append(("face_lost", m))
        }
        tracks.removeAll { t - $0.lastUs > Self.lostUs }
        return out
    }

    /// Camera off / faces off: every track is lost with reason vision_off.
    public func clear() -> [(String, [String: Any])] {
        defer { tracks = [] }
        return tracks.map { tr in var m = fields(tr, prob: 0); m["reason"] = "vision_off"; m.removeValue(forKey: "prob"); return ("face_lost", m) }
    }

    func fields(_ tr: Track, prob: Double) -> [String: Any] {
        let b = tr.box
        var m: [String: Any] = ["face": tr.id, "box": [Int(b.minX), Int(b.minY), Int(b.width), Int(b.height)],
                                "prob": round(prob * 100) / 100, "gender": "?"]
        if let n = tr.name { m["name"] = n }
        return m
    }
}

/// The Mac's local face library: up to 20 prints per name (FIFO, as M12), cosine similarity, one threshold.
/// Stored as JSON (names and float vectors only, no pictures) in the app's support folder.
public final class FaceBook {
    public private(set) var people: [String: [[Float]]] = [:]
    public var threshold: Float
    public static let perName = 20
    private let url: URL?

    public init(url: URL?, threshold: Float = 0.82) {
        self.url = url; self.threshold = threshold
        if let url, let d = try? Data(contentsOf: url), let o = try? JSONDecoder().decode([String: [[Float]]].self, from: d) {
            people = o
        }
    }

    public func add(_ name: String, _ print: [Float]) {
        var l = people[name] ?? []
        l.append(Self.unit(print))
        if l.count > Self.perName { l.removeFirst(l.count - Self.perName) }
        people[name] = l
        save()
    }

    public func forget(_ name: String) { people[name] = nil; save() }

    public func match(_ print: [Float]) -> (String?, Float) {
        let q = Self.unit(print)
        var bestName: String? = nil, best: Float = -1
        for (name, prints) in people {
            for p in prints where p.count == q.count {
                var s: Float = 0
                for i in 0..<q.count { s += p[i] * q[i] }
                if s > best { best = s; bestName = name }
            }
        }
        return best >= threshold ? (bestName, best) : (nil, max(best, 0))
    }

    static func unit(_ v: [Float]) -> [Float] {
        let n = sqrt(v.reduce(0) { $0 + $1 * $1 })
        return n > 0 ? v.map { $0 / n } : v
    }

    private func save() {
        guard let url, let d = try? JSONEncoder().encode(people) else { return }
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? d.write(to: url, options: .atomic)
    }
}
