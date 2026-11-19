import Foundation

/// hello's mode rules: a straight port of FW/mode_rules.h (itself the twin of server/mind/modes.py + mode_table.py).
/// The contract is tests/data/mode_vectors.json: HelloSelfTest replays every vector (as tests/mode_rules_test.cpp does).
public enum ModeRules {
    public enum Mode: Int, CaseIterable { case talk, chat, settings, event, subtitle, crowd, companion, idle
        public var id: String { ["talk", "chat", "settings", "event", "subtitle", "crowd", "companion", "idle"][rawValue] }
        public init?(id: String?) {
            guard let id, let m = Mode.allCases.first(where: { $0.id == id }) else { return nil }
            self = m
        }
    }
    public enum Dir: String { case zhEn = "zh_en", enZh = "en_zh" }
    public enum Input: String, CaseIterable {
        case forward, backward, play, pause, open, close, start, stop, next, previous, digit, tap
        case doubleTap = "double_tap", long, direction, select, boot, timeout
    }
    public enum Code: String { case ok, already, notInMode = "not_in_mode", onlyTalk = "only_talk",
                               notImplemented = "not_implemented", noAction = "no_action", unknownInput = "unknown_input" }
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
        public var dir: Dir? = nil
        public var badDir = false
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
                if let o = v as? [String: Any] {
                    out.target = Mode(id: o["mode"] as? String)
                    let d = o["dir"]
                    if d == nil || d is NSNull { out.dir = nil }
                    else if let s = d as? String, let dd = Dir(rawValue: s) { out.dir = dd }
                    else { out.badDir = true }
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

    struct Meaning {
        var codeOnly = false
        var code: Code = .ok
        var effect: Effect = .sayRound
        var touch = 0               // crowd: 1 listen (tap), 2 quiet (double tap)
        static func codeOf(_ c: Code) -> Meaning { Meaning(codeOnly: true, code: c) }
        static func fx(_ e: Effect) -> Meaning { Meaning(codeOnly: false, effect: e) }
        static let listen = Meaning(codeOnly: true, code: .ok, touch: 1)
        static let quiet = Meaning(codeOnly: true, code: .ok, touch: 2)
    }

    public struct ModeDef {
        public let label: String
        public let color: UInt32
        public let eye: String
        public let tap: TouchAct, dbl: TouchAct
        let forward: Meaning?, tapIn: Meaning?, doubleIn: Meaning?, next: Meaning?, previous: Meaning?
    }

    public static let defs: [ModeDef] = [
        ModeDef(label: "TALK", color: 0xE040A0, eye: "laughing_man", tap: .server, dbl: .server,
                forward: .fx(.sayRound), tapIn: .fx(.sayRound), doubleIn: .fx(.prefetchOn), next: .fx(.prefetchOn),
                previous: .fx(.prefetchOff)),
        ModeDef(label: "CHAT", color: 0xF0B000, eye: "v05", tap: .server, dbl: .server,
                forward: nil, tapIn: .codeOf(.notImplemented), doubleIn: .codeOf(.notImplemented), next: nil, previous: nil),
        ModeDef(label: "SET", color: 0xD8D8CC, eye: "page_settings", tap: .none, dbl: .none,
                forward: nil, tapIn: .codeOf(.noAction), doubleIn: .codeOf(.noAction), next: nil, previous: nil),
        ModeDef(label: "AUTO", color: 0x20B8C8, eye: "page_idle", tap: .none, dbl: .none,
                forward: nil, tapIn: .codeOf(.noAction), doubleIn: .codeOf(.noAction), next: nil, previous: nil),
        ModeDef(label: "SUB", color: 0x7C6CF0, eye: "page_subtitle", tap: .none, dbl: .none,
                forward: nil, tapIn: .codeOf(.noAction), doubleIn: .codeOf(.noAction), next: nil, previous: nil),
        ModeDef(label: "DOCK", color: 0x30D060, eye: "v14", tap: .server, dbl: .server,
                forward: nil, tapIn: .listen, doubleIn: .quiet, next: nil, previous: nil),
        ModeDef(label: "PAL", color: 0xF2E6C8, eye: "v18", tap: .server, dbl: .none,
                forward: nil, tapIn: .codeOf(.notImplemented), doubleIn: .codeOf(.noAction), next: nil, previous: nil),
        ModeDef(label: "IDLE", color: 0x505050, eye: "page_idle", tap: .badge, dbl: .server,
                forward: nil, tapIn: .codeOf(.noAction), doubleIn: .codeOf(.notImplemented), next: nil, previous: nil),
    ]
    public static func def(_ m: Mode) -> ModeDef { defs[m.rawValue] }

    public static func label(_ s: State) -> String {
        if s.mode == .subtitle { return s.dir == .zhEn ? "SUB ZH>EN" : s.dir == .enZh ? "SUB EN>ZH" : "SUB" }
        return def(s.mode).label
    }

    static let codeToast: [Code: String] = [.ok: "OK", .already: "ALREADY", .notInMode: "NOT IN ", .onlyTalk: "ONLY IN TALK",
                                            .notImplemented: "NOT YET", .noAction: "NO ACTION", .unknownInput: "NO EFFECT"]

    static func same(_ st: State, _ c: Code, named: Mode? = nil) -> Outcome {
        var o = Outcome(state: st, code: c)
        o.toast = c == .notInMode ? codeToast[c]! + def(named!).label : codeToast[c]!
        return o
    }

    static func done(_ old: State, _ nwIn: State, _ fx: [Effect] = []) -> Outcome {
        var nw = nwIn
        if nw.mode != .settings { nw.returnTo = nil }
        if nw.mode != .subtitle && !(nw.mode == .settings && nw.returnTo == .subtitle) { nw.dir = nil }
        var o = Outcome(state: nw)
        if nw.mode != old.mode {
            o.add(.stopSpeech)
            if old.mode == .talk { o.add(.talkLeave) }
            if nw.mode == .talk { o.add(.talkEnter) }
        }
        for e in fx { o.add(e) }
        if nw.mode != old.mode || nw.dir != old.dir { o.toast = label(nw) }
        else if let last = o.effects.last {
            switch last {
            case .sayRound: o.toast = "SAYING"
            case .prefetchOn: o.toast = "PREFETCH ON"
            case .prefetchOff: o.toast = "PREFETCH OFF"
            case .stopSpeech: o.toast = "STOP"
            case .talkEnter: o.toast = "TALK"
            default: o.toast = "OK"
            }
        } else { o.toast = "OK" }
        return o
    }

    static func plain(_ m: Mode, _ d: Dir? = nil) -> State { State(mode: m, dir: m == .subtitle ? d : nil) }

    static func enter(_ st: State, _ target: Mode, _ d: Dir?) -> Outcome {
        var nw = plain(target, d)
        if target == .settings {
            nw.returnTo = st.mode == .event ? .idle : st.mode
            nw.dir = st.mode == .subtitle ? st.dir : nil
        }
        return done(st, nw)
    }

    static func leave(_ st: State) -> Outcome {
        let nw: State
        if st.mode == .settings { nw = plain(st.returnTo ?? .idle, st.dir) }
        else if st.hosted { nw = plain(.event) }
        else { nw = plain(.idle) }
        if nw.mode == st.mode && nw.dir == st.dir { return same(st, .already) }
        return done(st, nw)
    }

    static func leaveHost(_ st: State) -> Outcome { done(st, plain(.idle)) }
    static func switchDir(_ st: State, _ d: Dir) -> Outcome { var nw = st; nw.dir = d; return done(st, nw) }

    static func meaning(_ m: ModeDef, _ input: Input) -> Meaning? {
        switch input {
        case .forward: return m.forward
        case .tap: return m.tapIn
        case .doubleTap: return m.doubleIn
        case .next: return m.next
        case .previous: return m.previous
        default: return nil
        }
    }

    /// modes.transition(state, input, value, speaking) plus the digit table. Pure. input nil = an unknown input id.
    public static func transition(_ st: State, _ input: Input?, _ v: Value, speaking: Bool) -> Outcome {
        guard let input else { return same(st, .unknownInput) }
        let m = def(st.mode)
        if input == .boot { return done(st, plain(.crowd), [.stopSpeech]) }
        if input == .long { return done(st, plain(.talk), [.stopSpeech]) }
        if input == .digit {
            if (1...3).contains(v.digit) { return same(st, .noAction) }
            if v.digit < 4 || v.digit > 8 { return same(st, .unknownInput) }
            let targets: [Mode] = [.idle, .crowd, .companion, .subtitle, .subtitle]
            let dirs: [Dir?] = [nil, nil, nil, .zhEn, .enZh]
            var sel = Value()
            sel.target = targets[v.digit - 4]; sel.dir = dirs[v.digit - 4]
            if st.mode == sel.target && st.dir == sel.dir { return same(st, .already) }
            return transition(st, .select, sel, speaking: speaking)
        }
        if let mean = meaning(m, input) {
            if mean.touch != 0 {
                var o = Outcome(state: st, code: .ok)
                if speaking { o.add(.stopSpeech) }
                o.toast = mean.touch == 2 ? "QUIET" : speaking ? "STOP" : "LISTEN"
                return o
            }
            if input == .tap && speaking && m.tap == .server { return done(st, st, [.stopSpeech]) }
            if mean.codeOnly { return same(st, mean.code) }
            return done(st, st, [mean.effect])
        }
        if input == .next || input == .previous { return same(st, .onlyTalk) }
        if input == .select {
            guard let target = v.target, !(target == .subtitle && v.badDir) else { return same(st, .unknownInput) }
            if st.hosted && target == .event { return leaveHost(st) }
            if target == st.mode {
                if target == .subtitle, let d = v.dir, d != st.dir { return switchDir(st, d) }
                return leave(st)
            }
            return enter(st, target, v.dir ?? .zhEn)
        }
        if input == .direction {
            let owners: [Mode] = [.subtitle, .subtitle, .crowd, .companion]
            let dirs: [Dir?] = [.zhEn, .enZh, nil, nil]
            guard (0...3).contains(v.direction) else { return same(st, .unknownInput) }
            let owner = owners[v.direction], d = dirs[v.direction]
            if owner == st.mode {
                if d == nil || d == st.dir { return leave(st) }
                return switchDir(st, d!)
            }
            return enter(st, owner, d)
        }
        let words: [(Input, Mode, Bool)] = [(.forward, .talk, true), (.backward, .talk, false), (.play, .chat, true),
                                            (.pause, .chat, false), (.open, .settings, true), (.close, .settings, false),
                                            (.timeout, .settings, false), (.start, .event, true), (.stop, .event, false)]
        for (win, owner, isEnter) in words where win == input {
            let hostIsOwner = st.hosted && owner == .event
            if isEnter {
                if owner == st.mode || hostIsOwner { return same(st, .already) }
                return enter(st, owner, nil)
            }
            if owner == st.mode { return leave(st) }
            if hostIsOwner { return leaveHost(st) }
            return same(st, .notInMode, named: owner)
        }
        return same(st, .unknownInput)
    }
}
