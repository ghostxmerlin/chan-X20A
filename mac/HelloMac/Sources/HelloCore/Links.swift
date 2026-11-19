import Foundation

/// What a link's logic sends through (the WebSocket, or a recorder in the self-test).
public protocol Transport: AnyObject {
    func send(text: String)
    func send(data: Data)
    func reconnect(_ why: String)
}

/// The logic side of one robot link: driven by open / text / binary / close, sends through `transport`.
public protocol LinkLogic: AnyObject {
    var transport: Transport? { get set }
    func opened()
    func received(text: String)
    func received(data: Data)
    func closed(_ why: String)
}

/// One robot WebSocket to the ear (Authorization: Bearer, as the robot), reconnecting with backoff 2..30 s.
/// Every callback runs on `queue` (the app's one core queue).
public final class WSLink: NSObject, Transport, URLSessionWebSocketDelegate {
    public let name: String
    public private(set) var url: URL
    private let token: String
    private let queue: DispatchQueue
    private weak var logic: LinkLogic?
    private var session: URLSession!
    private var task: URLSessionWebSocketTask?
    private var want = false
    private var gen = 0
    private var backoff = 2.0
    public private(set) var state = "idle"
    public var onState: ((String) -> Void)?

    public init(name: String, url: URL, token: String, queue: DispatchQueue, logic: LinkLogic) {
        self.name = name; self.url = url; self.token = token; self.queue = queue; self.logic = logic
        super.init()
        let oq = OperationQueue()
        oq.underlyingQueue = queue
        oq.maxConcurrentOperationCount = 1
        session = URLSession(configuration: .ephemeral, delegate: self, delegateQueue: oq)
        logic.transport = self
    }

    public func start() { queue.async { self.want = true; self.connect() } }
    public func stop() {
        queue.async {
            self.want = false
            self.gen += 1
            self.task?.cancel(with: .goingAway, reason: nil)
            self.task = nil
            self.set("idle")
        }
    }

    private func set(_ s: String) { state = s; onState?(s) }

    private func connect() {
        guard want, task == nil else { return }
        gen += 1
        var req = URLRequest(url: url)
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        req.timeoutInterval = 3600            // review: a long-lived socket (ticks / pings keep it alive; no 10 s idle cut)
        let t = session.webSocketTask(with: req)
        t.maximumMessageSize = 1 << 20
        task = t
        set("connecting")
        t.resume()
        receive(t, gen)
    }

    private func receive(_ t: URLSessionWebSocketTask, _ g: Int) {
        t.receive { [weak self] result in
            guard let self else { return }
            self.queue.async {
                guard g == self.gen, self.task === t else { return }
                switch result {
                case .success(.string(let s)): self.logic?.received(text: s)
                case .success(.data(let d)): self.logic?.received(data: d)
                case .success: break
                case .failure(let e): self.drop("receive: \(e.localizedDescription)"); return
                }
                self.receive(t, g)
            }
        }
    }

    public func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask, didOpenWithProtocol p: String?) {
        guard webSocketTask === task else { return }
        backoff = 2
        set("open")
        logic?.opened()
    }

    public func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask,
                           didCloseWith code: URLSessionWebSocketTask.CloseCode, reason: Data?) {
        guard webSocketTask === task else { return }
        let why = reason.flatMap { String(data: $0, encoding: .utf8) } ?? ""
        drop("closed \(code.rawValue) \(why)")
    }

    public func urlSession(_ session: URLSession, task t: URLSessionTask, didCompleteWithError error: Error?) {
        guard t === task else { return }
        drop("ended: \(error?.localizedDescription ?? "ok")")
    }

    private func drop(_ why: String) {
        guard task != nil else { return }
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        gen += 1
        logic?.closed(why)
        set("retrying")
        guard want else { set("idle"); return }
        let wait = backoff
        backoff = min(30, backoff * 2)
        queue.asyncAfter(deadline: .now() + wait) { [weak self] in self?.connect() }
    }

    // Transport
    public func send(text: String) {
        guard let t = task, state == "open" else { return }
        t.send(.string(text)) { [weak self] e in if let e { self?.queue.async { if self?.task === t { self?.drop("send: \(e.localizedDescription)") } } } }
    }

    public func send(data: Data) {
        guard let t = task, state == "open" else { return }
        t.send(.data(data)) { [weak self] e in if let e { self?.queue.async { if self?.task === t { self?.drop("send: \(e.localizedDescription)") } } } }
    }

    public func reconnect(_ why: String) { queue.async { self.drop(why) } }
}

/// Self-test transport: records what the logic sent.
public final class RecordingTransport: Transport {
    public var texts: [[String: Any]] = []
    public var datas: [Data] = []
    public var reconnects: [String] = []
    public init() {}
    public func send(text: String) { texts.append(Wire.parse(text) ?? [:]) }
    public func send(data: Data) { datas.append(data) }
    public func reconnect(_ why: String) { reconnects.append(why) }
    public func types() -> [String] { texts.compactMap { $0["type"] as? String } }
}
