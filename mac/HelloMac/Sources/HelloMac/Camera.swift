import AppKit
import AVFoundation
import CoreImage
import Foundation
import HelloCore
import Vision

/// The Mac camera in the robot's two roles: the body camera (a 320x240 JPEG when the picture changed, at most 1/s,
/// FW/frame_change.h) and the M12 face module (faces found / named / lost in 640x480 boxes, recognised locally).
final class Camera: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    private let session = AVCaptureSession()
    private let output = AVCaptureVideoDataOutput()
    private let camQueue = DispatchQueue(label: "hello.camera")
    private let ci = CIContext()
    private var change = FrameChange()
    private var lastFrameUs: UInt64 = 0, lastFaceUs: UInt64 = 0
    private var printAt: [Int: UInt64] = [:]
    private var pendingSnap: Int? = nil
    private var enrolling: (name: String, until: UInt64, n: Int)? = nil
    let tracker: FaceTracker
    var uploadFrames = true
    var facesOn = true
    private(set) var running = false
    private(set) var deviceName = "—"
    /// (jpeg, w, h, captureUs, diff, snap id)
    var onFrame: ((Data, Int, Int, UInt64, Int, Int?) -> Void)?
    var onFaceEvents: (([(String, [String: Any])]) -> Void)?
    var onFaces: (([CGRect]) -> Void)?             // for the preview overlay, in 640x480
    var onEnrolled: ((String, Int) -> Void)?
    var onPreview: ((CGImage) -> Void)?

    init(tracker: FaceTracker) { self.tracker = tracker }

    static func devices() -> [AVCaptureDevice] {
        AVCaptureDevice.DiscoverySession(deviceTypes: [.builtInWideAngleCamera, .external, .continuityCamera],
                                         mediaType: .video, position: .unspecified).devices
    }

    func start(deviceID: String?) throws {
        stop()
        guard let dev = Self.devices().first(where: { $0.uniqueID == deviceID }) ?? AVCaptureDevice.default(for: .video) else {
            throw NSError(domain: "hello", code: 2, userInfo: [NSLocalizedDescriptionKey: "没有摄像头"])
        }
        session.beginConfiguration()
        session.sessionPreset = session.canSetSessionPreset(.vga640x480) ? .vga640x480 : .medium
        for i in session.inputs { session.removeInput(i) }
        let input = try AVCaptureDeviceInput(device: dev)
        guard session.canAddInput(input) else { session.commitConfiguration(); throw NSError(domain: "hello", code: 3) }
        session.addInput(input)
        if session.outputs.isEmpty {
            output.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA]
            output.alwaysDiscardsLateVideoFrames = true
            output.setSampleBufferDelegate(self, queue: camQueue)
            if session.canAddOutput(output) { session.addOutput(output) }
        }
        session.commitConfiguration()
        session.startRunning()
        deviceName = dev.localizedName
        running = true
        change.reset()
    }

    func stop() {
        if session.isRunning { session.stopRunning() }
        if running { clearFaces() }
        running = false
    }

    func snap(_ id: Int) { camQueue.async { self.pendingSnap = id } }

    /// faces off: every track lost (on the camera queue, where the tracker lives)
    func clearFaces() { camQueue.async { let ev = self.tracker.clear(); if !ev.isEmpty { self.onFaceEvents?(ev) } } }

    func enroll(name: String) { camQueue.async { self.enrolling = (name, monoUs() + 5_000_000, 0) } }

    func captureOutput(_ o: AVCaptureOutput, didOutput sb: CMSampleBuffer, from c: AVCaptureConnection) {
        guard let px = CMSampleBufferGetImageBuffer(sb) else { return }
        let now = monoUs()
        guard now - lastFrameUs >= 100_000 else { return }          // ~10 fps, as the robot's camera
        lastFrameUs = now
        CVPixelBufferLockBaseAddress(px, .readOnly)
        let w = CVPixelBufferGetWidth(px), h = CVPixelBufferGetHeight(px)
        let thumb = FrameChange.thumb(bgra: CVPixelBufferGetBaseAddress(px)!.assumingMemoryBound(to: UInt8.self),
                                      width: w, height: h, rowBytes: CVPixelBufferGetBytesPerRow(px))
        CVPixelBufferUnlockBaseAddress(px, .readOnly)
        let image = CIImage(cvPixelBuffer: px)
        if let snapId = pendingSnap {
            pendingSnap = nil
            if let jpeg = jpeg(image) { onFrame?(jpeg, 320, 240, now, 255, snapId) }
        } else if uploadFrames, let diff = change.shouldSend(thumb, atUs: now), let jpeg = jpeg(image) {
            onFrame?(jpeg, 320, 240, now, diff, nil)
        }
        if let cg = ci.createCGImage(image, from: image.extent) { onPreview?(cg) }
        if facesOn && now - lastFaceUs >= 200_000 {                    // faces at ~5 fps
            lastFaceUs = now
            faces(px, w: w, h: h, now: now)
        }
    }

    private func jpeg(_ image: CIImage) -> Data? {
        let s = 320 / image.extent.width
        let small = image.transformed(by: CGAffineTransform(scaleX: s, y: s))
        guard let cg = ci.createCGImage(small, from: CGRect(x: 0, y: 0, width: 320, height: 240)) else { return nil }
        return NSBitmapImageRep(cgImage: cg).representation(using: .jpeg, properties: [.compressionFactor: 0.6])
    }

    private func faces(_ px: CVPixelBuffer, w: Int, h: Int, now: UInt64) {
        let req = VNDetectFaceRectanglesRequest()
        let handler = VNImageRequestHandler(cvPixelBuffer: px, options: [:])
        try? handler.perform([req])
        let obs = (req.results ?? []).filter { $0.boundingBox.width * CGFloat(w) >= 40 }
        var seen: [FaceSeen] = []
        let sx = 640 / CGFloat(w), sy = 480 / CGFloat(h)
        for o in obs {
            let bb = o.boundingBox            // normalised, origin bottom-left
            let box = CGRect(x: bb.minX * CGFloat(w) * sx, y: (1 - bb.maxY) * CGFloat(h) * sy,
                             width: bb.width * CGFloat(w) * sx, height: bb.height * CGFloat(h) * sy)
            seen.append(FaceSeen(box: box, prob: Double(o.confidence)))
        }
        // prints: for tracks not decided yet (every 0.4 s each), and for an enrollment (the largest face)
        let largest = seen.indices.max { seen[$0].box.width < seen[$1].box.width }
        let needEnroll = enrolling != nil && now < enrolling!.until
        for (i, o) in obs.enumerated() {
            let t = tracker.tracks.first { FaceTracker.iouPublic($0.box, seen[i].box) > 0.25 }
            let undecided = t == nil || !(t!.decided)
            let due = now - (printAt[t?.id ?? -1] ?? 0) >= 400_000
            guard (undecided && due) || (needEnroll && i == largest) else { continue }
            if let p = featurePrint(px, o.boundingBox) {
                seen[i].print = p
                if let tid = t?.id { printAt[tid] = now }
                if needEnroll && i == largest {
                    enrolling!.n += 1
                    tracker.book.add(enrolling!.name, p)
                }
            }
        }
        if let e = enrolling, now >= e.until {
            enrolling = nil
            onEnrolled?(e.name, e.n)
        }
        let ev = tracker.update(seen, atUs: now)
        onFaces?(seen.map(\.box))
        if !ev.isEmpty { onFaceEvents?(ev) }
    }

    /// A face's identity vector: Vision's image feature print of the face crop (a bit larger than the box).
    /// Not a dedicated face model (none on this Mac): good enough to tell a few household faces apart in one room.
    private func featurePrint(_ px: CVPixelBuffer, _ bb: CGRect) -> [Float]? {
        let grow = bb.insetBy(dx: -bb.width * 0.15, dy: -bb.height * 0.15).intersection(CGRect(x: 0, y: 0, width: 1, height: 1))
        let req = VNGenerateImageFeaturePrintRequest()
        req.imageCropAndScaleOption = .scaleFill
        req.regionOfInterest = grow
        let handler = VNImageRequestHandler(cvPixelBuffer: px, options: [:])
        guard (try? handler.perform([req])) != nil, let fp = req.results?.first, fp.elementType == .float else { return nil }
        return fp.data.withUnsafeBytes { Array($0.bindMemory(to: Float.self)) }
    }
}
