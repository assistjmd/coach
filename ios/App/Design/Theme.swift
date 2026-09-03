import SwiftUI

/// The design system.
///
/// Calm and spacious, in the manner of Oura rather than Whoop. The previous
/// direction was hard-edged and shouty — square corners, black condensed
/// uppercase, tracked labels on everything — which made a rest day look as
/// urgent as a threshold session. An app you open before you are properly awake
/// should not shout.
///
/// What changed and why:
/// - Corners are soft (18pt) and surfaces have no hairline. The border was
///   doing the work of separating panels; space does that better.
/// - Type is light-to-medium in sentence case. Uppercase tracking survives only
///   on small labels, where it reads as a caption rather than a headline.
/// - One number leads each screen, at size, with everything else quieter.
///
/// Two rules worth keeping if this gets extended:
/// 1. Numbers are always tabular (`monospacedDigit`). Readiness ticking from 61
///    to 59 must not reflow the layout.
/// 2. One accent only. Amber and red are states (caution, bad), not decoration.
enum Theme {

    // MARK: Palette

    enum Palette {
        /// Warm near-black. The old background was blue-cold, which made every
        /// surface on top of it feel clinical.
        static let bg = Color(hex: 0x0B0B0D)
        static let surface = Color(hex: 0x161619)
        static let surfaceHigh = Color(hex: 0x1F1F23)
        /// Kept for the few places a divider genuinely helps. Panels no longer
        /// use it — separation comes from space.
        static let line = Color(hex: 0x2A2A2F)

        static let text = Color(hex: 0xF5F4F2)
        /// Lifted from the old 0x8B98A5 so secondary text stays legible at the
        /// lighter weights this direction uses.
        static let textDim = Color(hex: 0x9A9AA2)

        /// Brand accent, desaturated a step. The old green was pure signal
        /// green and fought everything next to it.
        static let accent = Color(hex: 0x5FD69B)
        static let caution = Color(hex: 0xE8B268)
        static let bad = Color(hex: 0xE87A6B)

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
        /// The one big number on a screen. Light rather than black — at 72pt a
        /// heavy weight is a slab, and the size alone carries the emphasis.
        static func display(_ size: CGFloat = 72) -> SwiftUI.Font {
            .system(size: size, weight: .light, design: .rounded)
                .monospacedDigit()
        }

        static func stat(_ size: CGFloat = 26) -> SwiftUI.Font {
            .system(size: size, weight: .medium, design: .rounded).monospacedDigit()
        }

        /// Small, tracked, uppercase. The only place uppercase survives — at 11pt
        /// it reads as a caption, which is what a section label should be.
        static let sectionLabel = SwiftUI.Font.system(size: 11, weight: .semibold)
        static let body = SwiftUI.Font.system(size: 16, weight: .regular)
        static let bodyStrong = SwiftUI.Font.system(size: 17, weight: .medium)
        static let caption = SwiftUI.Font.system(size: 13, weight: .regular)
        /// Sentence case, not shouted. See ActionButton.
        static let button = SwiftUI.Font.system(size: 16, weight: .semibold)
        /// Headline prose — the coach's actual words. Serif would be a step too
        /// far, but the rounded face keeps it from reading like a system alert.
        static func headline(_ size: CGFloat = 22) -> SwiftUI.Font {
            .system(size: size, weight: .regular, design: .rounded)
        }
    }

    enum Metric {
        /// Soft. Paired with dropping the hairline border, this is most of what
        /// separates the new look from the old one.
        static let radius: CGFloat = 18
        static let gutter: CGFloat = 22
        /// Generous. Whitespace is doing the job the borders used to.
        static let stack: CGFloat = 18
        static let panelPadding: CGFloat = 20
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

/// Small uppercase tracked label that heads a section.
struct SectionLabel: View {
    let text: String
    init(_ text: String) { self.text = text }
    var body: some View {
        Text(text.uppercased())
            .font(Theme.Font.sectionLabel)
            .tracking(1.3)
            .foregroundStyle(Theme.Palette.textDim)
    }
}

/// The readiness dial.
///
/// One number, stated once, at size. The ring is a thin track rather than a
/// thick gauge — it should register as a quiet frame around the score, not
/// compete with it.
struct ReadinessRing: View {
    let score: Int
    var size: CGFloat = 190
    var label: String = "Readiness"

    private var fraction: Double { Double(min(max(score, 0), 100)) / 100 }
    private var tint: Color { Theme.Palette.readiness(score) }

    var body: some View {
        ZStack {
            Circle()
                .stroke(Theme.Palette.surfaceHigh, lineWidth: 6)
            Circle()
                .trim(from: 0, to: fraction)
                .stroke(tint, style: StrokeStyle(lineWidth: 6, lineCap: .round))
                // Start at the top rather than at 3 o'clock, which is where a
                // trimmed circle begins by default.
                .rotationEffect(.degrees(-90))
                .animation(.easeOut(duration: 0.6), value: fraction)

            VStack(spacing: 2) {
                Text("\(score)")
                    .font(Theme.Font.display(64))
                    .foregroundStyle(Theme.Palette.text)
                Text(label)
                    .font(Theme.Font.caption)
                    .foregroundStyle(Theme.Palette.textDim)
            }
        }
        .frame(width: size, height: size)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(label) \(score) out of 100")
    }
}

/// A soft surface. No border by design — the fill and the surrounding space are
/// enough, and the hairline was what made every screen read as an instrument
/// panel.
struct Panel<Content: View>: View {
    var accent: Color? = nil
    @ViewBuilder var content: () -> Content

    var body: some View {
        content()
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(Theme.Metric.panelPadding)
            .background(Theme.Palette.surface)
            .clipShape(RoundedRectangle(cornerRadius: Theme.Metric.radius, style: .continuous))
            .overlay(alignment: .topLeading) {
                // The primary panel gets a soft tint wash instead of the old
                // accent spine, which read as a warning stripe.
                if let accent {
                    RoundedRectangle(cornerRadius: Theme.Metric.radius, style: .continuous)
                        .fill(accent.opacity(0.06))
                        .allowsHitTesting(false)
                }
            }
    }
}

/// How closely the athlete is following the plan.
///
/// The differentiated number. Every wearable reports readiness because every
/// wearable measures a body; none of them prescribe anything, so none of them
/// can say whether you did it.
///
/// Copy states the fact and stops. "You've done 8 of 10" is information;
/// "Great job!" is the pep sign-off the coach's own voice rules already ban.
struct ConsistencyCard: View {
    let score: Int?
    let streak: Int
    let assessed: Int

    var body: some View {
        Panel {
            HStack(alignment: .center, spacing: 16) {
                VStack(alignment: .leading, spacing: 4) {
                    SectionLabel("Consistency")
                    if let score {
                        Text("\(score)%")
                            .font(Theme.Font.stat(30))
                            .foregroundStyle(tint(score))
                        Text(detail)
                            .font(Theme.Font.caption)
                            .foregroundStyle(Theme.Palette.textDim)
                    } else {
                        // Never render 0% for a new account — it reads as a
                        // judgement on someone who has not been asked to do
                        // anything yet.
                        Text("Not yet")
                            .font(Theme.Font.stat(22))
                            .foregroundStyle(Theme.Palette.textDim)
                        Text("A few more days and I'll show how closely you're following the plan.")
                            .font(Theme.Font.caption)
                            .foregroundStyle(Theme.Palette.textDim)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                Spacer(minLength: 0)
                if streak > 2 {
                    VStack(spacing: 2) {
                        Text("\(streak)")
                            .font(Theme.Font.stat(22))
                            .foregroundStyle(Theme.Palette.text)
                        Text("day run")
                            .font(Theme.Font.caption)
                            .foregroundStyle(Theme.Palette.textDim)
                    }
                }
            }
        }
    }

    private var detail: String {
        // Sessions, not days: rest days are excluded from the score, and saying
        // "of the last 14 days" would not match the number shown.
        assessed == 1
            ? "of 1 session I asked for"
            : "of the last \(assessed) sessions I asked for"
    }

    private func tint(_ score: Int) -> Color {
        switch score {
        case ..<50: return Theme.Palette.caution
        default: return Theme.Palette.text
        }
    }
}

/// Will's signature.
///
/// Not an avatar and not a badge. A 270° arc — the readiness ring's own
/// geometry shrunk — placed beside anything the coach wrote, so its voice is
/// distinguishable from the app's own chrome at a glance.
///
/// A face or a name would push this toward the assistant framing the whole
/// product avoids. A shape the app already owns says "this is Will" without
/// saying "this is a bot".
struct WillMark: View {
    var size: CGFloat = 14

    var body: some View {
        Circle()
            .trim(from: 0, to: 0.75)
            .stroke(
                Theme.Palette.accent,
                style: StrokeStyle(lineWidth: size * 0.16, lineCap: .round)
            )
            .rotationEffect(.degrees(-90))
            .frame(width: size, height: size)
            // Decorative: the prose beside it is the content, and a screen
            // reader announcing "image" before every line the coach says would
            // be noise.
            .accessibilityHidden(true)
    }
}

/// Something Will said, with its signature.
struct WillSays<Content: View>: View {
    var spacing: CGFloat = 12
    @ViewBuilder var content: () -> Content

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: spacing) {
            WillMark()
                // Nudged to sit on the first line's baseline rather than above
                // it, which reads as a bullet rather than a signature.
                .offset(y: 1)
            content()
            Spacer(minLength: 0)
        }
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
            Text(name)
                .font(Theme.Font.body)
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
        let arrow = d < 0 ? "↓" : "↑"
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
            // Sentence case. Shouting a label in uppercase was a large part of
            // what made every screen feel like a warning.
            Text(title)
                .font(Theme.Font.button)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 16)
                .foregroundStyle(filled ? Theme.Palette.bg : Theme.Palette.text)
                .background(filled ? Theme.Palette.accent : Theme.Palette.surfaceHigh)
                .clipShape(Capsule())
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

/// One choice in a list of them, with room for an explanation.
///
/// Used wherever a segmented control would truncate. Three options reading
/// "Just keep me...", "Train for a race", "Get faster or..." tell the athlete
/// nothing and cannot be compared, which is the whole job of the control.
struct SelectRow: View {
    let title: String
    let detail: String?
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 14) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(title)
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundStyle(Theme.Palette.text)
                    if let detail {
                        Text(detail)
                            .font(Theme.Font.caption)
                            .foregroundStyle(Theme.Palette.textDim)
                            .multilineTextAlignment(.leading)
                    }
                }
                Spacer(minLength: 8)
                Rectangle()
                    .fill(selected ? Theme.Palette.accent : Color.clear)
                    .overlay(
                        Rectangle().stroke(
                            selected ? Theme.Palette.accent : Theme.Palette.line,
                            lineWidth: 1.5
                        )
                    )
                    .frame(width: 18, height: 18)
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.Palette.surface)
            .overlay(
                Rectangle().stroke(
                    selected ? Theme.Palette.accent : Theme.Palette.line,
                    lineWidth: 1
                )
            )
            .clipShape(RoundedRectangle(cornerRadius: Theme.Metric.radius))
        }
        .buttonStyle(PressStyle())
    }
}
