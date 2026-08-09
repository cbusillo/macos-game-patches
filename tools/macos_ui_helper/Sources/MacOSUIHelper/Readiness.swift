import AppKit
@preconcurrency import ApplicationServices
import CoreGraphics
import Foundation
import ScreenCaptureKit

func readinessStatus() -> [String: Any] {
    var result: [String: Any] = [
        "accessibility": AXIsProcessTrusted(),
        "screenRecording": CGPreflightScreenCaptureAccess(),
    ]
    if let app = NSWorkspace.shared.frontmostApplication {
        result["frontmostApp"] = [
            "bundleID": app.bundleIdentifier as Any,
            "pid": app.processIdentifier,
            "name": app.localizedName as Any,
        ]
    } else {
        result["frontmostApp"] = NSNull()
    }
    return result
}

func requestScreenRecording() -> [String: Any] {
    ["screenRecording": CGRequestScreenCaptureAccess()]
}

func requestAccessibility() -> [String: Any] {
    let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
    return ["accessibility": AXIsProcessTrustedWithOptions(options)]
}

func targetWindows(_ target: ResolvedTarget, resolver: TargetResolver) async throws -> [[String: Any]] {
    guard CGPreflightScreenCaptureAccess() else {
        throw HelperFailure("permission.screen_recording.not_granted", exitCode: 77)
    }
    let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
    guard try resolver.resolve(target.arguments).generation == target.generation else {
        throw HelperFailure("target.identity_mismatch", exitCode: 69)
    }
    return content.windows.filter {
        $0.owningApplication?.processID == target.arguments.pid
    }.map { window in
        return [
            "windowID": window.windowID,
            "title": window.title ?? "",
            "onScreen": window.isOnScreen,
            "bounds": [
                "x": window.frame.origin.x,
                "y": window.frame.origin.y,
                "width": window.frame.width,
                "height": window.frame.height,
            ],
        ]
    }.sorted { ($0["windowID"] as? UInt32 ?? 0) < ($1["windowID"] as? UInt32 ?? 0) }
}
