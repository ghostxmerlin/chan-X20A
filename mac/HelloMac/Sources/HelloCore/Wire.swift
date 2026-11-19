import Foundation

/// The robot's binary framings and JSON helpers (docs/mac-hello/robot-links.md §3.2, §4.3, §5.2).
public enum Wire {
    // SEA1: hearing audio, robot -> ear. "SEA1" | u32 LE seq | u64 LE capture µs | one Opus packet.
    public static func sea1(seq: UInt32, captureUs: UInt64, opus: [UInt8]) -> Data {
        var d = Data("SEA1".utf8)
        d.appendLE(seq)
        d.appendLE(captureUs)
        d.append(contentsOf: opus)
        return d
    }

    public struct Spk1: Equatable {
        public let stream: UInt32, seq: UInt32, frameMs: UInt16, flags: UInt16
        public let payload: Data
    }

    // SPK1: speech, ear -> robot. "SPK1" | u32 LE stream | u32 LE seq | u16 LE frame_ms | u16 LE flags | one Opus packet.
    public static func spk1(_ d: Data) -> Spk1? {
        guard d.count > 16, d.prefix(4) == Data("SPK1".utf8) else { return nil }
        let b = [UInt8](d)
        return Spk1(stream: le32(b, 4), seq: le32(b, 8), frameMs: le16(b, 12), flags: le16(b, 14),
                    payload: d.subdata(in: 16..<d.count))
    }

    public static func makeSpk1(stream: UInt32, seq: UInt32, opus: [UInt8]) -> Data {     // tests / probe
        var d = Data("SPK1".utf8)
        d.appendLE(stream); d.appendLE(seq); d.appendLE(UInt16(60)); d.appendLE(UInt16(0))
        d.append(contentsOf: opus)
        return d
    }

    // SVF1: camera frame, robot -> ear. "SVF1" | u16 LE header length | JSON header | JPEG. Whole <= 32768 bytes.
    public static func svf1(header: [String: Any], jpeg: Data) -> Data? {
        guard let h = try? JSONSerialization.data(withJSONObject: header, options: [.sortedKeys]), h.count <= 512 else {
            return nil
        }
        var d = Data("SVF1".utf8)
        d.appendLE(UInt16(h.count))
        d.append(h)
        d.append(jpeg)
        return d.count <= 32768 ? d : nil
    }

    public static func json(_ obj: [String: Any]) -> String {
        guard let d = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys]),
              let s = String(data: d, encoding: .utf8) else { return "{}" }
        return s
    }

    public static func parse(_ s: String) -> [String: Any]? {
        guard let d = s.data(using: .utf8), let o = try? JSONSerialization.jsonObject(with: d) as? [String: Any] else {
            return nil
        }
        return o
    }

    static func le32(_ b: [UInt8], _ i: Int) -> UInt32 {
        UInt32(b[i]) | UInt32(b[i + 1]) << 8 | UInt32(b[i + 2]) << 16 | UInt32(b[i + 3]) << 24
    }

    public static func le32test(_ b: [UInt8], _ i: Int) -> UInt64 { UInt64(le32(b, i)) | UInt64(le32(b, i + 4)) << 32 }

    static func le16(_ b: [UInt8], _ i: Int) -> UInt16 { UInt16(b[i]) | UInt16(b[i + 1]) << 8 }
}

extension Data {
    mutating func appendLE<T: FixedWidthInteger>(_ v: T) {
        var x = v.littleEndian
        Swift.withUnsafeBytes(of: &x) { append(contentsOf: $0) }
    }
}

/// Device identity in the robot's shape: a 12-hex device id (kept), a 16-hex boot id (new each start).
public struct DeviceIdentity {
    public let deviceId: String
    public let bootId: String
    public let firmware: String

    public init(deviceId: String, bootId: String = DeviceIdentity.newBootId(), firmware: String = "hello-mac-0.1.0") {
        self.deviceId = deviceId
        self.bootId = bootId
        self.firmware = firmware
    }

    public var source: String { "stackchan-mac-\(deviceId)" }     // "stackchan" prefix: the ear's robot profile

    public static func newBootId() -> String {
        (0..<8).map { _ in String(format: "%02x", UInt8.random(in: 0...255)) }.joined()
    }

    public static func newDeviceId() -> String {
        (0..<6).map { _ in String(format: "%02x", UInt8.random(in: 0...255)) }.joined()
    }
}

/// Monotonic microseconds (the robot's esp_timer clock stand-in).
public func monoUs() -> UInt64 { UInt64(DispatchTime.now().uptimeNanoseconds / 1000) }
public func wallMs() -> Int64 { Int64(Date().timeIntervalSince1970 * 1000) }
