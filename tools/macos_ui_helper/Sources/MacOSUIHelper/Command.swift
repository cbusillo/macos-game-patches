import CoreGraphics
import Foundation

struct TargetArguments: Equatable {
    let bundleID: String
    let pid: pid_t
}

struct HelperInvocation: Equatable {
    let command: HelperCommand
    let resultToken: String?
}

enum HelperCommand: Equatable {
    case status
    case requestAccessibility
    case requestScreenRecording
    case windows(TargetArguments)
    case buttons(TargetArguments)
    case screenshot(TargetArguments, windowID: CGWindowID, name: String)
    case pressButton(TargetArguments, title: String)

    static func parse(_ arguments: [String]) throws -> HelperInvocation {
        guard let verb = arguments.first else {
            throw invalid("missing command")
        }
        var flags = try parseFlags(Array(arguments.dropFirst()))
        let resultToken = try parseResultToken(flags.removeValue(forKey: "--result-token"))
        let command: HelperCommand
        switch verb {
        case "status":
            try require(flags, exactly: [])
            command = .status
        case "request-screen-recording":
            try require(flags, exactly: [])
            command = .requestScreenRecording
        case "request-accessibility":
            try require(flags, exactly: [])
            command = .requestAccessibility
        case "windows":
            try require(flags, exactly: ["--bundle-id", "--pid"])
            command = .windows(try target(flags))
        case "buttons":
            try require(flags, exactly: ["--bundle-id", "--pid"])
            command = .buttons(try target(flags))
        case "screenshot":
            try require(flags, exactly: ["--bundle-id", "--pid", "--window-id", "--name"])
            guard let rawWindow = flags["--window-id"], let windowID = CGWindowID(rawWindow), windowID > 0 else {
                throw invalid("invalid --window-id")
            }
            guard let name = flags["--name"] else {
                throw invalid("missing --name")
            }
            command = .screenshot(try target(flags), windowID: windowID, name: name)
        case "press-button":
            try require(flags, exactly: ["--bundle-id", "--pid", "--title"])
            guard let title = flags["--title"], title == allowedButtonTitle else {
                throw HelperFailure("contract.button_not_allowed", exitCode: 78)
            }
            command = .pressButton(try target(flags), title: title)
        default:
            throw invalid("unknown command")
        }
        return HelperInvocation(command: command, resultToken: resultToken)
    }

    private static func parseFlags(_ arguments: [String]) throws -> [String: String] {
        guard arguments.count.isMultiple(of: 2) else {
            throw invalid("flags require values")
        }
        var result: [String: String] = [:]
        var index = 0
        while index < arguments.count {
            let flag = arguments[index]
            let value = arguments[index + 1]
            guard flag.hasPrefix("--"), !value.hasPrefix("--"), result[flag] == nil else {
                throw invalid("invalid or duplicate flag")
            }
            result[flag] = value
            index += 2
        }
        return result
    }

    private static func require(_ flags: [String: String], exactly expected: Set<String>) throws {
        guard Set(flags.keys) == expected else {
            throw invalid("unexpected flags")
        }
    }

    private static func target(_ flags: [String: String]) throws -> TargetArguments {
        guard let bundleID = flags["--bundle-id"], bundleID == allowedBundleID else {
            throw HelperFailure("target.bundle_not_allowed", exitCode: 78)
        }
        guard let rawPID = flags["--pid"], let pid = pid_t(rawPID), pid > 1 else {
            throw invalid("invalid --pid")
        }
        return TargetArguments(bundleID: bundleID, pid: pid)
    }

    private static func parseResultToken(_ token: String?) throws -> String? {
        guard let token else { return nil }
        let pattern = "^[A-Fa-f0-9]{32}$"
        guard token.range(of: pattern, options: .regularExpression) != nil else {
            throw invalid("invalid --result-token")
        }
        return token.lowercased()
    }

    private static func invalid(_ reason: String) -> HelperFailure {
        HelperFailure("usage.invalid", exitCode: 64, details: ["reason": reason])
    }
}
