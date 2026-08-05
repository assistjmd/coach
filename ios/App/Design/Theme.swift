import SwiftUI

/// The design system.
///
/// Direction is "athletic and physical": high contrast, heavy type, one
/// saturated accent, square corners. The previous look was default SwiftUI —
/// rounded cards on `.secondarySystemBackground` with SF Symbol labels — which
/// reads as generic because every unstyled app looks that way.
///
/// Two rules worth keeping if this gets extended:
/// 1. Numbers are always tabular (`monospacedDigit`). Readiness ticking from 61
///    to 59 must not reflow the layout.
/// 2. One accent only. Amber and red are states (caution, bad), not decoration.
enum Theme {

    // MARK: Palette

    enum Palette {
        /// Near-black rather than pure black: gives surfaces something to lift off.
        static let bg = Color(hex: 0x0A0C0E)
        static let surface = Color(hex: 0x14181C)
        static let surfaceHigh = Color(hex: 0x1E242A)
        static let line = Color(hex: 0x2A3138)

        static let text = Color(hex: 0xF2F5F7)
        static let textDim = Color(hex: 0x8B98A5)

        /// Brand accent — matches the app icon.
        static let accent = Color(hex: 0x35E08D)
        static let caution = Color(hex: 0xFFB020)
        static let bad = Color(hex: 0xFF5A4E)

        /// Colour for a readiness score. Thresholds match how the coach reads
        /// them, so the number and the prose never disagree.
        static func readiness(_ score: Int) -> Color {
            switch score {
            case ..<40: return bad
            case ..<65: return caution
            default: return accent
            }
        }
    }

    // MARK: Type

    enum Font {
        /// Big numerals. Tight tracking and tabular figures.
        static func display(_ size: CGFloat = 72) -> SwiftUI.Font {
            .system(size: size, weight: .black, design: .default)
                .monospacedDigit()
        }

        static func stat(_ size: CGFloat = 26) -> SwiftUI.Font {
            .system(size: size, weight: .bold).monospacedDigit()
        }

        static let sectionLabel = SwiftUI.Font.system(size: 11, weight: .heavy)
        static let body = SwiftUI.Font.system(size: 16, weight: .regular)
        static let bodyStrong = SwiftUI.Font.system(size: 17, weight: .semibold)
        static let caption = SwiftUI.Font.system(size: 13, weight: .medium)
        static let button = SwiftUI.Font.system(size: 16, weight: .heavy)
    }

    enum Metric {
        /// Slightly-cut corners rather than rounded: reads as equipment, not card.
        static let radius: CGFloat = 4
        static let gutter: CGFloat = 20
        static let stack: CGFloat = 14
    }
}

// MARK: - Building blocks

/// Full-bleed background. Every screen sits on this.
struct ScreenBackground<Content: View>: View {
    @ViewBuilder var content: () -> Content
    var body: some View {
        ZStack {
            Theme.Palette.bg.ignoresSafeArea()
            content()
        }
    }
}

/// Small uppercase tracked label that heads a section. Replaces SF Symbol
/// `Label` rows, which were most of the generic feel.
struct SectionLabel: View {
    let text: String
    init(_ text: String) { self.text = text }
    var body: some View {
        Text(text.uppercased())
            .font(Theme.Font.sectionLabel)
            .tracking(1.6)
            .foregroundStyle(Theme.Palette.textDim)
    }
}

/// Squared surface with a hairline. The hairline is what makes it read as an
/// instrument panel rather than a floating card.
struct Panel<Content: View>: View {
    var accent: Color? = nil
    @ViewBuilder var content: () -> Content

    var body: some View {
        content()
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(Theme.Metric.gutter)
            .background(Theme.Palette.surface)
            .overlay(alignment: .leading) {
                // Accent spine, used to mark the primary panel on a screen.
                if let accent {
                    Rectangle().fill(accent).frame(width: 3)
                }
            }
            .overlay(
                Rectangle().stroke(Theme.Palette.line, lineWidth: 1)
            )
            .clipShape(RoundedRectangle(cornerRadius: Theme.Metric.radius))
    }
}

/// One metric: name, value, optional delta against baseline.
struct StatRow: View {
    let name: String
    let value: String
    var delta: Double? = nil
    /// Whether a fall in this metric is bad. HRV falling is bad; resting heart
    /// rate falling is good, so the arrow colour cannot be derived from sign alone.
    var lowerIsWorse: Bool = true

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(name.uppercased())
                .font(Theme.Font.sectionLabel)
                .tracking(1.2)
                .foregroundStyle(Theme.Palette.textDim)
            Spacer(minLength: 12)
            Text(value)
                .font(Theme.Font.stat(20))
                .foregroundStyle(Theme.Palette.text)
            if let delta {
                Text(deltaText(delta))
                    .font(Theme.Font.caption.monospacedDigit())
                    .foregroundStyle(deltaColor(delta))
                    .frame(minWidth: 54, alignment: .trailing)
            }
        }
    }

    private func deltaText(_ d: Double) -> String {
        let arrow = d < 0 ? "▼" : "▲"
        return "\(arrow)\(abs(d).formatted(.number.precision(.fractionLength(0))))%"
    }

    private func deltaColor(_ d: Double) -> Color {
        if abs(d) < 3 { return Theme.Palette.textDim }
        let worse = lowerIsWorse ? d < 0 : d > 0
        return worse ? Theme.Palette.caution : Theme.Palette.accent
    }
}

/// Heavy full-width button.
struct ActionButton: View {
    let title: String
    var filled: Bool = true
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title.uppercased())
                .font(Theme.Font.button)
                .tracking(1.4)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 17)
                .foregroundStyle(filled ? Theme.Palette.bg : Theme.Palette.text)
                .background(filled ? Theme.Palette.accent : Color.clear)
                .overlay(
                    Rectangle().stroke(
                        filled ? Color.clear : Theme.Palette.line, lineWidth: 1
                    )
                )
                .clipShape(RoundedRectangle(cornerRadius: Theme.Metric.radius))
        }
        .buttonStyle(PressStyle())
    }
}

/// Physical press feedback. Cheap, and most of what makes the UI feel tactile.
struct PressStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.97 : 1)
            .opacity(configuration.isPressed ? 0.85 : 1)
            .animation(.spring(response: 0.25, dampingFraction: 0.7),
                       value: configuration.isPressed)
    }
}

extension Color {
    init(hex: UInt32) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255,
            opacity: 1
        )
    }
}
