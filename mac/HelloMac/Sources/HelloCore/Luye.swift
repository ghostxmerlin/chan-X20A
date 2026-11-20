import Foundation

/// 鹿野 as hello's face on the Mac (owner 2026-11-20: the sprite set they made, frames/<action>/NN.png + the
/// animation-manifest.json; the art stays in .private, never in the repo). Pure rules, no drawing:
/// - hello's states -> her actions: an eye expression (the server's `eyes`, a heard sentence's emotion, the natural
///   cycle) or the chat stage picks one of idle, waving, jumping (the punch-kick combo), failed (the full-body clear
///   shield), waiting, running (shaping metal: working), review (sensing), running-left / -right;
/// - the face the camera sees -> one of her 16 look frames (0 = up, clockwise 22.5 deg), nil near the middle;
/// - the robot's gestures without servos -> short look sequences (nod = down / up, shake = left / right).
public enum Luye {
    public struct Clip: Equatable {
        public let name: String
        public let durationsMs: [Int]
        public var frames: Int { durationsMs.count }
        public var totalMs: Int { durationsMs.reduce(0, +) }
    }

    public struct Manifest {
        public let clips: [String: Clip]
        public let looks: [Double]                 // degrees, clockwise from screen-up, in frames/look order
    }

    public static func parse(_ data: Data) -> Manifest? {
        guard let o = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let anims = o["animations"] as? [[String: Any]] else { return nil }
        var clips: [String: Clip] = [:]
        for a in anims {
            guard let name = a["state"] as? String, let d = a["durationMs"] as? [Int], !d.isEmpty,
                  d.allSatisfy({ $0 > 0 && $0 < 5000 }) else { continue }
            clips[name] = Clip(name: name, durationsMs: d)
        }
        let looks = (o["lookDirectionsClockwise"] as? [Double]) ?? (o["lookDirectionsClockwise"] as? [Int])?.map(Double.init) ?? []
        guard clips["idle"] != nil else { return nil }
        return Manifest(clips: clips, looks: looks)
    }

    /// An eye expression -> her action (FaceView's lively list, Respond.eye's six, the server's eyes command).
    public static func action(forEye e: String) -> String {
        switch e {
        case "greeting", "happy", "laughing", "grateful", "affectionate", "encouraging": return "waving"
        case "angry", "proud", "goofy": return "jumping"
        case "scared", "refusing": return "failed"
        case "curious", "surprised", "confused": return "review"
        case "thinking": return "running"
        case "expectant", "shy", "comforting", "relieved": return "waiting"
        default: return "idle"
        }
    }

    /// The chat stage dots -> what she does while they show: listening = sensing, thinking / a tool = shaping metal,
    /// busy = the shield; nil = back to idle (or looking at the face).
    public static func action(forStage s: String) -> String? {
        switch s {
        case "listen": return "review"
        case "think", "tool": return "running"
        case "busy": return "failed"
        default: return nil
        }
    }

    /// The face centre in the camera's 640x480 (top-left origin) -> a look frame index, nil near the middle or nobody.
    /// mirror flips left / right like the eyes' gaze; up / down counts less (people sit above the camera's middle).
    public static func look(x: Double?, y: Double?, mirror: Bool, directions n: Int = 16, deadZone: Double = 0.2) -> Int? {
        guard let x, n > 0 else { return nil }
        let dx = (x - 320) / 320 * (mirror ? 1 : -1)
        let dy = ((y ?? 240) - 240) / 240 * 0.6
        guard (dx * dx + dy * dy).squareRoot() >= deadZone else { return nil }
        var deg = atan2(dx, -dy) * 180 / .pi            // 0 = up, 90 = right (screen)
        if deg < 0 { deg += 360 }
        let step = 360 / Double(n)
        return Int((deg / step).rounded()) % n
    }

    /// The look index nearest a direction in degrees (0 up, 90 right, 180 down, 270 left).
    public static func lookIndex(_ deg: Double, directions n: Int = 16) -> Int {
        Int((deg.truncatingRemainder(dividingBy: 360) / (360 / Double(n))).rounded()) % n
    }

    /// A gesture without servos: (look index or nil = straight ahead, seconds) steps.
    public static func gesture(_ kind: String) -> [(look: Int?, s: Double)] {
        let down = lookIndex(180), left = lookIndex(270), right = lookIndex(90)
        switch kind {
        case "nod": return [(down, 0.25), (nil, 0.2), (down, 0.25), (nil, 0.2)]
        case "shake": return [(left, 0.22), (right, 0.22), (left, 0.22), (right, 0.22), (nil, 0.2)]
        case "left": return [(left, 0.9), (nil, 0.1)]
        case "right": return [(right, 0.9), (nil, 0.1)]
        default: return [(left, 0.3), (right, 0.3), (nil, 0.2)]
        }
    }

    /// Her own small moments when nothing happens: every 10-25 s, mostly sensing or waiting, sometimes a wave.
    public static let natural = ["review", "review", "waiting", "waiting", "waving"]
    public static let naturalEvery: ClosedRange<Double> = 10...25
}
