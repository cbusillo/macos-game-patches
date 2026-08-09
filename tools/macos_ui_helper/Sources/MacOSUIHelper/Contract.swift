import Foundation

let schemaVersion = 1
let allowedBundleID = "com.alvr.macos-bridge.iosurface"
let allowedTeamID = "MM5YXC7T6E"
let allowedButtonTitle = "Continue"

struct HelperFailure: Error, @unchecked Sendable {
    let code: String
    let exitCode: Int32
    let details: [String: Any]

    init(_ code: String, exitCode: Int32, details: [String: Any] = [:]) {
        self.code = code
        self.exitCode = exitCode
        self.details = details
    }
}

func success(_ result: [String: Any]) -> [String: Any] {
    ["schema": schemaVersion, "ok": true, "result": result]
}

func failure(_ error: HelperFailure) -> [String: Any] {
    [
        "schema": schemaVersion,
        "ok": false,
        "error": ["code": error.code, "details": error.details],
    ]
}

func emitJSON(_ object: [String: Any]) {
    let data = encodeJSON(object)
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data([0x0a]))
}

func encodeJSON(_ object: [String: Any]) -> Data {
    do {
        return try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
    } catch {
        return Data("{\"schema\":1,\"ok\":false,\"error\":{\"code\":\"internal.json\",\"details\":{}}}".utf8)
    }
}
