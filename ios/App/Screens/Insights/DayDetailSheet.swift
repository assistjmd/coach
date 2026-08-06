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
    /// Local copies so a tap is instant. The server is the source of truth, but
    /// waiting for a round trip before the box fills makes it feel broken.
    @State private var ticked: Set<String> = []
    @State private var journal: String = ""
    @State private var journalSaving = false
    @State private var loaded = false
    @FocusState private var journalFocused: Bool

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

                        checklist
                        journalPanel
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
        .task {
            // Once only: re-seeding on every redraw would fight the user's taps.
            guard !loaded else { return }
            loaded = true
            ticked = Set(day.completedItems)
            journal = day.journal ?? ""
        }
    }

    private func toggle(_ id: String) {
        if ticked.contains(id) { ticked.remove(id) } else { ticked.insert(id) }
        Task { await persist(items: Array(ticked), journal: nil) }
    }

    private func saveJournal() async {
        journalSaving = true
        defer { journalSaving = false }
        await persist(items: nil, journal: journal)
    }

    /// Sends only the fields that changed. The endpoint treats a missing field
    /// as "leave alone", so ticking a box cannot wipe a note and vice versa.
    private func persist(items: [String]?, journal: String?) async {
        var body: [String: Any] = [:]
        if let items { body["completed_items"] = items }
        if let journal { body["journal"] = journal }
        guard !body.isEmpty else { return }
        do {
            let updated: DayEntry = try await APIClient.shared.put(
                "/v1/insights/day/\(day.day)/log", body: body
            )
            onUpdated(updated)
        } catch {
            self.error = error.localizedDescription
        }
    }

    @ViewBuilder
    private var checklist: some View {
        if !day.sessionItems.isEmpty {
            Panel {
                VStack(alignment: .leading, spacing: 12) {
                    SectionLabel("The session")
                    ForEach(day.sessionItems) { item in
                        Button {
                            toggle(item.id)
                        } label: {
                            HStack(alignment: .top, spacing: 12) {
                                ZStack {
                                    Rectangle()
                                        .fill(ticked.contains(item.id)
                                              ? Theme.Palette.accent : Color.clear)
                                        .overlay(
                                            Rectangle().stroke(
                                                ticked.contains(item.id)
                                                    ? Theme.Palette.accent
                                                    : Theme.Palette.line,
                                                lineWidth: 1.5
                                            )
                                        )
                                        .frame(width: 20, height: 20)
                                    if ticked.contains(item.id) {
                                        Text("✓")
                                            .font(.system(size: 13, weight: .black))
                                            .foregroundStyle(Theme.Palette.bg)
                                    }
                                }
                                Text(item.text)
                                    .font(Theme.Font.body)
                                    .multilineTextAlignment(.leading)
                                    .foregroundStyle(
                                        ticked.contains(item.id)
                                            ? Theme.Palette.textDim : Theme.Palette.text
                                    )
                                    .strikethrough(ticked.contains(item.id),
                                                   color: Theme.Palette.textDim)
                                Spacer(minLength: 0)
                            }
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
    }

    private var journalPanel: some View {
        Panel {
            VStack(alignment: .leading, spacing: 10) {
                SectionLabel("Your notes")
                TextField("", text: $journal, axis: .vertical)
                    .placeholder(when: journal.isEmpty) {
                        Text("How did it actually go?")
                            .foregroundStyle(Theme.Palette.textDim)
                    }
                    .focused($journalFocused)
                    .font(Theme.Font.body)
                    .foregroundStyle(Theme.Palette.text)
                    .lineLimit(3...8)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 12)
                    .background(Theme.Palette.surfaceHigh)
                    .overlay(Rectangle().stroke(Theme.Palette.line, lineWidth: 1))
                    .clipShape(RoundedRectangle(cornerRadius: Theme.Metric.radius))
                // Saved explicitly rather than on every keystroke: autosaving a
                // free-text field means a request per character, and a half-typed
                // sentence briefly becoming the record.
                if journalFocused || journalDirty {
                    ActionButton(title: journalSaving ? "Saving" : "Save note") {
                        journalFocused = false
                        Task { await saveJournal() }
                    }
                    .disabled(journalSaving)
                }
            }
        }
    }

    private var journalDirty: Bool {
        journal.trimmingCharacters(in: .whitespacesAndNewlines) != (day.journal ?? "")
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
