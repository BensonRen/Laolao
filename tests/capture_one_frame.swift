// capture_one_frame.swift
//
// Grab one frame from a named AVFoundation camera and write it as PNG.
// Used by tests/test_virtualcam_macos.py.
//
// Usage:  swift capture_one_frame.swift "<device-name>" <out.png> [timeout-seconds] [warmup-frames]
// Exit:   0 on success, 1 on timeout/error
//
// Prints to stdout:
//   OK <width> <height> <ms-to-first-frame>
// or:
//   FAIL <reason>

import Foundation
import AVFoundation
import CoreImage
import AppKit

guard CommandLine.arguments.count >= 3 else {
    print("FAIL usage: capture_one_frame.swift <device> <out.png> [timeout] [warmup]")
    exit(2)
}

let targetName  = CommandLine.arguments[1]
let outPath     = CommandLine.arguments[2]
let timeoutSec  = Double(CommandLine.arguments.count > 3 ? CommandLine.arguments[3] : "8") ?? 8
let warmup      = Int(CommandLine.arguments.count > 4 ? CommandLine.arguments[4] : "3") ?? 3

let discovery = AVCaptureDevice.DiscoverySession(
    deviceTypes: [.external, .builtInWideAngleCamera, .continuityCamera, .deskViewCamera],
    mediaType: .video,
    position: .unspecified
)
guard let device = discovery.devices.first(where: { $0.localizedName == targetName }) else {
    print("FAIL device-not-found '\(targetName)'")
    exit(1)
}

final class FrameGrabber: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    let warmup: Int
    let outURL: URL
    let started = Date()
    var seen = 0
    var saved = false
    let done = DispatchSemaphore(value: 0)
    var width = 0
    var height = 0
    var elapsedMs = 0

    init(warmup: Int, outPath: String) {
        self.warmup = warmup
        self.outURL = URL(fileURLWithPath: outPath)
    }

    func captureOutput(_ output: AVCaptureOutput,
                       didOutput sampleBuffer: CMSampleBuffer,
                       from connection: AVCaptureConnection) {
        seen += 1
        if seen <= warmup { return }
        if saved { return }
        guard let pix = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        let ci = CIImage(cvPixelBuffer: pix)
        let ctx = CIContext()
        guard let cg = ctx.createCGImage(ci, from: ci.extent) else { return }
        let rep = NSBitmapImageRep(cgImage: cg)
        guard let png = rep.representation(using: .png, properties: [:]) else { return }
        do {
            try png.write(to: outURL)
            saved = true
            width = Int(ci.extent.width)
            height = Int(ci.extent.height)
            elapsedMs = Int(Date().timeIntervalSince(started) * 1000)
            done.signal()
        } catch {
            print("FAIL write-error \(error)")
            done.signal()
        }
    }
}

let grabber = FrameGrabber(warmup: warmup, outPath: outPath)
let session = AVCaptureSession()
session.sessionPreset = .high

do {
    let input = try AVCaptureDeviceInput(device: device)
    guard session.canAddInput(input) else { print("FAIL add-input"); exit(1) }
    session.addInput(input)
} catch {
    print("FAIL device-input \(error)")
    exit(1)
}

let output = AVCaptureVideoDataOutput()
output.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String:
                        Int(kCVPixelFormatType_32BGRA)]
output.alwaysDiscardsLateVideoFrames = true
let queue = DispatchQueue(label: "laolao.capture")
output.setSampleBufferDelegate(grabber, queue: queue)
guard session.canAddOutput(output) else { print("FAIL add-output"); exit(1) }
session.addOutput(output)

session.startRunning()

let result = grabber.done.wait(timeout: .now() + timeoutSec)
session.stopRunning()

if result == .timedOut || !grabber.saved {
    print("FAIL no-frame-in-\(timeoutSec)s seen=\(grabber.seen)")
    exit(1)
}

print("OK \(grabber.width) \(grabber.height) \(grabber.elapsedMs)")
exit(0)
