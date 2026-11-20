import AppKit
import AVFoundation
import HelloCore

// hello on the Mac (2026-11-19 night, owner: "把hello机身固件的功能，移植到一个mac app中"): the robot's links from a
// Mac window. Its own server stack (soul-spike-mac via ssh -L, tools/mac/deploy_stack.sh), its own copy of the data.
final class AppDelegate: NSObject, NSApplicationDelegate {
    var hello: Hello?
    var deck: DeckServer?
    var faceWin: NSWindow!
    var panelWin: NSWindow!
    let face = FaceView(frame: NSRect(x: 0, y: 0, width: 640, height: 520))
    let logView = NSTextView()
    var micPop = NSPopUpButton(), camPop = NSPopUpButton(), stylePop = NSPopUpButton()
    var checks: [String: NSButton] = [:]
    let faceName = NSTextField(string: ""), voiceName = NSTextField(string: "")
    let thresholdSlider = NSSlider(), thresholdLabel = NSTextField(labelWithString: "")
    var showPreview = false

    func applicationDidFinishLaunching(_ n: Notification) {
        face.eyesDir = Self.eyesDir()
        face.luyeDir = Self.luyeDir()
        if let s = UserDefaults.standard.string(forKey: "eyeStyle") { face.style = s }
        face.showIdle()
        faceWin = NSWindow(contentRect: NSRect(x: 120, y: 200, width: 640, height: 520),
                           styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        faceWin.title = "hello"
        faceWin.contentView = face
        faceWin.contentAspectRatio = NSSize(width: 640, height: 520)
        faceWin.makeKeyAndOrderFront(nil)
        buildPanel()
        NSApp.activate(ignoringOtherApps: true)
        do {
            let cfg = try HelloConfig.load()
            let h = try Hello(cfg: cfg)
            hello = h
            h.face = face
            deck = DeckServer(hello: h)          // the Mac deck's robot pane (monitor-mac.sh); nil without deck.token
            h.book.threshold = Float(thresholdSlider.doubleValue)
            h.onLog = { [weak self] s in self?.log(s) }
            h.onChange = { [weak self] in self?.refresh() }
            face.onTap = { double in h.tap(double: double) }
            h.camera.onPreview = { [weak self] cg in
                DispatchQueue.main.async { if self?.showPreview == true { self?.face.setPreview(cg) } }
            }
            let argv = CommandLine.arguments
            let noMic = argv.contains("--no-mic"), noCam = argv.contains("--no-camera")
            let begin = { h.start(mic: !noMic, camera: !noCam) }
            if noMic && noCam { begin() } else { askPermissions(begin) }
            if let i = argv.firstIndex(of: "--snapshot"), i + 1 < argv.count {     // render the face to a PNG and quit
                let path = argv[i + 1]
                DispatchQueue.main.asyncAfter(deadline: .now() + 8) { [weak self] in
                    guard let self else { return }
                    self.face.setStatus(h.statusLine())
                    for (v, name) in [(self.face as NSView, path), (self.panelWin.contentView!, path.replacingOccurrences(of: ".png", with: "-panel.png"))] {
                        if let rep = v.bitmapImageRepForCachingDisplay(in: v.bounds) {
                            v.cacheDisplay(in: v.bounds, to: rep)
                            try? rep.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: name))
                        }
                    }
                    NSApp.terminate(nil)
                }
            }
            Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
                guard let self, let h = self.hello else { return }
                self.face.setStatus(h.statusLine())
                let state = h.core.sync { h.mouth.modes.state }
                self.face.setMode(ModeRules.label(state), colour: ModeRules.def(state.mode).color)
                let eye = ModeRules.def(state.mode).eye
                if !self.stylePinned, ["v05", "v14", "v18"].contains(eye), self.face.style != eye { self.face.style = eye }
            }
        } catch {
            log("没有配置：先在仓库里运行 mac/HelloMac/setup.sh（\(error.localizedDescription)）")
        }
    }

    func applicationWillTerminate(_ n: Notification) { deck?.stop(); hello?.stop() }
    func applicationShouldTerminateAfterLastWindowClosed(_ s: NSApplication) -> Bool { true }

    static func eyesDir() -> URL? {
        if let r = Bundle.main.resourceURL?.appendingPathComponent("eyes"), FileManager.default.fileExists(atPath: r.path) { return r }
        let repo = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .appendingPathComponent("../../../../assets/soul-eyes/v2-front/previews").standardized
        return FileManager.default.fileExists(atPath: repo.path) ? repo : nil
    }

    /// 鹿野's frames: inside the app (build-app.sh), else the checkouts' .private/luye-pet (swift run); never in the repo
    static func luyeDir() -> URL? {
        let here = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        var dirs = [Bundle.main.resourceURL?.appendingPathComponent("luye"),
                    here.appendingPathComponent("../../../../.private/luye-pet").standardized]
        if let main = try? String(contentsOf: here.appendingPathComponent("../../../../.git").standardized, encoding: .utf8),
           let r = main.range(of: "gitdir: ") {               // a worktree: the main checkout's .private
            let gitdir = main[r.upperBound...].trimmingCharacters(in: .whitespacesAndNewlines)
            if let w = gitdir.range(of: "/.git/worktrees/") { dirs.append(URL(fileURLWithPath: String(gitdir[..<w.lowerBound]) + "/.private/luye-pet")) }
        }
        return dirs.compactMap { $0 }.first { FileManager.default.fileExists(atPath: $0.appendingPathComponent("animation-manifest.json").path) }
    }

    private func askPermissions(_ then: @escaping () -> Void) {
        AVCaptureDevice.requestAccess(for: .audio) { _ in
            AVCaptureDevice.requestAccess(for: .video) { _ in DispatchQueue.main.async { then() } }
        }
    }

    // ---- the panel: the robot's settings page, the monitor's switches ----
    private func buildPanel() {
        panelWin = NSWindow(contentRect: NSRect(x: 780, y: 120, width: 380, height: 640),
                            styleMask: [.titled, .miniaturizable, .resizable], backing: .buffered, defer: false)
        panelWin.title = "hello 设置"
        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 8
        stack.edgeInsets = NSEdgeInsets(top: 12, left: 12, bottom: 12, right: 12)

        micPop.target = self; micPop.action = #selector(micPicked)
        camPop.target = self; camPop.action = #selector(camPicked)
        stack.addArrangedSubview(row("麦克风", micPop))
        stack.addArrangedSubview(row("摄像头", camPop))
        for s in ["v14", "v05", "v18"] { stylePop.addItem(withTitle: s) }
        if face.luye != nil { stylePop.addItem(withTitle: "鹿野") }
        if let s = UserDefaults.standard.string(forKey: "eyeStyle") { stylePop.selectItem(withTitle: s == "luye" ? "鹿野" : s) }
        stylePop.target = self; stylePop.action = #selector(stylePicked)
        stack.addArrangedSubview(row("脸", stylePop))
        let boxes = NSStackView(); boxes.orientation = .horizontal; boxes.spacing = 10
        let boxes2 = NSStackView(); boxes2.orientation = .horizontal; boxes2.spacing = 10
        for (k, title) in [("hear", "听觉"), ("cam", "摄像头"), ("up", "上传画面"), ("faces", "认人"), ("asr", "语音口令"), ("pre", "小画面"), ("mirror", "眼睛左右反")] {
            let b = NSButton(checkboxWithTitle: title, target: self, action: #selector(toggled(_:)))
            b.identifier = NSUserInterfaceItemIdentifier(k)
            b.state = k == "pre" ? .off : k == "asr" ? (UserDefaults.standard.bool(forKey: "voiceModule") ? .on : .off)
                : k == "mirror" ? (UserDefaults.standard.bool(forKey: "gazeMirror") ? .on : .off) : .on
            checks[k] = b
            (["hear", "cam", "up", "faces"].contains(k) ? boxes : boxes2).addArrangedSubview(b)
        }
        stack.addArrangedSubview(boxes)
        stack.addArrangedSubview(boxes2)
        let btns = NSStackView(); btns.orientation = .horizontal
        btns.addArrangedSubview(NSButton(title: "说 Providence", target: self, action: #selector(wake)))
        btns.addArrangedSubview(NSButton(title: "单击", target: self, action: #selector(tap1)))
        btns.addArrangedSubview(NSButton(title: "双击", target: self, action: #selector(tap2)))
        btns.addArrangedSubview(NSButton(title: "全开 9", target: self, action: #selector(nine)))
        btns.addArrangedSubview(NSButton(title: "全关 0", target: self, action: #selector(zero)))
        stack.addArrangedSubview(btns)
        faceName.placeholderString = "名字"; voiceName.placeholderString = "名字"
        faceName.widthAnchor.constraint(equalToConstant: 140).isActive = true
        voiceName.widthAnchor.constraint(equalToConstant: 140).isActive = true
        stack.addArrangedSubview(row("录人脸", faceName, NSButton(title: "看摄像头 5 秒", target: self, action: #selector(enrollFace))))
        stack.addArrangedSubview(row("录声纹", voiceName, NSButton(title: "说五句话", target: self, action: #selector(enrollVoice))))
        thresholdSlider.minValue = 0.6; thresholdSlider.maxValue = 0.98
        thresholdSlider.doubleValue = UserDefaults.standard.object(forKey: "faceThreshold") as? Double ?? 0.82
        thresholdSlider.target = self; thresholdSlider.action = #selector(thresholdMoved)
        thresholdSlider.widthAnchor.constraint(equalToConstant: 180).isActive = true
        thresholdLabel.stringValue = String(format: "%.2f", thresholdSlider.doubleValue)
        stack.addArrangedSubview(row("认人门槛", thresholdSlider, thresholdLabel))
        let note = NSTextField(wrappingLabelWithString: "点 hello 的脸 = 单击屏幕（聊天模式下开始一段聊天）；连点两下 = 双击。没有舵机：转头、点头由眼睛动作代替。花钱的开关在观察台。")
        note.textColor = .secondaryLabelColor; note.font = .systemFont(ofSize: 11)
        note.preferredMaxLayoutWidth = 350
        stack.addArrangedSubview(note)
        let scroll = NSScrollView()
        scroll.hasVerticalScroller = true
        logView.isEditable = false
        logView.font = .systemFont(ofSize: 12)
        logView.autoresizingMask = [.width]
        scroll.documentView = logView
        scroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 280).isActive = true
        scroll.widthAnchor.constraint(equalToConstant: 354).isActive = true
        stack.addArrangedSubview(scroll)
        panelWin.contentView = stack
        panelWin.orderFront(nil)
        fillDevices()
    }

    private func row(_ title: String, _ views: NSView...) -> NSStackView {
        let l = NSTextField(labelWithString: title)
        l.widthAnchor.constraint(equalToConstant: 56).isActive = true
        let r = NSStackView(views: [l] + views)
        r.orientation = .horizontal
        return r
    }

    private var mics: [AudioDevice] = []
    private var cams: [AVCaptureDevice] = []
    private func fillDevices() {
        mics = AudioDevices.inputs()
        micPop.removeAllItems()
        micPop.addItem(withTitle: "系统默认")
        for m in mics { micPop.addItem(withTitle: m.name) }
        cams = Camera.devices()
        camPop.removeAllItems()
        camPop.addItem(withTitle: "系统默认")
        for c in cams { camPop.addItem(withTitle: c.localizedName) }
    }

    @objc func micPicked() {
        guard let h = hello else { return }
        let i = micPop.indexOfSelectedItem
        h.micDevice = i > 0 ? mics[i - 1].id : nil
        if h.hearingOn { h.setHearing(true) }
    }

    @objc func camPicked() {
        guard let h = hello else { return }
        let i = camPop.indexOfSelectedItem
        h.cameraDevice = i > 0 ? cams[i - 1].uniqueID : nil
        if h.cameraOn { h.setCamera(true) }
    }

    /// the owner's pick wins; until then the eyes follow the mode, as the robot's (crowd v14, chat v05, companion v18)
    var stylePinned = UserDefaults.standard.string(forKey: "eyeStyle") != nil
    @objc func stylePicked() {
        let t = stylePop.titleOfSelectedItem ?? "v14"
        face.style = t == "鹿野" ? "luye" : t
        stylePinned = true
        UserDefaults.standard.set(face.style, forKey: "eyeStyle")
    }

    @objc func toggled(_ b: NSButton) {
        guard let h = hello else { return }
        let on = b.state == .on
        switch b.identifier?.rawValue {
        case "hear": h.setHearing(on)
        case "cam": h.setCamera(on)
        case "up": h.setUpload(on)
        case "faces": h.setFaces(on)
        case "pre": showPreview = on; if !on { face.setPreview(nil) }
        case "asr": h.setVoiceModule(on)
        case "mirror": UserDefaults.standard.set(on, forKey: "gazeMirror")
        default: break
        }
    }

    @objc func thresholdMoved() {
        let v = thresholdSlider.doubleValue
        thresholdLabel.stringValue = String(format: "%.2f", v)
        hello?.book.threshold = Float(v)
        UserDefaults.standard.set(v, forKey: "faceThreshold")
    }

    @objc func wake() { hello?.wake() }
    @objc func tap1() { hello?.tap(double: false) }
    @objc func tap2() { hello?.tap(double: true) }
    @objc func nine() { hello?.digit(9) }
    @objc func zero() { hello?.digit(0) }
    @objc func enrollFace() { let n = faceName.stringValue.trimmingCharacters(in: .whitespaces); if !n.isEmpty { hello?.enrollFace(n) } }
    @objc func enrollVoice() { let n = voiceName.stringValue.trimmingCharacters(in: .whitespaces); if !n.isEmpty { hello?.enrollVoice(n) } }

    func refresh() {
        guard let h = hello else { return }
        checks["hear"]?.state = h.hearingOn ? .on : .off
        checks["cam"]?.state = h.cameraOn ? .on : .off
    }

    func log(_ s: String) {
        let f = DateFormatter(); f.dateFormat = "HH:mm:ss"
        let line = "\(f.string(from: Date()))  \(s)\n"
        logView.textStorage?.append(NSAttributedString(string: line, attributes: [.foregroundColor: NSColor.labelColor,
                                                                                    .font: NSFont.systemFont(ofSize: 12)]))
        if let ts = logView.textStorage, ts.length > 40000 { ts.deleteCharacters(in: NSRange(location: 0, length: ts.length - 30000)) }
        logView.scrollToEndOfDocument(nil)
    }
}

if CommandLine.arguments.contains("--selftest") { exit(AppSelfTest.run()) }
let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
