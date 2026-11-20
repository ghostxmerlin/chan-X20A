import Foundation

/// The robot's voice module on the Mac. Since 2026-11-20 (owner: Providence) that module is the owner's StickS3
/// (~/exia/stick/docs/voice-protocol.md, FW/asr_protocol.h): only its own eight English words, matched against a short
/// on-device transcript. A word counts only when the whole utterance is that word (a word inside a sentence is talk).
/// providence (the robot's name) = being called (asr_wake); the other seven are heard words, logged only.
public enum AsrWords {
    public enum Kind: Equatable {
        case wake                       // providence -> asr_wake (the server opens a chat)
        case heard                      // the Stick's other words: reported, no action
    }

    public static let words: [String] = ["docking sequence", "o raiser", "inevitable", "providence", "say what again",
                                         "inception", "expelliarmus", "expecto patronum"]

    /// "Providence." / "Say what again?" -> the lower-case word with single spaces, punctuation dropped.
    public static func normalise(_ text: String) -> String {
        let t = text.lowercased()
        let kept = t.unicodeScalars.map { CharacterSet.letters.contains($0) || $0 == " " ? Character($0) : " " }
        return String(kept).split(separator: " ").joined(separator: " ")
    }

    public static func match(_ text: String) -> (word: String, kind: Kind)? {
        let w = normalise(text)
        guard words.contains(w) else { return nil }
        return (w, kind(w))
    }

    public static func kind(_ w: String) -> Kind { w == "providence" ? .wake : .heard }
}
