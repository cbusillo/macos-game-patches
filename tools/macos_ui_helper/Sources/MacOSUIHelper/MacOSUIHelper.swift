import Foundation

@main
struct MacOSUIHelper {
    static func main() async {
        let resolver = TargetResolver()
        var resultToken: String?
        do {
            let invocation = try HelperCommand.parse(Array(CommandLine.arguments.dropFirst()))
            resultToken = invocation.resultToken
            let result: [String: Any]
            switch invocation.command {
            case .status:
                result = readinessStatus()
            case .requestScreenRecording:
                result = requestScreenRecording()
            case .requestAccessibility:
                result = requestAccessibility()
            case let .windows(arguments):
                let target = try resolver.resolve(arguments)
                result = [
                    "pid": arguments.pid,
                    "windows": try await targetWindows(target, resolver: resolver),
                ]
            case let .buttons(arguments):
                let target = try resolver.resolve(arguments)
                let traversal = try AccessibilityService().buttons(for: target)
                result = [
                    "pid": arguments.pid,
                    "nodeCount": traversal.nodeCount,
                    "truncated": traversal.truncated,
                    "buttons": traversal.buttons.map(\.json),
                ]
            case let .screenshot(arguments, windowID, name):
                let target = try resolver.resolve(arguments)
                result = try await ScreenCaptureService().capture(
                    target: target,
                    windowID: windowID,
                    name: name,
                    resolver: resolver
                )
            case let .pressButton(arguments, title):
                let target = try resolver.resolve(arguments)
                result = try AccessibilityService().press(title: title, target: target, resolver: resolver)
            }
            try deliver(success(result), resultToken: resultToken)
            Foundation.exit(0)
        } catch let error as HelperFailure {
            do {
                try deliver(failure(error), resultToken: resultToken)
            } catch {
                emitJSON(failure(HelperFailure("output.result_failed", exitCode: 73)))
            }
            Foundation.exit(error.exitCode)
        } catch {
            do {
                try deliver(
                    failure(HelperFailure("internal.unexpected", exitCode: 1)),
                    resultToken: resultToken
                )
            } catch {
                emitJSON(failure(HelperFailure("output.result_failed", exitCode: 73)))
            }
            Foundation.exit(1)
        }
    }

    private static func deliver(_ object: [String: Any], resultToken: String?) throws {
        guard let resultToken else {
            emitJSON(object)
            return
        }
        _ = try OutputRoot().writeResult(encodeJSON(object), token: resultToken)
    }
}
