import Foundation

enum ReasoningLevel: String, CaseIterable, Identifiable {
    case off, low, medium, high, xhigh

    var id: String { rawValue }

    var title: String {
        switch self {
        case .off: "Off"
        case .low: "Low"
        case .medium: "Medium"
        case .high: "High"
        case .xhigh: "Max"
        }
    }
}

struct ReasoningOptions {
    let levels: [ReasoningLevel]

    init(supportsReasoning: Bool, requiresReasoning: Bool) {
        levels = supportsReasoning
            ? ReasoningLevel.allCases.filter { !requiresReasoning || $0 != .off }
            : []
    }

    var defaultLevel: ReasoningLevel? { levels.first }
    var isAvailable: Bool { !levels.isEmpty }

    func selection(_ storedValue: String) -> ReasoningLevel? {
        guard let level = ReasoningLevel(rawValue: storedValue), levels.contains(level) else {
            return defaultLevel
        }
        return level
    }

    func strength(_ storedValue: String) -> String? {
        guard let level = selection(storedValue), level != .off else { return nil }
        return level.rawValue
    }
}
