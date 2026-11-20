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

    static func uid(_ id: AudioDeviceID) -> String? { string(id, kAudioDevicePropertyDeviceUID) }

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
    private var converter: AVAudioConverter?
    private let out = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 24000, channels: 1, interleaved: true)!
    var onPCM: (([Int16], UInt64) -> Void)?
    var onBuffer: ((AVAudioPCMBuffer) -> Void)?          // raw, for the voice module
    var onLevel: ((Float) -> Void)?
    var onRestart: ((String) -> Void)?                   // on main
    /// main thread's view: true from start() until it fails or stop()
    private(set) var running = false
    private(set) var deviceName = "—"
    // 2026-11-20: an AudioQueue, not AVAudioEngine. The engine ties the input to the output device and rebuilt itself on
    // every configuration change: a Bluetooth headset (its mic switches it to the call profile) sent one a second, the
    // rebuilds fed the loop, and a pending Apple callback on a freed engine crashed the app; earlier the main thread
    // waited in the engine and the window froze. The queue is input only, converts to 24 kHz mono itself and rides out
    // the device's format changes. Control on `q`, buffers on `io` (AudioQueueStop waits for the buffer callbacks).
    // A watchdog reopens it when no buffer came for 3 s (device gone), at most 5 times a minute.
    private let q = DispatchQueue(label: "hello.mic")
    private let io = DispatchQueue(label: "hello.mic.io")
    private var aq: AudioQueueRef?                        // on q
    private var wanted = false, device: AudioDeviceID?    // on q
    private var reopens: [Date] = []
    private var watchdog: DispatchSourceTimer?
    private let lock = NSLock()
    private var lastBuffer = Date.distantPast             // under lock
    static let rate = 24000.0, frames: UInt32 = 1200, buffers = 4    // 50 ms buffers
    static let silentLimit = 3.0, maxReopens = 5

    /// Open the chosen input (nil = the system default) off the main thread; `done` on main: nil = open, else why not.
    func start(device: AudioDeviceID?, done: ((String?) -> Void)? = nil) {
        running = true
        q.async {
            self.wanted = true
            self.device = device
            self.reopens = []
            let why = self.open()
            let name = self.currentName()
            DispatchQueue.main.async {
                self.deviceName = name
                if why != nil { self.running = false }
                done?(why)
            }
        }
    }

    func stop() {
        running = false
        q.async { self.wanted = false; self.close() }
    }

    private func currentName() -> String { AudioDevices.inputs().first { $0.id == device }?.name ?? "默认麦克风" }

    /// on q
    private func open() -> String? {
        close()
        let mono = prepare(rate: Self.rate)
        var asbd = AudioStreamBasicDescription(mSampleRate: Self.rate, mFormatID: kAudioFormatLinearPCM,
                                               mFormatFlags: kAudioFormatFlagIsFloat | kAudioFormatFlagIsPacked,
                                               mBytesPerPacket: 4, mFramesPerPacket: 1, mBytesPerFrame: 4,
                                               mChannelsPerFrame: 1, mBitsPerChannel: 32, mReserved: 0)
        var made: AudioQueueRef?
        var st = AudioQueueNewInputWithDispatchQueue(&made, &asbd, 0, io) { [weak self] queue, buf, _, _, _ in
            let n = Int(buf.pointee.mAudioDataByteSize) / 4
            if n > 0, let self, let b = AVAudioPCMBuffer(pcmFormat: mono, frameCapacity: AVAudioFrameCount(n)) {
                b.frameLength = AVAudioFrameCount(n)
                memcpy(b.floatChannelData![0], buf.pointee.mAudioData, n * 4)
                self.lock.lock(); self.lastBuffer = Date(); self.lock.unlock()
                self.handle(b, mono: mono)
            }
            AudioQueueEnqueueBuffer(queue, buf, 0, nil)
        }
        guard st == noErr, let made else { return "麦克风打不开（\(st)）" }
        aq = made
        if let device, let uid = AudioDevices.uid(device) {
            var cf = uid as CFString
            st = AudioQueueSetProperty(made, kAudioQueueProperty_CurrentDevice, &cf, UInt32(MemoryLayout<CFString>.size))
            if st != noErr { close(); return "选不了这个麦克风（\(st)）" }
        }
        for _ in 0..<Self.buffers {
            var b: AudioQueueBufferRef?
            if AudioQueueAllocateBuffer(made, Self.frames * 4, &b) == noErr, let b { AudioQueueEnqueueBuffer(made, b, 0, nil) }
        }
        st = AudioQueueStart(made, nil)
        if st != noErr { close(); return "麦克风打不开（\(st)，权限？）" }
        lock.lock(); lastBuffer = Date(); lock.unlock()
        let t = DispatchSource.makeTimerSource(queue: q)
        t.schedule(deadline: .now() + 1, repeating: 1)
        t.setEventHandler { [weak self] in self?.check() }
        t.resume()
        watchdog = t
        return nil
    }

    /// on q
    private func close() {
        watchdog?.cancel(); watchdog = nil
        if let a = aq { AudioQueueStop(a, true); AudioQueueDispose(a, true); aq = nil }
    }

    /// on q, every second: no buffer for 3 s while wanted = the device went away or stalled; reopen (limited)
    private func check() {
        guard wanted, aq != nil else { return }
        lock.lock(); let quiet = Date().timeIntervalSince(lastBuffer); lock.unlock()
        guard quiet >= Self.silentLimit else { return }
        let now = Date()
        reopens = reopens.filter { now.timeIntervalSince($0) < 60 } + [now]
        if reopens.count > Self.maxReopens {
            wanted = false
            close()
            DispatchQueue.main.async {
                self.running = false
                self.onRestart?("麦克风一分钟里断了 \(Self.maxReopens) 次以上，先停了；请换个麦克风再选一次")
            }
            return
        }
        let why = open()
        let name = currentName()
        DispatchQueue.main.async {
            self.deviceName = name
            self.running = why == nil
            self.onRestart?(why == nil ? "麦克风 3 秒没声音，已重新打开：\(name)" : "麦克风断了，重新打开失败：\(why!)")
        }
    }

    /// The converter for an input rate -> the mono format channel 0 is copied into.
    func prepare(rate: Double) -> AVAudioFormat {
        let mono = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: rate, channels: 1, interleaved: false)!
        converter = AVAudioConverter(from: mono, to: out)
        return mono
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
