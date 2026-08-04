import SwiftUI

struct ProfileView: View {
    @EnvironmentObject var session: SessionStore
    @State private var goal = GoalBody()
    @State private var loaded = false
    @State private var saveState: SaveState = .idle

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
        NavigationStack {
            Form {
                Section("Goal") {
                    Picker("Type", selection: $goal.kind) {
                        ForEach(GoalKind.allCases) { Text($0.title).tag($0) }
                    }
                    Picker("Sport", selection: $goal.sport) {
                        Text("Not set").tag(Sport?.none)
                        ForEach(Sport.allCases) { Text($0.title).tag(Sport?.some($0)) }
                    }
                    if goal.kind == .race {
                        DatePicker("Target date", selection: targetDate, displayedComponents: .date)
                    }
                    TextField(
                        "Notes (e.g. FTP 240W, target 3:30 marathon)",
                        text: Binding(
                            get: { goal.description ?? "" },
                            set: { goal.description = $0.isEmpty ? nil : $0 }
                        ),
                        axis: .vertical
                    )
                }

                Section {
                    Button {
                        Task { await save() }
                    } label: {
                        HStack {
                            Text("Save goal")
                            Spacer()
                            switch saveState {
                            case .saving: ProgressView()
                            case .saved: Image(systemName: "checkmark").foregroundStyle(.green)
                            default: EmptyView()
                            }
                        }
                    }
                    .disabled(saveState == .saving)

                    if case .failed(let message) = saveState {
                        Text(message).font(.footnote).foregroundStyle(.red)
                    }
                }

                Section {
                    Button("Sign out", role: .destructive) { session.signOut() }
                }
            }
            .navigationTitle("Profile")
            .task {
                guard !loaded else { return }
                loaded = true
                await load()
            }
        }
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
