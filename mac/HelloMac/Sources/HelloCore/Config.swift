import Foundation

/// ~/Library/Application Support/HelloMac/config.json (written by mac/HelloMac/setup.sh; the token sits in its own
/// file there, mode 600, never in this repo). The Mac reaches its own ear stack on the server through ssh -L.
public struct HelloConfig: Codable {
    public var sshTarget: String            // user@host (from the repo's .env SOUL_SERVER_SSH)
    public var sshOptions: [String]
    public var remotePort: Int              // soul-spike-mac on the server's loopback (tools/mac/deploy_stack.sh)
    public var localPort: Int
    public var tokenFile: String
    public var deviceId: String
    public var hearingPath: String
    public var powerOnNine: Bool            // the robot's power-on 9 (= all paid on): off unless the owner wants it

    public static var folder: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("HelloMac")
    }
    public static var file: URL { folder.appendingPathComponent("config.json") }

    public static func load() throws -> HelloConfig {
        let d = try Data(contentsOf: file)
        return try JSONDecoder().decode(HelloConfig.self, from: d)
    }

    public func token() throws -> String {
        try String(contentsOfFile: (tokenFile as NSString).expandingTildeInPath, encoding: .utf8)
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    public func url(_ path: String) -> URL { URL(string: "ws://127.0.0.1:\(localPort)\(path)")! }
}

/// ssh -N -L localPort:127.0.0.1:remotePort, restarted when it exits (the Mac sleeping, the network changing).
public final class Tunnel {
    private let cfg: HelloConfig
    private var proc: Process?
    private var want = false
    public private(set) var state = "off"
    public var onState: ((String) -> Void)?
    public var extraForwards: [(Int, Int)] = []        // (local, remote) e.g. the probe's mind / tts checks

    public init(cfg: HelloConfig) { self.cfg = cfg }

    public func start() { want = true; launch() }

    /// The ssh child dies with us: SIGTERM / SIGINT / SIGHUP stop the tunnel, then exit (a plain kill used to orphan it).
    public func stopOnSignals() {
        for s in [SIGTERM, SIGINT, SIGHUP] {
            signal(s, SIG_IGN)
            let src = DispatchSource.makeSignalSource(signal: s, queue: .main)
            src.setEventHandler { [weak self] in self?.proc?.terminate(); exit(0) }
            src.resume()
            signalSources.append(src)
        }
    }
    private var signalSources: [DispatchSourceSignal] = []

    public func stop() {
        want = false
        proc?.terminate()
        proc = nil
        set("off")
    }

    private func set(_ s: String) { state = s; onState?(s) }

    private func launch() {
        guard want, proc == nil else { return }
        // 2026-11-19 night: an orphaned tunnel (a killed app) on the port made a dead launch look "up", and every retry
        // reopened the links. A busy port is reported and retried, never taken for ours.
        if Self.portOpen(cfg.localPort) {
            // 2026-11-20: a crashed app (no signal handler runs on a crash) leaves its ssh behind, owned by launchd and
            // forwarding exactly our port to our port: that one is ours, close it. Anything else on the port is not.
            let mine = Self.orphans(ps: Self.psList(), local: cfg.localPort, remote: cfg.remotePort)
            for pid in mine { kill(pid, SIGTERM) }
            set(mine.isEmpty ? "busy: 127.0.0.1:\(cfg.localPort) 已被别的程序占用" : "关掉上次留下的隧道（\(mine.count) 个）")
            DispatchQueue.main.asyncAfter(deadline: .now() + (mine.isEmpty ? 5 : 1)) { [weak self] in self?.launch() }
            return
        }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/ssh")
        var args = cfg.sshOptions + ["-N", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15",
                                     "-o", "ServerAliveCountMax=3",
                                     "-L", "127.0.0.1:\(cfg.localPort):127.0.0.1:\(cfg.remotePort)"]
        for (l, r) in extraForwards { args += ["-L", "127.0.0.1:\(l):127.0.0.1:\(r)"] }
        args.append(cfg.sshTarget)
        p.arguments = args
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        p.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async {
                guard let self else { return }
                self.proc = nil
                self.set(self.want ? "retrying" : "off")
                if self.want { DispatchQueue.main.asyncAfter(deadline: .now() + 3) { self.launch() } }
            }
        }
        do { try p.run(); proc = p; set("starting") } catch { set("failed: \(error.localizedDescription)") }
        waitUp(0)
    }

    private func waitUp(_ n: Int) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { [weak self] in
            guard let self, let p = self.proc, p.isRunning else { return }
            if Self.portOpen(self.cfg.localPort) { self.set("up") }
            else if n < 60 { self.waitUp(n + 1) }
        }
    }

    /// `ps -axo pid=,ppid=,command=` lines -> the pids of ssh tunnels left by an earlier hello: parent launchd (1),
    /// /usr/bin/ssh, forwarding 127.0.0.1:local to 127.0.0.1:remote.
    public static func orphans(ps: String, local: Int, remote: Int) -> [Int32] {
        let fwd = "-L 127.0.0.1:\(local):127.0.0.1:\(remote)"
        return ps.split(separator: "\n").compactMap { line in
            let f = line.split(separator: " ", maxSplits: 2, omittingEmptySubsequences: true)
            guard f.count == 3, let pid = Int32(f[0]), f[1] == "1" else { return nil }
            let cmd = String(f[2])
            guard cmd.hasPrefix("/usr/bin/ssh ") || cmd.hasPrefix("ssh "), cmd.contains(fwd + " ") || cmd.hasSuffix(fwd) else { return nil }
            return pid
        }
    }

    static func psList() -> String {
        let p = Process(), pipe = Pipe()
        p.executableURL = URL(fileURLWithPath: "/bin/ps")
        p.arguments = ["-axo", "pid=,ppid=,command="]
        p.standardOutput = pipe
        p.standardError = FileHandle.nullDevice
        guard (try? p.run()) != nil else { return "" }
        let d = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return String(decoding: d, as: UTF8.self)
    }

    public static func portOpen(_ port: Int) -> Bool {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { return false }
        defer { close(fd) }
        var a = sockaddr_in()
        a.sin_family = sa_family_t(AF_INET)
        a.sin_port = in_port_t(UInt16(port).bigEndian)
        a.sin_addr.s_addr = inet_addr("127.0.0.1")
        return withUnsafePointer(to: &a) { p in
            p.withMemoryRebound(to: sockaddr.self, capacity: 1) { connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) == 0 }
        }
    }
}
