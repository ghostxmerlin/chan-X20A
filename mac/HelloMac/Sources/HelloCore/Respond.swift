import Foundation

/// The robot's own answer to a heard sentence (FW/hearing.cpp:185-211, FW/hearing_policy.h): with 嘴 on 录音 (the ear's
/// `respond`), a voice clip of the sentence's emotion, held until the room is quiet; otherwise the emotion on the eyes.
public enum Respond {
    /// Backend emotion -> eye state (FW/eye_mode.h); neutral / unknown show nothing.
    public static func eye(for emotion: String) -> String? {
        ["happy": "happy", "sad": "sad", "angry": "angry", "fearful": "scared", "disgusted": "refusing",
         "surprised": "surprised"][emotion]
    }

    /// Uniform among the clips of that label, avoiding `last` when another has it (emotion_clip).
    public static func clip(emotion: String, random: UInt32, tags: [Int: String], last: Int) -> Int {
        let ids = tags.keys.sorted().filter { tags[$0] == emotion }
        let others = ids.filter { $0 != last }
        if others.isEmpty { return ids.contains(last) ? last : 0 }
        return others[Int(random % UInt32(others.count))]
    }
}

/// ResponsePolicy: one answer per sentence id, not for our own voice or stale results, at most one per 10 s; happy
/// accumulates (the first in a 5-minute window, then every second one).
public struct ResponsePolicy {
    public var seen: UInt32 = 0
    public var lastResponse: UInt64 = 0
    public var lastClip = 0
    var happyWindow: UInt64 = 0, happyCount = 0, happyNeeded = 1
    public init() {}

    public mutating func consider(id: UInt32, emotion: String, speech: Bool, selfSounding: Bool, ageMs: Int,
                                  now: UInt64, random: UInt32, tags: [Int: String]) -> Int {
        guard id > seen else { return 0 }
        seen = id
        guard speech, !selfSounding, ageMs >= 0, ageMs <= 5000 else { return 0 }
        let happy = emotion == "happy"
        if happy {
            if happyWindow == 0 || now - happyWindow >= 300_000_000 { happyWindow = now; happyCount = 0; happyNeeded = 1 }
            happyCount += 1
            if happyCount < happyNeeded { return 0 }
        }
        if lastResponse != 0 && now - lastResponse < 10_000_000 { return 0 }
        let c = Respond.clip(emotion: emotion, random: random, tags: tags, last: lastClip)
        if c != 0 { lastClip = c; if happy { happyCount = 0; happyNeeded = 2 } }
        return c
    }

    public mutating func accepted(now: UInt64) { lastResponse = now }

    /// a new hearing session (the ear's ids restart): seen starts over, the 10 s gap and the happy count stay
    public mutating func newSession() { seen = 0 }
}

/// QuietWait: a chosen clip waits for 1.5 s without speech (the ear's VAD flag on every tick) and is dropped after 10 s.
public struct QuietWait {
    public enum Result { case wait, play, expired }
    public private(set) var clip = 0
    public private(set) var chosen: UInt64 = 0
    public init() {}
    public var pending: Bool { clip != 0 }
    public mutating func hold(_ id: Int, now: UInt64) { clip = id; chosen = now }
    public mutating func clear() { clip = 0 }

    public mutating func poll(now: UInt64, speaking: Bool, lastSpeech: UInt64) -> Result {
        guard clip != 0 else { return .wait }
        if now - chosen >= 10_000_000 { clip = 0; return .expired }
        let quietSince = max(chosen, lastSpeech)
        return !speaking && now - quietSince >= 1_500_000 ? .play : .wait
    }
}
