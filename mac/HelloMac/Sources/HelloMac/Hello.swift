import AppKit
import AVFoundation
import Foundation
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
    private var timer: DispatchSourceTimer?
    private let started = Date()
    weak var face: FaceView?
    var onLog: ((String) -> Void)?
    var onChange: (() -> Void)?

    // owner switches (the panel)
    private(set) var hearingOn = true, cameraOn = true, uploadOn = true, facesOn = true
    var micDevice: AudioDeviceID? = nil
    var cameraDevice: String? = nil
    private(set) var linkState: [String: String] = [:]
    private(set) var lastHeard = "", lastSpeaker = "", micPeak: Float = 0

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
                DispatchQueue.main.async { self?.linkState[l.name] = s; self?.onChange?() }
            }
        }
        hearing.onEvent = { [weak self] e in self?.heard(e) }
        vision.onError = { [weak self] m in self?.log("视觉：\(m)") }
        mic.onPCM = { [weak self] pcm, us in self?.core.async { self?.hearing.feed(pcm, firstUs: us) } }
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
            for (name, f) in ev where name == "face_named" { self?.log("认出：\(f["name"] ?? "")") }
        }
        camera.onEnrolled = { [weak self] name, n in self?.log("人脸录入 \(name)：\(n) 张") }
    }

    func start(mic: Bool = true, camera useCamera: Bool = true) {
        tunnel.onState = { [weak self] s in
            guard let self else { return }
            self.linkState["隧道"] = s
            self.onChange?()
            if s == "up" { for l in self.links { l.start() } }
        }
        tunnel.start()
        let t = DispatchSource.makeTimerSource(queue: core)
        t.schedule(deadline: .now() + 1, repeating: .milliseconds(100))
        var n = 0
        t.setEventHandler { [weak self] in
            guard let self else { return }
            self.mouth.tick()
            n += 1
            if n % 10 == 0 { self.hearing.watchdog(); self.vision.keepAlive() }
        }
        t.resume()
        timer = t
        if mic { setHearing(true) } else { hearingOn = false }
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
        if on { do { try mic.start(device: micDevice); log("麦克风：\(mic.deviceName)") } catch { log("麦克风打不开：\(error.localizedDescription)") } }
        else { mic.stop() }
        onChange?()
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
        if !on { let ev = camera.tracker.clear(); core.async { for (n, f) in ev { self.vision.event(n, f) } } }
        onChange?()
    }

    func tap(double: Bool) { core.async { let o = self.mouth.touch(double: double); DispatchQueue.main.async { self.face?.showToast(o.toast) } } }
    func digit(_ v: Int) { core.async { self.mouth.sendDigit(v, source: "touch") } }
    func wake() { core.async { self.mouth.asrWake() } }
    func enrollVoice(_ name: String) { core.async { self.hearing.enroll(name: name) }; log("声纹录入 \(name)：请说五句话") }
    func enrollFace(_ name: String) { camera.enroll(name: name); log("人脸录入 \(name)：看着摄像头 5 秒") }

    // ---- hearing events ----
    private func heard(_ e: [String: Any]) {
        let t = e["type"] as? String ?? ""
        if t == "speech", e["phase"] as? String == "end" {
            let text = e["text"] as? String ?? ""
            let sp = (e["speaker"] as? [String: Any])?["name"] as? String ?? ""
            DispatchQueue.main.async { self.lastHeard = text; self.lastSpeaker = sp; self.onChange?() }
            log("听到\(sp.isEmpty ? "" : "（\(sp)）")：\(text)")
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
        case "clip": return (false, "no_clip", [:])
        case "status":
            return (true, nil, ["fw": id.firmware, "up_s": Int(Date().timeIntervalSince(started)), "mode": mouth.modes.state.mode.id,
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

    func statusLine() -> String {
        let links = ["隧道", "听觉", "嘴", "视觉"].map { "\($0) \(linkState[$0] ?? "—")" }.joined(separator: " · ")
        let p = micPeak; micPeak = 0
        let db = p > 0 ? Int(20 * log10(p)) : -99
        return "\(links) · 麦 \(hearingOn ? "\(db) dB" : "关") · 摄像头 \(cameraOn ? "开" : "关") · \(id.deviceId)"
    }
}
