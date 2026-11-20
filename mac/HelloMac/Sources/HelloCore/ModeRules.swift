import Foundation

/// hello's mode rules: a straight port of FW/mode_rules.h (itself the twin of server/mind/modes.py + mode_table.py).
/// The contract is tests/data/mode_vectors.json: HelloSelfTest replays every vector (as tests/mode_rules_test.cpp does).
public enum ModeRules {
    // Docking (crowd) is the only mode since 2026-11-20 (owner: Providence), as in FW/mode_rules.h and mode_table.py.
    public enum Mode: Int, CaseIterable { case crowd
        public var id: String { ["crowd"][rawValue] }
        public init?(id: String?) {
            guard let id, let m = Mode.allCases.first(where: { $0.id == id }) else { return nil }
            self = m
        }
    }
    /// The modes removed on 2026-11-20: a select names one -> removed_mode.
    public static let removedModes: Set<String> = ["talk", "chat", "settings", "event", "subtitle", "companion", "idle"]
    public enum Dir: String { case zhEn = "zh_en", enZh = "en_zh" }   // kept for the wire (dir is always nil now)
    public enum Input: String, CaseIterable {
        case forward, backward, play, pause, open, close, start, stop, next, previous, digit, tap
        case doubleTap = "double_tap", long, direction, select, boot, timeout
    }
    public enum Code: String { case ok, already, notInMode = "not_in_mode", onlyTalk = "only_talk",
                               notImplemented = "not_implemented", noAction = "no_action", unknownInput = "unknown_input",
                               removedMode = "removed_mode" }
    public enum Effect: String { case stopSpeech = "stop_speech", talkLeave = "talk_leave", talkEnter = "talk_enter",
                                 sayRound = "say_round", prefetchOn = "prefetch_on", prefetchOff = "prefetch_off" }
    public enum TouchAct { case none, server, badge }

    public struct State: Equatable {
        public var mode: Mode = .crowd
        public var dir: Dir? = nil
        public var hosted = false
        public var returnTo: Mode? = nil
        public init(mode: Mode = .crowd, dir: Dir? = nil, hosted: Bool = false, returnTo: Mode? = nil) {
            self.mode = mode; self.dir = dir; self.hosted = hosted; self.returnTo = returnTo
        }
    }

    public struct Value {
        public var digit = -1
        public var direction = -1
        public var target: Mode? = nil
        public var removed = false
        public init() {}
        public static func from(_ input: Input?, _ v: Any?) -> Value {
            var out = Value()
            switch input {
            case .digit?:   // a JSON number, not true / false (NSNumber 1 also casts to Bool)
                if let n = v as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID(), n.doubleValue == Double(n.intValue),
                   (0...9).contains(n.intValue) { out.digit = n.intValue }
            case .direction?:
                if let s = v as? String, let i = ["up", "down", "left", "right"].firstIndex(of: s) { out.direction = i }
            case .select?:
                if let o = v as? [String: Any], let name = o["mode"] as? String {
                    out.target = Mode(id: name)
                    out.removed = removedModes.contains(name)
                }
            default: break
            }
            return out
        }
    }

    public struct Outcome {
        public var state: State
        public var code: Code = .ok
        public var toast = ""
        public var effects: [Effect] = []
        mutating func add(_ e: Effect) { if !effects.contains(e) && effects.count < 6 { effects.append(e) } }
    }

    public struct ModeDef {
        public let label: String
        public let color: UInt32
        public let eye: String
        public let tap: TouchAct, dbl: TouchAct
    }

    public static let defs: [ModeDef] = [
        ModeDef(label: "DOCK", color: 0x30D060, eye: "v14", tap: .server, dbl: .server),
    ]
    public static func def(_ m: Mode) -> ModeDef { defs[m.rawValue] }
    public static func label(_ s: State) -> String { def(s.mode).label }

    static let codeToast: [Code: String] = [.ok: "OK", .already: "ALREADY", .notInMode: "NOT IN", .onlyTalk: "ONLY IN TALK",
                                            .notImplemented: "NOT YET", .noAction: "NO ACTION", .unknownInput: "NO EFFECT",
                                            .removedMode: "DOCKING ONLY"]

    static func same(_ st: State, _ c: Code) -> Outcome {
        var o = Outcome(state: st, code: c)
        o.toast = codeToast[c]!
        return o
    }

    /// modes.transition(state, input, value, speaking) plus the digit table. Pure. input nil = an unknown input id.
    public static func transition(_ st: State, _ input: Input?, _ v: Value, speaking: Bool) -> Outcome {
        guard let input else { return same(st, .unknownInput) }
        switch input {
        case .boot, .long:                         // land in Docking, stop speech
            var o = Outcome(state: State())
            o.add(.stopSpeech)
            o.toast = st.mode == o.state.mode ? "STOP" : label(o.state)
            return o
        case .digit:                               // 5 = Docking (already), the other 1-8 do nothing; 0 / 9 are mind's
            if v.digit == 5 { return same(st, .already) }
            return same(st, (1...8).contains(v.digit) ? .noAction : .unknownInput)
        case .tap, .doubleTap:                     // Docking's touch: listen (stop while speaking) / quiet
            var o = Outcome(state: st, code: .ok)
            if speaking { o.add(.stopSpeech) }
            o.toast = input == .doubleTap ? "QUIET" : speaking ? "STOP" : "LISTEN"
            return o
        case .next, .previous:
            return same(st, .onlyTalk)
        case .select:
            if v.removed { return same(st, .removedMode) }
            return same(st, v.target == nil ? .unknownInput : .already)
        case .direction:                           // left was Docking's own direction
            return same(st, v.direction == 2 ? .already : .unknownInput)
        case .forward, .backward, .play, .pause, .open, .close, .start, .stop:
            return same(st, .noAction)
        case .timeout:
            return same(st, .unknownInput)
        }
    }
}
