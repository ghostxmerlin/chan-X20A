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
