import SwiftUI

struct ProfileView: View {
    @EnvironmentObject var session: SessionStore
    @State private var goal = GoalBody()
    @State private var loaded = false
    @State private var saveState: SaveState = .idle
    @FocusState private var notesFocused: Bool

    private enum SaveState: Equatable {
        case idle, saving, saved, failed(String)
    }

    /// Bridges the stored `yyyy-MM-dd` string to the DatePicker's `Date`,
    /// through a UTC-fixed formatter so the day can't drift.
    private var targetDate: Binding<Date> {
        Binding(
            get: {
                CalendarDate.date(from: goal.targetDate)
                    ?? Date().addingTimeInterval(60 * 60 * 24 * 60)
            },
            set: { goal.targetDate = CalendarDate.string(from: $0) }
        )
    }

    var body: some View {
        ScreenBackground {
            ScrollView {
                VStack(alignment: .leading, spacing: Theme.Metric.stack) {
                    header
                    goalPanel
                    saveRow
                    signOutRow
                }
                .padding(.horizontal, Theme.Metric.gutter)
                .padding(.bottom, 40)
            }
            .scrollDismissesKeyboard(.interactively)
        }
        .toolbar {
            ToolbarItemGroup(placement: .keyboard) {
                Spacer()
                Button("Done") { notesFocused = false }
                    .font(Theme.Font.button)
                    .tint(Theme.Palette.accent)
            }
        }
        .task {
            guard !loaded else { return }
            loaded = true
            await load()
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 2) {
            SectionLabel("What you're training for")
            Text("GOAL")
                .font(.system(size: 34, weight: .black))
                .tracking(-0.5)
                .foregroundStyle(Theme.Palette.text)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.top, 12)
        .padding(.bottom, 6)
    }

    private var goalPanel: some View {
        Panel {
            VStack(alignment: .leading, spacing: 20) {
                field("Type") {
                    Picker("", selection: $goal.kind) {
                        ForEach(GoalKind.allCases) { Text($0.title).tag($0) }
                    }
                    .pickerStyle(.segmented)
                }

                field("Sport") {
                    Picker("", selection: $goal.sport) {
                        Text("Not set").tag(Sport?.none)
                        ForEach(Sport.allCases) { Text($0.title).tag(Sport?.some($0)) }
                    }
                    .pickerStyle(.menu)
                    .tint(Theme.Palette.accent)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }

                if goal.kind == .race {
                    field("Target date") {
                        DatePicker("", selection: targetDate, displayedComponents: .date)
                            .datePickerStyle(.compact)
                            .labelsHidden()
                            .tint(Theme.Palette.accent)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }

                field("In your words") {
                    TextField(
                        "",
                        text: Binding(
                            get: { goal.description ?? "" },
                            set: { goal.description = $0.isEmpty ? nil : $0 }
                        ),
                        axis: .vertical
                    )
                    .placeholder(when: (goal.description ?? "").isEmpty) {
                        Text("Sub-20 5k without wrecking my knees")
                            .foregroundStyle(Theme.Palette.textDim)
                    }
                    .focused($notesFocused)
                    .font(Theme.Font.body)
                    .foregroundStyle(Theme.Palette.text)
                    .lineLimit(2...5)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 12)
                    .background(Theme.Palette.surfaceHigh)
                    .overlay(Rectangle().stroke(Theme.Palette.line, lineWidth: 1))
                    .clipShape(RoundedRectangle(cornerRadius: Theme.Metric.radius))
                }
            }
        }
    }

    private func field<C: View>(_ label: String, @ViewBuilder content: () -> C) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            SectionLabel(label)
            content()
        }
    }

    @ViewBuilder
    private var saveRow: some View {
        VStack(alignment: .leading, spacing: 10) {
            ActionButton(title: saveState == .saved ? "Saved" : "Save goal") {
                Task { await save() }
            }
            .disabled(saveState == .saving)
            .opacity(saveState == .saving ? 0.6 : 1)

            if case .failed(let message) = saveState {
                Text(message)
                    .font(Theme.Font.caption)
                    .foregroundStyle(Theme.Palette.bad)
            }
        }
    }

    private var signOutRow: some View {
        ActionButton(title: "Sign out", filled: false) { session.signOut() }
            .padding(.top, 8)
    }

    private func load() async {
        // A missing goal is a valid response (null), so decode optionally.
        if let existing: GoalBody? = try? await APIClient.shared.get("/v1/goal"),
           let existing {
            goal = existing
        }
    }

    private func save() async {
        saveState = .saving
        // The backend rejects a race goal without a date; catch it here so the
        // user gets a sentence rather than a validation dump.
        if goal.kind == .race, goal.targetDate == nil {
            goal.targetDate = CalendarDate.string(from: targetDate.wrappedValue)
        }
        do {
            let saved: GoalBody = try await APIClient.shared.put(
                "/v1/goal", body: goal.dictionary()
            )
            goal = saved
            saveState = .saved
            try? await Task.sleep(for: .seconds(2))
            if saveState == .saved { saveState = .idle }
        } catch {
            saveState = .failed(error.localizedDescription)
        }
    }
}
