import CoreGraphics
import Foundation
import ImageIO
import ScreenCaptureKit
import UniformTypeIdentifiers

struct ScreenCaptureService {
    func capture(
        target: ResolvedTarget,
        windowID: CGWindowID,
        name: String,
        resolver: TargetResolver
    ) async throws -> [String: Any] {
        guard CGPreflightScreenCaptureAccess() else {
            throw HelperFailure("permission.screen_recording.not_granted", exitCode: 77)
        }
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        guard try resolver.resolve(target.arguments).generation == target.generation else {
            throw HelperFailure("target.identity_mismatch", exitCode: 69)
        }
        guard let window = content.windows.first(where: {
            $0.windowID == windowID && $0.owningApplication?.processID == target.arguments.pid
        }) else {
            throw HelperFailure("target.window_invalid", exitCode: 69)
        }
        let frame = window.frame
        guard frame.width > 0, frame.height > 0, frame.width * frame.height <= 32_000_000 else {
            throw HelperFailure("capture.too_large", exitCode: 78)
        }
        guard let display = content.displays.first(where: { $0.frame.intersects(frame) }) else {
            throw HelperFailure("capture.display_unavailable", exitCode: 69)
        }

        let filter = SCContentFilter(display: display, including: [window])
        let configuration = SCStreamConfiguration()
        configuration.width = Int(frame.width)
        configuration.height = Int(frame.height)
        configuration.sourceRect = CGRect(
            x: frame.minX - display.frame.minX,
            y: frame.minY - display.frame.minY,
            width: frame.width,
            height: frame.height
        )
        configuration.showsCursor = false
        configuration.ignoreShadowsSingleWindow = false
        let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: configuration)
        guard try resolver.resolve(target.arguments).generation == target.generation else {
            throw HelperFailure("target.identity_mismatch", exitCode: 69)
        }
        let png = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(
            png,
            UTType.png.identifier as CFString,
            1,
            nil
        ) else {
            throw HelperFailure("capture.encode_failed", exitCode: 1)
        }
        CGImageDestinationAddImage(destination, image, nil)
        guard CGImageDestinationFinalize(destination), png.length <= 128 * 1024 * 1024 else {
            throw HelperFailure("capture.encode_failed", exitCode: 1)
        }
        let url = try OutputRoot().writePNG(png as Data, named: name)
        return [
            "windowID": windowID,
            "path": url.path,
            "bytes": png.length,
            "width": image.width,
            "height": image.height,
        ]
    }
}
