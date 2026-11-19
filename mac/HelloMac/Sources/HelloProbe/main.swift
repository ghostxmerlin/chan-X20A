import AppKit
import AVFoundation
import Foundation
import HelloCore

// HelloProbe: hello-on-the-Mac without a window, against the Mac's own server stack (soul-spike-mac via ssh -L).
// Speaks a synthetic sentence (macOS `say`, no household audio) into the hearing link in real time, opens /mouth
// and /vision like the robot, optionally asks soul-tts-mac to say a line back (--tts), and prints what came back.
//   swift run HelloProbe [--tts] [--text "..."] [--seconds 25]
var args = Array(CommandLine.arguments.dropFirst())
func opt(_ k: String) -> String? { if let i = args.firstIndex(of: k), i + 1 < args.count { return args[i + 1] }; return nil }
let wantTts = args.contains("--tts")
let text = opt("--text") ?? "你好，我是住在 Mac 上的 hello。今天下午三点提醒我开会。"
let seconds = Double(opt("--seconds") ?? "25") ?? 25

let cfg: HelloConfig
do { cfg = try HelloConfig.load() } catch { print("no config: run mac/HelloMac/setup.sh first (\(error))"); exit(2) }
let token = try cfg.token()
let id = DeviceIdentity(deviceId: cfg.deviceId, firmware: "hello-mac-probe-0.1.0")

// 1. the sentence, 24 kHz mono PCM16
let aiff = FileManager.default.temporaryDirectory.appendingPathComponent("hello-probe-\(getpid()).wav")
let say = Process()
say.executableURL = URL(fileURLWithPath: "/usr/bin/say")
say.arguments = ["-v", opt("--voice") ?? "Meijia", "-o", aiff.path, "--file-format=WAVE", "--data-format=LEI16@24000", text]
try say.run(); say.waitUntilExit()
let file = try AVAudioFile(forReading: aiff, commonFormat: .pcmFormatInt16, interleaved: true)
let buf = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: AVAudioFrameCount(file.length))!
try file.read(into: buf)
let speech = Array(UnsafeBufferPointer(start: buf.int16ChannelData![0], count: Int(buf.frameLength)))
try? FileManager.default.removeItem(at: aiff)
let sr = Int(file.processingFormat.sampleRate)
guard sr == 24000 else { print("say gave \(sr) Hz"); exit(2) }
let audio = [Int16](repeating: 0, count: 24000) + speech + [Int16](repeating: 0, count: 24000 * 4)
print("probe: sentence \(String(format: "%.1f", Double(speech.count) / 24000)) s, device \(id.deviceId), boot \(id.bootId)")

// 2. tunnel + the three links
let tunnel = Tunnel(cfg: cfg)
let core = DispatchQueue(label: "hello.core")
let hearing = HearingLogic(id: id)
let output = ClockSpeechOutput()
let mouth = MouthLogic(id: id, output: output)
let vision = VisionLogic(id: id)
mouth.onSounding = { hearing.setSounding($0) }
var got: [String: Int] = [:]
var heard: [String] = []
var speakers: [String] = []
func note(_ k: String) { got[k, default: 0] += 1 }
hearing.onEvent = { e in
    let t = e["type"] as? String ?? "?"
    note("hear.\(t)")
    if t == "speech", e["phase"] as? String == "end" {
        heard.append(e["text"] as? String ?? "")
        if let s = e["speaker"] as? [String: Any] { speakers.append("\(s["name"] ?? "-")/\(s["band"] ?? "-")") }
    }
}
let links = [WSLink(name: "hearing", url: cfg.url(cfg.hearingPath), token: token, queue: core, logic: hearing),
             WSLink(name: "vision", url: cfg.url("/vision"), token: token, queue: core, logic: vision)]
for l in links { l.onState = { s in note("\(l.name).\(s)") } }

final class TextSpy: LinkLogic {        // counts the mouth's messages without changing it
    let inner: MouthLogic
    var transport: Transport? { get { inner.transport } set { inner.transport = newValue } }
    init(_ m: MouthLogic) { inner = m }
    func opened() { inner.opened() }
    func received(text: String) { if let t = Wire.parse(text)?["type"] as? String { got["mouth.rx.\(t)", default: 0] += 1 }; inner.received(text: text) }
    func received(data: Data) { got["mouth.rx.audio", default: 0] += 1; inner.received(data: data) }
    func closed(_ why: String) { got["mouth.closed", default: 0] += 1; inner.closed(why) }
}

var spyHolder: TextSpy? = nil        // WSLink holds its logic weakly
var mouthLinkHolder: WSLink? = nil
tunnel.onState = { s in
    print("tunnel: \(s)")
    guard s == "up" else { return }
    spyHolder = TextSpy(mouth)
    let spy = spyHolder!
    let mouthLink = WSLink(name: "mouth", url: cfg.url("/mouth"), token: token, queue: core, logic: spy)
    mouthLinkHolder = mouthLink
    mouthLink.onState = { st in note("mouth.\(st)") }
    for l in links { l.start() }
    mouthLink.start()
    // feed the mic in real time from 1 s on (20 ms blocks), face events + one frame on /vision
    var pos = 0
    let t0 = monoUs()
    let timer = DispatchSource.makeTimerSource(queue: core)
    timer.schedule(deadline: .now() + 1, repeating: .milliseconds(20))
    timer.setEventHandler {
        output.tick(); mouth.tick(); hearing.watchdog(); vision.keepAlive()
        guard hearing.isAcked || pos > 0 else { return }
        if pos < audio.count {
            let n = min(480, audio.count - pos)
            hearing.feed(Array(audio[pos..<pos + n]), firstUs: t0 + UInt64(pos) * 1_000_000 / 24000)
            pos += n
        }
    }
    timer.resume()
    core.asyncAfter(deadline: .now() + 3) {
        vision.event("vision_on")
        vision.event("face_found", ["face": 1, "box": [260, 180, 120, 120], "prob": 0.93, "gender": "?"])
        let jpeg = probeJpeg()
        vision.frame(jpeg: jpeg, width: 320, height: 240, captureUs: monoUs(), diff: 255)
    }
    core.asyncAfter(deadline: .now() + 6) { vision.event("face_lost", ["face": 1, "reason": "timeout"]) }
    if wantTts {
        DispatchQueue.global().asyncAfter(deadline: .now() + seconds * 0.5) { ttsSay("这是服务器说给 Mac 听的一句话。") }   // off the core queue: ssh blocks
    }
    core.asyncAfter(deadline: .now() + seconds) {
        timer.cancel()
        let summary: [String: Any] = ["counts": got, "heard_chars": heard.map(\.count), "speakers": speakers,
                                      "packets": hearing.packets, "ticks": hearing.ticks, "acked": hearing.isAcked,
                                      "mode": mouth.modes.state.mode.id, "streams": mouth.streams,
                                      "vision_events": vision.eventsSent, "frames": vision.framesSent,
                                      "played_samples": output.samples.count]
        print(Wire.json(summary))
        if args.contains("--show-text") { for h in heard { print("heard: \(h)") } }
        for l in links { l.stop() }
        mouthLink.stop()
        DispatchQueue.main.async { tunnel.stop(); exit(hearing.isAcked && hearing.ticks > 50 ? 0 : 1) }
    }
}

func probeJpeg() -> Data {
    let w = 320, h = 240
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: w, pixelsHigh: h, bitsPerSample: 8, samplesPerPixel: 3,
                               hasAlpha: false, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    for y in 0..<h { for x in 0..<w { rep.setColor(NSColor(calibratedRed: CGFloat(x) / CGFloat(w), green: CGFloat(y) / CGFloat(h), blue: 0.4, alpha: 1), atX: x, y: y) } }
    return rep.representation(using: .jpeg, properties: [.compressionFactor: 0.7])!
}

/// soul-tts-mac's /v1/say on the server (loopback; its own API token there): hello says a line to this device.
func ttsSay(_ line: String) {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/bin/ssh")
    let body = Wire.json(["text": line]).replacingOccurrences(of: "'", with: "")
    p.arguments = cfg.sshOptions + [cfg.sshTarget,
        "curl -s -m 10 -H \"Authorization: Bearer $(cat ~/soul-tts/api.token)\" -H 'Content-Type: application/json' -d '\(body)' http://127.0.0.1:52850/v1/say"]
    let pipe = Pipe(); p.standardOutput = pipe; p.standardError = FileHandle.nullDevice
    try? p.run(); p.waitUntilExit()
    let out = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
    print("tts say: \(out.contains("\"id\"") ? "accepted" : "answer \(out.prefix(120))")")
}

tunnel.start()
dispatchMain()
