import AppKit
import SwiftUI

enum AppStyle {
    static let green = Color(red: 34 / 255, green: 197 / 255, blue: 94 / 255)
    static let accent = adaptive(light: 0x15803D, dark: 0x4ADE80)
    static let canvas = adaptive(light: 0xFAFAFA, dark: 0x171819)
    static let sidebar = adaptive(light: 0xF1F2F3, dark: 0x111213)
    static let surface = adaptive(light: 0xFFFFFF, dark: 0x202223)
    static let inset = adaptive(light: 0xF3F4F5, dark: 0x131516)
    static let line = adaptive(light: 0xDFE2E4, dark: 0x333638)
    static let hover = adaptive(light: 0xE9ECEE, dark: 0x2B2E30)
    static let prose = Font.system(size: 15)

    private static func adaptive(light: UInt32, dark: UInt32) -> Color {
        Color(nsColor: NSColor(name: nil) { appearance in
            let value = appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
                ? dark : light
            return NSColor(
                srgbRed: CGFloat((value >> 16) & 0xFF) / 255,
                green: CGFloat((value >> 8) & 0xFF) / 255,
                blue: CGFloat(value & 0xFF) / 255,
                alpha: 1
            )
        })
    }
}

struct AppIconTile: View {
    let symbol: String
    var color: Color = AppStyle.accent
    var size: CGFloat = 36

    var body: some View {
        Image(systemName: symbol)
            .font(.system(size: size * 0.46, weight: .medium))
            .foregroundStyle(color)
            .frame(width: size, height: size)
            .background(color.opacity(0.1), in: RoundedRectangle(cornerRadius: 8))
            .accessibilityHidden(true)
    }
}

struct AppPageHeading: View {
    let title: String
    var subtitle: String? = nil

    init(_ title: String, subtitle: String? = nil) {
        self.title = title
        self.subtitle = subtitle
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title)
                .font(.system(size: 23, weight: .semibold))
            if let subtitle {
                Text(subtitle)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

struct AppBadge: View {
    let text: String
    var symbol: String? = nil
    var color: Color = .secondary

    init(_ text: String, symbol: String? = nil, color: Color = .secondary) {
        self.text = text
        self.symbol = symbol
        self.color = color
    }

    var body: some View {
        HStack(spacing: 4) {
            if let symbol { Image(systemName: symbol) }
            Text(text)
        }
        .font(.system(size: 10, weight: .medium))
        .foregroundStyle(color)
        .padding(.horizontal, 6)
        .padding(.vertical, 3)
        .background(color.opacity(0.08), in: RoundedRectangle(cornerRadius: 4))
        .fixedSize()
    }
}

struct AppIconButtonStyle: ButtonStyle {
    var selected = false
    var prominent = false

    func makeBody(configuration: Configuration) -> some View {
        StyledLabel(configuration: configuration, selected: selected, prominent: prominent)
    }

    private struct StyledLabel: View {
        let configuration: ButtonStyle.Configuration
        let selected: Bool
        let prominent: Bool
        @Environment(\.isEnabled) private var isEnabled
        @State private var isHovered = false

        var body: some View {
            configuration.label
                .font(.system(size: 13, weight: .medium))
                .frame(minWidth: 30, minHeight: 30)
                .foregroundStyle(prominent ? Color.black : selected ? AppStyle.accent : Color.secondary)
                .background(
                    prominent ? AppStyle.green
                        : selected ? AppStyle.green.opacity(0.12)
                        : isHovered ? AppStyle.hover : Color.clear,
                    in: RoundedRectangle(cornerRadius: 6)
                )
                .opacity(isEnabled ? (configuration.isPressed ? 0.65 : 1) : 0.35)
                .contentShape(Rectangle())
                .onHover { isHovered = $0 }
        }
    }
}

struct AppRowButtonStyle: ButtonStyle {
    var selected = false

    func makeBody(configuration: Configuration) -> some View {
        StyledLabel(configuration: configuration, selected: selected)
    }

    private struct StyledLabel: View {
        let configuration: ButtonStyle.Configuration
        let selected: Bool
        @State private var isHovered = false

        var body: some View {
            configuration.label
                .background(
                    selected ? AppStyle.green.opacity(0.09)
                        : isHovered ? AppStyle.hover.opacity(0.6) : Color.clear,
                    in: RoundedRectangle(cornerRadius: 8)
                )
                .opacity(configuration.isPressed ? 0.7 : 1)
                .onHover { isHovered = $0 }
        }
    }
}

// Wrap compact controls and metadata without letting them widen the chat column.
struct AppFlowLayout: Layout {
    var spacing: CGFloat = 8
    var rowSpacing: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        arrangement(width: proposal.width, subviews: subviews).size
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let layout = arrangement(width: bounds.width, subviews: subviews)
        for (index, origin) in layout.origins.enumerated() {
            subviews[index].place(
                at: CGPoint(x: bounds.minX + origin.x, y: bounds.minY + origin.y),
                proposal: ProposedViewSize(layout.sizes[index])
            )
        }
    }

    private func arrangement(width: CGFloat?, subviews: Subviews)
        -> (size: CGSize, origins: [CGPoint], sizes: [CGSize]) {
        let availableWidth = width.flatMap { $0.isFinite ? max(0, $0) : nil }
        let limit = availableWidth ?? .greatestFiniteMagnitude
        var origins: [CGPoint] = []
        var sizes: [CGSize] = []
        var x: CGFloat = 0
        var y: CGFloat = 0
        var rowHeight: CGFloat = 0
        var usedWidth: CGFloat = 0
        for view in subviews {
            let ideal = view.sizeThatFits(.unspecified)
            let size = view.sizeThatFits(ProposedViewSize(width: min(ideal.width, limit), height: nil))
            if x > 0, x + size.width > limit {
                x = 0
                y += rowHeight + rowSpacing
                rowHeight = 0
            }
            origins.append(CGPoint(x: x, y: y))
            sizes.append(size)
            usedWidth = max(usedWidth, x + size.width)
            rowHeight = max(rowHeight, size.height)
            x += size.width + spacing
        }
        return (CGSize(width: availableWidth ?? usedWidth, height: y + rowHeight), origins, sizes)
    }
}
