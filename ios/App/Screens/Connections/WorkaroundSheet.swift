import SwiftUI

/// How to get a source working when we cannot talk to it directly.
///
/// Garmin has not approved our API access and may not for a while, but Garmin
/// Connect writes to Apple Health, so the data can reach Coach Will anyway. A
/// greyed-out row saying "coming soon" tells a Garmin owner their watch is
/// unsupported. It isn't — it just needs one setting turned on.
struct WorkaroundSheet: View {
    let providerName: String
    let title: String
    let steps: [String]
    /// Shown under the steps when the source also needs Apple Health connected,
    /// which is the part people forget.
    var footnote: String?

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScreenBackground {
                ScrollView {
                    VStack(alignment: .leading, spacing: Theme.Metric.stack) {
                        VStack(alignment: .leading, spacing: 8) {
                            SectionLabel(providerName)
                            Text(title)
                                .font(Theme.Font.headline(26))
                                .foregroundStyle(Theme.Palette.text)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        .padding(.top, 8)

                        VStack(spacing: 12) {
                            ForEach(Array(steps.enumerated()), id: \.offset) { i, step in
                                StepRow(number: i + 1, text: step)
                            }
                        }

                        if let footnote {
                            Text(footnote)
                                .font(Theme.Font.caption)
                                .foregroundStyle(Theme.Palette.textDim)
                                .fixedSize(horizontal: false, vertical: true)
                                .padding(.horizontal, 4)
                        }

                        ActionButton(title: "Got it") { dismiss() }
                            .padding(.top, 4)
                    }
                    .padding(.horizontal, Theme.Metric.gutter)
                    .padding(.bottom, 40)
                }
            }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Close") { dismiss() }.tint(Theme.Palette.textDim)
                }
            }
            .toolbarBackground(Theme.Palette.bg, for: .navigationBar)
        }
        .preferredColorScheme(.dark)
    }
}

private struct StepRow: View {
    let number: Int
    let text: String

    var body: some View {
        Panel {
            HStack(alignment: .top, spacing: 14) {
                Text("\(number)")
                    .font(Theme.Font.stat(15))
                    .foregroundStyle(Theme.Palette.accent)
                    .frame(width: 26, height: 26)
                    .background(Theme.Palette.accent.opacity(0.12))
                    .clipShape(Circle())
                Text(text)
                    .font(Theme.Font.body)
                    .lineSpacing(3)
                    .foregroundStyle(Theme.Palette.text)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
            }
        }
    }
}
