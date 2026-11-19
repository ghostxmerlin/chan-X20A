import Foundation
import HelloCore
import Network

/// The robot's HTTP face for the Mac stack's observation deck (monitor-mac.sh, web/monitor DemoRobotClient): loopback
/// only, its own token (deck.token, written by setup.sh). GET /v1/info, GET /v1/telemetry (mode, hearing, camera),
/// POST /v1/control {target:"mode", input, value} = the page's mode bar, applied by hello itself (by "page").
final class DeckServer {
    static let port: UInt16 = 18891
    private let listener: NWListener
    private let token: String
    private let queue = DispatchQueue(label: "hello.deck")
    private weak var hello: Hello?
    private var tseq = 0

    init?(hello: Hello) {
        guard let t = try? String(contentsOf: HelloConfig.folder.appendingPathComponent("deck.token"), encoding: .utf8)
                .trimmingCharacters(in: .whitespacesAndNewlines), !t.isEmpty else { return nil }
        token = t
        self.hello = hello
        let params = NWParameters.tcp
        params.requiredLocalEndpoint = NWEndpoint.hostPort(host: "127.0.0.1", port: NWEndpoint.Port(rawValue: Self.port)!)
        guard let l = try? NWListener(using: params) else { return nil }
        listener = l
        listener.newConnectionHandler = { [weak self] c in self?.serve(c) }
        listener.start(queue: queue)
    }

    func stop() { listener.cancel() }

    private func serve(_ c: NWConnection) {
        c.start(queue: queue)
        var buf = Data()
        func read() {
            c.receive(minimumIncompleteLength: 1, maximumLength: 65536) { [weak self] data, _, done, err in
                guard let self else { c.cancel(); return }
                if let data { buf.append(data) }
                if let req = Self.parse(buf) {
                    let (code, body) = self.handle(req)
                    let payload = Data(Wire.json(body).utf8)
                    var head = "HTTP/1.1 \(code) \(code == 200 ? "OK" : "Error")\r\nContent-Type: application/json; charset=utf-8\r\n"
                    head += "Content-Length: \(payload.count)\r\nConnection: close\r\n\r\n"
                    c.send(content: Data(head.utf8) + payload, completion: .contentProcessed { _ in c.cancel() })
                } else if done || err != nil || buf.count > 65536 {
                    c.cancel()
                } else { read() }
            }
        }
        read()
    }

    struct Request { let method: String; let path: String; let headers: [String: String]; let body: Data }

    static func parse(_ d: Data) -> Request? {
        guard let end = d.range(of: Data("\r\n\r\n".utf8)), let head = String(data: d[..<end.lowerBound], encoding: .utf8) else { return nil }
        var lines = head.components(separatedBy: "\r\n")
        let first = lines.removeFirst().split(separator: " ")
        guard first.count >= 2 else { return nil }
        var h: [String: String] = [:]
        for l in lines { if let i = l.firstIndex(of: ":") { h[l[..<i].lowercased()] = l[l.index(after: i)...].trimmingCharacters(in: .whitespaces) } }
        guard let len = Int(h["content-length"] ?? "0"), len >= 0, len <= 65536 else {      // answered 400, closed
            return Request(method: "BAD", path: "", headers: [:], body: Data())
        }
        let bodyStart = end.upperBound
        guard d.count - bodyStart >= len else { return nil }
        return Request(method: String(first[0]), path: String(first[1].split(separator: "?").first ?? ""), headers: h,
                       body: d.subdata(in: bodyStart..<bodyStart + len))
    }

    /// constant-time comparison (length leaks only)
    static func same(_ a: String, _ b: String) -> Bool {
        let x = Array(a.utf8), y = Array(b.utf8)
        guard x.count == y.count else { return false }
        var d: UInt8 = 0
        for i in 0..<x.count { d |= x[i] ^ y[i] }
        return d == 0
    }

    private func handle(_ r: Request) -> (Int, [String: Any]) {
        if r.method == "BAD" { return (400, ["error": "bad content-length"]) }
        guard let auth = r.headers["authorization"], Self.same(auth, "Bearer \(token)") else { return (401, ["error": "bad token"]) }
        guard let h = hello else { return (503, ["error": "hello not ready"]) }
        switch (r.method, r.path) {
        case ("GET", "/v1/info"):
            return (200, ["boot_id": h.id.bootId, "firmware": h.id.firmware, "device_id": h.id.deviceId,
                          "capabilities": ["web_control": "supported", "unified_control": "supported"]])
        case ("GET", "/v1/telemetry"):
            tseq += 1
            let m = h.core.sync { h.mouth.modes.state }
            let mode: [String: Any] = ["mode": m.mode.id, "dir": m.dir?.rawValue ?? NSNull(), "host": m.hosted ? "event" : NSNull(),
                                       "return_to": m.returnTo?.id ?? NSNull(), "seq": h.core.sync { h.mouth.modes.seq }, "by": "mac"]
            return (200, ["boot_id": h.id.bootId, "seq": tseq, "timestamp_us": Int(monoUs()), "mode": mode,
                          "hearing": ["requested": h.hearingOn, "state": h.hearingOn ? "listening" : "idle"],
                          "health": ["camera_enabled": h.cameraOn], "device": "mac"])
        case ("POST", "/v1/control"):
            guard let b = (try? JSONSerialization.jsonObject(with: r.body)) as? [String: Any], b["target"] as? String == "mode",
                  let name = b["input"] as? String, let input = ModeRules.Input(rawValue: name),
                  ![.boot, .timeout].contains(input) else { return (400, ["error": "only target mode, a known input"]) }
            let o = h.core.sync { h.mouth.input(input, value: ModeRules.Value.from(input, b["value"]), by: "page") }
            DispatchQueue.main.async { h.face?.showToast(o.toast) }
            return (200, ["accepted": true, "result": ["code": o.code.rawValue, "toast": o.toast]])
        default:
            return (404, ["error": "not found"])
        }
    }
}
