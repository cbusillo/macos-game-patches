import AppKit
import Darwin
import Foundation
import Security

struct ProcessGeneration: Equatable {
    let pid: pid_t
    let startSeconds: UInt64
    let startMicroseconds: UInt64
}

struct ResolvedTarget {
    let arguments: TargetArguments
    let application: NSRunningApplication
    let bundleURL: URL
    let executableURL: URL
    let generation: ProcessGeneration
}

struct TargetResolver {
    func resolve(_ arguments: TargetArguments) throws -> ResolvedTarget {
        guard let application = NSRunningApplication(processIdentifier: arguments.pid),
              !application.isTerminated,
              application.bundleIdentifier == arguments.bundleID,
              let bundleURL = application.bundleURL,
              let executableURL = application.executableURL
        else {
            throw HelperFailure("target.identity_mismatch", exitCode: 69)
        }

        let canonicalBundle = bundleURL.resolvingSymlinksInPath().standardizedFileURL
        let canonicalExecutable = executableURL.resolvingSymlinksInPath().standardizedFileURL
        let executableRoot = canonicalBundle.appendingPathComponent("Contents/MacOS", isDirectory: true).path + "/"
        guard canonicalExecutable.path.hasPrefix(executableRoot) else {
            throw HelperFailure("target.identity_mismatch", exitCode: 69)
        }

        var processInfo = proc_bsdinfo()
        let processInfoSize = Int32(MemoryLayout<proc_bsdinfo>.size)
        guard proc_pidinfo(
            arguments.pid,
            PROC_PIDTBSDINFO,
            0,
            &processInfo,
            processInfoSize
        ) == processInfoSize else {
            throw HelperFailure("target.identity_mismatch", exitCode: 69)
        }
        let generation = ProcessGeneration(
            pid: arguments.pid,
            startSeconds: processInfo.pbi_start_tvsec,
            startMicroseconds: processInfo.pbi_start_tvusec
        )

        let attributes = [kSecGuestAttributePid as String: NSNumber(value: arguments.pid)] as CFDictionary
        var runningCode: SecCode?
        guard SecCodeCopyGuestWithAttributes(nil, attributes, [], &runningCode) == errSecSuccess,
              let runningCode
        else {
            throw HelperFailure("target.signature_invalid", exitCode: 69)
        }
        let requirementText = "identifier \"\(allowedBundleID)\" and anchor apple generic and certificate 1[field.1.2.840.113635.100.6.2.6] exists and certificate leaf[field.1.2.840.113635.100.6.1.13] exists and certificate leaf[subject.OU] = \"\(allowedTeamID)\""
        var requirement: SecRequirement?
        guard SecRequirementCreateWithString(requirementText as CFString, [], &requirement) == errSecSuccess,
              let requirement,
              SecCodeCheckValidity(runningCode, [], requirement) == errSecSuccess
        else {
            throw HelperFailure("target.signature_invalid", exitCode: 69)
        }
        var staticCode: SecStaticCode?
        guard SecCodeCopyStaticCode(runningCode, [], &staticCode) == errSecSuccess,
              let staticCode
        else {
            throw HelperFailure("target.signature_invalid", exitCode: 69)
        }

        var rawSigningInfo: CFDictionary?
        let flags = SecCSFlags(rawValue: UInt32(kSecCSSigningInformation))
        guard SecCodeCopySigningInformation(staticCode, flags, &rawSigningInfo) == errSecSuccess,
              let signingInfo = rawSigningInfo as? [String: Any],
              signingInfo[kSecCodeInfoTeamIdentifier as String] as? String == allowedTeamID,
              signingInfo[kSecCodeInfoIdentifier as String] as? String == arguments.bundleID
        else {
            throw HelperFailure("target.signature_invalid", exitCode: 69)
        }

        return ResolvedTarget(
            arguments: arguments,
            application: application,
            bundleURL: canonicalBundle,
            executableURL: canonicalExecutable,
            generation: generation
        )
    }
}
