import AppKit
import AVFoundation
import Foundation
import IOKit.ps
import HelloCore

/// hello on the Mac: the three robot links to the Mac's own ear stack, the mic, the speaker, the camera, the face.
/// Everything protocol-side runs on `core`; UI on main.
final class Hello: MouthDelegate {
    let cfg: HelloConfig
    let id: DeviceIdentity
    let core = DispatchQueue(label: "hello.core")
    let tunnel: Tunnel
    let hearing: HearingLogic
    let mouth: MouthLogic
    let vision: VisionLogic
    let out: AudioOut
    let mic = AudioIn()
    let camera: Camera
    let book: FaceBook
    private var links: [WSLink] = []
    let voices = Voices()
    let voiceModule = VoiceModule()
    private var policy = ResponsePolicy()
    private var quiet = QuietWait()
    private var timer: DispatchSourceTimer?
    private let started = Date()
    weak var face: FaceView?
    var onLog: ((String) -> Void)?
    var onChange: (() -> Void)?

    // owner switches (the panel)
    private(set) var hearingOn = true { didSet { publishDeck() } }
    private(set) var cameraOn = true { didSet { publishDeck() } }
    private(set) var facesOn = true { didSet { publishDeck() } }
    private(set) var uploadOn = true
    var micDevice: AudioDeviceID? = nil
    var cameraDevice: String? = nil
    private(set) var linkState: [String: String] = [:] { didSet { publishDeck() } }
    private(set) var lastHeard = "", lastSpeaker = "", micPeak: Float = 0
    /// the camera's last face pass (main thread): how many faces and when, for the observation deck's vision state
    private(set) var facesSeen: (n: Int, at: Date)? { didSet { publishDeck() } }
    /// What the observation deck reads (DeckServer's own queue), copied on main whenever it changes: the deck must
    /// never wait on the main thread (2026-11-20: a stuck main thread took the deck down with it).
    let deckView = DeckView()
    /// heard sentences, responses and link changes for the deck's log (telemetry hearing.events)
    let deckEvents = DeckEvents()
    private var answering = (text: "", emotion: "")       // core: the sentence a held clip answers
    func publishDeck() { deckView.set((linkState, facesSeen, hearingOn, cameraOn, facesOn)) }

    init(cfg: HelloConfig) throws {
        self.cfg = cfg
        id = DeviceIdentity(deviceId: cfg.deviceId)
        tunnel = Tunnel(cfg: cfg)
        hearing = HearingLogic(id: id)
        out = AudioOut(queue: core)
        mouth = MouthLogic(id: id, output: out)
        vision = VisionLogic(id: id)
        book = FaceBook(url: HelloConfig.folder.appendingPathComponent("faces.json"))
        camera = Camera(tracker: FaceTracker(book: book))
        mouth.powerOnNine = cfg.powerOnNine
        mouth.delegate = self
        mouth.onSounding = { [weak self] s in self?.hearing.setSounding(s) }
        let token = try cfg.token()
        links = [WSLink(name: "听觉", url: cfg.url(cfg.hearingPath), token: token, queue: core, logic: hearing),
                 WSLink(name: "嘴", url: cfg.url("/mouth"), token: token, queue: core, logic: mouth),
                 WSLink(name: "视觉", url: cfg.url("/vision"), token: token, queue: core, logic: vision)]
        for l in links {
            l.onState = { [weak self] s in
                DispatchQueue.main.async {
                    guard let self else { return }
                    let was = self.linkState[l.name]
                    self.linkState[l.name] = s
                    if l.name == "听觉", s != was, s == "open" || was == "open" {
                        self.deckEvents.add("link", label: s == "open" ? "connected" : "transport_closed")
                    }
                    self.onChange?()
                }
            }
        }
        hearing.onEvent = { [weak self] e in self?.heard(e) }
        hearing.onOpen = { [weak self] in self?.policy.newSession(); self?.quiet.clear() }   // on core
        vision.onError = { [weak self] m in self?.log("视觉：\(m)") }
        vision.onOpen = { [weak self] in self?.camera.resetChange() }
        mic.onRestart = { [weak self] m in self?.log(m) }
        mic.onBuffer = { [weak self] b in self?.voiceModule.append(b) }
        voiceModule.muted = { [weak self] in self?.core.sync { self?.mouth.sounding ?? false } ?? false }
        voiceModule.onState = { [weak self] s in self?.log(s) }
        voiceModule.onWord = { [weak self] w, k in self?.core.async { self?.asrWord(w, k) } }
        mic.onPCM = { [weak self] pcm, us in
            guard let self, self.hearingOn else { return }          // hearing off: nothing goes up (the voice module still hears)
            self.core.async { self.hearing.feed(pcm, firstUs: us) }
        }
        mic.onLevel = { [weak self] p in self?.micPeak = max(self?.micPeak ?? 0, p) }
        camera.onFrame = { [weak self] jpeg, w, h, us, diff, snap in
            self?.core.async {
                guard let self else { return }
                let seq = self.vision.frame(jpeg: jpeg, width: w, height: h, captureUs: us, diff: diff, snap: snap)
                if let snap { self.mouth.snapResult(id: snap, ok: seq != nil, why: seq == nil ? "no_uplink" : nil,
                                                    seq: seq ?? 0, bytes: jpeg.count, ms: 0) }
            }
        }
        camera.onFaceEvents = { [weak self] ev in
            self?.core.async { for (name, f) in ev { self?.vision.event(name, f) } }
            for (name, f) in ev where name == "face_named" || name == "face_unknown" {   // sim: to tune the threshold
                self?.log(name == "face_named" ? "认出：\(f["name"] ?? "")（相似度 \(f["sim"] ?? "")）"
                                               : "陌生人（最像的相似度 \(f["sim"] ?? "")，门槛 \(self?.book.threshold ?? 0)）")
            }
        }
        camera.onEnrolled = { [weak self] name, n in self?.log("人脸录入 \(name)：\(n) 张") }
        camera.onFaces = { [weak self] boxes in                       // the eyes follow the largest face
            let big = boxes.max { $0.width < $1.width }
            DispatchQueue.main.async {
                self?.facesSeen = (boxes.count, Date())
                self?.face?.gaze(at: big?.midX, y: big?.midY, mirror: UserDefaults.standard.bool(forKey: "gazeMirror"))
            }
        }
    }

    func start(mic: Bool = true, camera useCamera: Bool = true) {
        tunnel.onState = { [weak self] s in
            guard let self else { return }
            self.linkState["隧道"] = s
            self.onChange?()
            if s == "up" {                                          // WSLink.start is idempotent (task == nil guard)
                self.linksStarted = true
                for (i, l) in self.links.enumerated() where i != 0 || self.hearingOn { l.start() }
            }
        }
        tunnel.stopOnSignals()
        tunnel.start()
        let t = DispatchSource.makeTimerSource(queue: core)
        t.schedule(deadline: .now() + 1, repeating: .milliseconds(100))
        var n = 0
        t.setEventHandler { [weak self] in
            guard let self else { return }
            self.mouth.tick()
            self.respondTick()
            n += 1
            if n % 10 == 0 { self.hearing.watchdog(); self.vision.keepAlive() }
        }
        t.resume()
        timer = t
        if mic { setHearing(true) } else { hearingOn = false }
        if mic && UserDefaults.standard.bool(forKey: "voiceModule") { voiceModule.start(); ensureMic() }
        if useCamera { setCamera(true) } else { cameraOn = false }
        DispatchQueue.main.async { self.face?.setMode(self.mouth.modes.label, colour: self.mouth.modes.colour) }
    }

    func stop() {
        for l in links { l.stop() }
        mic.stop(); camera.stop(); tunnel.stop()
    }

    // ---- owner switches ----
    func setHearing(_ on: Bool) {
        hearingOn = on
        if linksStarted { if on { links[0].start() } else { links[0].stop() } }   // the robot's hearing link runs only while listening
        ensureMic(restart: on)
        onChange?()
    }

    private var linksStarted = false

    /// The mic runs while hearing or the voice module wants it (the module must hear "nine" after a "zero").
    func ensureMic(restart: Bool = false) {
        let need = hearingOn || voiceModule.wanted
        if need && (restart || !mic.running) {
            mic.start(device: micDevice) { [weak self] why in             // off the main thread; the answer on main
                guard let self else { return }
                if let why { self.log("麦克风打不开：\(why)") }
                else { self.log("麦克风：\(self.mic.deviceName)\(self.hearingOn ? "" : "（只给语音口令）")") }
            }
        } else if !need && mic.running { mic.stop() }
    }

    func setCamera(_ on: Bool) {
        cameraOn = on
        if on {
            do { try camera.start(deviceID: cameraDevice); log("摄像头：\(camera.deviceName)") ; core.async { self.vision.event("vision_on") } }
            catch { log("摄像头打不开：\(error.localizedDescription)") }
        } else { camera.stop(); core.async { self.vision.event("vision_off") } }
        onChange?()
    }

    func setUpload(_ on: Bool) { uploadOn = on; camera.uploadFrames = on; onChange?() }
    func setFaces(_ on: Bool) {
        facesOn = on; camera.facesOn = on
        if !on { camera.clearFaces() }
        onChange?()
    }

    func tap(double: Bool) { core.async { let o = self.mouth.touch(double: double); DispatchQueue.main.async { self.face?.showToast(o.toast) } } }
    func digit(_ v: Int) { core.async { self.mouth.sendDigit(v, source: "touch") } }
    func wake() { core.async { self.mouth.asrWake() } }
    func enrollVoice(_ name: String) { core.async { self.hearing.enroll(name: name) }; log("声纹录入 \(name)：请说五句话") }
    func enrollFace(_ name: String) { camera.enroll(name: name); log("人脸录入 \(name)：看着摄像头 5 秒") }

    // ---- the robot's own answer to a sentence (FW/hearing.cpp:185-211), on the core queue ----
    private func respond(_ e: [String: Any]) {
        guard hearingOn else { return }
        let text = e["text"] as? String ?? ""
        let emotion = e["emotion"] as? String ?? "unknown"
        guard let idStr = e["id"] as? String, idStr.hasPrefix("s-"), let id = UInt32(idStr.dropFirst(2)) else { return }
        let end = ((e["pos"] as? [Any])?.last as? NSNumber)?.int64Value
        let rx = (e["_rx"] as? NSNumber)?.int64Value
        let dbg = e["debug"] as? [String: Any] ?? [:]
        let processing = ((dbg["server_ms"] as? NSNumber)?.doubleValue ?? 100000) + ((dbg["tick_wait_ms"] as? NSNumber)?.doubleValue ?? 100000)
        let age = (rx ?? 0) - (end ?? Int64.max)
        let valid = end != nil && rx != nil && age >= 0 && age <= 80000 && processing >= 0 && processing <= 5000 &&
            e["event"] as? String == "speech" && (e["voice"] as? [String: Any])?["acoustic_band"] as? String == "voice" && !text.isEmpty
        let selfFlag = ((e["self"] as? [String: Any])?["sounding"] as? Bool) ?? true
        let isSelf = selfFlag || mouth.sounding
        if quiet.pending { return }                                 // one response at a time
        let allowed = (e["respond"] as? Bool) ?? true               // 嘴 on TTS / off: the ear says false
        let clip = allowed && voices.count > 0 ?
            policy.consider(id: id, emotion: emotion, speech: valid, selfSounding: isSelf, ageMs: valid ? Int(processing) : -1,
                            now: monoUs(), random: UInt32.random(in: 0...UInt32.max), tags: voices.tags) : 0
        if clip != 0 { quiet.hold(clip, now: monoUs()); answering = (text, emotion) }
        else if !isSelf && !text.isEmpty, let eye = Respond.eye(for: emotion) { DispatchQueue.main.async { self.face?.play(eye) } }
    }

    private func respondTick() {
        guard quiet.pending else { return }
        let clip = quiet.clip
        switch quiet.poll(now: monoUs(), speaking: hearing.serverSpeaking || mouth.sounding, lastSpeech: hearing.lastSpeakingUs) {
        case .play:
            quiet.clear()
            if playClip(clip, why: "回应") { deckEvents.add("response", label: answering.text, detail: "\(answering.emotion)/\(clip)//") }
        case .expired:
            log("回应 PLAY-\(String(format: "%03d", clip))：10 秒没安静下来，不说了")
            deckEvents.add("response", label: answering.text, detail: "\(answering.emotion)/\(clip)/quiet_timeout/10000")
        case .wait: break
        }
    }

    @discardableResult
    private func playClip(_ id: Int, why: String) -> Bool {
        guard let pcm = voices.pcm(id), mouth.playLocal(pcm) else { return false }
        policy.accepted(now: monoUs())
        let emo = voices.tags[id] ?? ""
        if let eye = Respond.eye(for: emo) { DispatchQueue.main.async { self.face?.play(eye, seconds: Double(pcm.count) / 24000) } }
        log("\(why)：PLAY-\(String(format: "%03d", id))（\(emo)）")
        return true
    }

    // ---- the voice module's words (core queue), as FW/asr_unit.cpp:177-191 ----
    private func asrWord(_ word: String, _ kind: AsrWords.Kind) {
        log("口令：\(word)")
        switch kind {
        case .wake:
            mouth.asrWake(word: word == "hi asr" ? "hi A S R" : word == "hi m five" ? "Hi M Five" : "hello")
            return
        default:
            mouth.asrOrder(word)                  // Docking skips the sentence that carried an order
        }
        switch kind {
        case .mode(let input): let o = mouth.input(input, by: "asr"); DispatchQueue.main.async { self.face?.showToast(o.toast) }
        case .digit(let d): mouth.sendDigit(d, source: "asr")
        case .motion(let w):
            let g = w.contains("left") ? "left" : w.contains("right") ? "right" : w == "up" || w == "down" ? "nod" : "front"
            DispatchQueue.main.async { self.face?.gesture(g) }
        case .volume(let how):
            let v: Float = ["increase": min(1, out.volume + 0.2), "decrease": max(0.1, out.volume - 0.2),
                            "maximum": 1, "medium": 0.6, "minimum": 0.1][how] ?? out.volume
            out.volume = v
            DispatchQueue.main.async { self.face?.showToast("VOL \(Int(v * 100))") }
        default: break
        }
    }

    func setVoiceModule(_ on: Bool) {
        if on { voiceModule.start() } else { voiceModule.stop(); log("语音口令：关") }
        UserDefaults.standard.set(on, forKey: "voiceModule")
        ensureMic()
    }

    // ---- hearing events ----
    private func heard(_ e: [String: Any]) {
        let t = e["type"] as? String ?? ""
        if t == "speech", e["phase"] as? String == "end" {
            respond(e)
            let text = e["text"] as? String ?? ""
            let sp = (e["speaker"] as? [String: Any])?["name"] as? String ?? ""
            DispatchQueue.main.async { self.lastHeard = text; self.lastSpeaker = sp; self.onChange?() }
            log("听到\(sp.isEmpty ? "" : "（\(sp)）")：\(text)")
            let d = DeckEvents.speech(e)
            deckEvents.add("speech", label: d.label, detail: d.detail, p: d.p)
        } else if t == "enroll" {
            log("声纹录入：\(e["phase"] ?? "")\(e["n"].map { " \($0)/5" } ?? "")")
        } else if t == "keyword" {
            log("关键词：\(e["label"] ?? e["keyword"] ?? "")")
        }
    }

    func log(_ s: String) { DispatchQueue.main.async { self.onLog?(s) } }

    // ---- MouthDelegate (core queue) ----
    func command(_ verb: String, args: [String: Any]) -> (ok: Bool, error: String?, data: [String: Any]) {
        switch verb {
        case "eyes":
            let emotion = ["happy": "happy", "sad": "sad", "angry": "angry", "fearful": "scared", "disgusted": "refusing",
                           "surprised": "surprised"]
            let s = args["state"] as? String ?? "idle"
            let state = emotion[s] ?? s
            DispatchQueue.main.async { self.face?.play(state) }
            return (true, nil, ["state": state])
        case "word":
            let text = args["text"] as? String ?? ""
            let hold = args["hold_ms"] as? Int ?? 2000
            DispatchQueue.main.async { self.face?.showWord(text, holdMs: hold) }
            return (true, nil, ["text": text, "hold_ms": hold, "stays": hold > 2000])
        case "nod", "shake":
            DispatchQueue.main.async { self.face?.gesture(verb) }
            return (true, nil, ["gesture": verb, "times": args["times"] ?? 2, "amp_deg": 0])
        case "yaw":
            let to = args["to"] as? String ?? ""
            DispatchQueue.main.async { self.face?.gesture(to.hasPrefix("left") ? "left" : "right") }
            return (true, nil, ["action": "yaw", "facing": "front", "tracking": false])
        case "front", "turn_around", "tracking":
            return (true, nil, ["action": verb, "facing": "front", "tracking": false])
        case "volume":
            var v = Int(out.volume * 100)
            if let l = args["level"] as? Int { v = l } else if let s = args["step"] as? Int { v += s }
            v = max(10, min(100, v))
            out.volume = Float(v) / 100
            return (true, nil, ["volume": v])
        case "face_learn":
            guard let name = args["name"] as? String, !name.isEmpty else { return (false, "bad_args", [:]) }
            DispatchQueue.main.async { self.enrollFace(name) }
            return (true, nil, ["name": name])
        case "clip":                                  // the SD voice library (Resources/voices)
            guard voices.count > 0 else { return (false, "no_clip", [:]) }
            if mouth.speaking { return (false, "speaking", [:]) }
            var id = (args["clip_id"] as? Int) ?? 0
            if id == 0, let emo = args["emotion"] as? String {
                id = Respond.clip(emotion: emo, random: UInt32.random(in: 0...UInt32.max), tags: voices.tags, last: policy.lastClip)
            }
            if id == 0 { id = Int.random(in: 1...voices.count) }
            guard (1...voices.count).contains(id), playClip(id, why: "放台词") else { return (false, "no_clip", [:]) }
            return (true, nil, ["clip_id": id, "emotion": voices.tags[id] ?? ""])
        case "status":
            let (battery, charging) = Self.battery()
            return (true, nil, ["fw": id.firmware, "up_s": Int(Date().timeIntervalSince(started)), "mode": mouth.modes.state.mode.id,
                                "battery": battery.map { $0 as Any } ?? NSNull(), "charging": charging,
                                "online": true, "volume": Int(out.volume * 100), "speaking": mouth.speaking,
                                "camera": cameraOn, "m12": facesOn ? "detecting" : "standby", "motor": "none"])
        default: return (false, "unknown_verb", [:])
        }
    }

    func stage(_ stage: String) { DispatchQueue.main.async { self.face?.setStage(stage) } }
    func burst(_ m: [String: Any]) {
        DispatchQueue.main.async { self.face?.setBurst(on: m["on"] as? Bool ?? false, leftS: m["left_s"] as? Int ?? 0, looks: m["looks"] as? Int ?? 0) }
    }
    func toast(_ text: String, code: String) { DispatchQueue.main.async { self.face?.showToast(text) } }
    func snap(id: Int) {
        if cameraOn { camera.snap(id) } else { mouth.snapResult(id: id, ok: false, why: "camera_off") }
    }
    func localDigit(_ value: Int) {
        DispatchQueue.main.async {
            if value == 0 { self.setHearing(false); self.setCamera(false) }
            if value == 9 { self.setHearing(true); self.setCamera(true) }
        }
    }

    /// The Mac's battery (IOKit power sources): percent or nil (a desktop), charging / on AC.
    static func battery() -> (Int?, Bool) {
        guard let info = IOPSCopyPowerSourcesInfo()?.takeRetainedValue(),
              let list = IOPSCopyPowerSourcesList(info)?.takeRetainedValue() as? [CFTypeRef] else { return (nil, true) }
        for ps in list {
            guard let d = IOPSGetPowerSourceDescription(info, ps)?.takeUnretainedValue() as? [String: Any],
                  d[kIOPSTypeKey] as? String == kIOPSInternalBatteryType else { continue }
            let cur = d[kIOPSCurrentCapacityKey] as? Int ?? 0, max = d[kIOPSMaxCapacityKey] as? Int ?? 100
            let ac = d[kIOPSPowerSourceStateKey] as? String == kIOPSACPowerValue
            return (max > 0 ? cur * 100 / max : nil, ac)
        }
        return (nil, true)
    }

    func statusLine() -> String {
        let links = ["隧道", "听觉", "嘴", "视觉"].map { "\($0) \(linkState[$0] ?? "—")" }.joined(separator: " · ")
        let p = micPeak; micPeak = 0
        let db = p > 0 ? Int(20 * log10(p)) : -99
        return "\(links) · 麦 \(hearingOn ? "\(db) dB" : "关") · 摄像头 \(cameraOn ? "开" : "关") · \(id.deviceId)"
    }
}

/// A locked copy of hello's state for the deck's queue.
final class DeckView {
    typealias State = (links: [String: String], seen: (n: Int, at: Date)?, hearing: Bool, camera: Bool, faces: Bool)
    private let lock = NSLock()
    private var s: State = ([:], nil, true, true, true)
    func set(_ v: State) { lock.lock(); s = v; lock.unlock() }
    func get() -> State { lock.lock(); defer { lock.unlock() }; return s }
}
