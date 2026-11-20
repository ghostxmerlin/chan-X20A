import AppKit
import Foundation
import HelloCore

/// 鹿野 on hello's screen (2026-11-20): plays her frames with the manifest's timings. One timer, one picture; what it
/// shows, in order: a gesture's look steps, a played action (at least one whole cycle, then until its time is up),
/// the stage's action (looping while the dots show), the look frame toward the face, else idle (breathing, blinking).
final class LuyeView: NSView {
    private let img = NSImageView()
    private let m: Luye.Manifest
    private var frames: [String: [NSImage]] = [:]
    private var looks: [NSImage] = []
    private var timer: Timer?
    private var shown: String? = nil, at = 0
    private var played: (action: String, until: Date)?
    private var stage: String?
    private var lookAt: Int?
    private var steps: [(look: Int?, s: Double)] = []
    private var nextNatural = Date().addingTimeInterval(Double.random(in: Luye.naturalEvery))

    /// nil when the folder has no usable manifest or idle frames (the 鹿野 option is then not offered)
    init?(dir: URL) {
        guard let data = try? Data(contentsOf: dir.appendingPathComponent("animation-manifest.json")),
              let m = Luye.parse(data) else { return nil }
        self.m = m
        super.init(frame: .zero)
        for (name, clip) in m.clips {
            let imgs = (0..<clip.frames).compactMap { Self.load(dir.appendingPathComponent("frames/\(name)/\(String(format: "%02d", $0)).png")) }
            if imgs.count == clip.frames { frames[name] = imgs }
        }
        looks = (0..<m.looks.count).compactMap { Self.load(dir.appendingPathComponent("frames/look/\(String(format: "%02d", $0)).png")) }
        if looks.count != m.looks.count { looks = [] }
        guard frames["idle"] != nil else { return nil }
        img.imageScaling = .scaleProportionallyUpOrDown
        addSubview(img)
    }

    required init?(coder: NSCoder) { nil }

    /// One frame with the green-screen fringe taken out: no pixel may be greener than its red / blue (she wears none).
    static func load(_ url: URL) -> NSImage? {
        guard let src = NSImage(contentsOf: url)?.cgImage(forProposedRect: nil, context: nil, hints: nil) else { return nil }
        let w = src.width, h = src.height
        guard let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
                                  space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue),
              let px = ctx.data?.bindMemory(to: UInt8.self, capacity: w * h * 4) else { return nil }
        ctx.draw(src, in: CGRect(x: 0, y: 0, width: w, height: h))
        for i in stride(from: 0, to: w * h * 4, by: 4) where px[i + 3] > 0 {
            let cap = max(px[i], px[i + 2])
            if px[i + 1] > cap { px[i + 1] = cap }
        }
        guard let out = ctx.makeImage() else { return nil }
        return NSImage(cgImage: out, size: NSSize(width: w, height: h))
    }

    override func layout() { super.layout(); img.frame = bounds }

    var running = false {
        didSet {
            guard running != oldValue else { return }
            if running { shown = nil; step() } else { timer?.invalidate(); timer = nil }
        }
    }

    /// an action for about `seconds` (at least one whole cycle); unknown names are ignored
    func play(_ action: String, seconds: Double = 0) {
        guard let clip = m.clips[action], frames[action] != nil, action != "idle" else { return }
        played = (action, Date().addingTimeInterval(max(seconds, Double(clip.totalMs) / 1000)))
        kick()
    }

    func play(eye: String, seconds: Double = 0) { play(Luye.action(forEye: eye), seconds: seconds) }

    func setStage(_ s: String) { stage = Luye.action(forStage: s); kick() }

    func look(_ i: Int?) {
        guard i != lookAt else { return }
        lookAt = looks.isEmpty ? nil : i
        kick()
    }

    func gesture(_ kind: String) { steps = looks.isEmpty ? [] : Luye.gesture(kind); kick() }

    /// show the right thing now (a new state should not wait out the current frame)
    private func kick() { if running { timer?.invalidate(); step() } }

    private func step() {
        let now = Date()
        if !steps.isEmpty {
            let s = steps.removeFirst()
            img.image = s.look.flatMap { $0 < looks.count ? looks[$0] : nil } ?? frames["idle"]?.first
            shown = nil
            schedule(s.s)
            return
        }
        if let p = played, now >= p.until, shown != p.action || at == 0 { played = nil }
        if played == nil, stage == nil, lookAt == nil, now >= nextNatural {
            nextNatural = now.addingTimeInterval(Double.random(in: Luye.naturalEvery))
            if let a = Luye.natural.randomElement(), let clip = m.clips[a], frames[a] != nil {
                played = (a, now.addingTimeInterval(Double(clip.totalMs) / 1000))
            }
        }
        let want: String? = played?.action ?? stage.flatMap { frames[$0] != nil ? $0 : nil } ?? (lookAt != nil ? nil : "idle")
        guard let name = want, let fs = frames[name], let clip = m.clips[name] else {
            if let i = lookAt, i < looks.count { img.image = looks[i] }       // a still look toward the face
            shown = nil
            schedule(0.3)
            return
        }
        if shown != name { shown = name; at = 0 }
        img.image = fs[at]
        let ms = clip.durationsMs[at]
        at = (at + 1) % fs.count
        schedule(Double(ms) / 1000)
    }

    private func schedule(_ s: Double) {
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: max(0.03, s), repeats: false) { [weak self] _ in self?.step() }
    }
}
