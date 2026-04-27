#!/usr/bin/env swift

import AppKit
import CoreGraphics
import Foundation
import ScreenCaptureKit

struct ProbeConfig {
    var titleFilters: [String] = ["VR View", "Legacy Mirror"]
    var ownerFilters: [String] = []
    var listLimit: Int = 20
    var captureLimit: Int = 4
    var outputDir: URL = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        .appendingPathComponent("temp/macos-window-capture-probe", isDirectory: true)
    var capture: Bool = true
    var listOnly: Bool = false
    var onScreenOnly: Bool = true
    var minWidth: Double = 320
    var minHeight: Double = 200
}

struct WindowRecord: Codable {
    let id: UInt32
    let ownerName: String
    let ownerPID: Int
    let title: String
    let layer: Int
    let alpha: Double
    let isOnscreen: Bool
    let sharingState: Int
    let boundsX: Double
    let boundsY: Double
    let boundsWidth: Double
    let boundsHeight: Double
    let score: Int
}

struct CaptureAnalysis: Codable {
    let flat: Bool
    let sampleNonzeroCount: Int
    let sampleMin: Int
    let sampleMax: Int
    let firstPixelHex: String
}

struct CaptureResult: Codable {
    let window: WindowRecord
    let outputPath: String?
    let analysis: CaptureAnalysis?
    let captureSucceeded: Bool
}

struct Summary: Codable {
    let generatedAtUTC: String
    let matches: [WindowRecord]
    let captures: [CaptureResult]
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

func usage() {
    let lines = [
        "Usage: xcrun swift tools/macos_window_capture_probe.swift [options]",
        "",
        "Options:",
        "  --title-contains CSV    Window title filters (default: VR View,Legacy Mirror)",
        "  --owner-contains CSV    Optional owner-name filters",
        "  --list-limit N          Number of matching windows to print (default: 20)",
        "  --capture-limit N       Number of matching windows to capture (default: 4)",
        "  --output-dir PATH       Output directory (default: temp/macos-window-capture-probe)",
        "  --list-only             List windows but do not capture PNGs",
        "  --no-capture            Alias for --list-only",
        "  --all-windows           Include off-screen windows too",
        "  --min-width N           Minimum window width filter (default: 320)",
        "  --min-height N          Minimum window height filter (default: 200)",
        "  --help                  Show this help text",
    ]
    FileHandle.standardOutput.write(lines.joined(separator: "\n").data(using: .utf8)!)
}

func parseArguments() -> ProbeConfig {
    var config = ProbeConfig()
    let args = Array(CommandLine.arguments.dropFirst())
    var index = 0

    func readValue(_ flag: String) -> String {
        guard index + 1 < args.count else {
            fputs("missing value for \(flag)\n", stderr)
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
        case "--list-limit":
            config.listLimit = Int(readValue(arg)) ?? config.listLimit
        case "--capture-limit":
            config.captureLimit = Int(readValue(arg)) ?? config.captureLimit
        case "--output-dir":
            config.outputDir = URL(fileURLWithPath: readValue(arg), isDirectory: true)
        case "--list-only", "--no-capture":
            config.capture = false
            config.listOnly = true
        case "--all-windows":
            config.onScreenOnly = false
        case "--min-width":
            config.minWidth = Double(readValue(arg)) ?? config.minWidth
        case "--min-height":
            config.minHeight = Double(readValue(arg)) ?? config.minHeight
        case "--help", "-h":
            usage()
            exit(0)
        default:
            fputs("unknown argument: \(arg)\n", stderr)
            usage()
            exit(2)
        }
        index += 1
    }

    return config
}

func scoreWindow(ownerName: String, title: String, config: ProbeConfig) -> Int {
    var score = 0

    for (index, filter) in config.titleFilters.enumerated() where containsCaseInsensitive(title, filter) {
        score = max(score, 1000 - index * 50)
    }

    for (index, filter) in config.ownerFilters.enumerated() where containsCaseInsensitive(ownerName, filter) {
        score += max(0, 200 - index * 25)
    }

    if score == 0 {
        if containsCaseInsensitive(title, "vr") || containsCaseInsensitive(title, "steam") {
            score = 80
        } else if containsCaseInsensitive(ownerName, "crossover") || containsCaseInsensitive(ownerName, "wine") {
            score = 40
        }
    }

    return score
}

func enumerateWindows(config: ProbeConfig) -> [WindowRecord] {
    var options: CGWindowListOption = [.excludeDesktopElements]
    if config.onScreenOnly {
        options.insert(.optionOnScreenOnly)
    }

    guard let infoList = CGWindowListCopyWindowInfo(options, kCGNullWindowID) as? [[String: Any]] else {
        return []
    }

    var windows: [WindowRecord] = []
    windows.reserveCapacity(infoList.count)

    for info in infoList {
        guard let id = info[kCGWindowNumber as String] as? UInt32,
              let ownerName = info[kCGWindowOwnerName as String] as? String,
              let ownerPID = info[kCGWindowOwnerPID as String] as? Int,
              let layer = info[kCGWindowLayer as String] as? Int,
              let alpha = info[kCGWindowAlpha as String] as? Double,
              let boundsDict = info[kCGWindowBounds as String] as? [String: Any],
              let bounds = CGRect(dictionaryRepresentation: boundsDict as CFDictionary)
        else {
            continue
        }

        let title = (info[kCGWindowName as String] as? String) ?? ""
        let isOnscreen = (info[kCGWindowIsOnscreen as String] as? Int).map { $0 != 0 } ?? false
        let sharingState = info[kCGWindowSharingState as String] as? Int ?? 0

        if bounds.width < config.minWidth || bounds.height < config.minHeight {
            continue
        }

        let score = scoreWindow(ownerName: ownerName, title: title, config: config)
        if score <= 0 {
            continue
        }

        windows.append(
            WindowRecord(
                id: id,
                ownerName: ownerName,
                ownerPID: ownerPID,
                title: title,
                layer: layer,
                alpha: alpha,
                isOnscreen: isOnscreen,
                sharingState: sharingState,
                boundsX: bounds.origin.x,
                boundsY: bounds.origin.y,
                boundsWidth: bounds.width,
                boundsHeight: bounds.height,
                score: score
            )
        )
    }

    return windows.sorted {
        if $0.score != $1.score {
            return $0.score > $1.score
        }
        let lhsArea = $0.boundsWidth * $0.boundsHeight
        let rhsArea = $1.boundsWidth * $1.boundsHeight
        if lhsArea != rhsArea {
            return lhsArea > rhsArea
        }
        return $0.id < $1.id
    }
}

func sanitizeFileComponent(_ value: String) -> String {
    let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-_"))
    let scalars = value.unicodeScalars.map { scalar -> Character in
        allowed.contains(scalar) ? Character(scalar) : "-"
    }
    let collapsed = String(scalars).replacingOccurrences(of: "--+", with: "-", options: .regularExpression)
    return collapsed.trimmingCharacters(in: CharacterSet(charactersIn: "-")).prefix(80).description
}

func analyzeImage(_ image: CGImage) -> CaptureAnalysis? {
    guard let provider = image.dataProvider,
          let data = provider.data,
          let bytes = CFDataGetBytePtr(data)
    else {
        return nil
    }

    let width = image.width
    let height = image.height
    let bytesPerRow = image.bytesPerRow
    let bytesPerPixel = max(1, image.bitsPerPixel / 8)
    let sampleRows = 5
    let sampleCols = 5

    func pixelBytes(x: Int, y: Int) -> (UInt8, UInt8, UInt8, UInt8) {
        let offset = y * bytesPerRow + x * bytesPerPixel
        let b0 = bytes[offset]
        let b1 = bytes[offset + min(1, bytesPerPixel - 1)]
        let b2 = bytes[offset + min(2, bytesPerPixel - 1)]
        let b3 = bytes[offset + min(3, bytesPerPixel - 1)]
        return (b0, b1, b2, b3)
    }

    let first = pixelBytes(x: 0, y: 0)
    var flat = true
    var sampleNonzeroCount = 0
    var sampleMin = Int.max
    var sampleMax = Int.min

    for row in 0..<sampleRows {
        let y = ((height - 1) * row) / max(1, sampleRows - 1)
        for col in 0..<sampleCols {
            let x = ((width - 1) * col) / max(1, sampleCols - 1)
            let sample = pixelBytes(x: x, y: y)
            if sample != first {
                flat = false
            }
            let rgb = [Int(sample.0), Int(sample.1), Int(sample.2)]
            if rgb.contains(where: { $0 != 0 }) {
                sampleNonzeroCount += 1
            }
            sampleMin = min(sampleMin, rgb.min() ?? 0)
            sampleMax = max(sampleMax, rgb.max() ?? 0)
        }
    }

    let firstPixelHex = String(format: "%02x%02x%02x%02x", first.0, first.1, first.2, first.3)
    return CaptureAnalysis(
        flat: flat,
        sampleNonzeroCount: sampleNonzeroCount,
        sampleMin: sampleMin == Int.max ? 0 : sampleMin,
        sampleMax: sampleMax == Int.min ? 0 : sampleMax,
        firstPixelHex: firstPixelHex
    )
}

func savePNG(_ image: CGImage, to url: URL) throws {
    let bitmap = NSBitmapImageRep(cgImage: image)
    guard let data = bitmap.representation(using: .png, properties: [:]) else {
        throw NSError(domain: "macos_window_capture_probe", code: 1, userInfo: [NSLocalizedDescriptionKey: "failed to encode PNG"])
    }
    try data.write(to: url)
}

func shareableWindows(config: ProbeConfig) async throws -> [UInt32: SCWindow] {
    let content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: config.onScreenOnly)
    var windowsByID: [UInt32: SCWindow] = [:]
    windowsByID.reserveCapacity(content.windows.count)
    for window in content.windows {
        windowsByID[UInt32(window.windowID)] = window
    }
    return windowsByID
}

func captureWindow(_ window: WindowRecord, outputDir: URL, shareableWindow: SCWindow?) async -> CaptureResult {
    guard let shareableWindow else {
        return CaptureResult(window: window, outputPath: nil, analysis: nil, captureSucceeded: false)
    }

    let filter = SCContentFilter(desktopIndependentWindow: shareableWindow)
    let config = SCStreamConfiguration()
    config.width = Int(max(1, round(window.boundsWidth)))
    config.height = Int(max(1, round(window.boundsHeight)))
    config.showsCursor = false
    config.ignoreShadowsSingleWindow = true

    let image: CGImage
    do {
        image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
    } catch {
        return CaptureResult(window: window, outputPath: nil, analysis: nil, captureSucceeded: false)
    }

    let titleComponent = sanitizeFileComponent(window.title.isEmpty ? window.ownerName : window.title)
    let fileName = String(format: "window-%010u-%@.png", window.id, titleComponent)
    let outputPath = outputDir.appendingPathComponent(fileName)

    do {
        try savePNG(image, to: outputPath)
        let analysis = analyzeImage(image)
        return CaptureResult(
            window: window,
            outputPath: outputPath.path,
            analysis: analysis,
            captureSucceeded: true
        )
    } catch {
        return CaptureResult(window: window, outputPath: nil, analysis: nil, captureSucceeded: false)
    }
}

func printWindowTable(_ windows: [WindowRecord], limit: Int) {
    for window in windows.prefix(limit) {
        let title = window.title.isEmpty ? "<untitled>" : window.title
        let owner = window.ownerName.isEmpty ? "<unknown>" : window.ownerName
        let line = String(
            format: "id=%u score=%d owner=%@ pid=%d layer=%d alpha=%.2f on_screen=%@ bounds=%.0fx%.0f+%.0f+%.0f title=%@",
            window.id,
            window.score,
            owner,
            window.ownerPID,
            window.layer,
            window.alpha,
            window.isOnscreen ? "1" : "0",
            window.boundsWidth,
            window.boundsHeight,
            window.boundsX,
            window.boundsY,
            title
        )
        print(line)
    }
}

extension JSONEncoder {
    func withPrettyPrinted() -> JSONEncoder {
        outputFormatting = [.prettyPrinted, .sortedKeys]
        return self
    }
}

func run() async {
    let isoFormatter = ISO8601DateFormatter()
    let config = parseArguments()
    let windows = enumerateWindows(config: config)

    print("matches=\(windows.count)")
    printWindowTable(windows, limit: config.listLimit)

    var captures: [CaptureResult] = []
    if config.capture {
        try? FileManager.default.createDirectory(at: config.outputDir, withIntermediateDirectories: true)

        let shareableByID: [UInt32: SCWindow]
        do {
            shareableByID = try await shareableWindows(config: config)
        } catch {
            fputs("warning: failed to query ScreenCaptureKit shareable windows: \(error)\n", stderr)
            shareableByID = [:]
        }

        for window in windows.prefix(config.captureLimit) {
            let result = await captureWindow(
                window,
                outputDir: config.outputDir,
                shareableWindow: shareableByID[window.id]
            )
            captures.append(result)

            let outputPath = result.outputPath ?? "<capture failed>"
            if let analysis = result.analysis {
                print(
                    "capture id=\(window.id) output=\(outputPath) flat=\(analysis.flat ? 1 : 0) "
                    + "sample_nonzero=\(analysis.sampleNonzeroCount) sample_min=\(analysis.sampleMin) "
                    + "sample_max=\(analysis.sampleMax) first_pixel=0x\(analysis.firstPixelHex)"
                )
            } else {
                print("capture id=\(window.id) output=\(outputPath) flat=?")
            }
        }
    }

    let summary = Summary(generatedAtUTC: isoFormatter.string(from: Date()), matches: windows, captures: captures)
    let summaryPath = config.outputDir.appendingPathComponent("summary.json")
    if let data = try? JSONEncoder().withPrettyPrinted().encode(summary) {
        try? FileManager.default.createDirectory(at: config.outputDir, withIntermediateDirectories: true)
        try? data.write(to: summaryPath)
        print("summary=\(summaryPath.path)")
    }

    if config.capture && captures.allSatisfy({ !$0.captureSucceeded }) {
        fputs("warning: all captures failed; check macOS Screen Recording permission for the terminal\n", stderr)
    }
}

let semaphore = DispatchSemaphore(value: 0)
Task {
    await run()
    semaphore.signal()
}
semaphore.wait()
