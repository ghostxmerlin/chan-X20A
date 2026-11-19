// swift-tools-version:5.10
// hello on the Mac (2026-11-19 night): the robot's links (hearing / vision / mouth) from a Mac app, so the ear and
// mind treat it as hello. Command Line Tools only (no Xcode): tests are an executable (HelloSelfTest).
import PackageDescription

let opus = LinkerSetting.unsafeFlags(["/opt/homebrew/lib/libopus.a"])
let package = Package(
    name: "HelloMac",
    platforms: [.macOS(.v14)],
    targets: [
        .target(name: "COpus", path: "Sources/COpus"),
        .target(name: "HelloCore", dependencies: ["COpus"], path: "Sources/HelloCore", linkerSettings: [opus]),
        .executableTarget(name: "HelloMac", dependencies: ["HelloCore"], path: "Sources/HelloMac",
                          linkerSettings: [opus]),
        .executableTarget(name: "HelloSelfTest", dependencies: ["HelloCore"], path: "Sources/HelloSelfTest",
                          linkerSettings: [opus]),
        .executableTarget(name: "HelloProbe", dependencies: ["HelloCore"], path: "Sources/HelloProbe",
                          linkerSettings: [opus]),
    ]
)
