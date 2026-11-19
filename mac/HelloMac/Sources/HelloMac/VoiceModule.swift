import AVFoundation
import Foundation
import HelloCore
import Speech

/// The robot's voice module (Unit ASR) on the Mac: Apple's on-device recognizer (en-US, never the network: if this Mac
/// cannot recognise English on device, the module stays off) on the same mic; an utterance is cut at a 0.8 s pause in
/// the transcript and counts only when it is exactly one of the module's words (AsrWords). Off by default (panel).
/// State lives on main; `request` is shared with the audio thread under `lock` (review 2026-11-20).
final class VoiceModule {
    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private let lock = NSLock()
    private var request: SFSpeechAudioBufferRecognitionRequest?     // under lock
    private var format: AVAudioFormat?                               // under lock: the format the request was fed
    private var task: SFSpeechRecognitionTask?
    private var transcript = "", changedAt = Date(), startedAt = Date()
    private var heardSelf = false                // hello sounded while this utterance was being heard: never a word
    private var failures = 0
    private var timer: Timer?
    private(set) var on = false
    private(set) var wanted = false              // the owner's switch (start / stop before the permission answer)
    private var asking = false
    var muted: () -> Bool = { false }            // hello is sounding: its own "hello" must not wake it
    var onWord: ((String, AsrWords.Kind) -> Void)?
    var onState: ((String) -> Void)?

    func start() {
        wanted = true
        guard !on, !asking else { return }
        asking = true
        SFSpeechRecognizer.requestAuthorization { st in
            DispatchQueue.main.async {
                self.asking = false
                guard self.wanted, !self.on else { return }      // stopped meanwhile, or already running
                guard st == .authorized else { self.onState?("语音口令：没有语音识别权限"); return }
                guard let r = self.recognizer, r.isAvailable else { self.onState?("语音口令：识别器不可用"); return }
                guard r.supportsOnDeviceRecognition else {
                    self.onState?("语音口令：这台 Mac 不能在本机识别英语（不上传声音，所以不开）"); return
                }
                self.on = true
                self.failures = 0
                self.newTask()
                self.timer = Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { [weak self] _ in self?.tick() }
                self.onState?("语音口令：开（本机识别英语口令）")
            }
        }
    }

    func stop() {
        wanted = false
        on = false
        timer?.invalidate(); timer = nil
        task?.cancel(); task = nil
        lock.lock(); let r = request; request = nil; format = nil; lock.unlock()
        r?.endAudio()
    }

    /// The mic's raw buffers (AudioIn.onBuffer), on the audio thread. A new input format starts a fresh request.
    func append(_ b: AVAudioPCMBuffer) {
        lock.lock()
        let r = request
        let changed = r != nil && format != nil && format != b.format
        if !changed { if format == nil && r != nil { format = b.format }; r?.append(b) }
        lock.unlock()
        if changed { DispatchQueue.main.async { [weak self] in self?.newTask() } }
    }

    private func newTask() {
        task?.cancel(); task = nil
        lock.lock(); let old = request; request = nil; format = nil; lock.unlock()
        old?.endAudio()
        guard on, let r = recognizer else { return }
        let req = SFSpeechAudioBufferRecognitionRequest()
        req.requiresOnDeviceRecognition = true
        req.shouldReportPartialResults = true
        req.taskHint = .confirmation
        req.contextualStrings = AsrWords.words
        transcript = ""; heardSelf = muted()
        startedAt = Date(); changedAt = Date()
        task = r.recognitionTask(with: req) { [weak self] result, error in
            DispatchQueue.main.async {
                guard let self, self.on, self.isCurrent(req) else { return }
                if let result {
                    self.failures = 0
                    let t = result.bestTranscription.formattedString
                    if t != self.transcript { self.transcript = t; self.changedAt = Date(); if self.muted() { self.heardSelf = true } }
                    if result.isFinal { self.finish() }
                } else if error != nil {
                    // a failing recognizer (asset gone, outage) must not spin: 0.5 s, 1 s, 2 s ... up to 30 s
                    self.failures += 1
                    let wait = min(30, 0.5 * pow(2, Double(self.failures - 1)))
                    self.lock.lock(); self.request = nil; self.lock.unlock()
                    DispatchQueue.main.asyncAfter(deadline: .now() + wait) { [weak self] in
                        guard let self, self.on, self.request == nil else { return }
                        self.newTask()
                    }
                }
            }
        }
        lock.lock(); request = req; lock.unlock()
    }

    private func isCurrent(_ req: SFSpeechAudioBufferRecognitionRequest) -> Bool {
        lock.lock(); defer { lock.unlock() }
        return request === req
    }

    private func tick() {
        guard on else { return }
        if muted() { heardSelf = true }
        if !transcript.isEmpty && Date().timeIntervalSince(changedAt) >= 0.8 { finish() }
        else if Date().timeIntervalSince(startedAt) > 50 { newTask() }       // a task has a time limit: start fresh
    }

    private func finish() {
        let t = transcript, selfHeard = heardSelf || muted()
        newTask()
        guard !t.isEmpty, !selfHeard, let m = AsrWords.match(t) else { return }
        onWord?(m.word, m.kind)
    }
}
