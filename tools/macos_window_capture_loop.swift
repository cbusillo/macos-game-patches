#!/usr/bin/env swift

/// Continuous ScreenCaptureKit window capture loop.
///
/// Captures the best-matching SteamVR/CrossOver window at a fixed interval and
/// writes each frame as a PNG under --output-dir.  One JSON line per frame is
/// appended to manifest.jsonl so callers can assess pixel content without
/// loading every image.
///
/// Exits cleanly when:
///   • A stop-file path is supplied and the file appears on disk
///   • SIGTERM or SIGINT is received
///   • --duration-seconds elapses (0 = unlimited)
///   • --max-frames is reached (0 = unlimited)
///
/// Window selection reuses the same CGWindowList scoring logic as
/// macos_window_capture_probe.swift so the two tools rank candidates
/// identically.

import AppKit
import CoreGraphics
import Foundation
import ScreenCaptureKit

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

struct LoopConfig {
    var titleFilters: [String] = []          // empty → use fallback scoring only
    var ownerFilters: [String] = []
    var outputDir: URL = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        .appendingPathComponent("temp/macos-window-capture-loop", isDirectory: true)
    var intervalSeconds: Double = 3.0
    var durationSeconds: Double = 0          // 0 = unlimited
    var maxFrames: Int = 0                   // 0 = unlimited
    var stopFile: String = ""
    var onScreenOnly: Bool = true
    var minWidth: Double = 320
    var minHeight: Double = 200
}

// ---------------------------------------------------------------------------
// Shared types (same fields as probe for cross-tool consistency)
// ---------------------------------------------------------------------------

struct WindowRecord {
    let id: UInt32
    let ownerName: String
    let ownerPID: Int
    let title: String
    let boundsWidth: Double
    let boundsHeight: Double
    let score: Int
}

struct CaptureAnalysis {
    let flat: Bool
    let sampleNonzeroCount: Int
    let sampleMax: Int
    let firstPixelHex: String
}

struct ManifestEntry: Codable {
    let frameIndex: Int
    let capturedAtUTC: String
    let windowID: UInt32
    let ownerName: String
    let title: String
    let outputPath: String?
    let captureSucceeded: Bool
    let flat: Bool?
    let sampleNonzeroCount: Int?
    let sampleMax: Int?
    let firstPixelHex: String?
}

// ---------------------------------------------------------------------------
// Window helpers (same logic as probe)
// ---------------------------------------------------------------------------

func containsCaseInsensitive(_ text: String, _ needle: String) -> Bool {
    text.range(of: needle, options: [.caseInsensitive, .diacriticInsensitive]) != nil
}

func scoreWindow(ownerName: String, title: String, config: LoopConfig) -> Int {
    var score = 0

    for (index, filter) in config.titleFilters.enumerated()
        where containsCaseInsensitive(title, filter)
    {
        score = max(score, 1000 - index * 50)
    }

    for (index, filter) in config.ownerFilters.enumerated()
        where containsCaseInsensitive(ownerName, filter)
    {
        score += max(0, 200 - index * 25)
    }

    if score == 0 {
        if containsCaseInsensitive(title, "vr") || containsCaseInsensitive(title, "steam") {
            score = 80
        } else if containsCaseInsensitive(ownerName, "crossover")
            || containsCaseInsensitive(ownerName, "wine")
        {
            score = 40
        }
    }

    return score
}

func enumerateWindows(config: LoopConfig) -> [WindowRecord] {
    var options: CGWindowListOption = [.excludeDesktopElements]
    if config.onScreenOnly {
        options.insert(.optionOnScreenOnly)
    }

    guard let infoList = CGWindowListCopyWindowInfo(options, kCGNullWindowID) as? [[String: Any]]
    else {
        return []
    }

    var windows: [WindowRecord] = []
    for info in infoList {
        guard let id = info[kCGWindowNumber as String] as? UInt32,
              let ownerName = info[kCGWindowOwnerName as String] as? String,
              let ownerPID = info[kCGWindowOwnerPID as String] as? Int,
              let boundsDict = info[kCGWindowBounds as String] as? [String: Any],
              let bounds = CGRect(dictionaryRepresentation: boundsDict as CFDictionary)
        else {
            continue
        }

        let title = (info[kCGWindowName as String] as? String) ?? ""

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
                boundsWidth: bounds.width,
                boundsHeight: bounds.height,
                score: score
            )
        )
    }

    return windows.sorted {
        if $0.score != $1.score { return $0.score > $1.score }
        let lhsArea = $0.boundsWidth * $0.boundsHeight
        let rhsArea = $1.boundsWidth * $1.boundsHeight
        if lhsArea != rhsArea { return lhsArea > rhsArea }
        return $0.id < $1.id
    }
}

func shareableWindowsByID(onScreenOnly: Bool) async throws -> [UInt32: SCWindow] {
    let content = try await SCShareableContent.excludingDesktopWindows(
        true, onScreenWindowsOnly: onScreenOnly
    )
    var map: [UInt32: SCWindow] = [:]
    map.reserveCapacity(content.windows.count)
    for w in content.windows {
        map[UInt32(w.windowID)] = w
    }
    return map
}

// ---------------------------------------------------------------------------
// Capture helpers (same logic as probe)
// ---------------------------------------------------------------------------

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
        return (
            bytes[offset],
            bytes[offset + min(1, bytesPerPixel - 1)],
            bytes[offset + min(2, bytesPerPixel - 1)],
            bytes[offset + min(3, bytesPerPixel - 1)]
        )
    }

    let first = pixelBytes(x: 0, y: 0)
    var flat = true
    var sampleNonzeroCount = 0
    var sampleMax = Int.min

    for row in 0..<sampleRows {
        let y = ((height - 1) * row) / max(1, sampleRows - 1)
        for col in 0..<sampleCols {
            let x = ((width - 1) * col) / max(1, sampleCols - 1)
            let sample = pixelBytes(x: x, y: y)
            if sample != first { flat = false }
            let rgb = [Int(sample.0), Int(sample.1), Int(sample.2)]
            if rgb.contains(where: { $0 != 0 }) { sampleNonzeroCount += 1 }
            sampleMax = max(sampleMax, rgb.max() ?? 0)
        }
    }

    let firstPixelHex = String(
        format: "%02x%02x%02x%02x", first.0, first.1, first.2, first.3
    )
    return CaptureAnalysis(
        flat: flat,
        sampleNonzeroCount: sampleNonzeroCount,
        sampleMax: sampleMax == Int.min ? 0 : sampleMax,
        firstPixelHex: firstPixelHex
    )
}

func savePNG(_ image: CGImage, to url: URL) throws {
    let bitmap = NSBitmapImageRep(cgImage: image)
    guard let pngData = bitmap.representation(using: .png, properties: [:]) else {
        throw NSError(
            domain: "macos_window_capture_loop", code: 1,
            userInfo: [NSLocalizedDescriptionKey: "PNG encoding failed"]
        )
    }
    try pngData.write(to: url)
}

func captureFrame(
    window: WindowRecord,
    shareableWindow: SCWindow?,
    frameIndex: Int,
    outputDir: URL
) async -> ManifestEntry {
    let isoFormatter = ISO8601DateFormatter()
    let capturedAt = isoFormatter.string(from: Date())

    guard let shareableWindow else {
        return ManifestEntry(
            frameIndex: frameIndex, capturedAtUTC: capturedAt,
            windowID: window.id, ownerName: window.ownerName, title: window.title,
            outputPath: nil, captureSucceeded: false,
            flat: nil, sampleNonzeroCount: nil, sampleMax: nil, firstPixelHex: nil
        )
    }

    let filter = SCContentFilter(desktopIndependentWindow: shareableWindow)
    let streamConfig = SCStreamConfiguration()
    streamConfig.width = Int(max(1, round(window.boundsWidth)))
    streamConfig.height = Int(max(1, round(window.boundsHeight)))
    streamConfig.showsCursor = false
    streamConfig.ignoreShadowsSingleWindow = true

    let image: CGImage
    do {
        image = try await SCScreenshotManager.captureImage(
            contentFilter: filter, configuration: streamConfig
        )
    } catch {
        return ManifestEntry(
            frameIndex: frameIndex, capturedAtUTC: capturedAt,
            windowID: window.id, ownerName: window.ownerName, title: window.title,
            outputPath: nil, captureSucceeded: false,
            flat: nil, sampleNonzeroCount: nil, sampleMax: nil, firstPixelHex: nil
        )
    }

    let fileName = String(format: "frame-%06d-%u.png", frameIndex, window.id)
    let outputURL = outputDir.appendingPathComponent(fileName)
    let analysis = analyzeImage(image)

    do {
        try savePNG(image, to: outputURL)
    } catch {
        return ManifestEntry(
            frameIndex: frameIndex, capturedAtUTC: capturedAt,
            windowID: window.id, ownerName: window.ownerName, title: window.title,
            outputPath: nil, captureSucceeded: false,
            flat: analysis?.flat, sampleNonzeroCount: analysis?.sampleNonzeroCount,
            sampleMax: analysis?.sampleMax, firstPixelHex: analysis?.firstPixelHex
        )
    }

    return ManifestEntry(
        frameIndex: frameIndex, capturedAtUTC: capturedAt,
        windowID: window.id, ownerName: window.ownerName, title: window.title,
        outputPath: outputURL.path, captureSucceeded: true,
        flat: analysis?.flat, sampleNonzeroCount: analysis?.sampleNonzeroCount,
        sampleMax: analysis?.sampleMax, firstPixelHex: analysis?.firstPixelHex
    )
}

// ---------------------------------------------------------------------------
// Argument parsing
// ---------------------------------------------------------------------------

func parseCSV(_ value: String) -> [String] {
    value.split(separator: ",")
        .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
        .filter { !$0.isEmpty }
}

func usage() {
    let lines = [
        "Usage: xcrun swift tools/macos_window_capture_loop.swift [options]",
        "",
        "Options:",
        "  --title-contains CSV    Window title filters (default: empty, use fallback scoring)",
        "  --owner-contains CSV    Optional owner-name filters",
        "  --output-dir PATH       Output directory for PNGs and manifest.jsonl",
        "  --interval-seconds N    Seconds between captures (default: 3.0)",
        "  --duration-seconds N    Stop after N seconds, 0=unlimited (default: 0)",
        "  --max-frames N          Stop after N frames, 0=unlimited (default: 0)",
        "  --stop-file PATH        Exit cleanly when this file exists",
        "  --all-windows           Include off-screen windows",
        "  --min-width N           Minimum window width (default: 320)",
        "  --min-height N          Minimum window height (default: 200)",
        "  --help                  Show this help text",
    ]
    FileHandle.standardOutput.write(lines.joined(separator: "\n").data(using: .utf8)!)
}

func parseArguments() -> LoopConfig {
    var config = LoopConfig()
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
        case "--output-dir":
            config.outputDir = URL(fileURLWithPath: readValue(arg), isDirectory: true)
        case "--interval-seconds":
            config.intervalSeconds = Double(readValue(arg)) ?? config.intervalSeconds
        case "--duration-seconds":
            config.durationSeconds = Double(readValue(arg)) ?? config.durationSeconds
        case "--max-frames":
            config.maxFrames = Int(readValue(arg)) ?? config.maxFrames
        case "--stop-file":
            config.stopFile = readValue(arg)
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

// ---------------------------------------------------------------------------
// Main loop
// ---------------------------------------------------------------------------

var shouldStop = false
signal(SIGTERM) { _ in shouldStop = true }
signal(SIGINT) { _ in shouldStop = true }

func run() async {
    let config = parseArguments()

    do {
        try FileManager.default.createDirectory(
            at: config.outputDir, withIntermediateDirectories: true
        )
    } catch {
        fputs("failed to create output dir \(config.outputDir.path): \(error)\n", stderr)
        exit(1)
    }

    let manifestURL = config.outputDir.appendingPathComponent("manifest.jsonl")
    FileManager.default.createFile(atPath: manifestURL.path, contents: nil)
    guard let manifestHandle = try? FileHandle(forWritingTo: manifestURL) else {
        fputs("failed to open manifest.jsonl for writing\n", stderr)
        exit(1)
    }
    defer { manifestHandle.closeFile() }

    let encoder = JSONEncoder()
    encoder.outputFormatting = []  // compact lines for JSONL

    let startTime = Date()
    var frameIndex = 0

    print("capture_loop_start output_dir=\(config.outputDir.path) interval=\(config.intervalSeconds)s")

    while !shouldStop {
        // Stop-file sentinel
        if !config.stopFile.isEmpty
            && FileManager.default.fileExists(atPath: config.stopFile)
        {
            print("capture_loop_stop_file_found path=\(config.stopFile)")
            break
        }

        // Duration limit
        if config.durationSeconds > 0
            && Date().timeIntervalSince(startTime) >= config.durationSeconds
        {
            print("capture_loop_duration_elapsed seconds=\(config.durationSeconds)")
            break
        }

        // Frame count limit
        if config.maxFrames > 0 && frameIndex >= config.maxFrames {
            print("capture_loop_max_frames_reached max=\(config.maxFrames)")
            break
        }

        // Find best matching window
        let windows = enumerateWindows(config: config)
        guard let best = windows.first else {
            // No match yet — retry after interval
            do {
                try await Task.sleep(
                    nanoseconds: UInt64(config.intervalSeconds * 1_000_000_000)
                )
            } catch {}
            continue
        }

        // Refresh shareable content each iteration (cheap; needed if window
        // is newly on-screen or the process restarted since last iteration).
        let shareableByID: [UInt32: SCWindow]
        do {
            shareableByID = try await shareableWindowsByID(
                onScreenOnly: config.onScreenOnly
            )
        } catch {
            fputs("warning: SCShareableContent failed: \(error)\n", stderr)
            do {
                try await Task.sleep(
                    nanoseconds: UInt64(config.intervalSeconds * 1_000_000_000)
                )
            } catch {}
            continue
        }

        let entry = await captureFrame(
            window: best,
            shareableWindow: shareableByID[best.id],
            frameIndex: frameIndex,
            outputDir: config.outputDir
        )

        // Write manifest line
        if let data = try? encoder.encode(entry),
           let line = String(data: data, encoding: .utf8)
        {
            manifestHandle.write((line + "\n").data(using: .utf8)!)
        }

        // One-line progress to stdout (consumed by Python caller or shell)
        let status: String
        if !entry.captureSucceeded {
            status = "failed"
        } else if entry.flat == true {
            status = "flat first_pixel=0x\(entry.firstPixelHex ?? "?")"
        } else {
            status = "content nonzero=\(entry.sampleNonzeroCount ?? 0) max=\(entry.sampleMax ?? 0) first_pixel=0x\(entry.firstPixelHex ?? "?")"
        }
        let title = best.title.isEmpty ? best.ownerName : best.title
        print("frame=\(frameIndex) wid=\(best.id) title=\"\(title)\" \(status)")

        frameIndex += 1

        if shouldStop { break }

        do {
            try await Task.sleep(
                nanoseconds: UInt64(config.intervalSeconds * 1_000_000_000)
            )
        } catch {}
    }

    print("capture_loop_done frames=\(frameIndex)")
}

let semaphore = DispatchSemaphore(value: 0)
Task {
    await run()
    semaphore.signal()
}
semaphore.wait()
