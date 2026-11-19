import AVFoundation
import CoreAudio
import Foundation
import HelloCore

/// Input devices (Core Audio), for the microphone picker.
struct AudioDevice: Hashable { let id: AudioDeviceID; let uid: String; let name: String }

enum AudioDevices {
    static func inputs() -> [AudioDevice] {
        var addr = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDevices, mScope: kAudioObjectPropertyScopeGlobal,
                                              mElement: kAudioObjectPropertyElementMain)
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size) == noErr else { return [] }
        var ids = [AudioDeviceID](repeating: 0, count: Int(size) / MemoryLayout<AudioDeviceID>.size)
        guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &ids) == noErr else { return [] }
        return ids.compactMap { id in
            guard inputChannels(id) > 0 else { return nil }
            return AudioDevice(id: id, uid: string(id, kAudioDevicePropertyDeviceUID) ?? "\(id)",
                               name: string(id, kAudioObjectPropertyName) ?? "设备 \(id)")
        }
    }

    static func defaultInput() -> AudioDeviceID? {
        var addr = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDefaultInputDevice,
                                              mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        var id = AudioDeviceID(0), size = UInt32(MemoryLayout<AudioDeviceID>.size)
        return AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &id) == noErr ? id : nil
    }

    private static func inputChannels(_ id: AudioDeviceID) -> Int {
        var addr = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyStreamConfiguration,
                                              mScope: kAudioDevicePropertyScopeInput, mElement: kAudioObjectPropertyElementMain)
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(id, &addr, 0, nil, &size) == noErr, size > 0 else { return 0 }
        let raw = UnsafeMutableRawPointer.allocate(byteCount: Int(size), alignment: 16)
        defer { raw.deallocate() }
        guard AudioObjectGetPropertyData(id, &addr, 0, nil, &size, raw) == noErr else { return 0 }
        let list = UnsafeMutableAudioBufferListPointer(raw.assumingMemoryBound(to: AudioBufferList.self))
        return list.reduce(0) { $0 + Int($1.mNumberChannels) }
    }

    private static func string(_ id: AudioDeviceID, _ sel: AudioObjectPropertySelector) -> String? {
        var addr = AudioObjectPropertyAddress(mSelector: sel, mScope: kAudioObjectPropertyScopeGlobal,
                                              mElement: kAudioObjectPropertyElementMain)
        var cf: Unmanaged<CFString>? = nil
        var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        guard AudioObjectGetPropertyData(id, &addr, 0, nil, &size, &cf) == noErr, let s = cf?.takeRetainedValue() else { return nil }
        return s as String
    }
}

/// The microphone: the chosen input device -> 24 kHz mono PCM16 blocks with their capture time (monotonic µs).
final class AudioIn {
    private var engine: AVAudioEngine?
    private var converter: AVAudioConverter?
    private let out = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 24000, channels: 1, interleaved: true)!
    var onPCM: (([Int16], UInt64) -> Void)?
    var onBuffer: ((AVAudioPCMBuffer) -> Void)?          // raw, for the voice module
    var onLevel: ((Float) -> Void)?
    private(set) var running = false
    private(set) var deviceName = "—"
    private var device: AudioDeviceID?
    private var observer: NSObjectProtocol?
    var onRestart: ((String) -> Void)?

    func start(device: AudioDeviceID?) throws {
        stop()
        self.device = device
        let e = AVAudioEngine()
        let input = e.inputNode
        if let device, let unit = input.audioUnit {
            var d = device
            let st = AudioUnitSetProperty(unit, kAudioOutputUnitProperty_CurrentDevice, kAudioUnitScope_Global, 0, &d,
                                          UInt32(MemoryLayout<AudioDeviceID>.size))
            if st != noErr { NSLog("hello: could not select input device \(device): \(st)") }
        }
        let inFmt = input.outputFormat(forBus: 0)
        guard inFmt.sampleRate > 0 else { throw NSError(domain: "hello", code: 1, userInfo: [NSLocalizedDescriptionKey: "麦克风不可用（权限？）"]) }
        let mono = prepare(rate: inFmt.sampleRate)
        input.installTap(onBus: 0, bufferSize: 2048, format: inFmt) { [weak self] buf, when in
            self?.handle(buf, mono: mono)
        }
        try e.start()
        engine = e
        running = true
        // review 2026-11-19: the engine stops on a configuration change (device / AirPods / rate): start it again
        observer = NotificationCenter.default.addObserver(forName: .AVAudioEngineConfigurationChange, object: e,
                                                          queue: .main) { [weak self] _ in
            guard let self, self.running else { return }
            do { try self.start(device: self.device); self.onRestart?("麦克风换了设置，已重新打开：\(self.deviceName)") }
            catch { self.running = false; self.onRestart?("麦克风换了设置，重新打开失败：\(error.localizedDescription)") }
        }
        deviceName = AudioDevices.inputs().first { $0.id == device }?.name ?? "默认麦克风"
    }

    /// The converter for an input rate -> the mono format the tap's channel 0 is copied into.
    func prepare(rate: Double) -> AVAudioFormat {
        let mono = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: rate, channels: 1, interleaved: false)!
        converter = AVAudioConverter(from: mono, to: out)
        return mono
    }

    func stop() {
        if let o = observer { NotificationCenter.default.removeObserver(o); observer = nil }
        engine?.inputNode.removeTap(onBus: 0)
        engine?.stop()
        engine = nil
        running = false
    }

    func handle(_ buf: AVAudioPCMBuffer, mono: AVAudioFormat) {      // internal: the app's --selftest feeds it
        let n = Int(buf.frameLength)
        guard n > 0, let ch = buf.floatChannelData, let conv = converter else { return }
        onBuffer?(buf)
        let now = monoUs()
        let firstUs = now - UInt64(Double(n) / buf.format.sampleRate * 1_000_000)
        // channel 0 only, as the robot (FW/hearing.cpp:407)
        let m = AVAudioPCMBuffer(pcmFormat: mono, frameCapacity: AVAudioFrameCount(n))!
        m.frameLength = AVAudioFrameCount(n)
        memcpy(m.floatChannelData![0], ch[0], n * MemoryLayout<Float>.size)
        var peak: Float = 0
        for i in 0..<n { peak = max(peak, abs(ch[0][i])) }
        onLevel?(peak)
        let cap = AVAudioFrameCount(Double(n) * 24000 / buf.format.sampleRate + 64)
        let o = AVAudioPCMBuffer(pcmFormat: out, frameCapacity: cap)!
        var given = false
        var err: NSError?
        conv.convert(to: o, error: &err) { _, status in
            if given { status.pointee = .noDataNow; return nil }
            given = true
            status.pointee = .haveData
            return m
        }
        guard err == nil, o.frameLength > 0 else { return }
        onPCM?(Array(UnsafeBufferPointer(start: o.int16ChannelData![0], count: Int(o.frameLength))), firstUs)
    }
}

/// hello's speaker: a SpeechOutput on AVAudioEngine. Prebuffer 180 ms like the robot; played time counted from each
/// 60 ms buffer's completion; events come back on `queue` (the core queue).
final class AudioOut: SpeechOutput {
    var onEvent: ((SpeechEvent) -> Void)?
    private let engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()
    private let fmt = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 24000, channels: 1, interleaved: false)!
    private let queue: DispatchQueue
    private var stream: UInt32? = nil
    private var gen = 0
    private var queuedMs = 0, playedMs = 0, pending: [AVAudioPCMBuffer] = []
    private var started = false, finished = false
    var volume: Float = 0.8 { didSet { player.volume = volume } }

    private let offline: Bool

    /// offline: no speaker (the app's --selftest renders by hand and counts the reports).
    init(queue: DispatchQueue, offline: Bool = false) {
        self.queue = queue
        self.offline = offline
        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: fmt)
        player.volume = volume
        if offline {
            try? engine.enableManualRenderingMode(.offline, format: fmt, maximumFrameCount: 4096)
        }
        try? engine.start()
    }

    /// --selftest: render `frames` of output by hand (offline mode only).
    func renderForTest(frames: AVAudioFrameCount) {
        guard offline, let b = AVAudioPCMBuffer(pcmFormat: engine.manualRenderingFormat, frameCapacity: frames) else { return }
        _ = try? engine.renderOffline(frames, to: b)
    }

    func begin(stream s: UInt32) {
        if let old = stream { halt(); onEvent?(.stopped(stream: old, playedMs: playedMs)) }
        gen += 1
        stream = s; queuedMs = 0; playedMs = 0; pending = []; started = false; finished = false
        if !engine.isRunning { try? engine.start() }
    }

    func push(stream s: UInt32, pcm: [Int16]) {
        guard s == stream, !pcm.isEmpty else { return }
        let b = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: AVAudioFrameCount(pcm.count))!
        b.frameLength = AVAudioFrameCount(pcm.count)
        let f = b.floatChannelData![0]
        for i in 0..<pcm.count { f[i] = Float(pcm[i]) / 32768 }
        queuedMs += pcm.count * 1000 / 24000
        if started { schedule(b) } else {
            pending.append(b)
            if queuedMs >= 180 { startPlaying() }
        }
    }

    private func startPlaying() {
        guard let s = stream, !started else { return }
        started = true
        for b in pending { schedule(b) }
        pending = []
        player.play()
        onEvent?(.playing(stream: s, bufferedMs: queuedMs))
    }

    private func schedule(_ b: AVAudioPCMBuffer) {
        let g = gen, ms = Int(b.frameLength) * 1000 / 24000
        player.scheduleBuffer(b, completionCallbackType: offline ? .dataRendered : .dataPlayedBack) { [weak self] _ in
            self?.queue.async {
                guard let self, g == self.gen, let s = self.stream else { return }
                self.playedMs += ms
                if self.finished && self.playedMs >= self.queuedMs {
                    self.stream = nil
                    self.onEvent?(.done(stream: s, playedMs: self.playedMs))
                } else {
                    self.onEvent?(.progress(stream: s, playedMs: self.playedMs, bufferedMs: self.queuedMs - self.playedMs))
                }
            }
        }
    }

    func finish(stream s: UInt32) {
        guard s == stream else { return }
        finished = true
        if !started { startPlaying() }
        // review 2026-11-19: tts.end after the last buffer already played: no completion comes any more, so done now
        if queuedMs == 0 || (started && playedMs >= queuedMs) { stream = nil; onEvent?(.done(stream: s, playedMs: playedMs)) }
    }

    func stop(stream s: UInt32) {
        guard s == stream else { return }
        halt()
        stream = nil
        onEvent?(.stopped(stream: s, playedMs: playedMs))
    }

    func stopAll() { if let s = stream { stop(stream: s) } }

    private func halt() { gen += 1; player.stop() }
}
