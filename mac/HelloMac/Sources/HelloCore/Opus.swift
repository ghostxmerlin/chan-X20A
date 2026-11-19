import COpus
import Foundation

/// Opus as the robot uses it: 24 kHz mono, 60 ms frames (1440 samples). Uplink 40 kbps VBR (FW/hearing.cpp:236-243).
public final class OpusEncoder {
    public static let sampleRate: Int32 = 24000
    public static let frameSamples = 1440
    private let enc: OpaquePointer
    private var out = [UInt8](repeating: 0, count: 4000)

    public init?(bitrate: Int32 = 40000) {
        var err: Int32 = 0
        guard let e = opus_encoder_create(Self.sampleRate, 1, OPUS_APPLICATION_AUDIO, &err), err == OPUS_OK else { return nil }
        enc = e
        _ = copus_set_bitrate(e, bitrate)
        _ = copus_set_vbr(e, 1)
        _ = copus_set_complexity(e, 5)
    }

    deinit { opus_encoder_destroy(enc) }

    /// One 60 ms frame (exactly frameSamples) -> one packet.
    public func encode(_ pcm: [Int16]) -> [UInt8]? {
        guard pcm.count == Self.frameSamples else { return nil }
        let n = pcm.withUnsafeBufferPointer { p in opus_encode(enc, p.baseAddress!, Int32(Self.frameSamples), &out, 4000) }
        return n > 0 ? Array(out[0..<Int(n)]) : nil
    }
}

public final class OpusDecoder {
    private let dec: OpaquePointer
    private var buf = [Int16](repeating: 0, count: 5760)

    public init?(sampleRate: Int32 = 24000) {
        var err: Int32 = 0
        guard let d = opus_decoder_create(sampleRate, 1, &err), err == OPUS_OK else { return nil }
        dec = d
    }

    deinit { opus_decoder_destroy(dec) }

    public func decode(_ packet: Data) -> [Int16]? {
        let bytes = [UInt8](packet)
        let n = bytes.withUnsafeBufferPointer { p in
            opus_decode(dec, p.baseAddress!, Int32(bytes.count), &buf, Int32(buf.count), 0)
        }
        return n > 0 ? Array(buf[0..<Int(n)]) : nil
    }
}

