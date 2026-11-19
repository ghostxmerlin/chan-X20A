import CoreGraphics
import Foundation
import HelloCore

// HelloSelfTest: the package's tests as an executable (Command Line Tools have no XCTest runner).
//   swift run HelloSelfTest [repo root]      exit 0 = all passed
var failures = 0, checks = 0
func check(_ ok: Bool, _ what: @autoclosure () -> String, file: String = #file, line: Int = #line) {
    checks += 1
    if !ok { failures += 1; print("FAIL \((file as NSString).lastPathComponent):\(line) \(what())") }
}
var tests: [(String, () throws -> Void)] = []
func test(_ name: String, _ body: @escaping () throws -> Void) { tests.append((name, body)) }

let root = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] :
    URL(fileURLWithPath: #file).deletingLastPathComponent().appendingPathComponent("../../../..").standardized.path

// ---- mode rules: every vector of tests/data/mode_vectors.json ----
test("mode rules replay every vector") {
    let url = URL(fileURLWithPath: root).appendingPathComponent("tests/data/mode_vectors.json")
    let doc = try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as! [String: Any]
    let vectors = doc["vectors"] as! [[Any]]
    func state(_ a: [Any]) -> ModeRules.State {
        ModeRules.State(mode: ModeRules.Mode(id: a[0] as? String)!, dir: (a[1] as? String).flatMap { ModeRules.Dir(rawValue: $0) },
                        hosted: (a[2] as? String) == "event", returnTo: ModeRules.Mode(id: a[3] as? String))
    }
    var bad = 0
    for v in vectors {
        let st = state(v[0] as! [Any])
        let input = ModeRules.Input(rawValue: v[1] as! String)
        let value = ModeRules.Value.from(input, v[2] is NSNull ? nil : v[2])
        let o = ModeRules.transition(st, input, value, speaking: v[3] as! Bool)
        let want = state(v[4] as! [Any])
        let effects = (v[7] as! [String])
        if o.state != want || o.code.rawValue != v[5] as! String || o.toast != v[6] as! String ||
            o.effects.map(\.rawValue) != effects {
            bad += 1
            if bad <= 6 { let j = (try? JSONSerialization.data(withJSONObject: v)).flatMap { String(data: $0, encoding: .utf8) } ?? "?"; print("  vector \(j)\n    got \(o.state.mode.id) \(o.state.dir?.rawValue ?? "-") \(o.code.rawValue) \(o.toast) \(o.effects.map(\.rawValue))") }
        }
    }
    check(bad == 0, "\(bad) of \(vectors.count) vectors differ")
    check(vectors.count > 2000, "vectors: \(vectors.count)")
}

test("mode table labels and colours match the vectors' table") {
    let url = URL(fileURLWithPath: root).appendingPathComponent("tests/data/mode_vectors.json")
    let doc = try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as! [String: Any]
    let table = doc["table"] as! [String: [String: Any]]
    for m in ModeRules.Mode.allCases {
        let t = table[m.id]!
        check(ModeRules.def(m).label == t["label"] as! String, "label \(m.id)")
        check(String(format: "%06X", ModeRules.def(m).color) == t["color"] as! String, "colour \(m.id)")
    }
}

// ---- opus and framing ----
test("opus 24 kHz 60 ms round trip") {
    let enc = OpusEncoder()!, dec = OpusDecoder()!
    let pcm = (0..<OpusEncoder.frameSamples).map { Int16(8000 * sin(Double($0) * 2 * .pi * 440 / 24000)) }
    let pkt = enc.encode(pcm)!
    check(pkt.count > 10 && pkt.count < 768, "packet size \(pkt.count)")
    let out = dec.decode(Data(pkt))!
    check(out.count == 1440, "decoded \(out.count)")
    check(enc.encode(Array(pcm.prefix(100))) == nil, "a short frame is refused")
}

test("SEA1 / SPK1 / SVF1 framing") {
    let d = Wire.sea1(seq: 7, captureUs: 0x0102030405, opus: [9, 8])
    check(d.count == 18 && d.prefix(4) == Data("SEA1".utf8), "sea1 size")
    check([UInt8](d)[4] == 7 && [UInt8](d)[8] == 5 && [UInt8](d)[12] == 1, "sea1 little endian")
    let s = Wire.spk1(Wire.makeSpk1(stream: 0x40000001, seq: 3, opus: [1, 2, 3]))!
    check(s.stream == 0x40000001 && s.seq == 3 && s.frameMs == 60 && s.payload == Data([1, 2, 3]), "spk1 parse")
    check(Wire.spk1(Data("SPK1".utf8)) == nil, "short spk1 refused")
    let f = Wire.svf1(header: ["type": "frame", "seq": 1], jpeg: Data([0xFF, 0xD8, 1]))!
    let hl = Int([UInt8](f)[4]) | Int([UInt8](f)[5]) << 8
    check(f.prefix(4) == Data("SVF1".utf8) && f.count == 6 + hl + 3, "svf1 layout")
    check(Wire.svf1(header: [:], jpeg: Data(count: 40000)) == nil, "svf1 over 32 KiB refused")
}

// ---- links (a recording transport, a fake clock) ----
final class FakeClock { var us: UInt64 = 1_000_000; func now() -> UInt64 { us } }
let ident = DeviceIdentity(deviceId: "a1b2c3d4e5f6", bootId: "0011223344556677")

test("hearing: declaration, SEA1 seq from 0, self flag before the packet, tick watchdog") {
    let h = HearingLogic(id: ident), tr = RecordingTransport(), clk = FakeClock()
    h.transport = tr; h.now = clk.now
    h.opened()
    let decl = tr.texts[0]
    check(decl["type"] as? String == "audio" && decl["framing"] as? String == "sea1" && decl["sample_rate"] as? Int == 24000,
          "declaration \(decl)")
    check((decl["source"] as? String ?? "").hasPrefix("stackchan"), "stackchan source")
    h.feed([Int16](repeating: 100, count: 1440 * 2 + 10), firstUs: 5_000_000)
    check(tr.datas.count == 2, "two packets")
    check(tr.types() == ["audio", "self"], "self sent once before the first packet: \(tr.types())")
    let b0 = [UInt8](tr.datas[0]), b1 = [UInt8](tr.datas[1])
    check(b0[4] == 0 && b1[4] == 1, "seq 0, 1")
    check(Wire.le32test(b1, 8) == 5_060_000, "capture time +60 ms")
    h.setSounding(true)
    h.feed([Int16](repeating: 0, count: 1440), firstUs: 5_120_000)
    check(tr.texts.last?["sounding"] as? Bool == true, "self on change")
    h.received(text: Wire.json(["type": "tick", "seq": 1, "events": [["type": "audio", "format": "opus"]]]))
    check(h.isAcked && h.ticks == 1, "ack on a tick")
    clk.us += 3_100_000
    h.watchdog()
    check(tr.reconnects.count == 1, "3 s without a tick reconnects")
    var opens = 0
    h.onOpen = { opens += 1 }
    h.received(text: Wire.json(["type": "tick", "speaking": true]))
    h.opened()
    check(opens == 1 && !h.serverSpeaking, "a new session: onOpen, the ear's speaking flag cleared")
}

final class FakeDelegate: MouthDelegate {
    var verbs: [String] = [], stages: [String] = [], toasts: [String] = [], digits: [Int] = []
    func command(_ verb: String, args: [String: Any]) -> (ok: Bool, error: String?, data: [String: Any]) {
        verbs.append(verb); return (true, nil, ["state": args["state"] ?? ""])
    }
    func stage(_ stage: String) { stages.append(stage) }
    func burst(_ m: [String: Any]) {}
    func toast(_ text: String, code: String) { toasts.append(text) }
    func snap(id: Int) {}
    func localDigit(_ value: Int) { digits.append(value) }
}

test("mouth: hello, boot report, a stream played and reported, tap stops it") {
    let clk = FakeClock(), out = ClockSpeechOutput(); out.now = clk.now
    let m = MouthLogic(id: ident, output: out), tr = RecordingTransport(), dg = FakeDelegate()
    m.transport = tr; m.delegate = dg; m.now = clk.now
    m.opened()
    check(tr.texts[0]["type"] as? String == "hello" && tr.texts[0]["boot_id"] as? String == ident.bootId &&
          (tr.texts[0]["codecs"] as? [String]) == ["opus"], "robot hello")
    m.received(text: Wire.json(["type": "hello", "codec": "opus", "frame_ms": 60, "sample_rate": 24000]))
    let rep = tr.texts[1]
    check(rep["type"] as? String == "mode_report" && rep["mode"] as? String == "crowd" && rep["by"] as? String == "boot" &&
          rep["input"] as? String == "boot" && (rep["effects"] as? [String]) == ["stop_speech"], "boot report \(rep)")
    check(!tr.types().contains("mode_input"), "no power-on 9 unless asked")
    m.received(text: Wire.json(["type": "tts.start", "stream": 0x40000001, "codec": "opus", "sample_rate": 24000, "frame_ms": 60]))
    check(m.speaking && m.sounding, "speaking")
    let enc = OpusEncoder()!
    for i in 0..<5 {
        let pkt = enc.encode([Int16](repeating: 0, count: 1440))!
        m.received(data: Wire.makeSpk1(stream: 0x40000001, seq: UInt32(i), opus: pkt))
    }
    check(tr.types().contains("tts.playing"), "playing after the prebuffer")
    m.received(text: Wire.json(["type": "tts.end", "stream": 0x40000001, "frames": 5, "duration_ms": 300]))
    clk.us += 200_000; out.tick()
    check(tr.types().contains("tts.progress"), "progress")
    clk.us += 200_000; out.tick()
    let done = tr.texts.first { $0["type"] as? String == "tts.done" }
    check(done?["played_ms"] as? Int == 300, "done with played_ms 300: \(String(describing: done))")
    check(!m.speaking && m.sounding, "450 ms tail")
    clk.us += 500_000
    check(!m.sounding, "tail over")
    // a second stream; a tap while it plays stops it (crowd: STOP + stop_speech)
    m.received(text: Wire.json(["type": "tts.start", "stream": 0x40000002, "codec": "opus", "sample_rate": 24000, "frame_ms": 60]))
    let o = m.touch(double: false)
    check(o.toast == "STOP" && o.effects == [.stopSpeech], "tap while speaking: \(o.toast)")
    check(tr.types().contains("tts.stopped") && !m.speaking, "stopped reported")
    let tapRep = tr.texts.last { $0["type"] as? String == "mode_report" }!
    check(tapRep["input"] as? String == "tap" && tapRep["by"] as? String == "touch", "tap reported")
    let quiet = m.touch(double: true)
    check(quiet.toast == "QUIET", "double tap = quiet")
}

test("mouth: digits, cmd, stage, power-on 9 until ok") {
    let clk = FakeClock(), out = ClockSpeechOutput(); out.now = clk.now
    let m = MouthLogic(id: ident, output: out), tr = RecordingTransport(), dg = FakeDelegate()
    m.transport = tr; m.delegate = dg; m.now = clk.now; m.powerOnNine = true
    m.opened()
    m.received(text: Wire.json(["type": "hello", "codec": "opus", "frame_ms": 60, "sample_rate": 24000]))
    let nine = tr.texts.first { $0["type"] as? String == "mode_input" }
    check(nine?["value"] as? Int == 9 && nine?["source"] as? String == "quick", "power-on 9")
    clk.us += 10_500_000; m.tick()
    check(tr.texts.filter { $0["type"] as? String == "mode_input" }.count == 2, "resent after 10 s")
    check(dg.digits.filter { $0 == 9 }.count == 1, "the local half of 9 once, not on every resend")
    m.received(text: Wire.json(["type": "mode_result", "seq": 2, "code": "ok", "toast": "ALL ON"]))
    clk.us += 10_500_000; m.tick()
    check(tr.texts.filter { $0["type"] as? String == "mode_input" }.count == 2, "not after ok")
    m.received(text: Wire.json(["type": "cmd", "id": 7, "do": "eyes", "args": ["state": "happy"]]))
    let r = tr.texts.last!
    check(r["type"] as? String == "cmd_result" && r["id"] as? Int == 7 && r["ok"] as? Bool == true, "cmd_result \(r)")
    m.received(text: Wire.json(["type": "chat_stage", "stage": "think", "at_ms": 1]))
    check(dg.stages == ["think"] && dg.verbs == ["eyes"], "stage + verb")
    m.sendDigit(0, source: "touch")
    check(dg.digits.contains(0) && tr.texts.last?["value"] as? Int == 0, "0 = local half + mind")
    m.sendDigit(6, source: "touch")
    check(m.modes.state.mode == .companion, "6 = companion locally")
}

test("vision: hello, events with seq, frames, ping after 15 s") {
    let v = VisionLogic(id: ident), tr = RecordingTransport(), clk = FakeClock()
    v.transport = tr; v.now = clk.now
    var reopened = 0
    v.onOpen = { reopened += 1 }
    v.opened()
    check(reopened == 1, "onOpen on each open")
    check(tr.texts[0]["type"] as? String == "hello" && ((tr.texts[0]["device"] as? [String: Any])?["protocol"] as? String) == "soul-vision/1", "hello")
    v.event("face_found", ["face": 1, "box": [1, 2, 3, 4], "prob": 0.9])
    check(tr.texts[1]["event"] as? String == "face_found" && tr.texts[1]["seq"] as? Int == 1 && tr.texts[1]["type"] as? String == "vision", "event")
    v.frame(jpeg: Data([0xFF, 0xD8, 0, 0]), width: 320, height: 240, captureUs: 5, diff: 9, snap: 3)
    check(tr.datas.count == 1 && tr.datas[0].prefix(4) == Data("SVF1".utf8), "frame")
    clk.us += 16_000_000; v.keepAlive()
    check(tr.texts.last?["type"] as? String == "ping", "ping")
}

test("frame change: first goes, same picture never, a change after 1 s") {
    var fc = FrameChange()
    let a = [UInt8](repeating: 100, count: 192)
    check(fc.shouldSend(a, atUs: 0) != nil, "first")
    check(fc.shouldSend(a.map { $0 &+ 20 }, atUs: 2_000_000) == nil, "brightness only: no")
    var b = a; for i in 0..<10 { b[i] = 200 }
    check(fc.shouldSend(b, atUs: 500_000) == nil, "too soon")
    check(fc.shouldSend(b, atUs: 2_000_000) == 10, "10 cells changed")
}

test("faces: found, named once, lost after 1.5 s; the book keeps 20 per name") {
    let book = FaceBook(url: nil, threshold: 0.9)
    let me: [Float] = (0..<64).map { Float($0 % 7) + 1 }
    book.add("owner", me)
    let tk = FaceTracker(book: book)
    var ev = tk.update([FaceSeen(box: CGRect(x: 100, y: 100, width: 80, height: 80), prob: 0.95, print: me)], atUs: 1_000_000)
    check(ev.map(\.0) == ["face_found", "face_named"], "found + named: \(ev.map(\.0))")
    check(ev[1].1["name"] as? String == "owner", "name")
    ev = tk.update([FaceSeen(box: CGRect(x: 104, y: 102, width: 80, height: 80), prob: 0.95, print: me)], atUs: 1_200_000)
    check(ev.isEmpty, "same track, named once")
    ev = tk.update([], atUs: 2_800_000)
    check(ev.map(\.0) == ["face_lost"] && ev[0].1["reason"] as? String == "timeout", "lost")
    let other: [Float] = (0..<64).map { Float(($0 * 13) % 5) - 2 }
    for i in 0..<3 { ev = tk.update([FaceSeen(box: CGRect(x: 300, y: 50, width: 60, height: 60), prob: 0.9, print: other)], atUs: 3_000_000 + UInt64(i) * 100_000) }
    check(tk.tracks.count == 1 && tk.tracks[0].decided && tk.tracks[0].name == nil, "a stranger after three looks")
    for _ in 0..<25 { book.add("owner", me) }
    check(book.people["owner"]?.count == 20, "20 per name")
}

test("camera upload stays at the robot's size (<= 8 KB), quality stepping down") {
    let sizes: [Double: Int] = [0.5: 21000, 0.38: 14000, 0.28: 9100, 0.2: 7400, 0.14: 6000, 0.1: 5000]
    let r = JpegBudget.pick { q in Data(count: sizes[q]!) }!
    check(r.0.count == 7400 && r.1 == 0.2, "first quality under 8 KB: \(r.1)")
    let quiet = JpegBudget.pick { q in Data(count: q == 0.5 ? 5600 : 99999) }!
    check(quiet.1 == 0.5, "a quiet picture keeps 0.5")
    let huge = JpegBudget.pick { _ in Data(count: 50000) }!
    check(huge.0.count == 50000, "nothing fits: the smallest still goes")
}

test("responses: clip by emotion, one per 10 s, happy every second, quiet wait") {
    let tags: [Int: String] = [1: "happy", 2: "happy", 3: "sad", 4: "neutral"]
    check(Respond.clip(emotion: "sad", random: 5, tags: tags, last: 3) == 3, "the only sad clip, even if last")
    check(Respond.clip(emotion: "happy", random: 0, tags: tags, last: 1) == 2, "avoid the last")
    check(Respond.clip(emotion: "angry", random: 0, tags: tags, last: 0) == 0, "no clip of that label")
    check(Respond.eye(for: "fearful") == "scared" && Respond.eye(for: "neutral") == nil, "eyes")
    var p = ResponsePolicy()
    check(p.consider(id: 1, emotion: "sad", speech: true, selfSounding: false, ageMs: 300, now: 1_000_000, random: 0, tags: tags) == 3, "sad answers")
    p.accepted(now: 1_000_000)
    check(p.consider(id: 2, emotion: "sad", speech: true, selfSounding: false, ageMs: 300, now: 5_000_000, random: 0, tags: tags) == 0, "10 s gap")
    check(p.consider(id: 2, emotion: "sad", speech: true, selfSounding: false, ageMs: 300, now: 30_000_000, random: 0, tags: tags) == 0, "same id once")
    check(p.consider(id: 3, emotion: "sad", speech: true, selfSounding: true, ageMs: 300, now: 30_000_000, random: 0, tags: tags) == 0, "not our own voice")
    check(p.consider(id: 4, emotion: "happy", speech: true, selfSounding: false, ageMs: 300, now: 40_000_000, random: 0, tags: tags) != 0, "first happy")
    p.accepted(now: 40_000_000)
    check(p.consider(id: 5, emotion: "happy", speech: true, selfSounding: false, ageMs: 300, now: 60_000_000, random: 0, tags: tags) == 0, "second happy waits")
    check(p.consider(id: 6, emotion: "happy", speech: true, selfSounding: false, ageMs: 300, now: 70_000_000, random: 0, tags: tags) != 0, "every second happy")
    p.accepted(now: 70_000_000)
    p.newSession()                                  // the ear restarts its ids at s-1 on a new hearing session
    check(p.consider(id: 1, emotion: "sad", speech: true, selfSounding: false, ageMs: 300, now: 90_000_000, random: 0, tags: tags) == 3, "a new session: s-1 answers again")
    var q = QuietWait()
    q.hold(3, now: 0)
    check(q.poll(now: 1_000_000, speaking: false, lastSpeech: 0) == .wait, "not yet quiet 1.5 s")
    check(q.poll(now: 1_600_000, speaking: true, lastSpeech: 1_600_000) == .wait, "someone talks")
    check(q.poll(now: 3_200_000, speaking: false, lastSpeech: 1_600_000) == .play, "quiet 1.6 s -> play")
    q.hold(3, now: 0)
    check(q.poll(now: 10_500_000, speaking: true, lastSpeech: 10_400_000) == .expired, "dropped after 10 s")
}

test("mouth: a local clip plays as sounding, reports nothing, yields to a server stream") {
    let clk = FakeClock(), out = ClockSpeechOutput(); out.now = clk.now
    let m = MouthLogic(id: ident, output: out), tr = RecordingTransport()
    m.transport = tr; m.now = clk.now
    check(m.playLocal([Int16](repeating: 1, count: 24000)), "local clip starts")
    check(m.sounding && m.speaking, "sounding while it plays")
    check(!m.playLocal([1, 2, 3]), "a second one is refused while speaking")
    m.received(text: Wire.json(["type": "tts.start", "stream": 5, "codec": "opus", "sample_rate": 24000, "frame_ms": 60]))
    check(!tr.types().contains(where: { $0.hasPrefix("tts.") }), "nothing reported for the clip: \(tr.types())")
    check(m.speaking, "the server stream took over")
}

test("voice module words: whole utterance only, kinds as the robot") {
    check(AsrWords.match("Hello.")?.kind == .wake, "hello = wake")
    check(AsrWords.match("Hi, A.S.R.")?.kind == .wake, "hi ASR = wake")
    check(AsrWords.match("Turn left!")?.kind == .motion("turn left"), "turn left = motion")
    check(AsrWords.match("9")?.kind == .digit(9) && AsrWords.match("nine")?.kind == .digit(9), "9 / nine = digit")
    check(AsrWords.match("Play")?.kind == .mode(.play), "play = mode input")
    check(AsrWords.match("increase volume")?.kind == .volume("increase"), "volume")
    check(AsrWords.match("Okay")?.kind == .other, "ok = other")
    check(AsrWords.match("hello how are you") == nil, "a word inside a sentence is talk")
    check(AsrWords.match("你好") == nil, "not a module word")
    check(AsrWords.words.count == 42, "the module's 42 words")
}

var failedTests: [String] = []
for (name, body) in tests {
    let before = failures
    do { try body() } catch { failures += 1; print("FAIL \(name): threw \(error)") }
    let ok = failures == before
    if !ok { failedTests.append(name) }
    print("\(ok ? "ok  " : "FAIL") \(name)")
}
print("\(tests.count) tests, \(checks) checks, \(failures) failures")
exit(failures == 0 ? 0 : 1)
