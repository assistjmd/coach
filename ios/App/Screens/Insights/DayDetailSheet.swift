import SwiftUI

/// One day: what the coach asked for, what happened, and a way to disagree.
///
/// The correction exists because inference is a guess. Telling someone they
/// skipped a session they actually did is the fastest way to lose their trust in
/// everything else the app says, so overriding it is one tap and permanent.
struct DayDetailSheet: View {
    let day: DayEntry
    let onUpdated: (DayEntry) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var saving: String?
    @State private var error: String?

    var body: some View {
        NavigationStack {
            ScreenBackground {
                ScrollView {
                    VStack(alignment: .leading, spacing: Theme.Metric.stack) {
                        headline

                        if let p = day.prescription {
                            Panel(accent: Theme.Palette.accent) {
                                VStack(alignment: .leading, spacing: 8) {
                                    SectionLabel("What Will said")
                                    Text(p)
                                        .font(Theme.Font.bodyStrong)
                                        .lineSpacing(3)
                                        .foregroundStyle(Theme.Palette.text)
                                }
                            }
                        }

                        Panel {
                            VStack(alignment: .leading, spacing: 10) {
                                SectionLabel("What you did")
                                if day.workouts.isEmpty {
                                    Text("Nothing logged.")
                                        .font(Theme.Font.body)
                                        .foregroundStyle(Theme.Palette.textDim)
                                } else {
                                    ForEach(day.workouts) { w in
                                        HStack {
                                            Text(DayDetailSheet.describe(w))
                                                .font(Theme.Font.body)
                                                .foregroundStyle(Theme.Palette.text)
                                            Spacer()
                                            Text(w.source.uppercased())
                                                .font(.system(size: 10, weight: .heavy))
                                                .foregroundStyle(Theme.Palette.textDim)
                                        }
                                    }
                                }
                                loadLine
                            }
                        }

                        corrector

                        if let error {
                            Text(error)
                                .font(Theme.Font.caption)
                                .foregroundStyle(Theme.Palette.bad)
                        }
                    }
                    .padding(.horizontal, Theme.Metric.gutter)
                    .padding(.bottom, 40)
                }
            }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }.tint(Theme.Palette.accent)
                }
            }
            .toolbarBackground(Theme.Palette.bg, for: .navigationBar)
        }
        .preferredColorScheme(.dark)
    }

    private var headline: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(dateLabel)
                .font(.system(size: 26, weight: .black))
                .foregroundStyle(Theme.Palette.text)
            HStack(spacing: 10) {
                ComplianceTag(status: day.compliance, corrected: day.corrected)
                if let r = day.readiness {
                    Text("READINESS \(r)")
                        .font(.system(size: 10, weight: .heavy))
                        .tracking(0.8)
                        .foregroundStyle(Theme.Palette.readiness(r))
                }
            }
        }
        .padding(.top, 8)
    }

    private var loadLine: some View {
        HStack {
            Text("LOAD")
                .font(Theme.Font.sectionLabel)
                .tracking(1.2)
                .foregroundStyle(Theme.Palette.textDim)
            Spacer()
            Text(day.load.formatted(.number.precision(.fractionLength(0))))
                .font(Theme.Font.stat(20))
                .foregroundStyle(Theme.Palette.text)
            if day.estimated && day.load > 0 {
                // Says why rather than just flagging it — "estimated" alone
                // invites the question this answers.
                Text("est. no HR")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(Theme.Palette.caution)
            }
        }
        .padding(.top, 4)
    }

    @ViewBuilder
    private var corrector: some View {
        if day.prescription != nil {
            VStack(alignment: .leading, spacing: 10) {
                SectionLabel(day.corrected ? "You set this" : "Not right?")
                HStack(spacing: 8) {
                    ForEach(["complete", "partial", "missed", "rest"], id: \.self) { option in
                        Button {
                            Task { await correct(to: option) }
                        } label: {
                            Text(shortLabel(option))
                                .font(.system(size: 12, weight: .heavy))
                                .tracking(0.6)
                                .frame(maxWidth: .infinity)
                                .padding(.vertical, 11)
                                .foregroundStyle(
                                    day.compliance == option
                                        ? Theme.Palette.bg : Theme.Palette.text
                                )
                                .background(
                                    day.compliance == option
                                        ? Theme.Palette.accent : Theme.Palette.surface
                                )
                                .overlay(
                                    Rectangle().stroke(
                                        day.compliance == option
                                            ? Color.clear : Theme.Palette.line,
                                        lineWidth: 1
                                    )
                                )
                                .clipShape(RoundedRectangle(cornerRadius: Theme.Metric.radius))
                        }
                        .buttonStyle(PressStyle())
                        .disabled(saving != nil)
                    }
                }
                .opacity(saving != nil ? 0.6 : 1)
            }
        }
    }

    private func shortLabel(_ s: String) -> String {
        switch s {
        case "complete": return "DONE"
        case "partial": return "PART"
        case "missed": return "MISS"
        default: return "REST"
        }
    }

    private var dateLabel: String {
        guard let d = day.date else { return day.day }
        return d.formatted(.dateTime.weekday(.wide).day().month(.wide))
    }

    static func describe(_ w: WorkoutSummary) -> String {
        var parts: [String] = []
        if let s = w.sport { parts.append(s.replacingOccurrences(of: "_", with: " ").capitalized) }
        if let d = w.durationS, d > 0 { parts.append("\(d / 60) min") }
        if let m = w.distanceM, m > 0 {
            parts.append("\((m / 1000).formatted(.number.precision(.fractionLength(1)))) km")
        }
        if let hr = w.avgHr { parts.append("avg \(hr) bpm") }
        return parts.joined(separator: " · ")
    }

    private func correct(to status: String) async {
        saving = status
        defer { saving = nil }
        do {
            let updated: DayEntry = try await APIClient.shared.patch(
                "/v1/insights/day/\(day.day)", body: ["compliance": status]
            )
            onUpdated(updated)
        } catch {
            self.error = error.localizedDescription
        }
    }
}
