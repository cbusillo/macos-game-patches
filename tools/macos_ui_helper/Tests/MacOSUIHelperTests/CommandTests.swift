import Foundation
import Testing
@testable import MacOSUIHelper

@Test func parsesStatus() throws {
    let mixedCaseToken = String(repeating: "0", count: 31) + "A"
    let normalizedToken = String(repeating: "0", count: 31) + "a"
    #expect(try HelperCommand.parse(["status"]) == HelperInvocation(command: .status, resultToken: nil))
    #expect(
        try HelperCommand.parse(["request-accessibility"])
            == HelperInvocation(command: .requestAccessibility, resultToken: nil)
    )
    #expect(
        try HelperCommand.parse(["request-screen-recording", "--result-token", mixedCaseToken])
            == HelperInvocation(command: .requestScreenRecording, resultToken: normalizedToken)
    )
}

@Test func parsesStrictScreenshot() throws {
    let command = try HelperCommand.parse([
        "screenshot",
        "--bundle-id", allowedBundleID,
        "--pid", "123",
        "--window-id", "456",
        "--name", "bridge-window.png",
    ])
    #expect(command == HelperInvocation(
        command: .screenshot(
            TargetArguments(bundleID: allowedBundleID, pid: 123),
            windowID: 456,
            name: "bridge-window.png"
        ),
        resultToken: nil
    ))
}

@Test func rejectsUnknownOrDuplicateFlags() {
    #expect(throws: HelperFailure.self) {
        try HelperCommand.parse(["status", "--extra", "value"])
    }
    #expect(throws: HelperFailure.self) {
        try HelperCommand.parse(["status", "--result-token", "unsafe"])
    }
    #expect(throws: HelperFailure.self) {
        try HelperCommand.parse([
            "windows", "--bundle-id", allowedBundleID,
            "--pid", "12", "--pid", "13",
        ])
    }
}

@Test func rejectsUnapprovedTargetAndAction() {
    #expect(throws: HelperFailure.self) {
        try HelperCommand.parse(["windows", "--bundle-id", "com.apple.Terminal", "--pid", "12"])
    }
    #expect(throws: HelperFailure.self) {
        try HelperCommand.parse([
            "press-button", "--bundle-id", allowedBundleID,
            "--pid", "12", "--title", "Allow",
        ])
    }
}

@Test func validatesCaptureBasenames() throws {
    try OutputRoot.validateName("capture-01.png")
    for name in ["../escape.png", "nested/file.png", ".hidden.png", "capture.jpg", "a..png"] {
        #expect(throws: HelperFailure.self) {
            try OutputRoot.validateName(name)
        }
    }
}

@Test func writesPrivateExclusiveCapture() throws {
    let temporary = FileManager.default.temporaryDirectory
        .appendingPathComponent(UUID().uuidString, isDirectory: true)
    try FileManager.default.createDirectory(at: temporary, withIntermediateDirectories: false)
    defer { try? FileManager.default.removeItem(at: temporary) }

    let root = OutputRoot(homeURL: temporary)
    let destination = try root.writePNG(Data([1, 2, 3]), named: "fixture.png")
    let attributes = try FileManager.default.attributesOfItem(atPath: destination.path)
    #expect((attributes[.posixPermissions] as? NSNumber)?.intValue == 0o600)
    #expect(throws: HelperFailure.self) {
        try root.writePNG(Data([4]), named: "fixture.png")
    }
}

@Test func refusesSymlinkedOutputComponent() throws {
    let temporary = FileManager.default.temporaryDirectory
        .appendingPathComponent(UUID().uuidString, isDirectory: true)
    let redirected = FileManager.default.temporaryDirectory
        .appendingPathComponent(UUID().uuidString, isDirectory: true)
    try FileManager.default.createDirectory(at: temporary, withIntermediateDirectories: false)
    try FileManager.default.createDirectory(at: redirected, withIntermediateDirectories: false)
    defer {
        try? FileManager.default.removeItem(at: temporary)
        try? FileManager.default.removeItem(at: redirected)
    }
    try FileManager.default.createSymbolicLink(
        at: temporary.appendingPathComponent("Library"),
        withDestinationURL: redirected
    )

    #expect(throws: HelperFailure.self) {
        try OutputRoot(homeURL: temporary).writePNG(Data([1]), named: "fixture.png")
    }
}

@Test func writesPrivateExclusiveResult() throws {
    let temporary = FileManager.default.temporaryDirectory
        .appendingPathComponent(UUID().uuidString, isDirectory: true)
    try FileManager.default.createDirectory(at: temporary, withIntermediateDirectories: false)
    defer { try? FileManager.default.removeItem(at: temporary) }

    let root = OutputRoot(homeURL: temporary)
    let token = String(repeating: "0", count: 31) + "1"
    let destination = try root.writeResult(Data("{}".utf8), token: token)
    let attributes = try FileManager.default.attributesOfItem(atPath: destination.path)
    #expect(destination.lastPathComponent == "\(token).json")
    #expect((attributes[.posixPermissions] as? NSNumber)?.intValue == 0o600)
    #expect(throws: HelperFailure.self) {
        try root.writeResult(Data(), token: "../unsafe")
    }
    #expect(throws: HelperFailure.self) {
        try root.writeResult(Data(), token: token)
    }
}
