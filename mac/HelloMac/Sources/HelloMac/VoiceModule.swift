import AVFoundation
import Foundation
import HelloCore
import Speech

/// The robot's voice module (Unit ASR) on the Mac: Apple's on-device recognizer (en-US, never the network: if this Mac
/// cannot recognise English on device, the module stays off) on the same mic; an utterance is cut at a 0.8 s pause in
/// the transcript and counts only when it is exactly one of the module's words (AsrWords). Off by default (panel).
final class VoiceModule {
    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var transcript = "", changedAt = Date(), startedAt = Date()
    private var timer: Timer?
    private(set) var on = false
    var muted: () -> Bool = { false }          // hello is sounding: its own "hello" must not wake it
    var onWord: ((String, AsrWords.Kind) -> Void)?
    var onState: ((String) -> Void)?

    func start() {
        guard !on else { return }
        SFSpeechRecognizer.requestAuthorization { st in
            DispatchQueue.main.async {
                guard st == .authorized else { self.onState?("语音口令：没有语音识别权限"); return }
                guard let r = self.recognizer, r.isAvailable else { self.onState?("语音口令：识别器不可用"); return }
                guard r.supportsOnDeviceRecognition else {
                    self.onState?("语音口令：这台 Mac 不能在本机识别英语（不上传声音，所以不开）"); return
                }
                self.on = true
                self.newTask()
                self.timer = Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { [weak self] _ in self?.tick() }
                self.onState?("语音口令：开（本机识别英语口令）")
            }
        }
    }

    func stop() {
        on = false
        timer?.invalidate(); timer = nil
        task?.cancel(); task = nil
        request?.endAudio(); request = nil
    }

    /// The mic's raw buffers (AudioIn.onBuffer), any thread.
    func append(_ b: AVAudioPCMBuffer) { request?.append(b) }

    private func newTask() {
        task?.cancel()
        request?.endAudio()
        guard on, let r = recognizer else { return }
        let req = SFSpeechAudioBufferRecognitionRequest()
        req.requiresOnDeviceRecognition = true
        req.shouldReportPartialResults = true
        req.taskHint = .confirmation
        req.contextualStrings = AsrWords.words
        request = req
        transcript = ""
        startedAt = Date(); changedAt = Date()
        task = r.recognitionTask(with: req) { [weak self] result, error in
            DispatchQueue.main.async {
                guard let self, self.on, self.request === req else { return }
                if let result {
                    let t = result.bestTranscription.formattedString
                    if t != self.transcript { self.transcript = t; self.changedAt = Date() }
                    if result.isFinal { self.finish() }
                } else if error != nil {
                    self.newTask()
                }
            }
        }
    }

    private func tick() {
        guard on else { return }
        if !transcript.isEmpty && Date().timeIntervalSince(changedAt) >= 0.8 { finish() }
        else if Date().timeIntervalSince(startedAt) > 50 { newTask() }       // a task has a time limit: start fresh
    }

    private func finish() {
        let t = transcript
        newTask()
        guard !t.isEmpty, !muted(), let m = AsrWords.match(t) else { return }
        onWord?(m.word, m.kind)
    }
}
