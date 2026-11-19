import AppKit
import Foundation

/// hello's face: the robot's 320x240 screen drawn at 2x. Eyes (the repo's v05 / v14 / v18 GIFs, natural cycle), the
/// mode corner label, the toast and burst row on top, the word under the eyes, the chat stage dots, a status strip.
/// A click = the robot's tap, a double click = its double tap.
final class FaceView: NSView {
    private let eyes = NSImageView()
    private let modeLabel = NSTextField(labelWithString: "DOCK")
    private let toast = NSTextField(labelWithString: "")
    private let burst = NSTextField(labelWithString: "")
    private let word = NSTextField(labelWithString: "")
    private let dots = NSTextField(labelWithString: "")
    private let status = NSTextField(labelWithString: "")
    private let preview = NSImageView()
    var onTap: ((Bool) -> Void)?                   // double?
    var eyesDir: URL?
    var style = "v14" { didSet { showIdle() } }
    private var playing: String? = nil
    private var cycle: Timer?
    private var wordTimer: Timer?, toastTimer: Timer?
    private var singleTap: DispatchWorkItem?
    static let lively = ["happy", "curious", "thinking", "expectant", "greeting", "shy", "relieved", "proud", "laughing",
                         "goofy", "confused", "encouraging", "grateful", "comforting", "affectionate"]

    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        layer?.backgroundColor = NSColor.black.cgColor
        eyes.imageScaling = .scaleProportionallyUpOrDown
        eyes.animates = true
        preview.imageScaling = .scaleProportionallyUpOrDown
        preview.alphaValue = 0.9
        preview.isHidden = true
        for l in [modeLabel, toast, burst, word, dots, status] {
            l.textColor = .white; l.backgroundColor = .clear; l.isBordered = false; l.isEditable = false
        }
        modeLabel.font = .monospacedSystemFont(ofSize: 22, weight: .bold)
        toast.font = .monospacedSystemFont(ofSize: 18, weight: .medium); toast.alignment = .center
        burst.font = .systemFont(ofSize: 16); burst.textColor = NSColor.systemYellow
        word.font = .systemFont(ofSize: 34, weight: .semibold); word.alignment = .center
        dots.font = .systemFont(ofSize: 26); dots.alignment = .center
        status.font = .monospacedSystemFont(ofSize: 11, weight: .regular); status.textColor = NSColor(white: 0.6, alpha: 1)
        status.lineBreakMode = .byTruncatingTail
        for v in [eyes, preview, modeLabel, toast, burst, word, dots, status] { addSubview(v) }
        cycle = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in self?.naturalTick() }
    }

    required init?(coder: NSCoder) { nil }

    override func layout() {
        super.layout()
        let w = bounds.width, h = bounds.height
        modeLabel.frame = NSRect(x: 14, y: h - 36, width: 160, height: 28)
        burst.frame = NSRect(x: w - 220, y: h - 34, width: 210, height: 24); burst.alignment = .right
        toast.frame = NSRect(x: 170, y: h - 34, width: w - 400, height: 26)
        eyes.frame = NSRect(x: gazeX, y: 70, width: w, height: h - 120)
        preview.frame = NSRect(x: w - 170, y: 76, width: 160, height: 120)
        word.frame = NSRect(x: 10, y: 52, width: w - 20, height: 42)
        dots.frame = NSRect(x: 10, y: 22, width: w - 20, height: 32)
        status.frame = NSRect(x: 8, y: 2, width: w - 16, height: 16)
    }

    // ---- input: click = tap, double click = double tap (the single waits out the double-click interval) ----
    override func mouseDown(with e: NSEvent) {
        if e.clickCount >= 2 {
            singleTap?.cancel(); singleTap = nil
            onTap?(true)
        } else {
            let job = DispatchWorkItem { [weak self] in self?.onTap?(false) }
            singleTap = job
            DispatchQueue.main.asyncAfter(deadline: .now() + NSEvent.doubleClickInterval, execute: job)
        }
    }

    override var acceptsFirstResponder: Bool { true }

    // ---- eyes ----
    private func gif(_ state: String) -> NSImage? {
        guard let dir = eyesDir else { return nil }
        return NSImage(contentsOf: dir.appendingPathComponent(style).appendingPathComponent("\(state).gif"))
    }

    func showIdle() { playing = nil; eyes.image = gif("idle"); eyes.animates = true }

    /// Play one expression for about two loops of its 8 frames, then back to idle.
    func play(_ state: String, seconds: Double = 2.2) {
        guard let g = gif(state) else { return }
        playing = state
        eyes.image = g
        eyes.animates = true
        DispatchQueue.main.asyncAfter(deadline: .now() + seconds) { [weak self] in
            if self?.playing == state { self?.showIdle() }
        }
    }

    private var nextNatural = Date().addingTimeInterval(4)
    private func naturalTick() {          // the robot's natural cycle: 2-7 s, a weighted random expression, then idle
        guard playing == nil, Date() >= nextNatural else { return }
        nextNatural = Date().addingTimeInterval(Double.random(in: 2...7) + 2)
        play(Self.lively.randomElement()!, seconds: Double.random(in: 1.2...2.4))
    }

    /// The robot's gestures without servos: the eyes move instead.
    private var gesturing = false
    func gesture(_ kind: String) {
        gesturing = true
        let base = NSRect(x: gazeX, y: 70, width: bounds.width, height: bounds.height - 120)
        let steps: [CGFloat]
        switch kind {
        case "nod": steps = [0, -18, 0, -18, 0]
        case "shake": steps = [0, -24, 24, -24, 24, 0]
        case "left": steps = [0, -60, -60, -60, 0]
        case "right": steps = [0, 60, 60, 60, 0]
        default: steps = [0, 30, -30, 0]
        }
        for (i, d) in steps.enumerated() {
            DispatchQueue.main.asyncAfter(deadline: .now() + Double(i) * 0.15) { [weak self] in
                guard let self else { return }
                self.eyes.frame = kind == "nod" ? base.offsetBy(dx: 0, dy: d) : base.offsetBy(dx: d, dy: 0)
                if i == steps.count - 1 { self.gesturing = false; self.needsLayout = true }   // back to the gaze
            }
        }
    }

    // ---- gaze: no servos, so the eyes lean toward the face the camera sees (the robot's tracking stand-in) ----
    private var gazeX: CGFloat = 0
    /// x: the face centre in the camera's 640 px width, nil = nobody: back to the middle. mirror: flip left / right.
    func gaze(at x: CGFloat?, mirror: Bool) {
        var target: CGFloat = 0
        if let x { target = max(-1, min(1, (x - 320) / 320)) * 40 * (mirror ? 1 : -1) }
        guard !gesturing, abs(target - gazeX) > 3 else { return }
        gazeX = target
        NSAnimationContext.runAnimationGroup { c in
            c.duration = 0.35
            eyes.animator().frame = NSRect(x: gazeX, y: 70, width: bounds.width, height: bounds.height - 120)
        }
    }

    // ---- rows ----
    func setMode(_ label: String, colour: UInt32) {
        modeLabel.stringValue = label
        modeLabel.textColor = NSColor(red: CGFloat((colour >> 16) & 255) / 255, green: CGFloat((colour >> 8) & 255) / 255,
                                      blue: CGFloat(colour & 255) / 255, alpha: 1)
    }

    func showWord(_ text: String, holdMs: Int) {
        wordTimer?.invalidate()
        word.stringValue = text
        guard !text.isEmpty, holdMs > 0 else { return }
        wordTimer = Timer.scheduledTimer(withTimeInterval: Double(holdMs) / 1000, repeats: false) { [weak self] _ in
            self?.word.stringValue = ""
        }
    }

    func showToast(_ text: String) {
        toastTimer?.invalidate()
        toast.stringValue = text
        toastTimer = Timer.scheduledTimer(withTimeInterval: 2.5, repeats: false) { [weak self] _ in self?.toast.stringValue = "" }
    }

    /// chat_stage: listen (blue) think (amber) tool (amber, steady) skip / aside (grey) busy (red); "" clears.
    func setStage(_ stage: String) {
        let (txt, col): (String, NSColor) = {
            switch stage {
            case "listen": return ("● ● ●", .systemBlue)
            case "think": return ("● ● ●", .systemOrange)
            case "tool": return ("◆ ◆ ◆", .systemOrange)
            case "skip", "aside": return ("· · ·", .gray)
            case "busy": return ("● ● ●", .systemRed)
            default: return ("", .white)
            }
        }()
        dots.stringValue = txt
        dots.textColor = col
        if !txt.isEmpty {
            DispatchQueue.main.asyncAfter(deadline: .now() + 8) { [weak self] in
                if self?.dots.stringValue == txt { self?.dots.stringValue = "" }
            }
        }
    }

    func setBurst(on: Bool, leftS: Int, looks: Int) { burst.stringValue = on ? "👁 \(looks) · \(leftS) s" : "" }
    func setStatus(_ s: String) { status.stringValue = s }
    func setPreview(_ cg: CGImage?) {
        preview.isHidden = cg == nil
        if let cg { preview.image = NSImage(cgImage: cg, size: NSSize(width: cg.width, height: cg.height)) }
    }
}
