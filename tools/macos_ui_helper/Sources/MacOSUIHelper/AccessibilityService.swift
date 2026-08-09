import AppKit
import ApplicationServices
import Foundation

struct ButtonRecord {
    let element: AXUIElement
    let role: String
    let subrole: String
    let title: String
    let identifier: String
    let enabled: Bool
    let childCount: Int
    let pressable: Bool

    var json: [String: Any] {
        [
            "role": role,
            "subrole": subrole,
            "title": title,
            "identifier": identifier,
            "enabled": enabled,
            "childCount": childCount,
            "pressable": pressable,
        ]
    }
}

struct ButtonTraversal {
    let buttons: [ButtonRecord]
    let nodeCount: Int
    let truncated: Bool
}

struct AccessibilityService {
    private let maximumDepth = 8
    private let maximumNodes = 200
    private let maximumButtons = 64
    private let deadlineSeconds = 5.0

    func buttons(for target: ResolvedTarget) throws -> ButtonTraversal {
        guard AXIsProcessTrusted() else {
            throw HelperFailure("permission.accessibility.not_granted", exitCode: 77)
        }
        let root = AXUIElementCreateApplication(target.arguments.pid)
        let deadline = ProcessInfo.processInfo.systemUptime + deadlineSeconds
        var stack: [(AXUIElement, Int)] = [(root, 0)]
        var visited = Set<UInt>()
        var records: [ButtonRecord] = []
        var nodes = 0
        var truncated = false

        while let (element, depth) = stack.popLast() {
            if ProcessInfo.processInfo.systemUptime >= deadline {
                throw HelperFailure("operation.timeout", exitCode: 75)
            }
            AXUIElementSetMessagingTimeout(element, 0.25)
            let identity = CFHash(element)
            if !visited.insert(identity).inserted {
                continue
            }
            nodes += 1
            if nodes > maximumNodes {
                truncated = true
                break
            }

            let childResult = try copyChildren(
                element,
                limit: depth < maximumDepth ? maximumNodes - nodes : 0
            )
            let children = childResult.children
            if childResult.truncated { truncated = true }
            let role = try copyString(element, kAXRoleAttribute as CFString)
            if role == (kAXButtonRole as String) {
                if records.count >= maximumButtons {
                    truncated = true
                    break
                }
                records.append(ButtonRecord(
                    element: element,
                    role: role,
                    subrole: try copyString(element, kAXSubroleAttribute as CFString),
                    title: try copyString(element, kAXTitleAttribute as CFString),
                    identifier: try copyString(element, kAXIdentifierAttribute as CFString),
                    enabled: try copyBool(element, kAXEnabledAttribute as CFString),
                    childCount: childResult.totalCount,
                    pressable: try copyActions(element).contains(kAXPressAction as String)
                ))
            }

            if depth >= maximumDepth {
                continue
            }
            for child in children.reversed() {
                stack.append((child, depth + 1))
            }
        }
        return ButtonTraversal(buttons: records, nodeCount: min(nodes, maximumNodes), truncated: truncated)
    }

    func press(title: String, target: ResolvedTarget, resolver: TargetResolver) throws -> [String: Any] {
        guard title == allowedButtonTitle else {
            throw HelperFailure("contract.button_not_allowed", exitCode: 78)
        }
        if NSWorkspace.shared.frontmostApplication?.processIdentifier != target.arguments.pid {
            guard target.application.activate() else {
                throw HelperFailure("target.activation_failed", exitCode: 78)
            }
            let deadline = Date().addingTimeInterval(1.0)
            while Date() < deadline,
                  NSWorkspace.shared.frontmostApplication?.processIdentifier != target.arguments.pid
            {
                RunLoop.current.run(until: Date().addingTimeInterval(0.02))
            }
        }
        guard NSWorkspace.shared.frontmostApplication?.processIdentifier == target.arguments.pid else {
            throw HelperFailure("target.not_frontmost", exitCode: 78)
        }
        guard try resolver.resolve(target.arguments).generation == target.generation else {
            throw HelperFailure("target.identity_mismatch", exitCode: 69)
        }
        let traversal = try buttons(for: target)
        guard !traversal.truncated else {
            throw HelperFailure("ax.tree_truncated", exitCode: 78)
        }
        let matches = traversal.buttons.filter { $0.title == title }
        guard !matches.isEmpty else {
            throw HelperFailure("ax.button.not_found", exitCode: 78)
        }
        guard matches.count == 1 else {
            throw HelperFailure("ax.button.ambiguous", exitCode: 78, details: ["count": matches.count])
        }
        let match = matches[0]
        guard match.enabled, match.pressable else {
            throw HelperFailure("ax.button.not_pressable", exitCode: 78)
        }
        guard try resolver.resolve(target.arguments).generation == target.generation else {
            throw HelperFailure("target.identity_mismatch", exitCode: 69)
        }
        let result = AXUIElementPerformAction(match.element, kAXPressAction as CFString)
        if result == .cannotComplete {
            throw HelperFailure("operation.timeout", exitCode: 75)
        }
        guard result == .success else {
            throw HelperFailure("ax.action_failed", exitCode: 78, details: ["axError": result.rawValue])
        }
        return ["pid": target.arguments.pid, "title": title, "pressed": true]
    }

    private func copyValue(_ element: AXUIElement, _ attribute: CFString) throws -> CFTypeRef? {
        var value: CFTypeRef?
        let result = AXUIElementCopyAttributeValue(element, attribute, &value)
        if result == .cannotComplete {
            throw HelperFailure("operation.timeout", exitCode: 75)
        }
        guard result == .success else {
            return nil
        }
        return value
    }

    private func copyString(_ element: AXUIElement, _ attribute: CFString) throws -> String {
        try copyValue(element, attribute) as? String ?? ""
    }

    private func copyBool(_ element: AXUIElement, _ attribute: CFString) throws -> Bool {
        (try copyValue(element, attribute) as? NSNumber)?.boolValue ?? false
    }

    private func copyChildren(
        _ element: AXUIElement,
        limit: Int
    ) throws -> (children: [AXUIElement], totalCount: Int, truncated: Bool) {
        var rawCount: CFIndex = 0
        let countResult = AXUIElementGetAttributeValueCount(
            element,
            kAXChildrenAttribute as CFString,
            &rawCount
        )
        if countResult == .cannotComplete {
            throw HelperFailure("operation.timeout", exitCode: 75)
        }
        guard countResult == .success, rawCount > 0 else {
            return ([], 0, false)
        }
        let totalCount = Int(rawCount)
        let requestedCount = min(totalCount, max(0, limit))
        guard requestedCount > 0 else {
            return ([], totalCount, true)
        }
        var values: CFArray?
        let valuesResult = AXUIElementCopyAttributeValues(
            element,
            kAXChildrenAttribute as CFString,
            0,
            requestedCount,
            &values
        )
        if valuesResult == .cannotComplete {
            throw HelperFailure("operation.timeout", exitCode: 75)
        }
        guard valuesResult == .success else {
            return ([], totalCount, true)
        }
        return (
            values as? [AXUIElement] ?? [],
            totalCount,
            totalCount > requestedCount
        )
    }

    private func copyActions(_ element: AXUIElement) throws -> [String] {
        var actions: CFArray?
        let result = AXUIElementCopyActionNames(element, &actions)
        if result == .cannotComplete {
            throw HelperFailure("operation.timeout", exitCode: 75)
        }
        guard result == .success else {
            return []
        }
        return Array((actions as? [String] ?? []).prefix(16))
    }
}
