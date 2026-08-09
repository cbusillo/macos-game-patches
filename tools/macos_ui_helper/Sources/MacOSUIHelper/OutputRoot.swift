import Darwin
import Foundation

struct OutputRoot {
    let homeURL: URL

    init(homeURL: URL = FileManager.default.homeDirectoryForCurrentUser) {
        self.homeURL = homeURL
    }

    static func validateName(_ name: String) throws {
        let pattern = "^[A-Za-z0-9][A-Za-z0-9._-]{0,119}\\.png$"
        guard name.range(of: pattern, options: .regularExpression) != nil,
              !name.contains(".."),
              !name.contains("/")
        else {
            throw HelperFailure("output.name_invalid", exitCode: 73)
        }
    }

    func writePNG(_ data: Data, named name: String) throws -> URL {
        try Self.validateName(name)
        return try writePrivate(data, named: name, leafDirectory: "Captures")
    }

    func writeResult(_ data: Data, token: String) throws -> URL {
        let pattern = "^[a-f0-9]{32}$"
        guard token.range(of: pattern, options: .regularExpression) != nil else {
            throw HelperFailure("output.token_invalid", exitCode: 73)
        }
        return try writePrivate(data, named: "\(token).json", leafDirectory: "Results")
    }

    private func writePrivate(_ data: Data, named name: String, leafDirectory: String) throws -> URL {
        let components = ["Library", "Application Support", "MacOSGamePatches", "UIHelper", leafDirectory]
        var directoryFD = open(homeURL.path, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW)
        guard directoryFD >= 0 else {
            throw HelperFailure("output.root_unsafe", exitCode: 73)
        }
        defer { close(directoryFD) }

        for (index, component) in components.enumerated() {
            if mkdirat(directoryFD, component, 0o700) != 0 && errno != EEXIST {
                throw HelperFailure("output.root_unsafe", exitCode: 73, details: ["errno": errno])
            }
            let nextFD = openat(directoryFD, component, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW)
            guard nextFD >= 0 else {
                throw HelperFailure("output.root_unsafe", exitCode: 73, details: ["errno": errno])
            }
            close(directoryFD)
            directoryFD = nextFD
            if index == components.count - 1 {
                var metadata = stat()
                guard fstat(directoryFD, &metadata) == 0,
                      metadata.st_uid == getuid(),
                      (metadata.st_mode & S_IFMT) == S_IFDIR
                else {
                    throw HelperFailure("output.root_unsafe", exitCode: 73)
                }
                guard fchmod(directoryFD, 0o700) == 0 else {
                    throw HelperFailure("output.root_unsafe", exitCode: 73, details: ["errno": errno])
                }
            }
        }

        let fileFD = openat(directoryFD, name, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0o600)
        guard fileFD >= 0 else {
            throw HelperFailure("output.write_failed", exitCode: 73, details: ["errno": errno])
        }
        var shouldRemove = true
        defer {
            close(fileFD)
            if shouldRemove { unlinkat(directoryFD, name, 0) }
        }

        try data.withUnsafeBytes { rawBuffer in
            guard let baseAddress = rawBuffer.baseAddress else { return }
            var offset = 0
            while offset < rawBuffer.count {
                let count = Darwin.write(fileFD, baseAddress.advanced(by: offset), rawBuffer.count - offset)
                guard count > 0 else {
                    throw HelperFailure("output.write_failed", exitCode: 73, details: ["errno": errno])
                }
                offset += count
            }
        }
        guard fsync(fileFD) == 0 else {
            throw HelperFailure("output.write_failed", exitCode: 73, details: ["errno": errno])
        }
        shouldRemove = false
        return components.reduce(homeURL) { $0.appendingPathComponent($1, isDirectory: true) }
            .appendingPathComponent(name)
    }
}
