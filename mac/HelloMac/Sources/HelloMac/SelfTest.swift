import AppKit
import AVFoundation
import Foundation
import HelloCore

/// hello --selftest: the app-side audio paths without a mic or a speaker (the night of 2026-11-19: nobody in the room).
/// 1. AudioIn: 1 s of a 48 kHz sine through the converter -> about 24000 samples at 24 kHz.
/// 2. AudioOut offline: a 600 ms stream through the robot's report logic -> playing, progress, done with played 600 ms.
/// 3. The same stream stopped half way -> stopped. Exit 0 = passed.
enum AppSelfTest {
    static func run() -> Int32 {
        var fails = 0
        func check(_ ok: Bool, _ what: String) { print("\(ok ? "ok  " : "FAIL") \(what)"); if !ok { fails += 1 } }

        let mic = AudioIn()
        var got = 0, times: [UInt64] = []
        mic.onPCM = { pcm, us in got += pcm.count; times.append(us) }
        let mono = mic.prepare(rate: 48000)
        for _ in 0..<25 {                                  // 25 x 1920 frames = 1 s at 48 kHz
            let b = AVAudioPCMBuffer(pcmFormat: mono, frameCapacity: 1920)!
            b.frameLength = 1920
            for i in 0..<1920 { b.floatChannelData![0][i] = 0.3 * sin(Float(i) * 0.06) }
            mic.handle(b, mono: mono)
        }
        check(abs(got - 24000) <= 600, "mic 48 kHz -> 24 kHz: \(got) samples for 1 s")
        check(times.count == 25 && zip(times, times.dropFirst()).allSatisfy { $0 <= $1 }, "capture times in order")

        let q = DispatchQueue(label: "selftest.core")
        for stopHalf in [false, true] {
            let out = AudioOut(queue: q, offline: true)
            var events: [String] = []
            var played = -1
            out.onEvent = { e in
                switch e {
                case .playing: events.append("playing")
                case .progress: if events.last != "progress" { events.append("progress") }
                case .done(_, let p): events.append("done"); played = p
                case .stopped(_, let p): events.append("stopped"); played = p
                }
            }
            q.sync {
                out.begin(stream: 7)
                for _ in 0..<10 { out.push(stream: 7, pcm: [Int16](repeating: 1000, count: 1440)) }
                out.finish(stream: 7)
            }
            for i in 0..<20 {                             // 20 x 1440 frames = 1.2 s rendered
                q.sync { out.renderForTest(frames: 1440) }
                if stopHalf && i == 4 { q.sync { out.stop(stream: 7) } }
                q.sync {}
            }
            q.sync {}
            if stopHalf {
                check(events.first == "playing" && events.last == "stopped" && played < 600, "stopped half way: \(events) played \(played)")
            } else {
                check(events == ["playing", "progress", "done"] && played == 600, "speaker reports: \(events) played \(played)")
            }
        }
        // review 2026-11-19: tts.end arriving after the last buffer already played still ends the stream
        do {
            let out = AudioOut(queue: q, offline: true)
            var last = ""
            out.onEvent = { e in if case .done = e { last = "done" } }
            q.sync { out.begin(stream: 9); for _ in 0..<4 { out.push(stream: 9, pcm: [Int16](repeating: 500, count: 1440)) } }
            for _ in 0..<8 { q.sync { out.renderForTest(frames: 1440) }; q.sync {} }
            q.sync { out.finish(stream: 9) }
            q.sync {}
            check(last == "done", "a late tts.end still gives done")
        }
        // a busy 320x240 picture (noise: the worst case for JPEG) comes out at the robot's size
        do {
            let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 320, pixelsHigh: 240, bitsPerSample: 8, samplesPerPixel: 3,
                                       hasAlpha: false, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
            var seed: UInt32 = 7
            for y in 0..<240 { for x in 0..<320 {
                seed = seed &* 1664525 &+ 1013904223
                let n = CGFloat(seed >> 24) / 255
                rep.setColor(NSColor(calibratedRed: CGFloat(x) / 320 * 0.6 + n * 0.4, green: CGFloat(y) / 240 * 0.6 + n * 0.3,
                                     blue: 0.3 + n * 0.3, alpha: 1), atX: x, y: y)
            } }
            let q6 = rep.representation(using: .jpeg, properties: [.compressionFactor: 0.6])!.count
            let r = JpegBudget.pick { q in rep.representation(using: .jpeg, properties: [.compressionFactor: q]) }!
            check(r.0.count <= 12000, "busy frame: \(q6) B at 0.6 -> \(r.0.count) B at \(r.1)")
        }
        print("app self-test: \(fails == 0 ? "passed" : "\(fails) failed")")
        return fails == 0 ? 0 : 1
    }
}
