import Foundation

/// The robot's voice module (Unit ASR, FW/asr_protocol.h:51-98, FW/asr_mode_words.h) on the Mac: the same English
/// command words, matched against a short on-device transcript. A word counts only when the whole utterance is that
/// word (a word inside a sentence is talk, not an order).
public enum AsrWords {
    public enum Kind: Equatable {
        case wake                       // hello / hi ASR / Hi M Five -> asr_wake
        case mode(ModeRules.Input)      // forward backward play pause open close start stop next previous
        case digit(Int)                 // zero..nine
        case motion(String)             // up down left right turn left turn right front back (local, eyes)
        case volume(String)             // increase / decrease / maximum / medium / minimum
        case other                      // ok, turn on / off, the lights, firmware version: asr_order only
    }

    public static let words: [String] = ["up", "down", "left", "turn left", "right", "turn right", "forward", "front",
        "backward", "back", "open", "close", "start", "stop", "turn on", "turn off", "play", "pause", "turn on the lights",
        "turn off the lights", "previous", "next", "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
        "nine", "ok", "hi asr", "hello", "increase volume", "decrease volume", "maximum volume", "medium volume",
        "minimum volume", "check firmware version", "hi m five"]

    static let digitNames = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]

    /// "Hello." / "Turn left!" / "9" / "hi, A.S.R." -> the module's word, or nil.
    public static func normalise(_ text: String) -> String {
        var t = text.lowercased()
        for (d, n) in digitNames.enumerated() { if t.trimmingCharacters(in: .whitespacesAndNewlines) == "\(d)" { t = n } }
        t = t.replacingOccurrences(of: "a.s.r.", with: "asr").replacingOccurrences(of: "a s r", with: "asr")
            .replacingOccurrences(of: "okay", with: "ok").replacingOccurrences(of: "m5", with: "m five")
        let kept = t.unicodeScalars.map { CharacterSet.letters.contains($0) || $0 == " " ? Character($0) : " " }
        return String(kept).split(separator: " ").joined(separator: " ")
    }

    public static func match(_ text: String) -> (word: String, kind: Kind)? {
        let w = normalise(text)
        guard words.contains(w) else { return nil }
        return (w, kind(w))
    }

    public static func kind(_ w: String) -> Kind {
        switch w {
        case "hello", "hi asr", "hi m five": return .wake
        case "forward", "backward", "play", "pause", "open", "close", "start", "stop", "next", "previous":
            return .mode(ModeRules.Input(rawValue: w)!)
        case let d where digitNames.contains(d): return .digit(digitNames.firstIndex(of: d)!)
        case "up", "down", "left", "right", "turn left", "turn right", "front", "back": return .motion(w)
        case let v where v.hasSuffix("volume"): return .volume(String(v.split(separator: " ")[0]))
        default: return .other
        }
    }
}
