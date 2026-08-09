// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "MacOSUIHelper",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "macos-ui-helper", targets: ["MacOSUIHelper"]),
    ],
    targets: [
        .executableTarget(name: "MacOSUIHelper"),
        .testTarget(name: "MacOSUIHelperTests", dependencies: ["MacOSUIHelper"]),
    ]
)
