#!/usr/bin/env swift

import AppKit
import CoreGraphics
import Foundation
import ScreenCaptureKit

let FRAME_HEADER_MAGIC: UInt32 = 0x4d574346 // MWCF

struct StreamConfig {
    var titleFilters: [String] = []
    var ownerFilters: [String] = []
    var width: Int = 1280
    var height: Int = 720
    var fps: Int = 15
    var maxFrames: Int = 0
    var onScreenOnly: Bool = true
    var includeChildWindows: Bool = true
}

struct SelectedWindow {
    let window: SCWindow
    let score: Int
    let title: String
    let ownerName: String
}

struct WindowCandidate {
    let id: UInt32
    let score: Int
    let title: String
    let ownerName: String
    let area: Double
}

func containsCaseInsensitive(_ text: String, _ needle: String) -> Bool {
    text.range(of: needle, options: [.caseInsensitive, .diacriticInsensitive]) != nil
}

func parseCSV(_ value: String) -> [String] {
    value
        .split(separator: ",")
        .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
        .filter { !$0.isEmpty }
}

func eprintln(_ message: String) {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
}

func usage() {
    let lines = [
        "Usage: xcrun swift tools/macos_window_capture_stream.swift [options]",
        "",
        "Options:",
        "  --title-contains CSV    Required-preferred window title filters",
        "  --owner-contains CSV    Optional owner-name filters",
        "  --width N               Output width in pixels",
        "  --height N              Output height in pixels",
        "  --fps N                 Capture rate target (default: 15)",
        "  --max-frames N          Stop after N frames (0 = unlimited)",
        "  --all-windows           Include off-screen windows too",
        "  --exclude-child-windows Exclude child windows from capture",
        "  --help                  Show this help text",
    ]
    FileHandle.standardOutput.write(lines.joined(separator: "\n").data(using: .utf8)!)
}

func parseArguments() -> StreamConfig {
    var config = StreamConfig()
    let args = Array(CommandLine.arguments.dropFirst())
    var index = 0

    func readValue(_ flag: String) -> String {
        guard index + 1 < args.count else {
            eprintln("missing value for \(flag)")
            exit(2)
        }
        index += 1
        return args[index]
    }

    while index < args.count {
        let arg = args[index]
        switch arg {
        case "--title-contains":
            config.titleFilters = parseCSV(readValue(arg))
        case "--owner-contains":
            config.ownerFilters = parseCSV(readValue(arg))
        case "--width":
            config.width = Int(readValue(arg)) ?? config.width
        case "--height":
            config.height = Int(readValue(arg)) ?? config.height
        case "--fps":
            config.fps = max(1, Int(readValue(arg)) ?? config.fps)
        case "--max-frames":
            config.maxFrames = max(0, Int(readValue(arg)) ?? config.maxFrames)
        case "--all-windows":
            config.onScreenOnly = false
        case "--exclude-child-windows":
            config.includeChildWindows = false
        case "--help", "-h":
            usage()
            exit(0)
        default:
            eprintln("unknown argument: \(arg)")
            usage()
            exit(2)
        }
        index += 1
    }

    return config
}

func scoreWindow(title: String, ownerName: String, config: StreamConfig) -> Int {
    var score = 0
    let titleMatched = config.titleFilters.contains { containsCaseInsensitive(title, $0) }
    let ownerMatched = config.ownerFilters.contains { containsCaseInsensitive(ownerName, $0) }

    if (!config.titleFilters.isEmpty || !config.ownerFilters.isEmpty) && !titleMatched && !ownerMatched {
        return 0
    }

    for (index, filter) in config.titleFilters.enumerated() where containsCaseInsensitive(title, filter) {
        score = max(score, 1000 - index * 50)
    }

    for (index, filter) in config.ownerFilters.enumerated() where containsCaseInsensitive(ownerName, filter) {
        score += max(0, 250 - index * 25)
    }

    if score == 0 {
        if containsCaseInsensitive(title, "steamvr tutorial") || containsCaseInsensitive(title, "tutorial") {
            score = 120
        } else if containsCaseInsensitive(title, "vr") || containsCaseInsensitive(title, "steam") {
            score = 60
        } else if containsCaseInsensitive(ownerName, "crossover") || containsCaseInsensitive(ownerName, "wine") {
            score = 40
        }
    }

    return score
}

func enumerateWindowCandidates(config: StreamConfig) -> [WindowCandidate] {
    var options: CGWindowListOption = [.excludeDesktopElements]
    if config.onScreenOnly {
        options.insert(.optionOnScreenOnly)
    }

    guard let infoList = CGWindowListCopyWindowInfo(options, kCGNullWindowID) as? [[String: Any]] else {
        return []
    }

    var candidates: [WindowCandidate] = []
    for info in infoList {
        guard let id = info[kCGWindowNumber as String] as? UInt32,
              let ownerName = info[kCGWindowOwnerName as String] as? String,
              let boundsDict = info[kCGWindowBounds as String] as? [String: Any],
              let bounds = CGRect(dictionaryRepresentation: boundsDict as CFDictionary)
        else {
            continue
        }

        let title = (info[kCGWindowName as String] as? String) ?? ""
        let score = scoreWindow(title: title, ownerName: ownerName, config: config)
        if score <= 0 {
            continue
        }

        candidates.append(
            WindowCandidate(
                id: id,
                score: score,
                title: title,
                ownerName: ownerName,
                area: bounds.width * bounds.height
            )
        )
    }

    return candidates.sorted {
        if $0.score != $1.score { return $0.score > $1.score }
        if $0.area != $1.area { return $0.area > $1.area }
        return $0.id < $1.id
    }
}

@MainActor
func enumerateWindows(config: StreamConfig) async throws -> [SelectedWindow] {
    let content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: config.onScreenOnly)
    let shareableByID = Dictionary(uniqueKeysWithValues: content.windows.map { (UInt32($0.windowID), $0) })
    let candidates = enumerateWindowCandidates(config: config)

    var selected: [SelectedWindow] = []
    for candidate in candidates {
        guard let window = shareableByID[candidate.id] else {
            continue
        }
        selected.append(
            SelectedWindow(
                window: window,
                score: candidate.score,
                title: candidate.title,
                ownerName: candidate.ownerName
            )
        )
    }
    return selected
}

@MainActor
func captureImage(window: SCWindow, config: StreamConfig) async throws -> CGImage {
    let filter = SCContentFilter(desktopIndependentWindow: window)
    let streamConfig = SCStreamConfiguration()
    let sourceWidth = max(1, Int(round(window.frame.width)))
    let sourceHeight = max(1, Int(round(window.frame.height)))
    streamConfig.width = sourceWidth
    streamConfig.height = sourceHeight
    streamConfig.showsCursor = false
    streamConfig.ignoreShadowsSingleWindow = true
    streamConfig.includeChildWindows = config.includeChildWindows
    return try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: streamConfig)
}

func aspectFitRect(sourceWidth: Int, sourceHeight: Int, destWidth: Int, destHeight: Int) -> CGRect {
    let sourceAspect = Double(sourceWidth) / Double(max(1, sourceHeight))
    let destAspect = Double(destWidth) / Double(max(1, destHeight))

    let fittedWidth: Double
    let fittedHeight: Double

    if sourceAspect > destAspect {
        fittedWidth = Double(destWidth)
        fittedHeight = fittedWidth / sourceAspect
    } else {
        fittedHeight = Double(destHeight)
        fittedWidth = fittedHeight * sourceAspect
    }

    let originX = (Double(destWidth) - fittedWidth) / 2.0
    let originY = (Double(destHeight) - fittedHeight) / 2.0
    return CGRect(x: originX, y: originY, width: fittedWidth, height: fittedHeight)
}

func rectSummary(_ rect: CGRect) -> String {
    String(
        format: "x=%.1f y=%.1f w=%.1f h=%.1f",
        rect.origin.x,
        rect.origin.y,
        rect.size.width,
        rect.size.height
    )
}

func bgraData(from image: CGImage, width: Int, height: Int, emitLayoutLog: Bool = false) -> Data? {
    let bytesPerRow = width * 4
    var data = Data(count: bytesPerRow * height)
    let colorSpace = CGColorSpaceCreateDeviceRGB()

    let succeeded = data.withUnsafeMutableBytes { buffer -> Bool in
        guard let baseAddress = buffer.baseAddress,
              let context = CGContext(
                  data: baseAddress,
                  width: width,
                  height: height,
                  bitsPerComponent: 8,
                  bytesPerRow: bytesPerRow,
                  space: colorSpace,
                  bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue
              )
        else {
            return false
        }

        context.setFillColor(red: 0, green: 0, blue: 0, alpha: 1)
        context.fill(CGRect(x: 0, y: 0, width: width, height: height))

        // The native app-window fallback captures a monoscopic desktop window,
        // but the downstream path expects a stereo-sized texture where each eye
        // occupies half the frame width. Draw the same fitted image into both
        // eye halves so the headset sees aligned content rather than two
        // mismatched cropped halves of one oversized image.
        // Preserve odd total widths by assigning the extra column to the right eye.
        let leftEyeWidth = width / 2
        let rightEyeWidth = width - leftEyeWidth
        let leftRect = aspectFitRect(
            sourceWidth: image.width,
            sourceHeight: image.height,
            destWidth: leftEyeWidth,
            destHeight: height
        )
        let rightRect = aspectFitRect(
            sourceWidth: image.width,
            sourceHeight: image.height,
            destWidth: rightEyeWidth,
            destHeight: height
        ).offsetBy(dx: CGFloat(leftEyeWidth), dy: 0)

        if emitLayoutLog {
            eprintln(
                "capture_layout source=\(image.width)x\(image.height) output=\(width)x\(height) "
                + "left{\(rectSummary(leftRect))} right{\(rectSummary(rightRect))}"
            )
        }

        context.draw(image, in: leftRect)
        context.draw(image, in: rightRect)
        return true
    }

    return succeeded ? data : nil
}

func makeHeader(width: Int, height: Int, rowBytes: Int, payloadBytes: Int, sequence: UInt64, captureNs: UInt64) -> Data {
    let values: [UInt64] = [
        UInt64(FRAME_HEADER_MAGIC),
        UInt64(width),
        UInt64(height),
        UInt64(rowBytes),
        UInt64(payloadBytes),
        sequence,
        captureNs,
    ]
    var header = Data(capacity: values.count * MemoryLayout<UInt64>.size)
    for value in values {
        var little = value.littleEndian
        withUnsafeBytes(of: &little) { header.append(contentsOf: $0) }
    }
    return header
}

func writeFrame(header: Data, payload: Data) {
    FileHandle.standardOutput.write(header)
    FileHandle.standardOutput.write(payload)
    try? FileHandle.standardOutput.synchronize()
}

func monotonicNs() -> UInt64 {
    UInt64(DispatchTime.now().uptimeNanoseconds)
}

@MainActor
func run() async {
    let config = parseArguments()
    let frameIntervalNs = UInt64(1_000_000_000 / max(1, config.fps))
    let rowBytes = config.width * 4

    _ = NSApplication.shared

    eprintln("capture_stream_start width=\(config.width) height=\(config.height) fps=\(config.fps)")

    var current: SelectedWindow?
    var sequence: UInt64 = 0

    while config.maxFrames == 0 || sequence < UInt64(config.maxFrames) {
        let loopStart = monotonicNs()

        do {
            if current == nil || sequence % 120 == 0 {
                let windows = try await enumerateWindows(config: config)
                current = windows.first
                if let current {
                    eprintln(
                        "capture_window_selected id=\(current.window.windowID) owner=\(current.ownerName) title=\(current.title) score=\(current.score)"
                    )
                } else {
                    eprintln("capture_window_selected none")
                }
            }

            guard let selectedWindow = current else {
                try await Task.sleep(nanoseconds: frameIntervalNs)
                continue
            }

            let image = try await captureImage(window: selectedWindow.window, config: config)
            let shouldEmitLayoutLog = sequence < 3 || sequence % 120 == 0
            guard let payload = bgraData(
                from: image,
                width: config.width,
                height: config.height,
                emitLayoutLog: shouldEmitLayoutLog
            ) else {
                eprintln("capture_frame_failed reason=bgra_conversion")
                current = nil
                continue
            }

            sequence += 1
            let captureNs = monotonicNs()
            let header = makeHeader(
                width: config.width,
                height: config.height,
                rowBytes: rowBytes,
                    payloadBytes: payload.count,
                    sequence: sequence,
                    captureNs: captureNs
                )
            writeFrame(header: header, payload: payload)

            if sequence <= 5 || sequence % 120 == 0 {
                eprintln(
                    "capture_frame_ready sequence=\(sequence) id=\(selectedWindow.window.windowID) title=\(selectedWindow.title) bytes=\(payload.count)"
                )
            }
        } catch {
            eprintln("capture_frame_failed reason=\(error)")
            current = nil
        }

        let elapsedNs = monotonicNs() - loopStart
        if elapsedNs < frameIntervalNs {
            try? await Task.sleep(nanoseconds: frameIntervalNs - elapsedNs)
        }
    }
}

Task { @MainActor in
    await run()
    CFRunLoopStop(CFRunLoopGetMain())
}
CFRunLoopRun()
